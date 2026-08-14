# unified-fetch V2 — Architecture

> Single source of truth for the unified-fetch V2 design.
> All implementation decisions are derived from this document.

> **Status: IMPLEMENTED** — 111/111 測試 + P0 指紋驗證 496/496 + SO 級 CF 突破（headful）。
> 本版依 PaulPaul 最終決策定案：**HTTP-first + 瀏覽器自動升級（headless → headful）**，browser 是核心武器（interact/渲染/SO 級突破），但不再是所有抓取的主引擎。

---

## 1. Design Philosophy

V2 的 DNA：**不是全能，而是可插拔、可組合、可擴展、開箱即用。**

### 開箱即用（GitHub clone 後直接可用）

使用者 clone 後 `pip install -r requirements.txt` 即可用，**零設定**：

- Tier 0 硬依賴只有 `mcp` + `websockets`（browser/ 的 CDP 全部 stdlib）
- 搜尋/提取引擎全選配：`try: import` 自動偵測，裝了自動啟用、沒裝自動跳過
- 瀏覽器自動偵測 Chrome / Edge / Playwright 內建 Chromium（Windows 的 Edge 保證存在）
- **沒有 config 檔案**。`status()` 回報誰可用誰不可用

### 與 Hound（master-fetch）的關係

**Hound 是選配 Tier 1 引擎**（深度反爬、PDF OCR、neural ranking 委外），不是競爭者。我們是輕量開箱核心，Hound 是威力擴充。

### V2 vs V1 藍本

| 維度 | V1（藍本） | V2 |
|------|-----------|-----|
| 引擎鏈 | 順序 fallback（4/6 引擎） | HTTP-first + 瀏覽器自動升級 + 平行 search |
| 瀏覽器 | Playwright 外掛 | UnifiedBrowser（CDP 原生）內建核心武器 |
| interact | 綁 Playwright | CDP 原生（fill/hover/select/wait 補齊），Playwright 僅 fallback |
| 搜尋 | 順序 fallback | 平行 + quorum + 共識 + 多樣性 |
| 開箱依賴 | playwright 必須 | 只有 mcp + websockets |

---

## 2. Architecture — Three-Legged Stool (翻轉版)

