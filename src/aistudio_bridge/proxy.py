import asyncio
import base64
import uuid
from datetime import datetime

from aiohttp import web

from .bridge import ChromeBridge


class ProxyServer:
    def __init__(self, bridge: ChromeBridge, target_base: str):
        self.bridge = bridge
        self.target_base = target_base.rstrip("/")

    async def handle_request(self, request: web.Request):
        url = f"{self.target_base}{request.path_qs}"
        method = request.method

        headers = dict(request.headers)
        if "Host" in headers:
            del headers["Host"]
        if "Accept-Encoding" in headers:
            del headers["Accept-Encoding"]

        body_bytes = await request.read()
        body_text = body_bytes.decode("utf-8", errors="ignore") if body_bytes else None

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Proxying (Stream): {method} {url}")

        req_id = str(uuid.uuid4())

        try:
            meta_future, chunk_queue = await self.bridge.execute_fetch_stream(url, method, headers, body_text, req_id)
            meta = await asyncio.wait_for(meta_future, timeout=70.0)  # slightly more than JS timeout

            if "error" in meta:
                self.bridge._check_health(False)
                return web.Response(status=500, text=f"Proxy Fetch Error: {meta['error']}")

            self.bridge._check_health(True)
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
            self.bridge._check_health(False)
            return web.Response(status=504, text="Gateway Timeout: Fetch took too long to resolve headers.")
        except Exception as e:
            self.bridge._check_health(False)
            return web.Response(status=500, text=f"Proxy Exception: {str(e)}")
        finally:
            self.bridge.streams.pop(req_id, None)

    async def start(self, port: int):
        app = web.Application()
        app.router.add_route("*", "/{path_info:.*}", self.handle_request)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        print(f"HTTP Reverse Proxy listening on http://0.0.0.0:{port}")
        print(f"Forwarding all relative paths to: {self.target_base}")
