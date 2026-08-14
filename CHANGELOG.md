# Changelog

## 2.0.0 (2026-08-14) — Browser-first engine redesign (design complete + implementation shipped)

### Architecture Inversion
- **HTTP-first + browser auto-upgrade** (replaces v1's "browser as plugin"): scrape goes HTTP first (~1s), auto-upgrades to UnifiedBrowser only when blocked/JS shell/empty content
- **search = parallel + quorum + consensus + diversity** (Hound-style): DDG/Google/Hound run in parallel, cross-engine consensus weighting, ≤2 per domain
- **UnifiedBrowser becomes core weapon**: interact/rendering/hard-site breakthrough all go through CDP native; smart_browse is the UnifiedBrowser-first dedicated tool
- Hound (master-fetch) downgraded to **Tier 1 optional engine** (auto-enabled if installed, auto-skipped if not)

### New
- `browser_interact`: CDP native interact (click/fill/type/hover/select/scroll/press/wait_for/upload_file/cookies/dialog/evaluate/screenshot…); drag + frame only fallback to Playwright
- Actionable signals: `content_ok` / `page_type` / `next_action` / `engine_used` / `engine_chain` / `is_stale`
- BM25 focus extraction: `scrape(url, focus=...)` returns only relevant sections
- Smart cache: SQLite WAL, bad content never cached, size cap auto-eviction, `require_fresh`
- Connect-time instructions (orientation doc)
- Edge auto-detection (Windows out-of-box) + `UNIFIED_BROWSER_PATH`
- CDPSession: `fill` / `hover` / `select_option` / `wait_for_selector` / `upload_file` / `gc` (Memory.simulatePressureNotification)

### Tool Surface (15 → 14)
- `smart_scrape` merged into `scrape`
- `interact` → `browser_interact`
- `v2_browser_*` → `browser_*`
- `smart_browse` retained (UnifiedBrowser-first)

### Fixes
- DDG HTML parsing supports protocol-relative `//duckduckgo.com/l/?uddg=` links (search fallback fix)
- `UnifiedBrowser.navigate` session creation failure retries once (Chrome restart race fix)
- smart_browse cache storage uses `FetchResult` attribute fix

### Tests
- `test_v2_full_smoke.py` fully rewritten: Layer A-F (browser module + integration + 14 tool surfaces + HTTP-first chain + CDP interact + cache + parallel search)
- **111/111 tests pass** + fresh-venv minimal install smoke test (only mcp+websockets+httpx needed to search/scrape)

---

## 1.x (v1 blueprint, historical)
- unified-fetch v1: 4 search engines + 6 extraction engines + sequential fallback + Playwright interact (README at `../unified-fetch/`)

## 2.0.1 (2026-08-14) — P0 Fingerprint Verification Fix (found via honest assessment)

### Findings (dug out during incomplete work verification)
- **STEALTH_JS completely broken**: `const original GOPD` extra whitespace → SyntaxError, the entire stealth script never executed. All previous
  "webdriver=false / fingerprint pass" were headless Chrome's natural state, not the patch's doing
- **Fingerprint parser missed hard indicators**: `class="result passed"` fixed-order regex missed WebGL Vendor/Renderer,
  Broken Image Dimensions → fake 100% (only tested 8 easy-pass items)
- **webdriver patch self-detonated**: defining webdriver as navigator own enumerable property → sannysoft "WebDriver (New)"
  check detects `hasOwnProperty` → instead flagged as present (detection signal)

### Fixes
- anti_detect.py: Fix STEALTH_JS syntax error (original GOPD → originalGOPD)
- anti_detect.py: webdriver changed to only patch Navigator.prototype (no own property), keep getOwnPropertyDescriptor hidden
- anti_detect.py: WebGL getParameter patch covers GL_VENDOR(0x1F00)/GL_RENDERER(0x1F01) (this is what sannysoft reads)
- cdp_driver.py: `--use-gl=swiftshader-webgl` (Chrome 132+ deprecated) → `--enable-unsafe-swiftshader` + `--use-angle=swiftshader`
- cdp_driver.py: Remove `--disable-software-rasterizer` (kills software WebGL)
- fingerprint_verify.py: Parser changed to grab all passed/failed cells regardless of attribute order (11 items)
- fingerprint_verify.py: test() merges production DEFAULT_CHROME_ARGS (verification must test real production settings)

### Results
- bot.sannysoft.com **176/176 all pass (11 items × 16 profiles)** — this time it's real 100%
- Verification loop now does real measurement: parser fix fake 100% → 82% (real) → 91% (SwiftShader fix) → 100% (STEALTH_JS fix)

## 2.0.2 (2026-08-14) — P0 Cloudflare Real-World Test + Fingerprint Indicators Expanded

### P0 Cloudflare Real-World Test (Real anti-crawl sites)
| Site | Protection | Result |
|---|---|---|
| **nowsecure.nl** | CF Turnstile challenge | ✅ pass — got `cf_clearance`, shows "NOWSECURE by nodriver" success marker |
| **medium.com** | CF check | ✅ pass — renders full Medium content (title + navigation + content) |
| **stackoverflow.com** | CF hard challenge | ❌ stuck on "Just a moment" challenge page (see below) |

**Honest Assessment**:
- nowsecure.nl (hardest Turnstile) **auto-passes** — no manual click needed, STEALTH_JS + SwiftShader WebGL active
- **SO CF challenge stuck**: No iframe, no checkbox, pure "Performing security verification" JS challenge
  does not auto-complete. Even unpatched natural headless gets stuck — judgment is **Chrome 148 headless flagged by CF side**
  (Hound real implementation uses system Chrome `channel=chrome` + Playwright stealth to pass SO level).
  `next_action` reports switch_source, honest about not faking success

### Fingerprint Indicators Expanded (Canvas/Audio → 31 items)
- Added **fpscanner table (20 items)** parsing: PHANTOM_*/HEADCHR_*/CHR_*/SELENIUM_*/VIDEO_CODECS/SEQUENTUM
- Fix **HeadlessChrome UA leak**: `--user-agent` override added to DEFAULT_CHROME_ARGS (original UA contained HeadlessChrome)
- Fix **`--disable-blink-features=AutomationControlled` missing**: Added to production args
- Result: **496/496 all pass (31 items × 16 profiles)** — covers UA/WebGL/plugins/permissions/Canvas/phantom series

### Verification
- 111/111 regression tests pass (fingerprint fix no breakage)
- STEALTH_JS node syntax check passes

## 2.0.3 (2026-08-14) — SO-Level Breakthrough: headful auto-upgrade

### Breakthrough (comparative test evidence)
| Mode | StackOverflow (CF hard challenge) | Result |
|---|---|---|
| headless Chrome + stealth | ❌ stuck "Just a moment" | no cf_clearance |
| headless Edge + stealth | ❌ stuck | no |
| **headful Chrome (no stealth)** | ✅ **pass** | ✅ |
| **headful Edge + stealth** | ✅ **pass** | ✅ |

**Conclusion: CF's detection of SO = headless mode itself, unrelated to browser brand or stealth JS.** Real window rendering passes.
Headful needs no stealth patches (patches still retained for other detection).

### Implementation (headful only deployed when needed)
- `cdp_driver`: `headless=False` → headful; `headful_mode` (offscreen/xvfb/visible)
  - offscreen (Windows default): `--window-position=-32000,-32000` hides window, no disruption
  - xvfb: Linux server virtual display
- `create_session` fallback: When headful just started, `Target.createTarget` fails
  ("Failed to open new tab") → auto-attach to existing page target then navigate
- `unified_browser`:
  - `_detect_cf_challenge` ("Just a moment" / "Performing security verification")
  - `navigate_headful()`: site-level escalation (session_pool `escalate_to_headful`)
  - `fetch()` reports `cf_challenge` + `headful` flag
- `server`:
  - `scrape`: HTTP all-fail → headless browser → **detect CF wall auto-upgrade headful** → success
  - `smart_browse`: same auto-upgrade
  - engine chain tags `unified_browser(headful)`

### Verification
- **scrape SO full-chain success**: HTTP 6 engines fail → headless challenge → headful pass (35KB full content)
- smart_browse SO auto-upgrade passes
- **111/111 regression tests pass**

### Server Notes
- Windows: offscreen hidden (zero popups)
- Linux no-display: needs `Xvfb` (`pyvirtualdisplay`) — headful needs virtual display in no-X environment
