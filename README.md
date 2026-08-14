# 🌐 unified-fetch V2

**Browser-core MCP server. HTTP-first, auto-upgrades to a CDP-native stealth browser. Zero config.**

```
search · scrape · deep_search · crawl · map · smart_browse
browser_navigate · browser_get_content · browser_screenshot
browser_evaluate · browser_interact · browser_status · status
```

Based on v1 (unified-fetch) blueprint + v2 UnifiedBrowser (Raw CDP stealth browser) core.
GitHub clone then `pip install -r requirements.txt` works out of the box — **no API key, no config file, no other engines needed**.

---

## ✨ Features

| Capability | Description |
|---|---|
| ⚡ **HTTP-first + auto-upgrade** | scrape goes HTTP first (~1s); auto-upgrades to stealth browser only when blocked/JS shell/empty content |
| 🧬 **UnifiedBrowser core** | Raw CDP (no Playwright/Selenium/WebDriver), identity isolation + human behavior + anti-detection |
| 🔍 **Parallel search + consensus** | DDG/Google/Hound run in parallel, cross-engine consensus weighting, ≤2 per domain, quorum reporting |
| 🎯 **Actionable signals** | Every response includes `content_ok` / `page_type` / `next_action` / `engine_used` |
| ✂️ **BM25 focus** | `scrape(url, focus="what to find")` returns only relevant sections, saving 80%+ context |
| 🖱️ **CDP native interact** | click/fill/type/hover/select/scroll/press/wait_for/upload… no Playwright touch |
| 💾 **Smart cache** | SQLite WAL, bad content never cached, auto-eviction by size cap, `require_fresh` forces re-fetch |
| 🚀 **Second launch** | Lazy browser init: server starts <1s, Chrome opens only when browser is first needed |
| 📦 **Zero config** | All engines try-import auto-detect; no Chrome = HTTP-only, won't break |

---

## 🚀 Installation

```bash
git clone <your-repo>/unified-fetch-v2
cd unified-fetch-v2
pip install -r requirements.txt        # Tier 0: ready to use
```

**Browser** (auto-detected, optional): Chrome / Edge (Windows built-in) / Playwright chromium.

**Optional engines** (auto-enabled if installed, auto-skipped if not):

```bash
pip install -r requirements-optional.txt   # Hound deep anti-crawl + Playwright fallback + googlesearch + curl_cffi
playwright install chromium                # Only if you don't have Chrome/Edge
```

| Tier | Contents | Dependency |
|---|---|---|
| 0 | HTTP engines + search + browser core (CDP) | `mcp` `websockets` `httpx` `duckduckgo_search` `trafilatura` `readability-lxml` `justext` `newspaper3k` `lxml` |
| 1 (optional) | Hound (master-fetch), Playwright, curl_cffi, googlesearch | `requirements-optional.txt` |

### MCP Setup (Claude Code / Cursor, etc.)

```json
{
  "mcpServers": {
    "unified-fetch": {
      "command": "C:\\path\\to\\python.exe",
      "args": ["C:\\path\\to\\unified-fetch-v2\\unified-fetch-server.py"]
    }
  }
}
```

---

## ✅ Verification Results (2026-08-14)

| Verification Item | Result | Evidence |
|---|---|---|
| Functional tests | **72/72 assertions (19 test functions)** | Layer A-D (module+integration+tool surface+engine chain+interact+cache+parallel search) |
| Fingerprint verification | **496/496 items (31 groups × 16 profiles)** | bot.sannysoft.com + fpscanner, true 100% |
| Cloudflare real-world test | nowsecure ✅ Medium ✅ SO ✅ (headful auto-upgrade) | Real anti-crawl sites: headless → CF wall → headful pass |
| MCP handshake | initialize + 14 tools + scrape ✅ | stdio protocol real test |
| fresh-venv install | Minimal deps (mcp+websockets+httpx) can search/scrape | Out-of-box verification |

---

## 🔧 Tools (14)

| Tool | Description |
|---|---|
| `search(query, max_results)` | Parallel search + cross-engine consensus + diversity |
| `scrape(url, prefer_browser, focus, require_fresh)` | HTTP-first → browser upgrade; `focus` returns relevant sections |
| `status()` | Engine availability + browser pool + cache + user manual |
| `deep_search(query, sources=[github,npm,mdn])` | Tech source parallel search (no key needed) |
| `parallel_scrape(urls, ...)` | Concurrent scraping (≤5) |
| `crawl(url, max_depth, max_pages)` | BFS crawler, single-page failure auto-upgrades to browser |
| `map(url)` | Site structure (sitemap + internal links) |
| `smart_browse(url)` | **UnifiedBrowser-first**: SPA/JS rendering guaranteed |
| `browser_navigate(url, wait_until, behavior)` | Stealth browser navigation |
| `browser_get_content(format)` | Get page text/HTML |
| `browser_screenshot(full_page)` | Screenshot (base64 PNG) |
| `browser_evaluate(expression)` | Execute JS |
| `browser_interact(action, ...)` | CDP native interaction (drag/frame fallback Playwright) |
| `browser_status()` | Browser pool + identity engine status |

### Response Signals (agent-friendly)

```json
{
  "ok": true,
  "content": "...",
  "content_ok": true,
  "page_type": "article",
  "engine_used": "newspaper",
  "engine_chain": ["hound", "newspaper"],
  "next_action": "none",
  "is_stale": false,
  "duration_ms": 850,
  "cache_hit": false
}
```

- `content_ok` — Content is actually usable (not error page/empty)
- `page_type` — `article` / `list` / `js_shell` / `pdf` / `error`
- `next_action` — Tells agent next step on failure (`switch_source` / `upgrade_browser` / `retry` / `give_up`)
- `engine_chain` — Which engines were used (verification and debugging)

