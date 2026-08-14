# Changelog

## 2.0.0 (2026-08-14) — 瀏覽器優先引擎重設計（設計完成 + 實作落地）

### 架構翻轉
- **HTTP-first + 瀏覽器自動升級**（取代 v1 的「瀏覽器為外掛」）：scrape 先 HTTP (~1s)，被擋/JS shell/空內容才自動升級 UnifiedBrowser
- **search = 平行 + quorum + 共識 + 多樣性**（Hound 式）：DDG/Google/Hound 平行跑，跨引擎共識加權、每域名≤2
- **UnifiedBrowser 成為核心武器**：interact/渲染/硬網站突破全走 CDP 原生；smart_browse 為 UnifiedBrowser-first 專用工具
- Hound（master-fetch）降為 **Tier 1 選配引擎**（裝了自動啟用，不裝自動跳過）

### 新增
- `browser_interact`：CDP 原生 interact（click/fill/type/hover/select/scroll/press/wait_for/upload_file/cookies/dialog/evaluate/screenshot…）；drag + frame 才 fallback Playwright
- Actionable signals：`content_ok` / `page_type` / `next_action` / `engine_used` / `engine_chain` / `is_stale`
- BM25 focus 提取：`scrape(url, focus=...)` 只回相關段落
- 智慧快取：SQLite WAL、壞內容永不快取、大小上限自動淘汰、`require_fresh`
- Connect-time instructions（orientation doc）
- Edge 自動偵測（Windows 開箱即用）+ `UNIFIED_BROWSER_PATH`
- CDPSession：`fill` / `hover` / `select_option` / `wait_for_selector` / `upload_file` / `gc`（Memory.simulatePressureNotification）

### 工具面（15 → 14）
- `smart_scrape` 併入 `scrape`
- `interact` → `browser_interact`
- `v2_browser_*` → `browser_*`
- `smart_browse` 保留（UnifiedBrowser-first）

### 修正
- DDG HTML 解析支援 protocol-relative `//duckduckgo.com/l/?uddg=` 連結（搜尋 fallback 修復）
- `UnifiedBrowser.navigate` session 建立失敗重試一次（Chrome 重啟競態修復）
- smart_browse cache 儲存使用 `FetchResult` 屬性修正

### 測試
- `test_v2_full_smoke.py` 全面重寫：Layer A-F（browser 模組 + 整合 + 14 工具面 + HTTP-first 鏈 + CDP interact + 快取 + 平行搜索）
- **111/111 測試通過** + fresh-venv 最小安裝冒煙（僅 mcp+websockets+httpx 即可搜尋/抓取）

---

## 1.x（v1 藍本，歷史）
- unified-fetch v1：4 搜尋引擎 + 6 提取引擎 + 順序 fallback + Playwright interact（README 見 `../unified-fetch/`）

## 2.0.1 (2026-08-14) — P0 指紋驗證修復（誠實評估發現的假 100%）

### 發現（檢查未完成工作時實測挖出）
- **STEALTH_JS 整個失效**：`const original GOPD` 多餘空格 → SyntaxError，整個隱身腳本從未執行。之前
  所有「webdriver=false / 指紋通過」都是 headless Chrome 的天然狀態，不是補丁的功勞
- **指紋解析器漏測硬指標**：`class="result passed"` 固定順序 regex 漏掉 WebGL Vendor/Renderer、
  Broken Image Dimensions → 假 100%（只測 8 個易過項）
- **webdriver 補丁自爆**：把 webdriver 定義成 navigator 自有可列舉屬性 → sannysoft「WebDriver (New)」
  檢查偵測 `hasOwnProperty` → 反而被判為 present（檢測訊號）

### 修復
- anti_detect.py: 修 STEALTH_JS 語法錯誤（original GOPD → originalGOPD）
- anti_detect.py: webdriver 改為只補丁 Navigator.prototype（不設自有屬性），保留 getOwnPropertyDescriptor 隱藏
- anti_detect.py: WebGL getParameter 補丁涵蓋 GL_VENDOR(0x1F00)/GL_RENDERER(0x1F01)（sannysoft 讀的就是這個）
- cdp_driver.py: `--use-gl=swiftshader-webgl`（Chrome 132+ 已棄用）→ `--enable-unsafe-swiftshader` + `--use-angle=swiftshader`
- cdp_driver.py: 移除 `--disable-software-rasterizer`（殺掉軟體 WebGL）
- fingerprint_verify.py: 解析器改為不限屬性順序抓全部 passed/failed cell（11 項）
- fingerprint_verify.py: test() 合併 production DEFAULT_CHROME_ARGS（驗證必須測真實生產設定）

