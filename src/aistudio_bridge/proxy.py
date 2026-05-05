import asyncio
import base64
import json
import logging
import time
import uuid

from aiohttp import web

from .bridge import ChromeBridge

logger = logging.getLogger("aistudio-bridge.proxy")


class ProxyServer:
    def __init__(self, bridge: ChromeBridge, target_base: str):
        self.bridge = bridge
        self.target_base = target_base.rstrip("/")

    def _format_usage(self, usage: dict) -> str:
        if not usage:
            return ""
        p = usage.get("promptTokenCount", 0)
        ca = usage.get("cachedContentTokenCount", 0)
        c = usage.get("candidatesTokenCount", 0)
        th = usage.get("thoughtsTokenCount", 0)
        t = usage.get("totalTokenCount", 0)
        return f" Token Usage: Prompt={p} (Cached={ca}), Output={c} (Thoughts={th}), Total={t}"

    async def handle_request(self, request: web.Request):
        start_time = time.perf_counter()
        url = f"{self.target_base}{request.path_qs}"
        method = request.method
        is_stream = "streamGenerateContent" in request.path
        tag = "Stream" if is_stream else "Proxy"

        headers = dict(request.headers)
        if "Host" in headers:
            del headers["Host"]
        if "Accept-Encoding" in headers:
            del headers["Accept-Encoding"]

        body_bytes = await request.read()
        body_text = body_bytes.decode("utf-8", errors="ignore") if body_bytes else None

        req_id = str(uuid.uuid4())
        self.bridge.active_streams += 1
        await self.bridge._set_hud("Request Pending...", type="success")
        try:
            meta_future, chunk_queue = await self.bridge.execute_fetch_stream(url, method, headers, body_text, req_id)
            meta = await asyncio.wait_for(meta_future, timeout=70.0)  # slightly more than JS timeout

            status = meta.get("status", 200)
            if "error" in meta:
                duration = time.perf_counter() - start_time
                logger.error(f"[{tag}] [{status}] {method} {url} ({duration:.2f}s) - Error: {meta['error']}")
                self.bridge.trigger_fast_check()
                return web.Response(status=500, text=f"Proxy Fetch Error: {meta['error']}")

            logger.info(f"[{tag}] [{status}] {method} {url}")
            await self.bridge._set_hud(f"Proxying {tag}", type="success")

            resp_headers = meta.get("headers", {})
            if "content-encoding" in resp_headers:
                del resp_headers["content-encoding"]
            if "transfer-encoding" in resp_headers:
                del resp_headers["transfer-encoding"]

            response = web.StreamResponse(status=status, headers=resp_headers)
            await response.prepare(request)

            last_usage = None
            accumulated_text = ""
            try:
                while True:
                    chunk_data = await chunk_queue.get()
                    if chunk_data.get("done"):
                        break
                    if "chunk" in chunk_data:
                        chunk_bytes = base64.b64decode(chunk_data["chunk"])

                        # Token Tracking
                        try:
                            text = chunk_bytes.decode("utf-8", errors="ignore")
                            if is_stream:
                                if "usageMetadata" in text:
                                    for line in text.splitlines():
                                        if line.startswith("data:"):
                                            json_str = line[5:].strip()
                                            data = json.loads(json_str)
                                            usage = data.get("usageMetadata")
                                            if usage:
                                                last_usage = usage
                            else:
                                accumulated_text += text
                        except Exception:
                            pass

                        await response.write(chunk_bytes)

                if not is_stream and accumulated_text:
                    try:
                        data = json.loads(accumulated_text)
                        last_usage = data.get("usageMetadata")
                    except Exception:
                        pass

                duration = time.perf_counter() - start_time
                usage_str = self._format_usage(last_usage)
                logger.info(f"[{tag}] [DONE] {method} {url} ({duration:.2f}s){usage_str}")
            except (ConnectionResetError, asyncio.CancelledError):
                duration = time.perf_counter() - start_time
                logger.info(f"[{tag}] [ABORTED] {method} {url} ({duration:.2f}s)")
                raise

            return response

        except asyncio.TimeoutError:
            duration = time.perf_counter() - start_time
            logger.error(f"[{tag}] [504] {method} {url} ({duration:.2f}s) - Timeout")
            self.bridge.trigger_fast_check()
            return web.Response(status=504, text="Gateway Timeout: Fetch took too long to resolve headers.")
        except Exception as e:
            logger.error(f"[{tag}] [500] {method} {url} - Exception: {str(e)}")
            self.bridge.trigger_fast_check()
            return web.Response(status=500, text=f"Proxy Exception: {str(e)}")
        finally:
            self.bridge.active_streams -= 1
            await self.bridge._refresh_hud()
            self.bridge.streams.pop(req_id, None)

    async def start(self, port: int):
        app = web.Application()
        app.router.add_route("*", "/{path_info:.*}", self.handle_request)
        # Drop redundant timestamp [%t] from access log
        runner = web.AppRunner(app, access_log_format='%a "%r" %s %b "%{Referer}i" "%{User-Agent}i"')
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info(f"HTTP Reverse Proxy listening on http://0.0.0.0:{port}")
        logger.info(f"Forwarding all relative paths to: {self.target_base}")
