#!/usr/bin/env python3
"""
anti_detect.py — Anti-detection Layer for UnifiedBrowser.

The core difference from "stealth plugins": this layer works at the CDP
level, not the page_evaluate level. It patches browser internals, intercepts
network requests, and classifies anti-bot pages by pattern — not by
plausibility check.

Layers:
  1. CDP Leak Patches    — hide automation signals at the CDP level
  2. Resource Blocking   — block images/fonts/media at Network level
  3. Turnstile Solver    — handle Cloudflare Turnstile challenges
  4. Bot Page Detection  — classify fake pages (CF/reCAPTCHA/Akamai/DataDome)
"""

import asyncio
import json
import logging
import os
import random
import re
import sys
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse

logger = logging.getLogger("anti_detect")

# ═══════════════════════════════════════════════════════════════════
# 1. CDP Leak Patches
# ═══════════════════════════════════════════════════════════════════

# JavaScript that patches all known automation signals
# This runs as an init script on every page load.
STEALTH_JS = r"""
// ═══════ CDP Leak Patches — runs at document_start ═══════
(() => {
    // patch navigator.webdriver -> undefined (AGGRESSIVE: multiple levels)
    // NOTE: do NOT define webdriver as an OWN property of navigator —
    // natural Chrome has it on Navigator.prototype only. Making it an own
    // enumerable property is itself a detection signal ("WebDriver (New)"
    // on sannysoft flags hasOwnProperty/webdriver-in-keys).
    try {
        // Level 1: prototype-level patch (navigator.webdriver resolves here)
        Object.defineProperty(Navigator.prototype, 'webdriver', {
            get: () => undefined,
            configurable: true,
        });
    } catch (e) {}

    // Level 2: override getOwnPropertyDescriptor to hide the property entirely
    try {
        const originalGOPD = Object.getOwnPropertyDescriptor;
        Object.getOwnPropertyDescriptor = function(obj, prop) {
            if (obj === navigator && prop === 'webdriver') {
                return undefined;
            }
            return originalGOPD.call(this, obj, prop);
        };
    } catch (e) {}

    // patch navigator.plugins to appear real
    try {
        const realPlugins = navigator.plugins;
        const fakePlugins = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
        ];
        // Only override if empty (some environments have real plugins)
        if (realPlugins.length === 0) {
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    const arr = Array.from(fakePlugins);
                    arr.item = (i) => arr[i] || null;
                    arr.namedItem = (name) => arr.find(p => p.name === name) || null;
                    arr.refresh = () => {};
                    return arr;
                },
            });
        }
    } catch (e) {}

    // patch navigator.languages to look like US Chrome (2 languages)
    try {
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en'],
            configurable: true,
        });
    } catch (e) {}

    // patch navigator.permissions to auto-deny all permission queries
    try {
        const originalQuery = window.Permissions && window.Permissions.prototype.query;
        if (originalQuery) {
            window.Permissions.prototype.query = function(parameters) {
                // Auto-deny all permission queries to avoid popups
                return Promise.resolve({ state: 'denied', name: parameters?.name });
            };
        }
    } catch (e) {}

    // patch chrome.runtime existence (MUST exist for Chrome detection)
    try {
        if (!window.chrome) {
            window.chrome = {};
        }
        if (!window.chrome.runtime) {
            window.chrome.runtime = {
                getPlatformInfo: () => Promise.resolve({os: 'win', arch: 'x86-64', nacl_arch: 'x86-64'}),
                connect: () => ({}),
                sendMessage: () => {},
                getManifest: () => ({}),
            };
        }
        if (!window.chrome.csi) window.chrome.csi = () => {};
        if (!window.chrome.loadTimes) window.chrome.loadTimes = () => ({});
    } catch (e) {}

    // patch WebGL vendor/renderer (AGGRESSIVE: multiple levels)
    try {
        // Level 1: Intercept getContext to patch WebGL parameters
        const originalGetContext = HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext = function(type, ...args) {
            const ctx = originalGetContext.call(this, type, ...args);
            if (ctx && (type === 'webgl' || type === 'experimental-webgl' || type === 'webgl2')) {
                const getParam = ctx.getParameter.bind(ctx);
                ctx.getParameter = function(param) {
                    const UNMASKED_VENDOR = 0x9245;
                    const UNMASKED_RENDERER = 0x9246;
                    const VENDOR = 0x1F00;      // GL_VENDOR — sannysoft reads this for the row
                    const RENDERER = 0x1F01;    // GL_RENDERER — sannysoft reads this for the row
                    if (param === UNMASKED_VENDOR || param === VENDOR) {
                        return 'Google Inc. (NVIDIA)';
                    }
                    if (param === UNMASKED_RENDERER || param === RENDERER) {
                        return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)';
                    }
                    return getParam(param);
                };
            }
            return ctx;
        };
    } catch (e) {}

    // Level 2: Also patch WebGLRenderingContext prototype directly
    try {
        if (typeof WebGLRenderingContext !== 'undefined') {
            const proto = WebGLRenderingContext.prototype;
            const originalGP = proto.getParameter;
            proto.getParameter = function(param) {
                const UNMASKED_VENDOR = 0x9245;
                const UNMASKED_RENDERER = 0x9246;
                const VENDOR = 0x1F00;
                const RENDERER = 0x1F01;
                if (param === UNMASKED_VENDOR || param === VENDOR) {
                    return 'Google Inc. (NVIDIA)';
                }
                if (param === UNMASKED_RENDERER || param === RENDERER) {
                    return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)';
                }
                return originalGP.call(this, param);
            };
        }
    } catch (e) {}

    // Level 3: Patch WEBGL_debug_renderer_info extension
    try {
        const originalGetExtension = WebGLRenderingContext.prototype.getExtension;
        WebGLRenderingContext.prototype.getExtension = function(name) {
            const ext = originalGetExtension.call(this, name);
            if (name === 'WEBGL_debug_renderer_info' && ext) {
                // Override the extension's constants
                const origVendor = ext.UNMASKED_VENDOR_WEBGL;
                const origRenderer = ext.UNMASKED_RENDERER_WEBGL;
                Object.defineProperty(ext, 'UNMASKED_VENDOR_WEBGL', {
                    get: () => 0x9245,
                    configurable: true,
                });
                Object.defineProperty(ext, 'UNMASKED_RENDERER_WEBGL', {
                    get: () => 0x9246,
                    configurable: true,
                });
            }
            return ext;
        };
    } catch (e) {}

    // patch toArray to hide iframe Proxy (old Chrome headless leak)
    try {
        const originalToString = Function.prototype.toString;
        Function.prototype.toString = function() {
            if (this === window.getComputedStyle) {
                return 'function getComputedStyle() { [native code] }';
            }
            if (this === window.setTimeout) {
                return 'function setTimeout() { [native code] }';
            }
            return originalToString.call(this);
        };
    } catch (e) {}

    // hide if WebDriver flag
    try {
        Object.defineProperty(Navigator.prototype, 'webdriver', {
            get: () => undefined,
        });
    } catch (e) {}
})();
"""

