import asyncio
import json
import os
import random
import subprocess
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

import websockets

from .resources import get_asset

DEBUG_PORT = 9222
DEFAULT_PORT = 8080
DEFAULT_HOME = Path.home() / ".aistudio-bridge"

# Loaded from assets
VISUALIZER_JS = get_asset("visualizer.js")
BRIDGE_FETCH_STREAM_JS_FUNC = get_asset("fetch_stream.js")


class ChromeBridge:
    def __init__(self, app_id: str, profile_dir: str, use_visuals: bool, chrome_binary: str = "google-chrome"):
        self.app_id = app_id
        self.profile_dir = profile_dir
        self.use_visuals = use_visuals
        self.chrome_binary = chrome_binary
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

        # Streams registry
        self.streams = {}  # reqId -> {"meta": Future, "queue": Queue}

    async def _send_cmd(self, method, params=None, session_id=None):
        cmd_id = self.msg_id
        self.msg_id += 1
        payload = {"id": cmd_id, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        try:
            await self.ws.send(json.dumps(payload))
        except Exception as e:
            print(f"[!] WS SEND FAILED: {e}")
            raise
        return cmd_id

    async def _set_hud(self, text, success=False):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {text}")
        if self.use_visuals:
            try:
                success_str = "true" if success else "false"
                await self._send_cmd(
                    "Runtime.evaluate",
                    {"expression": f"if (window.__viz) window.__viz.update({json.dumps(text)}, {success_str})"},
                )
            except Exception:
                pass

    async def launch(self):
        attempt = 0
        while True:
            try:
                await self._do_launch()
                break
            except Exception as e:
                attempt += 1
                print(f"[!] Launch attempt {attempt} failed: {e}")
                print("Retrying in 10s...")
                await asyncio.sleep(10)

    async def _do_launch(self):
        print(f"[{datetime.now().isoformat()}] BOOTSTRAPPING BRIDGE FOR: {self.app_id}")

        binary_name = os.path.basename(self.chrome_binary)
        print(f"Cleaning up existing {binary_name} instances...")
        subprocess.run(["pkill", "-9", "-x", binary_name], stderr=subprocess.DEVNULL)
        await asyncio.sleep(2)

        flags = [
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={self.profile_dir}",
            "--disable-dev-shm-usage",
            "--window-size=1280,800",
            "--window-position=0,0",
            f"--app={self.target_url}",
        ]

        print(f"Launching Chrome from: {self.chrome_binary}")
        try:
            subprocess.Popen([self.chrome_binary] + flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"[!] FAILED TO LAUNCH CHROME: {e}")
            raise

        print(f"Waiting for Chrome CDP on port {DEBUG_PORT}...")
        ws_url = None
        for i in range(30):
            try:
                req = urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json")
                pages = json.loads(req.read())
                page = next(
                    p for p in pages if p["type"] == "page" and ("aistudio" in p["url"] or p["url"] == "about:blank")
                )
                ws_url = page["webSocketDebuggerUrl"]
                print(f"Connected to Chrome CDP: {ws_url}")
                break
            except Exception:
                if i % 5 == 0:
                    print(f"Still waiting for CDP... ({i}/30)")
                await asyncio.sleep(1)

        if not ws_url:
            raise Exception(f"Failed to find websocket URL on port {DEBUG_PORT}. Is Chrome running?")

        print("Establishing WebSocket connection...")
        self.ws = await websockets.connect(ws_url, ping_interval=None)

        await self._send_cmd("Runtime.enable")
        await self._send_cmd("Page.enable")
        await self._send_cmd("Page.bringToFront")

        if self.use_visuals:
            await self._send_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": VISUALIZER_JS})
            await self._send_cmd("Runtime.evaluate", {"expression": VISUALIZER_JS})

        await self._send_cmd(
            "Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True}
        )

        asyncio.create_task(self._listener())

        await self._send_cmd("Page.navigate", {"url": self.target_url})

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
                if "accounts.google.com" in current_url:
                    if not auth_warning_shown:
                        print("\n" + "!" * 60)
                        print("[!] AUTHENTICATION REQUIRED: Please log in in the Chrome window.")
                        print("!" * 60 + "\n")
                        await self._send_cmd("Page.bringToFront")
                        auth_warning_shown = True
                    await self._set_hud("LOGIN REQUIRED")
                elif self.app_id in current_url:
                    break
            except Exception:
                pass
            finally:
                self.pending_evals.pop(eval_id, None)
            await asyncio.sleep(2)

        await self._set_hud("WAITING FOR OOPIF...")

        try:
            self.oopif_ready.clear()
            await asyncio.wait_for(self.oopif_ready.wait(), timeout=15)
            await self._set_hud("OOPIF ATTACHED. SETTLING...")
            await asyncio.sleep(4)
        except asyncio.TimeoutError:
            await self._set_hud("OOPIF TIMEOUT (PROCEEDING)")
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
        await self._set_hud("WAITING ON PING...")
        ping_js = get_asset("ping.js", GEMINI_API_KEY="MY_GEMINI_API_KEY")

        for _ in range(120):  # Wait up to ~4 mins
            eval_id = await self._send_cmd(
                "Runtime.evaluate",
                {"expression": ping_js, "awaitPromise": True, "returnByValue": True},
                session_id=self.target_sid,
            )

            future = asyncio.Future()
            self.pending_evals[eval_id] = future
            try:
                res = await asyncio.wait_for(future, timeout=5.0)
                val = res.get("result", {}).get("result", {}).get("value")

                if val == 401 or val == 403:
                    await self._set_hud("PING: AUTH ERROR", success=False)
                    if not auth_warning_shown:
                        print(
                            "\n[!] PING FAILED (Auth Error). Your session might have expired. Please refresh/login in Chrome."
                        )
                        auth_warning_shown = True
                elif val and val != -1:
                    break
            except asyncio.TimeoutError:
                pass
            finally:
                self.pending_evals.pop(eval_id, None)
            await asyncio.sleep(2)

        self.proxy_ready = True
        self.consecutive_failures = 0
        await self._set_hud("PROXY READY.", success=True)
        print("\n[✓] BRIDGE INITIALIZATION COMPLETE. PROXY READY.")

    async def _listener(self):
        try:
            async for msg in self.ws:
                try:
                    data = json.loads(msg)
                    if "id" in data and data["id"] in self.pending_evals:
                        self.pending_evals[data["id"]].set_result(data)

                    method = data.get("method")
                    if method == "Target.attachedToTarget":
                        if data["params"]["targetInfo"]["type"] == "iframe":
                            self.target_sid = data["params"]["sessionId"]
                            self.oopif_ready.set()

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
        except Exception as e:
            print(f"[!] LISTENER CRASHED: {e}")
            self.proxy_ready = False

    async def _maintenance_loop(self):
        cx, cy = 500, 0
        while True:
            # 1. Jiggle mouse
            is_app = random.random() > 0.5
            tx, ty = (
                (random.randint(400, 700), random.randint(300, 550))
                if is_app
                else (random.randint(200, 900), random.randint(15, 45))
            )

            try:
                steps = 15
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
            except Exception:
                pass

            # 2. Warmup Ping
            if self.proxy_ready and not self.is_recovering:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [MAINTENANCE] Sending warmup ping...")
                req_id = f"warmup-{uuid.uuid4()}"
                try:
                    # Use the same logic as init ping, but through the stream executor
                    ping_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={'MY_GEMINI_API_KEY'}"
                    meta_fut, queue = await self.execute_fetch_stream(
                        ping_url,
                        "GET",
                        {},
                        None,
                        req_id,
                    )
                    res = await asyncio.wait_for(meta_fut, timeout=15)
                    if res.get("status") == 200:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] [MAINTENANCE] Warmup success.")
                        self._check_health(True)
                    else:
                        print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] [MAINTENANCE] Warmup failed: {res.get('status')} {res.get('error', '')}"
                        )
                        self._check_health(False)
                except Exception as e:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] [MAINTENANCE] Warmup exception: {e}")
                    self._check_health(False)
                finally:
                    self.streams.pop(req_id, None)

            # 3. Flexible Sleep
            if self.proxy_ready:
                await asyncio.sleep(random.uniform(300, 900))  # 5-15 mins
            else:
                await asyncio.sleep(random.uniform(0.5, 1.5))

    def _check_health(self, success: bool):
        if success:
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1
            if self.consecutive_failures >= 3 and not self.is_recovering:
                print(f"[!] Health check failed {self.consecutive_failures} times. Triggering recovery...")
                asyncio.create_task(self.recover())

    async def recover(self):
        if self.is_recovering:
            return
        self.is_recovering = True
        self.proxy_ready = False

        try:
            # Tier 1: Page Refresh
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [RECOVERY] Tier 1: Refreshing page...")
            await self._set_hud("RECOVERY: REFRESHING")
            await self._send_cmd("Page.reload")

            # Re-init attempt
            try:
                await self._do_launch()  # Re-runs auth checks and ping
                print("[✓] Recovery Tier 1 (Refresh) successful.")
                return
            except Exception as e:
                print(f"[!] Recovery Tier 1 failed: {e}")

            # Tier 2: Full Restart
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [RECOVERY] Tier 2: Full browser restart...")
            await self._set_hud("RECOVERY: RESTARTING")
            if self.ws:
                try:
                    await self.ws.close()
                except Exception:
                    pass

            await self.launch()
            print("[✓] Recovery Tier 2 (Restart) complete.")

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
