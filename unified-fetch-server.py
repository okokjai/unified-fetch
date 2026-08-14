#!/usr/bin/env python3
"""
unified-fetch MCP server — V2 architecture.

Design (see ARCHITECTURE.md):
  HTTP-first + browser auto-upgrade (UnifiedBrowser CDP core)
  parallel search + quorum + consensus + diversity
  actionable signals (content_ok / page_type / next_action / is_stale)
  connect-time instructions · BM25 focus · smart SQLite cache
  zero-config: Tier 0 = mcp + websockets only; everything else optional try-import

MCP tools (14):
  search / scrape / status / deep_search / parallel_scrape / crawl / map /
  smart_browse / browser_navigate / browser_get_content / browser_screenshot /
  browser_evaluate / browser_interact / browser_status
"""

import asyncio
import base64
import hashlib
import json
import logging
import math
import os
import random
import re
import sqlite3
import sys
import time
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from urllib.parse import urlparse

# ── Optional heavy deps ─────────────────────────────────────────────
try:
    import httpx
    HTTPX = True
except ImportError:
    HTTPX = False

try:
    from duckduckgo_search import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    DDGS_AVAILABLE = False

try:
    from googlesearch import search as gsearch
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False

try:
    import trafilatura
    TRAFILATURA_AVAILABLE = True
except ImportError:
    TRAFILATURA_AVAILABLE = False

try:
    from newspaper import Article
    NEWSPAPER_AVAILABLE = True
except ImportError:
    NEWSPAPER_AVAILABLE = False

try:
    import readability
    READABILITY_AVAILABLE = True
except ImportError:
    READABILITY_AVAILABLE = False

try:
    from justext import justext
    from lxml.html import fromstring
    JUSTEXT_AVAILABLE = True
except ImportError:
    JUSTEXT_AVAILABLE = False

try:
    from master_fetch.server import MasterFetchServer
    HOUND_AVAILABLE = True
except ImportError:
    HOUND_AVAILABLE = False

try:
    import playwright.async_api
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

try:
    import curl_cffi.requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False

# ── Config ──────────────────────────────────────────────────────────

@dataclass
class Config:
    user_agents: list = field(default_factory=lambda: [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    ])
    headers: dict = field(default_factory=lambda: {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8,zh;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    })
    timeouts: dict = field(default_factory=lambda: {
        "search": 12.0, "scrape": 30.0, "direct": 20.0, "browser": 45.0,
    })
    max_content_length: int = 500_000
    max_results_default: int = 5
    parallel_cap: int = 5
    # cache
    cache_db: str = os.path.join(os.path.expanduser("~"), ".unified-fetch-cache.db")
    cache_default_ttl: int = 3600          # 1 hour (Hound default)
    cache_size_cap_bytes: int = 500 * 1024 * 1024
    # browser
    browser_data_dir: str = os.environ.get(
        "UNIFIED_BROWSER_DIR",
        os.path.join(os.path.expanduser("~"), ".unified-browser"))
    browser_identity_count: int = 3
    browser_max_instances: int = 3
    browser_human_behavior: bool = True


config = Config()

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("unified-fetch-v2")


# ═══════════════════════════════════════════════════════════════════
# HTTP HELPERS (from v1)
# ═══════════════════════════════════════════════════════════════════

def _random_ua() -> str:
    return random.choice(config.user_agents)


def _build_headers(url: str | None = None) -> dict:
    h = {**config.headers, "User-Agent": _random_ua()}
    if url:
        h["Referer"] = urlparse(url).scheme + "://" + (urlparse(url).netloc or "")
    return h


async def _anti_delay():
    await asyncio.sleep(random.uniform(0.3, 0.9))


async def _request(method: str, url: str, *, timeout: float | None = None,
                   retry: int | None = None, **kwargs) -> tuple[Any | None, dict | None]:
    """HTTP GET/POST with retry + Retry-After respect + circuit-friendly errors."""
    if not HTTPX:
        return None, {"code": "NO_HTTPX", "message": "httpx not installed", "retryable": False}
    timeout = timeout or config.timeouts["scrape"]
    retry = 2 if retry is None else retry
    for attempt in range(retry + 1):
        try:
            kwargs.setdefault("headers", _build_headers())
            if method == "GET":
                r = await httpx.AsyncClient(follow_redirects=True,
                                            timeout=timeout).get(url, **kwargs)
            else:
                r = await httpx.AsyncClient(follow_redirects=True,
                                            timeout=timeout).request(method, url, **kwargs)
            if r.status_code in (429, 503) and "Retry-After" in r.headers:
                try:
                    wait = float(r.headers["Retry-After"])
                    await asyncio.sleep(min(wait, 5.0))
                except ValueError:
                    pass
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retry:
                await asyncio.sleep(0.8 * (attempt + 1))
                continue
            return r, None
        except httpx.HTTPStatusError as e:
            return None, {"code": "HTTP_STATUS", "message": str(e), "retryable": True}
        except httpx.TimeoutException as e:
            if attempt < retry:
                await asyncio.sleep(0.8 * (attempt + 1))
                continue
            return None, {"code": "TIMEOUT", "message": str(e), "retryable": True}
        except Exception as e:
            if attempt < retry:
                await asyncio.sleep(0.8 * (attempt + 1))
                continue
            return None, {"code": "CONNECT_ERROR", "message": str(e)[:200], "retryable": True}
    return None, {"code": "UNKNOWN", "message": "request failed", "retryable": True}


# ═══════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER (from v1)
# ═══════════════════════════════════════════════════════════════════

class CircuitBreaker:
    def __init__(self, name: str, max_failures: int = 3, cooldown: float = 60.0):
        self.name = name
        self.max_failures = max_failures
        self.cooldown = cooldown
        self._failures = 0
        self._open_since: float | None = None

    @property
    def is_open(self) -> bool:
        if self._open_since is None:
            return False
        return (time.monotonic() - self._open_since) < self.cooldown

    def record_success(self):
        self._failures = 0
        self._open_since = None

    def record_failure(self):
        self._failures += 1
        if self._failures >= self.max_failures:
            self._open_since = time.monotonic()

    def state(self) -> str:
        if self._open_since is not None and not self.is_open:
            return "recovering"
        if self.is_open:
            return "open"
        return "closed"


# ═══════════════════════════════════════════════════════════════════
# ENGINE ABSTRACTION (pluggable core)
# ═══════════════════════════════════════════════════════════════════

class Engine:
    name: str = "engine"
    optional: bool = False          # True = Tier 1 (optional)

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def is_healthy(cls) -> bool:
        return True


@dataclass
class ScrapeResult:
    """Actionable-signal scrape result (Hound-inspired)."""
    url: str
    ok: bool
    content: str = ""
    title: str = ""
    content_ok: bool = False
    page_type: str = "unknown"      # article | list | js_shell | pdf | error | empty
    engine_used: str = ""
    engine_chain: list = field(default_factory=list)
    next_action: str = "none"       # switch_source | upgrade_browser | retry | give_up
    is_stale: bool = False
    status_code: int = 0
    duration_ms: int = 0
    warnings: list = field(default_factory=list)
    error: str = ""
    error_type: str = ""

    def to_dict(self, max_content: int = 300_000) -> dict:
        d = asdict(self)
        d["content"] = d["content"][:max_content]
        d["content_length"] = len(self.content)
        return d


class ScrapeEngine(Engine):
    async def scrape(self, url: str, options: dict | None = None) -> ScrapeResult:
        raise NotImplementedError


@dataclass
class SearchResult:
    url: str
    title: str = ""
    snippet: str = ""
    source_type: str = "web"        # web | code | docs | news | pkg | academic
    source_engine: str = ""
    consensus: int = 1              # how many engines returned this URL
    extra: dict = field(default_factory=dict)


class SearchEngine(Engine):
    async def search(self, query: str, count: int = 5) -> list[SearchResult]:
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════════
# PAGE-TYPE DETECTION + BM25 FOCUS (A7, A5)
# ═══════════════════════════════════════════════════════════════════

_EMPTY_TITLE_PATTERNS = ["404", "not found", "error", "access denied", "forbidden"]


def detect_page_type(status: int, content: str, title: str = "",
                     headers: dict | None = None) -> str:
    """Classify a fetched page: article/list/js_shell/pdf/error/empty."""
    ct = (headers or {}).get("content-type", "").lower()
    if status >= 400:
        return "error"
    if content.lstrip().startswith("%PDF") or "pdf" in ct:
        return "pdf"
    stripped = content.strip()
    if len(stripped) < 50:
        if title:
            return "js_shell"       # title rendered, body JS-driven
        return "error"
    t = (title or "").lower()
    if any(p in t for p in _EMPTY_TITLE_PATTERNS) and len(stripped) < 500:
        return "error"
    # list page: high link density, low text density
    link_like = len(re.findall(r"^[\*\-•·]\s+\[.*?\]\(https?://", stripped, re.M))
    text_chars = len(stripped)
    if text_chars > 300 and link_like >= 10 and (link_like * 40) > text_chars:
        return "list"
    return "article"


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9一-鿿]{2,}", text.lower())