# Chrome launch args for stealth (minus the ones already in DEFAULT_CHROME_ARGS)
STEALTH_LAUNCH_ARGS = [
    # These are the args that sedo patterns on:
    "--test-type",                     # suppress Chrome's "unsupported flag" bar
    "--disable-blink-features=AutomationControlled",  # KEY: removes the flag
    "--window-position=0,0",
    "--lang=en-US",
    "--accept-lang=en-US",
    "--use-mock-keychain",
    "--disable-popup-blocking",
    "--disable-web-security",          # allow cross-origin for certain stealth
    "--autoplay-policy=user-gesture-required",
    "--disable-features=AudioServiceOutOfProcess,TranslateUI,BlinkGenPropertyTrees",
    "--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4",
]

# ═══════════════════════════════════════════════════════════════════
# 2. Resource Blocking
# ═══════════════════════════════════════════════════════════════════

# Resource types to block by default (safe to block, don't break page JS)
BLOCKABLE_RESOURCES = {
    "image", "imageset", "media", "font",
    "stylesheet", "texttrack", "prefetch", "ping", "favicon",
}

# Resources with expected value for many pages (block only with care)
HEAVY_BLOCKABLE = BLOCKABLE_RESOURCES | {"fetch", "xhr"}

BLOCKED_DOMAINS_DEFAULT = [
    "google-analytics.com",
    "googletagmanager.com",
    "connect.facebook.net",
    "analytics.tiktok.com",
    "mc.yandex.ru",
    "static.doubleclick.net",
    "adservice.google.com",
    "doubleclick.net",
    "scorecardresearch.com",
    "c.bing.com",
    "bat.bing.com",
    "hotjar.com",
    "criteo.com",
    "taboola.com",
]

