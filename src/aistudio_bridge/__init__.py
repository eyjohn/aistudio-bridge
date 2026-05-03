import argparse
import asyncio
import json
import subprocess
import urllib.request
import time
import os
import websockets
import random
import base64
import uuid
import sys
import yaml
import shutil
from pathlib import Path
from datetime import datetime
from aiohttp import web

CHROME_PATH = "google-chrome"
DEBUG_PORT = 9222
DEFAULT_PORT = 8080
DEFAULT_HOME = Path.home() / ".aistudio-bridge"

VISUALIZER_JS = """
(function() {
    const ID = 'viz-lifeline-badge';
    const CURSOR_ID = 'viz-lifeline-cursor';

    function ensureViz() {
        if (!document.body || document.getElementById(ID)) return;
        
        const style = document.createElement('style');
        style.textContent = `
            #${ID} {
                position: fixed !important; top: 10px !important; left: 10px !important; 
                width: 250px !important; height: 50px !important;
                background: rgba(255, 0, 0, 0.9) !important; 
                color: #ffffff !important; 
                font-family: 'Courier New', monospace !important;
                z-index: 2147483647 !important; pointer-events: none !important;
                display: flex !important; align-items: center !important;
                justify-content: center !important; padding: 5px !important; 
                font-size: 14px !important; font-weight: bold !important; 
                border: 2px solid yellow !important; border-radius: 4px !important;
                text-align: center !important;
            }
            #${CURSOR_ID} {
                position: fixed !important; width: 30px !important; height: 30px !important;
                border: 3px solid cyan !important; border-radius: 50% !important;
                background: rgba(0, 255, 255, 0.3) !important;
                z-index: 2147483646 !important; pointer-events: none !important;
                transform: translate(-50%, -50%) !important;
            }
        `;
        document.head.appendChild(style);

        const badge = document.createElement('div');
        badge.id = ID;
        const textSpan = document.createElement('span');
        textSpan.textContent = 'BRIDGE: STARTING...';
        badge.appendChild(textSpan);
        document.body.appendChild(badge);

        const cursor = document.createElement('div');
        cursor.id = CURSOR_ID;
        document.body.appendChild(cursor);
    }

    if (window.trustedTypes && window.trustedTypes.createPolicy && !window.trustedTypes.defaultPolicy) {
        try { window.trustedTypes.createPolicy('default', { createHTML: (s) => s, createScriptURL: (s) => s, createScript: (s) => s }); } catch (e) {}
    }

    setInterval(ensureViz, 500);
    ensureViz();

    window.__viz = {
        update: (s, isSuccess=false) => { 
            const el = document.querySelector(`#${ID} span`); 
            const badge = document.getElementById(ID);
            if (el) el.textContent = 'STATE: ' + s.toUpperCase(); 
            if (isSuccess && badge) {
                badge.style.background = 'rgba(0, 255, 0, 0.9)';
                badge.style.color = '#000';
            }
        },
        move: (x, y) => { 
            const el = document.getElementById(CURSOR_ID); 
            if (el) { el.style.left = x + 'px'; el.style.top = y + 'px'; } 
        }
    };
})();
"""

BRIDGE_FETCH_STREAM_JS_FUNC = """
async function(url, method, headers, bodyText, reqId) {
    try {
        const fetchOptions = { method };
        if (headers && Object.keys(headers).length > 0) fetchOptions.headers = headers;
        if (bodyText) fetchOptions.body = bodyText;

        const res = await fetch(url, fetchOptions);
        
        // Report status and headers
        window.__stream_meta(JSON.stringify({
            reqId: reqId,
            status: res.status,
            headers: Object.fromEntries(res.headers.entries())
        }));

        const reader = res.body.getReader();
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                window.__stream_chunk(JSON.stringify({reqId: reqId, done: true}));
                break;
            }
            // Convert binary chunk to base64
            let binary = '';
            for (let i = 0; i < value.byteLength; i++) {
                binary += String.fromCharCode(value[i]);
            }
            window.__stream_chunk(JSON.stringify({reqId: reqId, chunk: btoa(binary)}));
        }
    } catch(e) {
        window.__stream_meta(JSON.stringify({reqId: reqId, error: e.toString()}));
    }
}
"""