```
┌─────────────────────────────────────────────────────────────────┐
│                 unified-fetch V2 — 三腳凳（翻轉）                  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │ 左腿：引擎鏈（HTTP-first + 自動升級）                       │ │
│  │  └─ scrape: HTTP 提取 → 被擋 → UnifiedBrowser              │ │
│  │  └─ search: 平行 + quorum + 共識 + 多樣性                   │ │
│  │  └─ 6 類異質源 + 來源類型標記                              │ │
│  └──────────────────────┬────────────────────────────────────┘ │
│                         │                                       │
│  ┌──────────────────────▼────────────────────────────────────┐ │
│  │ 腰帶：調度層（連接兩腿的靈魂）                               │ │
│  │  └─ site_health 2D 矩陣（站點×引擎歷史成功率）              │ │
│  │  └─ decide_fetch_tier(domain) → 自動選擇起點                │ │
│  │  └─ 自動升級（失敗升一級，上限 2 級）                        │ │
│  │  └─ 每個結果附 next_action + content_ok + page_type        │ │
│  └──────────────────────┬────────────────────────────────────┘ │
│                         │                                       │
│  ┌──────────────────────▼────────────────────────────────────┐ │
│  │ 右腿：CORE = UnifiedBrowser（CDP 原生）                    │ │
│  │  └─ Identity Engine（身份隔離）                            │ │
│  │  └─ Anti-detection（隱身補丁 + bot 偵測）                  │ │
│  │  └─ Behavioral Engine（人類行為）                          │ │
│  │  └─ Session Pool（站點隔離 + 生命週期）                    │ │
│  │  └─ CDP interact（fill/hover/select/wait 補齊）            │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  基礎設施：SQLite 智慧快取  │  電路斷路器  │  metrics  │  focus  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Directory Structure

```
unified-fetch-v2/
├── unified-fetch-server.py       # MCP Server 入口（取代 unified-fetch-v2-server.py）
├── ARCHITECTURE.md               # 本文件
├── README.md                     # 安裝 + 使用 + Gotchas + Honest limits
├── CHANGELOG.md
├── requirements.txt              # Tier 0：mcp, websockets, httpx, duckduckgo_search, 提取引擎
├── requirements-optional.txt     # Tier 1：master-fetch, playwright, curl_cffi, googlesearch
│
├── browser/                      # CORE：UnifiedBrowser 套件（原地重用）
│   ├── cdp_driver.py             # Raw CDP + CDPSession（補 fill/hover/select/wait_for/upload）
│   ├── identity.py               # Identity Engine（profile 合成 + 站點路由）
│   ├── anti_detect.py            # Anti-detection + BotPageDetector
│   ├── behavior.py               # Behavioral Engine（人類行為）
│   ├── session_pool.py           # Session Pool（站點隔離 + 生命週期）
│   ├── fingerprint_verify.py     # 指紋驗證
│   └── unified_browser.py        # 整合入口（navigate/get_text/screenshot/evaluate/…）
│
├── adapters/                     # 引擎適配器（可插拔）── 進度：server 單檔先內聯，穩定後抽出
│   ├── base.py                   # Engine / ScrapeEngine / SearchEngine 抽象
│   ├── unified_browser.py        # UnifiedBrowser 引擎（CDP 主武器）
│   ├── hound.py                  # Hound 適配（Tier 1 選配）
│   ├── newspaper.py / trafilatura.py / readability.py / justext.py / direct.py
│   └── duckduckgo.py / googlesearch.py / github.py / npm.py / mdn.py
│
├── extract/                      # 內容提取工具
│   ├── bm25.py                   # BM25 聚焦提取（focus 參數）
│   ├── structured.py             # 結構化提取（page_type 偵測）
│   └── dedup.py                  # URL 正規化 + 去重
│
├── cache/                        # 快取層
│   └── sqlite_cache.py           # SQLite 智慧快取（WAL + 壞內容不快取 + 大小上限）
│
├── tools/                        # MCP 工具定義
│   ├── search.py / scrape.py / status.py / deep_search.py
│   ├── parallel_scrape.py / crawl.py / map.py
│   └── browser.py                # browser_* 工具（navigate/get_content/screenshot/evaluate/interact/status）
│
└── test_v2_full_smoke.py         # 測試（Layer A/B/C/D）
```

> 實作順序：第一階段 server 單檔內聯所有引擎（沿用 v1 模式），架構穩定後再拆 adapters/ 套件。

---

## 4. Engine Abstraction（可插拔核心）

所有引擎繼承抽象基底，順序 = 優先序，新引擎 = 註冊表加一行。

```python
class Engine:
    name: str
    optional: bool = False                 # True = Tier 1 選配
    async def is_available(self) -> bool: ...  # try-import 自動偵測
    async def is_healthy(self) -> bool: ...    # 電路斷路器狀態

class ScrapeEngine(Engine):
    async def scrape(self, url: str, options: ScrapeOptions) -> ScrapeResult

class SearchEngine(Engine):
    async def search(self, query: str, count: int = 10) -> SearchResult
```

### 註冊表（順序 = 優先序）

```python
# scrape/fetch 鏈：HTTP-first → 被擋 → 瀏覽器
SCRAPE_CHAIN = [
    HoundEngine,          # Tier 1 選配（深度反爬/PDF OCR/neural）——有就先用
    NewspaperEngine,      # Tier 0
    TrafilaturaEngine,    # Tier 0
    ReadabilityEngine,    # Tier 0
    JusTextEngine,        # Tier 0
    DirectEngine,         # Tier 0（最後手段）
    UnifiedBrowserEngine, # 內建 CDP 主武器（升級目標 / prefer_browser 起點）
]

# search 鏈：平行 + quorum + 共識
SEARCH_CHAIN = [
    DuckDuckGoEngine,     # Tier 0（無 key，<1s）
    GoogleEngine,         # Tier 1 選配
    HoundEngine,          # Tier 1 選配
    DirectEngine,         # Tier 0（最後手段）
    BrowserSearchEngine,  # 內建（所有 HTTP 被封鎖時的最後手段）
]
```

### ScrapeResult（Hound 式 actionable signals）

```python
@dataclass
class ScrapeResult:
    url: str
    ok: bool
    content: str
    title: str
    content_ok: bool                  # 內容真的可用（非錯誤頁/空頁）
    page_type: str                    # "article" | "list" | "js_shell" | "pdf" | "error"
    engine_used: str
    engine_chain: list[str]           # 走了哪些引擎
    next_action: str                  # 失敗時告訴 agent 下一步（換源/升級/重試）
    is_stale: bool                    # 內容可能過時（freshness 提示）
    duration_ms: int
    warnings: list[dict]
