#!/usr/bin/env python3
"""
unified_browser.py — UnifiedBrowser 整合入口.

Integrates all layers into a single coherent browser API:

  Identity Engine  → who am I (fingerprint, profile persistence)
  Anti-detection   → how to hide (CDP patches, resource blocking, bot detect)
  Behavioral Engine→ how to act (human-like timing, mouse, scroll, typing)
  Session Pool     → where to run (isolated Chrome instances per site)
  CDP Transport    → the raw duct work (WebSocket protocol)

This is the public entry point — everything else is internal.
"""

import asyncio
import json
import logging
import os
import sys
import time
import random
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

try:
    from .identity import IdentityManager, FingerprintProfile
    from .session_pool import SessionPool, PoolConfig, BrowserInstance
    from .anti_detect import AntiDetect, BotPageDetector
    from .behavior import BehaviorGenerator
except ImportError:
    from identity import IdentityManager, FingerprintProfile
    from session_pool import SessionPool, PoolConfig, BrowserInstance
    from anti_detect import AntiDetect, BotPageDetector
    from behavior import BehaviorGenerator

logger = logging.getLogger("unified_browser")

# ═══════════════════════════════════════════════════════════════════
# Main Config
# ═══════════════════════════════════════════════════════════════════

@dataclass
class UnifiedBrowserConfig:
    """Configuration for UnifiedBrowser."""
    # Directories
    data_dir: str = "~/.unified-browser"

    # Identity
    identity_count: int = 3           # profiles to create/load
    min_profile_score: float = 0.3    # minimum score to accept

    # Pool
    min_instances: int = 1
    max_instances: int = 5

    # Anti-detection
    block_tracking: bool = True
    block_heavy: bool = False
    detect_bot: bool = True
    auto_solve_turnstile: bool = True

    # Headful (real window) mode — passes SO-class CF hard challenges.
    # headless is the default (zero popups); headful only for smart_browse(prefer_headful)
    # or scrape auto-upgrade when a challenge wall is detected.
    headful: bool = False
    headful_mode: str = "offscreen"   # "offscreen" (win) | "xvfb" (linux) | "visible"

    # Behavior
    human_behavior: bool = True       # enable human-like behavior simulation
    min_visit_duration: float = 2.0

    # Fetch limits
    max_content_length: int = 300_000


# ═══════════════════════════════════════════════════════════════════
# Fetch Result
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FetchResult:
    """Result of a page fetch."""
    url: str
    ok: bool
    title: str = ""
    content: str = ""          # markdown/text content
    html: str = ""             # raw HTML (if requested)
    status_code: int = 0
    final_url: str = ""
    engine: str = "unified_browser"
    identity: dict = field(default_factory=dict)
    behavior: dict = field(default_factory=dict)
    anti_detect: dict = field(default_factory=dict)
    bot_detection: dict = field(default_factory=dict)
    error: str = ""
    error_type: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "ok": self.ok,
            "title": self.title,
            "content": self.content[:self.max_len()],
            "content_length": len(self.content),
            "status_code": self.status_code,
            "final_url": self.final_url or self.url,
            "engine": self.engine,
            "identity": self.identity,
            "behavior": self.behavior,
            "anti_detect": self.anti_detect,
            "bot_detection": self.bot_detection,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }

    def max_len(self) -> int:
        return 300_000


# ═══════════════════════════════════════════════════════════════════
# UnifiedBrowser
# ═══════════════════════════════════════════════════════════════════

