"""Read-only check of the revision-9 event-slug migration.

Run after the migration:
    python tmp/revision9-database-backup-20261013/verify_migration.py

The backup was taken before the migration. This script never writes to either
the backup or the live database.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKUP = Path(__file__).resolve().parent / "database"
CURRENT = ROOT / "webpage" / "database"
COMPETITION = "xsb26"
EVENTS = ("men-single", "men-double", "women-single", "women-double")
OLD_TO_NEW = {f"{COMPETITION}-{event}": event for event in EVENTS}


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def json_paths(directory: Path) -> set[Path]:
    return {path.relative_to(directory) for path in directory.rglob("*.json")}


def migrated_path(relative: Path) -> Path:
    parts = list(relative.parts)
    if len(parts) >= 2 and parts[0] == COMPETITION:
        parts[1] = OLD_TO_NEW.get(parts[1], parts[1])
    return Path(*parts)


def expected_json(relative: Path, data: Any) -> Any:
    if relative == Path(COMPETITION, "meta.json"):
        return {**data, "events": [OLD_TO_NEW.get(event, event) for event in data["events"]]}
    if (len(relative.parts) == 3 and relative.parts[0] == COMPETITION
            and relative.parts[1] in OLD_TO_NEW and relative.parts[2] == "meta.json"):
        return {**data, "slug": OLD_TO_NEW[relative.parts[1]]}
    return data


def remove_new_empty_round_robin_schedule(relative: Path, before: Any, after: Any) -> Any:
    """Permit only newly initialized, empty card-level RR schedule fields."""
    if relative.parts[-2:] != ("round_robin", "data.json"):
        return after
    if not isinstance(before, dict) or not isinstance(after, dict):
        return after
    normalized = dict(after)
    for key in ("scheduled_time", "scheduled_table", "scheduled_referee"):
        if key not in before and normalized.get(key) == "":
            normalized.pop(key, None)
    return normalized


def old_slug_strings(value: Any, location: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(old_slug_strings(key, f"{location}.<key>"))
            found.extend(old_slug_strings(item, f"{location}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(old_slug_strings(item, f"{location}[{index}]"))
    elif isinstance(value, str) and any(old in value for old in OLD_TO_NEW):
        found.append(location)
    return found


def scored_games(data: Any) -> tuple[int, int]:
    """Count scored matches and games, including knockout bronze matches."""
    if not isinstance(data, dict):
        return 0, 0
    matches: list[Any] = []
    if isinstance(data.get("groups"), list):
        for group in data["groups"]:
            matches.extend(group.get("matches", []))
    else:
        matches.extend(data.get("matches", []))
        if isinstance(data.get("bronze_match"), dict):
            matches.append(data["bronze_match"])
    scored = sum(bool(match.get("games")) for match in matches if isinstance(match, dict))
    games = sum(len(match.get("games", [])) for match in matches if isinstance(match, dict))
    return scored, games


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, default=CURRENT,
                        help="live database directory; defaults to the project's webpage/database")
    args = parser.parse_args()
    current = args.current.resolve()
    docs = current.parent.parent / "docs" / "webpage" / "database"
    errors: list[str] = []
    if not BACKUP.is_dir() or not current.is_dir():
        print(f"Missing backup or live database: {BACKUP} | {current}", file=sys.stderr)
        return 1

    backup_files = json_paths(BACKUP)
    current_files = json_paths(current)
    expected_files = {migrated_path(path) for path in backup_files}
    for relative in sorted(expected_files - current_files):
        errors.append(f"Missing live JSON: {relative}")
    for relative in sorted(current_files - expected_files):
        errors.append(f"Unexpected live JSON: {relative}")

    backup_competition = read_json(BACKUP / COMPETITION / "meta.json")
    expected_old_events = list(OLD_TO_NEW)
    if backup_competition.get("events") != expected_old_events:
        errors.append("Backup competition.events differs from the four expected old slugs")

    stage_files = 0
    scored_matches = 0
    games = 0
    for old_relative in sorted(backup_files):
        new_relative = migrated_path(old_relative)
        new_path = current / new_relative
        if not new_path.is_file():
            continue
        old_data = read_json(BACKUP / old_relative)
        new_data = read_json(new_path)
        comparable_new_data = remove_new_empty_round_robin_schedule(old_relative, old_data, new_data)
        if comparable_new_data != expected_json(old_relative, old_data):
            errors.append(f"JSON content changed beyond expected slug updates: {new_relative}")
        if old_relative.name in ("metadata.json", "data.json"):
            stage_files += 1
            if old_relative.name == "data.json":
                count, game_count = scored_games(old_data)
                scored_matches += count
                games += game_count
        for location in old_slug_strings(new_data):
            errors.append(f"Old event slug remains in {new_relative} at {location}")

        doc_path = docs / f"{new_relative}.md"
        if not doc_path.is_file():
            errors.append(f"Missing documentation: {doc_path}")
        elif f"webpage/database/{new_relative.as_posix()}" not in doc_path.read_text(encoding="utf-8"):
            errors.append(f"Documentation title/path is stale: {doc_path}")

    for old_slug, new_slug in OLD_TO_NEW.items():
        if (current / COMPETITION / old_slug).exists():
            errors.append(f"Old event directory remains: {COMPETITION}/{old_slug}")
        if not (current / COMPETITION / new_slug).is_dir():
            errors.append(f"New event directory missing: {COMPETITION}/{new_slug}")
        if (docs / COMPETITION / old_slug).exists():
            errors.append(f"Old documentation directory remains: {COMPETITION}/{old_slug}")

    print(json.dumps({
        "ok": not errors,
        "backup_json_files": len(backup_files),
        "live_json_files": len(current_files),
        "verified_stage_files": stage_files,
        "preserved_scored_matches": scored_matches,
        "preserved_games": games,
        "renamed_events": OLD_TO_NEW,
        "errors": errors,
    }, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
