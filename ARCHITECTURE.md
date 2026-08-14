# unified-fetch V2 — Architecture

> Single source of truth for the unified-fetch V2 design.
> All implementation decisions are derived from this document.

> **Status: IMPLEMENTED** — 111/111 tests + P0 fingerprint verification 496/496 + SO-level CF breakthrough (headful).
> This version is finalized per PaulPaul's final decision: **HTTP-first + browser auto-upgrade (headless → headful)**, browser is the core weapon (interact/rendering/SO-level breakthrough), but no longer the primary engine for all scraping.

---

## 1. Design Philosophy

V2's DNA: **not all-powerful, but pluggable, composable, extensible, works out of the box.**

### Works Out of the Box (works immediately after GitHub clone)

User clones then `pip install -r requirements.txt` — **zero config**:

- Tier 0 hard dependencies are only `mcp` + `websockets` (all CDP in browser/ uses stdlib)
- Search/extraction engines are all optional: `try: import` auto-detect, enabled if installed, skipped if not
- Browser auto-detects Chrome / Edge / Playwright bundled Chromium (Edge guaranteed on Windows)
- **No config file**. `status()` reports what's available and what's not

### Relationship with Hound (master-fetch)

**Hound is an optional Tier 1 engine** (deep anti-crawl, PDF OCR, neural ranking outsourced), not a competitor. We are the lightweight out-of-box core; Hound is the power expansion.

### V2 vs V1 Blueprint

| Dimension | V1 (Blueprint) | V2 |
|------|-----------|-----|
| Engine chain | Sequential fallback (4/6 engines) | HTTP-first + browser auto-upgrade + parallel search |
| Browser | Playwright plugin | UnifiedBrowser (CDP native) built-in core weapon |
| Interact | Tied to Playwright | CDP native (fill/hover/select/wait complemented), Playwright fallback only |
| Search | Sequential fallback | Parallel + quorum + consensus + diversity |
| Out-of-box deps | playwright required | Only mcp + websockets |

---

## 2. Architecture — Three-Legged Stool (Inverted)

