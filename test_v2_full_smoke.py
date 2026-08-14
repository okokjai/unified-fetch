#!/usr/bin/env python3
"""
Full smoke test for unified-fetch V2 — new architecture.

Design under test (ARCHITECTURE.md):
  - CORE: browser/ 7 modules (cdp_driver, identity, anti_detect, behavior,
    session_pool, fingerprint_verify, unified_browser)
  - Server: unified-fetch-server.py (14 tools, HTTP-first + browser upgrade,
    parallel search + consensus, actionable signals, smart cache)

Layers:
  A: browser/ module tests (no Chrome needed)
  B: UnifiedBrowser integration (needs Chrome/Edge)
  C: MCP server tool definitions (new 14-tool surface)
  D: new-architecture behavior (HTTP-first chain, prefer_browser, cache,
     parallel search, CDP interact, smart_browse)
"""

import asyncio
import os
import pathlib
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR / "browser"))

_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

_passed = 0
_failed = 0


def log_step(msg):
    print(f"\n{_BOLD}{_YELLOW}>> {msg}{_RESET}")


def log_info(msg):
    print(f"  {_DIM}{msg}{_RESET}")


def check(cond, pass_msg, fail_msg):
    global _passed, _failed
    if cond:
        print(f"  {_GREEN}PASS{_RESET} {pass_msg}")
        _passed += 1
    else:
        print(f"  {_RED}FAIL{_RESET} {fail_msg}")
        _failed += 1


def step(title):
    print(f"\n{_BOLD}{_YELLOW}>> {title}{_RESET}")


def load_server():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "uf_v2", SCRIPT_DIR / "unified-fetch-server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ═══════════════════════════════════════════════════════════════════
# Layer A: browser/ module tests (no Chrome needed)
# ═══════════════════════════════════════════════════════════════════

async def test_identity():
    step("Layer A1: identity.py — Factory, Store, Site routing, Persistence")
    from identity import IdentityManager

    tmpdir = tempfile.mkdtemp(prefix="test_id_")
    mgr = IdentityManager(tmpdir)
    profiles = mgr.factory.synthesize(count=5)
    check(len(profiles) == 5, "Factory: 5 profiles", f"Got {len(profiles)}")
    for p in profiles:
        check(p.name.startswith("synthetic_"), f"Profile name: {p.name}", "")
        check(len(p.user_agent) > 50, f"UA length: {len(p.user_agent)}", "")
        check(len(p.fingerprint_id) == 16, "Fingerprint ID 16 chars", "")
    for p in profiles[:3]:
        mgr.add_profile(p)
    prof1, new1 = mgr.get_profile_for_site("example.com")
    check(prof1 is not None and new1, "Site routing new site", "")
    prof2, new2 = mgr.get_profile_for_site("example.com")
    check(prof1.name == prof2.name and not new2, "Same site reuses profile", "")
    mgr.close()
    mgr2 = IdentityManager(tmpdir)
    s2 = mgr2.get_stats()
    check(s2["total_profiles"] == 3, f"Persisted: {s2['total_profiles']}", "")
    mgr2.close()
    shutil.rmtree(tmpdir, ignore_errors=True)


async def test_session_pool():
    step("Layer A2: session_pool.py — Instantiation, Status")
    from session_pool import SessionPool, PoolConfig
    pool = SessionPool(PoolConfig(min_instances=0, max_instances=3))
    status = await pool.status()
    check(status["total_instances"] == 0, "Empty pool", "")
    check(status["max_instances"] == 3, "max_instances=3", "")
    await pool.close()


async def test_cdp_driver():
    step("Layer A3: cdp_driver.py — Browser detection (Chrome/Edge)")
    from cdp_driver import CDPTransport
    t = CDPTransport()
    path = t.find_chrome()
    check(path != "", f"Browser found: {path}", "Browser NOT found")
    check(os.path.isfile(path) or os.path.isdir(path), "Path valid", "")
    # New CDP interact methods exist (Phase 1)
    from cdp_driver import CDPSession
    for m in ["fill", "hover", "focus_selector", "select_option",
              "wait_for_selector", "upload_file", "gc"]:
        check(hasattr(CDPSession, m), f"CDPSession.{m} exists", f"MISSING {m}")


