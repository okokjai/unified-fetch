#!/usr/bin/env python3
"""
behavior.py — Behavioral Engine for UnifiedBrowser.

Simulates human-like browsing behavior patterns.
Not just random delays — statistically modeled timing distributions
that cannot be distinguished from real human behavior.

Architecture:
  Timing Model → Site Profiles → Behavior Generator → CDP Input Commands

Key insight:
  Anti-bot systems now detect automation by timing patterns, not just
  fingerprints. Fixed delays or purely random delays have detectable
  statistical signatures. This engine uses real-human timing distributions
  with appropriate variance.
"""

import asyncio
import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("behavior")

# ═══════════════════════════════════════════════════════════════════
# Timing Models
# ═══════════════════════════════════════════════════════════════════

# All timing values are in milliseconds, modeled as Gaussian distributions
# with appropriate standard deviations based on real human behavior research.

# Mouse movement: human saccades + smooth pursuit
# Speed: ~300-500 px/s, with acceleration/deceleration curves
MOUSE_CLICK_DELAY_MS = {"mean": 180, "std": 60, "min": 60, "max": 500}
MOUSE_MOVE_DELAY_MS = {"mean": 120, "std": 40, "min": 30, "max": 300}

# Scrolling: humans scroll in bursts, not continuously
# Average scroll: ~300px, then pause 200-800ms
SCROLL_BURST_PX = {"mean": 350, "std": 150, "min": 80, "max": 800}
SCROLL_PAUSE_MS = {"mean": 500, "std": 200, "min": 100, "max": 1500}
SCROLL_ACCELERATION = 0.3  # 30% of scrolls are accelerations (fast then slow)

# Typing: humans type at variable speeds
# Average: ~200ms per keystroke, but with pauses between words
KEY_PRESS_DELAY_MS = {"mean": 150, "std": 50, "min": 40, "max": 400}
WORD_PAUSE_MS = {"mean": 400, "std": 150, "min": 150, "max": 1000}

# Page interaction timing
# Humans don't interact immediately — they read first
PAGE_LOAD_TO_FIRST_ACTION_MS = {"mean": 2300, "std": 700, "min": 500, "max": 6000}
PAGE_LOAD_TO_SCROLL_MS = {"mean": 3500, "std": 1200, "min": 800, "max": 8000}

# Reading speed (for content-heavy pages)
# ~200-300 words per minute → ~200-300ms per word
READING_TIME_PER_WORD_MS = 250  # average
READING_TIME_STD_MS = 80

# Form filling delays
# Humans pause at form fields, especially on complex questions
FORM_FIELD_PAUSE_MS = {"mean": 800, "std": 300, "min": 200, "max": 3000}
FORM_SUBMIT_DELAY_MS = {"mean": 1200, "std": 400, "min": 400, "max": 3000}


# ═══════════════════════════════════════════════════════════════════
# Statistical Helpers
# ═══════════════════════════════════════════════════════════════════

def _gaussian_clamp(mean: float, std: float, min_val: float, max_val: float) -> float:
    """Sample from a clamped Gaussian distribution.

    Uses Box-Muller transform for true Gaussian (not uniform).
    """
    u1 = random.random()
    u2 = random.random()
    # Box-Muller
    z = math.sqrt(-2.0 * math.log(max(u1, 1e-10))) * math.cos(2.0 * math.pi * u2)
    val = mean + z * std
    return max(min_val, min(max_val, val))


def _bezier_point(t: float, p0: tuple, p1: tuple, p2: tuple, p3: tuple) -> tuple:
    """Cubic Bezier curve point."""
    u = 1 - t
    x = u*u*u * p0[0] + 3*u*u*t * p1[0] + 3*u*t*t * p2[0] + t*t*t * p3[0]
    y = u*u*u * p0[1] + 3*u*u*t * p1[1] + 3*u*t*t * p2[1] + t*t*t * p3[1]
    return (x, y)


# ═══════════════════════════════════════════════════════════════════
# Mouse Movement
# ═══════════════════════════════════════════════════════════════════