```

---

## 5. Engine Chains（深化⑥ 定案）

### scrape/fetch 鏈 —— HTTP-first + 自動升級

```
1. HTTP 提取鏈（~1s）
   Hound（若有）→ newspaper → trafilatura → readability → justext → direct
   品質閘門：空內容 / <50 chars / 錯誤頁 → 判失敗換下一家（v1 模式）

2. 被擋 / JS shell / 全滅 → 自動升級 UnifiedBrowser（CDP 隱身）
   Identity + Behavior + 站點隔離——最強武器放鏈尾
   升級判斷：403/429/bot 頁 / content 空或極短

3. prefer_browser=true → 直接從瀏覽器開始（保留原意志）

每層失敗都附 next_action：換源 / 升級瀏覽器 / 重試（Retry-After 尊重）
```

**為什麼 HTTP-first 而非 browser-first**（PaulPaul 決策，記憶已存）：
- 開箱即用：沒 Chrome 的機器（Linux 伺服器/Termux/CI）仍能用 HTTP 引擎
- 速度：HTTP ~1s vs 瀏覽器 5-10s
- 瀏覽器是**最終武器**，不是每次請求的代價

### search 鏈 —— 平行 + quorum + 共識（Hound 式）

```
1. DDG + Google（選配）+ Hound（選配）平行
2. quorum：至少 2 引擎貢獻才回（單一引擎偏見無法主導）
3. 跨引擎共識加權（多引擎都回同 URL → 加權）
4. 多樣性：頂部結果每域名≤2
5. 全滅 → DirectFetch →（最後手段）瀏覽器搜索
6. 每引擎斷路器 60s cooldown + Retry-After 尊重
```

### smart_browse 鏈 —— UnifiedBrowser-first（主力意志入口）

```
1. 直接 UnifiedBrowser（CDP 隱身）——保證 JS 渲染/SPA
   require_fresh=true → 強制 live fetch（跳過快取）
2. 瀏覽器失敗 → 回報 blocked + next_action（不降級 HTTP，
   因為呼叫者已明確要渲染——降級會假裝成功）
```

**分工：scrape = 快（HTTP-first），smart_browse = 保證渲染（UnifiedBrowser-first）。**
「主力用 UnifiedBrowser」的意志落在此 + browser_* 工具（interact/截圖/JS 執行）。

### 搜尋來源路由（保留）

| Type | Backend | Dependency | Rate Limit |
|------|---------|------------|------------|
| web | DDG（+ Google/Hound 平行） | ddgs 等 | None |
| code | GitHub public API | httpx | 60 req/hr |
| docs | MDN + Wikipedia API | httpx | None |
| news | HackerNews API | httpx | None |
| pkg | npm + PyPI + crates.io | httpx | None |
| academic | arXiv + PubMed | httpx | 10 req/s |

---

## 6. UnifiedBrowser — CORE（右腿，不變動的內建核心）

UnifiedBrowser 是 V2 的內建 CDP 原生瀏覽器引擎，位於 `browser/` 套件。**這是本設計的核心武器**——interact、渲染、硬網站突破都靠它。

```
UnifiedBrowser（browser/）
├── CDP Transport（cdp_driver.py）
│   └── Raw WebSocket → Chrome DevTools Protocol
│   └── No Playwright, no Selenium, no WebDriver
│   └── navigator.webdriver = undefined（天然隱身）
│   └── 補齊 interact 動作：fill / hover / select / wait_for / upload_file
│
├── Identity Engine（identity.py）
│   ├── Profile Factory — 合成真實 Chrome 指紋
│   ├── Profile Pool — 每站點身份隔離
│   └── Fingerprint Validation — bot.sannysoft.com 自動測試
│
├── Anti-detection（anti_detect.py）
│   ├── CDP leak patches（webdriver/plugins/languages/WebGL）
│   ├── Resource blocking（CDP 層級）
│   ├── Bot page detection（CF/reCAPTCHA/Akamai/DataDome）
│   └── 假內容偵測（跨引擎 hash 比對）
│
├── Behavioral Engine（behavior.py）
│   ├── Timing model — 高斯分布真實數據
│   ├── Mouse movement — Bezier 曲線 + 加速度
│   ├── Typing — 變速 + 2% 錯字率
│   └── Per-site behavior profiles（read/search/form/browse）
│
└── Session Pool（session_pool.py）
    ├── Browser 實例池（站點隔離，每站點獨立身份）
    ├── Lifecycle（idle 回收、age 重啟、失敗重啟）
    ├── Memory cap（400MB）
    └── Headful escalation（site 級：headless 被 CF 牆卡 → headful）