async def test_anti_detect():
    step("Layer A4: anti_detect.py — STEALTH_JS, BotPageDetector")
    from anti_detect import AntiDetect, BotPageDetector, STEALTH_JS
    check(len(STEALTH_JS) > 100, f"STEALTH_JS {len(STEALTH_JS)} chars", "")
    det = BotPageDetector()
    r = det.detect("https://example.com/", "Example Domain")
    check(r.get("is_bot_page") is False, "example.com NOT bot page", "")


async def test_behavior():
    step("Layer A5: behavior.py — MousePathGenerator")
    from behavior import MousePathGenerator
    mp = MousePathGenerator(100, 100, 300, 200)
    pts = mp.generate_path()
    check(len(pts) > 0, f"Mouse points: {len(pts)}", "Empty")
    check(abs(pts[0]["x"] - 100) < 30, "Start near (100,100)", "")
    last = pts[-1]
    check(abs(last["x"] - 300) < 30, "End near (300,200)", "")


async def test_fingerprint_verify():
    step("Layer A6: fingerprint_verify.py — Module loads")
    try:
        from fingerprint_verify import FingerprintVerifier
        check(True, "FingerprintVerifier imported", "")
    except ImportError as e:
        check(False, "", f"Import failed: {e}")


# ═══════════════════════════════════════════════════════════════════
# Layer B: UnifiedBrowser integration (needs Chrome)
# ═══════════════════════════════════════════════════════════════════

async def test_browser_lifecycle():
    step("Layer B1: UnifiedBrowser — init → close → reinit")
    from unified_browser import UnifiedBrowser, UnifiedBrowserConfig
    tmpdir = tempfile.mkdtemp(prefix="ub_life_")
    cfg = UnifiedBrowserConfig(data_dir=tmpdir, identity_count=2,
                               min_instances=1, max_instances=2, human_behavior=False)
    ub = UnifiedBrowser(cfg)
    r1 = await ub.initialize()
    check(r1["ok"], "Init #1 OK", f"{r1}")
    check(r1["identity_profiles"] >= 1, "Profiles", "")
    await ub.close()
    ub2 = UnifiedBrowser(cfg)
    r2 = await ub2.initialize()
    check(r2["ok"], "Init #2 OK (reinit)", f"{r2}")
    await ub2.close()
    shutil.rmtree(tmpdir, ignore_errors=True)


async def test_navigate():
    step("Layer B2: navigate — wait_until + behaviors")
    from unified_browser import UnifiedBrowser, UnifiedBrowserConfig
    tmpdir = tempfile.mkdtemp(prefix="ub_nav_")
    cfg = UnifiedBrowserConfig(data_dir=tmpdir, identity_count=1,
                               min_instances=1, max_instances=2, human_behavior=False)
    ub = UnifiedBrowser(cfg)
    await ub.initialize()
    for w in ["load", "domcontentloaded", "networkidle"]:
        r = await ub.navigate("https://example.com/", wait_until=w)
        check(r["ok"], f"wait_until='{w}' OK", f"failed: {r.get('error')}")
        check(r.get("title") != "", "title", "")
    cfg2 = UnifiedBrowserConfig(data_dir=tmpdir, identity_count=1,
                                min_instances=1, max_instances=2, human_behavior=True)
    ub2 = UnifiedBrowser(cfg2)
    await ub2.initialize()
    for b in ["read", "search", "form", "browse"]:
        r = await ub2.navigate("https://example.com/", behavior=b)
        check(r["ok"], f"behavior='{b}' OK", f"failed: {r.get('error')}")
    await ub.close()
    await ub2.close()
    shutil.rmtree(tmpdir, ignore_errors=True)


async def test_content_and_js():
    step("Layer B3: get_text + get_html + evaluate")
    from unified_browser import UnifiedBrowser, UnifiedBrowserConfig
    tmpdir = tempfile.mkdtemp(prefix="ub_content_")
    cfg = UnifiedBrowserConfig(data_dir=tmpdir, identity_count=1,
                               min_instances=1, max_instances=1, human_behavior=False)
    ub = UnifiedBrowser(cfg)
    await ub.initialize()
    await ub.navigate("https://example.com/", wait_until="load")
    text = await ub.get_text()
    check(len(text) > 50, f"Text {len(text)} chars", "Empty")
    html = await ub.get_html()
    check(len(html) > 100 and "<html" in html.lower(), "HTML full doc", "")
    r1 = await ub.evaluate("document.title")
    check(r1 == "Example Domain", f"Title: {r1!r}", "")
    await ub.close()
    shutil.rmtree(tmpdir, ignore_errors=True)


