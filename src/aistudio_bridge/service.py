import os
import shutil
import subprocess
import sys
from pathlib import Path


def get_service_file():
    cwd = os.getcwd()
    # Check if we are in a dev environment (pyproject.toml present)
    is_dev = (Path(cwd) / "pyproject.toml").exists()
    uv_path = shutil.which("uv") or "uv"

    # Capture current display environment to make the background service work
    display = os.environ.get("DISPLAY")
    wayland = os.environ.get("WAYLAND_DISPLAY")
    env_lines = ""
    if display:
        env_lines += f"Environment=DISPLAY={display}\n"
    if wayland:
        env_lines += f"Environment=WAYLAND_DISPLAY={wayland}\n"

    if is_dev:
        return f"""[Unit]
Description=AI Studio Streaming Bridge (Dev)
After=network.target

[Service]
Type=simple
WorkingDirectory={cwd}
Environment=PYTHONUNBUFFERED=1
ExecStart={uv_path} run aistudio-bridge
Restart=always
{env_lines}
[Install]
WantedBy=default.target
"""
    else:
        exec_path = shutil.which("aistudio-bridge") or sys.argv[0]
        return f"""[Unit]
Description=AI Studio Streaming Bridge
After=network.target

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
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
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "aistudio-bridge.service"], check=False)
        subprocess.run(["systemctl", "--user", "restart", "aistudio-bridge.service"], check=False)
        print(f"[✓] Service installed and started at {svc_file}")
        print("Use 'systemctl --user status aistudio-bridge' to check status.")
    else:
        subprocess.run(["systemctl", "--user", "stop", "aistudio-bridge.service"], check=False)
        subprocess.run(["systemctl", "--user", "disable", "aistudio-bridge.service"], check=False)
        if svc_file.exists():
            svc_file.unlink()
        print("[✓] Service stopped and removed.")