def bm25_focus(text: str, query: str, top_k: int = 5) -> list[str]:
    """Return the top-k most relevant paragraphs (BM25-lite, in original order)."""
    q_terms = set(_tokenize(query))
    if not q_terms or len(text) < 500:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) > 80]
    if not paras:
        return []
    scores = []
    for i, para in enumerate(paras):
        toks = _tokenize(para)
        if not toks:
            continue
        # term overlap with small length penalty (BM25-ish)
        overlap = sum(1 for t in q_terms if t in toks)
        if overlap:
            scores.append((overlap / max(len(q_terms), 1), len(toks), i, para))
    scores.sort(key=lambda s: (-s[0], s[1]))
    keep = {s[2] for s in scores[:top_k]}
    return [para for i, para in enumerate(paras) if i in keep]


# ═══════════════════════════════════════════════════════════════════
# SMART CACHE (A6: WAL + bad-content-never-cached + size cap)
# ═══════════════════════════════════════════════════════════════════

class SQLiteCache:
    def __init__(self, db_path: str = "", size_cap: int = 0):
        self._db = db_path or config.cache_db
        self._size_cap = size_cap or config.cache_size_cap_bytes
        os.makedirs(os.path.dirname(self._db) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self._db, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")           # WAL for concurrency
        self._conn.execute("""CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL)""")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_expires ON cache(expires_at)")
        self._conn.commit()

    @staticmethod
    def key(*parts: str) -> str:
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    def get(self, key: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT value FROM cache WHERE key=? AND expires_at > ?",
            (key, time.time())).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def set(self, key: str, value: dict, ttl: int = 0) -> None:
        """Store a value. Bad content is never cached (caller checks content_ok)."""
        ttl = ttl or config.cache_default_ttl
        raw = json.dumps(value, ensure_ascii=False, default=str)
        now = time.time()
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)",
                (key, raw, len(raw.encode()), now, now + ttl))
            self._conn.commit()
            self._evict_if_needed()
        except Exception as e:
            logger.debug("cache set failed: %s", e)

    def _evict_if_needed(self) -> None:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(size_bytes),0), COALESCE(COUNT(*),0) FROM cache").fetchone()
        total, count = row
        if total <= self._size_cap:
            return
        # evict oldest first until under cap
        while total > self._size_cap:
            oldest = self._conn.execute(
                "SELECT key FROM cache ORDER BY created_at ASC LIMIT 1").fetchone()
            if not oldest:
                break
            self._conn.execute("DELETE FROM cache WHERE key=?", (oldest[0],))
            total -= len(oldest[0])
        self._conn.commit()

    def clear(self) -> None:
        self._conn.execute("DELETE FROM cache")
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection (server keeps it open for lifetime)."""
        try:
            self._conn.close()
        except Exception:
            pass

    def stats(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes),0) FROM cache").fetchone()
        return {"entries": row[0], "size_mb": round(row[1] / 1_048_576, 1),
                "db": self._db}


# ═══════════════════════════════════════════════════════════════════
# HTTP SCRAPE ENGINES (L0-L2, Tier 0)
# ═══════════════════════════════════════════════════════════════════

class NewspaperEngine(ScrapeEngine):
    name = "newspaper"

    @classmethod
    def is_available(cls) -> bool:
        return NEWSPAPER_AVAILABLE

    async def scrape(self, url: str, options: dict | None = None) -> ScrapeResult:
        loop = asyncio.get_event_loop()
        try:
            def _run():
                a = Article(url)
                a.download()
                a.parse()
                return a.title or "", a.text or ""
            title, text = await loop.run_in_executor(None, _run)
            if not text.strip():
                return ScrapeResult(url=url, ok=False, content="", title=title,
                                    content_ok=False, engine_used=self.name,
                                    page_type="empty")
            return ScrapeResult(url=url, ok=True, content=text.strip(), title=title,
                                content_ok=True, engine_used=self.name,
                                page_type=detect_page_type(200, text, title))
        except Exception as e:
            return ScrapeResult(url=url, ok=False, error=str(e)[:200],
                                error_type="engine_error", engine_used=self.name)


class TrafilaturaEngine(ScrapeEngine):
    name = "trafilatura"

    @classmethod
    def is_available(cls) -> bool:
        return TRAFILATURA_AVAILABLE

    async def scrape(self, url: str, options: dict | None = None) -> ScrapeResult:
        try:
            html = await loop_exec_fetch(url)   # defined below
            if html is None:
                return ScrapeResult(url=url, ok=False, engine_used=self.name,
                                    error="fetch failed", error_type="network_error")
            text = await asyncio.get_event_loop().run_in_executor(
                None, lambda: trafilatura.extract(html) or "")
            if not text.strip():
                return ScrapeResult(url=url, ok=False, content="", engine_used=self.name,
                                    content_ok=False, page_type="empty")
            title = ""
            m = re.search(r"<title[^>]*>(.*?)</title>", html[:20_000], re.I | re.S)
            if m:
                title = m.group(1).strip()
            return ScrapeResult(url=url, ok=True, content=text.strip(), title=title,
                                content_ok=True, engine_used=self.name,
                                page_type=detect_page_type(200, text, title))
        except Exception as e:
            return ScrapeResult(url=url, ok=False, error=str(e)[:200],
                                error_type="engine_error", engine_used=self.name)


class ReadabilityEngine(ScrapeEngine):
    name = "readability"

    @classmethod
    def is_available(cls) -> bool:
        return READABILITY_AVAILABLE

    async def scrape(self, url: str, options: dict | None = None) -> ScrapeResult:
        try:
            html = await loop_exec_fetch(url)
            if html is None:
                return ScrapeResult(url=url, ok=False, engine_used=self.name,
                                    error="fetch failed", error_type="network_error")

            def _run():
                doc = readability.Document(html)
                return doc.title(), doc.summary()

            title, summary = await asyncio.get_event_loop().run_in_executor(None, _run)
            text = re.sub(r"<[^>]+>", " ", summary)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) < 50:
                return ScrapeResult(url=url, ok=False, content=text, title=title,
                                    engine_used=self.name, content_ok=False,
                                    page_type="empty")
            return ScrapeResult(url=url, ok=True, content=text, title=title,
                                content_ok=True, engine_used=self.name,
                                page_type=detect_page_type(200, text, title))
        except Exception as e:
            return ScrapeResult(url=url, ok=False, error=str(e)[:200],
                                error_type="engine_error", engine_used=self.name)


class JusTextEngine(ScrapeEngine):
    name = "justext"

    @classmethod
    def is_available(cls) -> bool:
        return JUSTEXT_AVAILABLE

    async def scrape(self, url: str, options: dict | None = None) -> ScrapeResult:
        try:
            html = await loop_exec_fetch(url)
            if html is None:
                return ScrapeResult(url=url, ok=False, engine_used=self.name,
                                    error="fetch failed", error_type="network_error")

            def _run():
                paras = justext(fromstring(html), stoplist="English")
                return "\n\n".join(p.text for p in paras
                                   if not p.is_boilerplate and len(p.text) > 30)

            text = await asyncio.get_event_loop().run_in_executor(None, _run)
            if len(text.strip()) < 50:
                return ScrapeResult(url=url, ok=False, content=text.strip(),
                                    engine_used=self.name, content_ok=False,
                                    page_type="empty")
            return ScrapeResult(url=url, ok=True, content=text.strip(),
                                content_ok=True, engine_used=self.name,
                                page_type=detect_page_type(200, text, ""))
        except Exception as e:
            return ScrapeResult(url=url, ok=False, error=str(e)[:200],
                                error_type="engine_error", engine_used=self.name)


class DirectEngine(ScrapeEngine):
    """Ultimate fallback: raw HTTP + naive HTML strip."""
    name = "direct"

    @classmethod
    def is_available(cls) -> bool:
        return HTTPX

    async def scrape(self, url: str, options: dict | None = None) -> ScrapeResult:
        r, err = await _request("GET", url, timeout=config.timeouts["direct"])
        if r is None:
            return ScrapeResult(url=url, ok=False, engine_used=self.name,
                                error=(err or {}).get("message", "fetch failed"),
                                error_type="network_error")
        if r.status_code >= 400:
            return ScrapeResult(url=url, ok=False, engine_used=self.name,
                                status_code=r.status_code,
                                error=f"HTTP {r.status_code}", error_type="http_error")
        ct = r.headers.get("content-type", "")
        text = ""
        if "application/json" in ct.lower():
            try:
                text = json.dumps(r.json(), ensure_ascii=False, indent=2)[:config.max_content_length]
            except Exception:
                text = r.text
        else:
            html = r.text
            text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
        title = ""
        m = re.search(r"<title[^>]*>(.*?)</title>", (r.text or "")[:20_000], re.I | re.S)
        if m:
            title = m.group(1).strip()
        return ScrapeResult(url=url, ok=True, content=text[:config.max_content_length],
                            title=title, content_ok=bool(text.strip()),
                            engine_used=self.name, status_code=r.status_code,
                            page_type=detect_page_type(r.status_code, text, title, r.headers))


async def loop_exec_fetch(url: str) -> str | None:
    """Fetch raw HTML for extraction engines (returns HTML or None on failure)."""
    r, err = await _request("GET", url, timeout=config.timeouts["scrape"])
    if r is None or r.status_code >= 400:
        return None
    ct = r.headers.get("content-type", "").lower()
    if "pdf" in ct:
        return "%PDF"            # signal PDF without loading binary
    return r.text


# ═══════════════════════════════════════════════════════════════════
# SEARCH ENGINES
# ═══════════════════════════════════════════════════════════════════

class DuckDuckGoEngine(SearchEngine):
    name = "duckduckgo"

    @classmethod
    def is_available(cls) -> bool:
        return DDGS_AVAILABLE

    async def search(self, query: str, count: int = 5) -> list[SearchResult]:
        loop = asyncio.get_event_loop()
        try:
            results = await loop.run_in_executor(
                None, lambda: list(DDGS().text(query, max_results=count)))
            return [SearchResult(url=r.get("href", ""), title=r.get("title", ""),
                                 snippet=r.get("body", ""),
                                 source_engine=self.name) for r in results]
        except Exception:
            return []


class GoogleEngine(SearchEngine):
    """Tier 1 — googlesearch-python (Google frequently blocks; fallback engine)."""
    name = "googlesearch"
    optional = True

    @classmethod
    def is_available(cls) -> bool:
        return GOOGLE_AVAILABLE

    async def search(self, query: str, count: int = 5) -> list[SearchResult]:
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(
                None, lambda: list(gsearch(query, num_results=count, advanced=True)))
            out = []
            for r in raw:
                try:
                    out.append(SearchResult(url=r.url or "", title=r.title or "",
                                            snippet=r.description or "",
                                            source_engine=self.name))
                except Exception:
                    out.append(SearchResult(url=str(r), source_engine=self.name))
            return out
        except Exception:
            return []


def _parse_ddg_html(html: str, count: int, engine: str) -> list[SearchResult]:
    """Parse DDG HTML results. Handles protocol-relative uddg redirect links."""
    out = []
    for a in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                         html, re.DOTALL):
        raw_url = a.group(1)
        # DDG wraps outbound links: //duckduckgo.com/l/?uddg=<encoded>&rut=...
        if "uddg=" in raw_url:
            m = re.search(r"[?&]uddg=([^&]+)", raw_url)
            if not m:
                continue
            url = urllib.parse.unquote(m.group(1))
        elif raw_url.startswith("//"):
            url = "https:" + raw_url
        else:
            url = raw_url
        if not url.startswith("http"):
            continue
        title = re.sub(r"<[^>]+>", "", a.group(2)).strip()
        out.append(SearchResult(url=url, title=title, snippet="",
                                source_engine=engine))
        if len(out) >= count:
            break
    return out


class DirectSearchEngine(SearchEngine):
    """Ultimate fallback: parse DDG HTML directly."""
    name = "direct-ddgo"

    @classmethod
    def is_available(cls) -> bool:
        return HTTPX

    async def search(self, query: str, count: int = 5) -> list[SearchResult]:
        r, err = await _request(
            "POST", "https://html.duckduckgo.com/html/",
            data={"q": query}, headers=config.headers,
            timeout=config.timeouts["direct"])
        if r is None or r.status_code >= 400:
            return []
        return _parse_ddg_html(r.text, count, self.name)


class GitHubEngine(SearchEngine):
    name = "github"
    source_type = "code"

    @classmethod
    def is_available(cls) -> bool:
        return HTTPX

    async def search(self, query: str, count: int = 5) -> list[SearchResult]:
        r, err = await _request(
            "GET", f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}&per_page={count}&sort=stars",
            headers={**config.headers, "Accept": "application/vnd.github.v3+json"},
            timeout=15, retry=3)
        if r is None or r.status_code >= 400:
            return []
        items = r.json().get("items", [])[:count]
        return [SearchResult(url=x.get("html_url", ""), title=x.get("full_name", ""),
                             snippet=x.get("description", "") or "",
                             source_type="code", source_engine=self.name,
                             extra={"stars": x.get("stargazers_count", 0)})
                for x in items]


class NPMSearchEngine(SearchEngine):
    name = "npm"
    source_type = "pkg"

    @classmethod
    def is_available(cls) -> bool:
        return HTTPX

    async def search(self, query: str, count: int = 5) -> list[SearchResult]:
        r, err = await _request(
            "GET", f"https://registry.npmjs.org/-/v1/search?text={urllib.parse.quote(query)}&size={count}",
            headers=config.headers, timeout=15)
        if r is None or r.status_code >= 400:
            return []
        out = []
        for obj in r.json().get("objects", [])[:count]:
            pkg = obj.get("package", {})
            out.append(SearchResult(url=pkg.get("links", {}).get("npm", ""),
                                    title=pkg.get("name", ""),
                                    snippet=pkg.get("description", "") or "",
                                    source_type="pkg", source_engine=self.name,
                                    extra={"version": pkg.get("version", "")}))
        return out


class MDNSearchEngine(SearchEngine):
    name = "mdn"
    source_type = "docs"

    @classmethod
    def is_available(cls) -> bool:
        return HTTPX

    async def search(self, query: str, count: int = 5) -> list[SearchResult]:
        r, err = await _request(
            "GET", f"https://developer.mozilla.org/api/v1/search?q={urllib.parse.quote(query)}&limit={count}",
            headers=config.headers, timeout=15)
        if r is None or r.status_code >= 400:
            return []
        out = []
        for doc in r.json().get("documents", [])[:count]:
            out.append(SearchResult(url="https://developer.mozilla.org" + doc.get("mdn_url", ""),
                                    title=doc.get("title", ""),
                                    snippet=doc.get("summary", "") or "",
                                    source_type="docs", source_engine=self.name))
        return out


# ═══════════════════════════════════════════════════════════════════
# HOUND ENGINE (Tier 1 — optional. deep anti-bot / PDF OCR / neural outsourcing)
# ═══════════════════════════════════════════════════════════════════

class HoundEngine(ScrapeEngine):
    """Adapters to master-fetch (Hound). Tier 1 — auto-enables if installed."""
    name = "hound"
    optional = True

    def __init__(self):
        self._server = None
        self._lock = asyncio.Lock()

    @classmethod
    def is_available(cls) -> bool:
        return HOUND_AVAILABLE

    async def _srv(self):
        async with self._lock:
            if self._server is None:
                self._server = MasterFetchServer()
        return self._server

    async def scrape(self, url: str, options: dict | None = None) -> ScrapeResult:
        if not HOUND_AVAILABLE:
            return ScrapeResult(url=url, ok=False, engine_used=self.name,
                                error="hound not installed", error_type="not_installed")
        try:
            s = await self._srv()
            kwargs = {"extraction_type": "markdown"}
            r = await s.smart_fetch(url, **kwargs)
            text = "\n".join(r.content) if r.content else ""
            return ScrapeResult(url=url, ok=r.content_ok, content=text,
                                title=r.title or "",
                                content_ok=bool(text.strip()),
                                engine_used=self.name,
                                status_code=getattr(r, "status", 0) or 0,
                                page_type=detect_page_type(getattr(r, "status", 200) or 200,
                                                           text, r.title or ""),
                                error=getattr(r, "error", "") or "")
        except Exception as e:
            return ScrapeResult(url=url, ok=False, engine_used=self.name,
                                error=str(e)[:200], error_type="engine_error")

    async def search(self, query: str, count: int = 5) -> list[SearchResult]:
        if not HOUND_AVAILABLE:
            return []
        try:
            s = await self._srv()
            r = await s.smart_search(query, max_results=count)
            d = r.model_dump()
            return [SearchResult(url=x.get("url", ""), title=x.get("title", ""),
                                 snippet=x.get("snippet", ""),
                                 source_engine=self.name)
                    for x in d.get("results", [])]
        except Exception:
            return []


# ═══════════════════════════════════════════════════════════════════
# UNIFIEDBROWSER ENGINE (CORE — CDP-native browser)
# ═══════════════════════════════════════════════════════════════════

_browser_lock = asyncio.Lock()
_browser: "UnifiedBrowser | None" = None


def browser_available() -> bool:
    """Check if a browser binary exists (Chrome/Edge/Playwright chromium)."""
    try:
        from browser.cdp_driver import CDPTransport
        path = CDPTransport().find_chrome()
        return bool(path)
    except Exception:
        return False


async def get_browser():
    """Lazy-init the UnifiedBrowser (D7: server starts <1s, browser on first use)."""
    global _browser
    if _browser is not None:
        return _browser
    async with _browser_lock:
        if _browser is not None:
            return _browser
        from browser.unified_browser import UnifiedBrowser, UnifiedBrowserConfig
        os.makedirs(config.browser_data_dir, exist_ok=True)
        cfg = UnifiedBrowserConfig(
            data_dir=config.browser_data_dir,
            identity_count=config.browser_identity_count,
            max_instances=config.browser_max_instances,
            human_behavior=config.browser_human_behavior,
        )
        _browser = UnifiedBrowser(cfg)
        try:
            await _browser.initialize()
        except Exception as e:
            logger.warning("UnifiedBrowser init failed: %s", e)
            _browser = None
            raise
        logger.info("UnifiedBrowser ready")
        return _browser


class BrowserScrapeEngine(ScrapeEngine):
    """UnifiedBrowser as a scrape engine (the final weapon in the chain)."""
    name = "unified_browser"

    @classmethod
    def is_available(cls) -> bool:
        return browser_available()

    async def scrape(self, url: str, options: dict | None = None) -> ScrapeResult:
        try:
            ub = await get_browser()
        except Exception as e:
            return ScrapeResult(url=url, ok=False, engine_used=self.name,
                                error=f"browser unavailable: {str(e)[:120]}",
                                error_type="no_browser")
        options = options or {}
        start = time.monotonic()
        try:
            r = await ub.fetch(url, wait_until=options.get("wait_until", "load"),
                               timeout=options.get("timeout", config.timeouts["browser"]),
                               return_html=False,
                               human_behavior=config.browser_human_behavior)
            dur = int((time.monotonic() - start) * 1000)
            if not r.ok:
                bot = r.bot_detection.get("system", "")
                return ScrapeResult(url=url, ok=False, engine_used=self.name,
                                    content=r.content, title=r.title,
                                    content_ok=False, duration_ms=dur,
                                    error=r.error or f"bot-blocked ({bot})",
                                    error_type="bot_blocked" if bot else "fetch_error",
                                    next_action="switch_source",
                                    page_type=detect_page_type(
                                        r.status_code or 403, r.content, r.title))
            return ScrapeResult(url=url, ok=True, content=r.content, title=r.title,
                                content_ok=bool(r.content.strip()),
                                engine_used=self.name, duration_ms=dur,
                                page_type=detect_page_type(200, r.content, r.title))
        except Exception as e:
            return ScrapeResult(url=url, ok=False, engine_used=self.name,
                                error=str(e)[:200], error_type="engine_error",
                                duration_ms=int((time.monotonic() - start) * 1000))


class BrowserSearchEngine(SearchEngine):
    """Last-resort search via the stealth browser (when all HTTP search blocked)."""
    name = "browser_search"

    @classmethod
    def is_available(cls) -> bool:
        return browser_available()

    async def search(self, query: str, count: int = 5) -> list[SearchResult]:
        try:
            ub = await get_browser()
        except Exception:
            return []
        try:
            await ub.navigate(
                f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}",
                wait_until="domcontentloaded")
            html = await ub.get_html()
            return _parse_ddg_html(html, count, self.name)
        except Exception:
            return []


# ═══════════════════════════════════════════════════════════════════
# ENGINE REGISTRY (order = priority)
# ═══════════════════════════════════════════════════════════════════

def _available(chain: list) -> list:
    return [e for e in chain if e.is_available()]


SCRAPE_CHAIN: list = [
    HoundEngine,            # Tier 1 optional (deep anti-bot / PDF / neural) — use first if available
    NewspaperEngine,
    TrafilaturaEngine,
    ReadabilityEngine,
    JusTextEngine,
    DirectEngine,           # last resort HTTP
    BrowserScrapeEngine,    # CORE: UnifiedBrowser (upgrade target)
]

SEARCH_CHAIN: list = [
    DuckDuckGoEngine,
    GoogleEngine,           # Tier 1 optional
    HoundEngine,            # Tier 1 optional
    DirectSearchEngine,
    BrowserSearchEngine,    # CORE: last resort
]

DEEP_SOURCES: dict = {
    "github": GitHubEngine,
    "npm": NPMSearchEngine,
    "mdn": MDNSearchEngine,
}


# ═══════════════════════════════════════════════════════════════════
# UNIFIED FACADE
# ═══════════════════════════════════════════════════════════════════

class Unified:
    def __init__(self):
        self._breakers = {c.name: CircuitBreaker(c.name) for c in SCRAPE_CHAIN + SEARCH_CHAIN}
        self._sem = asyncio.Semaphore(config.parallel_cap)
        self._cache = SQLiteCache()

    # ── helpers ────────────────────────────────────────────────────

    def _healthy(self, engine: type) -> bool:
        cb = self._breakers.get(engine.name)
        return cb is None or not cb.is_open

    def _record(self, engine_name: str, ok: bool):
        cb = self._breakers.get(engine_name)
        if cb:
            cb.record_success() if ok else cb.record_failure()

    def _next_action_for(self, reason: str) -> str:
        return {"bot_blocked": "switch_source", "http_error": "upgrade_browser",
                "no_browser": "give_up", "timeout": "retry"}.get(reason, "retry")

    # ── search: parallel + quorum + consensus + diversity ─────────

    async def search(self, query: str, max_results: int = 5) -> dict:
        start = time.monotonic()
        chain = _available(SEARCH_CHAIN)
        primary = [c for c in chain if c in (DuckDuckGoEngine, GoogleEngine, HoundEngine)]
        primary = [c for c in primary if self._healthy(c)]
        fallbacks = [c for c in chain if c not in primary]

        per_engine = {}

        async def _run_one(engine_cls):
            engine = engine_cls()
            try:
                results = await asyncio.wait_for(
                    engine.search(query, max_results), timeout=config.timeouts["search"])
                per_engine[engine_cls.name] = results
            except (asyncio.TimeoutError, Exception):
                per_engine[engine_cls.name] = []

        await asyncio.gather(*[_run_one(c) for c in primary]) if primary else None

        # quorum: need at least 2 contributing (or 1 if only 1 primary available)
        contributors = {k: v for k, v in per_engine.items() if v}
        if not contributors:
            # all primaries failed → fallback chain (direct → browser search)
            for cls in fallbacks:
                if not self._healthy(cls):
                    continue
                engine = cls()
                try:
                    results = await asyncio.wait_for(
                        engine.search(query, max_results), timeout=config.timeouts["search"])
                except Exception:
                    results = []
                self._record(cls.name, bool(results))
                if results:
                    contributors = {cls.name: results}
                    break

        # merge + consensus
        merged: dict[str, SearchResult] = {}
        for engine_name, results in contributors.items():
            for r in results:
                if not r.url or r.url.startswith("http") is False:
                    continue
                key = r.url.rstrip("/")
                if key in merged:
                    merged[key].consensus += 1
                    if not merged[key].snippet and r.snippet:
                        merged[key].snippet = r.snippet
                    if not merged[key].title:
                        merged[key].title = r.title
                else:
                    r.consensus = 1
                    merged[key] = r

        # rank: consensus desc → engine priority (DDG first) → title presence
        priority = {e.name: i for i, e in enumerate(SEARCH_CHAIN)}
        ranked = sorted(merged.values(),
                        key=lambda r: (-r.consensus,
                                       priority.get(r.source_engine, 99),
                                       -len(r.title or "")))

        # diversity: max 2 per domain in top results
        seen_domains: dict[str, int] = {}
        final: list[SearchResult] = []
        for r in ranked:
            dom = urlparse(r.url).netloc or "?"
            if seen_domains.get(dom, 0) >= 2:
                continue
            seen_domains[dom] = seen_domains.get(dom, 0) + 1
            final.append(r)
            if len(final) >= max_results:
                break

        dur = int((time.monotonic() - start) * 1000)
        ok = bool(final)
        return {
            "ok": ok,
            "query": query,
            "total": len(final),
            "results": [{"url": r.url, "title": r.title, "snippet": r.snippet,
                         "source_type": r.source_type, "source_engine": r.source_engine,
                         "consensus": r.consensus,
                         **{k: v for k, v in r.extra.items()}} for r in final],
            "engines_consensus": sorted({r.source_engine for r in final}),
            "quorum": {"contributors": sorted(contributors.keys()),
                       "needed": min(2, max(1, len(primary) or 1)),
                       "met": len(contributors) >= min(2, max(1, len(primary) or 1))},
            "engine_status": {e.name: "ok" if per_engine.get(e.name) else ("blocked" if self._breakers.get(e.name, CircuitBreaker(e.name)).is_open else "empty") for e in primary},
            "metrics": {"total_ms": dur},
            "cache_hit": False,
        }

    # ── scrape: HTTP-first → auto-upgrade to browser ──────────────

    async def scrape(self, url: str, prefer_browser: bool = False,
                     focus: str = "", require_fresh: bool = False,
                     cache_ttl: int = 0, wait_until: str = "load",
                     timeout: float | None = None) -> dict:
        start = time.monotonic()
        ck = SQLiteCache.key("scrape", url, "text")
        if not require_fresh:
            cached = self._cache.get(ck)
            if cached:
                cached["cache_hit"] = True
                cached["duration_ms"] = 0
                return cached

        http_chain = _available([c for c in SCRAPE_CHAIN if c is not BrowserScrapeEngine])
        http_chain = [c for c in http_chain if self._healthy(c)]
        browser_on = browser_available()

        warnings: list = []
        engine_chain: list = []
        blocked_flags: list = []

        if prefer_browser:
            if not browser_on:
                return {"ok": False, "error": "prefer_browser but no browser found",
                        "error_type": "no_browser", "next_action": "give_up",
                        "cache_hit": False}
            r = await self._scrape_with_browser(url, wait_until, timeout)
            engine_chain.append("unified_browser")
            return self._finish_scrape(r, url, focus, engine_chain, start, ck)

        # HTTP chain first (~1s)
        for cls in http_chain:
            if not self._healthy(cls):
                continue
            engine = cls()
            opts = {"timeout": timeout} if timeout else {}
            try:
                r = await asyncio.wait_for(engine.scrape(url, opts),
                                           timeout=config.timeouts["scrape"])
            except asyncio.TimeoutError:
                r = ScrapeResult(url=url, ok=False, engine_used=cls.name,
                                 error="timeout", error_type="timeout")
            except Exception as e:
                r = ScrapeResult(url=url, ok=False, engine_used=cls.name,
                                 error=str(e)[:200], error_type="engine_error")
            engine_chain.append(cls.name)
            self._record(cls.name, r.ok and r.content_ok)

            if r.error_type in ("http_error", "network_error", "bot_blocked"):
                blocked_flags.append(f"{cls.name}:{r.error_type}")
            if r.content_ok:
                warnings.extend(self._quality_warnings(r))
                return self._finish_scrape(r, url, focus, engine_chain, start, ck)
            if r.content.strip():
                warnings.append({"engine": cls.name, "code": "LOW_CONTENT",
                                 "message": f"content short ({len(r.content.strip())} chars)"})
                blocked_flags.append(f"{cls.name}:low_content")

        # HTTP all failed → upgrade to browser (the final weapon)
        if browser_on:
            r = await self._scrape_with_browser(url, wait_until, timeout)
            engine_chain.append("unified_browser")
            if r.ok and r.content_ok:
                return self._finish_scrape(r, url, focus, engine_chain, start, ck)
            # CF challenge wall? Escalate to headful (real window) — passes SO-class.
            if r.error_type in ("bot_blocked", "fetch_error"):
                r2 = await self._scrape_with_browser(url, wait_until, timeout,
                                                     prefer_headful=True)
                if r2.ok and r2.content_ok:
                    engine_chain.append("unified_browser(headful)")
                    r2.engine_chain = list(engine_chain)
                    return self._finish_scrape(r2, url, focus, engine_chain, start, ck)
                blocked_flags.append(f"headful:{r2.error_type or 'failed'}")
            blocked_flags.append(f"unified_browser:{r.error_type or 'failed'}")

        reason = "blocked" if blocked_flags else "all_engines_failed"
        return {
            "ok": False, "url": url, "error": "; ".join(blocked_flags) or "all engines failed",
            "error_type": reason, "engine_chain": engine_chain,
            "next_action": "switch_source" if reason == "blocked" else "give_up",
            "warnings": warnings, "cache_hit": False,
            "metrics": {"total_ms": int((time.monotonic() - start) * 1000)},
        }

    async def _scrape_with_browser(self, url: str, wait_until: str,
                                   timeout: float | None,
                                   prefer_headful: bool = False) -> ScrapeResult:
        try:
            ub = await get_browser()
            if prefer_headful:
                # SO-class CF walls pass only in headful (real window) mode.
                r = await ub.navigate_headful(url, wait_until=wait_until)
                if r.get("ok"):
                    content = await ub.get_text()
                    title = r.get("title", "")
                    return ScrapeResult(url=url, ok=True, content=content,
                                        title=title, content_ok=bool(content.strip()),
                                        engine_used="unified_browser(headful)",
                                        page_type=detect_page_type(200, content, title))
                return ScrapeResult(url=url, ok=False, content="", title=r.get("title", ""),
                                    engine_used="unified_browser(headful)",
                                    error=r.get("error") or "cf challenge unsolved",
                                    error_type="bot_blocked",
                                    next_action="give_up")
            r = await ub.fetch(url, wait_until=wait_until,
                               timeout=timeout or config.timeouts["browser"],
                               return_html=False,
                               human_behavior=config.browser_human_behavior)
            if not r.ok:
                bot = r.bot_detection.get("system", "")
                return ScrapeResult(url=url, ok=False, content=r.content,
                                    title=r.title, engine_used="unified_browser",
                                    error=r.error or f"bot-blocked ({bot})",
                                    error_type="bot_blocked" if bot else "fetch_error",
                                    page_type=detect_page_type(r.status_code or 403,
                                                               r.content, r.title))
            return ScrapeResult(url=url, ok=True, content=r.content, title=r.title,
                                content_ok=bool(r.content.strip()),
                                engine_used="unified_browser",
                                page_type=detect_page_type(200, r.content, r.title))
        except Exception as e:
            return ScrapeResult(url=url, ok=False, engine_used="unified_browser",
                                error=str(e)[:200], error_type="no_browser")

    def _quality_warnings(self, r: ScrapeResult) -> list:
        out = []
        if not r.content.strip():
            out.append({"engine": r.engine_used, "code": "EMPTY_CONTENT",
                        "message": "HTTP 200 but content empty"})
        elif len(r.content.strip()) < 50:
            out.append({"engine": r.engine_used, "code": "LOW_CONTENT",
                        "message": f"content short ({len(r.content.strip())} chars)"})
        if r.page_type == "js_shell":
            out.append({"engine": r.engine_used, "code": "JS_SHELL",
                        "message": "JS-rendered shell detected — use smart_browse for full render"})
        return out

    def _finish_scrape(self, r: ScrapeResult, url: str, focus: str,
                       engine_chain: list, start: float, ck: str) -> dict:
        dur = int((time.monotonic() - start) * 1000)
        is_stale = r.page_type == "error" or r.error_type in ("bot_blocked",)
        content = r.content
        if focus and r.content_ok:
            focus_hits = bm25_focus(r.content, focus)
            if focus_hits:
                content = "\n\n".join(focus_hits)
        result = {
            "ok": r.ok,
            "url": url,
            "title": r.title,
            "content": content[:config.max_content_length],
            "content_ok": r.content_ok,
            "page_type": r.page_type,
            "engine_used": r.engine_used or engine_chain[-1] if engine_chain else "",
            "engine_chain": engine_chain,
            "next_action": "none" if r.ok and r.content_ok else self._next_action_for(r.error_type),
            "is_stale": is_stale,
            "status_code": r.status_code or 0,
            "duration_ms": dur,
            "warnings": [{"engine": r.engine_used, "code": r.error_type or "error",
                          "message": r.error[:200]} for _ in [0] if r.error],
            "cache_hit": False,
            "focus_applied": bool(focus and r.content_ok),
        }
        if r.content_ok and not result["warnings"]:
            self._cache.set(ck, result, ttl=config.cache_default_ttl)
        return result

    # ── smart_browse: UnifiedBrowser-first (primary entry point) ─────────

    async def smart_browse(self, url: str, max_age_months: int = 12,
                           require_fresh: bool = False) -> dict:
        ck = SQLiteCache.key("smart_browse", url)
        if not require_fresh:
            cached = self._cache.get(ck)
            if cached:
                cached["cache_hit"] = True
                return cached
        if not browser_available():
            return {"ok": False, "url": url, "error": "no browser found",
                    "error_type": "no_browser", "next_action": "install_chrome",
                    "cache_hit": False}
        start = time.monotonic()
        try:
            ub = await get_browser()
            r = await ub.fetch(url, wait_until="load",
                               timeout=config.timeouts["browser"],
                               return_html=False, human_behavior=True)
            headful_used = False
            if not r.ok and r.cf_challenge:
                # Headless hit a CF wall → escalate to headful (real window)
                r2 = await ub.navigate_headful(url, wait_until="load")
                if r2.get("ok"):
                    content = await ub.get_text()
                    r = type("R", (), {"ok": True, "title": r2.get("title", ""),
                                       "content": content})()
                    headful_used = True
                else:
                    r = type("R", (), {"ok": False, "title": r2.get("title", ""),
                                       "content": "", "cf_challenge": True})()
            result = {
                "ok": r.ok, "url": url, "title": getattr(r, "title", ""),
                "content": getattr(r, "content", "")[:config.max_content_length],
                "content_ok": bool(getattr(r, "content", "") .strip()),
                "page_type": detect_page_type(200, getattr(r, "content", ""),
                                              getattr(r, "title", "")),
                "engine_used": "unified_browser(headful)" if headful_used else "unified_browser",
                "engine_chain": ["unified_browser", "unified_browser(headful)"] if headful_used
                                else ["unified_browser"],
                "next_action": "none" if r.ok else "switch_source",
                "is_stale": False,
                "duration_ms": int((time.monotonic() - start) * 1000),
                "cache_hit": False,
            }
            if r.ok and result["content"]:
                self._cache.set(ck, result, ttl=config.cache_default_ttl)
            return result
        except Exception as e:
            return {"ok": False, "url": url, "error": str(e)[:200],
                    "error_type": "browser_error", "next_action": "give_up",
                    "cache_hit": False}

    # ── deep_search / parallel_scrape ─────────────────────────────

    async def deep_search(self, query: str, max_results: int = 5,
                          sources: list | None = None) -> dict:
        sources = sources or list(DEEP_SOURCES.keys())
        completed: dict = {}
        for name in sources:
            cls = DEEP_SOURCES.get(name)
            if not cls or not cls.is_available():
                completed[name] = {"ok": False, "err": "unavailable", "results": []}
                continue
            engine = cls()
            try:
                results = await asyncio.wait_for(engine.search(query, max_results),
                                                 timeout=config.timeouts["search"])
                completed[name] = {"ok": bool(results), "total": len(results),
                                   "results": [{"url": r.url, "title": r.title,
                                                "snippet": r.snippet,
                                                "source_type": r.source_type,
                                                **{k: v for k, v in r.extra.items()}}
                                               for r in results]}
            except Exception as e:
                completed[name] = {"ok": False, "err": str(e)[:120], "results": []}
        all_results = [r for name, d in completed.items()
                       for r in d.get("results", [])]
        return {"ok": bool(all_results), "query": query,
                "total": len(all_results), "results": all_results,
                "sources": {k: {"ok": v.get("ok", False), "total": len(v.get("results", []))}
                            for k, v in completed.items()}}

    async def parallel_scrape(self, urls: list[str], prefer_browser: bool = False,
                              focus: str = "") -> list[dict]:
        async def _one(url: str):
            async with self._sem:
                return await self.scrape(url, prefer_browser=prefer_browser, focus=focus)
        return await asyncio.gather(*[_one(u) for u in urls])

    # ── crawl / map (HTTP BFS, single-page failure → upgrade to browser) ─────────────────

    async def crawl(self, start_url: str, max_depth: int = 3, max_pages: int = 50,
                    stay_domain: bool = True) -> dict:
        if not HTTPX:
            return {"ok": False, "err": "httpx not installed", "pages": []}
        parsed = urllib.parse.urlparse(start_url)
        domain = parsed.netloc
        visited: set = set()
        queue: list = [(start_url, 0)]
        pages: list = []
        sem = asyncio.Semaphore(3)
        browser_on = browser_available()

        async def _fetch_one(url: str, depth: int):
            async with sem:
                if url in visited or len(pages) >= max_pages:
                    return
                visited.add(url)
                r, _err = await _request("GET", url, timeout=30)
                blocked = False
                if r is None:
                    blocked = True
                elif r.status_code >= 400:
                    blocked = r.status_code in (403, 429)
                text = ""
                title = ""
                status = r.status_code if r else 0
                if r is not None and r.status_code < 400:
                    text = r.text
                if blocked and browser_on:
                    # upgrade this page to browser (D6)
                    try:
                        ub = await get_browser()
                        br = await ub.fetch(url, wait_until="domcontentloaded")
                        if br.ok and br.content:
                            text = br.content
                            title = br.title
                            status = 200
                            blocked = False
                    except Exception:
                        pass
                if not text.strip():
                    return
                if not title:
                    m = re.search(r"<title[^>]*>(.*?)</title>", text[:20_000], re.I | re.S)
                    title = m.group(1).strip() if m else ""
                clean = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
                clean = re.sub(r"<style[^>]*>.*?</style>", "", clean, flags=re.DOTALL)
                clean = re.sub(r"<[^>]+>", " ", clean)
                clean = re.sub(r"\s+", " ", clean).strip()[:config.max_content_length]
                pages.append({"url": url, "depth": depth, "title": title,
                              "content_length": len(clean), "status": status,
                              "content": clean})
                if depth < max_depth and r is not None:
                    for link in re.findall(r'href="(https?://[^"]+)"', r.text, re.I):
                        lp = urllib.parse.urlparse(link)
                        if stay_domain and lp.netloc != domain:
                            continue
                        if any(link.lower().endswith(ext) for ext in
                               [".jpg", ".jpeg", ".png", ".gif", ".pdf", ".zip",
                                ".mp4", ".mp3", ".css", ".js"]):
                            continue
                        norm = link.rstrip("/")
                        if norm not in visited:
                            queue.append((link, depth + 1))

        while queue and len(pages) < max_pages:
            batch = queue[:6]
            queue = queue[6:]
            await asyncio.gather(*[_fetch_one(u, d) for u, d in batch])

        return {"ok": bool(pages), "engine": "crawler", "total_pages": len(pages),
                "pages": pages, "domain": domain, "max_depth": max_depth}

    async def map_site(self, url: str, max_pages: int = 30) -> dict:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc
        scheme = parsed.scheme or "https"
        structure = {"domain": domain, "pages": [],
                     "sitemap_urls": [], "internal_links": {},
                     "categories": []}
        for sm_url in [f"{scheme}://{domain}/sitemap.xml",
                       f"{scheme}://{domain}/sitemap_index.xml",
                       f"{scheme}://{domain}/sitemap"]:
            r, _err = await _request("GET", sm_url, timeout=15)
            if r and r.status_code < 400:
                structure["sitemap_urls"].extend(
                    re.findall(r"<loc>(.*?)</loc>", r.text, re.I)[:max_pages])
                break
        r, _err = await _request("GET", url, timeout=20)
        if r and r.status_code < 400:
            links = re.findall(r'href="(https?://[^"]+)"', r.text, re.I)
            internal = []
            for link in links:
                lp = urllib.parse.urlparse(link)
                if lp.netloc == domain or not lp.netloc:
                    norm = link.rstrip("/")
                    if norm not in internal:
                        internal.append(norm)
            structure["internal_links"][url] = internal[:50]
            paths = set()
            for link in internal:
                segs = [s for s in urllib.parse.urlparse(link).path.split("/") if s]
                if segs:
                    paths.add(segs[0])
            structure["categories"] = sorted(paths)
        return {"ok": True, "engine": "site-map", "structure": structure}

    # ── status ────────────────────────────────────────────────────

    async def status(self) -> dict:
        browser_status = {}
        try:
            ub = await get_browser()
            s = await ub.search_engine_status()
            browser_status = {"available": True, **s}
        except Exception:
            browser_status = {"available": browser_available()}
        return {
            "server": "unified-fetch-v2",
            "engines": {
                c.name: {"available": c.is_available(),
                         "optional": c.optional,
                         "circuit": self._breakers.get(c.name, CircuitBreaker(c.name)).state()}
                for c in SCRAPE_CHAIN + SEARCH_CHAIN
            },
            "browser": browser_status,
            "cache": self._cache.stats(),
            "orientation": ORIENTATION,
        }


# ═══════════════════════════════════════════════════════════════════
# CONNECT-TIME INSTRUCTIONS (A1 — Hound-inspired, ~0.8K tokens)
# ═══════════════════════════════════════════════════════════════════

ORIENTATION = """\
You have access to unified-fetch-v2 — a local web research MCP server.

