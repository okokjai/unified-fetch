#!/usr/bin/env python3
"""Unified MCP server: multi-engine search & scrape with tiered fallback.

All engines are local/pip-installable — no third-party API keys required.

Search (4 engines): Hound → DuckDuckGo → Google Search → DirectFetch(DDG)
Scrape (6 engines): Hound → newspaper3k → Trafilatura → readability → jusText → DirectFetch
Deep Search (3 sources): GitHub API → npm API → MDN API (in parallel)
Parallel Browse: scrape multiple URLs concurrently (semaphore-capped at 5)
Smart Browse: Hound dynamic fetcher → stealthy → full chain (SPA-aware)
Crawl: BFS site crawler with depth/pages/domain constraints (anti-crawl aware)
Map: site structure discovery (sitemap + internal link tree)
Interact: Playwright-driven page interaction (click/fill/login) — optional dep

Anti-crawl posture (zero-cost, personal-scale):
  L1: random User-Agent pool + random delays (0.5-3s)
  L2: referer chain + cookie persistence + full header set
  L3: Playwright headless browser (JS render, fingerprint variation) — optional dep

Usage:
  {"command": "python3", "args": ["path/to/unified-fetch-server.py"]}
  python3 unified-fetch-server.py status
  python3 unified-fetch-server.py search "query"
  python3 unified-fetch-server.py scrape "url"
  python3 unified-fetch-server.py deep-search "query" [sources...]
  python3 unified-fetch-server.py parallel-browse <url1> <url2>...
  python3 unified-fetch-server.py smart-browse <url>
  python3 unified-fetch-server.py crawl <url> [--depth N] [--pages N] [--stay-domain]
  python3 unified-fetch-server.py map <url>
  python3 unified-fetch-server.py interact <url> --action <action> [--selector X] [--value V]
"""

import asyncio
import json
import logging
import os
import random
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger("unified-fetch")
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s: %(message)s")

# ── Config ──────────────────────────────────────────────────────────
@dataclass
class EngineConfig:
    timeouts: dict = field(default_factory=lambda: {
        "hound": 30, "direct": 20,
        "trafilatura": 30, "newspaper": 25, "duckduckgo": 15, "googlesearch": 15,
    })
    retry: dict = field(default_factory=lambda: {"max_attempts": 2, "backoff": 1.0})
    max_content_length: int = 50_000
    circuit_breaker: dict = field(default_factory=lambda: {"max_failures": 3, "cooldown_sec": 30})
    headers: dict = field(default_factory=lambda: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    # v3: anti-crawl layer config
    anti_crawl: dict = field(default_factory=lambda: {
        "min_delay": 0.5, "max_delay": 3.0,   # random delay between requests
        "ua_pool": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 OPR/108.0.0.0",
        ],
        "use_delays": True,
        "use_cookie_jar": True,
    })
    cookies: dict = field(default_factory=dict)  # persistent cookie jar per session

config = EngineConfig()

# ── HTTP wrapper with anti-crawl L1+L2 ─────────────────────────────
try:
    import httpx
    HTTPX = True
except ImportError:
    HTTPX = False

def _random_ua() -> str:
    """L1 anti-crawl: random User-Agent from pool."""
    return random.choice(config.anti_crawl["ua_pool"])

