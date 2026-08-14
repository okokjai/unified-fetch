# 🌐 unified-fetch V2

**Browser-core MCP server. HTTP-first, auto-upgrades to a CDP-native stealth browser. Zero config.**

```
search · scrape · deep_search · crawl · map · smart_browse
browser_navigate · browser_get_content · browser_screenshot
browser_evaluate · browser_interact · browser_status · status
```

基于 v1（unified-fetch）藍本 + v2 UnifiedBrowser（Raw CDP 隱身瀏覽器）核心。
GitHub clone 後 `pip install -r requirements.txt` 即可用——**不用 API key、不用 config 檔、不用裝其他引擎**。

---

## ✨ 特色

| 能力 | 說明 |
|---|---|
| ⚡ **HTTP-first + 自動升級** | scrape 先走 HTTP（~1s）；被擋/JS shell/空內容才自動升級到隱身瀏覽器 |
| 🧬 **UnifiedBrowser 核心** | Raw CDP（無 Playwright/Selenium/WebDriver），身份隔離 + 人類行為 + 反偵測 |
| 🔍 **平行搜索 + 共識** | DDG/Google/Hound 平行跑，跨引擎共識加權、每域名≤2、quorum 報告 |
| 🎯 **Actionable signals** | 每個回應帶 `content_ok` / `page_type` / `next_action` / `engine_used` |
| ✂️ **BM25 focus** | `scrape(url, focus="要找什麼")` 只回相關段落，省 80%+ context |
| 🖱️ **CDP 原生 interact** | click/fill/type/hover/select/scroll/press/wait_for/upload… 不碰 Playwright |
| 💾 **智慧快取** | SQLite WAL、壞內容永不快取、大小上限自動淘汰、`require_fresh` 強制重取 |
| 🚀 **秒啟** | 瀏覽器懶初始化：server 啟動 <1s，首次用到瀏覽器才開 Chrome |
| 📦 **零設定** | 所有引擎 try-import 自動偵測；沒 Chrome 就 HTTP-only，不會壞 |

---

## 🚀 安裝

```bash
git clone <your-repo>/unified-fetch-v2
cd unified-fetch-v2
pip install -r requirements.txt        # Tier 0：立即可用
```

**瀏覽器**（自動偵測，選配）：Chrome / Edge（Windows 內建）/ Playwright chromium。

**選配引擎**（裝了自動啟用，不裝自動跳過）：

```bash
pip install -r requirements-optional.txt   # Hound 深度反爬 + Playwright fallback + googlesearch + curl_cffi
playwright install chromium                # 僅當你沒有 Chrome/Edge
```

| Tier | 內容 | 依賴 |
|---|---|---|
| 0 | HTTP 引擎 + 搜索 + 瀏覽器核心（CDP） | `mcp` `websockets` `httpx` `duckduckgo_search` `trafilatura` `readability-lxml` `justext` `newspaper3k` `lxml` |
| 1（選配） | Hound（master-fetch）、Playwright、curl_cffi、googlesearch | `requirements-optional.txt` |

### MCP 設定（Claude Code / Cursor 等）

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

## ✅ 驗證結果（2026-08-14）

| 驗證項目 | 結果 | 證據 |
|---|---|---|
| 功能測試 | **112/112 assertions（19 測試函數）** | Layer A-D（模組+整合+工具面+引擎鏈+interact+快取+平行搜索） |
| 指紋驗證 | **496/496 項（31 組 × 16 profiles）** | bot.sannysoft.com + fpscanner，真 100% |
| Cloudflare 實測 | nowsecure ✅ Medium ✅ SO ✅（headful 自動升級） | 真實反爬站點：headless → CF 牆 → headful 過關 |
| MCP 握手 | initialize + 14 tools + scrape ✅ | stdio 協議真實測試 |
| fresh-venv 安裝 | 最小依賴（mcp+websockets+httpx）可搜尋/抓取 | 開箱即用驗證 |

