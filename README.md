# aistudio-proxy

`aistudio-proxy` is a developer tool designed to bridge the gap between the Google AI Studio web environment and your local development workflow. It enables developers to iterate locally in their preferred IDEs and tools while leveraging the [authenticated execution environment and increased usage limits](https://blog.google/innovation-and-ai/technology/developers-tools/google-one-ai-studio/) provided to AI Studio users.

By providing a local endpoint that tunnels requests through a secure browser session, `aistudio-proxy` allows you to maintain the benefits of the AI Studio ecosystem while working in a native local environment.

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
    > **Note**: The "body" or logic of the app (the prompt, model settings, etc.) is completely irrelevant. The app simply provides an authenticated execution environment for the proxy to tunnel through.
3. Copy the UUID from the browser URL.

### 2. Initial Setup (Authentication)
The first time you run the proxy, you **must** log in to your Google account in the Chrome window that appears.

```bash
aistudio-proxy <APP_ID> --visual-overlay
```

1. A Chrome window will open to your AI Studio App.
2. If prompted, log in to your Google account.
3. Wait for the terminal to show `[✓] BRIDGE INITIALIZATION COMPLETE`.
4. Your session is now saved in `~/.aistudio-proxy/profile/`. Subsequent runs (including background services) will inherit this login.

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