async def test_screenshot_isolation():
    step("Layer B4: screenshot + session isolation")
    from unified_browser import UnifiedBrowser, UnifiedBrowserConfig
    tmpdir = tempfile.mkdtemp(prefix="ub_shot_")
    cfg = UnifiedBrowserConfig(data_dir=tmpdir, identity_count=3,
                               min_instances=1, max_instances=3, human_behavior=False)
    ub = UnifiedBrowser(cfg)
    await ub.initialize()
    await ub.navigate("https://example.com/", wait_until="load")
    png = await ub.screenshot()
    check(len(png) > 5000 and png[:4] == b"\x89PNG", f"Screenshot {len(png)}B PNG", "")
    await ub.evaluate("document.cookie = 'ub_test_a=val_a; path=/'")
    await ub.navigate("https://example.net/", wait_until="load")
    c1 = await ub.evaluate("document.cookie") or ""
    check("ub_test_a" not in c1, "Cookies isolated between sites (example.com→example.net)", f"LEAK {c1[:100]}")
    await ub.close()
    shutil.rmtree(tmpdir, ignore_errors=True)


async def test_interact_methods():
    step("Layer B5: UnifiedBrowser interact pass-throughs (Phase 1)")
    from unified_browser import UnifiedBrowser, UnifiedBrowserConfig
    tmpdir = tempfile.mkdtemp(prefix="ub_inter_")
    cfg = UnifiedBrowserConfig(data_dir=tmpdir, identity_count=1,
                               min_instances=1, max_instances=1, human_behavior=False)
    ub = UnifiedBrowser(cfg)
    await ub.initialize()
    # Write a self-contained form to a local HTML file (no external dependency)
    form_html = os.path.join(tmpdir, "form.html")
    with open(form_html, "w", encoding="utf-8") as f:
        f.write("""<html><body>
<form method="post" action="/post">
<p><label>Customer name: <input name="custname"></label></p>
<p><label>Telephone: <input type="tel" name="custtel"></label></p>
<p><label>E-mail address: <input type="email" name="custemail"></label></p>
<fieldset><legend>Pizza Size</legend>
<p><label><input type="radio" name="size" value="small"> Small</label></p>
<p><label><input type="radio" name="size" value="medium"> Medium</label></p>
<p><label><input type="radio" name="size" value="large"> Large</label></p>
</fieldset>
<p><label>Toppings: <select name="size">
<option value="small">Small</option>
<option value="medium">Medium</option>
<option value="large">Large</option>
</select></label></p>
<p><button>Submit order</button></p>
</form></body></html>""")
    form_url = pathlib.Path(form_html).as_uri()
    await ub.navigate(form_url, wait_until="load")
    ok = await ub.fill("input[name=custname]", "Paul")
    check(ok, "fill() OK", "")
    val = await ub.evaluate("document.querySelector('input[name=custname]').value")
    check(val == "Paul", f"fill value: {val!r}", "")
    ok = await ub.hover("input[name=custname]")
    check(ok, "hover() OK", "")
    ok = await ub.select("select[name=size]", value="medium")
    check(ok, "select(value='medium') OK", "select failed")
    ok = await ub.wait_for("input[name=custname]", timeout=5)
    check(ok, "wait_for() OK", "")
    await ub.close()
    shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
# Layer C: MCP server — new 14-tool surface
# ═══════════════════════════════════════════════════════════════════

def test_mcp_tools():
    step("Layer C: MCP server — new tool surface")
    mod = load_server()
    tools = mod._build_tools()
    names = [t.name for t in tools]
    check(len(tools) == 14, f"14 tools ({len(tools)})", f"got {len(tools)}")
    for t in ["search", "scrape", "status", "deep_search", "parallel_scrape",
              "crawl", "map", "smart_browse",
              "browser_navigate", "browser_get_content", "browser_screenshot",
              "browser_evaluate", "browser_interact", "browser_status"]:
        check(t in names, f"tool '{t}'", f"MISSING {t}")
    stale = [n for n in names if n in ("smart_scrape", "interact") or n.startswith("v2_browser_")]
    check(not stale, "no stale tool names (smart_scrape/interact/v2_browser_*)",
          f"STALE: {stale}")
    # browser_interact action enum
    for tool in tools:
        if tool.name == "browser_interact":
            schema = getattr(tool, "input_schema", getattr(tool, "inputSchema", {}))
            enum = schema["properties"]["action"]["enum"]
            check("fill" in enum and "drag" in enum and "wait_for" in enum,
                  f"browser_interact actions ({len(enum)})", f"{enum}")