```

### 與 Hound stealth 對齊的生命週期（採用）

- **單一 warm 瀏覽器 + idle 回收**：session_pool 已有 idle recycle（60s），對齊 Hound 的 300s 閒置關閉（`HOUND_BROWSER_IDLE_TIMEOUT`）
- **`Memory.simulatePressureNotification`**（A9 採用）：每次 fetch 後觸發 Chrome GC（CDP 一行）

---

## 6.5 Headful 模式（SO 級 CF 突破）

### 對照測試實證（2026-08-14）

| 模式 | StackOverflow (CF 硬挑戰) | 結論 |
|---|---|---|
| headless Chrome + stealth | ❌ 卡「Just a moment」 | headless 本身被標記 |
| headless Edge + stealth | ❌ 卡 | 同上（與品牌無關） |
| **headful Chrome（連 stealth 都不用）** | ✅ 過 | **headful 是真解** |
| **headful Edge + stealth** | ✅ 過 | Windows 開箱即用（Edge 內建） |

**關鍵發現：CF 對 SO 的判定 = headless 模式本身，與瀏覽器品牌、stealth JS 都無關。**

### 設計（headful 只在必要時出動）

```
預設：headless（零彈窗，95% 網站夠用）
  ↓ 偵測 CF 硬挑戰（title="Just a moment" / "Performing security verification"）
自動升級：site 級 headful（session_pool.escalate_to_headful）
  - Windows：offscreen 隱藏（--window-position=-32000,-32000）→ 零干擾
  - Linux 伺服器：Xvfb 虛擬顯示（pyvirtualdisplay）
  - 實測：offscreen headful 仍能過 SO
```

### Headful 實作細節

- `cdp_driver.start(headless=False, headful_mode="offscreen|xvfb|visible")`
- **create_session fallback**：headful 剛啟動時 `Target.createTarget` 會失敗
  （「Failed to open new tab - no browser is open」）→ 自動 attach 到現有 page target
  （chrome://intro 首頁）再導覽。這是 headful 啟動競態的真實修復
- `unified_browser.navigate_headful()`：escalate → 重開 session → navigate
- `_detect_cf_challenge()`：標題/內容偵測 CF 牆
- `fetch()` 回報 `cf_challenge` + `headful` 旗標
- `scrape` / `smart_browse`：全鏈自動升級（HTTP → headless → **headful**），
  引擎鏈標示 `unified_browser(headful)`

### 能力矩陣（最終）

| 網站類型 | 引擎 | 延遲 |
|---|---|---|
| 一般網站 | HTTP 引擎（Hound→newspaper→…） | ~1s |
| SPA/JS 渲染 | headless UnifiedBrowser | 5-10s |
| **SO 級 CF 硬挑戰** | **headful 自動升級** | +5-10s |

---

## 7. Tools Specification

### 工具面（v1 藍本，形狀不變 + rename）

| Tool | 引擎 | 說明 |
|------|------|------|
| `search(query, max_results)` | search 鏈 | 平行 + quorum + 共識 |
| `scrape(url, prefer_browser, focus, require_fresh)` | scrape 鏈 | HTTP-first → 瀏覽器升級；BM25 focus |
| `status()` | — | 引擎可用性 + browser pool + 快取統計 |
| `deep_search(query, max_results, sources)` | github/npm/mdn | 異質來源平行 |
| `parallel_scrape(urls, …)` | scrape 鏈 | 並發（semaphore ≤5） |
| `smart_browse(url, max_age_months, require_fresh)` | **UnifiedBrowser-first** | SPA/JS 渲染專用，保證渲染 |
| `crawl(url, max_depth, max_pages, stay_domain)` | HTTP BFS | 單頁失敗升瀏覽器 |
| `map(url, max_pages)` | HTTP | sitemap + 內部連結樹 |
| `browser_navigate(url, wait_until, behavior)` | UnifiedBrowser | **v2_browser_* → browser_*** |
| `browser_get_content(format)` | UnifiedBrowser | text/html，50K 截斷 |
| `browser_screenshot(full_page)` | UnifiedBrowser | base64 PNG |
| `browser_evaluate(expression)` | UnifiedBrowser | JS 執行 |
| `browser_interact(action, selector, value, …)` | CDP 原生（→Playwright fallback） | click/fill/type/hover/select/scroll/press/wait_for/… |
| `browser_status()` | UnifiedBrowser | pool + identity 狀態 |

### 工具數：14（v1 15 − smart_scrape 併入 scrape + interact → browser_interact rename；smart_browse 保留 + v2_browser_* → browser_* rename）

> smart_scrape 併入 `scrape`（CDP 原生實作，零 Playwright 依賴）；`interact` 改 `browser_interact`（CDP 原生，Playwright 僅 upload/drag/frame fallback）；`smart_browse` 保留為 **UnifiedBrowser-first** 專用工具（主力意志入口）；`v2_browser_*` 改名 `browser_*`（誠實命名）。

### 每個回應的 actionable signals（Hound 式，A2 採用）

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

### Connect-time instructions（Hound 式，A1 採用）

MCP 握手時注入一次使用手冊（~0.8K tokens）：工具清單、#1 工作流程、已知限制。第一回合就上手，不重複每個 turn。

---

## 8. Anti-Crawl / 升級策略

### Tier 定義（翻轉後）

| Level | 技術 | 依賴 | 使用時機 |
|-------|------|------|----------|
| L0 | HTTP + 基本 headers | httpx | Public APIs, docs |
| L1 | UA 池 + 隨機延遲 + cookie jar | httpx | 一般網站 |
| L2 | curl_cffi TLS 指紋 | curl_cffi（選配） | 電商、新聞 |
| L3 | UnifiedBrowser（CDP 隱身） | 內建（需 Chrome/Edge） | SPA、被擋、JS shell |
| L4 | UnifiedBrowser + Memory GC | 內建 | 常態瀏覽器使用 |

> Playwright 不再作為升級目標（僅 upload/drag/frame fallback）。curl_cffi 為選配。

### 自動升級（上限 2 級）

```
HTTP (L0-L2) → 403/429/空內容/錯誤頁 → UnifiedBrowser（L3）
UnifiedBrowser → 失敗 → 回報 blocked + next_action（換源/重試）
```

### 電路斷路器（v1 既有，補 Retry-After）

- 每引擎獨立斷路器（v1 已有）
- **Retry-After 尊重**（Hound A4）：429 帶 Retry-After 就照等，不狂重試
- 每引擎 pacing 下限（DDG 1.2s 等，Hound A4）

---

## 9. Cache Layer（智慧快取，Hound A6）

```python
class SQLiteCache:
    def get(self, key: str) -> Optional[dict]
    def set(self, key: str, value: dict, ttl_seconds: int)
    def get_stats(self) -> dict
