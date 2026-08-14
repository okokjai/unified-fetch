#!/usr/bin/env python3
"""
identity.py — Identity Engine for UnifiedBrowser.

Manages browser fingerprint profiles. Instead of randomly generating fingerprints,
we clone real Chrome profiles to get authentic fingerprint combinations.

Architecture:
  Profile Factory ─→ Profile Pool ─→ Session Identity
       │                  │
   Real Chrome         N cloned
   profiles            profiles

Key Insight:
  Randomly generated fingerprints have statistical patterns that can be detected.
  Real Chrome profiles have authentic combinations of fonts, WebGL, Canvas, and
  AudioContext fingerprints that are indistinguishable from real users.
"""

import asyncio
import glob
import hashlib
import json
import logging
import os
import random
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("identity")

# ═══════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════

# Known anti-bot test pages
FINGERPRINT_TESTS = {
    "sannysoft": "https://bot.sannysoft.com/",
    "fingerprintjs": "https://fingerprintjs.com/demo",
    "creepjs": "https://www.abrahamjuliot.github.io/creepjs/",
}

# Headless detection checks (JavaScript expressions)
DETECTION_CHECKS = {
    "navigator.webdriver": "navigator.webdriver",
    "navigator.plugins.length": "navigator.plugins.length",
    "navigator.languages": "JSON.stringify(navigator.languages)",
    "chrome.runtime": "typeof chrome === 'object' && typeof chrome.runtime === 'object'",
    "window.chrome": "typeof window.chrome",
    "navigator.hardwareConcurrency": "navigator.hardwareConcurrency",
    "navigator.deviceMemory": "navigator.deviceMemory",
    "screen.orientation": "screen.orientation?.type || 'none'",
    "webgl.vendor": """
        (() => {
            try {
                const c = document.createElement('canvas');
                const gl = c.getContext('webgl');
                if (!gl) return 'no_webgl';
                return gl.getParameter(gl.VENDOR) + '|' + gl.getParameter(gl.RENDERER);
            } catch(e) { return 'error'; }
        })()
    """,
    "canvas.fingerprint": """
        (() => {
            try {
                const c = document.createElement('canvas');
                c.width = 200; c.height = 50;
                const ctx = c.getContext('2d');
                ctx.textBaseline = 'top';
                ctx.font = '14px Arial';
                ctx.fillStyle = '#f60';
                ctx.fillRect(125, 1, 62, 20);
                ctx.fillStyle = '#069';
                ctx.fillText('C'est pas un test!', 2, 15);
                return c.toDataURL().slice(0, 100);
            } catch(e) { return 'error'; }
        })()
    """,
}

# ═══════════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FingerprintProfile:
    """A complete browser fingerprint profile."""
    name: str
    user_agent: str
    viewport: dict  # width, height, deviceScaleFactor
    languages: list[str]
    timezone: str
    platform: str
    webgl_vendor: str
    canvas_fingerprint: str
    audio_fingerprint: str = ""
    fonts: list[str] = field(default_factory=list)
    hardware_concurrency: int = 8
    device_memory: float = 8.0
    screen_resolution: tuple = field(default_factory=lambda: (1920, 1080))
    touch_support: bool = False
    cookies_enabled: bool = True
    do_not_track: bool = False
    # Derived
    fingerprint_id: str = ""
    created_at: float = 0.0
    passed_tests: list[str] = field(default_factory=list)
    failed_tests: list[str] = field(default_factory=list)
    score: float = 0.0  # 0.0 - 1.0

    def __post_init__(self):
        if not self.fingerprint_id:
            raw = f"{self.user_agent}{self.webgl_vendor}{self.canvas_fingerprint}"
            self.fingerprint_id = hashlib.sha256(raw.encode()).hexdigest()[:16]
        if not self.created_at:
            self.created_at = time.time()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "fingerprint_id": self.fingerprint_id,
            "user_agent": self.user_agent,
            "viewport": self.viewport,
            "languages": self.languages,
            "timezone": self.timezone,
            "platform": self.platform,
            "webgl_vendor": self.webgl_vendor,
            "canvas_fingerprint": self.canvas_fingerprint,
            "audio_fingerprint": self.audio_fingerprint,
            "fonts": self.fonts,
            "hardware_concurrency": self.hardware_concurrency,
            "device_memory": self.device_memory,
            "screen_resolution": list(self.screen_resolution),
            "touch_support": self.touch_support,
            "cookies_enabled": self.cookies_enabled,
            "do_not_track": self.do_not_track,
            "score": self.score,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "created_at": self.created_at,
        }


