import asyncio
import base64
import json
import logging
import uuid

from aiohttp import web

from .bridge import ChromeBridge

logger = logging.getLogger("aistudio-bridge.proxy")


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

        req_id = str(uuid.uuid4())
        try:
            meta_future, chunk_queue = await self.bridge.execute_fetch_stream(url, method, headers, body_text, req_id)
            meta = await asyncio.wait_for(meta_future, timeout=70.0)  # slightly more than JS timeout

            status = meta.get("status", 200)
            if "error" in meta:
                logger.error(f"Proxying (Stream) [500]: {method} {url} - Error: {meta['error']}")
                self.bridge.trigger_fast_check()
                return web.Response(status=500, text=f"Proxy Fetch Error: {meta['error']}")

            logger.info(f"Proxying (Stream) [{status}]: {method} {url}")
            self.bridge._check_health(True)
            resp_headers = meta.get("headers", {})
            if "content-encoding" in resp_headers:
                del resp_headers["content-encoding"]
            if "transfer-encoding" in resp_headers:
                del resp_headers["transfer-encoding"]

            response = web.StreamResponse(status=status, headers=resp_headers)
            await response.prepare(request)

            last_usage = None
            try:
                while True:
                    chunk_data = await chunk_queue.get()
                    if chunk_data.get("done"):
                        break
                    if "chunk" in chunk_data:
                        chunk_bytes = base64.b64decode(chunk_data["chunk"])

                        # Token Tracking: Try to find usageMetadata in the chunk
                        try:
                            text = chunk_bytes.decode("utf-8", errors="ignore")
                            if "usageMetadata" in text:
                                # Extract JSON from SSE format (data: {...})
                                for line in text.splitlines():
                                    if line.startswith("data:"):
                                        json_str = line[5:].strip()
                                        data = json.loads(json_str)
                                        usage = data.get("usageMetadata")
                                        if usage:
                                            last_usage = usage
                        except Exception:
                            pass

                        await response.write(chunk_bytes)

                if last_usage:
                    p = last_usage.get("promptTokenCount", 0)
                    ca = last_usage.get("cachedContentTokenCount", 0)
                    c = last_usage.get("candidatesTokenCount", 0)
                    th = last_usage.get("thoughtsTokenCount", 0)
                    t = last_usage.get("totalTokenCount", 0)
                    logger.info(f"[USAGE] Tokens: Prompt={p} (Cached={ca}), Output={c} (Thoughts={th}), Total={t}")

                logger.info(f"Proxying (Stream) [DONE]: {method} {url}")
            except (ConnectionResetError, asyncio.CancelledError):
                logger.info(f"Proxying (Stream) [ABORTED]: {method} {url}")
                raise

            return response

        except asyncio.TimeoutError:
            logger.error(f"Proxying (Stream) [504]: {method} {url} - Timeout")
            self.bridge.trigger_fast_check()
            return web.Response(status=504, text="Gateway Timeout: Fetch took too long to resolve headers.")
        except Exception as e:
            logger.error(f"Proxying (Stream) [500]: {method} {url} - Exception: {str(e)}")
            self.bridge.trigger_fast_check()
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
        logger.info(f"HTTP Reverse Proxy listening on http://0.0.0.0:{port}")
        logger.info(f"Forwarding all relative paths to: {self.target_base}")
