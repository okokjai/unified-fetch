#!/usr/bin/env python3
"""
session_pool.py — Session Pool for UnifiedBrowser.

Manages a pool of Chrome browser instances, each bound to a distinct
identity (fingerprint profile). This provides:

1. **Identity isolation** — each site gets its own browser instance,
   so cookies/fingerprints never leak between sites
2. **Resource pooling** — don't launch a new Chrome for every request;
   reuse instances with configurable lifecycle
3. **Fault isolation** — one instance being detected/blocked doesn't
   poison the others; instances can be recycled on failure

Architecture:
  Caller → SessionPool.get(url) → BrowserIdentity (bound instance)
                                  └── reuse existing instance for same site
                                  └── recycle/restart on failure
"""

import asyncio
import logging
import os
import random
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

try:
    from .cdp_driver import CDPTransport, CDPSession
    from .identity import IdentityManager, FingerprintProfile
except ImportError:
    # Direct execution fallback
    from cdp_driver import CDPTransport, CDPSession
    from identity import IdentityManager, FingerprintProfile

logger = logging.getLogger("session_pool")

# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PoolConfig:
    """Session pool configuration."""
    # Pool size
    min_instances: int = 2           # keep at least this many warm
    max_instances: int = 8           # never spawn more than this

    # Lifecycle
    idle_timeout_sec: float = 60.0   # close instances idle for this long
    max_age_sec: float = 1800.0      # force-restart instances older than this (30 min)
    max_memory_mb: int = 400         # restart instances using more than this

    # Failure handling
    max_failures_before_recycle: int = 3
    cooldown_sec: float = 15.0       # after recycle, wait before reuse

    # Identity
    identity_manager: Optional[IdentityManager] = None
    profiles: dict = field(default_factory=dict)  # profile_id -> FingerprintProfile


# ═══════════════════════════════════════════════════════════════════
# Browser Instance
# ═══════════════════════════════════════════════════════════════════

class BrowserInstance:
    """
    A Chrome instance with its own identity (user-data-dir + profile).

    This is the unit of isolation: one instance serves one "digital
    identity" — cookies, localStorage, and fingerprints are kept separate
    from every other instance.
    """

    def __init__(self, name: str, profile: Optional[FingerprintProfile],
                 chrome_args: Optional[list[str]] = None,
                 headful: bool = False):
        self.name = name
        self.profile = profile
        self.headful = headful
        self._user_data_dir = tempfile.mkdtemp(prefix=f"ub_{name}_")
        self.transport: Optional[CDPTransport] = None
        self.created_at = time.monotonic()
        self.last_used = self.created_at
        self.failures = 0
        self._lock = asyncio.Lock()
        self._chrome_args = chrome_args

    async def start(self) -> CDPTransport:
        """Launch Chrome with this instance's identity."""
        async with self._lock:
            if self.transport:
                return self.transport

            transport = CDPTransport()
            args = list(self._chrome_args or [])
            if self.profile:
                # Pass profile-specific UA
                args.append(f"--user-agent={self.profile.user_agent}")
                # Viewport
                vp = self.profile.viewport
                args.append(f"--window-size={vp['width']},{vp['height']}")
                # Disable the default UA override to use our custom one
                args.append("--disable-features=UnexpireFlagsM111")

            await transport.start(
                user_data_dir=self._user_data_dir,
                args=args if args else None,
                headless=not self.headful,
            )
            self.transport = transport
            self.last_used = time.monotonic()
            return transport

    async def stop(self):
        """Stop Chrome and clean up."""
        async with self._lock:
            if self.transport:
                self.transport.stop()
                self.transport = None
            # Clean up temp dir
            if os.path.isdir(self._user_data_dir):
                for _ in range(3):
                    try:
                        shutil.rmtree(self._user_data_dir, ignore_errors=True)
                        break
                    except Exception:
                        time.sleep(0.5)

    def touch(self):
        """Update last_used timestamp."""
        self.last_used = time.monotonic()

    def record_failure(self):
        """Record a failure for this instance."""
        self.failures += 1
        logger.debug(f"Instance {self.name} failure #{self.failures}")

    def should_recycle(self, config: PoolConfig) -> bool:
        """Check if this instance should be recycled."""
        age = time.monotonic() - self.created_at
        if age > config.max_age_sec:
            logger.info(f"Instance {self.name} recycled: age {age:.0f}s > {config.max_age_sec}s")
            return True
        if self.failures >= config.max_failures_before_recycle:
            logger.info(f"Instance {self.name} recycled: {self.failures} failures")
            return True
        return False

    async def is_healthy(self) -> bool:
        """Check if Chrome is still responsive."""
        if not self.transport or not self.transport._running:
            return False
        try:
            await self.transport._get_version()
            return True
        except Exception:
            return False

    def __repr__(self):
        return f"<BrowserInstance {self.name} ({self.profile.name if self.profile else 'no-profile'})>"


