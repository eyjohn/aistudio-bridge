from pathlib import Path


def get_asset(name: str, **kwargs) -> str:
    """Load an asset from the assets directory and optionally template it."""
    asset_path = Path(__file__).parent / "assets" / name
    content = asset_path.read_text()

    for key, value in kwargs.items():
        # Simple {{KEY}} replacement
        content = content.replace(f"{{{{{key}}}}}", str(value))

    return content
