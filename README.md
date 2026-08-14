# 🌐 unified-fetch V2

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/okokjai/unified-fetch?style=flat&logo=github)](https://github.com/okokjai/unified-fetch)
[![GitHub Release](https://img.shields.io/github/v/release/okokjai/unified-fetch?style=flat&logo=github)](https://github.com/okokjai/unified-fetch/releases)
[![Platform: Claude Code](https://img.shields.io/badge/Platform-Claude%20Code-8A2BE2?style=flat&logo=anthropic)](https://claude.ai/code)
[![Zero Python MCP](https://img.shields.io/badge/Zero%20Python%20MCP-✅-brightgreen?style=flat)](https://github.com/okokjai/unified-fetch)

**Browser-core MCP server for Claude Code. HTTP-first, auto-upgrades to a CDP-native stealth browser. Zero Python dependencies required.**

**Its defining architecture: Identity Engine (fingerprint synthesis) → Behavior Engine (human emulation) → Session Pool (sandbox isolation). A unified 3-layer anti-detection strategy, not bolted-on features.**

```
search · scrape · deep_search · crawl · map · smart_browse
browser_navigate · browser_get_content · browser_screenshot
browser_evaluate · browser_interact · browser_status · status
```

Built on the v1 blueprint + v2 UnifiedBrowser (Raw CDP stealth browser) core.
Clone the repo, `pip install -r requirements.txt`, and you're ready — **no API keys, no config files, no extra engine installs**.

> **Zero Python MCP**: works with just `mcp` + `websockets` + `httpx` — search and scrape run out of the box (Tier 0). Everything else is optional try-import.

---

## ✨ Features

| Capability | Description |
|---|---|
| ⚡ **HTTP-first + auto-upgrade** | scrape goes HTTP first (~1s); auto-upgrades to stealth browser when blocked / JS shell / empty content |
| 🧬 **UnifiedBrowser core** | Raw CDP (no Playwright/Selenium/WebDriver). **3-layer anti-detection strategy**: Identity Engine (496/496 fingerprint synthesis) → Behavior Engine (human emulation) → Session Pool (sandbox isolation + headful escalation) |
| 🔍 **Parallel search + consensus** | DDG/Google/Hound in parallel, cross-engine consensus weighting, ≤2 per domain, quorum report |
| 🎯 **Actionable signals** | Every response carries `content_ok` / `page_type` / `next_action` / `engine_used` |
| ✂️ **BM25 focus** | `scrape(url, focus="what you're looking for")` returns only relevant paragraphs, saves 80%+ context |
| 🖱️ **CDP-native interact** | click/fill/type/hover/select/scroll/press/wait_for/upload… no Playwright involved |
| 💾 **Smart cache** | SQLite WAL, bad content never cached, size-limit auto-eviction, `require_fresh` force-refresh |
| 🚀 **Instant start** | Lazy browser init: server starts <1s, Chrome only launches on first browser use |
| 📦 **Zero config** | All engines try-import auto-detection; no Chrome → HTTP-only, never breaks |

---

## 🎯 The Core Strategy: 3-Layer Anti-Detection Architecture

> **This is what sets unified-fetch apart.** Not a list of bolted-on evasion tricks — but a *single unified strategy* of three complementary layers, each reinforcing the next, so every task the site sees looks like an independent, ordinary human visitor.

```
  Task A ─┐                    ┌─ Layer 1: Identity Engine (Who you are)
  Task B ─┼─ every task gets ──┼─ Layer 2: Behavior Engine  (How you act)
  Task C ─┘    its own profile ┴─ Layer 3: Session Pool     (Where you live)
```

### Layer 1 — Identity Engine: Per-Task Identity & Fingerprint Isolation

Every task receives a **fresh, internally-consistent fingerprint profile** — synthesized by an Identity Engine, not copied from your machine:

- **Synthetic identity** — UA / timezone / resolution / WebGL / canvas / fonts / navigator properties all generated per-task, so the site sees a brand-new independent user each time
- **Internal consistency** — GPU ↔ WebGL, screen ↔ device, timezone ↔ locale are cross-validated so profiles never contradict themselves (the #1 fingerprint giveaway)
- **Prevents fingerprint linking** — two tasks can never be tied back to the same physical machine

**Verified: 496/496 fingerprint checks pass** (31 groups × 16 profiles) across bot.sannysoft.com + fpscanner.

### Layer 2 — Behavior Engine: Human Behavior Isolation

A real visitor isn't a robot — so neither is unified-fetch:

- **Human-like actions** — mouse trajectories, scrolling rhythm, keystroke timing, hover pauses modeled to feel organic
- **Per-profile randomization** — each identity gets its own behavior signature instead of one shared bot rhythm
- **Session-consistent** — behavior stays coherent within a session, varying naturally across sessions

### Layer 3 — Session Pool: Sandbox Isolation & Instance Lifecycle

Each task runs in an **isolated sandbox** — cookies, storage, and context never leak across sites:

- **Site isolation** — Session Pool ensures separate browse contexts per site; no cross-site cookie/localStorage bleed
- **Lifecycle management** — every task gets a fresh browser context and is burned after use ("用完即焚"), so the next task starts from a clean initial state, untouched by previous tasks' data
- **Headful escalation** — when Cloudflare's hard challenge appears, the pool escalates to a real headful window (offscreen on Windows) and passes where headless gets stuck

```
  Define:   Identity Engine ──→ Behavior Engine ──→ Session Pool
    "who"       fingerprint        human motion      sandbox isolation
    independent  per-task           per-profile      burn-after-use
```

This unified strategy is what makes Cloudflare real-world pass (nowsecure ✅ / Medium ✅ / StackOverflow headful ✅) while staying HTTP-first for the other 95% of sites.

---

## 🚀 Install

```bash
git clone <your-repo>/unified-fetch-v2
cd unified-fetch-v2
pip install -r requirements.txt        # Tier 0: works immediately
```

**Browsers** (auto-detected, optional): Chrome / Edge (built into Windows) / Playwright chromium.

**Optional engines** (auto-enabled when installed, auto-skipped when not):

```bash
pip install -r requirements-optional.txt   # Hound deep anti-crawl + Playwright fallback + googlesearch + curl_cffi
playwright install chromium                # Only if you have no Chrome/Edge
```

| Tier | Contents | Dependencies |
|---|---|---|
| 0 | HTTP engines + search + browser core (CDP) | `mcp` `websockets` `httpx` `duckduckgo_search` `trafilatura` `readability-lxml` `justext` `newspaper3k` `lxml` |
| 1 (opt) | Hound (master-fetch) + Playwright + curl_cffi + googlesearch | `requirements-optional.txt` |

### MCP config (Claude Code / Cursor / etc.)

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

## ✅ Verified Results (2026-08-14)

| Verification | Result | Evidence |
|---|---|---|
| Functional tests | **112/112 assertions (19 test functions)** | Layer A-D (modules + integration + tool surface + engine chain + interact + cache + parallel search) |
| Fingerprint verification | **496/496 checks (31 × 16 profiles)** | bot.sannysoft.com + fpscanner, genuine 100% |
| Cloudflare real-world | nowsecure ✅ Medium ✅ SO ✅ (headful auto-upgrade) | real anti-crawl sites: headless → CF wall → headful passes |
| MCP handshake | initialize + 14 tools + scrape ✅ | real stdio protocol test |
| fresh-venv install | minimal deps (mcp+websockets+httpx) search+scrape works | out-of-the-box verification |

---

## 🔧 Tools (14)

| Tool | Description |
|---|---|
| `search(query, max_results)` | parallel search + cross-engine consensus + diversity |
| `scrape(url, prefer_browser, focus, require_fresh)` | HTTP-first → browser upgrade; `focus` returns relevant paragraphs |
| `status()` | engine availability + browser pool + cache + usage guide |
| `deep_search(query, sources=[github,npm,mdn])` | technical sources parallel search (no keys) |
| `parallel_scrape(urls, ...)` | concurrent scraping (≤5) |
| `crawl(url, max_depth, max_pages)` | BFS crawler, per-page failure auto-upgrades to browser |
| `map(url)` | site structure (sitemap + internal links) |
| `smart_browse(url)` | **UnifiedBrowser-first**: guaranteed SPA/JS rendering |
| `browser_navigate(url, wait_until, behavior)` | stealth browser navigation |
| `browser_get_content(format)` | page text/HTML |
| `browser_screenshot(full_page)` | screenshot (base64 PNG) |
| `browser_evaluate(expression)` | execute JS |
| `browser_interact(action, ...)` | CDP-native interaction (drag/frame fallback to Playwright) |
| `browser_status()` | browser pool + identity engine status |

### Response signals (agent-friendly)

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

- `content_ok` — content is genuinely usable (not error/empty page)
- `page_type` — `article` / `list` / `js_shell` / `pdf` / `error`
- `next_action` — tells the agent what to do next on failure (`switch_source` / `upgrade_browser` / `retry` / `give_up`)
- `engine_chain` — which engines ran (verification + debugging)

---

## 💡 Usage Patterns

### #1 Research workflow

```
search("python httpx async") → pick URLs → scrape(url, focus="timeout retry") → synthesize
```

### Engine chain (HTTP-first + auto-upgrade)

```
scrape:
  1. Hound (if present) → newspaper → trafilatura → readability → justext → direct   (~1s)
  2. Blocked / JS shell / all dead → UnifiedBrowser (CDP stealth)                     (5-10s)
  3. CF interactive challenge (e.g. StackOverflow) → UnifiedBrowser headful (real window) (+5-10s)
  4. prefer_browser=true → start from browser directly

search:
  DDG + Google + Hound parallel → consensus weighting → ≤2/domain → all dead → direct → browser search
```

### JS-rendered pages

```
smart_browse("https://spa-example.com")      # guaranteed rendering, UnifiedBrowser-first
```

### Headful mode (CF hard challenge auto-upgrade)

| Mode | StackOverflow (CF hard challenge) | Result |
|---|---|---|
| headless Chrome + stealth | ❌ stuck on "Just a moment" | headless itself flagged by CF |
| headless Edge + stealth | ❌ stuck | same (brand-independent) |
| **headful Chrome (no stealth)** | ✅ **passes** | **headful is the real fix** |
| **headful Edge + stealth** | ✅ **passes** | works out of the box on Windows (built-in Edge) |

**Key finding: CF's judgment of SO = the headless mode itself, unrelated to browser brand or stealth JS.**
Headful is scrape / smart_browse's auto-upgrade strategy (not default): HTTP all-dead → headless hits CF wall → auto-upgrades to headful.

| Layer | Behavior |
|---|---|
| **Default: headless** | zero popups, enough for 95% of sites |
| **CF hard challenge detected** | auto-upgrades site-level headful (session_pool.escalate_to_headful) |
| **Windows** | offscreen hidden (`--window-position=-32000,-32000`) → zero distraction |
| **Linux server** | needs Xvfb (pyvirtualdisplay) — headful needs virtual display without X |

---

## 🧪 Test

```bash
python test_v2_full_smoke.py     # 112 assertions (19 test functions)
```

---

## ⚠️ Known Limits (Honest Limits)

### Verified boundaries

| Limit | Behavior |
|---|---|
| DataDome / Akamai / interactive Turnstile | may not pass. `next_action` tells you to switch source |
| Login walls | not bypassed (interact doesn't handle authenticated sessions) |
| Deep Shadow-DOM | partially reachable (scroll/click/wait_for), not fully wired |
| Machine without browser | HTTP engines still fully work (graceful degradation) |
| PDF OCR / neural ranking | not built-in — install Hound (Tier 1) to get it |

### Known unimplemented gaps

| Item | Status |
|---|---|
| P2 behavior data store (real timing collection) | not done — `behavior.py` still uses hand-tuned Gaussian params |
| Linux server headful | needs Xvfb (Windows offscreen verified; Linux leaves interface) |

---

## 🚫 Gotchas

- **`pip install` does not install browser binaries.** `playwright install chromium` only needed if you have no Chrome/Edge. `status()` tells you if a browser is available
- **Cache defaults to 1 hour.** For live content pass `require_fresh=true`; cache hits show `duration_ms: 0`
- **Search is HTTP.** Browser doesn't participate in search (same design as Hound: search is 100% HTTP)
- **`robots.txt` not checked by default.** This is a research tool, not a bulk scraper
- **No mass scraping.** High-frequency bulk access will get you anti-crawl blocked — this is an agent research tool, not Scrapy

---

## 📄 License

MIT

---

## 📦 Project Structure

```
unified-fetch-v2/
├── unified-fetch-server.py       # MCP Server entry (2031 lines, 14 tools)
├── ARCHITECTURE.md               # 562 lines, Status: IMPLEMENTED
├── README.md                     # this file
├── CHANGELOG.md                  # version history
├── requirements.txt              # Tier 0 dependencies
├── requirements-optional.txt     # Tier 1 optional dependencies
├── test_v2_full_smoke.py         # 112 assertions (19 test functions)
└── browser/                      # CORE: UnifiedBrowser package (5489 lines)
    ├── cdp_driver.py             # Raw CDP + CDPSession (full interact actions)
    ├── unified_browser.py        # integration entry (navigate/get_text/screenshot/…)
    ├── identity.py               # Identity Engine (profile synthesis + site routing)
    ├── anti_detect.py            # Anti-detection + BotPageDetector
    ├── behavior.py               # Behavioral Engine (human behavior)
    ├── session_pool.py           # Session Pool (site isolation + lifecycle + headful upgrade)
    └── fingerprint_verify.py     # fingerprint verification
```

> 10 .py files, 8,703 lines total (2031 server + 5489 browser/ + 497 test + 562 ARCHITECTURE.md + 124 CHANGELOG.md). v2.0.0 all done: HTTP-first + CDP stealth core + headful CF breakthrough + fingerprint 496/496.

---

## 🔗 Related

- Full architecture: [ARCHITECTURE.md](ARCHITECTURE.md)
- Version history: [CHANGELOG.md](CHANGELOG.md)
- v1 blueprint: `../unified-fetch/`
- Hound (master-fetch, optional engine): https://github.com/dondai1234/master-fetch

---

*Last updated: 2026-08-14*
*Author: PaulPaul + Claude Code*
*License: MIT · Platform: Claude Code · Zero Python MCP*
*Status: v2.0.0 — 112/112 assertions + 496/496 fingerprint + SO headful breakthrough + CF real-world verified*
