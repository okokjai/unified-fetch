<div align="center">

# 🌐 unified-fetch V2

**Browser-core MCP server — HTTP speed meets stealth-browser penetration**
**HTTP-first · CDP-native · Cloudflare breakthrough · zero config**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/okokjai/unified-fetch?style=social)](https://github.com/okokjai/unified-fetch)
[![GitHub Release](https://img.shields.io/github/v/release/okokjai/unified-fetch)](https://github.com/okokjai/unified-fetch/releases)
[![Platform: Claude Code](https://img.shields.io/badge/Platform-Claude%20Code-000000.svg?logo=claude)](https://claude.com/claude-code)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![MCP](https://img.shields.io/badge/MCP-Ready-orange.svg)](https://modelcontextprotocol.io)

```
search · scrape · deep_search · crawl · map · smart_browse
browser_navigate · browser_get_content · browser_screenshot
browser_evaluate · browser_interact · browser_status · status
```

Works with Claude Code · Cursor · Windsurf · any MCP client

</div>

---

## ✨ Features

| Category | Capabilities |
|----------|-------------|
| ⚡ **HTTP-first + auto-upgrade** | scrape goes HTTP first (~1s); auto-upgrades to stealth browser only when blocked / JS shell / empty |
| 🧬 **UnifiedBrowser core** | Raw CDP (no Playwright / Selenium / WebDriver), identity isolation + human behavior + anti-detection |
| 🔍 **Parallel search + consensus** | DDG / Google / Hound run in parallel, cross-engine consensus weighting, ≤2 per domain, quorum reporting |
| 🎯 **Actionable signals** | Every response includes `content_ok` / `page_type` / `next_action` / `engine_used` / `engine_chain` |
| ✂️ **BM25 focus** | `scrape(url, focus="...")` returns only relevant sections, saving 80%+ context |
| 🖱️ **CDP native interact** | click / fill / type / hover / select / scroll / press / wait_for / upload — no Playwright touch |
| 💾 **Smart cache** | SQLite WAL, bad content never cached, auto-eviction by size cap, `require_fresh` forces re-fetch |
| 🚀 **Lazy browser init** | Server starts <1s; Chrome opens only when browser is first needed |
| 📦 **Zero config** | All engines try-import auto-detect; no Chrome = HTTP-only, won't break |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    unified-fetch V2 — Three-Legged Stool                  │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │ Left Leg: Engine Chain (HTTP-first + auto-upgrade)                │ │
│  │  └─ scrape: HTTP extraction → blocked → UnifiedBrowser            │ │
│  │  └─ search: parallel + quorum + consensus + diversity             │ │
│  │  └─ 6 heterogeneous extraction engines + source type tagging      │ │
│  └──────────────────────────────────┬────────────────────────────────┘ │
│                                     │                                   │
│  ┌──────────────────────────────────▼────────────────────────────────┐ │
│  │ Belt: Scheduling Layer (connects the two legs)                     │ │
│  │  └─ site_health 2D matrix (site × engine historical success rate) │ │
│  │  └─ decide_fetch_tier(domain) → auto-select starting point        │ │
│  │  └─ auto-upgrade (escalate on failure, max 2 levels)              │ │
│  │  └─ Each result: next_action + content_ok + page_type             │ │
│  └──────────────────────────────────┬────────────────────────────────┘ │
│                                     │                                   │
│  ┌──────────────────────────────────▼────────────────────────────────┐ │
│  │ Right Leg: CORE = UnifiedBrowser (CDP-native)                     │ │
│  │  └─ Identity Engine (profile synthesis + site routing)            │ │
│  │  └─ Anti-detection (stealth patches + bot detection)              │ │
│  │  └─ Behavioral Engine (human behavior)                            │ │
│  │  └─ Session Pool (site isolation + lifecycle + headful escalation)│ │
│  └───────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  Infrastructure: SQLite cache · circuit breaker · metrics · focus      │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Requirements

- **Python 3.10+**
- **MCP client** (Claude Code / Cursor / Windsurf / any stdio MCP client)
- **Browser** (auto-detected, optional): Chrome / Edge (Windows built-in) / Playwright bundled Chromium

### Installation

```bash
git clone https://github.com/okokjai/unified-fetch.git
cd unified-fetch
pip install -r requirements.txt        # Tier 0: ready to use
```

### MCP Configuration

Add to your MCP client config:

```json
{
  "mcpServers": {
    "unified-fetch": {
      "command": "python",
      "args": ["/path/to/unified-fetch/unified-fetch-server.py"]
    }
  }
}
```

### Optional Enhancements

Auto-enabled if installed, auto-skipped if not:

```bash
pip install -r requirements-optional.txt   # Hound + Playwright fallback + curl_cffi + googlesearch
playwright install chromium                # Only if no Chrome / Edge available
```

---

## 🔍 Engine Chain

### scrape — HTTP-first + auto-upgrade

```
HTTP extraction chain (~1s)
  Hound → newspaper → trafilatura → readability → justext → direct
     ↓ blocked / JS shell / all engines fail
UnifiedBrowser CDP stealth (5–10s)
     ↓ Cloudflare hard challenge (e.g. StackOverflow)
Headful real window (auto-upgrade, +5–10s)
```

- 95% of sites resolved on the first HTTP attempt — no browser startup overhead
- Upgrades only when needed, max 2 levels
- `prefer_browser=true` to start directly from browser

### search — parallel + quorum + consensus

```
DDG + Google + Hound sent in parallel
     ↓
Consensus weighting (same URL by multiple engines → ranked higher)
     ↓
Max 2 results per domain (prevent single-site domination)
     ↓
All fail → DirectFetch → (last resort) browser search
```

---

## 🎯 Agent-Friendly Responses

Every response includes actionable signals:

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

| Field | Purpose |
|-------|---------|
| `content_ok` | Content is actually usable (not error page / empty) |
| `page_type` | `article` / `list` / `js_shell` / `pdf` / `error` |
| `next_action` | Tells the agent what to try: `switch_source` / `upgrade_browser` / `retry` / `give_up` |
| `engine_chain` | Full engine chain for debugging |

---

## 🖥️ Headful Mode: Cloudflare Hard-Challenge Breakthrough

| Mode | StackOverflow (CF hard challenge) | Result |
|------|-----------------------------------|--------|
| headless Chrome + stealth | ❌ Stuck on "Just a moment" | headless mode itself flagged |
| headless Edge + stealth | ❌ Stuck | Same — unrelated to browser brand |
| **headful Chrome (no stealth)** | ✅ **Pass** | **Headful is the real solution** |
| **headful Edge + stealth** | ✅ **Pass** | Windows out-of-the-box |

**Key finding: CF detects headless mode itself — not browser brand or stealth JS.** Real window rendering passes.

| Level | Behavior |
|-------|---------|
| **Default: headless** | No popups, sufficient for 95% of sites |
| **Detect CF hard challenge** | Auto-upgrade to site-level headful |
| **Windows** | Offscreen hidden (`--window-position=-32000,-32000`) → zero disruption |
| **Linux server** | Needs Xvfb (pyvirtualdisplay) |

---

## 🛠️ Tool Reference (14 tools)

| Tool | Description |
|------|-------------|
| `search(query, max_results)` | Parallel search + consensus + diversity |
| `scrape(url, focus?, prefer_browser?, require_fresh?)` | HTTP-first → browser upgrade; BM25 focus extraction |
| `status()` | Engine availability + browser pool + cache + user manual |
| `deep_search(query, sources?)` | Tech source parallel search (GitHub / npm / MDN / HN / arXiv) |
| `parallel_scrape(urls, ...)` | Concurrent scraping (≤5) |
| `crawl(url, max_depth, max_pages)` | BFS crawler; single-page fail auto-upgrades to browser |
| `map(url)` | Site structure (sitemap + internal links) |
| `smart_browse(url)` | **UnifiedBrowser-first**: guaranteed JS rendering for SPAs |
| `browser_navigate(url)` | Stealth browser navigation |
| `browser_get_content(format)` | Page text / HTML |
| `browser_screenshot(full_page)` | Screenshot (base64 PNG) |
| `browser_evaluate(expression)` | Execute JavaScript |
| `browser_interact(action, ...)` | CDP-native: click / fill / type / hover / select / scroll / press / wait_for / upload |
| `browser_status()` | Browser pool + identity engine status |

---

## ✅ Verification

| Check | Result |
|-------|--------|
| Functional tests | **111/111 passed** |
| Fingerprint | **496/496 all pass (31 items × 16 profiles)** |
| Cloudflare real-world | nowsecure ✅ · Medium ✅ · SO headful ✅ |
| MCP handshake | initialize + 14 tools + scrape ✅ |
| Fresh-venv install | mcp+websockets+httpx only → search/scrape works |

---

## ⚠️ Honest Limitations

| Limitation | Behavior |
|------------|----------|
| DataDome / Akamai / interactive Turnstile | May not pass. `next_action` suggests switching source |
| Login walls | Not bypassed |
| Deep Shadow-DOM | Partially reachable, not fully penetrated |
| No browser on machine | HTTP engines still work (graceful degradation) |
| PDF OCR / neural ranking | Not built-in — install Hound (Tier 1) |
| Linux server headful | Needs Xvfb (Windows offscreen verified) |

---

## 📁 Project Structure

```
unified-fetch/
├── unified-fetch-server.py       # MCP Server entry (2,031 lines, 14 tools)
├── ARCHITECTURE.md               # Full architecture document
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

## 📄 License

MIT — see [LICENSE](LICENSE) for details.
