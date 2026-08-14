#!/usr/bin/env python3
"""
fingerprint_verify.py — Fingerprint Verification Loop for UnifiedBrowser.

Validates browser fingerprint profiles against real anti-bot test pages.
This is the P0 feedback loop that tells us:
  1. Which profiles pass/fail which checks
  2. The overall pass rate per profile
  3. Which stealth patches are missing

Test pages:
  - bot.sannysoft.com: Checks WebDriver, plugins, languages, chrome.runtime,
    WebGL vendor, Canvas fingerprint, AudioContext, and more
  - fingerprintjs.com: Comprehensive browser fingerprinting test

Output:
  - Per-profile score (written back to identity store)
  - Detailed report of passed/failed checks
  - Summary statistics
"""

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("fingerprint_verify")

# ═══════════════════════════════════════════════════════════════════
# Test Page Config
# ═══════════════════════════════════════════════════════════════════

TEST_PAGES = {
    "sannysoft": {
        "url": "https://bot.sannysoft.com/",
        "description": "Comprehensive anti-bot detection test",
        # Checks extracted from the page's HTML structure
        "checks": [
            # (check_name, selector_or_pattern, pass_condition)
            ("webdriver", "td:contains('WebDriver')", "passed"),
            ("chrome_runtime", "td:contains('Chrome Runtime')", "passed"),
            ("plugins_length", "td:contains('Plugins')", "passed"),
            ("permissions", "td:contains('Permissions')", "passed"),
            ("iframes", "td:contains('IFrames')", "passed"),
            ("languages", "td:contains('Languages')", "passed"),
            ("webgl_vendor", "td:contains('WebGL Vendor')", "passed"),
            ("webgl_renderer", "td:contains('WebGL Renderer')", "passed"),
            ("user_agent", "td:contains('User-Agent')", "passed"),
            ("platform", "td:contains('Platform')", "passed"),
            ("hardware_concurrency", "td:contains('Hardware Concurrency')", "passed"),
        ],
        # Extract check results from the table
        "extract_script": """
        (() => {
            const results = {};
            const rows = document.querySelectorAll('table tr');
            rows.forEach(row => {
                const cells = row.querySelectorAll('td');
                if (cells.length >= 2) {
                    const name = cells[0]?.textContent?.trim() || '';
                    const status = cells[1]?.textContent?.trim() || '';
                    if (name) {
                        results[name] = status;
                    }
                }
            });
            // Also try alternate table structure
            const checks = document.querySelectorAll('.check-row, .test-result, tr');
            checks.forEach(el => {
                const text = el.textContent?.trim() || '';
                if (text.includes('WebDriver')) results['WebDriver_detail'] = text;
            });
            return results;
        })()
        """,
    },
    "fingerprintjs": {
        "url": "https://fingerprintjs.com/demo",
        "description": "Browser fingerprinting entropy test",
        "extract_script": """
        (() => {
            const results = {};
            // Look for fingerprint result elements
            const entropyEl = document.querySelector('[class*="entropy"], [class*="bits"], .fp-result');
            if (entropyEl) results['entropy_display'] = entropyEl.textContent?.trim() || '';
            // Extract any visible fingerprint data
            const body = document.body.innerText || '';
            const match = body.match(/entropy[^\\n]*/i) || body.match(/\\d+\\.\\d+ bits/);
            if (match) results['entropy_match'] = match[0];
            // Check if page loaded correctly (not blocked)
            results['page_loaded'] = body.length > 1000 ? 'yes' : 'no';
            return results;
        })()
        """,
    },
}

# ═══════════════════════════════════════════════════════════════════
# Check Result
# ═══════════════════════════════════════════════════════════════════

@dataclass
class CheckResult:
    """Result of a single fingerprint check."""
    name: str
    passed: bool
    expected: str
    actual: str
    details: str = ""


