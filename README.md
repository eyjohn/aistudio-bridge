# aistudio-proxy

A fully functional, headless Chrome-based HTTP reverse proxy for the Google Gemini API. `aistudio-proxy` routes local API traffic through an authenticated Google AI Studio browser session, allowing you to inherit the browser's active session state, auth tokens, and network bypasses. 

## Features
- **Drop-in Reverse Proxy**: Listens on `http://localhost:8080` and transparently forwards all traffic (e.g. `/v1beta/models/...`) to the Gemini API (`https://generativelanguage.googleapis.com`) through the browser.
- **Native SSE Streaming**: Fully supports Server-Sent Events (SSE) streaming out of the box. It uses Javascript's `ReadableStream.getReader()` to chunk response data and pipes it back to Python in real-time via high-speed CDP (Chrome DevTools Protocol) bindings.
- **OOPIF Targeting**: Intelligently searches for and attaches to the specific Out-of-Process Iframe (OOPIF) running the AI Studio network layer.
- **Anti-Idle Mouse Jiggler**: Runs a continuous, asynchronous background task to dispatch mouse movements and clicks, keeping the AI Studio session alive and preventing the iframe's network activity from sleeping.
- **Visual HUD**: Optional `--visual-overlay` flag injects a real-time status UI and ghost cursor directly into the Chrome window so you can monitor proxy health and mouse jiggler activity.

## Installation

This project uses `uv` for dependency management.

```bash
uv sync
```

## Usage

Start the proxy server:

```bash
uv run aistudio-proxy <APP_ID> \
  --profile-dir /path/to/your/.chrome_profile \
  --visual-overlay \
  --chrome-binary google-chrome
```

*Wait for the terminal to display `[✓] BRIDGE INITIALIZATION COMPLETE. PROXY READY.`*

### Connecting your apps
Once the proxy is running, simply point your API clients, SDKs, or `curl` commands to `http://localhost:8080`. 

**Example (Standard Request):**
```bash
curl -X POST http://127.0.0.1:8080/v1beta/models/gemini-flash-lite-latest:generateContent?key=MY_API_KEY \
    -H 'Content-Type: application/json' \
    -d '{"contents":[{"parts":[{"text":"Explain proxy streaming in 5 words."}]}]}'
```

**Example (SSE Stream Request):**
```bash
curl -X POST http://127.0.0.1:8080/v1beta/models/gemini-3.1-pro-preview:streamGenerateContent?key=MY_API_KEY\&alt=sse \
    -H 'Content-Type: application/json' \
    -d '{"contents":[{"parts":[{"text":"Print 10 paragraphs of lorum ipsum."}]}]}'
```

## Architecture

1. **Python `aiohttp` Server**: Accepts incoming HTTP traffic on port 8080.
2. **Chrome CDP Bridge**: Python communicates with Chrome via WebSockets. It translates the incoming request into a Javascript `fetch()` call.
3. **Stream Bindings**: For streaming requests, the JS uses a `reader` loop and passes base64-encoded chunks back to Python synchronously via `Runtime.addBinding`.
4. **Proxy Response**: Python decodes the chunks and writes them directly to the `aiohttp` `StreamResponse`, maintaining exact SSE compatibility with zero buffering.