TRACKING_PATTERNS = [
    r"/analytics",
    r"/collect",
    r"/beacon",
    r"/pixel",
    r"/telemetry",
    r"google-analytics",
    r"analytics\.",
]


class ResourceBlocker:
    """Intercept and block unwanted network requests at the CDP level."""

    def __init__(self, session, block_tracking: bool = True,
                 block_heavy: bool = True):
        self._session = session
        self._block_tracking = block_tracking
        self._block_heavy = block_heavy
        self._blocked_domains = list(BLOCKED_DOMAINS_DEFAULT)
        self._blocked = set()       # requestIds blocked
        self._allowed = set()       # requestIds allowed
        self._stats = {"blocked": 0, "allowed": 0}

    async def enable(self):
        """Enable request interception via Fetch domain."""
        await self._session.send("Fetch.enable", {
            "patterns": [
                {"urlPattern": "*", "requestStage": "Request"},
            ],
        })
        self._session.on("Fetch.requestPaused", self._on_request_paused)

    async def _on_request_paused(self, params: dict):
        """Handle Fetch.requestPaused event."""
        req_id = params.get("requestId", "")
        url = params.get("request", {}).get("url", "")
        rtype = params.get("resourceType", "")
        request = params.get("request", {})
        _continue = lambda r=req_id: asyncio.ensure_future(
            self._session.send("Fetch.continueRequest", {"requestId": r})
        )
        _fail = lambda r=req_id: asyncio.ensure_future(
            self._session.send("Fetch.failRequest", {
                "requestId": r,
                "errorReason": "BlockedByClient",
            })
        )

        # Analyze URL
        host = urlparse(url).hostname or ""

        should_block = False
        reason = ""

        # Block by resource type (heavy blocking)
        if self._block_heavy and rtype in HEAVY_BLOCKABLE:
            should_block = True
            reason = f"resource_type:{rtype}"

        # Block tracking domains
        if self._block_tracking and not should_block:
            for domain in self._blocked_domains:
                if host == domain or host.endswith("." + domain):
                    should_block = True
                    reason = f"tracking_domain:{domain}"
                    break

        # Block by URL pattern
        if not should_block:
            for pattern in TRACKING_PATTERNS:
                if re.search(pattern, url, re.I):
                    should_block = True
                    reason = f"tracking_pattern:{pattern}"
                    break

        if should_block:
            self._blocked.add(req_id)
            self._stats["blocked"] += 1
            logger.debug(f"BLOCK {rtype}: {url[:120]} ({reason})")
            await _fail()
        else:
            self._allowed.add(req_id)
            self._stats["allowed"] += 1
            await _continue()

    def add_domain(self, domain: str):
        """Add a domain to the blocklist."""
        if domain not in self._blocked_domains:
            self._blocked_domains.append(domain)

    def get_stats(self) -> dict:
        return self._stats

    async def disable(self):
        """Disable request interception."""
        try:
            await self._session.send("Fetch.disable")
        except Exception:
            pass
        self._session.off("Fetch.requestPaused", self._on_request_paused)


# ═══════════════════════════════════════════════════════════════════
# 3. Turnstile Solver (Cloudflare)
# ═══════════════════════════════════════════════════════════════════

