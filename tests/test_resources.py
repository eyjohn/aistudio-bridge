from aistudio_bridge.resources import get_asset


def test_get_asset():
    viz = get_asset("visualizer.js")
    assert "viz-lifeline-badge" in viz

    ping = get_asset("ping.js")
    assert "generativelanguage" in ping