```
┌─────────────────────────────────────────────────────────────────┐
│                 unified-fetch V2 — Three-Legged Stool (Inverted)                 │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │ Left Leg: Engine Chain (HTTP-first + auto-upgrade)                       │ │
│  │  └─ scrape: HTTP extraction → blocked → UnifiedBrowser              │ │
│  │  └─ search: parallel + quorum + consensus + diversity                   │ │
│  │  └─ 6 types of heterogeneous sources + source type tagging              │ │
│  └──────────────────────┬────────────────────────────────────┘ │
│                         │                                       │
│  ┌──────────────────────▼────────────────────────────────────┐ │
│  │ Belt: Scheduling Layer (the soul connecting the two legs)                               │ │
│  │  └─ site_health 2D matrix (site × engine historical success rate)              │ │
│  │  └─ decide_fetch_tier(domain) → auto-select starting point                │ │
│  │  └─ auto-upgrade (escalate on failure, max 2 levels)                        │ │
│  │  └─ Each result includes next_action + content_ok + page_type        │ │
│  └──────────────────────┬────────────────────────────────────┘ │
│                         │                                       │
│  ┌──────────────────────▼────────────────────────────────────┐ │
│  │ Right Leg: CORE = UnifiedBrowser (CDP native)                    │ │
│  │  └─ Identity Engine (identity isolation)                            │ │
│  │  └─ Anti-detection (stealth patches + bot detection)                  │ │
│  │  └─ Behavioral Engine (human behavior)                          │ │
│  │  └─ Session Pool (site isolation + lifecycle)                    │ │
│  │  └─ CDP interact (fill/hover/select/wait complemented)            │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  Infrastructure: SQLite smart cache  │  circuit breaker  │  metrics  │  focus  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Directory Structure

```
unified-fetch-v2/
├── unified-fetch-server.py       # MCP Server entry (replaces unified-fetch-v2-server.py)
├── ARCHITECTURE.md               # This document
├── README.md                     # Installation + usage + Gotchas + Honest limits
├── CHANGELOG.md
├── requirements.txt              # Tier 0: mcp, websockets, httpx, duckduckgo_search, extraction engines
├── requirements-optional.txt     # Tier 1: master-fetch, playwright, curl_cffi, googlesearch
│
├── browser/                      # CORE: UnifiedBrowser package (in-place reuse)
│   ├── cdp_driver.py             # Raw CDP + CDPSession (complement fill/hover/select/wait_for/upload)
│   ├── identity.py               # Identity Engine (profile synthesis + site routing)
│   ├── anti_detect.py            # Anti-detection + BotPageDetector
│   ├── behavior.py               # Behavioral Engine (human behavior)
│   ├── session_pool.py           # Session Pool (site isolation + lifecycle)
│   ├── fingerprint_verify.py     # Fingerprint verification
│   └── unified_browser.py        # Integration entry (navigate/get_text/screenshot/evaluate/…)
│
├── adapters/                     # Engine adapters (pluggable) — progress: server single-file inline first, extract when stable
│   ├── base.py                   # Engine / ScrapeEngine / SearchEngine abstraction
│   ├── unified_browser.py        # UnifiedBrowser engine (CDP main weapon)
│   ├── hound.py                  # Hound adapter (optional Tier 1)
│   ├── newspaper.py / trafilatura.py / readability.py / justext.py / direct.py
│   └── duckduckgo.py / googlesearch.py / github.py / npm.py / mdn.py
│
├── extract/                      # Content extraction utilities
│   ├── bm25.py                   # BM25 focus extraction (focus parameter)
│   ├── structured.py             # Structured extraction (page_type detection)
│   └── dedup.py                  # URL normalization + deduplication
│
├── cache/                        # Cache layer
│   └── sqlite_cache.py           # SQLite smart cache (WAL + bad content not cached + size cap)
│
├── tools/                        # MCP tool definitions
│   ├── search.py / scrape.py / status.py / deep_search.py
│   ├── parallel_scrape.py / crawl.py / map.py
│   └── browser.py                # browser_* tools (navigate/get_content/screenshot/evaluate/interact/status)
│
└── test_v2_full_smoke.py         # Tests (Layer A/B/C/D)
```

> Implementation order: Phase 1 server single-file inline all engines (following v1 pattern), then extract adapters/ package once architecture is stable.

---

## 4. Engine Abstraction (Pluggable Core)

All engines inherit abstract base, order = priority, new engine = one line in registry.

```python
class Engine:
    name: str
    optional: bool = False                 # True = Tier 1 optional
    async def is_available(self) -> bool: ...  # try-import auto-detect
    async def is_healthy(self) -> bool: ...    # Circuit breaker status

class ScrapeEngine(Engine):
    async def scrape(self, url: str, options: ScrapeOptions) -> ScrapeResult

class SearchEngine(Engine):
    async def search(self, query: str, count: int = 10) -> SearchResult
```

### Registry (order = priority)

```python
# scrape/fetch chain: HTTP-first → blocked → browser
SCRAPE_CHAIN = [
    HoundEngine,          # Tier 1 optional (deep anti-crawl/PDF OCR/neural) — use if available
    NewspaperEngine,      # Tier 0
    TrafilaturaEngine,    # Tier 0
    ReadabilityEngine,    # Tier 0
    JusTextEngine,        # Tier 0
    DirectEngine,         # Tier 0 (last resort)
    UnifiedBrowserEngine, # Built-in CDP main weapon (escalation target / prefer_browser starting point)
]

# search chain: parallel + quorum + consensus
SEARCH_CHAIN = [
    DuckDuckGoEngine,     # Tier 0 (no key, <1s)
    GoogleEngine,         # Tier 1 optional
    HoundEngine,          # Tier 1 optional
    DirectEngine,         # Tier 0 (last resort)
    BrowserSearchEngine,  # Built-in (last resort when all HTTP is blocked)
]
```

### ScrapeResult (Hound-style actionable signals)

```python
@dataclass
class ScrapeResult:
    url: str
    ok: bool
    content: str
    title: str
    content_ok: bool                  # Content is actually usable (not error page/empty)
    page_type: str                    # "article" | "list" | "js_shell" | "pdf" | "error"
    engine_used: str
    engine_chain: list[str]           # Which engines were used
    next_action: str                  # Tells agent next step on failure (switch source / upgrade browser / retry)
    is_stale: bool                    # Content may be outdated (freshness hint)
    duration_ms: int
    warnings: list[dict]
