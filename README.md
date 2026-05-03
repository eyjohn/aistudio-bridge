# aistudio-proxy

A fully functional, headless Chrome-based HTTP reverse proxy for the Google Gemini API. `aistudio-proxy` routes local API traffic through an authenticated Google AI Studio browser session.

## Features
- **Drop-in Reverse Proxy**: Listens on `http://localhost:8080`.
- **Native SSE Streaming**: Real-time chunking via CDP bindings.
- **Systemd Integration**: Easy userspace service installation.
- **Persistent Config**: Managed via `~/.aistudio-proxy/config.yaml`.

## Installation

We recommend using `pipx` for a clean, global installation:

```bash
# Clone the repo
git clone https://github.com/youruser/aistudio-proxy
cd aistudio-proxy

# Install globally
pipx install .
```

## Usage

### 1. Obtain an App ID
1. Navigate to [AI Studio Apps](https://aistudio.google.com/apps).
2. Create a new application.
3. Copy the UUID from the browser URL.

### 2. Initial Setup
Run the proxy once with your App ID to save the configuration:

```bash
aistudio-proxy <APP_ID> --visual-overlay
```

This creates `~/.aistudio-proxy/config.yaml` and initializes the Chrome profile in `~/.aistudio-proxy/profile/`.

### 3. Background Service (Linux)
To keep the proxy running as a background service:

```bash
# Install the systemd user service
aistudio-proxy --install

# Check status
systemctl --user status aistudio-proxy

# Stop/Remove service
aistudio-proxy --uninstall
```

## Connecting your apps
Simply point your API clients to `http://localhost:8080`.

**Standard Request:**
```bash
curl -X POST http://127.0.0.1:8080/v1beta/models/gemini-flash-lite-latest:generateContent?key=MY_API_KEY \
    -H 'Content-Type: application/json' \
    -d '{"contents":[{"parts":[{"text":"Explain proxy streaming in 5 words."}]}]}'
```

**SSE Stream Request:**
```bash
curl -X POST http://127.0.0.1:8080/v1beta/models/gemini-3.1-pro-preview:streamGenerateContent?key=MY_API_KEY\&alt=sse \
    -H 'Content-Type: application/json' \
    -d '{"contents":[{"parts":[{"text":"Print 10 paragraphs of lorum ipsum."}]}]}'
```

## Configuration
Settings are stored in `~/.aistudio-proxy/config.yaml`. You can edit this file manually or update it via CLI arguments.

```yaml
app_id: your-uuid-here
visual_overlay: true
chrome_binary: google-chrome
target_api: https://generativelanguage.googleapis.com
```
