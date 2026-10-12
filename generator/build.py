"""Compatibility entry point for checking an already uploadable public site.

The site now reads the single live JSON database in ``webpage/database``.
Administration saves directly there; publishing never requires this command.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from backend.storage import ROOT, Database


def build_site(root: Path | None = None) -> Path:
    """Initialize legacy data if necessary and check canonical website files.

    Retained for older callers. No database copy, aggregate snapshot, or asset
    build is produced. Existing scores, temporary games, and assets stay live.
    """
    output = Database(root or ROOT).base.parent
    for name in ("index.html", "app.js", "style.css"):
        if not (output / name).is_file():
            raise FileNotFoundError(f"Website file is missing: {output / name}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="Project root")
    args = parser.parse_args()
    print(build_site(args.root))


if __name__ == "__main__":
    main()
