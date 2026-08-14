#!/usr/bin/env python3
"""
cdp_driver.py — Raw CDP Transport Layer for UnifiedBrowser.

No Playwright, no Selenium, no WebDriver.
Direct WebSocket connection to Chrome DevTools Protocol using native asyncio.

Architecture:
  Chrome Process ──ws──→ CDPTransport ───→ Session Manager ──→ Page/Tab API
                              │
                          Event Queue
                              │
                          Message Router

Usage:
    transport = CDPTransport()
    await transport.start(chrome_path="/path/to/chrome")
    session = await transport.create_session("about:blank")
    result = await session.evaluate("1 + 1")
    transport.stop()
"""

import asyncio
import base64
import glob
import json
import logging
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("cdp_driver")

# ═══════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════

DEFAULT_CHROME_ARGS = [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--enable-unsafe-swiftshader",      # Chrome 128+: allow software WebGL (was --use-gl=swiftshader-webgl, deprecated in 132+)
    "--use-angle=swiftshader",          # ANGLE with SwiftShader backend → real WebGL context in headless
    "--disable-extensions",
    "--disable-sync",
    "--disable-default-apps",
    "--disable-translate",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-breakpad",
    "--disable-component-extensions-with-background-pages",
    "--disable-component-update",
    "--disable-crash-reporter",
    "--disable-domain-reliability",
    "--disable-features=TranslateUI,BlinkGenPropertyTrees",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding",
    "--enable-features=NetworkService,NetworkServiceInProcess",
    "--force-color-profile=srgb",
    "--hide-scrollbars",
    "--metrics-recording-only",
    "--mute-audio",
    "--no-first-run",
    "--password-store=basic",
    "--use-mock-keychain",
    "--lang=en-US",
    "--disable-blink-features=AutomationControlled",   # hide the automation probe
    "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",  # strip HeadlessChrome from UA
]

CDP_DEFAULT_TIMEOUT = 30.0  # seconds

# ═══════════════════════════════════════════════════════════════════
# CDP Exceptions
# ═══════════════════════════════════════════════════════════════════

class CDPError(Exception):
    """Error returned by Chrome DevTools Protocol."""
    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(f"CDP error {code}: {message}")

class CDPTimeoutError(Exception):
    """CDP command timed out."""
    pass

class CDPConnectionError(Exception):
    """Failed to connect to Chrome DevTools."""
    pass

# ═══════════════════════════════════════════════════════════════════
# CDP Transport
# ═══════════════════════════════════════════════════════════════════