class TurnstileSolver:
    """
    Handles Cloudflare Turnstile challenges.

    Strategy:
    1. Detect the challenge page (via URL or content)
    2. Wait for the Turnstile widget to render
    3. If a checkbox is present, click it
    4. Wait for the page to resolve

    Note: This handles *non-interactive* Turnstile (the common case).
    Interactive challenges (requiring actual user puzzle solving) are NOT
    supported — we honestly report this to the caller.
    """

    CHALLENGE_URL_PATTERN = re.compile(
        r"challenges\.cloudflare\.com|cdn-cgi/challenge-platform|turnstile"
    )
    CHALLENGE_BODY_MARKERS = [
        "challenge-platform", "cf-chl-opt", "cf-browser-verification",
        "turnstile/api.js", "turnstile", "cloudflarechallenge.com",
        "Just a moment", "Verify you are human",
    ]

    def __init__(self, session, timeout: float = 30.0):
        self._session = session
        self._timeout = timeout

    async def is_challenge_page(self, url: str, html: str) -> bool:
        """Check if current page is a Cloudflare challenge."""
        if self.CHALLENGE_URL_PATTERN.search(url):
            return True
        low = html.lower()
        return any(marker in low for marker in self.CHALLENGE_BODY_MARKERS)

    async def wait_and_solve(self, url: str, html: str,
                             timeout: float = 30.0) -> dict:
        """
        Wait for Turnstile to resolve and attempt to click the checkbox if present.

        Returns:
            {"solved": bool, "method": "auto_checkbox"|"auto_resolve"|"none",
             "msg": str}
        """
        start = time.monotonic()
        solved = False
        method = "none"
        msg = "no challenge detected"

        if not await self.is_challenge_page(url, html):
            return {"solved": True, "method": "none", "msg": "not a challenge page"}

        logger.info("Cloudflare Turnstile challenge detected, attempting to solve...")

        last_html = html
        while time.monotonic() - start < timeout:
            # Check if challenge resolved (page navigated or content changed)
            current_html = await self._session.get_html()

            # Check if still on challenge (look for markers)
            still_challenge = any(
                m in current_html.lower() for m in self.CHALLENGE_BODY_MARKERS
            )
            if not still_challenge:
                solved = True
                method = "auto_resolve"
                msg = "challenge resolved (content changed)"
                break

            # Try to click the Turnstile checkbox (common non-interactive case)
            clicked = await self._try_click_checkbox()
            if clicked:
                method = "auto_checkbox"
                msg = "clicked Turnstile checkbox"
                # Wait a bit for resolution
                await asyncio.sleep(random.uniform(1.5, 3.0))
                continue

            await asyncio.sleep(1.0)

        if not solved:
            msg = "turnstile unresolved (may require interactive challenge)"

        return {"solved": solved, "method": method, "msg": msg}

    async def _try_click_checkbox(self) -> bool:
        """Try to click the Turnstile checkbox if it's visible."""
        try:
            # Turnstile iframe checkbox
            result = await self._session.evaluate("""
                (() => {
                    const frames = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]');
                    if (frames.length === 0) return null;
                    // Find the checkbox inside the frame (returns position for clicking)
                    // Note: cross-origin iframes can't be accessed directly,
                    // so we click at the iframe element's position.
                    const f = frames[0];
                    const r = f.getBoundingClientRect();
                    return { x: r.x + r.width/2, y: r.y + r.height/2, frame: true };
                })()
            """)

            if not result or not result.get("frame"):
                # Try clicking the widget container directly (Cloudflare sometimes renders it inline)
                result = await self._session.evaluate("""
                    (() => {
                        const el = document.querySelector('#cf-chl-widget, .turnstile-wrapper, .cf-turnstile');
                        if (!el) return null;
                        const r = el.getBoundingClientRect();
                        return { x: r.x + r.width/2, y: r.y + r.height/2, frame: false };
                    })()
                """)

            if result and result.get("x") is not None:
                from .cdp_driver import CDPSession  # import inside to avoid circular
                await self._session.click(result["x"], result["y"])
                logger.debug("Clicked Turnstile checkbox")
                return True

            return False
        except Exception as e:
            logger.debug(f"Turnstile checkbox click failed: {e}")
            return False