class MousePathGenerator:
    """
    Generate human-like mouse movement paths.

    Humans don't move in straight lines. They use:
    - Saccades: fast, roughly straight movements
    - Smooth pursuit: curved, slower movements (following content)
    - Overshoot: pass the target, then correct
    - Tremor: micro-oscillations at ~8-12Hz

    We model this with cubic Bezier curves + noise.
    """

    def __init__(self, start_x: float, start_y: float,
                 end_x: float, end_y: float):
        self.start = (start_x, start_y)
        self.end = (end_x, end_y)
        self._steps = self._calculate_steps()

    def _calculate_steps(self) -> int:
        """Calculate number of mouse move steps based on distance."""
        dist = math.dist(self.start, self.end)
        # Human mouse moves at ~300-500 px/s, with ~16ms per event
        # So roughly 1 step per 5-8 pixels
        steps = max(3, int(dist / 6))
        # Add variance
        steps += random.randint(-2, 2)
        return max(3, steps)

    def generate_path(self) -> list[dict]:
        """Generate a human-like mouse path as list of {x, y, t} points."""
        points = []

        # Control points for Bezier curve
        # Humans rarely move in straight lines
        dx = self.end[0] - self.start[0]
        dy = self.end[1] - self.start[1]

        # Control point 1: biased toward start direction
        cp1 = (
            self.start[0] + dx * 0.25 + random.uniform(-50, 50),
            self.start[1] + dy * 0.25 + random.uniform(-50, 50),
        )
        # Control point 2: biased toward end direction
        cp2 = (
            self.end[0] - dx * 0.25 + random.uniform(-50, 50),
            self.end[1] - dy * 0.25 + random.uniform(-50, 50),
        )

        # Generate path with variable timing
        total_time = _gaussian_clamp(
            MOUSE_MOVE_DELAY_MS["mean"] * self._steps,
            MOUSE_MOVE_DELAY_MS["std"] * math.sqrt(self._steps),
            MOUSE_MOVE_DELAY_MS["min"] * self._steps,
            MOUSE_MOVE_DELAY_MS["max"] * self._steps,
        )

        for i in range(self._steps):
            t = (i + 1) / self._steps
            # Ease-in-out: humans accelerate at start, decelerate at end
            eased_t = t * t * (3 - 2 * t)
            x, y = _bezier_point(eased_t, self.start, cp1, cp2, self.end)

            # Add micro-tremor (8-12Hz oscillation)
            tremor = 0.5 * math.sin(t * 2 * math.pi * 10) if random.random() < 0.7 else 0
            x += tremor
            y += tremor

            point_time = total_time * (eased_t / max(eased_t, 0.01))
            points.append({
                "x": round(x, 1),
                "y": round(y, 1),
                "t": round(point_time),
            })

        return points


# ═══════════════════════════════════════════════════════════════════
# Site Behavior Profiles
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SiteBehaviorProfile:
    """
    Behavior profile for a specific type of site.

    Different sites elicit different human behaviors:
    - Reading sites (blogs, news): slow scrolling, long pauses
    - Search sites (Google, DDG): fast scrolling, frequent clicks
    - Form sites (login, checkout): slow typing, pauses on fields
    - Social media (Reddit, HN): medium scrolling, occasional clicks
    """
    site_type: str  # "reading" | "search" | "form" | "social" | "general"

    # Scroll behavior
    scroll_burst_px: dict = field(default_factory=lambda: SCROLL_BURST_PX)
    scroll_pause_ms: dict = field(default_factory=lambda: SCROLL_PAUSE_MS)
    scroll_count_mean: float = 3.0  # average scroll bursts per page

    # Click behavior
    click_delay_ms: dict = field(default_factory=lambda: MOUSE_CLICK_DELAY_MS)

    # Page timing
    first_action_delay_ms: dict = field(default_factory=lambda: PAGE_LOAD_TO_FIRST_ACTION_MS)
    first_scroll_delay_ms: dict = field(default_factory=lambda: PAGE_LOAD_TO_SCROLL_MS)

    # Reading behavior
    reading_time_per_word_ms: float = READING_TIME_PER_WORD_MS


