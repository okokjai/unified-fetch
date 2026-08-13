<div align="center">

# 🌐 unified-fetch

**Multi-engine web search & scraping MCP server for AI agents.**
**4 search engines · 6 scrape engines · adaptive fallback · zero API keys**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![MCP](https://img.shields.io/badge/MCP-Ready-orange.svg)](https://modelcontextprotocol.io)
[![Playwright](https://img.shields.io/badge/Playwright-optional-green.svg)](https://playwright.dev)

```bash
pip install httpx duckduckgo_search trafilatura readability-lxml lxml[html_clean] justext newspaper3k
python3 unified-fetch-server.py
```

Give your AI agent the web. Zero accounts. No API keys. Self-hosted.

</div>

---

## ✨ Features

| Category | Capabilities |
|----------|-------------|
| 🔍 **Search** (4 engines) | 4 search engines with adaptive fallback — auto-switch on failure |
| 📄 **Scrape** (6 engines) | 6 extraction engines with tiered fallback — auto-switch on empty content |
| 🔬 **Deep Search** (3 sources) | GitHub API + npm API + MDN API — parallel technical search |
| 🕷️ **Crawl** | BFS site crawler with depth/pages/domain constraints — anti-crawl aware |
| 🗺️ **Map** | Site structure discovery: sitemap + internal link tree + category hierarchy |
| 🎭 **Interact** | Playwright-driven page interaction: click, fill, hover, screenshot, scroll |
| 🧠 **Smart Browse** | SPA-aware: detects JS-heavy pages, forces dynamic rendering |
| ⚡ **Parallel** | Scrape multiple URLs concurrently (semaphore-capped at 5) |

---

## 🎯 Search & Scrape Strategy

### Search Strategy (4 engines)

| Order | Engine | Role |
|-------|--------|------|
| 1 | **Smart Fetch** | Primary — quality results first, SPA-aware rendering |
| 2 | **DuckDuckGo** | Fast, no login required |
| 3 | **Google Search** | Broadest coverage |
| 4 | **DirectFetch** | Last resort — parses results directly from the search page |

**Fallback rules:**
- Engine fails or returns 0 results → automatically switch to next engine
- **Circuit breaker**: 3 consecutive failures → 30s cooldown per engine (no hammering a dead engine)
- All engines fail → returns structured error with `retryable` flag

### Scrape Strategy (6 engines)

| Order | Engine | Role |
|-------|--------|------|
| 1 | **Smart Fetch** | Primary — Cloudflare bypass + JS rendering when needed |
| 2 | **newspaper3k** | Article extraction (headlines, body, dates) |
| 3 | **Trafilatura** | Clean main-content extraction, language-aware |
| 4 | **Readability** | Distills article text from cluttered pages |
| 5 | **jusText** | Heuristic boilerplate removal |
| 6 | **DirectFetch** | Last resort — raw fetch + text normalization |

**Fallback rules:**
- Engine fails or returns empty content → automatically switch to next engine
- Each engine's timeout is independent (no single dead endpoint blocks the chain)

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    unified-fetch  MCP  Server                    │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Search Layer (4 engines)                    │   │
│  │  Smart Fetch → DuckDuckGo → Google → DirectFetch        │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Scrape Layer (6 engines)                    │   │
│  │  Smart Fetch → newspaper3k → Trafilatura → readability  │   │
│  │  → jusText → DirectFetch                                │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│          ┌──────────────────┼────────────────────┐              │
│          ▼                  ▼                    ▼              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐      │
│  │  Crawl/Map   │  │ Deep Search  │  │   Playwright     │      │
│  │  BFS sitemap │  │GitHub/npm/MDN│  │  Interact/Smart  │      │
│  └──────────────┘  └──────────────┘  └──────────────────┘      │
│                                                                 │
│  ⚡ Anti-crawl: random UA pool · random delays · cookie jar     │
│  ⚡ Circuit breaker: 3 failures → 30s cooldown per engine      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Requirements

- Python 3.10+
- `pip install` the engines you need (all optional — missing engines are skipped gracefully)

### Installation

```bash
# Clone
git clone https://github.com/okokjai/unified-fetch.git
cd unified-fetch

# Install dependencies (install what you want — missing ones auto-skip)
pip install httpx duckduckgo_search trafilatura readability-lxml lxml[html_clean] justext newspaper3k

# Optional: Playwright for JS rendering + page interaction
pip install playwright
playwright install chromium

# Run
python3 unified-fetch-server.py
```

### MCP Configuration

Add to your `~/.claude/mcp_servers.json`:

```json
{
  "mcpServers": {
    "unified-fetch": {
      "command": "python3",
      "args": ["path/to/unified-fetch-server.py"]
    }
  }
}
```

---

## 🛠️ Tools

| Tool | Description |
|------|-------------|
| `search` | Search web. 4 engines with adaptive fallback |
| `scrape` | Scrape URL to text. 6 engines with tiered fallback |
| `status` | Check engine availability |
| `deep_search` | Parallel technical search across GitHub, npm, and MDN |
| `parallel_scrape` | Scrape multiple URLs concurrently (capped at 5) |
| `smart_browse` | SPA-aware browse — forces dynamic rendering for JS pages |
| `crawl` | BFS site crawler with depth/pages/domain constraints |
| `map` | Site structure discovery: sitemap + internal link tree |
| `smart_scrape` | Auto-detect HTTP vs Playwright rendering |
| `interact` | Page interaction: click, fill, hover, screenshot, scroll, etc. |

---

## 🔧 Anti-Crawl Posture

Built-in, zero-cost protection for personal-scale use:

| Layer | Mechanism |
|-------|-----------|
| **L1** | Random User-Agent pool (10 UAs) + random delays (0.5–3s) |
| **L2** | Referer chain + cookie persistence + full header set |
| **L3** | Playwright headless browser (JS render, fingerprint variation) — optional |

---

## 📄 License

MIT — see [LICENSE](LICENSE) for details.