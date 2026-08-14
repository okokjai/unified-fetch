<div align="center">

# 🌐 unified-fetch V2

**An MCP server that delivers HTTP speed with stealth-browser penetration.**
**Zero config. Zero API keys. Clone and run.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![MCP](https://img.shields.io/badge/MCP-Ready-orange.svg)](https://modelcontextprotocol.io)
[![GitHub stars](https://img.shields.io/github/stars/okokjai/unified-fetch?style=social)](https://github.com/okokjai/unified-fetch)
[![Release](https://img.shields.io/github/v/release/okokjai/unified-fetch)](https://github.com/okokjai/unified-fetch/releases)

```
search · scrape · deep_search · crawl · map · smart_browse
browser_navigate · browser_get_content · browser_screenshot
browser_evaluate · browser_interact · browser_status · status
```

**Works with Claude Code · Cursor · Windsurf · any MCP client**

Give your AI agent the web. No browser-first overhead. No Playwright dependency. No config files.

</div>

---

> **v2.0.0** — HTTP-first auto-upgrade engine + CDP-native stealth browser + Cloudflare real-world breakthrough + 496/496 fingerprint verification

---

## What problem does this solve?

When building AI agents, you encounter three kinds of websites:

| Website Type | What Generic Tools Struggle With | What unified-fetch V2 Does |
|---|---|---|
| **Regular sites** (news, docs, blogs) | Browser-first tools open a browser for every request — 5-10s each time | **HTTP engines grab content directly, ~1s** |
| **SPA / JS-rendered pages** | Pure HTTP misses dynamic content | Auto-upgrade to CDP stealth browser with full rendering |
| **Cloudflare-protected sites** (StackOverflow, Medium…) | Headless browsers get blocked every time | **HTTP → headless → headful three-tier auto-upgrade**, proven to penetrate |

**The core difference: it doesn't "use a browser for everything" — it uses the fastest path available and only pulls out the heavy weapon when needed.**

---

## Why unified-fetch V2?

### Works Out of the Box

```
git clone + pip install -r requirements.txt
```

- **Minimal dependencies**: only `mcp` + `websockets` (everything in `browser/` uses stdlib)
- **Zero config, zero API keys**: all engines use `try-import` auto-detection — enabled if installed, gracefully skipped if not
- **Edge auto-detection**: Edge is built into Windows — no extra browser install needed
- No Chrome on the machine? HTTP engines keep working without errors

### HTTP-First Speed

```
The default path for scrape(url):

  HTTP extraction chain (~1s)
    Hound → newspaper → trafilatura → readability → justext → direct
       ↓ blocked / JS shell / all engines fail
  UnifiedBrowser CDP stealth (5-10s)
       ↓ Cloudflare hard challenge (e.g. StackOverflow)
  Headful real window (auto-upgrade, +5-10s)
```

- **95% of sites are resolved on the first HTTP attempt** — no browser startup overhead
- Upgrades only when needed, max 2 levels, no infinite retry loops
- `prefer_browser=true` reserved for cases that genuinely need a browser

### CDP-Native Stealth Core (No Playwright, No Selenium)

```
UnifiedBrowser (browser/ package)
├── CDP Driver     — raw Chrome DevTools Protocol
├── Identity Engine — per-site identity isolation, 496/496 fingerprint pass
├── Anti-Detection  — webdriver / plugins / languages / WebGL patches
├── Behavioral Engine — human-like mouse movement + typing rhythm
└── Session Pool    — site isolation + memory management + headful escalation
```

- **Raw CDP**: no Playwright, no Selenium, no WebDriver
- **496/496 fingerprint**: 31 indicators × 16 profiles, verified by bot.sannysoft.com + fpscanner
- **Human behavior**: mouse moves along Bezier curves with acceleration; typing has 2% typo rate
- **Site isolation**: each site gets its own identity — no cookie leakage across domains

### Parallel Search + Consensus

```
DDG + Google + Hound sent in parallel
       ↓
  Consensus weighting (URLs returned by multiple engines ranked higher)
       ↓
  Max 2 results per domain (prevent single-site domination)
       ↓
  All fail → DirectFetch → (last resort) browser search
```

- Multiple engines run in parallel — no waiting for a single engine
- Consensus mechanism: URLs appearing in multiple engines get boosted
- Per-engine circuit breaker (60s cooldown) + Retry-After respect

### Agent-Friendly Response Format

```json
{
  "ok": true,
  "content": "...",
  "content_ok": true,
  "page_type": "article",
  "engine_used": "trafilatura",
  "engine_chain": ["newspaper", "trafilatura"],
  "next_action": "none",
  "is_stale": false,
  "duration_ms": 850,
  "cache_hit": false
}
```

Every response tells the agent **what happened, which engine was used, and what to do next**.

| Field | Purpose |
|---|---|
| `content_ok` | Content is actually usable (not an error page / empty) |
| `page_type` | `article` / `list` / `js_shell` / `pdf` / `error` |
| `next_action` | Tells the agent what to try next: `switch_source` / `upgrade_browser` / `retry` / `give_up` |
| `engine_chain` | Full engine chain for debugging |

### Focused Content: BM25 Extraction

```python
scrape(url, focus="API rate limit and retry strategy")
```

Instead of dumping the entire page, BM25 extraction returns only relevant sections. **Saves 80%+ context waste.**

### Smart Cache: SQLite WAL + Bad Content Filtering

- WAL mode: concurrent read/write without locks
- Error pages / 403 / 429 / bot pages **never cached**
- Size cap with oldest-first eviction
- `require_fresh=true` forces a live re-fetch

---

## Tool Reference (14 tools)

| Tool | Description |
|---|---|
| `search(query, max_results)` | Parallel search + consensus + diversity |
| `scrape(url, focus?, prefer_browser?, require_fresh?)` | HTTP-first → browser upgrade; focus mode returns only relevant sections |
| `status()` | Engine availability + browser pool + cache + user manual |
| `deep_search(query, sources?)` | Parallel search across GitHub / npm / MDN and other tech sources |
| `parallel_scrape(urls, ...)` | Concurrent scraping (up to 5) |
| `crawl(url, max_depth, max_pages)` | BFS crawler; single-page failures auto-upgrade to browser |
| `map(url)` | Site structure (sitemap + internal link tree) |
| `smart_browse(url)` | **UnifiedBrowser-first**: guaranteed JS rendering for SPAs |
| `browser_navigate(url)` | Stealth browser navigation |
| `browser_get_content(format)` | Page text / HTML |
| `browser_screenshot(full_page)` | Screenshot (base64 PNG) |
| `browser_evaluate(expression)` | Execute JavaScript |
| `browser_interact(action, ...)` | CDP-native interactions: click / fill / type / hover / select / scroll / press / wait_for / upload |
| `browser_status()` | Browser pool + identity engine status |

---

## Installation

```bash
git clone https://github.com/okokjai/unified-fetch.git
cd unified-fetch
pip install -r requirements.txt        # Tier 0: ready to use
```

**Browser** (auto-detected, optional): Chrome / Edge (Windows built-in) / Playwright bundled Chromium.

**Optional enhancements** (auto-enabled if installed, auto-skipped if not):

```bash
pip install -r requirements-optional.txt   # Hound + Playwright fallback + curl_cffi + googlesearch
playwright install chromium                # Only needed if no Chrome / Edge available
```

| Tier | Contents | Dependencies |
|---|---|---|
| 0 | HTTP engines + search + browser core (CDP) | `mcp` `websockets` `httpx` `duckduckgo_search` `trafilatura` `readability-lxml` `justext` `newspaper3k` `lxml` |
| 1 (optional) | Hound (master-fetch), Playwright, curl_cffi, googlesearch | `requirements-optional.txt` |

### MCP Setup

```json
{
  "mcpServers": {
    "unified-fetch": {
      "command": "C:\\path\\to\\python.exe",
      "args": ["C:\\path\\to\\unified-fetch\\unified-fetch-server.py"]
    }
  }
}
```

---

## Verification Numbers

| Check | Result | Details |
|---|---|---|
| Functional tests | **111/111 passed** | Layers A–F (modules + integration + tool surface + engine chain + interact + cache + parallel search) |
| Fingerprint | **496/496 all pass (31 items × 16 profiles)** | bot.sannysoft.com + fpscanner, genuine 100% |
| Cloudflare real-world | nowsecure ✅ · Medium ✅ · SO headful ✅ | Real anti-crawl sites: headless blocked → headful auto-upgrade passes |
| MCP handshake | initialize + 14 tools + scrape ✅ | stdio protocol real test |
| Fresh-venv install | Only mcp+websockets+httpx needed to search/scrape | Out-of-the-box verified |

---

## Headful Mode: Cloudflare Hard-Challenge Breakthrough

This is the key V2.0.3 breakthrough:

| Mode | StackOverflow (CF hard challenge) | Result |
|---|---|---|
| headless Chrome + stealth | ❌ Stuck on "Just a moment" | headless mode itself flagged by CF |
| headless Edge + stealth | ❌ Stuck | Same — unrelated to browser brand |
| **headful Chrome (no stealth needed)** | ✅ **Pass** | **Headful is the real solution** |
| **headful Edge + stealth** | ✅ **Pass** | Windows out-of-the-box |

**Key finding: CF's detection of StackOverflow = headless mode itself, not browser brand or stealth JS.** Real window rendering passes. Headful needs no stealth patches (patches retained for other detection vectors).

| Level | Behavior |
|---|---|
| **Default: headless** | No popups, sufficient for 95% of sites |
| **Detect CF hard challenge** | Auto-upgrade to site-level headful (session_pool.escalate_to_headful) |
| **Windows** | Offscreen hidden (`--window-position=-32000,-32000`) → zero disruption |
| **Linux server** | Needs Xvfb (pyvirtualdisplay) — headful requires a virtual display in no-X environments |

---

## Usage Examples

### #1 Research workflow

```
search("python httpx async") → pick URLs → scrape(url, focus="timeout retry") → synthesize
```

### #2 SPA / JS-rendered content

```
smart_browse("https://spa-example.com")      # Guaranteed rendering, UnifiedBrowser-first
```

### #3 Interactive browser operations

```
browser_navigate("https://example.com/form")
browser_interact(action="fill", selector="#email", value="test@test.com")
browser_interact(action="click", selector="button[type=submit]")
browser_screenshot(full_page=true)
```

---

## Honest Limitations

| Limitation | Behavior |
|---|---|
| DataDome / Akamai / interactive Turnstile | May not pass. `next_action` suggests switching source |
| Login walls | Not bypassed (interact does not handle authenticated sessions) |
| Deep Shadow-DOM | Partially reachable (scroll/click/wait_for), not fully penetrated |
| Machines without browser | HTTP engines still fully functional (graceful degradation) |
| PDF OCR / neural ranking | Not built-in — install Hound (Tier 1) to get them |
| Linux server headful | Needs Xvfb (Windows offscreen verified) |

---

## Project Structure

```
unified-fetch/
├── unified-fetch-server.py       # MCP Server entry (2,031 lines, 14 tools)
├── ARCHITECTURE.md               # Full architecture document
├── README.md                     # This document
├── CHANGELOG.md                  # Version history
├── requirements.txt              # Tier 0 dependencies
├── requirements-optional.txt     # Tier 1 optional dependencies
├── test_v2_full_smoke.py         # 111 tests
└── browser/                      # CORE: UnifiedBrowser package (~5,489 lines)
    ├── cdp_driver.py             # Raw CDP + CDPSession
    ├── unified_browser.py        # Integration entry
    ├── identity.py               # Identity Engine
    ├── anti_detect.py            # Anti-detection + BotPageDetector
    ├── behavior.py               # Behavioral Engine
    ├── session_pool.py           # Session Pool + headful escalation
    └── fingerprint_verify.py     # Fingerprint verification
```

> **~8,000 lines of Python** (2,031 server + ~5,489 browser/ + 497 tests)

---

## Version History

| Version | Highlights |
|---|---|
| **2.0.3** (2026-08-14) | Headful auto-upgrade — StackOverflow-level CF breakthrough, three-tier HTTP → headless → headful |
| **2.0.2** (2026-08-14) | CF real-world tests (nowsecure / Medium / SO) + fingerprint expanded to 496/496 |
| **2.0.1** (2026-08-14) | P0 fingerprint fixes: STEALTH_JS syntax error, webdriver patch self-detonation, parser fake 100% |
| **2.0.0** (2026-08-14) | HTTP-first + browser auto-upgrade + CDP core + 14 tools |

---

## Related

- Full architecture: [ARCHITECTURE.md](ARCHITECTURE.md)
- Version history: [CHANGELOG.md](CHANGELOG.md)
- Hound (master-fetch, optional engine): https://github.com/dondai1234/master-fetch

---

*Author: PaulPaul + Claude Code*
*Last updated: 2026-08-14*