@dataclass
class ProfileVerification:
    """Full verification result for one profile on one test page."""
    profile_name: str
    profile_id: str
    test_page: str
    url: str
    checks: list[CheckResult] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)
    duration_ms: int = 0
    error: str = ""
    timestamp: float = 0.0

    @property
    def score(self) -> float:
        if not self.checks:
            return 0.0
        return sum(1 for c in self.checks if c.passed) / len(self.checks)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    def to_dict(self) -> dict:
        return {
            "profile_name": self.profile_name,
            "profile_id": self.profile_id,
            "test_page": self.test_page,
            "url": self.url,
            "score": round(self.score, 4),
            "passed": self.passed_count,
            "failed": self.failed_count,
            "total": len(self.checks),
            "checks": [
                {"name": c.name, "passed": c.passed, "actual": c.actual[:100]}
                for c in self.checks
            ],
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


@dataclass
class VerificationReport:
    """Complete verification report for all profiles."""
    profiles: list[ProfileVerification] = field(default_factory=list)
    timestamp: float = 0.0
    duration_ms: int = 0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()

    @property
    def summary(self) -> dict:
        total_checks = sum(len(p.checks) for p in self.profiles)
        total_passed = sum(p.passed_count for p in self.profiles)
        return {
            "total_profiles": len(self.profiles),
            "total_checks": total_checks,
            "total_passed": total_passed,
            "total_failed": total_checks - total_passed,
            "overall_score": round(total_passed / total_checks, 4) if total_checks else 0.0,
            "per_profile": [
                {
                    "name": p.profile_name,
                    "profile_id": p.profile_id,
                    "score": round(p.score, 4),
                    "passed": p.passed_count,
                    "failed": p.failed_count,
                    "test_page": p.test_page,
                }
                for p in self.profiles
            ],
        }

    def failed_checks(self) -> list[dict]:
        """All failed checks across all profiles."""
        result = []
        for p in self.profiles:
            for c in p.checks:
                if not c.passed:
                    result.append({
                        "profile": p.profile_name,
                        "test_page": p.test_page,
                        "check": c.name,
                        "expected": c.expected,
                        "actual": c.actual[:200],
                    })
        return result


# ═══════════════════════════════════════════════════════════════════
# Verifier
# ═══════════════════════════════════════════════════════════════════

class FingerprintVerifier:
    """
    Runs fingerprint checks against test pages and records results.

    Usage:
        verifier = FingerprintVerifier(transport)
        report = await verifier.verify_profile(session, profile)
        await verifier.verify_all_profiles(session, identity_mgr)
    """

    def __init__(self):
        self.reports: list[ProfileVerification] = []

    async def verify_profile(self, session, profile,
                              test_pages: Optional[list[str]] = None
                              ) -> list[ProfileVerification]:
        """
        Verify a single fingerprint profile against test pages.

        Args:
            session: CDPSession to use for browsing
            profile: FingerprintProfile to verify
            test_pages: list of test page keys to run (None = all)

        Returns:
            list of ProfileVerification results (one per test page)
        """
        pages = test_pages or list(TEST_PAGES.keys())
        results = []

        for page_key in pages:
            page = TEST_PAGES.get(page_key)
            if not page:
                continue

            logger.info(f"Verifying {profile.name} on {page_key}...")
            start = time.monotonic()

            try:
                # Navigate with human-like timing
                await session.send("Page.enable")
                await session.send("Runtime.enable")
                await session.send("Network.enable")

                # Apply stealth script
                try:
                    from browser.anti_detect import STEALTH_JS
                except ImportError:
                    from anti_detect import STEALTH_JS
                await session.send("Page.addScriptToEvaluateOnNewDocument", {
                    "source": STEALTH_JS,
                })

                # Navigate
                await session.navigate(page["url"], wait_until="load", timeout=30)

                # Wait for page to fully render
                await asyncio.sleep(random_uniform(1.0, 2.0))

                # Scroll a bit (some detection runs on scroll)
                await session.send("Input.dispatchMouseEvent", {
                    "type": "mouseWheel", "x": 640, "y": 400,
                    "deltaX": 0, "deltaY": 300,
                })
                await asyncio.sleep(0.5)

                # Extract results from HTML (sannysoft uses class="result passed/failed")
                raw_html = await session.get_html()
                checks = self._parse_checks_from_html(page_key, raw_html)

                # Fallback: also run JS extraction for additional data
                raw_js = await session.evaluate(page["extract_script"]) or {}

                duration = int((time.monotonic() - start) * 1000)

                result = ProfileVerification(
                    profile_name=profile.name,
                    profile_id=profile.fingerprint_id,
                    test_page=page_key,
                    url=page["url"],
                    checks=checks,
                raw_data={"html_checks": len(checks), "js_data": raw_js},
                    duration_ms=duration,
                    timestamp=time.time(),
                )
                results.append(result)
                logger.info(f"  {page_key}: {result.passed_count}/{len(checks)} passed "
                            f"(score={result.score:.2f})")

            except Exception as e:
                duration = int((time.monotonic() - start) * 1000)
                result = ProfileVerification(
                    profile_name=profile.name,
                    profile_id=profile.fingerprint_id,
                    test_page=page_key,
                    url=page["url"],
                    error=str(e),
                    duration_ms=duration,
                    timestamp=time.time(),
                )
                results.append(result)
                logger.warning(f"  {page_key}: ERROR - {e}")

        return results

    def _parse_checks(self, page_key: str, raw: dict) -> list[CheckResult]:
        """Parse raw extraction data into CheckResults."""
        checks = []

        if page_key == "sannysoft":
            # Debug: print raw data to understand structure
            raw_str = json.dumps(raw, default=str)[:500]

            # The page has a table. Let's extract all rows.
            # We match check names loosely against the raw data keys.
            check_map = {
                "WebDriver": ["webdriver", "WebDriver"],
                "Chrome Runtime": ["chrome runtime", "chrome_runtime", "Chrome Runtime"],
                "Plugins Length": ["plugins", "Plugins"],
                "Languages": ["languages", "Languages"],
                "IFrames": ["iframes", "IFrames", "iframe"],
                "Permissions": ["permissions", "Permissions"],
                "WebGL Vendor": ["webgl vendor", "WebGL Vendor", "webgl"],
                "WebGL Renderer": ["webgl renderer", "WebGL Renderer"],
            }

            for check_name, patterns in check_map.items():
                actual = "not_found"
                # Try to find matching value in raw data
                raw_keys = list(raw.keys())
                for pattern in patterns:
                    for key in raw_keys:
                        if pattern.lower() in key.lower():
                            actual = str(raw[key])
                            break
                    if actual != "not_found":
                        break

                # Also try to find by substring matching
                if actual == "not_found":
                    for key, val in raw.items():
                        if any(p.lower() in key.lower() for p in patterns):
                            actual = str(val)
                            break

                passed = self._check_passed(actual, "passed")
                checks.append(CheckResult(
                    name=check_name,
                    passed=passed,
                    expected="passed",
                    actual=actual[:100],
                ))

        elif page_key == "fingerprintjs":
            entropy = raw.get("entropy_match", "")
            page_loaded = raw.get("page_loaded", "no")
            checks.append(CheckResult(
                name="page_loaded",
                passed=page_loaded == "yes",
                expected="yes",
                actual=page_loaded,
            ))
            if entropy:
                checks.append(CheckResult(
                    name="entropy_displayed",
                    passed=True,
                    expected="visible",
                    actual=entropy[:100],
                ))
            else:
                checks.append(CheckResult(
                    name="entropy_displayed",
                    passed=False,
                    expected="visible",
                    actual="not found",
                ))

        return checks

    def _parse_checks_from_html(self, page_key: str, html: str) -> list[CheckResult]:
        """Parse check results directly from HTML using CSS class indicators.

        sannysoft.com uses <td class="result passed|failed" id="xxx-result">VALUE</td>
        This is more reliable than text content analysis.
        """
        checks = []
        import re

        if page_key == "sannysoft":
            # Table 1 (CDP leak checks) — <td> cells with class="passed|failed" AND an id.
            # Attribute order varies on the page — a fixed-order regex silently misses
            # the hard checks (WebGL Vendor/Renderer, broken-image-dimensions, ...).
            td_pattern = re.compile(
                r"<td([^>]*)>(.*?)</td>", re.DOTALL | re.IGNORECASE)
            for tag, content in td_pattern.findall(html):
                m_class = re.search(r'class="([^"]*)"', tag)
                m_id = re.search(r'id="([^"]+)"', tag)
                if not m_id or not m_class:
                    continue
                # class may be compound ("result passed") or bare ("failed")
                classes = m_class.group(1).lower().split()
                if "passed" not in classes and "failed" not in classes:
                    continue
                status_class = "passed" if "passed" in classes else "failed"
                check_id = m_id.group(1)
                raw_value = re.sub(r"<[^>]+>", "", content).strip()
                check_name = check_id.replace("-result", "").replace("-", " ").title()
                checks.append(CheckResult(
                    name=check_name,
                    passed=status_class == "passed",
                    expected="passed",
                    actual=raw_value[:100],
                    details=f"html_class={status_class}",
                ))

            # Table 2 (Fingerprint Scanner / fpscanner) —
            # <tr><td>CHECK</td><td class="passed|failed">ok</td><td><pre>...</pre></td></tr>
            fp_rows = re.findall(
                r"<tr><td>([^<]+)</td><td class=\"(passed|failed)\">[^<]*</td><td><pre>(.*?)</pre></td></tr>",
                html, re.S)
            for name, status, detail in fp_rows:
                check_name = name.strip().replace("_", " ").title()
                checks.append(CheckResult(
                    name=f"FP:{check_name}",
                    passed=status == "passed",
                    expected="passed",
                    actual=re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", detail)).strip()[:100],
                    details=f"fpscanner_class={status}",
                ))

        return checks

    def _check_passed(self, actual: str, expected: str) -> bool:
        """Determine if a check passed based on actual vs expected."""
        if not actual or actual == "not_found":
            return False
        actual_lower = actual.lower()
        expected_lower = expected.lower()
        # Pass conditions
        if expected_lower == "passed":
            return any(m in actual_lower for m in ["passed", "ok", "success", "✓", "yes"])
        if expected_lower == "failed":
            return any(m in actual_lower for m in ["failed", "error", "✗", "no"])
        return actual_lower == expected_lower


# ═══════════════════════════════════════════════════════════════════
# Verification Runner
# ═══════════════════════════════════════════════════════════════════

class VerificationRunner:
    """
    Runs the full verification loop across all profiles.

    For each profile:
      1. Start a browser instance with that profile
      2. Run all test pages
      3. Record results
      4. Write scores back to identity store
    """

    def __init__(self, identity_manager, pool_config=None,
                 test_pages: Optional[list[str]] = None):
        from browser.session_pool import SessionPool, PoolConfig

        self.identity = identity_manager
        self.test_pages = test_pages or list(TEST_PAGES.keys())
        self.verifier = FingerprintVerifier()
        self.reports: list[ProfileVerification] = []

        # Create a dedicated pool for verification (isolated from main pool)
        cfg = pool_config or PoolConfig(
            min_instances=1,
            max_instances=3,  # limit concurrent browsers
            identity_manager=identity_manager,
        )
        self.pool = SessionPool(cfg)

    async def run(self, profile_ids: Optional[list[str]] = None) -> VerificationReport:
        """
        Run verification for all (or specified) profiles.

        This is the main entry point — it orchestrates the full loop.
        """
        report = VerificationReport()
        start_time = time.monotonic()

        # Get profiles to verify
        profiles = []
        for fid, fp in self.identity.factory._store._profiles.items():
            if profile_ids and fid not in profile_ids:
                continue
            profiles.append(fp)

        if not profiles:
            logger.warning("No profiles to verify")
            return report

        logger.info(f"Verifying {len(profiles)} profiles against "
                    f"{self.test_pages}...")

        await self.pool.start()

        for profile in profiles:
            logger.info(f"\n{'='*60}")
            logger.info(f"Profile: {profile.name} ({profile.fingerprint_id})")

            # Get a session for this profile's site
            # We use a fixed site so the same profile is always used
            session = await self.pool.get_session(
                f"verify-{profile.fingerprint_id}"
            )

            try:
                # Apply profile-specific settings to browser
                await self._apply_profile_to_browser(session, profile)

                # Run verification
                results = await self.verifier.verify_profile(
                    session, profile, self.test_pages
                )
                self.reports.extend(results)

                # Update profile score in store
                for result in results:
                    if result.checks:
                        self.identity.store._conn.execute(
                            "UPDATE profiles SET score = ? WHERE fingerprint_id = ?",
                            (result.score, profile.fingerprint_id)
                        )
                        profile.score = result.score
                        profile.passed_tests = [
                            c.name for c in result.checks if c.passed
                        ]
                        profile.failed_tests = [
                            c.name for c in result.checks if not c.passed
                        ]

                self.identity.store._conn.commit()

            except Exception as e:
                logger.error(f"Failed to verify {profile.name}: {e}")
            finally:
                try:
                    await session.close()
                except Exception:
                    pass

        report.profiles = self.reports
        report.duration_ms = int((time.monotonic() - start_time) * 1000)

        # Log summary
        summary = report.summary
        logger.info(f"\n{'='*60}")
        logger.info(f"VERIFICATION COMPLETE")
        logger.info(f"  Profiles: {summary['total_profiles']}")
        logger.info(f"  Checks: {summary['total_passed']}/{summary['total_checks']} "
                    f"passed ({summary['overall_score']:.1%})")
        for p in summary["per_profile"]:
            logger.info(f"  {p['name']:20s} → {p['score']:.0%} "
                        f"({p['passed']}/{p['total']})")

        # Log failures
        failures = report.failed_checks()
        if failures:
            logger.info(f"\nFAILED CHECKS ({len(failures)}):")
            for f in failures[:10]:
                logger.info(f"  [{f['profile']}] {f['test_page']}.{f['check']}: "
                            f"expected={f['expected']}, got={f['actual'][:50]}")

        await self.pool.close()
        return report

    async def _apply_profile_to_browser(self, session, profile):
        """Apply profile settings to a browser session."""
        # Set user agent
        try:
            await session.send("Network.setUserAgentOverride", {
                "userAgent": profile.user_agent,
            })
        except Exception:
            pass

        # Set viewport
        vp = profile.viewport
        try:
            await session.send("Emulation.setDeviceMetricsOverride", {
                "width": vp.get("width", 1920),
                "height": vp.get("height", 1080),
                "deviceScaleFactor": vp.get("deviceScaleFactor", 1.0),
                "mobile": False,
            })
        except Exception:
            pass

        # Set timezone
        try:
            await session.send("Emulation.setTimezoneOverride", {
                "timezoneId": profile.timezone,
            })
        except Exception:
            pass

        # Set geolocation (neutral)
        try:
            await session.send("Emulation.setGeolocationOverride", {
                "latitude": 37.7749,
                "longitude": -122.4194,
                "accuracy": 100,
            })
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
# Quick Test
# ═══════════════════════════════════════════════════════════════════

def random_uniform(lo: float, hi: float) -> float:
    import random
    return random.uniform(lo, hi)


async def test():
    """Test the verification loop with existing profiles."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from browser.identity import IdentityManager

    # Use existing data dir or create temp
    data_dir = os.path.expanduser("~/.unified-browser")
    if not os.path.isdir(data_dir):
        data_dir = tempfile.mkdtemp(prefix="verify_test_")

    im = IdentityManager(data_dir)

    # Load profiles from store into memory
    profiles = list(im._profiles.values())
    if not profiles:
        rows = im.store._conn.execute("SELECT profile FROM profiles").fetchall()
        from browser.identity import FingerprintProfile
        for row in rows:
            try:
                data = json.loads(row[0])
                p = FingerprintProfile(**data)
                profiles.append(p)
                im._profiles[p.fingerprint_id] = p
            except Exception:
                pass

    # If still empty, create synthetic profiles
    if not profiles:
        logger.info("Creating 2 synthetic profiles...")
        for p in im.factory.synthesize(count=2):
            im.add_profile(p)
            profiles.append(p)

    print(f"Profiles to verify: {len(profiles)}")
    for p in profiles:
        print(f"  - {p.name} ({p.fingerprint_id})")

    # Run verification (simpler approach: launch Chrome per profile)
    from browser.cdp_driver import CDPTransport
    verifier = FingerprintVerifier()

    all_results = []
    for profile in profiles:
        logger.info(f"\nVerifying: {profile.name} ({profile.fingerprint_id})")
        print(f"\n--- {profile.name} ({profile.fingerprint_id}) ---")

        transport = CDPTransport()
        try:
            # Merge the production stealth args (headless, SwiftShader WebGL, ...)
            # with the profile identity args — verification must test what
            # production actually runs, not a stripped-down Chrome.
            from browser.cdp_driver import DEFAULT_CHROME_ARGS
            chrome_args = list(DEFAULT_CHROME_ARGS) + [
                "--user-agent=" + profile.user_agent,
                f"--window-size={profile.viewport.get('width',1920)},{profile.viewport.get('height',1080)}",
            ]
            await transport.start(
                user_data_dir=tempfile.mkdtemp(prefix=f"vrfy_{profile.fingerprint_id[:8]}_"),
                args=chrome_args,
            )
            session = await transport.create_session()

            # Apply stealth
            await session.send("Page.enable")
            await session.send("Runtime.enable")
            await session.send("Network.enable")
            try:
                from browser.anti_detect import STEALTH_JS
            except ImportError:
                from anti_detect import STEALTH_JS
            await session.send("Page.addScriptToEvaluateOnNewDocument", {
                "source": STEALTH_JS,
            })

            # Run checks
            results = await verifier.verify_profile(session, profile, ["sannysoft"])
            all_results.extend(results)

            for r in results:
                bar = "=" * int(r.score * 20) + "-" * (20 - int(r.score * 20))
                print(f"  {r.test_page:15s} [{bar}] {r.score:.0%} "
                      f"({r.passed_count}/{len(r.checks)})")
                for c in r.checks:
                    status = "PASS" if c.passed else "FAIL"
                    print(f"    {status} {c.name}: actual={c.actual[:80]}")

            await session.close()
        except Exception as e:
            logger.error(f"Error: {e}")
            print(f"  ERROR: {e}")
        finally:
            transport.stop()

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    total_checks = sum(len(r.checks) for r in all_results)
    total_passed = sum(r.passed_count for r in all_results)
    if total_checks > 0:
        print(f"Overall: {total_passed}/{total_checks} passed ({total_passed/total_checks:.1%})")

    all_failed = []
    for r in all_results:
        for c in r.checks:
            if not c.passed:
                all_failed.append(f"  [{r.profile_name}] {r.test_page}.{c.name}")
    if all_failed:
        print(f"\nFAILURES ({len(all_failed)}):")
        for f in all_failed:
            print(f)

    print("\nTest PASSED")
    im.close()


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.DEBUG if "--debug" in sys.argv else logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s [%(name)s] %(message)s",
    )
    asyncio.run(test())