```

- **WAL mode**（並發讀寫）
- **壞內容永不快取**：錯誤頁、空內容、429/403、bot 頁
- **大小上限淘汰最舊**（防長壽 agent 快取無限成長）
- 快取鍵：URL + 提取類型 + focus；`require_fresh=true` 強制 live fetch
- 預設 TTL 1 小時（`cache_ttl`，Hound 預設）；`duration_ms: 0` = cache hit

---

## 10. Hound 擷取清單（已定案）

### 立即採用（A1-A8）

| # | 擷取 | 落地 |
|---|---|---|
| A1 | connect-time instructions | server 握手注入使用手冊 |
| A2 | actionable signals | content_ok / next_action / page_type / is_stale |
| A3 | 平行 + quorum + 共識 + 多樣性 | search 鏈重構 |
| A4 | 斷路器 + Retry-After + pacing | 補齊 v1 斷路器 |
| A5 | BM25 focus 提取 | scrape 加 focus= 參數 |
| A6 | 智慧快取 | WAL + 壞內容不快取 + 大小上限 |
| A7 | 內容適應提取 | page_type（article/list/js_shell） |
| A8 | 誠實 limits 文件 | README Gotchas / Honest limits 章節 |
| A9 | Memory.simulatePressureNotification | 每次 fetch 後觸發 Chrome GC（cdp_driver 一行） |

### 第二階段（架構預留，不實作）

| # | 擷取 | 狀態 |
|---|---|---|
| B1 | BYOK 搜尋（keys + env vars） | 介面預留 |
| B2 | 6 訊號排序（domain reputation） | 等 consensus 上線後疊 |
| B3 | self-healing CLI（--doctor） | 預留 |
| B5 | 單一 warm 瀏覽器 + idle 回收 | 對齊 300s idle |

### 明確不抄

- Neural rerank（ONNX 80MB）→ 委外
- PDF OCR → 委外 Hound adapter
- 10 引擎平行 → 保持輕量（2-3 引擎）

---

## 11. Requirements（開箱即用）

### Tier 0（requirements.txt）——安裝即有，零設定

```
mcp                  # MCP 協議
websockets           # CDP WebSocket（browser/ 唯一非 stdlib 硬依賴）
httpx                # HTTP 請求（L0-L1 + 搜索引擎）
duckduckgo_search    # DDG 搜索（Tier 0）
trafilatura          # 提取（Tier 0）
readability-lxml     # 提取（Tier 0）
lxml[html_clean]     # 提取依賴
justext              # 提取（Tier 0）
newspaper3k          # 提取（Tier 0）
```

### Tier 1（requirements-optional.txt）——選配，裝了自動啟用

```
master-fetch         # Hound（深度反爬/PDF OCR/neural ranking）
playwright           # 僅 upload/drag/frame fallback（瀏覽器需另裝）
curl_cffi            # L2 TLS 指紋
googlesearch-python  # 備用搜索
```

### 瀏覽器偵測（強化，Edge 加入）

```
1. 環境變數：CHROME_PATH / UNIFIED_BROWSER_PATH
2. Edge（Windows 保證內建）：Program Files (x86)/Microsoft/Edge/.../msedge.exe
3. Chrome：Program Files/Google/Chrome/.../chrome.exe + ms-playwright bundled
4. shutil.which：chrome / chromium / msedge ...
5. 全部失敗 → HTTP-only 模式（graceful degradation，Hound 式）
```

---

## 12. Error Handling

統一錯誤信封（v1 模式）：

```json
{ "ok": false, "error": "...", "error_type": "blocked|auth|timeout|not_found|internal",
  "retryable": true, "next_action": "switch_source|upgrade_browser|retry|give_up" }