---

## 🔧 工具（14 個）

| 工具 | 說明 |
|---|---|
| `search(query, max_results)` | 平行搜索 + 跨引擎共識 + 多樣性 |
| `scrape(url, prefer_browser, focus, require_fresh)` | HTTP-first → 瀏覽器升級；`focus` 回相關段落 |
| `status()` | 引擎可用性 + 瀏覽器池 + 快取 + 使用手冊 |
| `deep_search(query, sources=[github,npm,mdn])` | 技術來源平行搜索（無 key） |
| `parallel_scrape(urls, ...)` | 並發抓取（≤5） |
| `crawl(url, max_depth, max_pages)` | BFS 爬蟲，單頁失敗自動升瀏覽器 |
| `map(url)` | 站點結構（sitemap + 內部連結） |
| `smart_browse(url)` | **UnifiedBrowser-first**：SPA/JS 渲染保證 |
| `browser_navigate(url, wait_until, behavior)` | 隱身瀏覽器導覽 |
| `browser_get_content(format)` | 取頁面文字/HTML |
| `browser_screenshot(full_page)` | 截圖（base64 PNG） |
| `browser_evaluate(expression)` | 執行 JS |
| `browser_interact(action, ...)` | CDP 原生互動（drag/frame 才 fallback Playwright） |
| `browser_status()` | 瀏覽器池 + 身份引擎狀態 |

### 回應訊號（agent 友善）

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

- `content_ok` — 內容真的可用（非錯誤頁/空頁）
- `page_type` — `article` / `list` / `js_shell` / `pdf` / `error`
- `next_action` — 失敗時告訴 agent 下一步（`switch_source` / `upgrade_browser` / `retry` / `give_up`）
- `engine_chain` — 走了哪些引擎（驗證與除錯）

---

## 💡 使用模式

### #1 研究流程

```
search("python httpx async") → 挑 URL → scrape(url, focus="timeout retry") → 合成
```

### 引擎鏈（HTTP-first + 自動升級）

```
scrape:
  1. Hound（若有）→ newspaper → trafilatura → readability → justext → direct   (~1s)
  2. 被擋 / JS shell / 全滅 → UnifiedBrowser（CDP 隱身）                          (5-10s)
  3. CF 互動挑戰（如 StackOverflow）→ UnifiedBrowser headful（真實視窗）           (+5-10s)
  4. prefer_browser=true → 直接從瀏覽器開始

search:
  DDG + Google + Hound 平行 → 共識加權 → 每域名≤2 → 全滅 → direct → 瀏覽器搜索
```

### 需要 JS 渲染的頁面

```
smart_browse("https://spa-example.com")      # 保證渲染，UnifiedBrowser-first
```

### Headful 模式（CF 硬挑戰自動升級）

| 模式 | StackOverflow (CF 硬挑戰) | 說明 |
|---|---|---|
| headless Chrome + stealth | ❌ 卡「Just a moment」 | headless 本身被 CF 側標記 |
| headless Edge + stealth | ❌ 卡 | 同上（與品牌無關） |
| **headful Chrome（無 stealth）** | ✅ **過** | **headful 是真解** |
| **headful Edge + stealth** | ✅ **過** | Windows 開箱即用（Edge 內建） |

**關鍵發現：CF 對 SO 的判定 = headless 模式本身，與瀏覽器品牌、stealth JS 都無關。**
Headful 是 scrape / smart_browse 的自動升級策略（非預設）：HTTP 全滅 → headless 被 CF 牆卡 → 自動升 headful。

| 層級 | 行為 |
|---|---|
| **預設：headless** | 零彈窗，95% 網站夠用 |
| **偵測到 CF 硬挑戰** | 自動升級 site 級 headful（session_pool.escalate_to_headful） |
| **Windows** | offscreen 隱藏（`--window-position=-32000,-32000`）→ 零干擾 |
| **Linux 伺服器** | 需 Xvfb（pyvirtualdisplay）——headful 在無 X 環境需虛擬顯示 |