### 結果
- bot.sannysoft.com **176/176 全過（11 項 × 16 profiles）**——這次是真 100%
- 驗證循環現在是真實測量：parser 修正前假 100% → 82%（真）→ 91%（SwiftShader 修正）→ 100%（STEALTH_JS 修復）

## 2.0.2 (2026-08-14) — P0 Cloudflare 實測 + 指紋指標擴充

### P0 Cloudflare 實測（真實反爬站點）
| 站點 | 保護 | 結果 |
|---|---|---|
| **nowsecure.nl** | CF Turnstile 挑戰 | ✅ 通過——get 到 `cf_clearance`，顯示「NOWSECURE by nodriver」成功標記 |
| **medium.com** | CF 檢查 | ✅ 通過——渲染出完整 Medium 內容（title + 導覽 + 內容） |
| **stackoverflow.com** | CF 硬挑戰 | ❌ 卡在「Just a moment」挑戰頁（見下） |

**誠實評估**：
- nowsecure.nl（最難的 Turnstile）**自動通過**——無需人工點擊，STEALTH_JS + SwiftShader WebGL 生效
- **SO 的 CF 挑戰卡住**：無 iframe、無 checkbox、純「Performing security verification」JS 挑戰
  不自動完成。連無補丁的天然 headless 也卡——判斷是 **Chrome 148 headless 被 CF 側標記**
  （Hound 實作用 system Chrome `channel=chrome` + Playwright stealth 才能過 SO 級別）。
  `next_action` 已回報 switch_source，誠實不假裝成功

### 指紋指標擴充（Canvas/Audio → 31 項）
- 新增 **fpscanner 表（20 項）**解析：PHANTOM_*/HEADCHR_*/CHR_*/SELENIUM_*/VIDEO_CODECS/SEQUENTUM
- 修 **HeadlessChrome UA 洩漏**：`--user-agent` override 進 DEFAULT_CHROME_ARGS（原 UA 含 HeadlessChrome）
- 修 **`--disable-blink-features=AutomationControlled` 缺失**：加入 production args
- 結果：**496/496 全過（31 項 × 16 profiles）**——涵蓋 UA/WebGL/plugins/permissions/Canvas/phantom 系列

### 驗證
- 111/111 回歸測試通過（fingerprint 修復無破壞）
- STEALTH_JS node 語法檢查通過

## 2.0.3 (2026-08-14) — SO 級突破：headful 自動升級

### 突破（對照測試實證）
| 模式 | StackOverflow (CF 硬挑戰) | 結果 |
|---|---|---|
| headless Chrome + stealth | ❌ 卡「Just a moment」 | 無 cf_clearance |
| headless Edge + stealth | ❌ 卡 | 無 |
| **headful Chrome（無 stealth）** | ✅ **過** | ✅ |
| **headful Edge + stealth** | ✅ **過** | ✅ |

**結論：CF 對 SO 的判定 = headless 模式本身，與品牌/stealth JS 無關。** 真實視窗渲染即過。
headful 無需任何 stealth 補丁（補丁仍保留以防其他偵測）。

### 實作（headful 只在必要時出動）
- `cdp_driver`：`headless=False` → headful；`headful_mode`（offscreen/xvfb/visible）
  - offscreen（Windows 預設）：`--window-position=-32000,-32000` 隱藏視窗，不干擾
  - xvfb：Linux 伺服器虛擬顯示
- `create_session` fallback：headful 剛啟動時 `Target.createTarget` 會失敗
  （「Failed to open new tab」）→ 自動 attach 到現有 page target 再導覽
- `unified_browser`：
  - `_detect_cf_challenge`（「Just a moment」/「Performing security verification」）
  - `navigate_headful()`：site 級升級（session_pool `escalate_to_headful`）
  - `fetch()` 回報 `cf_challenge` + `headful` 旗標
- `server`：
  - `scrape`：HTTP 全滅 → headless browser → **偵測 CF 牆自動升 headful** → 成功
  - `smart_browse`：同樣自動升級
  - 引擎鏈標示 `unified_browser(headful)`

### 驗證
- **scrape SO 全鏈成功**：HTTP 6 引擎失敗 → headless 挑戰 → headful 通過（35KB 完整內容）
- smart_browse SO 自動升級通過
- **111/111 回歸測試通過**

### 伺服器注意
- Windows：offscreen 隱藏（零彈窗）
- Linux 無顯示：需 `Xvfb`（`pyvirtualdisplay`）——headful 在無 X 環境需虛擬顯示