# ═══════════════════════════════════════════════════════════════════
# Session Pool
# ═══════════════════════════════════════════════════════════════════

class SessionPool:
    """
    Pool of browser instances, each bound to a site identity.

    Maps site → instance, isolates identities, recycles on failure.
    """

    def __init__(self, config: Optional[PoolConfig] = None):
        self.config = config or PoolConfig()
        self._instances: dict[str, BrowserInstance] = {}
        self._site_to_instance: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._maintenance_task: Optional[asyncio.Task] = None
        self._id_counter = 0
        self._headful_sites: set[str] = set()   # sites escalated to headful mode

    # ── Headful escalation ─────────────────────────────────────────

    async def escalate_to_headful(self, site: str) -> BrowserInstance:
        """Recycle the site's instance and relaunch it headful (real window).
        Used when a CF challenge wall is detected that headless can't pass."""
        async with self._lock:
            inst_name = self._site_to_instance.get(site)
            if inst_name and inst_name in self._instances:
                await self._recycle_instance(inst_name, None)
            self._headful_sites.add(site)
            logger.info(f"Escalating {site} to headful mode")
            return await self._new_instance_for_site(site)

    def site_is_headful(self, site: str) -> bool:
        return site in self._headful_sites

    # ── Public API ─────────────────────────────────────────────────

    async def start(self):
        """Start the pool and begin maintenance."""
        logger.info(f"Session pool starting (min={self.config.min_instances}, "
                    f"max={self.config.max_instances})")
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def get_instance(self, site: str) -> BrowserInstance:
        """
        Get a browser instance for a site.

        Rules:
        - Same site → same instance (identity persistence)
        - Different sites → different instances (identity isolation)
        """
        async with self._lock:
            # Already have an instance for this site?
            inst_name = self._site_to_instance.get(site)
            if inst_name and inst_name in self._instances:
                inst = self._instances[inst_name]
                # Recycle if unhealthy
                if inst.should_recycle(self.config):
                    logger.info(f"Recycling unhealthy instance {inst_name} for {site}")
                    await self._recycle_instance(inst_name, site)
                    return await self._new_instance_for_site(site)
                inst.touch()
                return inst

            # Assign a site to an existing instance if we have one with no site
            # (allows pooling when sites don't need isolation)
            for name, inst in self._instances.items():
                if not inst.name.startswith("site_"):
                    # Unused instance (never bound to site)
                    if inst.failures == 0 and not inst.should_recycle(self.config):
                        self._site_to_instance[site] = name
                        inst.touch()
                        return inst

            # Create new instance
            return await self._new_instance_for_site(site)

    async def get_session(self, site: str,
                          create: bool = True) -> "CDPSession":
        """Get a CDP session (tab) for a site from a fresh browser."""
        inst = await self.get_instance(site)
        transport = await inst.start()
        session = await transport.create_session()
        inst.touch()
        return session

    async def _new_instance_for_site(self, site: str) -> BrowserInstance:
        """Create a new instance, assign it to a site."""
        if len(self._instances) >= self.config.max_instances:
            # Recycle the least-recently-used instance
            lru_name = min(
                self._instances,
                key=lambda n: (n.startswith("site_"), -self._instances[n].last_used),
            )
            logger.info(f"Pool full, recycling LRU: {lru_name}")
            await self._recycle_instance(lru_name, None)

        self._id_counter += 1
        profile = self._get_profile_for_site(site)

        name = f"site_{site.replace('.', '_').replace(':', '_')}"
        headful = site in self._headful_sites
        instance = BrowserInstance(name, profile, headful=headful)
        self._instances[name] = instance
        self._site_to_instance[site] = name
        logger.info(f"New browser instance: {name} (profile: "
                    f"{profile.name if profile else 'default'}, "
                    f"headful={headful})")
        return instance

    def _get_profile_for_site(self, site: str) -> Optional[FingerprintProfile]:
        """Get a fingerprint profile for a site from the identity pool."""
        im = self.config.identity_manager
        if not im:
            return None
        try:
            profile, is_new = im.get_profile_for_site(site)
            return profile
        except Exception as e:
            logger.warning(f"Failed to get profile for {site}: {e}")
            return None

    async def _recycle_instance(self, name: str, new_site: Optional[str]):
        """Stop an instance and remove it from tracking."""
        inst = self._instances.pop(name, None)
        if not inst:
            return
        # Remove site mapping
        for site, iname in list(self._site_to_instance.items()):
            if iname == name:
                del self._site_to_instance[site]
        logger.info(f"Recycling instance {name}")
        try:
            await inst.stop()
        except Exception as e:
            logger.warning(f"Failed to stop instance {name}: {e}")

    # ── Maintenance ────────────────────────────────────────────────

    async def _maintenance_loop(self):
        """Periodic maintenance: close idle instances, recycle stale ones."""
        while True:
            try:
                await asyncio.sleep(10.0)
                await self._perform_maintenance()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Maintenance error: {e}")

    async def _perform_maintenance(self):
        """Perform pool maintenance."""
        now = time.monotonic()
        # 1. Close idle instances (over min)
        idle = [
            name for name, inst in self._instances.items()
            if (now - inst.last_used) > self.config.idle_timeout_sec
            and self._is_recyclable(name)
        ]
        # Keep at least min_instances alive
        safe_to_close = max(0, len(self._instances) - self.config.min_instances)
        for name in idle[:safe_to_close]:
            logger.info(f"Closing idle instance: {name}")
            await self._recycle_instance(name, None)

        # 2. Restart stale instances (either by age or failures)
        for name, inst in list(self._instances.items()):
            if inst.should_recycle(self.config):
                site = self._get_site_for_instance(name)
                await self._recycle_instance(name, None)
                if site:
                    # Recreate for the site (fresh instance)
                    new_inst = await self._new_instance_for_site(site)
                    logger.info(f"Restarted {name} → {new_inst.name}")

    def _is_recyclable(self, name: str) -> bool:
        """Check if instance has any site mapped to it."""
        return name not in self._site_to_instance.values()

    def _get_site_for_instance(self, name: str) -> Optional[str]:
        for site, iname in self._site_to_instance.items():
            if iname == name:
                return site
        return None

    # ── Shutdown ───────────────────────────────────────────────────

    async def close(self):
        """Stop all instances and clean up."""
        if self._maintenance_task:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
            self._maintenance_task = None

        for name, inst in list(self._instances.items()):
            logger.info(f"Closing instance: {name}")
            await inst.stop()
        self._instances.clear()
        self._site_to_instance.clear()

    # ── Status ─────────────────────────────────────────────────────

    async def status(self) -> dict:
        """Get pool status."""
        status = {
            "total_instances": len(self._instances),
            "max_instances": self.config.max_instances,
            "min_instances": self.config.min_instances,
            "sites_mapped": len(self._site_to_instance),
            "headful_sites": sorted(self._headful_sites),
            "instances": [],
        }
        for name, inst in self._instances.items():
            status["instances"].append({
                "name": name,
                "profile": inst.profile.name if inst.profile else "default",
                "age_sec": round(time.monotonic() - inst.created_at, 1),
                "idle_sec": round(time.monotonic() - inst.last_used, 1),
                "failures": inst.failures,
                "headful": inst.headful,
                "running": bool(inst.transport and inst.transport._running),
            })
        return status


