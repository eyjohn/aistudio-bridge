import asyncio
import json
import logging
import os
import random
import signal
import subprocess
import urllib.request
from pathlib import Path

import websockets

from .resources import get_asset

# Ensure fresh entropy for maintenance patterns
random.seed()

DEBUG_PORT = 9222
DEFAULT_PORT = 8080
DEFAULT_HOME = Path.home() / ".aistudio-bridge"

# Loaded from assets
VISUALIZER_JS = get_asset("visualizer.js")
BRIDGE_FETCH_STREAM_JS_FUNC = get_asset("fetch_stream.js")

logger = logging.getLogger("aistudio-bridge.bridge")


class ChromeBridge:
    def __init__(
        self,
        app_id: str,
        profile_dir: str,
        use_visuals: bool,
        chrome_binary: str = "google-chrome",
        verbose: bool = False,
    ):
        self.app_id = app_id
        self.profile_dir = profile_dir
        self.use_visuals = use_visuals
        self.chrome_binary = chrome_binary
        self.verbose = verbose
        self.target_url = (
            f"https://aistudio.google.com/apps/{app_id}?fullscreenApplet=true&showPreview=true&showAssistant=true"
        )
        self.ws = None
        self.msg_id = 1
        self.target_sid = None
        self.pending_evals = {}
        self.oopif_ready = asyncio.Event()
        self.maintenance_task = None
        self.proxy_ready = False
        self.consecutive_failures = 0
        self.is_recovering = False
        self.chrome_proc = None

        # Streams registry
        self.streams = {}  # reqId -> {"meta": Future, "queue": Queue}
        self.is_checking_health = False
        self.active_streams = 0
        self.last_status_text = "Initializing..."
        self.last_status_type = "neutral"
        self.last_mouse_pos = (500, 400)

    async def _send_cmd(self, method, params=None, session_id=None):
        cmd_id = self.msg_id
        self.msg_id += 1
        payload = {"id": cmd_id, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        try:
            await self.ws.send(json.dumps(payload))
        except Exception as e:
            logger.error(f"WS SEND FAILED: {e}")
            raise
        return cmd_id

    async def _set_hud(self, text, type="neutral"):
        # Priority Logic: Recovery > Health Check > Requests > Idle/Jiggle
        if self.is_recovering and type != "recovery":
            return
        if self.is_checking_health and type not in ("warning", "error", "recovery"):
            return

        self.last_status_text = text
        self.last_status_type = type

        if not self.use_visuals:
            return
        try:
            has_reqs = "true" if self.active_streams > 0 else "false"
            await self._send_cmd(
                "Runtime.evaluate",
                {"expression": f"if (window.__viz) window.__viz.update({json.dumps(text)}, '{type}', {has_reqs})"},
            )
        except Exception as e:
            logger.warning(f"HUD UPDATE FAILED: {e}")

    async def _refresh_hud(self):
        """Restore HUD to best available state based on current activity."""
        if self.is_recovering:
            await self._set_hud("Hard Recovery...", type="recovery")
        elif self.is_checking_health:
            await self._set_hud("Health Check", type="warning")
        elif self.active_streams > 0:
            await self._set_hud("Proxying Stream", type="success")
        elif self.proxy_ready:
            await self._set_hud("Bridge Ready", type="success")
        else:
            await self._set_hud("Initializing...", type="neutral")

    async def launch(self):
        attempt = 0
        while True:
            try:
                await self._do_launch()
                break
            except Exception as e:
                attempt += 1
                logger.error(f"Launch attempt {attempt} failed: {e}")
                logger.info("Retrying in 10s...")
                await asyncio.sleep(10)

    async def _do_launch(self):
        logger.info(f"Bootstrapping bridge for: {self.app_id}")
        logger.info("Cleaning up existing bridge instances...")
        subprocess.run(["pkill", "-9", "-f", f"--user-data-dir={self.profile_dir}"], stderr=subprocess.DEVNULL)
        await asyncio.sleep(2)

        flags = [
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={self.profile_dir}",
            "--disable-dev-shm-usage",
            "--window-size=1280,800",
            "--window-position=0,0",
            f"--app={self.target_url}",
        ]

        logger.info(f"Launching Chrome from: {self.chrome_binary}")
        try:
            self.chrome_proc = subprocess.Popen(
                [self.chrome_binary] + flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid
            )
        except Exception as e:
            logger.error(f"FAILED TO LAUNCH CHROME: {e}")
            raise

        logger.info(f"Waiting for Chrome CDP on port {DEBUG_PORT}...")
        ws_url = None
        for i in range(30):
            try:
                req = urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json")
                pages = json.loads(req.read())
                page = next(
                    p for p in pages if p["type"] == "page" and ("aistudio" in p["url"] or p["url"] == "about:blank")
                )
                ws_url = page["webSocketDebuggerUrl"]
                logger.info(f"Connected to Chrome CDP: {ws_url}")
                break
            except Exception:
                if i % 5 == 0:
                    logger.info(f"Still waiting for CDP... ({i}/30)")
                await asyncio.sleep(1)

        if not ws_url:
            raise Exception(f"Failed to find websocket URL on port {DEBUG_PORT}. Is Chrome running?")

        logger.info("Establishing WebSocket connection...")
        try:
            self.ws = await websockets.connect(ws_url, ping_interval=None)
        except Exception as e:
            logger.error(f"FAILED TO CONNECT TO WS: {e}")
            raise

        await self._send_cmd("Runtime.enable")
        await self._send_cmd("Page.enable")
        await self._send_cmd("Page.bringToFront")

        if self.use_visuals:
            await self._send_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": VISUALIZER_JS})
            await self._send_cmd("Runtime.evaluate", {"expression": VISUALIZER_JS})

        await self._send_cmd(
            "Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True}
        )

        # Force visibility and disable idle throttling
        await self._send_cmd("Emulation.setFocusEmulation", {"enabled": True})
        try:
            await self._send_cmd("Emulation.setIdleOverride", {"isUserActive": True, "isScreenUnlocked": True})
        except Exception:
            pass  # Might not be supported in older Chrome versions

        await self._send_cmd("Page.setVisibilityState", {"state": "visible"})

        asyncio.create_task(self._listener())

        await self._send_cmd("Page.navigate", {"url": self.target_url})

        # Reinforce injection after navigation
        if self.use_visuals:
            await asyncio.sleep(1)
            await self._send_cmd("Runtime.evaluate", {"expression": VISUALIZER_JS})

        # 1. Proactive Auth Detection
        auth_warning_shown = False
        while True:
            # Check current URL via Runtime.evaluate
            eval_id = await self._send_cmd("Runtime.evaluate", {"expression": "window.location.href"})
            future = asyncio.Future()
            self.pending_evals[eval_id] = future
            try:
                res = await asyncio.wait_for(future, timeout=2.0)
                current_url = res.get("result", {}).get("result", {}).get("value", "")
                logger.debug(f"Auth loop check: {current_url}")
                if "accounts.google.com" in current_url:
                    if not auth_warning_shown:
                        logger.warning("Authentication required: Please log in via the Chrome window")
                        await self._send_cmd("Page.bringToFront")
                        auth_warning_shown = True
                    await self._set_hud("Login Required", type="warning")
                elif self.app_id in current_url:
                    break
            except Exception:
                pass
            finally:
                self.pending_evals.pop(eval_id, None)
            await asyncio.sleep(2)

        await self._set_hud("Waiting for Preview Frame", type="neutral")
        await asyncio.sleep(1)  # Extra settle time for HUD
        logger.info("Waiting for Preview Frame")

        try:
            self.oopif_ready.clear()
            await asyncio.wait_for(self.oopif_ready.wait(), timeout=15)
            await self._set_hud("Preview Frame attached", type="success")
            await asyncio.sleep(4)
        except asyncio.TimeoutError:
            await self._set_hud("Preview Frame timeout", type="warning")
            await asyncio.sleep(2)

        if not self.target_sid:
            raise Exception("No Target SID found. Cannot monitor bridge status.")

        # Register bindings for stream data passing
        await self._send_cmd("Runtime.addBinding", {"name": "__stream_meta"}, session_id=self.target_sid)
        await self._send_cmd("Runtime.addBinding", {"name": "__stream_chunk"}, session_id=self.target_sid)

        # 2. Start the maintenance loop
        self.proxy_ready = False
        if not self.maintenance_task or self.maintenance_task.done():
            self.maintenance_task = asyncio.create_task(self._maintenance_loop())

        # 3. Fire the ping and wait for it to succeed
        await self._set_hud("Waiting on ping", type="neutral")
        logger.info("Waiting for initial ping")
        ping_js = get_asset("ping.js", GEMINI_API_KEY="MY_GEMINI_API_KEY")

        for i in range(120):  # Wait up to ~4 mins
            eval_id = await self._send_cmd(
                "Runtime.evaluate",
                {"expression": ping_js, "awaitPromise": True, "returnByValue": True},
                session_id=self.target_sid,
            )
            logger.debug(f"Warmup ping attempt {i + 1} (eval_id: {eval_id})")

            future = asyncio.Future()
            self.pending_evals[eval_id] = future
            try:
                res = await asyncio.wait_for(future, timeout=5.0)
                val = res.get("result", {}).get("result", {}).get("value")
                logger.debug(f"Warmup ping result: {val}")

                if val == 401 or val == 403:
                    await self._set_hud("PING: AUTH ERROR", success=False)
                    if not auth_warning_shown:
                        logger.error(
                            "PING FAILED (Auth Error). Your session might have expired. Please refresh/login in Chrome."
                        )
                        auth_warning_shown = True
                elif val and val != -1:
                    logger.info(f"Initial ping successful (status: {val})")
                    break
            except asyncio.TimeoutError:
                logger.debug("Initial ping attempt timed out")
            finally:
                self.pending_evals.pop(eval_id, None)
            await asyncio.sleep(2)

        self.proxy_ready = True
        self.consecutive_failures = 0
        await self._set_hud("Bridge Ready", type="success")
        logger.info("Bridge initialization complete, proxy ready")

    async def _listener(self):
        try:
            async for msg in self.ws:
                try:
                    data = json.loads(msg)
                    if "method" in data:
                        # Skip extremely frequent stream binding events in debug logs
                        if data["method"] != "Runtime.bindingCalled":
                            logger.debug(f"CDP Event: {data['method']}")
                    elif "id" in data:
                        if "error" in data:
                            logger.error(f"CDP Response Error (ID:{data['id']}): {data['error']}")

                    if "id" in data and data["id"] in self.pending_evals:
                        self.pending_evals[data["id"]].set_result(data)

                    method = data.get("method")
                    if method == "Target.attachedToTarget":
                        target_info = data["params"]["targetInfo"]
                        logger.debug(f"Attached to {target_info['type']}: {target_info['url']}")
                        if target_info["type"] == "iframe":
                            self.target_sid = data["params"]["sessionId"]
                            self.oopif_ready.set()

                    if method == "Target.detachedFromTarget":
                        logger.debug(f"Detached from session: {data['params']['sessionId']}")

                    if method == "Runtime.bindingCalled":
                        name = data["params"]["name"]
                        payload = data["params"]["payload"]
                        if name in ("__stream_meta", "__stream_chunk"):
                            parsed = json.loads(payload)
                            reqId = parsed.get("reqId")
                            if reqId and reqId in self.streams:
                                if name == "__stream_meta":
                                    if not self.streams[reqId]["meta"].done():
                                        self.streams[reqId]["meta"].set_result(parsed)
                                elif name == "__stream_chunk":
                                    self.streams[reqId]["queue"].put_nowait(parsed)
                except Exception:
                    pass
        except Exception:
            logger.error("LISTENER CRASHED", exc_info=True)
            self.proxy_ready = False
            if not self.is_recovering:
                asyncio.create_task(self.recover())

            # 3. Aggressive Frequency (approx 1 min)
            if self.proxy_ready:
                await asyncio.sleep(random.uniform(45, 75))
            else:
                await asyncio.sleep(random.uniform(0.5, 1.5))

    def trigger_fast_check(self):
        """Called by proxy on request failure to force an immediate health check."""
        if self.proxy_ready and not self.is_recovering:
            # We can't easily 'jump' the maintenance loop sleep,
            # so we just fire a one-off check task.
            asyncio.create_task(self._do_heartbeat_check())

    async def _do_heartbeat_check(self):
        """Unified verification path: Immediate, 5s, 10s retries."""
        if self.is_checking_health or self.is_recovering:
            return

        self.is_checking_health = True
        try:
            # Pacing: 0s, 5s, 10s = ~15s total verification window
            retries = [0, 5, 10]
            for attempt, delay in enumerate(retries, 1):
                if delay > 0:
                    await asyncio.sleep(delay)

                status_text = f"Health Check ({attempt}/3)"
                await self._set_hud(status_text, type="warning")
                logger.info(f"Health verification (Attempt {attempt}/3, delay: {delay}s)...")
                try:
                    ping_js = get_asset("ping.js", GEMINI_API_KEY="MY_GEMINI_API_KEY")
                    eval_id = await self._send_cmd(
                        "Runtime.evaluate",
                        {"expression": ping_js, "awaitPromise": True, "returnByValue": True},
                        session_id=self.target_sid,
                    )
                    future = asyncio.Future()
                    self.pending_evals[eval_id] = future

                    # 1. Quick WS check
                    try:
                        pong_waiter = await self.ws.ping()
                        await asyncio.wait_for(pong_waiter, timeout=5.0)
                    except Exception:
                        raise Exception("Websocket stall")

                    # 2. App check
                    res = await asyncio.wait_for(future, timeout=10.0)
                    val = res.get("result", {}).get("result", {}).get("value")

                    if val and val != -1 and val < 400:
                        logger.info(f"Health verified (status: {val})")
                        self.consecutive_failures = 0
                        return True
                    else:
                        logger.warning(f"Verification failed (status: {val})")
                        if attempt == 1:
                            logger.info("First verification failed, attempting 'nudge' jiggle...")
                            asyncio.create_task(self._do_jiggle())
                except Exception as e:
                    logger.warning(f"Verification attempt {attempt} failed: {e}")
                    if attempt == 1:
                        logger.info("First verification failed, attempting 'nudge' jiggle...")
                        asyncio.create_task(self._do_jiggle())
                finally:
                    if "eval_id" in locals():
                        self.pending_evals.pop(eval_id, None)

            # All attempts failed -> Escalate to hard recovery
            logger.error("Verification exhausted. Escalating to recovery.")
            asyncio.create_task(self.recover())
            return False
        finally:
            self.is_checking_health = False

    async def _do_jiggle(self):
        """Perform a window-scaled mouse interaction to keep session active."""
        logger.info("Starting maintenance jiggle...")
        if self.is_recovering:
            return

        cx, cy = self.last_mouse_pos
        try:
            # 0. Get Window Size for scaling
            eval_id = await self._send_cmd(
                "Runtime.evaluate", {"expression": "[window.innerWidth, window.innerHeight]", "returnByValue": True}
            )
            fut = asyncio.Future()
            self.pending_evals[eval_id] = fut
            res = await asyncio.wait_for(fut, timeout=2.0)
            width, height = res["result"]["result"]["value"]
            logger.debug(f"Jiggle detected window: {width}x{height}")
        except Exception as e:
            logger.warning(f"Jiggle failed to get window size: {e}")
            width, height = 1280, 800
        finally:
            if "eval_id" in locals():
                self.pending_evals.pop(eval_id, None)

        # 1. Clear 'App has been paused' if present
        try:
            reload_script = """
            (() => {
                const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
                const reloadBtn = buttons.find(b => b.innerText && b.innerText.includes('Reload the app'));
                if (reloadBtn) {
                    reloadBtn.click();
                    return true;
                }
                return false;
            })()
            """
            eval_id = await self._send_cmd("Runtime.evaluate", {"expression": reload_script, "returnByValue": True})
            logger.debug(f"Auto-reload check (eval_id: {eval_id})")
            fut = asyncio.Future()
            self.pending_evals[eval_id] = fut
            res = await asyncio.wait_for(fut, timeout=2.0)
            val = res.get("result", {}).get("result", {}).get("value")
            logger.debug(f"Auto-reload check result: {val}")
            if val:
                logger.info("Detected 'App has been paused' overlay - clicked Reload.")
                await self._set_hud("App Reloaded", type="success")
                await asyncio.sleep(2)  # Give it a moment to reload
        except Exception:
            pass
        finally:
            if "eval_id" in locals():
                self.pending_evals.pop(eval_id, None)

        # 2. Move through 3 random points for more "activity"
        await self._set_hud("Jiggling Mouse", type="neutral")

        try:
            for _ in range(3):
                is_app = random.random() > 0.4
                if is_app:
                    tx = random.randint(int(width * 0.1), int(width * 0.6))
                    ty = random.randint(int(height * 0.2), int(height * 0.8))
                else:
                    tx = random.randint(int(width * 0.1), int(width * 0.5))
                    ty = random.randint(int(height * 0.02), int(height * 0.08))

                steps = 20
                for i in range(steps):
                    t = i / steps
                    mx, my = cx + (tx - cx) * t, cy + (ty - cy) * t
                    await self._send_cmd(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": int(mx), "y": int(my), "button": "none"},
                    )
                    if self.use_visuals:
                        await self._send_cmd(
                            "Runtime.evaluate",
                            {"expression": f"if (window.__viz) window.__viz.move({int(mx)}, {int(my)})"},
                        )
                    await asyncio.sleep(0.01)

                cx, cy = tx, ty
                await self._send_cmd(
                    "Input.dispatchMouseEvent",
                    {"type": "mousePressed", "x": tx, "y": ty, "button": "left", "clickCount": 1},
                )
                await asyncio.sleep(0.1)
                await self._send_cmd(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseReleased", "x": tx, "y": ty, "button": "left", "clickCount": 1},
                )

            self.last_mouse_pos = (cx, cy)
        except Exception:
            pass
        finally:
            await self._refresh_hud()

    async def _maintenance_loop(self):
        import time

        logger.info("Maintenance loop started.")
        last_jiggle = 0  # Trigger immediately on first loop
        jiggle_interval = random.uniform(300, 600)  # 5-10 min

        while True:
            # 1. Heartbeat check with retries every ~1 min
            if self.proxy_ready and not self.is_recovering:
                await self._do_heartbeat_check()
                await self._refresh_hud()

            # 2. Check for decoupled jiggle
            now = time.time()
            if now - last_jiggle > jiggle_interval:
                await self._do_jiggle()
                last_jiggle = now
                if self.proxy_ready:
                    jiggle_interval = random.uniform(300, 600)
                else:
                    # During warmup, jiggle less frequently to avoid interrupting loads
                    jiggle_interval = random.uniform(60, 120)

            # 3. Sleep (approx 1 min)
            if self.proxy_ready:
                await asyncio.sleep(random.uniform(45, 75))
            else:
                # During warmup, check more frequently
                await asyncio.sleep(5.0)

    async def recover(self):
        if self.is_recovering:
            return
        self.is_recovering = True
        self.proxy_ready = False
        self.consecutive_failures = 0

        try:
            logger.warning("Triggering hard recovery: Restarting browser session")
            await self._set_hud("Hard Recovery...", type="recovery")

            # 1. Atomic Termination
            if self.chrome_proc:
                logger.info(f"Terminating process group: {self.chrome_proc.pid}")
                try:
                    os.killpg(os.getpgid(self.chrome_proc.pid), signal.SIGKILL)
                except Exception:
                    pass
                self.chrome_proc = None

            if self.ws:
                try:
                    await self.ws.close()
                except Exception:
                    pass

            # 2. Re-launch
            await self.launch()
            logger.info("Hard recovery complete")

        finally:
            self.is_recovering = False

    async def execute_fetch_stream(self, url: str, method: str, headers: dict, body_text: str, req_id: str):
        if not self.target_sid:
            raise Exception("Bridge not ready (no target SID)")

        meta_future = asyncio.Future()
        chunk_queue = asyncio.Queue()
        self.streams[req_id] = {"meta": meta_future, "queue": chunk_queue}

        eval_expr = f"({BRIDGE_FETCH_STREAM_JS_FUNC})({json.dumps(url)}, {json.dumps(method)}, {json.dumps(headers)}, {json.dumps(body_text)}, {json.dumps(req_id)})"

        await self._send_cmd(
            "Runtime.evaluate", {"expression": eval_expr, "awaitPromise": False}, session_id=self.target_sid
        )

        return meta_future, chunk_queue