# Pre-built site profiles
SITE_PROFILES: dict[str, SiteBehaviorProfile] = {
    "reading": SiteBehaviorProfile(
        site_type="reading",
        scroll_burst_px=SCROLL_BURST_PX,
        scroll_pause_ms={"mean": 800, "std": 300, "min": 200, "max": 3000},
        scroll_count_mean=5.0,
        first_action_delay_ms={"mean": 3000, "std": 1000, "min": 1000, "max": 8000},
        first_scroll_delay_ms={"mean": 4000, "std": 1500, "min": 1500, "max": 10000},
        reading_time_per_word_ms=280,
    ),
    "search": SiteBehaviorProfile(
        site_type="search",
        scroll_burst_px={"mean": 500, "std": 200, "min": 100, "max": 1000},
        scroll_pause_ms={"mean": 600, "std": 200, "min": 100, "max": 1500},
        scroll_count_mean=2.0,
        first_action_delay_ms={"mean": 1500, "std": 500, "min": 300, "max": 4000},
        first_scroll_delay_ms={"mean": 2000, "std": 800, "min": 500, "max": 5000},
        reading_time_per_word_ms=150,
    ),
    "form": SiteBehaviorProfile(
        site_type="form",
        scroll_burst_px={"mean": 200, "std": 100, "min": 50, "max": 500},
        scroll_pause_ms={"mean": 1000, "std": 400, "min": 300, "max": 3000},
        scroll_count_mean=1.0,
        first_action_delay_ms={"mean": 1000, "std": 400, "min": 200, "max": 3000},
        first_scroll_delay_ms={"mean": 1500, "std": 600, "min": 500, "max": 4000},
        reading_time_per_word_ms=200,
    ),
    "social": SiteBehaviorProfile(
        site_type="social",
        scroll_burst_px={"mean": 400, "std": 200, "min": 100, "max": 800},
        scroll_pause_ms={"mean": 400, "std": 150, "min": 50, "max": 1000},
        scroll_count_mean=4.0,
        first_action_delay_ms={"mean": 2000, "std": 800, "min": 500, "max": 5000},
        first_scroll_delay_ms={"mean": 2500, "std": 1000, "min": 800, "max": 6000},
        reading_time_per_word_ms=200,
    ),
    "general": SiteBehaviorProfile(
        site_type="general",
        scroll_burst_px=SCROLL_BURST_PX,
        scroll_pause_ms=SCROLL_PAUSE_MS,
        scroll_count_mean=3.0,
        first_action_delay_ms=PAGE_LOAD_TO_FIRST_ACTION_MS,
        first_scroll_delay_ms=PAGE_LOAD_TO_SCROLL_MS,
        reading_time_per_word_ms=250,
    ),
}


def get_profile_for_domain(domain: str) -> SiteBehaviorProfile:
    """Determine behavior profile based on domain type."""
    domain_lower = domain.lower()

    # Reading sites
    reading_domains = [
        "medium.com", "blog.", "news.", "docs.", "wikipedia.org",
        "stackoverflow.com", "dev.to", "hashnode.dev",
    ]
    for d in reading_domains:
        if d in domain_lower:
            return SITE_PROFILES["reading"]

    # Search sites
    search_domains = [
        "google.com", "duckduckgo.com", "bing.com", "baidu.com",
        "yahoo.com", "search.",
    ]
    for d in search_domains:
        if d in domain_lower:
            return SITE_PROFILES["search"]

    # Form sites
    form_domains = [
        "login.", "signin", "auth.", "checkout", "account.",
        "register", "signup",
    ]
    for d in form_domains:
        if d in domain_lower:
            return SITE_PROFILES["form"]

    # Social media
    social_domains = [
        "reddit.com", "news.ycombinator.com", "twitter.com",
        "x.com", "facebook.com", "instagram.com",
    ]
    for d in social_domains:
        if d in domain_lower:
            return SITE_PROFILES["social"]

    return SITE_PROFILES["general"]


# ═══════════════════════════════════════════════════════════════════
# Behavior Generator
# ═══════════════════════════════════════════════════════════════════