# ═══════════════════════════════════════════════════════════════════
# Quick Test
# ═══════════════════════════════════════════════════════════════════

async def test():
    """Test session pool with live browser."""
    import sys, os, json
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from browser.identity import IdentityManager

    # Create identity manager
    data_dir = tempfile.mkdtemp(prefix="pool_test_")
    im = IdentityManager(data_dir)
    # Add a couple synthetic profiles
    for p in im.factory.synthesize(count=2):
        im.add_profile(p)

    # Create pool config with identity manager
    config = PoolConfig(
        min_instances=1,
        max_instances=3,
        identity_manager=im,
    )
    pool = SessionPool(config)

    try:
        await pool.start()

        # Test 1: Get session for a site
        print("=== Test 1: Get session for site ===")
        sess1 = await pool.get_session("github.com")
        print(f"  Session: {sess1}")
        print(f"  Status: {json.dumps(await pool.status(), indent=2)}")

        # Test 2: Same site returns same instance
        print("\n=== Test 2: Same site isolation ===")
        sess2 = await pool.get_session("github.com")
        inst1 = pool._instances.get(pool._site_to_instance.get("github.com"))
        print(f"  Same instance for github.com: {inst1.name}")

        # Test 3: Different site gets different instance
        print("\n=== Test 3: Different site isolation ===")
        sess3 = await pool.get_session("medium.com")
        print(f"  Instances: {list(pool._instances.keys())}")
        print(f"  Sites mapped: {list(pool._site_to_instance.keys())}")

        # Test 4: Navigate with the session
        print("\n=== Test 4: Navigation ===")
        await sess3.send("Page.enable")
        await sess3.send("Runtime.enable")
        await sess3.navigate("https://example.com/", wait_until="load")
        title = await sess3.get_title()
        print(f"  Title: {title}")

        # Test 5: Check isolation of cookies
        print("\n=== Test 5: Cookie isolation ===")
        await sess3.set_cookie("test_cookie", "site_value",
                                url="https://example.com/")
        cookies = await sess3.get_cookies()
        print(f"  Cookies on medium session: {[c['name'] for c in cookies]}")

        print("\nTest PASSED")

    finally:
        await pool.close()
        im.close()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    import sys
    import json
    logging.basicConfig(
        level=logging.DEBUG if "--debug" in sys.argv else logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s [%(name)s] %(message)s",
    )
    asyncio.run(test())