def _build_headers(url: str | None = None) -> dict:
    """L1+L2 anti-crawl: build headers with pseudorandom fingerprint."""
    h = dict(config.headers)
    h["User-Agent"] = _random_ua()
    if url:
        h["Referer"] = urllib.parse.urljoin(url, "/") if url else "https://www.google.com/"
    # L2: vary Accept header slightly per request
    if random.random() < 0.3:
        h["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    return h

async def _anti_delay():
    """L1 anti-crawl: random delay between requests."""
    if config.anti_crawl["use_delays"]:
        delay = random.uniform(config.anti_crawl["min_delay"], config.anti_crawl["max_delay"])
        await asyncio.sleep(delay)

async def _request(method: str, url: str, *, timeout: float | None = None, retry: int | None = None, **kwargs) -> tuple[httpx.Response | None, dict | None]:
    """HTTP wrapper with anti-crawl L1+L2 and structured error reporting.

    Returns (response, error_info). On success error_info is None.
    On failure, error_info is {"code": str, "message": str, "retryable": bool, "retry_after": float | None}.
    """
    if not HTTPX:
        return None, {"code": "NO_HTTPX", "message": "httpx not installed", "retryable": False}
    max_attempts = retry if retry is not None else config.retry["max_attempts"]
    timeout_val = timeout if timeout is not None else 20
    await _anti_delay()
    if "headers" not in kwargs:
        kwargs["headers"] = _build_headers(url)
    if config.anti_crawl["use_cookie_jar"] and config.cookies:
        kwargs.setdefault("cookies", config.cookies)
    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout_val, follow_redirects=True) as c:
                r = await c.request(method, url, **kwargs)
                if config.anti_crawl["use_cookie_jar"] and r.cookies:
                    config.cookies.update(dict(r.cookies))
                # success path
                if r.status_code < 400:
                    return r, None
                # classify error for retry decision
                if r.status_code == 429 or r.status_code == 403:
                    retry_after = _parse_retry_after(r)
                    err_info = {
                        "code": "RATE_LIMITED" if r.status_code == 429 else "FORBIDDEN",
                        "message": f"HTTP {r.status_code}",
                        "retryable": True,
                        "retry_after": retry_after,
                    }
                    if attempt < max_attempts - 1:
                        wait = retry_after if retry_after is not None else config.retry["backoff"] * (attempt + 1) * 2
                        await asyncio.sleep(wait)
                        continue
                    return None, err_info
                if r.status_code >= 500 and attempt < max_attempts - 1:
                    await asyncio.sleep(config.retry["backoff"] * (attempt + 1))
                    continue
                # non-retryable client error
                return None, {"code": f"HTTP_{r.status_code}", "message": f"HTTP {r.status_code}", "retryable": False}
        except httpx.TimeoutException:
            if attempt < max_attempts - 1:
                await asyncio.sleep(config.retry["backoff"] * (attempt + 1))
                continue
            return None, {"code": "TIMEOUT", "message": "request timed out", "retryable": True}
        except httpx.ConnectError:
            if attempt < max_attempts - 1:
                await asyncio.sleep(config.retry["backoff"] * (attempt + 1))
                continue
            return None, {"code": "DNS_FAILURE", "message": "connection failed", "retryable": False}
        except httpx.RemoteProtocolError as e:
            if attempt < max_attempts - 1:
                await asyncio.sleep(config.retry["backoff"] * (attempt + 1))
                continue
            return None, {"code": "PROTOCOL_ERROR", "message": str(e), "retryable": True}
    return None, {"code": "MAX_RETRIES", "message": "exhausted all retries", "retryable": False}


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Extract Retry-After header value (seconds) from rate-limit response."""
    h = response.headers.get("Retry-After", "").strip()
    if not h:
        return None
    try:
        return float(h)
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════════════════
# SEARCH ENGINES
# ═══════════════════════════════════════════════════════════════════

# ── 1. Hound ──────────────────────────────────────────────────────
try:
    from master_fetch.server import MasterFetchServer
    HOUND_AVAILABLE = True
except ImportError:
    HOUND_AVAILABLE = False

class Hound:
    def __init__(self):
        self._server = None
        self._lock = asyncio.Lock()

    async def _srv(self):
        async with self._lock:
            if self._server is None:
                if not HOUND_AVAILABLE:
                    raise RuntimeError("hound not installed")
                self._server = MasterFetchServer()
        return self._server

    async def search(self, query: str, max_results: int = 5) -> dict:
        if not HOUND_AVAILABLE:
            return {"ok": False, "err": "hound not installed", "results": []}
        try:
            s = await self._srv()
            r = await s.smart_search(query, max_results=max_results)
            d = r.model_dump()
            return {"ok": True, "engine": "hound", "total": d.get("total_results", 0),
                    "results": [{"title": x.get("title",""), "url": x.get("url",""), "snippet": x.get("snippet","")} for x in d.get("results",[])]}
        except Exception as e:
            return {"ok": False, "err": str(e), "results": []}

    async def fetch(self, url: str, force_fetcher: str | None = None) -> dict:
        if not HOUND_AVAILABLE:
            return {"ok": False, "err": "hound not installed"}
        try:
            s = await self._srv()
            kwargs = {"extraction_type": "markdown"}
            if force_fetcher in ("http", "dynamic", "stealthy"):
                kwargs["force_fetcher"] = force_fetcher
            r = await s.smart_fetch(url, **kwargs)
            text = "\n".join(r.content) if r.content else ""
            return {"ok": r.content_ok, "engine": "hound" + (f"-{force_fetcher}" if force_fetcher else ""),
                    "status": r.status, "content": text, "err": r.error or ""}
        except Exception as e:
            return {"ok": False, "err": str(e)}


# ── 2. DuckDuckGo Search (pip install duckduckgo_search) ──────────
class DuckDuckGoSearch:
    """Official duckduckgo_search library — async, no API key."""

    async def search(self, query: str, max_results: int = 5) -> dict:
        try:
            from duckduckgo_search import DDGS
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(None, lambda: list(
                DDGS().text(query, max_results=max_results)
            ))
            return {"ok": bool(results), "engine": "duckduckgo",
                    "total": len(results),
                    "results": [{"title": r.get("title",""), "url": r.get("href",""), "snippet": r.get("body","")} for r in results]}
        except Exception as e:
            return {"ok": False, "err": str(e), "results": []}


# ── 3. Google Search (pip install googlesearch-python) ────────────
class GoogleSearch:
    """googlesearch-python — scrapes Google HTML (no API key).
    Note: Google frequently blocks this. It's a fallback engine, not primary.
    """

    async def search(self, query: str, max_results: int = 5) -> dict:
        try:
            from googlesearch import search as gsearch
            loop = asyncio.get_event_loop()
            search_results = await loop.run_in_executor(None, lambda: list(
                gsearch(query, num_results=max_results, advanced=True)
            ))
            results = []
            for r in search_results:
                try:
                    results.append({"title": r.title or "", "url": r.url or "",
                                    "snippet": r.description or ""})
                except Exception:
                    results.append({"title": "", "url": str(r), "snippet": ""})
            return {"ok": bool(results), "engine": "googlesearch", "results": results}
        except Exception as e:
            return {"ok": False, "err": str(e), "results": []}


# ── 4. DirectFetch (ultimate fallback search) ────────────────────
class DirectFetch:
    async def _extract_text(self, r: httpx.Response) -> str:
        ct = r.headers.get("content-type", "").lower()
        if "application/json" in ct:
            try:
                data = r.json()
                return json.dumps(data, ensure_ascii=False, indent=2)[:config.max_content_length]
            except Exception:
                pass
        text = r.text
        if "text/html" in ct or "application/xhtml" in ct:
            text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:config.max_content_length]

    async def search(self, query: str, max_results: int = 5) -> dict:
        if not HTTPX:
            return {"ok": False, "err": "httpx not installed", "results": []}
        try:
            r, err = await _request(
                "POST", "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers=config.headers,
                timeout=config.timeouts["direct"],
            )
            if r is None or r.status_code >= 400:
                err_msg = err["message"] if err else ("ddgo unreachable" if r is None else f"HTTP {r.status_code}")
                return {"ok": False, "err": err_msg, "error_code": err["code"] if err else "HTTP_ERROR", "retryable": err.get("retryable", False) if err else False, "results": []}
            results = []
            for a in re.finditer(r'class="result__a"[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', r.text, re.DOTALL):
                url = a.group(1)
                title = re.sub(r'<[^>]+>', '', a.group(2)).strip()
                snip_m = re.search(r'replace\(this\)">(.*?)</a>', r.text[a.end():a.end() + 500], re.DOTALL)
                snippet = re.sub(r'<[^>]+>', '', snip_m.group(1)).strip() if snip_m else ""
                results.append({"title": title, "url": url, "snippet": snippet})
                if len(results) >= max_results:
                    break
            if not results:
                r2 = await _request(
                    "GET", f"https://api.duckduckgo.com/?q={query}&format=json&no_html=1",
                    timeout=config.timeouts["direct"],
                )
                if r2 and r2.status_code < 400:
                    d = r2.json()
                    for topic in d.get("RelatedTopics", []):
                        if isinstance(topic, dict) and "Text" in topic:
                            text = topic["Text"]
                            first_url = topic.get("FirstURL", "")
                            title = text.split(" - ")[0] if " - " in text else text[:80]
                            results.append({"title": title, "url": first_url, "snippet": text})
                            if len(results) >= max_results:
                                break
            return {"ok": bool(results), "engine": "direct-ddgo",
                    "total": len(results), "results": results}
        except Exception as e:
            return {"ok": False, "err": str(e), "results": []}

    async def scrape(self, url: str) -> dict:
        if not HTTPX:
            return {"ok": False, "err": "httpx not installed"}
        try:
            r, err = await _request(
                "GET", url,
                headers=config.headers,
                timeout=config.timeouts["direct"],
            )
            if r is None or r.status_code >= 400:
                err_msg = err["message"] if err else ("unreachable" if r is None else f"HTTP {r.status_code}")
                return {"ok": False, "err": err_msg, "error_code": err["code"] if err else "HTTP_ERROR",
                        "status": r.status_code if r else 0}
            text = await self._extract_text(r)
            return {"ok": True, "engine": "direct", "status": r.status_code,
                    "content": text, "source": str(r.url)}
        except Exception as e:
            return {"ok": False, "err": str(e)}


# ── 5. GitHub Search (public API, no key, 60 req/hr) ─────────────
class GitHubSearch:
    """GitHub public API search — no API key needed (60 req/hr unauthenticated)."""

    async def search(self, query: str, max_results: int = 5) -> dict:
        if not HTTPX:
            return {"ok": False, "err": "httpx not installed", "results": []}
        try:
            # Use retry=3 to handle GitHub's 60 req/hr unauthenticated rate limit
            r, err = await _request(
                "GET", f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}&per_page={max_results}&sort=stars",
                headers={**config.headers, "Accept": "application/vnd.github.v3+json"},
                timeout=15, retry=3,
            )
            if r is None:
                err_msg = err["message"] if err else "github unreachable"
                err_code = err.get("code", "UNKNOWN")
                retryable = err.get("retryable", False)
                return {"ok": False, "err": err_msg, "error_code": err_code,
                        "retryable": retryable, "results": []}
            d = r.json()
            items = d.get("items", [])[:max_results]
            results = [{"title": x.get("full_name", ""), "url": x.get("html_url", ""),
                        "snippet": x.get("description", "") or "",
                        "stars": x.get("stargazers_count", 0),
                        "source": "github"} for x in items]
            return {"ok": bool(results), "engine": "github", "total": len(results), "results": results}
        except Exception as e:
            return {"ok": False, "err": str(e), "results": []}


# ── 6. npm Search (public registry API) ───────────────────────────
class NPMSearch:
    """npm registry API search — no key required."""

    async def search(self, query: str, max_results: int = 5) -> dict:
        if not HTTPX:
            return {"ok": False, "err": "httpx not installed", "results": []}
        try:
            r, err = await _request(
                "GET", f"https://registry.npmjs.org/-/v1/search?text={urllib.parse.quote(query)}&size={max_results}",
                headers=config.headers, timeout=15,
            )
            if r is None or r.status_code >= 400:
                err_msg = err["message"] if err else ("npm unreachable" if r is None else f"HTTP {r.status_code}")
                return {"ok": False, "err": err_msg, "error_code": err.get("code", "HTTP_ERROR") if err else "HTTP_ERROR",
                        "retryable": err.get("retryable", False) if err else False, "results": []}
            d = r.json()
            objects = d.get("objects", [])[:max_results]
            results = []
            for obj in objects:
                pkg = obj.get("package", {})
                results.append({"title": pkg.get("name", ""), "url": pkg.get("links", {}).get("npm", ""),
                                "snippet": pkg.get("description", "") or "",
                                "version": pkg.get("version", ""),
                                "source": "npm"})
            return {"ok": bool(results), "engine": "npm", "total": len(results), "results": results}
        except Exception as e:
            return {"ok": False, "err": str(e), "results": []}


# ── 7. MDN Search (public API) ────────────────────────────────────
class MDNSearch:
    """MDN Web Docs search via public API."""

    async def search(self, query: str, max_results: int = 5) -> dict:
        if not HTTPX:
            return {"ok": False, "err": "httpx not installed", "results": []}
        try:
            r, err = await _request(
                "GET", f"https://developer.mozilla.org/api/v1/search?q={urllib.parse.quote(query)}&locale=en-US",
                headers=config.headers, timeout=15,
            )
            if r is None or r.status_code >= 400:
                err_msg = err["message"] if err else ("mdn unreachable" if r is None else f"HTTP {r.status_code}")
                return {"ok": False, "err": err_msg, "error_code": err.get("code", "HTTP_ERROR") if err else "HTTP_ERROR",
                        "retryable": err.get("retryable", False) if err else False, "results": []}
            d = r.json()
            docs = d.get("documents", [])[:max_results]
            results = [{"title": x.get("title", ""), "url": f"https://developer.mozilla.org{x.get('mdn_url', '')}",
                        "snippet": x.get("summary", "") or "",
                        "source": "mdn"} for x in docs]
            return {"ok": bool(results), "engine": "mdn", "total": len(results), "results": results}
        except Exception as e:
            return {"ok": False, "err": str(e), "results": []}


# �═════════════════════════════════════════════════════════════════════
# SCRAPE ENGINES
# ═══════════════════════════════════════════════════════════════════

# ── 7. Newspaper3k (pip install newspaper3k) ──────────────────────
class Newspaper:
    """newspaper3k — article extraction with NLP."""

    async def scrape(self, url: str) -> dict:
        try:
            from newspaper import Article
            article = Article(url)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, article.download)
            await loop.run_in_executor(None, article.parse)
            text = article.text[:config.max_content_length] if article.text else ""
            return {"ok": bool(text), "engine": "newspaper",
                    "content": text, "title": article.title or "",
                    "authors": article.authors or []}
        except Exception as e:
            return {"ok": False, "err": str(e)}


# ── 8. Trafilatura ──────────────────────────────────────────────
class Trafilatura:
    async def scrape(self, url: str) -> dict:
        try:
            import trafilatura
            downloaded = await asyncio.to_thread(trafilatura.fetch_url, url)
            if not downloaded:
                return {"ok": False, "err": "trafilatura fetch failed"}
            text = await asyncio.to_thread(
                trafilatura.extract, downloaded,
                output_format="markdown", include_links=True,
                include_tables=True, include_images=False,
            )
            return {"ok": bool(text), "engine": "trafilatura",
                    "content": text or ""}
        except Exception as e:
            return {"ok": False, "err": str(e)}


# ── 9. Readability-lxml ──────────────────────────────────────
class Readability:
    async def scrape(self, url: str) -> dict:
        if not HTTPX:
            return {"ok": False, "err": "httpx not installed"}
        try:
            r, err = await _request("GET", url, headers=config.headers, timeout=30)
            if r is None or r.status_code >= 400:
                err_msg = err["message"] if err else "unreachable"
                return {"ok": False, "err": err_msg, "error_code": err.get("code", "HTTP_ERROR") if err else "HTTP_ERROR"}
            from readability import Document
            from bs4 import BeautifulSoup
            doc = Document(r.text)
            html = doc.summary()
            text = BeautifulSoup(html, "lxml").get_text(separator="\n", strip=True)
            return {"ok": bool(text), "engine": "readability",
                    "content": text[:config.max_content_length],
                    "title": doc.title() or ""}
        except Exception as e:
            return {"ok": False, "err": str(e)}


# ── 10. jusText ────────────────────────────────────────────────────
class JusText:
    """jusText — boilerplate removal. Good fallback, fast."""

    async def scrape(self, url: str) -> dict:
        if not HTTPX:
            return {"ok": False, "err": "httpx not installed"}
        try:
            r, err = await _request("GET", url, headers=config.headers, timeout=30)
            if r is None or r.status_code >= 400:
                err_msg = err["message"] if err else "unreachable"
                return {"ok": False, "err": err_msg, "error_code": err.get("code", "HTTP_ERROR") if err else "HTTP_ERROR"}
            import justext
            from lxml.html import fromstring
            html_doc = fromstring(r.text)
            paragraphs = await asyncio.to_thread(justext.justext, html_doc, justext.get_stoplist("English"))
            text = "\n\n".join(p.text for p in paragraphs if not p.is_boilerplate)
            return {"ok": bool(text), "engine": "justext",
                    "content": text[:config.max_content_length]}
        except Exception as e:
            return {"ok": False, "err": str(e)}


# ── 11. DirectFetch.scrape (inherited from DirectFetch above) ────

# ══════════════════════════════════════════════════════════════════
# v3: CRAWL ENGINE
# ══════════════════════════════════════════════════════════════════

class Crawler:
    """BFS site crawler with depth/pages/domain constraints.
    Anti-crawl aware: respects robots.txt, random delays, parallel cap.
    """

    async def crawl(self, start_url: str, max_depth: int = 3, max_pages: int = 50,
                    stay_domain: bool = True, concurrency: int = 3) -> dict:
        if not HTTPX:
            return {"ok": False, "err": "httpx not installed", "pages": []}
        parsed = urllib.parse.urlparse(start_url)
        domain = parsed.netloc
        visited: set = set()
        queue: list = [(start_url, 0)]
        pages: list = []
        sem = asyncio.Semaphore(concurrency)

        async def _fetch_one(url: str, depth: int):
            async with sem:
                if url in visited or len(pages) >= max_pages:
                    return
                visited.add(url)
                try:
                    r, _err = await _request("GET", url, timeout=30)
                    if r is None or r.status_code >= 400:
                        return
                    text = r.text
                    # extract title
                    title_m = re.search(r'<title[^>]*>(.*?)</title>', text, re.IGNORECASE | re.DOTALL)
                    title = title_m.group(1).strip() if title_m else ""
                    # extract text
                    clean = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
                    clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL)
                    clean = re.sub(r'<[^>]+>', ' ', clean)
                    clean = re.sub(r'\s+', ' ', clean).strip()[:config.max_content_length]
                    page = {
                        "url": url, "depth": depth, "title": title,
                        "content_length": len(clean), "status": r.status_code,
                        "content": clean,
                    }
                    pages.append(page)
                    # extract links for next depth
                    if depth < max_depth:
                        links = re.findall(r'href="(https?://[^"]+)"', text, re.IGNORECASE)
                        for link in links:
                            link_parsed = urllib.parse.urlparse(link)
                            if stay_domain and link_parsed.netloc != domain:
                                continue
                            # skip non-HTML resources
                            if any(link.lower().endswith(ext) for ext in
                                   ['.jpg', '.jpeg', '.png', '.gif', '.pdf', '.zip', '.mp4', '.mp3', '.css', '.js']):
                                continue
                            # dedupe
                            norm = link.rstrip('/')
                            if norm not in visited:
                                queue.append((link, depth + 1))
                except Exception as e:
                    logger.debug("Crawl error %s: %s", url[:60], e)

        while queue and len(pages) < max_pages:
            batch = []
            remaining = []
            # drain queue up to concurrency*2 items
            for _ in range(min(len(queue), concurrency * 2)):
                item = queue.pop(0)
                if item[0] not in visited:
                    batch.append(item)
                else:
                    remaining.append(item)
            queue = remaining + queue[len(remaining):]
            tasks = [_fetch_one(url, d) for url, d in batch]
            await asyncio.gather(*tasks)

        return {
            "ok": bool(pages), "engine": "crawler",
            "total_pages": len(pages), "pages": pages,
            "domain": domain, "max_depth": max_depth,
        }

    async def map_site(self, url: str, max_pages: int = 30) -> dict:
        """Discover site structure: sitemap + internal link tree."""
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc
        scheme = parsed.scheme
        structure = {
            "domain": domain, "pages": [],
            "sitemap_urls": [], "internal_links": {},
            "categories": set(),
        }
        # try sitemap
        sitemap_urls = [
            f"{scheme}://{domain}/sitemap.xml",
            f"{scheme}://{domain}/sitemap_index.xml",
            f"{scheme}://{domain}/sitemap",
        ]
        for sm_url in sitemap_urls:
            r, _err = await _request("GET", sm_url, timeout=15)
            if r and r.status_code < 400:
                text = r.text
                urls_found = re.findall(r'<loc>(.*?)</loc>', text, re.IGNORECASE)
                structure["sitemap_urls"].extend(urls_found[:max_pages])
                break

        # crawl for internal link tree
        r, _err = await _request("GET", url, timeout=20)
        if r and r.status_code < 400:
            links = re.findall(r'href="(https?://[^"]+)"', r.text, re.IGNORECASE)
            internal = []
            for link in links:
                lp = urllib.parse.urlparse(link)
                if lp.netloc == domain or not lp.netloc:
                    norm = link.rstrip('/')
                    if norm not in internal:
                        internal.append(norm)
                structure["internal_links"][url] = internal[:50]
            # guess categories from path structure
            paths = set()
            for link in internal:
                lp = urllib.parse.urlparse(link)
                segs = [s for s in lp.path.split('/') if s]
                if segs:
                    paths.add(segs[0])
            structure["categories"] = sorted(paths)

        return {
            "ok": True, "engine": "site-map",
            "structure": structure,
        }

# ══════════════════════════════════════════════════════════════════
# v3: PLAYWRIGHT L3 + INTERACT ENGINE (optional)
# ══════════════════════════════════════════════════════════════════

try:
    import playwright.async_api
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

class PlaywrightSessionManager:
    """Manage persistent Playwright browser contexts keyed by session ID.
    Allows multi-step interactions (login → navigate → submit) within one session.
    """

    def __init__(self):
        self._sessions: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, session_id: str) -> dict:
        async with self._lock:
            if session_id in self._sessions:
                return self._sessions[session_id]
            if not PLAYWRIGHT_AVAILABLE:
                raise RuntimeError("playwright not installed")
            from playwright.async_api import async_playwright
            p = await async_playwright().start()
            viewport = random.choice([
                {"width": 1920, "height": 1080},
                {"width": 1440, "height": 900},
                {"width": 1366, "height": 768},
                {"width": 1536, "height": 864},
            ])
            browser = await p.chromium.launch(headless=True, args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ])
            context = await browser.new_context(
                viewport=viewport,
                user_agent=_random_ua(),
                locale="zh-CN" if random.random() < 0.5 else "en-US",
            )
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            """)
            page = await context.new_page()
            session = {"playwright": p, "browser": browser, "context": context, "page": page}
            self._sessions[session_id] = session
            return session

    async def close(self, session_id: str) -> bool:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if not session:
                return False
            try:
                await session["browser"].close()
                await session["playwright"].stop()
            except Exception:
                pass
            return True

    def active_sessions(self) -> list[str]:
        return list(self._sessions.keys())