#1 workflow: search() → pick promising URLs → scrape() each → synthesize.

Tools:
- search(query): parallel web search with cross-engine consensus.
- scrape(url, focus=...): HTTP-first, auto-upgrades to stealth browser when blocked.
  Pass focus="what you're looking for" to return only relevant paragraphs (saves context).
- smart_browse(url): force stealth-browser render (SPA/JS-heavy pages).
- browser_interact(action, ...): click/fill/type/scroll/press on the active page.
- browser_screenshot() / browser_evaluate(expr): visual + JS control.
- deep_search(query, sources=[github,npm,mdn]): technical sources.
- crawl(url) / map(url): site structure discovery.

Every response carries actionable fields: content_ok (is the content real?),
page_type (article/list/js_shell/pdf/error), next_action (what to try next),
engine_used + engine_chain (which engines ran).

Known limits: mass scraping is out of scope; login-gated content is not bypassed;
the browser needs Chrome/Edge (auto-detected) — without it, HTTP engines still work.
"""


# ═══════════════════════════════════════════════════════════════════
# BROWSER-INTERACT (CDP-native, Playwright fallback for drag/frame)
# ═══════════════════════════════════════════════════════════════════

BROWSER_ACTIONS = ["click", "fill", "type", "hover", "select", "scroll", "press",
                   "wait_for", "screenshot", "get_text", "get_html", "evaluate",
                   "upload_file", "cookies", "clear_cookies", "dialog",
                   "navigate", "close_session", "list_sessions", "drag"]


async def browser_interact(action: str, url: str = "", selector: str = "",
                           value: str = "", wait_seconds: float = 1.0,
                           session: str = "", frame_selector: str = "",
                           expression: str = "", timeout: float = 10.0,
                           accept: bool = True) -> dict:
    """CDP-native page interaction (Playwright fallback only for drag/frame)."""
    if not browser_available():
        return {"ok": False, "error": "no browser found", "error_type": "no_browser",
                "next_action": "install_chrome"}

    # Playwright fallback: drag + frame-scoped actions (Playwright optional)
    if action == "drag" or frame_selector:
        return await _playwright_interact(action, url, selector, value,
                                          wait_seconds, session, frame_selector,
                                          expression, timeout, accept)

    if action == "list_sessions":
        try:
            ub = await get_browser()
            return {"ok": True, "sessions": list(ub._active_sessions.keys())}
        except Exception as e:
            return {"ok": False, "error": str(e)[:150]}

    if action == "close_session":
        try:
            ub = await get_browser()
            await ub.close_session(session or "")
            return {"ok": True, "closed": session or "all"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:150]}

    try:
        ub = await get_browser()
    except Exception as e:
        return {"ok": False, "error": str(e)[:150], "error_type": "browser_error"}

    try:
        if action == "navigate":
            r = await ub.navigate(url or "about:blank", wait_until="load")
            return {"ok": r.get("ok", False), "title": r.get("title", ""),
                    "url": url, "bot_detected": r.get("bot_detected", False),
                    "duration_ms": r.get("duration_ms", 0)}
        if action == "click":
            ok = await ub.click(selector)
            return {"ok": ok, "action": "click", "selector": selector}
        if action == "fill":
            ok = await ub.fill(selector, value)
            return {"ok": ok, "action": "fill", "selector": selector}
        if action == "type":
            ok = await ub.type(value, selector)
            return {"ok": ok, "action": "type"}
        if action == "hover":
            ok = await ub.hover(selector)
            return {"ok": ok, "action": "hover"}
        if action == "select":
            ok = await ub.select(selector, value=value)
            return {"ok": ok, "action": "select"}
        if action == "scroll":
            await ub.scroll(value or "down")
            return {"ok": True, "action": "scroll", "direction": value or "down"}
        if action == "press":
            await ub.press(value or "Enter")
            return {"ok": True, "action": "press", "key": value or "Enter"}
        if action == "wait_for":
            ok = await ub.wait_for(selector, timeout=timeout)
            return {"ok": ok, "action": "wait_for", "selector": selector,
                    "timeout": timeout}
        if action == "screenshot":
            png = await ub.screenshot(full_page=(value == "full_page"))
            return {"ok": True, "image": base64.b64encode(png).decode(),
                    "mimeType": "image/png", "bytes": len(png)}
        if action == "get_text":
            return {"ok": True, "content": (await ub.get_text())[:config.max_content_length]}
        if action == "get_html":
            return {"ok": True, "html": (await ub.get_html())[:config.max_content_length]}
        if action == "evaluate":
            return {"ok": True, "result": await ub.evaluate(expression)}
        if action == "upload_file":
            ok = await ub.upload_file(selector, value)
            return {"ok": ok, "action": "upload_file", "selector": selector}
        if action == "cookies":
            return {"ok": True, "cookies": await ub.get_cookies()}
        if action == "clear_cookies":
            await ub.clear_cookies()
            return {"ok": True, "action": "clear_cookies"}
        if action == "dialog":
            await ub.handle_dialog(accept=accept, prompt_text=value)
            return {"ok": True, "action": "dialog", "accept": accept}
        return {"ok": False, "error": f"unknown action: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "error_type": "action_error",
                "next_action": "retry"}


async def _playwright_interact(action: str, url: str, selector: str, value: str,
                               wait_seconds: float, session: str, frame_selector: str,
                               expression: str, timeout: float, accept: bool) -> dict:
    """Fallback for drag + frame-scoped actions (requires playwright, optional)."""
    if not PLAYWRIGHT_AVAILABLE:
        return {"ok": False, "error": "action requires playwright (not installed)",
                "error_type": "missing_dependency", "next_action": "pip install playwright"}
    try:
        from playwright.async_api import async_playwright
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            if url:
                await page.goto(url, wait_until="domcontentloaded",
                                timeout=timeout * 1000)
            frame = page
            if frame_selector:
                frame = page.frame_locator(frame_selector) if hasattr(page, "frame_locator") else page
            if action == "drag":
                target = frame.locator(selector) if hasattr(frame, "locator") else page.locator(selector)
                await target.drag_to(frame.locator(value) if hasattr(frame, "locator") else page.locator(value))
                return {"ok": True, "action": "drag", "note": "via playwright fallback"}
            if action == "click":
                await frame.locator(selector).click()
            elif action == "fill":
                await frame.locator(selector).fill(value)
            elif action == "hover":
                await frame.locator(selector).hover()
            elif action == "get_text":
                return {"ok": True, "content": (await frame.inner_text("body"))[:config.max_content_length],
                        "note": "via playwright fallback"}
            elif action == "get_html":
                return {"ok": True, "html": (await frame.content())[:config.max_content_length],
                        "note": "via playwright fallback"}
            elif action == "screenshot":
                png = await page.screenshot(full_page=(value == "full_page"))
                return {"ok": True, "image": base64.b64encode(png).decode(),
                        "mimeType": "image/png", "bytes": len(png),
                        "note": "via playwright fallback"}
            elif action == "evaluate":
                return {"ok": True, "result": await frame.evaluate(expression),
                        "note": "via playwright fallback"}
            if wait_seconds:
                await page.wait_for_timeout(wait_seconds * 1000)
            return {"ok": True, "action": action, "note": "via playwright fallback"}
        finally:
            await browser.close()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "error_type": "playwright_error"}


# ═══════════════════════════════════════════════════════════════════
# MCP SERVER
# ═══════════════════════════════════════════════════════════════════

TOOLS = None


def _build_tools():
    from mcp.types import Tool
    return [
        Tool(name="search",
             description="Search web. Parallel engines with cross-engine consensus "
                         "(duckduckgo → googlesearch → hound → direct → browser).",
             inputSchema={"type": "object", "properties": {
                 "query": {"type": "string", "description": "search query"},
                 "max_results": {"type": "integer", "description": "max results", "default": 5}},
                 "required": ["query"]}),
        Tool(name="scrape",
             description="Scrape URL to text. HTTP-first, auto-upgrades to stealth browser "
                         "when blocked. focus= returns only relevant paragraphs.",
             inputSchema={"type": "object", "properties": {
                 "url": {"type": "string", "description": "URL to scrape"},
                 "prefer_browser": {"type": "boolean", "default": False,
                                    "description": "start from browser instead of HTTP"},
                 "focus": {"type": "string", "default": "",
                           "description": "BM25 focus query — return only relevant paragraphs"},
                 "require_fresh": {"type": "boolean", "default": False,
                                   "description": "bypass cache"},
                 "wait_until": {"type": "string", "enum": ["load", "domcontentloaded", "networkidle"],
                                "default": "load"}},
                 "required": ["url"]}),
        Tool(name="status",
             description="Engine availability + browser pool + cache stats + orientation.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="deep_search",
             description="Parallel technical search across GitHub, npm, and MDN "
                         "(public APIs, no keys).",
             inputSchema={"type": "object", "properties": {
                 "query": {"type": "string", "description": "search query"},
                 "max_results": {"type": "integer", "description": "max results per source", "default": 5},
                 "sources": {"type": "array", "items": {"type": "string", "enum": ["github", "npm", "mdn"]},
                             "description": "sources to query; default all three"}},
                 "required": ["query"]}),
        Tool(name="parallel_scrape",
             description="Scrape multiple URLs concurrently (semaphore-capped at 5).",
             inputSchema={"type": "object", "properties": {
                 "urls": {"type": "array", "items": {"type": "string"}, "description": "URLs to scrape"},
                 "prefer_browser": {"type": "boolean", "default": False},
                 "focus": {"type": "string", "default": ""}},
                 "required": ["urls"]}),
        Tool(name="crawl",
             description="BFS site crawler. Anti-crawl aware; single pages auto-upgrade to browser.",
             inputSchema={"type": "object", "properties": {
                 "url": {"type": "string", "description": "start URL"},
                 "max_depth": {"type": "integer", "description": "max crawl depth", "default": 3},
                 "max_pages": {"type": "integer", "description": "max pages to crawl", "default": 50},
                 "stay_domain": {"type": "boolean", "description": "stay within same domain", "default": True}},
                 "required": ["url"]}),
        Tool(name="map",
             description="Discover site structure: sitemap URLs + internal link tree.",
             inputSchema={"type": "object", "properties": {
                 "url": {"type": "string", "description": "site URL"},
                 "max_pages": {"type": "integer", "description": "max pages to scan", "default": 30}},
                 "required": ["url"]}),
        Tool(name="smart_browse",
             description="Force stealth-browser render (UnifiedBrowser-first). For SPA/JS-heavy "
                         "pages where HTTP scraping returns a shell.",
             inputSchema={"type": "object", "properties": {
                 "url": {"type": "string", "description": "URL"},
                 "max_age_months": {"type": "integer", "description": "max age in months (informational)", "default": 12},
                 "require_fresh": {"type": "boolean", "description": "require fresh content", "default": False}},
                 "required": ["url"]}),
        Tool(name="browser_navigate",
             description="Navigate the UnifiedBrowser (CDP-native, no Playwright) to a URL.",
             inputSchema={"type": "object", "properties": {
                 "url": {"type": "string", "description": "URL to navigate"},
                 "wait_until": {"type": "string", "enum": ["load", "domcontentloaded", "networkidle"],
                                "description": "When navigation is complete", "default": "load"},
                 "behavior": {"type": "string", "enum": ["read", "search", "form", "browse"],
                              "description": "Behavior profile", "default": "browse"}},
                 "required": ["url"]}),
        Tool(name="browser_get_content",
             description="Get page content (text or HTML) from the current browser session.",
             inputSchema={"type": "object", "properties": {
                 "format": {"type": "string", "enum": ["text", "html"],
                            "description": "Content format", "default": "text"}},
                 "required": []}),
        Tool(name="browser_screenshot",
             description="Screenshot the current page (base64 PNG).",
             inputSchema={"type": "object", "properties": {
                 "full_page": {"type": "boolean", "description": "Full page capture", "default": False}},
                 "required": []}),
        Tool(name="browser_evaluate",
             description="Execute JavaScript in the page context.",
             inputSchema={"type": "object", "properties": {
                 "expression": {"type": "string", "description": "JS expression to evaluate"}},
                 "required": ["expression"]}),
        Tool(name="browser_interact",
             description="CDP-native page interaction: click/fill/type/hover/select/scroll/press/"
                         "wait_for/screenshot/get_text/get_html/evaluate/upload_file/cookies/"
                         "clear_cookies/dialog/navigate/close_session/list_sessions. "
                         "drag + frame_selector fall back to Playwright (optional).",
             inputSchema={"type": "object", "properties": {
                 "action": {"type": "string", "enum": BROWSER_ACTIONS,
                            "description": "action to perform"},
                 "url": {"type": "string", "description": "page URL (navigate)"},
                 "selector": {"type": "string", "description": "CSS selector"},
                 "value": {"type": "string", "description": "text/direction/key/target"},
                 "expression": {"type": "string", "description": "JS for evaluate"},
                 "session": {"type": "string", "description": "session for close_session"},
                 "frame_selector": {"type": "string", "description": "iframe selector (Playwright fallback)"},
                 "wait_seconds": {"type": "number", "description": "wait after action", "default": 1.0},
                 "timeout": {"type": "number", "description": "wait_for timeout", "default": 10.0},
                 "accept": {"type": "boolean", "description": "dialog accept", "default": True}},
                 "required": ["action"]}),
        Tool(name="browser_status",
             description="Get browser pool + identity engine status.",
             inputSchema={"type": "object", "properties": {}}),
    ]


async def _call_tool(name: str, arguments: dict | None) -> list:
    from mcp.types import TextContent, ImageContent
    engine = get_engine()
    args = dict(arguments) if arguments else {}
    try:
        if name == "search":
            r = await engine.search(args.get("query", ""), args.get("max_results", 5))
        elif name == "scrape":
            r = await engine.scrape(
                args.get("url", ""),
                prefer_browser=args.get("prefer_browser", False),
                focus=args.get("focus", ""),
                require_fresh=args.get("require_fresh", False),
                wait_until=args.get("wait_until", "load"))
        elif name == "status":
            r = await engine.status()
        elif name == "deep_search":
            r = await engine.deep_search(args.get("query", ""), args.get("max_results", 5),
                                         args.get("sources"))
        elif name == "parallel_scrape":
            r = await engine.parallel_scrape(args.get("urls", []),
                                             prefer_browser=args.get("prefer_browser", False),
                                             focus=args.get("focus", ""))
        elif name == "crawl":
            r = await engine.crawl(args.get("url", ""), args.get("max_depth", 3),
                                   args.get("max_pages", 50), args.get("stay_domain", True))
        elif name == "map":
            r = await engine.map_site(args.get("url", ""), args.get("max_pages", 30))
        elif name == "smart_browse":
            r = await engine.smart_browse(args.get("url", ""),
                                          args.get("max_age_months", 12),
                                          args.get("require_fresh", False))
        elif name == "browser_navigate":
            ub = await get_browser()
            r = await ub.navigate(args.get("url", ""),
                                  wait_until=args.get("wait_until", "load"),
                                  behavior=args.get("behavior", "browse"))
        elif name == "browser_get_content":
            ub = await get_browser()
            content = await (ub.get_html() if args.get("format") == "html" else ub.get_text())
            if len(content) > 50_000:
                content = content[:50_000] + f"\n\n... [truncated: {len(content)} chars]"
            return [TextContent(type="text", text=content)]
        elif name == "browser_screenshot":
            ub = await get_browser()
            png = await ub.screenshot(full_page=args.get("full_page", False))
            return [ImageContent(type="image", data=base64.b64encode(png).decode(),
                                 mimeType="image/png")]
        elif name == "browser_evaluate":
            ub = await get_browser()
            result = await ub.evaluate(args.get("expression", ""))
            return [TextContent(type="text", text=json.dumps(
                {"result": result, "type": type(result).__name__},
                ensure_ascii=False, indent=2, default=str))]
        elif name == "browser_interact":
            r = await browser_interact(
                action=args.get("action", ""), url=args.get("url", ""),
                selector=args.get("selector", ""), value=args.get("value", ""),
                wait_seconds=args.get("wait_seconds", 1.0),
                session=args.get("session", ""),
                frame_selector=args.get("frame_selector", ""),
                expression=args.get("expression", ""),
                timeout=args.get("timeout", 10.0),
                accept=args.get("accept", True))
        elif name == "browser_status":
            ub = await get_browser()
            r = await ub.search_engine_status()
            r = {"browser": "unified-fetch-v2", "status": r}
        else:
            r = {"ok": False, "err": f"unknown tool: {name}"}
    except Exception as e:
        logger.exception("Unhandled error in tool '%s'", name)
        r = {"ok": False, "err": f"internal error: {e}",
             "error_code": "INTERNAL_ERROR", "retryable": False}
    return [TextContent(type="text", text=json.dumps(r, ensure_ascii=False, indent=2))]


_engine: "Unified | None" = None


def get_engine() -> "Unified":
    global _engine
    if _engine is None:
        _engine = Unified()
    return _engine


async def serve_mcp():
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (CallToolRequestParams, CallToolResult, ListToolsResult,
                           PaginatedRequestParams, TextContent)

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

    app = Server("unified-fetch", version="2.0.0",
                 on_list_tools=handle_list, on_call_tool=handle_call)

    async with stdio_server() as (rs, ws):
        await app.run(rs, ws, app.create_initialization_options(
            extensions={"instructions": {"text": ORIENTATION}}))
        logger.info("unified-fetch-v2 ready")


# ═══════════════════════════════════════════════════════════════════
# CLI MODE
# ═══════════════════════════════════════════════════════════════════

async def cli():
    sys.stdout.reconfigure(encoding="utf-8")
    engine = Unified()
    if len(sys.argv) < 2:
        print("Usage: unified-fetch-server.py "
              "search <query> | scrape <url> [--prefer-browser] [--focus X] | "
              "status | deep-search <query> [github|npm|mdn] | "
              "parallel-browse <url1> <url2>... | smart-browse <url> | "
              "crawl <url> [--depth N] [--pages N] | map <url> | "
              "browse <url> [--get-content|--screenshot|--evaluate EXPR]")
        return
    cmd = sys.argv[1]
    if cmd == "search":
        r = await engine.search(" ".join(sys.argv[2:]))
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "scrape":
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("url")
        parser.add_argument("--prefer-browser", action="store_true")
        parser.add_argument("--focus", default="")
        args = parser.parse_args(sys.argv[2:])
        r = await engine.scrape(args.url, prefer_browser=args.prefer_browser,
                                focus=args.focus)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "status":
        r = await engine.status()
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    elif cmd == "deep-search":
        parts = sys.argv[2:]
        sources = None
        if parts and parts[-1] in ("github", "npm", "mdn"):
            sources = parts[-1:]
            parts = parts[:-1]
        r = await engine.deep_search(" ".join(parts), sources=sources)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "parallel-browse":
        urls = sys.argv[2:]
        r = await engine.parallel_scrape(urls)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "smart-browse":
        url = sys.argv[2] if len(sys.argv) > 2 else ""
        if not url:
            print("ERROR: need a URL"); return
        r = await engine.smart_browse(url)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "crawl":
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("url")
        parser.add_argument("--depth", type=int, default=3)
        parser.add_argument("--pages", type=int, default=50)
        parser.add_argument("--no-stay-domain", action="store_true")
        args = parser.parse_args(sys.argv[2:])
        r = await engine.crawl(args.url, args.depth, args.pages, not args.no_stay_domain)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "map":
        url = sys.argv[2] if len(sys.argv) > 2 else ""
        if not url:
            print("ERROR: need a URL"); return
        r = await engine.map_site(url)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "browse":
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("url")
        parser.add_argument("--get-content", action="store_true")
        parser.add_argument("--screenshot", action="store_true")
        parser.add_argument("--evaluate", default="")
        args = parser.parse_args(sys.argv[2:])
        ub = await get_browser()
        r = await ub.navigate(args.url, wait_until="load")
        print(json.dumps({"ok": r.get("ok"), "title": r.get("title")},
                         ensure_ascii=False, indent=2))
        if args.get_content:
            content = await ub.get_text()
            print(content[:config.max_content_length])
        if args.evaluate:
            print(json.dumps(await ub.evaluate(args.evaluate), ensure_ascii=False,
                             default=str))
        if args.screenshot:
            png = await ub.screenshot()
            out = "screenshot.png"
            with open(out, "wb") as f:
                f.write(png)
            print(f"saved {out} ({len(png)} bytes)")
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        asyncio.run(cli())
    else:
        # MCP stdio mode
        try:
            asyncio.run(serve_mcp())
        except KeyboardInterrupt:
            pass