# ═══════════════════════════════════════════════════════════════════
# Layer D: new-architecture behavior
# ═══════════════════════════════════════════════════════════════════

async def test_http_first_chain():
    step("Layer D1: scrape — HTTP-first chain (not browser)")
    mod = load_server()
    u = mod.Unified()
    r = await u.scrape("https://example.com/", require_fresh=True)
    check(r["ok"], "scrape example.com OK", f"{r.get('error')}")
    check(r["engine_used"] in ("newspaper", "trafilatura", "readability",
                               "justext", "direct", "hound"),
          f"engine from HTTP chain: {r['engine_used']}",
          f"browser unexpectedly first: {r['engine_used']}")
    check(r["content_ok"], "content_ok True", "")
    check(r["page_type"] in ("article", "list"), f"page_type: {r['page_type']}", "")
    check("engine_chain" in r and len(r["engine_chain"]) >= 1, "engine_chain present", "")
    check("next_action" in r, "next_action present", "")
    check("focus_applied" in r, "focus_applied present", "")


async def test_prefer_browser():
    step("Layer D2: scrape — prefer_browser=True → UnifiedBrowser")
    mod = load_server()
    u = mod.Unified()
    r = await u.scrape("https://example.com/", prefer_browser=True, require_fresh=True)
    check(r["ok"], "prefer_browser scrape OK", f"{r.get('error')}")
    check(r["engine_used"] == "unified_browser",
          f"engine = unified_browser ({r['engine_used']})", "")
    check(r["engine_chain"] == ["unified_browser"], "chain = [unified_browser]", "")


async def test_cache():
    step("Layer D3: smart cache — hit + require_fresh bypass + bad never cached")
    mod = load_server()
    u = mod.Unified()
    # cold scrape → miss
    r1 = await u.scrape("https://example.com/", require_fresh=True)
    check(r1["cache_hit"] is False, "first call: cache_hit=False", "")
    # warm scrape → hit (duration 0)
    r2 = await u.scrape("https://example.com/")
    check(r2.get("cache_hit") is True, "second call: cache_hit=True", f"{r2.get('cache_hit')}")
    check(r2.get("duration_ms", -1) == 0, "cache hit duration_ms=0", "")
    # require_fresh bypasses cache
    r3 = await u.scrape("https://example.com/", require_fresh=True)
    check(r3.get("cache_hit") is False, "require_fresh bypasses cache", "")
    # bad content never cached: 404 URL → cache key absent
    bad = await u.scrape("https://example.com/404-not-a-real-page", require_fresh=True)
    if not bad["ok"]:
        cached = u._cache.get(mod.SQLiteCache.key("scrape",
                                                  "https://example.com/404-not-a-real-page",
                                                  "text"))
        check(cached is None, "error page NOT cached", f"cached: {cached is not None}")


async def test_parallel_search():
    step("Layer D4: search — parallel + quorum + consensus structure")
    mod = load_server()
    u = mod.Unified()
    r = await u.search("python async http", max_results=5)
    check(r["ok"], f"search OK ({r['total']} results)", f"{r.get('error')}")
    check(isinstance(r["results"], list), "results list", "")
    check("quorum" in r and "contributors" in r["quorum"], "quorum report present", "")
    check("engines_consensus" in r, "engines_consensus present", "")
    for res in r["results"]:
        check("consensus" in res and res["url"].startswith("http"),
              f"result has consensus + url ({res['url'][:40]})", "")
    # diversity: max 2 per domain in top results
    from urllib.parse import urlparse
    doms = {}
    for res in r["results"]:
        d = urlparse(res["url"]).netloc
        doms[d] = doms.get(d, 0) + 1
    check(max(doms.values()) <= 2, f"diversity <=2/domain: {doms}", f"{doms}")