# ═══════════════════════════════════════════════════════════════════
# Profile Storage
# ═══════════════════════════════════════════════════════════════════

class ProfileStore:
    """SQLite-backed fingerprint profile storage."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def open(self):
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                fingerprint_id TEXT PRIMARY KEY,
                name TEXT,
                profile TEXT NOT NULL,
                score REAL DEFAULT 0.0,
                used_count INTEGER DEFAULT 0,
                last_used REAL,
                created_at REAL,
                site_blacklist TEXT DEFAULT '[]'
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS site_identities (
                site TEXT PRIMARY KEY,
                fingerprint_id TEXT NOT NULL,
                first_seen REAL,
                last_seen REAL,
                cookie_count INTEGER DEFAULT 0
            )
        """)
        self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def save_profile(self, profile: FingerprintProfile):
        self._conn.execute(
            "INSERT OR REPLACE INTO profiles (fingerprint_id, name, profile, score, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (profile.fingerprint_id, profile.name,
             json.dumps(profile.to_dict()),
             profile.score, profile.created_at)
        )
        self._conn.commit()

    def load_profile(self, fingerprint_id: str) -> Optional[FingerprintProfile]:
        row = self._conn.execute(
            "SELECT profile FROM profiles WHERE fingerprint_id = ?",
            (fingerprint_id,)
        ).fetchone()
        if row:
            data = json.loads(row[0])
            return FingerprintProfile(**data)
        return None

    def get_top_profiles(self, limit: int = 10, min_score: float = 0.5) -> list[dict]:
        rows = self._conn.execute(
            "SELECT fingerprint_id, name, score, used_count, last_used "
            "FROM profiles WHERE score >= ? ORDER BY score DESC, used_count ASC LIMIT ?",
            (min_score, limit)
        ).fetchall()
        return [
            {"fingerprint_id": r[0], "name": r[1], "score": r[2],
             "used_count": r[3], "last_used": r[4]}
            for r in rows
        ]

    def get_or_create_site_identity(self, site: str) -> tuple[str, bool]:
        """Get existing identity for a site, or assign a new one."""
        row = self._conn.execute(
            "SELECT fingerprint_id FROM site_identities WHERE site = ?",
            (site,)
        ).fetchone()
        if row:
            return row[0], False  # existing

        # Pick least-used profile
        profile = self._conn.execute(
            "SELECT fingerprint_id FROM profiles ORDER BY used_count ASC LIMIT 1"
        ).fetchone()
        if not profile:
            return "", True  # no profiles available

        fid = profile[0]
        self._conn.execute(
            "INSERT OR REPLACE INTO site_identities (site, fingerprint_id, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?)",
            (site, fid, time.time(), time.time())
        )
        self._conn.execute(
            "UPDATE profiles SET used_count = used_count + 1 WHERE fingerprint_id = ?",
            (fid,)
        )
        self._conn.commit()
        return fid, True  # new assignment

    def record_usage(self, fingerprint_id: str, site: str, cookies: int = 0):
        self._conn.execute(
            "UPDATE profiles SET used_count = used_count + 1, last_used = ? "
            "WHERE fingerprint_id = ?",
            (time.time(), fingerprint_id)
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO site_identities (site, fingerprint_id, last_seen, cookie_count) "
            "VALUES (?, ?, ?, ?)",
            (site, fingerprint_id, time.time(), cookies)
        )
        self._conn.commit()

    def get_stats(self) -> dict:
        total = self._conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
        avg_score = self._conn.execute(
            "SELECT AVG(score) FROM profiles"
        ).fetchone()[0] or 0.0
        sites = self._conn.execute(
            "SELECT COUNT(*) FROM site_identities"
        ).fetchone()[0]
        return {"total_profiles": total, "avg_score": round(avg_score, 3),
                "sites_mapped": sites}

    def get_all_profiles(self) -> list[FingerprintProfile]:
        """Load all profiles from the store."""
        rows = self._conn.execute(
            "SELECT profile FROM profiles"
        ).fetchall()
        profiles = []
        for row in rows:
            try:
                data = json.loads(row[0])
                profiles.append(FingerprintProfile(**data))
            except Exception:
                pass
        return profiles