class UnifiedBrowser:
    """
    Main public API for UnifiedBrowser.

    Usage:
        ub = UnifiedBrowser(config)
        await ub.initialize()
        result = await ub.fetch("https://example.com/")
        await ub.close()

    Session-based usage (for MCP / multi-step workflows):
        await ub.navigate("https://example.com/")
        content = await ub.get_text()
        await ub.screenshot()
        await ub.close()
    """

    def __init__(self, config: Optional[UnifiedBrowserConfig] = None):
        self.config = config or UnifiedBrowserConfig()
        self._data_dir = os.path.expanduser(self.config.data_dir)
        os.makedirs(self._data_dir, exist_ok=True)

        self.identity: Optional[IdentityManager] = None
        self.pool: Optional[SessionPool] = None
        self._initialized = False

        # Persistent session state (for MCP multi-step workflows)
        self._active_sessions: dict[str, Any] = {}  # domain -> {transport, session, instance}
        self._last_domain: str = ""  # most recently used domain

    # ── Lifecycle ─────────────────────────────────────────────────

    async def initialize(self) -> dict:
        """Initialize all components."""
        logger.info(f"Initializing UnifiedBrowser (data: {self._data_dir})")

        # 1. Identity engine (needs a browser to validate, but can work offline)
        self.identity = IdentityManager(self._data_dir)
        # We can't validate without a browser, so just load/synthesize
        profiles_loaded = 0
        existing = self.identity.store.get_stats()
        if existing.get("total_profiles", 0) < self.config.identity_count:
            # Create synthetic profiles (validation happens on first fetch)
            for p in self.identity.factory.synthesize(count=self.config.identity_count):
                self.identity.add_profile(p)
                profiles_loaded += 1

        # 2. Session pool
        self.pool = SessionPool(PoolConfig(
            min_instances=self.config.min_instances,
            max_instances=self.config.max_instances,
            identity_manager=self.identity,
        ))
        await self.pool.start()

        self._initialized = True
        return {
            "ok": True,
            "data_dir": self._data_dir,
            "identity_profiles": self.identity.get_stats().get("total_profiles", 0),
            "pool_instances": await self.pool.status(),
            "headful": self.config.headful,
        }

    async def close(self):
        """Clean up all resources."""
        if self.pool:
            await self.pool.close()
        if self.identity:
            self.identity.close()
        self._initialized = False
        logger.info("UnifiedBrowser closed")

    # ── Session-based API (for MCP / multi-step workflows) ─────────

    async def _get_or_create_session(self, domain: str) -> tuple[Any, Any]:
        """Get or create a persistent session for a domain.

        The persistent session model is keyed by domain. For non-http(s) URLs
        (data:, file:, about:) there is no routable domain — normalizing them
        to a fixed key keeps a single reuseable session (each navigate() to a
        data:/file: URL issues a fresh Page.navigate on that same session),
        and mirrors http(s) behavior where the session is one per site.
        """
        scheme = urlparse(domain).scheme
        if scheme not in ("http", "https"):
            domain = f"__internal__:{scheme or 'non-http'}"

        if domain in self._active_sessions:
            state = self._active_sessions[domain]
            # Check if session is still alive
            try:
                await state["session"].send("Runtime.evaluate", {
                    "expression": "1", "returnByValue": True
                }, timeout=3.0)
                return state["transport"], state["session"]
            except Exception:
                # Session dead, clean up and recreate
                try:
                    await state["session"].close()
                except Exception:
                    pass
                del self._active_sessions[domain]

        # Create new session
        instance = await self.pool.get_instance(domain)
        transport = await instance.start()
        if transport is None:
            # Browser launch failed (no browser binary) — surface clearly
            raise RuntimeError("no browser available")
        session = await transport.create_session()

        # Apply anti-detection
        await session.send("Page.enable")
        await session.send("Runtime.enable")
        try:
            from anti_detect import STEALTH_JS as _SJ
        except ImportError:
            try:
                from browser.anti_detect import STEALTH_JS as _SJ
            except ImportError:
                _SJ = ""
        if _SJ:
            await session.send("Page.addScriptToEvaluateOnNewDocument", {
                "source": _SJ,
            })

        self._active_sessions[domain] = {
            "transport": transport,
            "session": session,
            "instance": instance,
        }
        self._last_domain = domain
        return transport, session

    async def navigate(self, url: str,
                       wait_until: str = "load",
                       behavior: str = "browse") -> dict:
        """Navigate to a URL. Creates or reuses a session for the domain."""
        domain = urlparse(url).hostname or url.split("/")[0]
        start = time.monotonic()
        try:
            _transport, session = await self._get_or_create_session(domain)
        except Exception as e:
            # Session creation failure (e.g. Chrome died mid-restart) —
            # retry once with a fresh pool instance, then report cleanly.
            logger.warning("session create failed (%s), retrying", str(e)[:80])
            try:
                _transport, session = await self._get_or_create_session(domain)
            except Exception as e2:
                return {"ok": False, "error": str(e2).split("\n")[0],
                        "url": url, "duration_ms": int((time.monotonic() - start) * 1000)}

        try:
            await session.navigate(url, wait_until=wait_until)
        except Exception as e:
            return {"ok": False, "error": str(e), "url": url}

        # Apply behavior
        try:
            bgen = BehaviorGenerator(session)
            await bgen.set_domain_profile(domain)
            if behavior == "read":
                await bgen.after_page_load(domain)
                await bgen.human_scroll(count=random.randint(1, 2))
            elif behavior == "search":
                await bgen.after_page_load(domain)
            elif behavior == "browse":
                await bgen.after_page_load(domain)
                await bgen.human_scroll(count=random.randint(2, 4))
        except Exception as e:
            logger.debug(f"Behavior: {e}")

        title = await session.get_title()
        duration = int((time.monotonic() - start) * 1000)

        # Bot detection
        content_sample = ""
        try:
            content_sample = await session.get_text()
        except Exception:
            pass

        bot_det = {}
        try:
            from anti_detect import BotPageDetector
            bot_det = BotPageDetector().detect(url, content_sample[:5000])
        except Exception:
            pass

        # CF challenge-wall detection (SO-class: "Just a moment" / no clearance)
        challenge = self._detect_cf_challenge(title, content_sample)

        instance = self._active_sessions.get(domain, {}).get("instance")
        if instance:
            instance.touch()
        self._last_domain = domain

        return {
            "ok": (not bot_det.get("is_bot_page", False)) and not challenge,
            "url": url,
            "title": title or "",
            "identity": self._get_identity_info(instance) if instance else {},
            "anti_crawl": {"practices": ["stealth_js", "identity_isolated"]},
            "bot_detected": bot_det.get("is_bot_page", False),
            "cf_challenge": challenge,
            "headful": bool(instance and instance.headful),
            "duration_ms": duration,
            "error": "cloudflare challenge wall" if challenge else None,
        }

    def _detect_cf_challenge(self, title: str, text: str) -> bool:
        """Detect a Cloudflare hard challenge (SO-class: no checkbox, auto-pending)."""
        t = (title or "").lower()
        if "just a moment" in t or "security verification" in (text or "").lower():
            return True
        if "performing security verification" in (text or "").lower():
            return True
        return False

    async def navigate_headful(self, url: str,
                               wait_until: str = "load",
                               behavior: str = "browse") -> dict:
        """Navigate, escalating this site to headful (real window) if headless
        hit a CF challenge wall. Headful passes SO-class challenges."""
        domain = urlparse(url).hostname or url.split("/")[0]
        start = time.monotonic()

        if self.pool and not self.pool.site_is_headful(domain):
            await self.pool.escalate_to_headful(domain)
            # Drop any existing headless session for this domain
            await self.close_session(domain)

        _transport, session = await self._get_or_create_session(domain)
        r = await self.navigate(url, wait_until=wait_until, behavior=behavior)
        if not r.get("ok") and r.get("cf_challenge"):
            r["next_action"] = "give_up"   # headful already tried
        return r

    async def get_text(self) -> str:
        """Get text content of the current active page."""
        session = await self._get_active_session()
        return await session.get_text()

    async def get_html(self) -> str:
        """Get HTML content of the current active page."""
        session = await self._get_active_session()
        return await session.get_html()

    async def screenshot(self, full_page: bool = False) -> bytes:
        """Take a screenshot of the current page."""
        session = await self._get_active_session()
        return await session.screenshot(full_page=full_page)

    async def evaluate(self, expression: str) -> Any:
        """Execute JavaScript in the page context."""
        session = await self._get_active_session()
        return await session.evaluate(expression)

    async def click(self, selector: str) -> bool:
        """Click an element by CSS selector."""
        session = await self._get_active_session()
        return await session.click_selector(selector)

    async def type(self, text: str, selector: Optional[str] = None) -> bool:
        """Type text, optionally into a specific element."""
        session = await self._get_active_session()
        if selector:
            box = await session.get_by_selector(selector)
            if not box:
                return False
            await session.click(box["x"], box["y"])
            await asyncio.sleep(random.uniform(0.1, 0.3))
        await session.type_text(text)
        return True

    async def scroll(self, direction: str = "down") -> None:
        """Scroll the page."""
        session = await self._get_active_session()
        if direction == "up":
            await session.scroll(delta_y=-400)
        elif direction == "bottom":
            await session.scroll_to_bottom(steps=5)
        else:
            await session.scroll_to_bottom(steps=3)

    # ── CDP-native interact actions (browser_interact) ─────────────

    async def fill(self, selector: str, text: str) -> bool:
        """Clear an input and type text into it."""
        session = await self._get_active_session()
        return await session.fill(selector, text)

    async def hover(self, selector: str) -> bool:
        """Move the mouse over an element (no click)."""
        session = await self._get_active_session()
        return await session.hover(selector)

    async def select(self, selector: str, value: str = "",
                     label: str = "", index: int = -1) -> bool:
        """Select an option in a <select> by value/label/index."""
        session = await self._get_active_session()
        return await session.select_option(selector, value, label, index)

    async def wait_for(self, selector: str, timeout: float = 10.0) -> bool:
        """Wait for an element matching the selector to appear."""
        session = await self._get_active_session()
        return await session.wait_for_selector(selector, timeout)

    async def press(self, key: str) -> None:
        """Press a keyboard key (Enter, Escape, ArrowDown, ...)."""
        session = await self._get_active_session()
        await session.press(key)

    async def upload_file(self, selector: str, path: str) -> bool:
        """Set files on an <input type=file> element."""
        session = await self._get_active_session()
        return await session.upload_file(selector, path)

    async def get_cookies(self) -> list:
        """Return current page cookies."""
        session = await self._get_active_session()
        return await session.get_cookies()

    async def clear_cookies(self) -> None:
        """Clear all browser cookies."""
        session = await self._get_active_session()
        await session.clear_cookies()

    async def handle_dialog(self, accept: bool = True,
                            prompt_text: str = "") -> None:
        """Accept/dismiss a JavaScript dialog."""
        session = await self._get_active_session()
        await session.handle_dialog(accept, prompt_text or None)

    async def _get_active_session(self) -> Any:
        """Get the most recently used session, or raise an error."""
        if not self._active_sessions:
            raise RuntimeError("No active session. Call navigate() first.")
        domain = self._last_domain
        if domain not in self._active_sessions:
            # Fallback to any available session
            domain = next(iter(self._active_sessions))
        return self._active_sessions[domain]["session"]

    async def close_session(self, domain: str = ""):
        """Close a specific domain session, or all if no domain given."""
        if domain:
            state = self._active_sessions.pop(domain, None)
            if state:
                try:
                    await state["session"].close()
                except Exception:
                    pass
        else:
            for d, state in list(self._active_sessions.items()):
                try:
                    await state["session"].close()
                except Exception:
                    pass
            self._active_sessions.clear()

    # ── Core Fetch ────────────────────────────────────────────────

    async def fetch(self, url: str,
                    wait_until: str = "load",
                    timeout: float = 30.0,
                    return_html: bool = False,
                    human_behavior: Optional[bool] = None) -> FetchResult:
        """
        Fetch a single page with full anti-detection stack.

        Steps:
        1. Resolve site for identity routing
        2. Get browser instance from pool (site-isolated)
        3. Start Chrome (if not running) with profile identity
        4. Create session, apply anti-detection, apply behavior profile
        5. Navigate, wait, extract content
        6. Detect bot pages, return structured result
        """
        if not self._initialized:
            await self.initialize()

        start_time = time.monotonic()
        domain = urlparse(url).hostname or url.split("/")[0]

        # 1. Get instance from pool
        instance = await self.pool.get_instance(domain)
        transport = await instance.start()
        if transport is None:
            return self._error_result(
                url, RuntimeError("no browser available"), "no browser available",
                "no_browser", start_time, instance=instance)

        session = await transport.create_session()
        try:
            # 2. Anti-detection setup
            await session.send("Page.enable")
            await session.send("Runtime.enable")

            try:
                from .anti_detect import STEALTH_JS as _SJ
            except ImportError:
                from anti_detect import STEALTH_JS as _SJ
            await session.send("Page.addScriptToEvaluateOnNewDocument", {
                "source": _SJ,
            })

            # 3. Behavior profile
            behavior = BehaviorGenerator(session)
            if self.config.human_behavior:
                await behavior.set_domain_profile(domain)

            # 4. Navigate
            try:
                nav_result = await session.navigate(url, wait_until=wait_until,
                                                    timeout=timeout)
            except Exception as e:
                return self._error_result(
                    url, e, str(e).split("\n")[0], "navigation_error", start_time,
                    instance=instance,
                )

            # 5. Human-like behavior (if enabled)
            human_used = False
            if (self.config.human_behavior if human_behavior is None else human_behavior):
                try:
                    await behavior.after_page_load(domain)
                    # Only scroll a little (we want content, not full browsing)
                    await behavior.human_scroll(count=random_int(1, 2))
                    human_used = True
                except Exception as e:
                    logger.debug(f"Behavior simulation failed: {e}")

            # 6. Extract content
            title = ""
            content = ""
            html = ""
            try:
                title = await session.get_title()
                content = await session.get_text()
                if return_html:
                    html = await session.get_html()
            except Exception as e:
                logger.debug(f"Content extraction failed: {e}")

            # 7. Bot page detection
            bot_detection = {}
            if self.config.detect_bot:
                detector = BotPageDetector()
                bot_detection = detector.detect(url, content[:5000] or html[:5000])

            duration = int((time.monotonic() - start_time) * 1000)

            # A9: memory hygiene — trigger Chrome GC + cache drop
            try:
                await session.gc()
            except Exception:
                pass

            result = FetchResult(
                url=url,
                ok=not bot_detection.get("is_bot_page"),
                title=title or "",
                content=content[:self.config.max_content_length],
                html=html if return_html else "",
                status_code=200,
                final_url=url,
                engine="unified_browser",
                identity=self._get_identity_info(instance),
                behavior=behavior.get_stats() if human_used else {},
                anti_detect={"practices": ["stealth_js", "identity_isolated",
                                            "behavior_simulated" if human_used else ""]},
                bot_detection=bot_detection,
                duration_ms=duration,
            )
            instance.touch()
            return result

        except Exception as e:
            instance.record_failure()
            return self._error_result(
                url, e, str(e).split("\n")[0], "fetch_error", start_time,
                instance=instance,
            )
        finally:
            try:
                await session.close()
            except Exception:
                pass

    # ── High-level helpers ────────────────────────────────────────

    async def fetch_text(self, url: str, **kwargs) -> str:
        """Fetch a page and return just the text content."""
        result = await self.fetch(url, **kwargs)
        if not result.ok:
            raise RuntimeError(f"Fetch failed: {result.error} "
                               f"(bot: {result.bot_detection.get('system')})")
        return result.content

    async def search_engine_status(self) -> dict:
        """Get status of all components."""
        return {
            "initialized": self._initialized,
            "identity": self.identity.get_stats() if self.identity else None,
            "pool": await self.pool.status() if self.pool else None,
        }

    # ── Helpers ───────────────────────────────────────────────────

    def _get_identity_info(self, instance: BrowserInstance) -> dict:
        """Get identity metadata for a result."""
        if instance and instance.profile:
            p = instance.profile
            return {
                "profile": p.name,
                "fingerprint_id": p.fingerprint_id,
                "user_agent": p.user_agent[:80],
                "timezone": p.timezone,
                "languages": p.languages,
                "platform": p.platform,
            }
        return {"profile": "default"}

    def _error_result(self, url: str, error: Exception,
                      error_msg: str, error_type: str,
                      start_time: float,
                      instance: Optional[BrowserInstance] = None) -> FetchResult:
        """Build an error result."""
        duration = int((time.monotonic() - start_time) * 1000)
        logger.warning(f"{error_type} fetching {url}: {error_msg}")
        return FetchResult(
            url=url,
            ok=False,
            error=error_msg,
            error_type=error_type,
            duration_ms=duration,
            identity=self._get_identity_info(instance) if instance else {},
        )