---

## 💡 Usage Patterns

### #1 Research Workflow

```
search("python httpx async") → pick URLs → scrape(url, focus="timeout retry") → synthesize
```

### Engine Chain (HTTP-first + auto-upgrade)

```
scrape:
  1. Hound (if available) → newspaper → trafilatura → readability → justext → direct   (~1s)
  2. Blocked / JS shell / all-fail → UnifiedBrowser (CDP stealth)                          (5-10s)
  3. CF interactive challenge (e.g. StackOverflow) → UnifiedBrowser headful (real window)           (+5-10s)
  4. prefer_browser=true → start directly from browser

search:
  DDG + Google + Hound parallel → consensus weighting → ≤2 per domain → all-fail → direct → browser search
```

### Pages Requiring JS Rendering

```
smart_browse("https://spa-example.com")      # Guaranteed rendering, UnifiedBrowser-first
```

### Headful Mode (CF Hard Challenge Auto-Upgrade)

| Mode | StackOverflow (CF hard challenge) | Description |
|---|---|---|
| headless Chrome + stealth | ❌ stuck on "Just a moment" | headless itself flagged by CF |
| headless Edge + stealth | ❌ stuck | Same (unrelated to brand) |
| **headful Chrome (no stealth)** | ✅ **pass** | **headful is the real solution** |
| **headful Edge + stealth** | ✅ **pass** | Windows out-of-box (Edge built-in) |

**Key finding: CF's detection of SO = headless mode itself, unrelated to browser brand or stealth JS.**
Headful is an auto-upgrade strategy for scrape / smart_browse (not default): HTTP all-fail → headless blocked by CF wall → auto-upgrade to headful.

| Level | Behavior |
|---|---|
| **Default: headless** | Zero popups, sufficient for 95% of sites |
| **Detect CF hard challenge** | Auto-upgrade site-level headful (session_pool.escalate_to_headful) |
| **Windows** | offscreen hidden (`--window-position=-32000,-32000`) → zero disruption |
| **Linux server** | Needs Xvfb (pyvirtualdisplay) — headful needs virtual display in no-X environment |

---

## 🧪 Testing

```bash
python test_v2_full_smoke.py     # 72 assertions (19 test functions)
```

---

## ⚠️ Known Limitations (Honest Limits)

### Verified Boundaries

| Limitation | Behavior |
|---|---|
| DataDome / Akamai / interactive Turnstile | May not pass. `next_action` tells you to switch source |
| Login walls | Not bypassed (interact does not handle authenticated sessions) |
| Deep Shadow-DOM | Partially reachable (scroll/click/wait_for), not fully penetrated |
| Machines without browser | HTTP engines still fully functional (graceful degradation) |
| PDF OCR / neural ranking | Not built-in — install Hound (Tier 1) to get them |

### Known Gaps Not Yet Implemented

| Item | Status |
|---|---|
| P2 behavior data storage (real timing collection) | Not done — `behavior.py` still uses hand-crafted Gaussian parameters |
| Linux server headful | Needs Xvfb (Windows offscreen verified; Linux interface reserved) |

---

## 🚫 Gotchas

- **`pip install` does not install browser binaries**. `playwright install chromium` only needed when no Chrome/Edge available. `status()` tells you if browser is available
- **Default cache is 1 hour**. For fresh content pass `require_fresh=true`; cache hit shows `duration_ms: 0`
- **Search is HTTP**. Browser does not participate in search (Hound design same: search 100% HTTP)
- **`robots.txt` not checked by default**. This is a research tool, not a bulk scraper
- **Do not mass-scrape**. Frequent high-volume access triggers anti-crawl blocks — this is an agent research tool, not Scrapy

---

## 📦 Project Structure

```
unified-fetch-v2/
├── unified-fetch-server.py       # MCP Server entry (2031 lines, 14 tools)
├── ARCHITECTURE.md               # 562 lines, Status: IMPLEMENTED
├── README.md                     # This document
├── CHANGELOG.md                  # Version history
├── requirements.txt              # Tier 0 dependencies
├── requirements-optional.txt     # Tier 1 optional dependencies
├── test_v2_full_smoke.py         # 72 assertions (19 test functions)
└── browser/                      # CORE: UnifiedBrowser package (5489 lines)
    ├── cdp_driver.py             # Raw CDP + CDPSession (complemented interact actions)
    ├── unified_browser.py        # Integration entry (navigate/get_text/screenshot/…)
    ├── identity.py               # Identity Engine (profile synthesis + site routing)
    ├── anti_detect.py            # Anti-detection + BotPageDetector
    ├── behavior.py               # Behavioral Engine (human behavior)
    ├── session_pool.py           # Session Pool (site isolation + lifecycle + headful escalation)
    └── fingerprint_verify.py     # Fingerprint verification
```

> Total 10 .py files, 8703 lines (2031 server + 5489 browser/ + 497 test + 562 ARCHITECTURE.md + 124 CHANGELOG.md). v2.0.0 fully complete: HTTP-first + CDP stealth core + headful CF breakthrough + fingerprint 496/496.

---

## 🔗 Related

- Full architecture: [ARCHITECTURE.md](ARCHITECTURE.md)
- Version history: [CHANGELOG.md](CHANGELOG.md)
- v1 blueprint: `../unified-fetch/`
- Hound (master-fetch, optional engine): https://github.com/dondai1234/master-fetch

---

*Last updated: 2026-08-14*
*Author: PaulPaul + Claude Code*
*Status: v2.0.0 — 72/72 assertions + 496/496 fingerprint + SO headful breakthrough + CF real-world verified*