```

---

## 5. Engine Chains (Decision ⑥ Finalized)

### scrape/fetch chain — HTTP-first + auto-upgrade

```
1. HTTP extraction chain (~1s)
   Hound (if available) → newspaper → trafilatura → readability → justext → direct
    Quality gate: empty content / <50 chars / error page → fail, move to next engine (v1 pattern)

2. Blocked / JS shell / all-fail → auto-upgrade UnifiedBrowser (CDP stealth)
   Identity + Behavior + site isolation — strongest weapon goes at chain end
   Upgrade trigger: 403/429/bot page / content empty or very short

3. prefer_browser=true → start directly from browser (respects caller's intent)

Each layer failure includes next_action: switch source / upgrade browser / retry (respects Retry-After)
```

**Why HTTP-first instead of browser-first** (PaulPaul decision, memory stored):
- Works out of the box: machines without Chrome (Linux servers/Termux/CI) still work with HTTP engines
- Speed: HTTP ~1s vs browser 5-10s
- Browser is the **last resort weapon**, not the cost for every request

### search chain — parallel + quorum + consensus (Hound-style)

```
1. DDG + Google (optional) + Hound (optional) parallel
2. quorum: at least 2 engines must contribute (single-engine bias cannot dominate)
3. Cross-engine consensus weighting (same URL returned by multiple engines → weighted)
4. Diversity: top results ≤2 per domain
5. All-fail → DirectFetch → (last resort) browser search
6. Per-engine circuit breaker 60s cooldown + Retry-After respect
```

### smart_browse chain — UnifiedBrowser-first (primary intent entry)

```
1. Direct UnifiedBrowser (CDP stealth) — guarantees JS rendering/SPA
   require_fresh=true → force live fetch (skip cache)
2. Browser failure → report blocked + next_action (do NOT downgrade to HTTP,
   because caller explicitly wants rendering — downgrade would fake success)
```

**Division of labor: scrape = fast (HTTP-first), smart_browse = guaranteed rendering (UnifiedBrowser-first).**
The intent of "primary uses UnifiedBrowser" lives here + browser_* tools (interact/screenshot/JS execution).

### Search Source Routing (retained)

| Type | Backend | Dependency | Rate Limit |
|------|---------|------------|------------|
| web | DDG (+ Google/Hound parallel) | ddgs etc | None |
| code | GitHub public API | httpx | 60 req/hr |
| docs | MDN + Wikipedia API | httpx | None |
| news | HackerNews API | httpx | None |
| pkg | npm + PyPI + crates.io | httpx | None |
| academic | arXiv + PubMed | httpx | 10 req/s |

---

## 6. UnifiedBrowser — CORE (Right Leg, Unchanged Built-in Core)

UnifiedBrowser is V2's built-in CDP native browser engine, located in the `browser/` package. **This is this design's core weapon** — interact, rendering, hard-site breakthroughs all rely on it.

```
UnifiedBrowser (browser/)
├── CDP Transport (cdp_driver.py)
│   └── Raw WebSocket → Chrome DevTools Protocol
│   └── No Playwright, no Selenium, no WebDriver
│   └── navigator.webdriver = undefined (natural stealth)
│   └── Complement interact actions: fill / hover / select / wait_for / upload_file
│
├── Identity Engine (identity.py)
│   ├── Profile Factory — synthesize real Chrome fingerprints
│   ├── Profile Pool — per-site identity isolation
│   └── Fingerprint Validation — bot.sannysoft.com automated testing
│
├── Anti-detection (anti_detect.py)
│   ├── CDP leak patches (webdriver/plugins/languages/WebGL)
│   ├── Resource blocking (CDP-level)
│   ├── Bot page detection (CF/reCAPTCHA/Akamai/DataDome)
│   └── Fake content detection (cross-engine hash comparison)
│
├── Behavioral Engine (behavior.py)
│   ├── Timing model — Gaussian distribution real data
│   ├── Mouse movement — Bezier curve + acceleration
│   ├── Typing — variable speed + 2% typo rate
│   └── Per-site behavior profiles (read/search/form/browse)
│
└── Session Pool (session_pool.py)
    ├── Browser instance pool (site isolation, per-site independent identity)
    ├── Lifecycle (idle recycle, age restart, failure restart)
    ├── Memory cap (400MB)
    └── Headful escalation (site-level: headless blocked by CF wall → headful)
```

### Lifecycle aligned with Hound stealth (adopted)

- **Single warm browser + idle recycle**: session_pool already has idle recycle (60s), aligned with Hound's 300s idle close (`HOUND_BROWSER_IDLE_TIMEOUT`)
- **`Memory.simulatePressureNotification`** (A9 adopted): Triggers Chrome GC after each fetch (CDP one-liner)

---

## 6.5 Headful Mode (SO-level CF Breakthrough)

### Comparative Test Evidence (2026-08-14)

| Mode | StackOverflow (CF hard challenge) | Conclusion |
|---|---|---|
| headless Chrome + stealth | ❌ stuck on "Just a moment" | headless itself flagged |
| headless Edge + stealth | ❌ stuck | same (unrelated to brand) |
| **headful Chrome (no stealth needed)** | ✅ pass | **headful is the real solution** |
| **headful Edge + stealth** | ✅ pass | Windows out-of-box (Edge built-in) |

**Key finding: CF's detection of SO = headless mode itself, unrelated to browser brand or stealth JS.**

### Design (headful only deployed when needed)

```
Default: headless (zero popups, sufficient for 95% of sites)
  ↓ Detect CF hard challenge (title="Just a moment" / "Performing security verification")
Auto-upgrade: site-level headful (session_pool.escalate_to_headful)
  - Windows: offscreen hidden (--window-position=-32000,-32000) → zero disruption
  - Linux server: Xvfb virtual display (pyvirtualdisplay)
  - Verified: offscreen headful still passes SO
```

### Headful Implementation Details

- `cdp_driver.start(headless=False, headful_mode="offscreen|xvfb|visible")`
- **create_session fallback**: When headful just started, `Target.createTarget` fails
  ("Failed to open new tab - no browser is open") → auto-attach to existing page target
  (chrome://intro homepage) then navigate. This is the real fix for headful startup race
- `unified_browser.navigate_headful()`: escalate → restart session → navigate
- `_detect_cf_challenge()`: title/content detection of CF wall
- `fetch()` reports `cf_challenge` + `headful` flag
- `scrape` / `smart_browse`: full-chain auto-upgrade (HTTP → headless → **headful**),
  engine chain tags `unified_browser(headful)`

### Capability Matrix (Final)

| Site Type | Engine | Latency |
|---|---|---|
| Regular sites | HTTP engines (Hound→newspaper→…) | ~1s |
| SPA/JS rendering | headless UnifiedBrowser | 5-10s |
| **SO-level CF hard challenge** | **headful auto-upgrade** | +5-10s |

---

## 7. Tools Specification

### Tool Surface (v1 blueprint, same shape + rename)

| Tool | Engine | Description |
|------|------|------|
| `search(query, max_results)` | search chain | parallel + quorum + consensus |
| `scrape(url, prefer_browser, focus, require_fresh)` | scrape chain | HTTP-first → browser upgrade; BM25 focus |
| `status()` | — | Engine availability + browser pool + cache + user manual |
| `deep_search(query, max_results, sources)` | github/npm/mdn | Heterogeneous source parallel |
| `parallel_scrape(urls, …)` | scrape chain | Concurrent (semaphore ≤5) |
| `smart_browse(url, max_age_months, require_fresh)` | **UnifiedBrowser-first** | SPA/JS rendering dedicated, guaranteed rendering |
| `crawl(url, max_depth, max_pages, stay_domain)` | HTTP BFS | Single-page fail upgrades to browser |
| `map(url, max_pages)` | HTTP | sitemap + internal link tree |
| `browser_navigate(url, wait_until, behavior)` | UnifiedBrowser | **v2_browser_* → browser_*** |
| `browser_get_content(format)` | UnifiedBrowser | text/html, 50K truncated |
| `browser_screenshot(full_page)` | UnifiedBrowser | base64 PNG |
| `browser_evaluate(expression)` | UnifiedBrowser | JS execution |
| `browser_interact(action, selector, value, …)` | CDP native (→Playwright fallback) | click/fill/type/hover/select/scroll/press/wait_for/… |
| `browser_status()` | UnifiedBrowser | pool + identity engine status |

### Tool Count: 14 (v1 15 − smart_scrape merged into scrape + interact → browser_interact rename; smart_browse retained + v2_browser_* → browser_* rename)

> smart_scrape merged into `scrape` (CDP native implementation, zero Playwright dependency); `interact` renamed to `browser_interact` (CDP native, Playwright only upload/drag/frame fallback); `smart_browse` retained as **UnifiedBrowser-first** dedicated tool (primary intent entry); `v2_browser_*` renamed to `browser_*` (honest naming).

### Actionable signals per response (Hound-style, A2 adopted)

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
  "warnings": []
}
```

### Connect-time instructions (Hound-style, A1 adopted)

MCP handshake injects a user manual once (~0.8K tokens): tool list, #1 workflow, known limitations. Ready on first turn, no repetition.

---

## 8. Anti-Crawl / Escalation Strategy

### Tier Definition (After Inversion)

| Level | Technique | Dependency | When to Use |
|-------|------|------|----------|
| L0 | HTTP + basic headers | httpx | Public APIs, docs |
| L1 | UA pool + random delay + cookie jar | httpx | Regular sites |
| L2 | curl_cffi TLS fingerprint | curl_cffi (optional) | E-commerce, news |
| L3 | UnifiedBrowser (CDP stealth) | Built-in (requires Chrome/Edge) | SPA, blocked, JS shell |
| L4 | UnifiedBrowser + Memory GC | Built-in | Routine browser use |

> Playwright is no longer an escalation target (upload/drag/frame fallback only). curl_cffi is optional.

### Auto-Upgrade (Max 2 Levels)

```
HTTP (L0-L2) → 403/429/empty/error → UnifiedBrowser (L3)
UnifiedBrowser → fail → report blocked + next_action (switch/retry)
```

### Circuit Breaker (v1 existing, add Retry-After)

- Per-engine independent circuit breaker (v1 existing)
- **Retry-After respect** (Hound A4): If 429 has Retry-After, wait accordingly; no aggressive retries
- Per-engine pacing floor (DDG 1.2s etc, Hound A4)

---

## 9. Cache Layer (Smart Cache, Hound A6)

```python
class SQLiteCache:
    def get(self, key: str) -> Optional[dict]
    def set(self, key: str, value: dict, ttl_seconds: int)
    def get_stats(self) -> dict
```

- **WAL mode** (concurrent read/write)
- **Bad content never cached**: error pages, empty content, 429/403, bot pages
- **Size cap evicts oldest** (prevents long-lived agent cache from growing unbounded)
- Cache key: URL + extraction type + focus; `require_fresh=true` forces live fetch
- Default TTL 1 hour (`cache_ttl`, Hound default); `duration_ms: 0` = cache hit

---

## 10. Hound Extraction List (Finalized)

### Adopt Immediately (A1-A8)

| # | Extraction | Implementation |
|---|---|---|
| A1 | connect-time instructions | Server handshake injects user manual |
| A2 | actionable signals | content_ok / next_action / page_type / is_stale |
| A3 | parallel + quorum + consensus + diversity | search chain refactored |
| A4 | circuit breaker + Retry-After + pacing | Complement v1 circuit breaker |
| A5 | BM25 focus extraction | scrape adds focus= parameter |
| A6 | smart cache | WAL + bad content not cached + size cap |
| A7 | content-adaptive extraction | page_type (article/list/js_shell) |
| A8 | honest limits documentation | README Gotchas / Honest limits section |
| A9 | Memory.simulatePressureNotification | Triggers Chrome GC after each fetch (cdp_driver one-liner) |

### Phase 2 (architecture reserved, not implemented)

| # | Extraction | Status |
|---|---|---|
| B1 | BYOK search (keys + env vars) | Interface reserved |
| B2 | 6-signal sort (domain reputation) | Stack after consensus goes live |
| B3 | self-healing CLI (--doctor) | Reserved |
| B5 | Single warm browser + idle recycle | Aligned with 300s idle |

### Explicitly Not Copied

- Neural rerank (ONNX 80MB) → outsourced
- PDF OCR → outsourced Hound adapter
- 10-engine parallel → stay lightweight (2-3 engines)

---

## 11. Requirements (Works Out of the Box)

### Tier 0 (requirements.txt) — installed and ready, zero config

```
mcp                  # MCP protocol
websockets           # CDP WebSocket (only non-stdlib hard dep in browser/)
httpx                # HTTP requests (L0-L1 + search engines)
duckduckgo_search    # DDG search (Tier 0)
trafilatura          # Extraction (Tier 0)
readability-lxml     # Extraction (Tier 0)
lxml[html_clean]     # Extraction dependency
justext              # Extraction (Tier 0)
newspaper3k          # Extraction (Tier 0)
```

### Tier 1 (requirements-optional.txt) — optional, auto-enabled if installed

```
master-fetch         # Hound (deep anti-crawl/PDF OCR/neural ranking)
playwright           # upload/drag/frame fallback only (browser binary needs separate install)
curl_cffi            # L2 TLS fingerprint
googlesearch-python  # Backup search
```

### Browser Detection (Enhanced, Edge added)

```
1. Environment variables: CHROME_PATH / UNIFIED_BROWSER_PATH
2. Edge (Windows guaranteed built-in): Program Files (x86)/Microsoft/Edge/.../msedge.exe
3. Chrome: Program Files/Google/Chrome/.../chrome.exe + ms-playwright bundled
4. shutil.which: chrome / chromium / msedge ...
5. All fail → HTTP-only mode (graceful degradation, Hound-style)
```

---

## 12. Error Handling

Unified error envelope (v1 pattern):

```json
{ "ok": false, "error": "...", "error_type": "blocked|auth|timeout|not_found|internal",
  "retryable": true, "next_action": "switch_source|upgrade_browser|retry|give_up" }
```

- Per-engine independent circuit breaker
- Auto-upgrade cap 2 levels (prevents 5-layer timeout cascade)
- 429 respects Retry-After
- Hard blocks (404/bot/auth) return clean errors, no fake success

---

## 13. Implementation Phases

> Phase 1-5 design finalized (previously deleted redesign superseded by this document)

```
Phase 1  Add cdp_driver interact actions (fill/hover/select/wait_for/upload_file)
Phase 2  Write new unified-fetch-server.py (replaces v2-server.py)
         - Engine abstraction + registry + engine chain (HTTP-first + escalation)
         - browser_* tools (rename + interact)
         - actionable signals + focus + page_type
         - connect-time instructions
Phase 3  Smart cache completed (WAL + bad content not cached + size cap)
Phase 4  Expand test_v2_full_smoke.py (Layer D: new engine chain + browser_* tools)
Phase 5  Update ARCHITECTURE.md (this document) / README (Gotchas + Honest limits)
Phase 6  fresh-venv install smoke test + mcp_servers.json update
```

---

## 14. Key Design Decisions (Finalized)

| # | Decision | Rationale |
|---|------|------|
| D1 | **HTTP-first + auto-upgrade** (not browser-first) | Works out of the box (PaulPaul personally inverted, memory stored) |
| D2 | Browser is the **last resort weapon** not primary engine | Speed + dependency + cross-platform |
| D3 | `prefer_browser=true` retained to force browser | Preserves caller intent |
| D4 | interact = CDP native, Playwright fallback only | no-Playwright philosophy |
| D5 | search = parallel + quorum + consensus | Hound A3 |
| D6 | smart_scrape/smart_browse merged into scrape | Tool surface simplification |
| D7 | v2_browser_* → browser_* (rename) | Honest naming (browser is already core) |
| D8 | Hound = Tier 1 optional | You prefer browser > Hound, but Hound is option |
| D9 | Tier 0 only mcp + websockets hard dep | Works out of the box |
| D10 | Edge detection added | Windows out-of-box rate near 100% |

---

## 15. What V2 Does NOT Do

- PDF OCR → outsourced Hound adapter
- Neural ranking → outsourced Hound adapter
- Mass scraping / high-frequency access (out of scope)
- Paid proxy services (provide interface, user supplies keys)
- 10-engine parallel search (stay lightweight)

---

*Last updated: 2026-08-14*
*Author: PaulPaul + Claude Code*
*Status: DESIGN COMPLETE — implementation pending*