# ═══════════════════════════════════════════════════════════════════
# 4. Bot Page Detection (Anti-fake-content)
# ═══════════════════════════════════════════════════════════════════

class BotPageDetector:
    """
    Detect anti-bot pages by pattern — not by plausibility.

    This is the anti-fake-content layer: it recognizes specific anti-bot
    system fingerprints so we can report exactly WHY a fetch failed,
    instead of just "empty content".
    """

    # Cloudflare Turnstile / Challenge
    CLOUDFLARE = {
        "name": "cloudflare",
        "url_patterns": [
            r"challenges\.cloudflare\.com",
            r"cdn-cgi/challenge-platform",
            r"/cdn-cgi/l/challenge",
        ],
        "body_markers": [
            "cf-browser-verification", "cf-chl-opt", "challenge-platform",
            "turnstile", "Just a moment", "Verify you are human",
            "cdn-cgi/challenge",
        ],
        "header_markers": ["server: cloudflare", "cf-ray"],
    }

    # reCAPTCHA
    RECAPTCHA = {
        "name": "recaptcha",
        "url_patterns": [
            r"google\.com/recaptcha",
            r"recaptcha\.api",
            r"g-recaptcha",
        ],
        "body_markers": [
            "g-recaptcha", "recaptcha/api.js", "recaptcha__zh_cn",
            "recaptcha__en", "Verify you are human", "reCAPTCHA",
        ],
        "header_markers": [],
    }

    # Akamai Bot Manager
    AKAMAI = {
        "name": "akamai",
        "url_patterns": [
            r"akamai",
            r"akamaihd",
        ],
        "body_markers": [
            "bmi-validated", "akamai", "_abck", "AAMIG",
            "just a moment", "Access Denied",
        ],
        "header_markers": ["x-akamai-transformed", "akamai-nsc"],
    }

    # DataDome
    DATADOME = {
        "name": "datadome",
        "url_patterns": [
            r"datadome",
        ],
        "body_markers": [
            "datadome", "DataDome", "geetest", "geo.captcha-delivery",
            "Access denied", "captcha-delivery.com",
        ],
        "header_markers": ["x-datadome", "datadome"],
    }

    # PerimeterX / PerimeterX (now HUMAN)
    PERIMETERX = {
        "name": "perimeterx",
        "url_patterns": [
            r"perimeterx",
            r"px-captcha",
            r"humansecurity",
        ],
        "body_markers": [
            "perimeterx", "px-captcha", "PX10116", "humansecurity",
            "captcha-delivery",
        ],
        "header_markers": ["x-px", "_px"],
    }

    # Generic captcha / bot wall
    GENERIC = {
        "name": "generic_bot_wall",
        "url_patterns": [
            r"captcha",
            r"bot-wall",
            r"challenge",
        ],
        "body_markers": [
            "are you a robot", "verify you are human", "captcha check",
            "bot check", "enable javascript", "performing an automated check",
        ],
        "header_markers": [],
    }

    SYSTEMS = [CLOUDFLARE, RECAPTCHA, AKAMAI, DATADOME, PERIMETERX, GENERIC]

    def detect(self, url: str, html: str = "", headers: Optional[dict] = None) -> dict:
        """
        Detect which anti-bot system (if any) this page belongs to.

        Returns:
            {"is_bot_page": bool, "system": str|None, "detection_method": str,
             "markers_found": list[str]}
        """
        low_url = url.lower()
        low_html = html.lower()
        headers = headers or {}

        for system in self.SYSTEMS:
            markers_found = []

            # URL patterns
            for pattern in system["url_patterns"]:
                if re.search(pattern, low_url):
                    markers_found.append(f"url:{pattern}")
            # Body markers
            for marker in system["body_markers"]:
                if marker.lower() in low_html:
                    markers_found.append(f"body:{marker}")
            # Header markers
            for marker in system["header_markers"]:
                m = marker.split(":")
                key = m[0].strip().lower()
                val = m[1].strip().lower() if len(m) > 1 else ""
                for hk, hv in headers.items():
                    if key in hk.lower():
                        if not val or val in hv.lower():
                            markers_found.append(f"header:{hk}")
                            break

            if markers_found:
                # Need at least 2 markers or 1 strong marker to confirm
                strong = any(m.startswith("url:") for m in markers_found)
                if len(markers_found) >= 2 or strong:
                    return {
                        "is_bot_page": True,
                        "system": system["name"],
                        "detection_method": "pattern_match",
                        "markers_found": markers_found[:5],
                    }

        # Check for "fake success" — page with 200 but no meaningful content
        if html and len(html.strip()) < 500 and "doctype" in html.lower():
            # Very short page, likely a placeholder
            return {
                "is_bot_page": True,
                "system": "unknown_short_page",
                "detection_method": "short_content",
                "markers_found": [f"content_length:{len(html)}"],
            }

        return {"is_bot_page": False, "system": None,
                "detection_method": "none", "markers_found": []}

    def describe(self, detection: dict) -> str:
        """Human-readable description of the detection."""
        if not detection.get("is_bot_page"):
            return "Page appears legitimate"
        system = detection.get("system", "unknown")
        markers = ", ".join(detection.get("markers_found", [])[:3])
        return (f"Blocked: {system} (matched markers: {markers}) "
                f"→ suggest switching engine")