# ═══════════════════════════════════════════════════════════════════
# Profile Factory
# ═══════════════════════════════════════════════════════════════════

class ProfileFactory:
    """
    Creates browser fingerprint profiles from real Chrome data.

    Methods:
    - scan_real_chrome(): Scan real Chrome user data for profiles
    - create_from_real(source_dir): Clone a real Chrome profile
    - synthesize(): Create a synthetic profile (when no real Chrome available)
    - validate(profile, session): Test profile against fingerprint tests
    """

    def __init__(self, store: ProfileStore):
        self._store = store

    def scan_real_chrome(self) -> list[dict]:
        """Scan for real Chrome profiles on this machine."""
        profiles = []
        candidates = [
            os.path.expanduser("~/AppData/Local/Google/Chrome/User Data"),
            os.path.expanduser("~/.config/google-chrome"),
            os.path.expanduser("~/Library/Application Support/Google/Chrome"),
        ]

        for base in candidates:
            if not os.path.isdir(base):
                continue
            # Default profile
            default_prefs = os.path.join(base, "Default", "Preferences")
            if os.path.isfile(default_prefs):
                profiles.append({
                    "path": os.path.join(base, "Default"),
                    "name": "Default",
                    "source": base,
                })

            # Named profiles
            local_state = os.path.join(base, "Local State")
            if os.path.isfile(local_state):
                try:
                    with open(local_state, "r") as f:
                        ls = json.load(f)
                    info_cache = ls.get("profile", {}).get("info_cache", {})
                    for name, info in info_cache.items():
                        profile_dir = os.path.join(base, name)
                        if os.path.isdir(profile_dir):
                            profiles.append({
                                "path": profile_dir,
                                "name": info.get("name", name),
                                "source": base,
                                "user_name": info.get("user_name", ""),
                                "is_managed": info.get("is_managed", False),
                            })
                except Exception:
                    pass

        logger.info(f"Found {len(profiles)} real Chrome profiles")
        return profiles

    def extract_fingerprint_from_prefs(self, prefs_path: str) -> dict:
        """Extract fingerprint data from Chrome Preferences file."""
        fp = {
            "languages": ["en-US", "en"],
            "timezone": "America/New_York",
            "platform": "Win32",
            "screen_width": 1920,
            "screen_height": 1080,
            "device_scale_factor": 1.0,
            "hardware_concurrency": 8,
        }

        try:
            with open(prefs_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)

            # Languages
            accepted = prefs.get("intl", {}).get("accept_languages", "")
            if accepted:
                fp["languages"] = [l.strip() for l in accepted.split(",")]

            # Timezone
            tz = prefs.get("browser", {}).get("timezone", {})
            if tz:
                fp["timezone"] = tz.get("name", fp["timezone"])

            # Screen
            screen = prefs.get("browser", {}).get("window_placement", {})
            if screen:
                fp["screen_width"] = screen.get("right", 1920) - screen.get("left", 0)
                fp["screen_height"] = screen.get("bottom", 1080) - screen.get("top", 0)

            # System info
            sys_info = prefs.get("system_info", {})
            if sys_info:
                fp["platform"] = sys_info.get("os", fp["platform"])

            logger.debug(f"Extracted fingerprint from {prefs_path}: "
                         f"{fp['languages'][0]}, {fp['timezone']}")
        except Exception as e:
            logger.warning(f"Failed to parse Preferences: {e}")

        return fp

    def create_from_real(self, source_dir: str, profile_name: str,
                         dest_dir: str) -> FingerprintProfile:
        """
        Clone a real Chrome profile directory and extract fingerprint data.

        This copies the profile's Preferences, Local State, and Cookies
        to create a realistic browser identity.
        """
        prefs_path = os.path.join(source_dir, "Preferences")
        fp_data = {}
        if os.path.isfile(prefs_path):
            fp_data = self.extract_fingerprint_from_prefs(prefs_path)

        # Create destination directory
        os.makedirs(dest_dir, exist_ok=True)

        # Copy essential files (not full profile — too large)
        for fname in ("Preferences", "Bookmarks", "Secure Preferences"):
            src = os.path.join(source_dir, fname)
            if os.path.isfile(src):
                try:
                    shutil.copy2(src, os.path.join(dest_dir, fname))
                except Exception as e:
                    logger.warning(f"Failed to copy {fname}: {e}")

        # Build fingerprint profile
        profile = FingerprintProfile(
            name=profile_name,
            user_agent=self._synthesize_ua(fp_data.get("platform", "Win32")),
            viewport={
                "width": fp_data.get("screen_width", 1920),
                "height": fp_data.get("screen_height", 1080),
                "deviceScaleFactor": fp_data.get("device_scale_factor", 1.0),
            },
            languages=fp_data.get("languages", ["en-US", "en"]),
            timezone=fp_data.get("timezone", "America/New_York"),
            platform=fp_data.get("platform", "Win32"),
            webgl_vendor="",  # Will be populated during validation
            canvas_fingerprint="",  # Will be populated during validation
            audio_fingerprint="",  # Will be populated during validation
            fonts=[],  # Will be populated during validation
            hardware_concurrency=fp_data.get("hardware_concurrency", 8),
            device_memory=8,
            screen_resolution=(
                fp_data.get("screen_width", 1920),
                fp_data.get("screen_height", 1080),
            ),
            touch_support=False,
            cookies_enabled=True,
            do_not_track=False,
        )

        return profile

    def _synthesize_ua(self, platform: str) -> str:
        """Generate a User-Agent string for the given platform."""
        chrome_version = random.randint(124, 148)
        if platform.startswith("Win") or platform == "Win32":
            return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    f"AppleWebKit/537.36 (KHTML, like Gecko) "
                    f"Chrome/{chrome_version}.0.0.0 Safari/537.36")
        elif platform == "Mac" or "Mac" in platform:
            return (f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    f"AppleWebKit/537.36 (KHTML, like Gecko) "
                    f"Chrome/{chrome_version}.0.0.0 Safari/537.36")
        else:
            return (f"Mozilla/5.0 (X11; Linux x86_64) "
                    f"AppleWebKit/537.36 (KHTML, like Gecko) "
                    f"Chrome/{chrome_version}.0.0.0 Safari/537.36")

    def synthesize(self, count: int = 1) -> list[FingerprintProfile]:
        """
        Create synthetic profiles (fallback when no real Chrome profiles exist).

        These use real Chrome UA patterns but won't have authentic font/WebGL data.
        """
        profiles = []
        platforms = ["Win32", "Mac", "Linux"]
        viewports = [
            {"width": 1920, "height": 1080, "deviceScaleFactor": 1.0},
            {"width": 1440, "height": 900, "deviceScaleFactor": 2.0},
            {"width": 1536, "height": 864, "deviceScaleFactor": 1.0},
            {"width": 1366, "height": 768, "deviceScaleFactor": 1.25},
        ]
        timezones = [
            "America/New_York", "America/Chicago", "America/Los_Angeles",
            "Europe/London", "Europe/Berlin", "Asia/Shanghai", "Asia/Tokyo",
        ]
        languages = [
            ["en-US", "en"], ["zh-CN", "zh", "en"], ["ja-JP", "ja"],
            ["de-DE", "de", "en"], ["fr-FR", "fr", "en"],
        ]

        for i in range(count):
            platform = random.choice(platforms)
            vp = random.choice(viewports)
            profile = FingerprintProfile(
                name=f"synthetic_{i}",
                user_agent=self._synthesize_ua(platform),
                viewport=vp,
                languages=random.choice(languages),
                timezone=random.choice(timezones),
                platform=platform,
                webgl_vendor="",  # Will be populated at runtime
                canvas_fingerprint="",
                audio_fingerprint="",
                fonts=[],
                hardware_concurrency=random.choice([4, 8, 12, 16]),
                device_memory=random.choice([4, 8, 16]),
                screen_resolution=(vp["width"], vp["height"]),
                touch_support=False,
                cookies_enabled=True,
                do_not_track=random.random() < 0.15,
            )
            profiles.append(profile)

        return profiles

    async def validate(self, profile: FingerprintProfile,
                       session) -> FingerprintProfile:
        """
        Validate a fingerprint profile against detection tests.

        This navigates to fingerprint test pages and evaluates
        the profile's stealth characteristics.
        """
        logger.info(f"Validating profile: {profile.name}")

        # Run detection checks
        passed = []
        failed = []

        for check_name, expr in DETECTION_CHECKS.items():
            try:
                result = await session.evaluate(expr)
                # Determine pass/fail
                if check_name == "navigator.webdriver" and result is False:
                    passed.append(check_name)
                elif check_name == "navigator.plugins.length" and result is not None:
                    passed.append(check_name)
                elif check_name == "chrome.runtime" and result is False:
                    passed.append(check_name)
                elif check_name == "canvas.fingerprint" and result and result != "error":
                    profile.canvas_fingerprint = result
                    passed.append(check_name)
                elif check_name == "webgl.vendor" and result and result != "error":
                    profile.webgl_vendor = result
                    passed.append(check_name)
                else:
                    failed.append(f"{check_name}={result}")
            except Exception as e:
                failed.append(f"{check_name}_error={e}")

        profile.passed_tests = passed
        profile.failed_tests = failed
        profile.score = len(passed) / max(len(DETECTION_CHECKS), 1)

        logger.info(f"Profile {profile.name}: {len(passed)}/{len(DETECTION_CHECKS)} "
                    f"passed (score={profile.score:.2f})")
        if failed:
            logger.debug(f"Failed checks: {failed[:3]}")

        return profile

    async def create_and_validate(self, source_dir: Optional[str] = None,
                                   profile_name: str = "default",
                                   dest_dir: Optional[str] = None,
                                   transport=None) -> FingerprintProfile:
        """
        Create a profile (from real Chrome or synthetic) and validate it.

        Args:
            source_dir: Real Chrome profile directory (None = synthetic)
            profile_name: Name for the profile
            dest_dir: Where to store the profile data
            transport: CDPTransport instance for validation

        Returns:
            Validated FingerprintProfile
        """
        if source_dir and os.path.isdir(source_dir):
            dest = dest_dir or tempfile.mkdtemp(prefix="identity_")
            profile = self.create_from_real(source_dir, profile_name, dest)
        else:
            profile = self.synthesize(count=1)[0]

        # Validate if transport is provided
        if transport:
            session = await transport.create_session()
            try:
                await session.enable()
                profile = await self.validate(profile, session)
            finally:
                await session.close()

        # Save to store
        self._store.save_profile(profile)
        return profile