class ChromeBridge:
    def __init__(self, app_id: str, profile_dir: str, use_visuals: bool, chrome_binary: str = "google-chrome"):
        self.app_id = app_id
        self.profile_dir = profile_dir
        self.use_visuals = use_visuals
        self.chrome_binary = chrome_binary
        self.target_url = f"https://aistudio.google.com/apps/{app_id}?fullscreenApplet=true&showPreview=true&showAssistant=true"
        self.ws = None
        self.msg_id = 1
        self.target_sid = None
        self.pending_evals = {}
        self.oopif_ready = asyncio.Event()
        self.keep_alive_task = None
        self.proxy_ready = False
        
        # Streams registry
        self.streams = {}  # reqId -> {"meta": Future, "queue": Queue}

    async def _send_cmd(self, method, params=None, session_id=None):
        cmd_id = self.msg_id
        self.msg_id += 1
        payload = {"id": cmd_id, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        await self.ws.send(json.dumps(payload))
        return cmd_id

    async def _set_hud(self, text, success=False):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {text}")
        if self.use_visuals:
            success_str = "true" if success else "false"
            await self._send_cmd("Runtime.evaluate", {"expression": f"if (window.__viz) window.__viz.update({json.dumps(text)}, {success_str})"})

    async def launch(self):
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
            f"--app={self.target_url}"
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
                page = next(p for p in pages if p["type"] == "page" and ("aistudio" in p["url"] or p["url"] == "about:blank"))
                ws_url = page["webSocketDebuggerUrl"]
                print(f"Connected to Chrome CDP: {ws_url}")
                break
            except Exception:
                if i % 5 == 0: print(f"Still waiting for CDP... ({i}/30)")
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
        
        await self._send_cmd("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
        
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
                        print("\n" + "!"*60)
                        print("[!] AUTHENTICATION REQUIRED: Please log in in the Chrome window.")
                        print("!"*60 + "\n")
                        await self._send_cmd("Page.bringToFront")
                        auth_warning_shown = True
                    await self._set_hud("LOGIN REQUIRED")
                elif self.app_id in current_url:
                    break
            except:
                pass
            finally:
                self.pending_evals.pop(eval_id, None)
            await asyncio.sleep(2)

        await self._set_hud("WAITING FOR OOPIF...")

        try:
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

        # 2. Start the mouse jiggler in fast mode to quickly unblock the UI
        self.proxy_ready = False
        self.keep_alive_task = asyncio.create_task(self._mouse_jiggler())

        # 3. Fire the ping and wait for it to succeed
        await self._set_hud("WAITING ON PING...")
        ping_js = "fetch('https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest?key=MY_GEMINI_API_KEY').then(r => r.status).catch(e => -1)"
        
        for _ in range(120):  # Wait up to ~4 mins
            eval_id = await self._send_cmd("Runtime.evaluate", {
                "expression": ping_js,
                "awaitPromise": True,
                "returnByValue": True
            }, session_id=self.target_sid)
            
            future = asyncio.Future()
            self.pending_evals[eval_id] = future
            try:
                res = await asyncio.wait_for(future, timeout=5.0)
                val = res.get("result", {}).get("result", {}).get("value")
                
                if val == 401 or val == 403:
                    await self._set_hud("PING: AUTH ERROR", success=False)
                    if not auth_warning_shown:
                        print("\n[!] PING FAILED (Auth Error). Your session might have expired. Please refresh/login in Chrome.")
                        auth_warning_shown = True
                elif val and val != -1:
                    break
            except asyncio.TimeoutError:
                pass
            finally:
                self.pending_evals.pop(eval_id, None)
            await asyncio.sleep(2)

        self.proxy_ready = True
        await self._set_hud("PROXY READY.", success=True)
        print("\n[✓] BRIDGE INITIALIZATION COMPLETE. PROXY READY.")

    async def _listener(self):
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

    async def _mouse_jiggler(self):
        cx, cy = 500, 0
        while True:
            is_app = random.random() > 0.5
            tx, ty = (random.randint(400, 700), random.randint(300, 550)) if is_app else (random.randint(200, 900), random.randint(15, 45))
            
            steps = 15
            for i in range(steps):
                t = i/steps
                mx, my = cx + (tx-cx)*t, cy + (ty-cy)*t
                await self._send_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": int(mx), "y": int(my), "button": "none"})
                if self.use_visuals:
                    await self._send_cmd("Runtime.evaluate", {"expression": f"if (window.__viz) window.__viz.move({int(mx)}, {int(my)})"})
                await asyncio.sleep(0.01)
            cx, cy = tx, ty
            
            await self._send_cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": tx, "y": ty, "button": "left", "clickCount": 1})
            await asyncio.sleep(0.1)
            await self._send_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": tx, "y": ty, "button": "left", "clickCount": 1})
            
            if getattr(self, "proxy_ready", False):
                await asyncio.sleep(random.uniform(5, 15))
            else:
                await asyncio.sleep(random.uniform(0.5, 1.5))

    async def execute_fetch_stream(self, url: str, method: str, headers: dict, body_text: str, req_id: str):
        if not self.target_sid:
            raise Exception("Bridge not ready (no target SID)")

        meta_future = asyncio.Future()
        chunk_queue = asyncio.Queue()
        self.streams[req_id] = {"meta": meta_future, "queue": chunk_queue}

        eval_expr = f"({BRIDGE_FETCH_STREAM_JS_FUNC})({json.dumps(url)}, {json.dumps(method)}, {json.dumps(headers)}, {json.dumps(body_text)}, {json.dumps(req_id)})"
        
        await self._send_cmd("Runtime.evaluate", {
            "expression": eval_expr,
            "awaitPromise": False
        }, session_id=self.target_sid)
        
        return meta_future, chunk_queue

class ProxyServer:
    def __init__(self, bridge: ChromeBridge, target_base: str):
        self.bridge = bridge
        self.target_base = target_base.rstrip('/')

    async def handle_request(self, request: web.Request):
        url = f"{self.target_base}{request.path_qs}"
        method = request.method
        
        headers = dict(request.headers)
        if "Host" in headers:
            del headers["Host"]
        if "Accept-Encoding" in headers:
            del headers["Accept-Encoding"]
            
        body_bytes = await request.read()
        body_text = body_bytes.decode('utf-8', errors='ignore') if body_bytes else None

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Proxying (Stream): {method} {url}")

        req_id = str(uuid.uuid4())
        
        try:
            meta_future, chunk_queue = await self.bridge.execute_fetch_stream(url, method, headers, body_text, req_id)
            meta = await asyncio.wait_for(meta_future, timeout=30.0)
            
            if "error" in meta:
                return web.Response(status=500, text=f"Proxy Fetch Error: {meta['error']}")
                
            resp_headers = meta.get("headers", {})
            if "content-encoding" in resp_headers:
                del resp_headers["content-encoding"]
            if "transfer-encoding" in resp_headers:
                del resp_headers["transfer-encoding"]

            response = web.StreamResponse(status=meta.get("status", 200), headers=resp_headers)
            await response.prepare(request)
            
            while True:
                chunk_data = await chunk_queue.get()
                if chunk_data.get("done"):
                    break
                if "chunk" in chunk_data:
                    chunk_bytes = base64.b64decode(chunk_data["chunk"])
                    await response.write(chunk_bytes)
                    
            return response

        except asyncio.TimeoutError:
            return web.Response(status=504, text="Gateway Timeout: Fetch took too long to resolve headers.")
        except Exception as e:
            return web.Response(status=500, text=f"Proxy Exception: {str(e)}")
        finally:
            self.bridge.streams.pop(req_id, None)

    async def start(self, port: int):
        app = web.Application()
        app.router.add_route('*', '/{path_info:.*}', self.handle_request)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        print(f"HTTP Reverse Proxy listening on http://0.0.0.0:{port}")
        print(f"Forwarding all relative paths to: {self.target_base}")

def get_service_file():
    cwd = os.getcwd()
    is_dev = (Path(cwd) / "pyproject.toml").exists()
    uv_path = shutil.which("uv") or "uv"
    
    # Capture current display environment to make the background service work
    display = os.environ.get("DISPLAY")
    wayland = os.environ.get("WAYLAND_DISPLAY")
    env_lines = ""
    if display: env_lines += f"Environment=DISPLAY={display}\n"
    if wayland: env_lines += f"Environment=WAYLAND_DISPLAY={wayland}\n"
    
    if is_dev:
        return f"""[Unit]
Description=AI Studio Streaming Bridge (Dev)
After=network.target

[Service]
Type=simple
WorkingDirectory={cwd}
ExecStart={uv_path} run aistudio-bridge
Restart=always
{env_lines}
[Install]
WantedBy=default.target
"""
    else:
        exec_path = sys.argv[0]
        return f"""[Unit]
Description=AI Studio Streaming Bridge
After=network.target

[Service]
Type=simple
ExecStart={exec_path}
Restart=always
{env_lines}
[Install]
WantedBy=default.target
"""

def manage_service(install=True):
    svc_dir = Path.home() / ".config" / "systemd" / "user"
    svc_file = svc_dir / "aistudio-bridge.service"
    
    if install:
        svc_dir.mkdir(parents=True, exist_ok=True)
        svc_file.write_text(get_service_file())
        subprocess.run(["systemctl", "--user", "daemon-reload"])
        subprocess.run(["systemctl", "--user", "enable", "aistudio-bridge.service"])
        subprocess.run(["systemctl", "--user", "restart", "aistudio-bridge.service"])
        print(f"[✓] Service installed and started at {svc_file}")
        print("Use 'systemctl --user status aistudio-bridge' to check status.")
    else:
        subprocess.run(["systemctl", "--user", "stop", "aistudio-bridge.service"])
        subprocess.run(["systemctl", "--user", "disable", "aistudio-bridge.service"])
        if svc_file.exists():
            svc_file.unlink()
        print("[✓] Service stopped and removed.")

def main():
    parser = argparse.ArgumentParser(description="AI Studio Streaming Bridge")
    parser.add_argument("app_id", nargs="?", help="The App ID UUID")
    parser.add_argument("--port", type=int, help=f"Proxy port (default: {DEFAULT_PORT})")
    parser.add_argument("--profile-dir", help="Absolute path to the Chrome profile directory")
    parser.add_argument("--visual-overlay", action="store_true", help="Enable the HUD status badge")
    parser.add_argument("--chrome-binary", default="google-chrome", help="Path to the Chrome binary")
    parser.add_argument("--target-api", default="https://generativelanguage.googleapis.com", help="Target API Base URL")
    
    parser.add_argument("--install", action="store_true", help="Install as systemd user service")
    parser.add_argument("--uninstall", action="store_true", help="Uninstall systemd user service")
    parser.add_argument("--config", action="store_true", help="Show current config and exit")

    args = parser.parse_args()

    DEFAULT_HOME.mkdir(parents=True, exist_ok=True)
    config_path = DEFAULT_HOME / "config.yaml"
    
    config = {}
    if config_path.exists():
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}

    if args.app_id: config["app_id"] = args.app_id
    if args.port: config["port"] = args.port
    if args.profile_dir: config["profile_dir"] = args.profile_dir
    if args.chrome_binary: config["chrome_binary"] = args.chrome_binary
    if args.target_api: config["target_api"] = args.target_api
    config["visual_overlay"] = args.visual_overlay or config.get("visual_overlay", False)

    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)

    if args.install:
        if not config.get("app_id"):
            parser.error("app_id is required to install the service.")
        manage_service(install=True)
        return
    if args.uninstall:
        manage_service(install=False)
        return

    if args.config:
        print(yaml.dump(config, default_flow_style=False))
        return

    app_id = config.get("app_id")
    profile_dir = config.get("profile_dir") or str(DEFAULT_HOME / "profile")
    port = config.get("port") or DEFAULT_PORT
    
    if not app_id:
        parser.error("app_id is required (via argument or config.yaml)")

    async def run():
        bridge = ChromeBridge(app_id, profile_dir, config["visual_overlay"], config.get("chrome_binary", "google-chrome"))
        await bridge.launch()
        server = ProxyServer(bridge, config.get("target_api", "https://generativelanguage.googleapis.com"))
        await server.start(port)
        while True: await asyncio.sleep(3600)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nAborted.")

if __name__ == "__main__":
    main()