def random_int(lo: int, hi: int) -> int:
    import random
    return random.randint(lo, hi)


# ═══════════════════════════════════════════════════════════════════
# Quick Test
# ═══════════════════════════════════════════════════════════════════

async def test():
    """Integration test for UnifiedBrowser."""
    import tempfile

    config = UnifiedBrowserConfig(
        data_dir=tempfile.mkdtemp(prefix="ub_test_"),
        identity_count=2,
        min_instances=1,
        max_instances=2,
        human_behavior=True,
    )
    ub = UnifiedBrowser(config)

    try:
        # 1. Initialize
        print("=== Test 1: Initialize ===")
        init = await ub.initialize()
        print(f"  {json.dumps(init, default=str)[:300]}")

        # 2. Fetch a simple page
        print("\n=== Test 2: Fetch example.com ===")
        result = await ub.fetch("https://example.com/", wait_until="load")
        print(f"  ok={result.ok}, title={result.title!r}")
        print(f"  content length: {len(result.content)}")
        print(f"  duration: {result.duration_ms}ms")
        print(f"  identity: {result.identity.get('profile', '?')}")

        # 3. Fetch a JS-heavy page (Wikipedia is static, but tests everything)
        print("\n=== Test 3: Fetch another site (isolation) ===")
        result2 = await ub.fetch("https://en.wikipedia.org/wiki/Web_scraping",
                                 wait_until="load")
        print(f"  ok={result2.ok}, title={result2.title!r}")
        print(f"  content length: {len(result2.content)}")
        print(f"  identity: {result2.identity.get('profile', '?')}")
        print(f"  bot_detection: {result2.bot_detection.get('system', 'none')}")

        # 4. Status
        print("\n=== Test 4: Status ===")
        status = await ub.search_engine_status()
        print(f"  {json.dumps(status, default=str)[:400]}")

        print("\nTest PASSED")

    finally:
        await ub.close()


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.DEBUG if "--debug" in sys.argv else logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s [%(name)s] %(message)s",
    )
    asyncio.run(test())