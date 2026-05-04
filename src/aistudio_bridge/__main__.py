import argparse
import asyncio

import yaml

from .bridge import DEFAULT_HOME, DEFAULT_PORT, ChromeBridge
from .proxy import ProxyServer
from .service import manage_service


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

    if args.app_id:
        config["app_id"] = args.app_id
    if args.port:
        config["port"] = args.port
    if args.profile_dir:
        config["profile_dir"] = args.profile_dir
    if args.chrome_binary:
        config["chrome_binary"] = args.chrome_binary
    if args.target_api:
        config["target_api"] = args.target_api
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
        bridge = ChromeBridge(
            app_id, profile_dir, config["visual_overlay"], config.get("chrome_binary", "google-chrome")
        )
        await bridge.launch()
        server = ProxyServer(bridge, config.get("target_api", "https://generativelanguage.googleapis.com"))
        await server.start(port)
        while True:
            await asyncio.sleep(3600)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nAborted.")


if __name__ == "__main__":
    main()
