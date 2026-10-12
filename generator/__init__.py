"""Static public-site generation for PKUTTA competitions."""

from pathlib import Path


def build_site(root: Path | None = None) -> Path:
    """Check the live site without importing the CLI module during import."""
    from .build import build_site as build

    return build(root)

__all__ = ["build_site"]