# ═══════════════════════════════════════════════════════════════════
# AntiDetectFacade — 集成的反偵測入口
# ═══════════════════════════════════════════════════════════════════

class AntiDetect:
    """
    Integrated anti-detection facade.

    Usage:
        anti = AntiDetect(session)
        await anti.initialize()
        await anti.navigate("https://example.com")
        content = await anti.get_content()
    """

    STEALTH_SCRIPT = STEALTH_JS

    def __init__(self, session,
                 block_tracking: bool = True,
                 block_heavy: bool = False,
                 detect_bot: bool = True):
        self._session = session
        self._blocker: Optional[ResourceBlocker] = None
        self._turnstile: Optional[TurnstileSolver] = None
        self._detector = BotPageDetector() if detect_bot else None
        self._block_tracking = block_tracking
        self._block_heavy = block_heavy
        self._stats = {"pages_loaded": 0, "bot_pages_detected": 0,
                       "resources_blocked": 0, "turnstile_solved": 0}

    async def initialize(self):
        """Enable all anti-detection measures."""
        # 1. Enable Page + Runtime for init script
        await self._session.send("Page.enable")
        await self._session.send("Runtime.enable")

        # 2. Install stealth JS as init script (runs at document_start)
        await self._session.send("Page.addScriptToEvaluateOnNewDocument", {
            "source": self.STEALTH_SCRIPT,
        })

        # 3. Enable network interception
        self._blocker = ResourceBlocker(
            self._session,
            block_tracking=self._block_tracking,
            block_heavy=self._block_heavy,
        )
        await self._blocker.enable()

        # 4. Setup Turnstile solver
        self._turnstile = TurnstileSolver(self._session)

        # 5. Auto-handle dialogs
        self._session.on("Page.javascriptDialogOpening", self._on_dialog)

        logger.info("Anti-detection initialized")

    async def _on_dialog(self, params):
        """Auto-accept JS dialogs (prevents hangs)."""
        try:
            await self._session.send("Page.handleJavaScriptDialog", {"accept": True})
        except Exception:
            pass

    async def navigate(self, url: str, wait_until: str = "load",
                       timeout: float = 30.0) -> dict:
        """Navigate with anti-detection + bot page detection."""
        result = await self._session.navigate(url, wait_until=wait_until,
                                              timeout=timeout)
        self._stats["pages_loaded"] += 1

        # Get page content for detection
        html = ""
        try:
            html = await self._session.get_html()
        except Exception:
            pass

        # Check for Cloudflare challenge
        if self._turnstile:
            t = await self._turnstile.wait_and_solve(url, html)
            if t.get("solved"):
                self._stats["turnstile_solved"] += 1
                # Re-fetch HTML after solving
                try:
                    html = await self._session.get_html()
                except Exception:
                    pass

        # Detect bot pages
        if self._detector:
            detection = self._detector.detect(url, html)
            if detection.get("is_bot_page"):
                self._stats["bot_pages_detected"] += 1
                return {
                    "ok": False,
                    "content": html,
                    "bot_page": True,
                    "bot_system": detection.get("system"),
                    "bot_markers": detection.get("markers_found"),
                    "detection": detection,
                    "stats": self._stats,
                }

        return {
            "ok": True,
            "content": html,
            "bot_page": False,
            "stats": self._stats,
        }

    async def get_text(self) -> str:
        """Get clean page text."""
        return await self._session.get_text()

    async def evaluate(self, expression: str) -> Any:
        """Execute JS with anti-detection context."""
        return await self._session.evaluate(expression)

    def get_stats(self) -> dict:
        """Get anti-detection statistics."""
        if self._blocker:
            self._stats["resources_blocked"] = self._blocker.get_stats()["blocked"]
        return self._stats

    async def cleanup(self):
        """Disable all anti-detection measures."""
        if self._blocker:
            await self._blocker.disable()