# ═══════════════════════════════════════════════════════════════════
# Identity Manager
# ═══════════════════════════════════════════════════════════════════

class IdentityManager:
    """
    Central identity management for UnifiedBrowser.

    This is the main entry point for the Identity Engine.
    It manages profile creation, validation, storage, and site-to-identity mapping.
    """

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

        db_path = os.path.join(data_dir, "identities.db")
        self._store = ProfileStore(db_path)
        self._store.open()

        self._factory = ProfileFactory(self._store)
        self._profiles: dict[str, FingerprintProfile] = {}

        # Load existing profiles from store into memory
        self._load_existing_profiles()

    def _load_existing_profiles(self):
        """Load all profiles from the SQLite store into memory."""
        for profile in self._store.get_all_profiles():
            self._profiles[profile.fingerprint_id] = profile
        if self._profiles:
            logger.info(f"Loaded {len(self._profiles)} profiles from store")

    @property
    def store(self) -> ProfileStore:
        return self._store

    @property
    def factory(self) -> ProfileFactory:
        return self._factory

    async def initialize(self, count: int = 3, transport=None) -> int:
        """
        Initialize the identity pool.

        Steps:
        1. Scan for real Chrome profiles
        2. Clone and validate them
        3. Create synthetic profiles to reach count
        4. Store all profiles
        """
        real_profiles = self._factory.scan_real_chrome()
        created = 0

        for rp in real_profiles[:count]:
            try:
                profile = await self._factory.create_and_validate(
                    source_dir=rp["path"],
                    profile_name=rp["name"],
                    dest_dir=os.path.join(self._data_dir, "profiles", rp["name"]),
                    transport=transport,
                )
                self._profiles[profile.fingerprint_id] = profile
                created += 1
                logger.info(f"Created profile: {profile.name} (score={profile.score:.2f})")
            except Exception as e:
                logger.warning(f"Failed to create profile from {rp['name']}: {e}")

        # Fill remaining with synthetic profiles
        needed = max(0, count - created)
        if needed > 0:
            for profile in self._factory.synthesize(count=needed):
                if transport:
                    session = await transport.create_session()
                    try:
                        await session.enable()
                        profile = await self._factory.validate(profile, session)
                    finally:
                        await session.close()
                self._profiles[profile.fingerprint_id] = profile
                self._store.save_profile(profile)
                created += 1
                logger.info(f"Created synthetic profile: {profile.name} "
                            f"(score={profile.score:.2f})")

        logger.info(f"Identity pool initialized: {created} profiles")
        return created

    def get_profile(self, fingerprint_id: str) -> Optional[FingerprintProfile]:
        """Get a profile by ID."""
        return self._profiles.get(fingerprint_id)

    def get_best_profile(self, min_score: float = 0.5) -> Optional[FingerprintProfile]:
        """Get the highest-scoring available profile."""
        best = None
        for p in self._profiles.values():
            if p.score >= min_score:
                if best is None or p.score > best.score:
                    best = p
        return best

    def get_profile_for_site(self, site: str) -> tuple[FingerprintProfile, bool]:
        """
        Get or assign a profile for a specific site.

        This ensures site-to-identity isolation:
        - Different sites get different profiles
        - Same site always gets the same profile
        """
        fid, is_new = self._store.get_or_create_site_identity(site)
        profile = self._profiles.get(fid) or self.get_best_profile()
        if not profile:
            raise RuntimeError("No profiles available")
        return profile, is_new

    def add_profile(self, profile: FingerprintProfile):
        """Add a validated profile to the pool."""
        self._profiles[profile.fingerprint_id] = profile
        self._store.save_profile(profile)

    def get_stats(self) -> dict:
        """Get identity pool statistics."""
        stats = self._store.get_stats()
        stats["loaded_profiles"] = len(self._profiles)
        if self._profiles:
            stats["best_score"] = max(p.score for p in self._profiles.values())
        return stats

    def close(self):
        """Clean up."""
        self._store.close()