async def test_cdp_interact():
    step("Layer D5: browser_interact — CDP-native end-to-end")
    mod = load_server()
    # Use local file form (same as Layer B5 — httpbin.org is unreliable)
    import tempfile
    import os
    _tmpdir = tempfile.mkdtemp(prefix="ub_d5_")
    _form_path = os.path.join(_tmpdir, "form.html")
    with open(_form_path, "w", encoding="utf-8") as f:
        f.write("""<html><body>
<form method="post" action="/post">
<p><label>Customer name: <input name="custname"></label></p>
</form></body></html>""")
    _form_url = pathlib.Path(_form_path).as_uri()
    r = await mod.browser_interact("navigate", url=_form_url)
    check(r["ok"], "navigate forms page", f"{r.get('error')}")
    r = await mod.browser_interact("fill", selector="input[name=custname]", value="Paul")
    check(r["ok"], "fill", "")
    r = await mod.browser_interact("evaluate",
                                   expression="document.querySelector('input[name=custname]').value")
    check(r.get("result") == "Paul", f"fill value: {r.get('result')!r}", "")
    r = await mod.browser_interact("get_text")
    check(r["ok"] and len(r.get("content", "")) > 0, "get_text", "")
    r = await mod.browser_interact("screenshot")
    check(r["ok"] and r.get("bytes", 0) > 5000, "screenshot", "")
    import shutil
    shutil.rmtree(_tmpdir, ignore_errors=True)


async def test_smart_browse():
    step("Layer D6: smart_browse — UnifiedBrowser-first (主力意志)")
    mod = load_server()
    u = mod.Unified()
    r = await u.smart_browse("https://example.com/", require_fresh=True)
    check(r["ok"], "smart_browse OK", f"{r.get('error')}")
    check(r["engine_used"] == "unified_browser",
          f"engine = unified_browser ({r['engine_used']})", "")
    check(r["content_ok"], "content_ok", "")


async def test_status_engines():
    step("Layer D7: status — engine availability report")
    mod = load_server()
    u = mod.Unified()
    r = await u.status()
    check(r["server"] == "unified-fetch-v2", "server id", "")
    check("unified_browser" in r["engines"], "browser engine in report", "")
    check("cache" in r and "entries" in r["cache"], "cache stats", "")
    check("orientation" in r and len(r["orientation"]) > 200, "orientation doc", "")


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

async def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"\n{_BOLD}{'=' * 60}")
    print("  unified-fetch V2 — Full Smoke Test (new architecture)")
    print("  HTTP-first + UnifiedBrowser core · 14 tools")
    print(f"  {SCRIPT_DIR}")
    print(f"{'=' * 60}{_RESET}")

    # Layer A (no browser)
    await test_identity()
    await test_session_pool()
    await test_cdp_driver()
    await test_anti_detect()
    await test_behavior()
    await test_fingerprint_verify()

    # Layer C (no browser)
    test_mcp_tools()

    # Check browser
    from cdp_driver import CDPTransport
    path = CDPTransport().find_chrome()
    chrome_ok = bool(path and (os.path.isfile(path) or os.path.isdir(path)))

    if not chrome_ok:
        print(f"\n{_YELLOW}Browser not found — skipping Layer B, D (browser-dependent){_RESET}")
        # Layer D non-browser parts still run
        await test_http_first_chain()
        await test_cache()
        await test_parallel_search()
        await test_status_engines()
    else:
        print(f"\n{_DIM}Browser: {path}{_RESET}")
        await test_browser_lifecycle()
        await asyncio.sleep(0.8)          # let Chrome processes fully exit
        await test_navigate()
        await asyncio.sleep(0.8)
        await test_content_and_js()
        await test_screenshot_isolation()
        await asyncio.sleep(0.8)
        await test_interact_methods()
        await asyncio.sleep(0.8)
        await test_http_first_chain()
        await test_prefer_browser()
        await test_cache()
        await test_parallel_search()
        await test_cdp_interact()
        await test_smart_browse()
        await test_status_engines()

    # Cleanup: close global browser + cache connections (stop Chrome, quiet warnings)
    try:
        mod = load_server()
        if mod._browser is not None:
            await mod._browser.close()
            mod._browser = None
            log_info("global browser closed")
        if mod._engine is not None:
            try:
                mod._engine._cache.close()
            except Exception:
                pass
    except Exception:
        pass

    print()
    print("=" * 60)
    total = _passed + _failed
    if _failed == 0:
        print(f"{_GREEN}{_BOLD}ALL {total} TESTS PASSED{_RESET}")
    else:
        print(f"{_RED}{_BOLD}{_failed} FAILED{_RESET} / "
              f"{_GREEN}{_passed} passed{_RESET} / {total} total")
    print("=" * 60)
    return 1 if _failed > 0 else 0


if __name__ == "__main__":
    import traceback
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print(f"\n{_YELLOW}Interrupted{_RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{_RED}FATAL: {e}{_RESET}")
        traceback.print_exc()
        sys.exit(2)