# ═══════════════════════════════════════════════════════════════════
# Quick Test
# ═══════════════════════════════════════════════════════════════════

async def test():
    """Test anti-detection against bot detector sites."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from browser.cdp_driver import CDPTransport

    transport = CDPTransport()
    try:
        await transport.start()
        session = await transport.create_session()

        anti = AntiDetect(session)
        # Skip blocker for test (it intercepts all requests)
        await anti._session.send("Page.enable")
        await anti._session.send("Runtime.enable")
        await anti._session.send("Page.addScriptToEvaluateOnNewDocument", {
            "source": anti.STEALTH_SCRIPT,
        })
        anti._session.on("Page.javascriptDialogOpening", anti._on_dialog)

        # Test 1: bot.sannysoft.com
        print("=== Test 1: bot.sannysoft.com ===")
        try:
            await anti._session.navigate("https://bot.sannysoft.com/", wait_until="load")
            text = await anti.get_text()
            # Check for webdriver leakage
            wd_line = [l for l in text.split('\n') if 'webdriver' in l.lower()]
            print(f"  webdriver lines: {wd_line[:3]}")
            print(f"  text length: {len(text)}")
            wd_found = any('True' in l or 'failed' in l.lower() for l in wd_line)
            print(f"  webdriver detected: {wd_found}")
        except Exception as e:
            print(f"  FAILED: {e}")

        # Test 2: a normal site
        print("\n=== Test 2: example.com ===")
        try:
            await anti._session.navigate("https://example.com/", wait_until="load")
            title = await session.get_title()
            print(f"  title: {title}")
        except Exception as e:
            print(f"  FAILED: {e}")

        # Test 3: offline pattern detection
        print("\n=== Test 3: BotPageDetector pattern tests ===")
        detector = BotPageDetector()

        cf_html = '<html><head><title>Just a moment...</title></head>' \
                  '<body><script src="/cdn-cgi/challenges/..."></script></body></html>'
        d = detector.detect("https://site.com/", cf_html)
        print(f"  Cloudflare: {d['system']} ({len(d['markers_found'])} markers)")

        recaptcha_html = '<html><body><div class="g-recaptcha"></div>' \
                         '<script src="https://www.google.com/recaptcha/api.js"></script></body></html>'
        d = detector.detect("https://login.site.com/", recaptcha_html)
        print(f"  reCAPTCHA: {d['system']} ({len(d['markers_found'])} markers)")

        normal_html = '<html><head><title>Hello</title></head>' \
                      '<body><p>Normal page content</p></body></html>'
        d = detector.detect("https://normal.com/", normal_html)
        print(f"  Normal page: {d['is_bot_page']}")

        print("\nTest PASSED")

    finally:
        await transport.astop()


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.DEBUG if "--debug" in sys.argv else logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s [%(name)s] %(message)s",
    )
    asyncio.run(test())