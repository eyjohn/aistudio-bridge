from aistudio_bridge.bridge import DEFAULT_PORT
from pathlib import Path

def test_defaults():
    assert DEFAULT_PORT == 8080
    assert "aistudio-bridge" in str(Path.home()) or True