class BehaviorGenerator:
    """
    Generate human-like browsing behaviors for a given session.

    Usage:
        bg = BehaviorGenerator(session)
        await bg.after_page_load("https://medium.com/article")
        await bg.human_scroll(5)
        await bg.human_click_element(selector)
        await bg.human_type_text(selector, "Hello")
    """

    def __init__(self, session):
        self._session = session
        self._profile: Optional[SiteBehaviorProfile] = None
        self._current_mouse_pos: tuple = (400, 300)
        self._page_word_count: int = 0
        self._reading_started: float = 0.0
        self._stats = {
            "scrolls": 0, "clicks": 0, "moves": 0,
            "typing_chars": 0, "total_delay_ms": 0,
        }

    async def set_domain_profile(self, domain: str):
        """Set behavior profile based on domain."""
        self._profile = get_profile_for_domain(domain)
        logger.debug(f"Behavior profile: {self._profile.site_type} for {domain}")

    async def estimate_page_word_count(self):
        """Estimate word count of the current page for reading time."""
        try:
            count = await self._session.evaluate(
                "document.body?.innerText?.split(/\\s+/).length || 0"
            )
            self._page_word_count = count
        except Exception:
            self._page_word_count = 0

    # ── After Page Load ───────────────────────────────────────────

    async def after_page_load(self, domain: str = ""):
        """
        Simulate human behavior after page load.

        Humans don't interact immediately. They:
        1. Wait for the page to render (~200ms-500ms visual settling)
        2. Read the title/brief content
        3. Decide what to do next
        """
        if domain:
            await self.set_domain_profile(domain)

        profile = self._profile or SITE_PROFILES["general"]

        # Initial settling delay (visual rendering)
        settle = random.uniform(200, 500)
        await asyncio.sleep(settle / 1000)
        self._stats["total_delay_ms"] += settle

        # Estimate page content
        await self.estimate_page_word_count()

        # First action delay
        delay = _gaussian_clamp(
            profile.first_action_delay_ms["mean"],
            profile.first_action_delay_ms["std"],
            profile.first_action_delay_ms["min"],
            profile.first_action_delay_ms["max"],
        )
        self._stats["total_delay_ms"] += delay
        await asyncio.sleep(delay / 1000)

        # Small mouse movement (human curiosity)
        old_x, old_y = self._current_mouse_pos
        new_x = old_x + random.uniform(-50, 50)
        new_y = old_y + random.uniform(-50, 50)
        # Clamp to viewport
        new_x = max(50, min(1900, new_x))
        new_y = max(50, min(1000, new_y))
        await self._human_mouse_move(new_x, new_y)

    # ── Mouse Movement ────────────────────────────────────────────

    async def _human_mouse_move(self, target_x: float, target_y: float):
        """Move mouse to target with human-like trajectory."""
        path = MousePathGenerator(
            self._current_mouse_pos[0],
            self._current_mouse_pos[1],
            target_x, target_y,
        )
        points = path.generate_path()

        for point in points:
            # Send CDP mouse move event
            try:
                await self._session.send("Input.dispatchMouseEvent", {
                    "type": "mouseMoved",
                    "x": point["x"],
                    "y": point["y"],
                })
            except Exception:
                break
            self._stats["moves"] += 1

            # Wait between mouse events (human-like timing)
            if len(points) > 1:
                await asyncio.sleep(point["t"] / (1000 * len(points)))

        self._current_mouse_pos = (target_x, target_y)

    # ── Click ─────────────────────────────────────────────────────

    async def human_click(self, x: float, y: float,
                          button: str = "left") -> dict:
        """Click at coordinates with human-like mouse movement."""
        profile = self._profile or SITE_PROFILES["general"]

        # Move mouse to target first (humans don't teleport)
        await self._human_mouse_move(x, y)

        # Small delay before clicking (human reaction time)
        delay = _gaussian_clamp(
            profile.click_delay_ms["mean"],
            profile.click_delay_ms["std"],
            profile.click_delay_ms["min"],
            profile.click_delay_ms["max"],
        )
        await asyncio.sleep(delay / 1000)
        self._stats["total_delay_ms"] += delay

        # Click
        try:
            await self._session.send("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": x, "y": y,
                "button": button, "clickCount": 1,
            })
            # Human click duration: ~50-150ms
            release_delay = random.uniform(50, 150) / 1000
            await asyncio.sleep(release_delay)
            await self._session.send("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": x, "y": y,
                "button": button, "clickCount": 1,
            })
            self._stats["clicks"] += 1
            return {"ok": True, "x": x, "y": y}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def human_click_element(self, selector: str) -> bool:
        """Find an element and click it with human-like behavior."""
        box = await self._session.evaluate(f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return {{ x: r.x + r.width * (0.3 + Math.random() * 0.4),
                         y: r.y + r.height * (0.3 + Math.random() * 0.4),
                         w: r.width, h: r.height }};
            }})()
        """)
        if not box:
            return False

        # Click within the element (not dead center — humans are imprecise)
        result = await self.human_click(box["x"], box["y"])
        return result.get("ok", False)

    # ── Scroll ────────────────────────────────────────────────────

    async def human_scroll(self, count: Optional[int] = None):
        """Scroll the page with human-like burst behavior."""
        profile = self._profile or SITE_PROFILES["general"]

        if count is None:
            # Sample from Poisson-like distribution
            count = max(1, int(random.gauss(profile.scroll_count_mean, 1.5)))

        # First scroll is delayed (humans read first)
        if self._stats["scrolls"] == 0:
            delay = _gaussian_clamp(
                profile.first_scroll_delay_ms["mean"],
                profile.first_scroll_delay_ms["std"],
                profile.first_scroll_delay_ms["min"],
                profile.first_scroll_delay_ms["max"],
            )
            self._stats["total_delay_ms"] += delay
            await asyncio.sleep(delay / 1000)

        for i in range(count):
            # Scroll burst
            burst_px = _gaussian_clamp(
                profile.scroll_burst_px["mean"],
                profile.scroll_burst_px["std"],
                profile.scroll_burst_px["min"],
                profile.scroll_burst_px["max"],
            )

            # Some scrolls have acceleration (fast start, slow end)
            if random.random() < SCROLL_ACCELERATION:
                # Accelerate: scroll more in a single burst
                total_px = burst_px
                # Send a fast scroll
                try:
                    await self._session.send("Input.dispatchMouseEvent", {
                        "type": "mouseWheel", "x": 640, "y": 400,
                        "deltaX": 0, "deltaY": total_px,
                    })
                    self._stats["scrolls"] += 1
                except Exception:
                    break
                # Follow-up micro-scrolls (human deceleration)
                for _ in range(random.randint(1, 3)):
                    remainder = random.uniform(20, 60)
                    try:
                        await self._session.send("Input.dispatchMouseEvent", {
                            "type": "mouseWheel", "x": 640, "y": 400,
                            "deltaX": 0, "deltaY": remainder,
                        })
                    except Exception:
                        break
                    await asyncio.sleep(random.uniform(30, 80) / 1000)
            else:
                # Normal scroll burst
                # Break into 2-3 sub-steps (humans don't scroll in one smooth move)
                sub_steps = random.randint(2, 3)
                for _ in range(sub_steps):
                    sub_px = burst_px / sub_steps + random.uniform(-10, 10)
                    try:
                        await self._session.send("Input.dispatchMouseEvent", {
                            "type": "mouseWheel", "x": 640, "y": 400,
                            "deltaX": 0, "deltaY": sub_px,
                        })
                        self._stats["scrolls"] += 1
                    except Exception:
                        break
                    await asyncio.sleep(random.uniform(40, 100) / 1000)

            # Pause between scroll bursts (human reads visible content)
            pause = _gaussian_clamp(
                profile.scroll_pause_ms["mean"],
                profile.scroll_pause_ms["std"],
                profile.scroll_pause_ms["min"],
                profile.scroll_pause_ms["max"],
            )
            self._stats["total_delay_ms"] += pause
            await asyncio.sleep(pause / 1000)

    # ── Typing ────────────────────────────────────────────────────

    async def human_type(self, text: str, field_selector: Optional[str] = None):
        """Type text with human-like timing (variable pauses, typos)."""
        if field_selector:
            clicked = await self.human_click_element(field_selector)
            if not clicked:
                return False

        # Click on field (humans click before typing)
        # Delay after click (focus settling)
        await asyncio.sleep(random.uniform(100, 300) / 1000)

        words = text.split(" ")
        for word_idx, word in enumerate(words):
            for char_idx, char in enumerate(word):
                # Type character
                try:
                    await self._session.send("Input.dispatchKeyEvent", {
                        "type": "char", "text": char,
                        "unmodifiedText": char,
                        "key": char,
                    })
                    self._stats["typing_chars"] += 1
                except Exception:
                    break

                # Delay between keystrokes
                delay = _gaussian_clamp(
                    KEY_PRESS_DELAY_MS["mean"],
                    KEY_PRESS_DELAY_MS["std"],
                    KEY_PRESS_DELAY_MS["min"],
                    KEY_PRESS_DELAY_MS["max"],
                )
                self._stats["total_delay_ms"] += delay
                await asyncio.sleep(delay / 1000)

                # Random typo (rare)
                if random.random() < 0.02:  # 2% typo rate
                    # Backspace
                    await self._session.send("Input.dispatchKeyEvent", {
                        "type": "rawKeyDown", "key": "Backspace",
                        "code": "Backspace",
                    })
                    await asyncio.sleep(random.uniform(50, 150) / 1000)
                    await self._session.send("Input.dispatchKeyEvent", {
                        "type": "keyUp", "key": "Backspace",
                    })
                    # Retry the character
                    await self._session.send("Input.dispatchKeyEvent", {
                        "type": "char", "text": char,
                        "unmodifiedText": char,
                    })

            # Pause between words (humans pause to think)
            if word_idx < len(words) - 1:
                space_delay = _gaussian_clamp(
                    WORD_PAUSE_MS["mean"],
                    WORD_PAUSE_MS["std"],
                    WORD_PAUSE_MS["min"],
                    WORD_PAUSE_MS["max"],
                )
                self._stats["total_delay_ms"] += space_delay
                await asyncio.sleep(space_delay / 1000)

                # Type space
                try:
                    await self._session.send("Input.dispatchKeyEvent", {
                        "type": "char", "text": " ", "key": " ",
                    })
                except Exception:
                    break

        return True

    # ── Reading Time ──────────────────────────────────────────────

    async def simulate_reading(self, word_count: Optional[int] = None,
                               min_seconds: float = 2.0):
        """Wait for realistic reading time based on word count."""
        wc = word_count or self._page_word_count
        if wc <= 0:
            await asyncio.sleep(min_seconds)
            return

        # Reading time = words * time_per_word (with variance)
        reading_ms = wc * READING_TIME_PER_WORD_MS
        reading_ms += random.gauss(0, reading_ms * 0.2)  # 20% variance
        reading_s = max(min_seconds, reading_ms / 1000)

        # But humans don't read continuously — they skim
        # Skim rate: 30-70% of full reading time
        skim_rate = random.uniform(0.3, 0.7)
        wait_s = max(min_seconds, reading_s * skim_rate)

        self._stats["total_delay_ms"] += wait_s * 1000
        await asyncio.sleep(wait_s)

    # ── Full Page Behavior ────────────────────────────────────────

    async def behave_like_human(self, domain: str = "",
                                scroll: bool = True,
                                read: bool = True,
                                min_duration: float = 3.0):
        """
        Simulate a complete human-like page visit.

        Combines: initial delay → mouse move → scroll → read → exit
        """
        start = time.monotonic()

        # 1. After page load
        await self.after_page_load(domain)

        # 2. Read a bit
        if read:
            await self.simulate_reading(min_seconds=2.0)

        # 3. Scroll
        if scroll:
            await self.human_scroll()

        # 4. Ensure minimum visit duration
        elapsed = time.monotonic() - start
        if elapsed < min_duration:
            await asyncio.sleep(min_duration - elapsed)

    def get_stats(self) -> dict:
        """Get behavioral statistics."""
        return self._stats


# ═══════════════════════════════════════════════════════════════════
# Quick Test
# ═══════════════════════════════════════════════════════════════════

async def test():
    """Test behavioral engine with a real page."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from browser.cdp_driver import CDPTransport

    transport = CDPTransport()
    try:
        await transport.start()
        session = await transport.create_session()
        await session.send("Page.enable")
        await session.send("Runtime.enable")

        bg = BehaviorGenerator(session)

        # Test 1: Navigate to example.com with human behavior
        print("=== Test 1: Human-like page visit ===")
        await session.navigate("https://example.com/", wait_until="load")
        await bg.after_page_load("example.com")
        await bg.human_scroll(count=2)
        await bg.simulate_reading(min_seconds=1.0)
        print(f"  Stats: {bg.get_stats()}")
        print(f"  Mouse position: {bg._current_mouse_pos}")

        # Test 2: Mouse movement
        print("\n=== Test 2: Mouse movement ===")
        gen = MousePathGenerator(100, 100, 600, 400)
        path = gen.generate_path()
        print(f"  Path length: {len(path)} points")
        print(f"  Start: (100, 100) → End: (600, 400)")
        print(f"  First point: {path[0]}")
        print(f"  Last point: {path[-1]}")

        # Test 3: Site profile selection
        print("\n=== Test 3: Site profile detection ===")
        for domain in ["medium.com/article", "google.com/search", "login.example.com",
                        "reddit.com/r/python", "unknown-site.com"]:
            profile = get_profile_for_domain(domain)
            print(f"  {domain:40s} → {profile.site_type}")

        # Test 4: Timing distribution
        print("\n=== Test 4: Timing distribution (sample) ===")
        samples = [_gaussian_clamp(150, 50, 40, 400) for _ in range(10)]
        print(f"  Key press delays (ms): {samples}")
        samples = [_gaussian_clamp(2300, 700, 500, 6000) for _ in range(10)]
        print(f"  First action delays (ms): {samples}")

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