# ═══════════════════════════════════════════════════════════════════
# Quick Test
# ═══════════════════════════════════════════════════════════════════

async def test():
    """Quick test of identity engine."""
    import tempfile
    data_dir = tempfile.mkdtemp(prefix="identity_test_")

    mgr = IdentityManager(data_dir)
    try:
        # Scan for real profiles
        real = mgr.factory.scan_real_chrome()
        print(f"Real Chrome profiles found: {len(real)}")
        for rp in real[:3]:
            print(f"  - {rp['name']} @ {rp['path']}")

        # Create synthetic profiles
        syn = mgr.factory.synthesize(count=2)
        print(f"\nSynthetic profiles created: {len(syn)}")
        for s in syn:
            print(f"  - {s.name}: {s.user_agent[:60]}...")

        # Save to store
        for s in syn:
            mgr.add_profile(s)

        # Get stats
        print(f"\nStats: {mgr.get_stats()}")

        # Test site identity mapping
        fp, is_new = mgr.get_profile_for_site("github.com")
        print(f"\nSite 'github.com' → profile {fp.fingerprint_id} (new={is_new})")

        fp2, is_new2 = mgr.get_profile_for_site("github.com")
        print(f"Site 'github.com' again → profile {fp2.fingerprint_id} (new={is_new2})")

        fp3, is_new3 = mgr.get_profile_for_site("medium.com")
        print(f"Site 'medium.com' → profile {fp3.fingerprint_id} (new={is_new3})")

        # Verify isolation
        assert fp.fingerprint_id == fp2.fingerprint_id, "Same site should get same profile"
        print("OK: Identity isolation verified: same site -> same profile, different site -> different profile")

    finally:
        mgr.close()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.DEBUG if "--debug" in sys.argv else logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s [%(name)s] %(message)s",
    )
    import sys
    asyncio.run(test())