```

- 電路斷路器每引擎獨立
- 自動升級上限 2 級（防 5 層超時串接）
- 429 尊重 Retry-After
- 硬阻擋（404/bot/auth）回乾淨錯誤，不假裝成功

---

## 13. Implementation Phases

> Phase 1-5 設計定案（先前 deleted 的重設計已由本文件取代）

```
Phase 1  補 cdp_driver interact 動作（fill/hover/select/wait_for/upload_file）
Phase 2  寫新版 unified-fetch-server.py（取代 v2-server.py）
         - Engine 抽象 + 註冊表 + 引擎鏈（HTTP-first + 升級）
         - browser_* 工具（rename + interact）
         - actionable signals + focus + page_type
         - connect-time instructions
Phase 3  智慧快取補齊（WAL + 壞內容不快取 + 大小上限）
Phase 4  擴充 test_v2_full_smoke.py（Layer D：新引擎鏈 + browser_* 工具）
Phase 5  更新 ARCHITECTURE.md（本文件）/ README（Gotchas + Honest limits）
Phase 6  fresh-venv 安裝冒煙 + mcp_servers.json 更新
```

---

## 14. Key Design Decisions（最終定案）

| # | 決策 | 理由 |
|---|------|------|
| D1 | **HTTP-first + 自動升級**（非 browser-first） | 開箱即用（PaulPaul 親自翻轉，記憶已存） |
| D2 | browser 是**最終武器**非主引擎 | 速度 + 依賴 + 全平台 |
| D3 | `prefer_browser=true` 保留強制瀏覽器 | 保留原意志 |
| D4 | interact = CDP 原生，Playwright 僅 fallback | no-Playwright 哲學 |
| D5 | search = 平行 + quorum + 共識 | Hound A3 |
| D6 | smart_scrape/smart_browse 併入 scrape | 工具面精簡 |
| D7 | v2_browser_* → browser_*（rename） | 誠實命名（browser 已是核心） |
| D8 | Hound = Tier 1 選配 | 你心中主用 browser > Hound，但 Hound 是 option |
| D9 | Tier 0 只有 mcp + websockets 硬依賴 | 開箱即用 |
| D10 | Edge 偵測加入 | Windows 開箱率接近 100% |

---

## 15. What V2 Does NOT Do

- PDF OCR（委外 Hound adapter）
- Neural ranking（委外 Hound adapter）
- Mass scraping / high-frequency access（out of scope）
- Paid proxy services（提供介面，使用者給 keys）
- 10 引擎平行搜索（保持輕量）

---

*Last updated: 2026-08-14*
*Author: PaulPaul + Claude Code*
*Status: DESIGN COMPLETE — implementation pending*