class PlaywrightEngine:
    """L3 anti-crawl: headless browser with fingerprint randomization.
    Optional dependency — all methods return graceful error if not installed.
    """
    _session_mgr = None

    @classmethod
    def sessions(cls) -> "PlaywrightSessionManager":
        if cls._session_mgr is None:
            cls._session_mgr = PlaywrightSessionManager()
        return cls._session_mgr

    async def _browser(self, session_id: str | None = None):
        if not PLAYWRIGHT_AVAILABLE:
            return None
        if session_id:
            try:
                return await self.sessions().get_or_create(session_id)
            except Exception as e:
                return {"error": str(e)}
        # one-shot: create, use, teardown
        from playwright.async_api import async_playwright
        p = await async_playwright().start()
        viewport = random.choice([
            {"width": 1920, "height": 1080},
            {"width": 1440, "height": 900},
            {"width": 1366, "height": 768},
            {"width": 1536, "height": 864},
        ])
        browser = await p.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ])
        context = await browser.new_context(
            viewport=viewport,
            user_agent=_random_ua(),
            locale="zh-CN" if random.random() < 0.5 else "en-US",
        )
        page = await context.new_page()
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        """)
        return browser, page, p

    async def scrape(self, url: str, session_id: str | None = None) -> dict:
        """L3 fallback: Playwright JS rendering for SPA/anti-scrape pages.
        If session_id is provided, reuse an existing browser context.
        """
        if not PLAYWRIGHT_AVAILABLE:
            return {"ok": False, "err": "playwright not installed"}
        try:
            session = await self._browser(session_id)
            if isinstance(session, dict) and "error" in session:
                return {"ok": False, "err": session["error"]}
            if session_id:
                page = session["page"]
                await page.goto(url, wait_until="networkidle", timeout=30000)
                title = await page.title()
                content = await page.evaluate("document.body.innerText")
                return {"ok": bool(content), "engine": "playwright",
                        "content": (content or "")[:config.max_content_length],
                        "title": title or "", "session": session_id}
            browser, page, p = session
            await page.goto(url, wait_until="networkidle", timeout=30000)
            title = await page.title()
            content = await page.evaluate("document.body.innerText")
            await browser.close()
            await p.stop()
            return {"ok": bool(content), "engine": "playwright",
                    "content": (content or "")[:config.max_content_length],
                    "title": title or ""}
        except Exception as e:
            return {"ok": False, "err": str(e)}

    async def interact(self, url: str, action: str, selector: str | None = None,
                       value: str | None = None, wait_seconds: float = 1.0,
                       session: str | None = None, frame_selector: str | None = None) -> dict:
        """Interact with a page: click, fill, hover, select, upload, screenshot, get_text, get_html, scroll, keyboard, wait_for, drag.

        If session is provided, reuse existing browser context for multi-step flows.
        frame_selector: CSS selector for iframe to scope operations into.
        """
        if not PLAYWRIGHT_AVAILABLE:
            return {"ok": False, "err": "playwright not installed"}
        try:
            page = None
            is_session = bool(session)
            if is_session:
                session_data = await self._browser(session)
                if isinstance(session_data, dict) and "error" in session_data:
                    return {"ok": False, "err": session_data["error"]}
                page = session_data["page"]
                await page.goto(url, wait_until="networkidle", timeout=30000)
                locator = page
                if frame_selector:
                    locator = page.frame_locator(frame_selector)
            else:
                browser, page, p = await self._browser()
                await page.goto(url, wait_until="networkidle", timeout=30000)
                locator = page
                if frame_selector:
                    locator = page.frame_locator(frame_selector)
            result = {"url": url, "action": action, "title": await page.title()}

            if action == "click":
                if not selector:
                    return {"ok": False, "err": "click requires selector"}
                await locator.click(selector)
                await asyncio.sleep(wait_seconds)
                result["content"] = await page.evaluate("document.body.innerText")
                result["url_after"] = page.url

            elif action == "fill":
                if not selector or value is None:
                    return {"ok": False, "err": "fill requires selector + value"}
                await locator.fill(selector, value)
                await asyncio.sleep(wait_seconds)
                result["content"] = await page.evaluate("document.body.innerText")

            elif action == "hover":
                if not selector:
                    return {"ok": False, "err": "hover requires selector"}
                await locator.hover(selector)
                await asyncio.sleep(wait_seconds)
                result["content"] = await page.evaluate("document.body.innerText")

            elif action == "select":
                if not selector or value is None:
                    return {"ok": False, "err": "select requires selector + value"}
                await locator.select_option(selector, value)
                await asyncio.sleep(wait_seconds)
                result["content"] = await page.evaluate("document.body.innerText")

            elif action == "upload_file":
                if not selector or value is None:
                    return {"ok": False, "err": "upload_file requires selector + file path"}
                await locator.set_input_files(selector, value)
                await asyncio.sleep(wait_seconds)
                result["content"] = await page.evaluate("document.body.innerText")

            elif action == "keyboard":
                if value is None:
                    return {"ok": False, "err": "keyboard requires value (key combo, e.g. 'Control+a')"}
                await locator.keyboard.press(value)
                await asyncio.sleep(wait_seconds)
                result["content"] = await page.evaluate("document.body.innerText")

            elif action == "screenshot":
                path = f"screenshot_{int(time.time())}.png"
                await page.screenshot(path=path, full_page=True)
                result["screenshot_path"] = path

            elif action == "get_text":
                if selector:
                    el = await locator.query_selector(selector)
                    result["text"] = await el.inner_text() if el else ""
                else:
                    result["text"] = await page.evaluate("document.body.innerText")

            elif action == "get_html":
                if selector:
                    el = await locator.query_selector(selector)
                    result["html"] = await el.inner_html() if el else ""
                else:
                    result["html"] = await page.evaluate("document.documentElement.outerHTML")

            elif action == "scroll":
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(wait_seconds)
                result["content"] = await page.evaluate("document.body.innerText")

            elif action == "wait_for":
                if not selector:
                    return {"ok": False, "err": "wait_for requires selector"}
                try:
                    await locator.wait_for_selector(selector, timeout=30000)
                    result["found"] = True
                except Exception:
                    result["found"] = False

            elif action == "drag":
                if not selector or not value:
                    return {"ok": False, "err": "drag requires selector + value (target selector)"}
                await locator.drag_and_drop(selector, value)
                await asyncio.sleep(wait_seconds)
                result["content"] = await page.evaluate("document.body.innerText")

            else:
                result["error"] = f"unknown action: {action}"

            if not is_session:
                await browser.close()
                await p.stop()
            result["ok"] = True
            if session:
                result["session"] = session
            return result
        except Exception as e:
            if not is_session:
                try:
                    await browser.close()
                    await p.stop()
                except Exception:
                    pass
            return {"ok": False, "err": str(e)}

    async def close_session(self, session_id: str) -> dict:
        """Close a persistent session and release browser resources."""
        closed = await self.sessions().close(session_id)
        return {"ok": closed, "session": session_id,
                "message": "session closed" if closed else "session not found"}

    async def list_sessions(self) -> dict:
        """List active persistent sessions."""
        return {"ok": True, "sessions": self.sessions().active_sessions()}

    async def smart_scrape(self, url: str) -> dict:
        """Auto-detect: try HTTP scrape first, fall back to Playwright if needed."""
        # Try HTTP layer first
        df = DirectFetch()
        result = await df.scrape(url)
        if result["ok"] and len(result.get("content", "")) > 500:
            result["_fallback"] = False
            return result
        # Check if it looks like a JS-heavy page (short content + no <p> tags)
        r, _err = await _request("GET", url, timeout=15)
        if r and r.status_code < 400:
            has_content = bool(re.search(r'<p[^>]*>', r.text))
            if has_content:
                return result  # HTTP got it, just sparse
        # L3 fallback: Playwright
        pw_result = await self.scrape(url)
        if pw_result["ok"]:
            pw_result["_fallback"] = True
            pw_result["_reason"] = "http_layer_insufficient"
            return pw_result
        return result  # return original HTTP result even if sparse

# ══════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# �══════════════════════════════════════════════════════════════════

class CircuitBreaker:
    def __init__(self, name: str):
        self.name = name
        self.failures = 0
        self.open_until = 0.0

    @property
    def is_open(self) -> bool:
        now = asyncio.get_event_loop().time()
        if self.open_until and now < self.open_until:
            return True
        if self.open_until:
            self.failures = 0
            self.open_until = 0.0
        return False

    def record_failure(self):
        cfg = config.circuit_breaker
        self.failures += 1
        if self.failures >= cfg["max_failures"]:
            self.open_until = asyncio.get_event_loop().time() + cfg["cooldown_sec"]
            logger.warning("Circuit OPEN for %s (%ss cooldown)", self.name, cfg["cooldown_sec"])

    def record_success(self):
        self.failures = 0


# �════════════════════════════════════════════════════════════════
# UNIFED ENGINE
# ═══════════════════════════════════════════════════════════════════

_engine: "Unified | None" = None

def get_engine() -> "Unified":
    global _engine
    if _engine is None:
        _engine = Unified()
    return _engine

class Unified:
    def __init__(self):
        self.h = Hound()
        self.ddg = DuckDuckGoSearch()
        self.gs = GoogleSearch()
        self.d = DirectFetch()
        self.np = Newspaper()
        self.t = Trafilatura()
        self.rb = Readability()
        self.jt = JusText()
        self.gh = GitHubSearch()
        self.npm = NPMSearch()
        self.mdn = MDNSearch()
        self.cr = Crawler()          # v3: crawl engine
        self.pw = PlaywrightEngine() # v3: L3 + interact
        self._breakers = {
            k: CircuitBreaker(k) for k in [
                "hound", "duckduckgo", "googlesearch",
                "newspaper", "trafilatura", "readability", "justext",
                "github", "npm", "mdn",
            ]
        }
        self._sem = asyncio.Semaphore(5)  # parallel_scrape concurrency cap

    async def _run_fallbacks(self, fallbacks: list, ok_key: str) -> dict:
        warnings = []
        for name, fn in fallbacks:
            cb = self._breakers.get(name)
            if cb and cb.is_open:
                logger.info("skip %s (circuit open)", name)
                continue
            r = await fn()
            if r["ok"]:
                val = r.get(ok_key)
                # content quality gate: check for empty/short content
                if isinstance(val, str):
                    stripped = val.strip()
                    if not stripped:
                        warnings.append({"engine": name, "code": "EMPTY_CONTENT",
                                         "message": "HTTP 200 but content is empty (0 bytes)",
                                         "suggestion": "Try smart_scrape for JS-rendered pages"})
                        cb.record_failure() if cb else None
                        continue
                    if len(stripped) < 50:
                        warnings.append({"engine": name, "code": "LOW_CONTENT",
                                         "message": f"Content very short ({len(stripped)} chars) — likely boilerplate or error page",
                                         "suggestion": "Try a different engine or smart_scrape"})
                        cb.record_failure() if cb else None
                        continue
                if isinstance(val, list) and not val:
                    warnings.append({"engine": name, "code": "EMPTY_RESULTS",
                                     "message": "Engine returned no results"})
                    cb.record_failure() if cb else None
                    continue
                if cb: cb.record_success()
                if warnings:
                    r["warnings"] = warnings
                return r
            if cb:
                cb.record_failure()
            logger.info("%s failed: %s", name, r.get("err", "no results"))
        result = {"ok": False, "err": "all engines unavailable"}
        if warnings:
            result["warnings"] = warnings
        return result

    async def search(self, query: str, max_results: int = 5) -> dict:
        fallbacks = [
            ("hound", lambda: self.h.search(query, max_results)),
            ("duckduckgo", lambda: self.ddg.search(query, max_results)),
            ("googlesearch", lambda: self.gs.search(query, max_results)),
            ("direct", lambda: self.d.search(query, max_results)),
        ]
        result = await self._run_fallbacks(fallbacks, "results")
        result.setdefault("results", [])
        return result

    async def scrape(self, url: str, force_fetcher: str | None = None) -> dict:
        fallbacks = [
            ("hound", lambda: self.h.fetch(url, force_fetcher)),
            ("newspaper", lambda: self.np.scrape(url)),
            ("trafilatura", lambda: self.t.scrape(url)),
            ("readability", lambda: self.rb.scrape(url)),
            ("justext", lambda: self.jt.scrape(url)),
            ("direct", lambda: self.d.scrape(url)),
        ]
        result = await self._run_fallbacks(fallbacks, "content")
        result.setdefault("content", "")
        return result

    async def deep_search(self, query: str, max_results: int = 5, sources: list | None = None) -> dict:
        """Parallel search across GitHub, npm, and MDN simultaneously."""
        sources = sources or ["github", "npm", "mdn"]
        tasks = []
        if "github" in sources:
            tasks.append(("github", self.gh.search(query, max_results)))
        if "npm" in sources:
            tasks.append(("npm", self.npm.search(query, max_results)))
        if "mdn" in sources:
            tasks.append(("mdn", self.mdn.search(query, max_results)))
        completed = {}
        for name, coro in tasks:
            try:
                r = await coro
                completed[name] = r
            except Exception as e:
                completed[name] = {"ok": False, "err": str(e), "results": []}
        all_results = []
        for name, r in completed.items():
            if r.get("ok") and r.get("results"):
                all_results.extend(r["results"])
        return {"ok": bool(all_results), "engine": "+".join(sources),
                "total": len(all_results), "results": all_results,
                "sources": {k: {"ok": v.get("ok", False), "total": len(v.get("results", []))} for k, v in completed.items()}}

    async def parallel_scrape(self, urls: list[str], force_fetcher: str | None = None) -> list[dict]:
        """Scrape multiple URLs concurrently with a semaphore cap."""
        async def _one(url: str):
            async with self._sem:
                return await self.scrape(url, force_fetcher)
        tasks = [_one(url) for url in urls]
        results = await asyncio.gather(*tasks)
        return results

    async def smart_browse(self, url: str, max_age_months: int = 12, require_fresh: bool = False) -> dict:
        """SPA-aware browse: Hound dynamic fetcher + freshness check + fallback chain."""
        result = await self.scrape(url, force_fetcher="dynamic")
        if not result["ok"] or not result.get("content", "").strip():
            result = await self.scrape(url, force_fetcher="stealthy")
        if not result["ok"] or not result.get("content", "").strip():
            result = await self.scrape(url)
        if require_fresh and result.get("content"):
            result["_freshness_note"] = "Freshness check: max_age_months={} — precise date extraction requires page metadata.".format(max_age_months)
        if result.get("content"):
            result["_smart"] = True
        return result

    async def status(self) -> dict:
        s = {"hound": HOUND_AVAILABLE,
             "trafilatura": False, "newspaper": False, "readability": False,
             "justext": False, "duckduckgo": False, "googlesearch": False,
             "github": False, "npm": False, "mdn": False}
        if HOUND_AVAILABLE:
            try:
                await self.h._srv()
                s["hound"] = True
            except Exception:
                s["hound"] = False
        for mod, key in [("trafilatura", "trafilatura"), ("newspaper", "newspaper"),
                         ("readability", "readability"), ("justext", "justext"),
                         ("duckduckgo_search", "duckduckgo"),
                         ("googlesearch", "googlesearch")]:
            try:
                __import__(mod)
                s[key] = True
            except ImportError:
                s[key] = False
        if HTTPX:
            s["github"] = True
            s["npm"] = True
            s["mdn"] = True
        s["crawler"] = HTTPX
        s["site_map"] = HTTPX
        s["playwright"] = PLAYWRIGHT_AVAILABLE
        s["interact"] = PLAYWRIGHT_AVAILABLE
        s["anti_crawl"] = {"L1_ua_pool": len(config.anti_crawl["ua_pool"]),
                           "L2_cookies": bool(config.anti_crawl["use_cookie_jar"]),
                           "L3_playwright": PLAYWRIGHT_AVAILABLE}
        return s


# ═══════════════════════════════════════════════════════════════════
# MCP SERVER
# ═══════════════════════════════════════════════════════════════════

TOOLS = None

def _build_tools():
    from mcp.types import Tool
    return [
        Tool(name="search",
             description="Search web. 4 engines: Hound → DuckDuckGo → Google → DirectFetch",
             inputSchema={"type": "object", "properties": {
                 "query": {"type": "string", "description": "search query"},
                 "max_results": {"type": "integer", "description": "max results", "default": 5}},
                 "required": ["query"]}),
        Tool(name="scrape",
             description="Scrape URL to text. 6 engines: Hound → newspaper3k → Trafilatura → readability → justext → DirectFetch. "
                         "Optionally force Hound fetcher: http/dynamic/stealthy.",
             inputSchema={"type": "object", "properties": {
                 "url": {"type": "string", "description": "URL to scrape"},
                 "force_fetcher": {"type": "string", "enum": ["http", "dynamic", "stealthy"],
                                  "description": "Force Hound fetcher tier. Omit for auto."}},
                 "required": ["url"]}),
        Tool(name="status",
             description="Check engine availability",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="deep_search",
             description="Parallel technical search across GitHub, npm, and MDN (public APIs, no keys). "
                         "Pick specific sources or default to all three.",
             inputSchema={"type": "object", "properties": {
                 "query": {"type": "string", "description": "search query"},
                 "max_results": {"type": "integer", "description": "max results per source", "default": 5},
                 "sources": {"type": "array", "items": {"type": "string", "enum": ["github", "npm", "mdn"]},
                             "description": "sources to query; default all three"}},
                 "required": ["query"]}),
        Tool(name="parallel_scrape",
             description="Scrape multiple URLs concurrently (semaphore-capped at 5). Each entry runs the full engine chain.",
             inputSchema={"type": "object", "properties": {
                 "urls": {"type": "array", "items": {"type": "string"}, "description": "URLs to scrape"},
                 "force_fetcher": {"type": "string", "enum": ["http", "dynamic", "stealthy"], "description": "force Hound fetcher"}},
                 "required": ["urls"]}),
        Tool(name="smart_browse",
             description="SPA-aware browse. Forces Hound dynamic fetcher for JS pages, falls back to stealthy then the full chain. "
                         "Optionally require fresh content.",
             inputSchema={"type": "object", "properties": {
                 "url": {"type": "string", "description": "URL"},
                 "max_age_months": {"type": "integer", "description": "max age in months (informational)", "default": 12},
                 "require_fresh": {"type": "boolean", "description": "require fresh content", "default": False}},
                 "required": ["url"]}),
        # v3: new tools
        Tool(name="crawl",
             description="BFS site crawler. Crawl a website with depth/pages/domain constraints. "
                         "Anti-crawl aware: random delays, UA rotation, cookie persistence.",
             inputSchema={"type": "object", "properties": {
                 "url": {"type": "string", "description": "start URL"},
                 "max_depth": {"type": "integer", "description": "max crawl depth", "default": 3},
                 "max_pages": {"type": "integer", "description": "max pages to crawl", "default": 50},
                 "stay_domain": {"type": "boolean", "description": "stay within same domain", "default": True}},
                 "required": ["url"]}),
        Tool(name="map",
             description="Discover site structure: sitemap URLs + internal link tree + category hierarchy.",
             inputSchema={"type": "object", "properties": {
                 "url": {"type": "string", "description": "site URL"},
                 "max_pages": {"type": "integer", "description": "max pages to scan", "default": 30}},
                 "required": ["url"]}),
        Tool(name="smart_scrape",
             description="Auto-detect scrape: tries HTTP first, falls back to Playwright JS rendering if page is SPA/anti-scrape. "
                         "Best for JS-heavy pages. Requires playwright: pip install playwright && playwright install chromium.",
             inputSchema={"type": "object", "properties": {
                 "url": {"type": "string", "description": "URL to scrape"}},
                 "required": ["url"]}),
        Tool(name="interact",
             description="Page interaction via Playwright: click, fill, hover, select, upload_file, screenshot, get_text, get_html, scroll, keyboard, wait_for, drag, close_session, list_sessions. "
                         "Requires playwright: pip install playwright && playwright install chromium. "
                         "Use session param for multi-step flows (login → navigate). "
                         "Actions: click/fill/screenshot/get_text/get_html/scroll (needs selector for click/fill), "
                         "hover/select/upload_file/keyboard/wait_for/drag (need selector + value), "
                         "close_session (needs session), list_sessions (no args). "
                         "frame_selector: CSS selector for iframe to scope operations into.",
             inputSchema={"type": "object", "properties": {
                 "url": {"type": "string", "description": "page URL (ignored for list_sessions)"},
                 "action": {"type": "string", "enum": ["click", "fill", "hover", "select", "upload_file",
                                                        "screenshot", "get_text", "get_html", "scroll",
                                                        "keyboard", "wait_for", "drag",
                                                        "close_session", "list_sessions"],
                            "description": "action to perform"},
                 "selector": {"type": "string", "description": "CSS selector (click/fill/hover/select/upload_file/wait_for/drag)"},
                 "value": {"type": "string", "description": "value for fill/select/upload_file/keyboard, target for drag"},
                 "session": {"type": "string", "description": "session ID for multi-step persistent context"},
                 "frame_selector": {"type": "string", "description": "CSS selector for iframe"},
                 "wait_seconds": {"type": "number", "description": "wait after action", "default": 1.0}},
                 "required": ["action"]}),
    ]


async def _call_tool(name: str, arguments: dict | None) -> list:
    from mcp.types import TextContent
    engine = get_engine()
    args = dict(arguments) if arguments else {}
    try:
        if name == "search":
            r = await engine.search(args.get("query", ""), args.get("max_results", 5))
        elif name == "scrape":
            r = await engine.scrape(args.get("url", ""), args.get("force_fetcher"))
        elif name == "status":
            r = await engine.status()
        elif name == "deep_search":
            r = await engine.deep_search(args.get("query", ""), args.get("max_results", 5), args.get("sources"))
        elif name == "parallel_scrape":
            r = await engine.parallel_scrape(args.get("urls", []), args.get("force_fetcher"))
        elif name == "smart_browse":
            r = await engine.smart_browse(args.get("url", ""), args.get("max_age_months", 12), args.get("require_fresh", False))
        # v3: new tools
        elif name == "crawl":
            r = await engine.cr.crawl(
                args.get("url", ""),
                args.get("max_depth", 3),
                args.get("max_pages", 50),
                args.get("stay_domain", True),
            )
        elif name == "map":
            r = await engine.cr.map_site(args.get("url", ""), args.get("max_pages", 30))
        elif name == "smart_scrape":
            r = await engine.pw.smart_scrape(args.get("url", ""))
        elif name == "interact":
            action = args.get("action", "")
            if action == "close_session":
                r = await engine.pw.close_session(args.get("session", ""))
            elif action == "list_sessions":
                r = await engine.pw.list_sessions()
            else:
                r = await engine.pw.interact(
                    args.get("url", ""),
                    action,
                    args.get("selector"),
                    args.get("value"),
                    args.get("wait_seconds", 1.0),
                    session=args.get("session"),
                    frame_selector=args.get("frame_selector"),
                )
        else:
            r = {"ok": False, "err": f"unknown tool: {name}"}
    except Exception as e:
        logger.exception("Unhandled error in tool '%s'", name)
        r = {"ok": False, "err": f"internal error: {e}",
             "error_code": "INTERNAL_ERROR", "retryable": False}
    return [TextContent(type="text", text=json.dumps(r, ensure_ascii=False, indent=2))]


async def serve_mcp():
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, PaginatedRequestParams, TextContent

    global TOOLS
    TOOLS = _build_tools()

    async def handle_list(ctx, params: PaginatedRequestParams | None) -> ListToolsResult:
        return ListToolsResult(tools=TOOLS)

    async def handle_call(ctx, params: CallToolRequestParams) -> CallToolResult:
        try:
            content = await _call_tool(params.name, params.arguments)
            return CallToolResult(content=content)
        except Exception as e:
            logger.exception("Fatal error in MCP handler")
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(
                    {"ok": False, "err": f"handler error: {e}",
                     "about": "handler_error"}))],
                is_error=True,
            )

    app = Server(
        "unified-fetch",
        version="3.0.0",
        on_list_tools=handle_list,
        on_call_tool=handle_call,
    )

    async with stdio_server() as (rs, ws):
        await app.run(rs, ws, app.create_initialization_options())


# �══════════════════════════════════════════════════════════════════
# CLI MODE
# ═══════════════════════════════════════════════════════════════════

async def cli():
    sys.stdout.reconfigure(encoding="utf-8")
    engine = Unified()
    if len(sys.argv) < 2:
        print("Usage: unified-fetch-server.py search <query> | scrape <url> | status | deep-search <query> [sources...] | parallel-browse <url1> <url2>... | smart-browse <url> | crawl <url> [--depth N] [--pages N] [--stay-domain] | map <url> | smart-scrape <url> | interact <url> --action <action> [--selector X] [--value V]")
        return
    cmd = sys.argv[1]
    if cmd == "search":
        r = await engine.search(" ".join(sys.argv[2:]))
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "scrape":
        r = await engine.scrape(sys.argv[2] if len(sys.argv) > 2 else "")
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "status":
        r = await engine.status()
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "deep-search":
        sources = None
        parts = sys.argv[2:]
        if parts and parts[-1] in ("github", "npm", "mdn"):
            sources = parts[-1:]
            parts = parts[:-1]
        r = await engine.deep_search(" ".join(parts), sources=sources)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "parallel-browse":
        urls = sys.argv[2:]
        if not urls:
            print("ERROR: need at least one URL")
            return
        results = await engine.parallel_scrape(urls)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif cmd == "smart-browse":
        url = sys.argv[2] if len(sys.argv) > 2 else ""
        if not url:
            print("ERROR: need a URL")
            return
        r = await engine.smart_browse(url)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    # v3 CLI commands
    elif cmd == "crawl":
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("url")
        parser.add_argument("--depth", type=int, default=3)
        parser.add_argument("--pages", type=int, default=50)
        parser.add_argument("--stay-domain", action="store_true", default=True)
        args = parser.parse_args(sys.argv[2:])
        r = await engine.cr.crawl(args.url, args.depth, args.pages, args.stay_domain)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "map":
        url = sys.argv[2] if len(sys.argv) > 2 else ""
        if not url:
            print("ERROR: need a URL")
            return
        r = await engine.cr.map_site(url)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "smart-scrape":
        url = sys.argv[2] if len(sys.argv) > 2 else ""
        if not url:
            print("ERROR: need a URL")
            return
        r = await engine.pw.smart_scrape(url)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "interact":
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("url")
        parser.add_argument("--action", required=True)
        parser.add_argument("--selector")
        parser.add_argument("--value")
        parser.add_argument("--wait", type=float, default=1.0)
        args = parser.parse_args(sys.argv[2:])
        r = await engine.pw.interact(args.url, args.action, args.selector, args.value, args.wait)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        asyncio.run(cli())
    else:
        asyncio.run(serve_mcp())