class CDPTransport:
    """
    Raw WebSocket connection to Chrome DevTools Protocol.

    Manages:
    - Chrome process lifecycle (start/stop)
    - WebSocket connection to DevTools (via `websockets` library)
    - CDP command/response matching
    - Event routing
    - Session management (tabs/pages)
    """

    def __init__(self):
        self._chrome_process: Optional[subprocess.Popen] = None
        self._ws = None
        self._port: int = 0
        self._user_data_dir: Optional[str] = None
        self._running = False
        self._cmd_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._event_handlers: dict[str, list[Callable]] = {}
        self._listener_task: Optional[asyncio.Task] = None
        self._sessions: dict[str, "CDPSession"] = {}
        self._send_lock = asyncio.Lock()

    # ── Chrome Process Management ──────────────────────────────────

    def find_chrome(self) -> str:
        """Find Chrome/Chromium/Edge executable."""
        candidates = [
            os.environ.get("CHROME_PATH", ""),
            os.environ.get("UNIFIED_BROWSER_PATH", ""),
            # Playwright bundled chromium (Windows + POSIX)
            os.path.expanduser("~/AppData/Local/ms-playwright"),
            # Edge (Windows 11 built-in — guarantees out-of-box usability)
            "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
            "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
            # Chrome
            "C:/Program Files/Google/Chrome/Application/chrome.exe",
            "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
            "/c/Program Files/Google/Chrome/Application/chrome.exe",
            "/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]

        for c in candidates:
            if not c:
                continue
            # Playwright bundled chromium: <base>/chromium-*/chrome-win64/chrome.exe
            if os.path.isdir(c):
                matches = sorted(glob.glob(os.path.join(c, "chromium-*/chrome-win64/chrome.exe")))
                if matches:
                    return matches[-1]
            elif os.path.isfile(c):
                return c

        # shutil.which
        for name in ("chrome", "chromium", "msedge", "google-chrome", "google-chrome-stable"):
            try:
                p = shutil.which(name)
                if p:
                    return p
            except Exception:
                pass

        raise RuntimeError(
            "No browser found. Set CHROME_PATH env var or install Chrome/Edge."
        )

    def _find_free_port(self) -> int:
        """Find a free TCP port for Chrome's remote debugging."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    async def start(
        self,
        chrome_path: Optional[str] = None,
        args: Optional[list[str]] = None,
        user_data_dir: Optional[str] = None,
        headless: bool = True,
        headful_mode: str = "offscreen",   # "offscreen" (Windows) | "xvfb" (Linux) | "visible"
        window_size: tuple[int, int] = (1920, 1080),
        proxy: Optional[str] = None,
    ) -> dict:
        """
        Start Chrome and connect to DevTools.

        headless=True → headless mode (no window; fails CF hard challenges).
        headless=False → headful mode (real window = passes SO-class CF walls).
          - headful_mode="offscreen": move window off-screen (-32000,-32000) to
            minimize popup disruption on Windows.
          - headful_mode="xvfb": wrap in Xvfb virtual display (Linux servers).
          - headful_mode="visible": show the window normally.

        Returns browser version info dict.
        """
        if self._running:
            return await self._get_version()

        try:
            chrome_path = chrome_path or self.find_chrome()
        except RuntimeError:
            logger.warning("No browser binary found — browser unavailable")
            return None

        self._port = self._find_free_port()

        if not user_data_dir:
            self._user_data_dir = tempfile.mkdtemp(prefix="unifiedbrowser_")
        else:
            self._user_data_dir = user_data_dir

        chrome_args = list(args or DEFAULT_CHROME_ARGS)
        chrome_args += [
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={self._user_data_dir}",
        ]
        # headful: strip headless flag + offscreen positioning (unless visible)
        if headless:
            if "--headless=new" not in chrome_args:
                chrome_args.append("--headless=new")
        else:
            chrome_args = [a for a in chrome_args if "headless" not in a]
            if headful_mode == "offscreen" and sys.platform == "win32":
                chrome_args += ["--window-position=-32000,-32000"]
            elif headful_mode == "offscreen":
                chrome_args += ["--window-position=0,0"]
        if window_size:
            chrome_args.append(f"--window-size={window_size[0]},{window_size[1]}")
        if proxy:
            chrome_args.append(f"--proxy-server={proxy}")

        logger.info(f"Starting Chrome: {chrome_path}")
        logger.debug(f"Chrome args: {chrome_args}")

        self._create_process(chrome_path, chrome_args)

        # Wait for DevTools port and connect
        ws_url = await self._wait_for_debug_port(timeout=15.0)
        await self._connect(ws_url)
        self._running = True

        version = await self._get_version()
        logger.info(f"Connected: {version.get('product', 'unknown')}")
        return version

    def _create_process(self, chrome_path: str, chrome_args: list[str]):
        """Launch Chrome (no asyncio, runs in executor if needed)."""
        try:
            self._chrome_process = subprocess.Popen(
                [chrome_path] + chrome_args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to launch Chrome: {e}")

    async def _wait_for_debug_port(self, timeout: float = 15.0) -> str:
        """Wait for Chrome's DevTools listener, return browser WS URL."""
        import urllib.request

        start = time.monotonic()
        while time.monotonic() - start < timeout:
            # Check process alive
            if self._chrome_process and self._chrome_process.poll() is not None:
                raise RuntimeError(
                    f"Chrome exited early (code {self._chrome_process.returncode})"
                )
            try:
                url = f"http://127.0.0.1:{self._port}/json/version"
                resp = urllib.request.urlopen(url, timeout=2)
                data = json.loads(resp.read().decode())
                ws = data.get("webSocketDebuggerUrl")
                if ws:
                    return ws
            except Exception:
                pass
            await asyncio.sleep(0.2)

        raise CDPConnectionError(
            f"Chrome DevTools not ready after {timeout}s (port {self._port})"
        )

    # ── WebSocket Connection ───────────────────────────────────────

    async def _connect(self, ws_url: str):
        """Connect WebSocket using native asyncio `websockets` library."""
        import websockets

        self._ws = await websockets.connect(
            ws_url,
            max_size=100 * 1024 * 1024,  # 100MB messages (page content can be huge)
            compression=None,
        )

        # Start listener task
        self._listener_task = asyncio.create_task(self._message_loop())
        logger.debug("WebSocket connected")

    async def _message_loop(self):
        """Listen for incoming CDP messages and route them."""
        while self._running:
            try:
                message = await self._ws.recv()
                if not message:
                    continue

                data = json.loads(message)

                # Command response
                if "id" in data:
                    future = self._pending.pop(data["id"], None)
                    if future and not future.done():
                        if "error" in data:
                            future.set_exception(
                                CDPError(data["error"]["code"],
                                         data["error"]["message"])
                            )
                        else:
                            future.set_result(data.get("result", {}))

                # Event
                elif "method" in data:
                    await self._route_event(data["method"],
                                            data.get("params", {}),
                                            data.get("sessionId"))

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.error(f"Message loop: {e}")
                break

        # Fail all pending on disconnect
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(CDPConnectionError("WebSocket closed"))
        self._pending.clear()
        logger.debug("Message loop ended")

    async def send(self, method: str, params: Optional[dict] = None,
                   session_id: Optional[str] = None,
                   timeout: float = CDP_DEFAULT_TIMEOUT) -> dict:
        """Send a CDP command and await its response."""
        if not self._ws or not self._running:
            raise CDPConnectionError("Not connected to Chrome")

        async with self._send_lock:
            self._cmd_id += 1
            cmd_id = self._cmd_id
            command = {"id": cmd_id, "method": method, "params": params or {}}
            if session_id:
                command["sessionId"] = session_id

            future = asyncio.get_event_loop().create_future()
            self._pending[cmd_id] = future
            await self._ws.send(json.dumps(command))

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            raise CDPTimeoutError(f"CDP '{method}' timed out after {timeout}s")

    async def _route_event(self, method: str, params: dict, session_id: Optional[str]):
        """Dispatch an event to session-bound and global handlers."""
        # Find session-specific handlers
        if session_id:
            for sess in self._sessions.values():
                if sess._session_id == session_id:
                    await sess._dispatch_event(method, params)

        # Global handlers
        handlers = self._event_handlers.get(method, [])
        for h in handlers:
            try:
                res = h(params)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(f"Event handler {method}: {e}")

    def on(self, method: str, handler: Callable):
        self._event_handlers.setdefault(method, []).append(handler)

    def off(self, method: str, handler: Callable):
        if method in self._event_handlers:
            try:
                self._event_handlers[method].remove(handler)
            except ValueError:
                pass

    # ── Browser Info ───────────────────────────────────────────────

    async def _get_version(self) -> dict:
        return await self.send("Browser.getVersion")

    # ── Session Management ─────────────────────────────────────────

    async def create_session(self, url: str = "about:blank") -> "CDPSession":
        """Create a new tab/page and attach to it.

        Headful quirk: Target.createTarget with newWindow=False can fail with
        "Failed to open new tab" right after launch (the headful browser is
        still initializing its window). Fall back to attaching to an existing
        page target (the chrome://intro/new-tab page) and navigating it.
        """
        try:
            result = await self.send("Target.createTarget", {
                "url": url,
                "newWindow": False,
            })
            target_id = result["targetId"]
        except CDPError as e:
            # Fallback: attach to an existing page target and navigate it.
            logger.debug("createTarget failed (%s) — attaching to existing page", str(e)[:80])
            targets = await self.list_targets()
            page = next((t for t in targets if t.get("type") == "page"), None)
            if not page:
                raise
            session = await self.attach_to_target(page["targetId"])
            if url and url != "about:blank":
                await session.send("Page.enable")
                await session.send("Runtime.enable")
                await session.navigate(url, wait_until="load", timeout=30)
            return session

        # Attach (flattened => nested sessionId in commands)
        attach = await self.send("Target.attachToTarget", {
            "targetId": target_id,
            "flatten": True,
        })

        session = CDPSession(self, target_id, attach["sessionId"])
        self._sessions[target_id] = session
        return session

    async def attach_to_target(self, target_id: str) -> "CDPSession":
        attach = await self.send("Target.attachToTarget", {
            "targetId": target_id,
            "flatten": True,
        })
        session = CDPSession(self, target_id, attach["sessionId"])
        self._sessions[target_id] = session
        return session

    async def list_targets(self) -> list[dict]:
        result = await self.send("Target.getTargets")
        return result.get("targetInfos", [])

    async def close_session(self, target_id: str):
        try:
            await self.send("Target.closeTarget", {"targetId": target_id})
        except Exception:
            pass
        self._sessions.pop(target_id, None)

    # ── Shutdown ───────────────────────────────────────────────────

    def stop(self):
        """Stop Chrome and clean up."""
        self._running = False

        if self._listener_task:
            self._listener_task.cancel()
            self._listener_task = None

        if self._ws:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Can't run_until_complete on a running loop, schedule it
                    loop.create_task(self._ws.close())
                else:
                    loop.run_until_complete(self._ws.close())
            except Exception:
                pass
            self._ws = None

        if self._chrome_process:
            try:
                self._chrome_process.terminate()
                self._chrome_process.wait(timeout=5)
            except Exception:
                try:
                    self._chrome_process.kill()
                except Exception:
                    pass
            self._chrome_process = None

        if self._user_data_dir and os.path.isdir(self._user_data_dir):
            try:
                # Chrome sometimes locks files; retry
                for _ in range(3):
                    try:
                        shutil.rmtree(self._user_data_dir, ignore_errors=True)
                        break
                    except Exception:
                        time.sleep(0.5)
            except Exception:
                pass

        logger.info("Chrome stopped")

    async def astop(self):
        """Async stop (used when called from within event loop)."""
        self._running = False

        if self._listener_task:
            self._listener_task.cancel()
            await asyncio.gather(self._listener_task, return_exceptions=True)
            self._listener_task = None

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        if self._chrome_process:
            try:
                self._chrome_process.terminate()
                self._chrome_process.wait(timeout=5)
            except Exception:
                try:
                    self._chrome_process.kill()
                except Exception:
                    pass
            self._chrome_process = None

        if self._user_data_dir and os.path.isdir(self._user_data_dir):
            try:
                shutil.rmtree(self._user_data_dir, ignore_errors=True)
            except Exception:
                pass

        logger.info("Chrome stopped")


# ═══════════════════════════════════════════════════════════════════
# CDP Session (Page/Tab)
# ═══════════════════════════════════════════════════════════════════

class CDPSession:
    """
    A CDP session bound to a single target (tab/page).
    """

    def __init__(self, transport: CDPTransport, target_id: str, session_id: str):
        self._transport = transport
        self._target_id = target_id
        self._session_id = session_id
        self._event_handlers: dict[str, list[Callable]] = {}

    @property
    def target_id(self) -> str:
        return self._target_id

    @property
    def session_id(self) -> str:
        return self._session_id

    # ── Command —───────────────────────────────────────────────────

    async def send(self, method: str, params: Optional[dict] = None,
                   timeout: float = CDP_DEFAULT_TIMEOUT) -> dict:
        """Send a CDP command scoped to this session."""
        return await self._transport.send(method, params,
                                          session_id=self._session_id,
                                          timeout=timeout)

    # ── Event handling (session-scoped) ────────────────────────────

    def on(self, method: str, handler: Callable):
        self._event_handlers.setdefault(method, []).append(handler)

    def off(self, method: str, handler: Callable):
        if method in self._event_handlers:
            try:
                self._event_handlers[method].remove(handler)
            except ValueError:
                pass

    async def _dispatch_event(self, method: str, params: dict):
        handlers = self._event_handlers.get(method, [])
        for h in handlers:
            try:
                res = h(params)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(f"Session event {method}: {e}")

    async def _wait_for_event(self, method: str, timeout: float = 30.0) -> dict:
        """Wait for a CDP event to fire, return its params."""
        future = asyncio.get_event_loop().create_future()

        def handler(params):
            if not future.done():
                future.set_result(params)

        self.on(method, handler)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            raise CDPTimeoutError(f"Event '{method}' timed out")
        finally:
            self.off(method, handler)

    # ── Page Navigation ───────────────────────────────────────────

    async def enable(self):
        """Enable required domains."""
        await asyncio.gather(
            self.send("Page.enable"),
            self.send("Runtime.enable"),
            self.send("Network.enable"),
        )

    async def navigate(self, url: str,
                       wait_until: str = "load",
                       timeout: float = 30.0) -> dict:
        """
        Navigate to a URL.

        wait_until: 'load' | 'domcontentloaded' | 'networkidle'
        """
        result = await self.send("Page.navigate", {"url": url}, timeout=timeout)

        if wait_until == "load":
            await self._wait_for_event("Page.loadEventFired", timeout=timeout)
        elif wait_until == "domcontentloaded":
            await self._wait_for_event("Page.domContentEventFired",
                                       timeout=timeout)
        elif wait_until == "networkidle":
            await self._wait_for_network_idle(timeout=timeout)

        return result

    async def _wait_for_network_idle(self, timeout: float = 30.0,
                                     idle_ms: int = 800):
        """Wait until no network requests happen for idle_ms."""
        last_activity = time.monotonic()
        waited = asyncio.Event()
        notified = False

        async def bump():
            nonlocal last_activity, notified
            last_activity = time.monotonic()
            notified = True

        self.on("Network.requestWillBeSent",
                lambda p: asyncio.ensure_future(bump()))
        self.on("Network.responseReceived",
                lambda p: asyncio.ensure_future(bump()))

        try:
            start = time.monotonic()
            while time.monotonic() - start < timeout:
                await asyncio.sleep(0.2)
                if (time.monotonic() - last_activity) >= (idle_ms / 1000):
                    return
        finally:
            # remove handlers (best effort, they'll be GC'd)
            self._event_handlers.pop("Network.requestWillBeSent", None)
            self._event_handlers.pop("Network.responseReceived", None)

    # ── Content ────────────────────────────────────────────────────

    async def get_html(self) -> str:
        result = await self.send("Runtime.evaluate", {
            "expression": "document.documentElement.outerHTML",
            "returnByValue": True,
        })
        return result.get("result", {}).get("value", "")

    async def get_text(self) -> str:
        result = await self.send("Runtime.evaluate", {
            "expression": "document.body ? document.body.innerText : ''",
            "returnByValue": True,
        })
        return result.get("result", {}).get("value", "")

    async def get_title(self) -> str:
        result = await self.send("Runtime.evaluate", {
            "expression": "document.title",
            "returnByValue": True,
        })
        return result.get("result", {}).get("value", "")

    async def evaluate(self, expression: str,
                       return_by_value: bool = True) -> Any:
        """Evaluate JS in page context."""
        result = await self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": return_by_value,
        })

        if "exceptionDetails" in result:
            exc = result["exceptionDetails"]
            raise RuntimeError(
                f"JS Error: {exc.get('text', '')}"
                f" (line {exc.get('lineNumber', '?')})"
            )

        # handle unserializable values
        r = result.get("result", {})
        if r.get("type") == "undefined":
            return None
        return r.get("value")

    async def evaluate_async(self, expression: str) -> Any:
        """Evaluate an async JS expression (wrapped in awaitPromise)."""
        result = await self.send("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
        })
        if "exceptionDetails" in result:
            exc = result["exceptionDetails"]
            raise RuntimeError(
                f"JS Error: {exc.get('text', '')}"
                f" (line {exc.get('lineNumber', '?')})"
            )
        return result.get("result", {}).get("value")

    # ── Screenshot ─────────────────────────────────────────────────

    async def screenshot(self, format: str = "png", quality: int = 80,
                         full_page: bool = False) -> bytes:
        params = {"format": format, "quality": quality,
                  "captureBeyondViewport": full_page}
        if full_page:
            metrics = await self.send("Page.getLayoutMetrics")
            cs = metrics.get("contentSize", {})
            params["clip"] = {
                "x": 0, "y": 0,
                "width": cs.get("width", 1920),
                "height": cs.get("height", 1080),
                "scale": 1,
            }
        result = await self.send("Page.captureScreenshot", params)
        return base64.b64decode(result["data"])

    # ── Input Simulation ───────────────────────────────────────────

    async def click(self, x: float, y: float, button: str = "left",
                    click_count: int = 1, delay_ms: int = 0):
        await self.send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": button, "clickCount": 1,
        })
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)
        await self.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": button, "clickCount": 1,
        })

    async def type_text(self, text: str, delay_ms: int = 50):
        for ch in text:
            if ch == "\n":
                await self.send("Input.dispatchKeyEvent", {
                    "type": "rawKeyDown", "key": "Enter",
                    "code": "Enter", "windowsVirtualKeyCode": 13,
                })
                continue
            await self.send("Input.dispatchKeyEvent", {
                "type": "char", "text": ch, "unmodifiedText": ch,
            })
            if delay_ms:
                await asyncio.sleep(random.uniform(delay_ms * 0.7,
                                                   delay_ms * 1.3) / 1000)

    async def scroll(self, delta_x: float = 0, delta_y: float = 300):
        await self.send("Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": 640, "y": 400,
            "deltaX": delta_x, "deltaY": delta_y,
        })

    async def press(self, key_binding: str):
        """Press a key by name (e.g. 'Enter', 'Escape', 'ArrowDown')."""
        await self.send("Input.dispatchKeyEvent", {
            "type": "rawKeyDown", "key": key_binding,
        })
        await self.send("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": key_binding,
        })

    # ── Cookies ────────────────────────────────────────────────────

    async def get_cookies(self) -> list[dict]:
        result = await self.send("Network.getCookies")
        return result.get("cookies", [])

    async def set_cookie(self, name: str, value: str,
                         url: Optional[str] = None,
                         domain: Optional[str] = None):
        params = {"name": name, "value": value}
        if url:
            params["url"] = url
        if domain:
            params["domain"] = domain
        await self.send("Network.setCookie", params)

    async def clear_cookies(self):
        await self.send("Network.clearBrowserCookies")

    # ── Dialogs ────────────────────────────────────────────────────

    async def handle_dialog(self, accept: bool = True,
                            prompt_text: Optional[str] = None):
        params = {"accept": accept}
        if prompt_text:
            params["promptText"] = prompt_text
        await self.send("Page.handleJavaScriptDialog", params)

    # ── Storage / DOM ──────────────────────────────────────────────

    async def get_by_selector(self, selector: str) -> Optional[dict]:
        """Return bounding box of first element matching a CSS selector."""
        result = await self.evaluate(f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return {{ x: r.x + r.width/2, y: r.y + r.height/2,
                          w: r.width, h: r.height,
                          text: (el.innerText || '').slice(0, 200) }};
            }})()
        """)
        return result

    async def click_selector(self, selector: str) -> bool:
        """Click an element by CSS selector."""
        box = await self.get_by_selector(selector)
        if not box:
            return False
        wait_ms = random.randint(60, 180)
        await asyncio.sleep(wait_ms / 1000)
        await self.click(box["x"], box["y"])
        return True

    async def hover(self, selector: str) -> bool:
        """Move the mouse to the center of an element (no click)."""
        box = await self.get_by_selector(selector)
        if not box:
            return False
        await asyncio.sleep(random.uniform(0.05, 0.15))
        await self.send("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": box["x"], "y": box["y"],
        })
        return True

    async def focus_selector(self, selector: str) -> bool:
        """Focus an element (for typing)."""
        return await self.evaluate(
            f"""(() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return false;
                el.focus();
                return true;
            }})()"""
        )

    async def fill(self, selector: str, text: str,
                   delay_ms: int = 40) -> bool:
        """Clear an input/textarea and type text into it."""
        if not await self.focus_selector(selector):
            return False
        # Select all + delete to clear existing value
        await self.send("Input.dispatchKeyEvent", {
            "type": "rawKeyDown", "key": "Control",
            "code": "ControlLeft", "windowsVirtualKeyCode": 17,
            "modifiers": 2,
        })
        await self.send("Input.dispatchKeyEvent", {
            "type": "rawKeyDown", "key": "a", "code": "KeyA",
            "windowsVirtualKeyCode": 65, "modifiers": 2,
        })
        await self.send("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "a", "code": "KeyA",
            "windowsVirtualKeyCode": 65, "modifiers": 2,
        })
        await self.send("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "Control", "code": "ControlLeft",
            "windowsVirtualKeyCode": 17, "modifiers": 2,
        })
        await self.send("Input.dispatchKeyEvent", {
            "type": "rawKeyDown", "key": "Backspace",
            "code": "Backspace", "windowsVirtualKeyCode": 8,
        })
        await self.send("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "Backspace",
            "code": "Backspace", "windowsVirtualKeyCode": 8,
        })
        await self.type_text(text, delay_ms=delay_ms)
        return True

    async def select_option(self, selector: str, value: str = "",
                            label: str = "", index: int = -1) -> bool:
        """Select an option in a <select> dropdown by value/label/index."""
        cond = "undefined"
        if value:
            cond = f"v === {json.dumps(value)}"
        elif label:
            cond = f"o.textContent.trim() === {json.dumps(label)}"
        elif index >= 0:
            cond = f"i === {index}"
        result = await self.evaluate(
            f"""(() => {{
                const sel = document.querySelector({json.dumps(selector)});
                if (!sel || sel.tagName !== 'SELECT') return false;
                const opts = Array.from(sel.options);
                for (let i = 0; i < opts.length; i++) {{
                    const o = opts[i], v = o.value;
                    if ({cond}) {{
                        sel.value = v;
                        sel.dispatchEvent(new Event('change', {{bubbles: true}}));
                        sel.dispatchEvent(new Event('input', {{bubbles: true}}));
                        return true;
                    }}
                }}
                return false;
            }})()"""
        )
        return bool(result)

    async def wait_for_selector(self, selector: str,
                                timeout: float = 10.0) -> bool:
        """Wait until an element matching the selector appears."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.evaluate(
                f"!!document.querySelector({json.dumps(selector)})"
            ):
                return True
            await asyncio.sleep(0.15)
        return False

    async def upload_file(self, selector: str, path: str) -> bool:
        """Set files on an <input type=file> element."""
        await self.send("DOM.enable")
        doc = await self.send("DOM.getDocument")
        root = doc.get("root", {}).get("nodeId", 0)
        q = await self.send("DOM.querySelector", {
            "nodeId": root, "selector": selector,
        })
        node_id = q.get("nodeId", 0)
        if not node_id:
            return False
        await self.send("DOM.setFileInputFiles", {
            "nodeId": node_id, "files": [os.path.abspath(path)],
        })
        return True

    async def gc(self):
        """Trigger Chrome's internal GC + cache drop (memory hygiene)."""
        try:
            await self.send("Memory.simulatePressureNotification", {
                "level": "moderate",
            })
            await self.send("Memory.simulatePressureNotification", {
                "level": "none",
            })
            await self.evaluate("""
                if (window.gc) { window.gc(); }
            """)
        except Exception:
            pass

    async def scroll_to_bottom(self, steps: int = 5):
        """Gradually scroll to page bottom."""
        for _ in range(steps):
            await self.evaluate(
                f"window.scrollBy(0, document.body.scrollHeight / {steps})"
            )
            await asyncio.sleep(random.uniform(0.15, 0.45))

    async def close(self):
        await self._transport.close_session(self._target_id)

    # ── Lifecycle ──────────────────────────────────────────────────

    def __repr__(self):
        return f"<CDPSession {self._target_id[:8]}>"


# ═══════════════════════════════════════════════════════════════════
# Quick Test
# ═══════════════════════════════════════════════════════════════════

async def test():
    """Quick smoke test."""
    t = CDPTransport()
    try:
        v = await t.start()
        print(f"Browser: {v.get('product', '?')} / {v.get('protocolVersion', '?')}")

        s = await t.create_session()
        print(f"Session: {s}")
        await s.enable()
        await s.navigate("https://bot.sannysoft.com/", wait_until="load")
        title = await s.get_title()
        print(f"Title: {title}")
        text = await s.get_text()
        print(f"Text length: {len(text)}")

        webdriver = await s.evaluate("navigator.webdriver")
        print(f"navigator.webdriver: {webdriver}")

        await s.close()
    finally:
        await t.astop()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG if "--debug" in sys.argv else logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s [%(name)s] %(message)s",
    )
    asyncio.run(test())