---

## 🧪 測試

```bash
python test_v2_full_smoke.py     # 112 assertions（19 測試函數）
```

---

## ⚠️ 已知限制（Honest Limits）

### 已驗證的邊界

| 限制 | 行為 |
|---|---|
| DataDome / Akamai / 互動式 Turnstile | 不一定能過。`next_action` 會叫你換源 |
| 登入牆 | 不繞過（interact 不處理 authenticated sessions） |
| 深層 Shadow-DOM | 部分可達（scroll/click/wait_for），未完整打通 |
| 無瀏覽器的機器 | HTTP 引擎仍完整可用（graceful degradation） |
| PDF OCR / neural ranking | 不內建——裝 Hound（Tier 1）即取得 |

### 尚未實作的已知缺口

| 項目 | 狀態 |
|---|---|
| P2 行為數據存儲（真實 timing 收集） | 未做——`behavior.py` 仍是手工 Gaussian 參數 |
| Linux 伺服器 headful | 需 Xvfb（Windows offscreen 已驗證；Linux 留介面） |

---

## 🚫 Gotchas

- **`pip install` 不裝瀏覽器二進位**。`playwright install chromium` 只在沒有 Chrome/Edge 時需要。`status()` 會告訴你有沒有瀏覽器
- **快取預設 1 小時**。要即時內容傳 `require_fresh=true`；快取命中 `duration_ms: 0`
- **搜索是 HTTP**。瀏覽器不參與搜索（Hound 設計同款：search 100% HTTP）
- **`robots.txt` 預設不檢查**。這是研究工具不是批量抓取器
- **不要做 mass scraping**。頻繁高量存取會被反爬封鎖——這是 agent 研究工具，不是 Scrapy

---

## 📦 專案結構

```
unified-fetch-v2/
├── unified-fetch-server.py       # MCP Server 入口（2031 行，14 工具）
├── ARCHITECTURE.md               # 562 行，Status: IMPLEMENTED
├── README.md                     # 本文件
├── CHANGELOG.md                  # 版本紀錄
├── requirements.txt              # Tier 0 依賴
├── requirements-optional.txt     # Tier 1 選配依賴
├── test_v2_full_smoke.py         # 112 assertions（19 測試函數）
└── browser/                      # CORE：UnifiedBrowser 套件（5489 行）
    ├── cdp_driver.py             # Raw CDP + CDPSession（補齊 interact 動作）
    ├── unified_browser.py        # 整合入口（navigate/get_text/screenshot/…）
    ├── identity.py               # Identity Engine（profile 合成 + 站點路由）
    ├── anti_detect.py            # Anti-detection + BotPageDetector
    ├── behavior.py               # Behavioral Engine（人類行為）
    ├── session_pool.py           # Session Pool（站點隔離 + 生命週期 + headful 升級）
    └── fingerprint_verify.py     # 指紋驗證
```

> 共 10 個 .py 檔案，8703 行總計（2031 server + 5489 browser/ + 497 test + 562 ARCHITECTURE.md + 124 CHANGELOG.md）。v2.0.0 全數完成：HTTP-first + CDP 隱身核心 + headful CF 突破 + 指紋 496/496。

---

## 🔗 相關

- 完整架構：[ARCHITECTURE.md](ARCHITECTURE.md)
- 版本紀錄：[CHANGELOG.md](CHANGELOG.md)
- v1 藍本：`../unified-fetch/`
- Hound（master-fetch，選配引擎）：https://github.com/dondai1234/master-fetch

---

*Last updated: 2026-08-14*
*Author: PaulPaul + Claude Code*
*Status: v2.0.0 — 112/112 assertions + 496/496 fingerprint + SO headful breakthrough + CF real-world verified*
