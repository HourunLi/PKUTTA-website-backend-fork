"""Canonical deployment data, safe legacy migration, and incremental writes."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from backend import logic
from backend.storage import DataError, Database
from generator.build import build_site


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _files(tree: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(tree).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tree.rglob("*") if path.is_file()
    }


class LiveDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = PROJECT_ROOT / "tmp" / "tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        webpage = self.root / "webpage"
        webpage.mkdir()
        for filename in ("index.html", "app.js", "style.css"):
            shutil.copyfile(PROJECT_ROOT / "webpage" / filename, webpage / filename)

    def _database(self) -> Database:
        database = Database(self.root)
        database.create_competition({
            "slug": "autumn-open", "name": "秋季赛", "start_date": "2026-10-12",
            "end_date": "2026-10-12", "venue": "体育馆",
        })
        return database

    def _round_robin(self, database: Database) -> str:
        return database.create_event("autumn-open", {
            "discipline": "men", "format": "single", "schedule": "round-robin",
            "csv": "甲,北大,0\n乙,北大,0\n丙,北大,0",
        })["event"]["slug"]

    def test_score_edit_changes_only_target_stage_file(self) -> None:
        database = self._database()
        event = self._round_robin(database)
        build_site(self.root)
        webpage = self.root / "webpage"
        before = _files(webpage)
        match_id = database.get_event("autumn-open", event)["stages"]["round_robin"]["data"]["matches"][0]["id"]
        database.mutate_match("autumn-open", event, "round_robin", match_id, "add_game", {"a": 11, "b": 5})
        after = _files(webpage)
        self.assertEqual(before.keys(), after.keys())
        changed = {path for path in before if before[path] != after[path]}
        self.assertEqual(changed, {f"database/autumn-open/{event}/round_robin/data.json"})
        stored = json.loads((webpage / "database" / "autumn-open" / event / "round_robin" / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["matches"][0]["games"], [[11, 5]])
        self.assertFalse((self.root / "database").exists())

    def test_competition_rename_changes_only_its_metadata(self) -> None:
        database = self._database()
        self._round_robin(database)
        build_site(self.root)
        webpage = self.root / "webpage"
        before = _files(webpage)
        database.update_competition("autumn-open", {"name": "新赛事名"})
        after = _files(webpage)
        self.assertEqual(before.keys(), after.keys())
        self.assertEqual({path for path in before if before[path] != after[path]}, {"database/autumn-open/meta.json"})
        self.assertEqual(database.list_competitions()["competitions"][0]["name"], "新赛事名")

    def test_legacy_migration_preserves_scores_and_discards_aggregate_copies(self) -> None:
        legacy = self.root / "database"
        event = "autumn-open-men-single"
        participant_a = {"kind": "player", "members": ["甲"], "team": "北大"}
        participant_b = {"kind": "player", "members": ["乙"], "team": "北大"}
        entrants = [{"participant": person, "seed": 0} for person in (participant_a, participant_b)]
        competition = {"slug": "autumn-open", "name": "原始赛事", "start_date": "2026-10-12", "end_date": "2026-10-12", "venue": "体育馆", "events": [event]}
        event_meta = {"slug": event, "discipline": "men", "format": "single", "name": "男子单打", "schedule": "round-robin", "stages": ["round_robin"]}
        data = {"count": 2, "participants": [participant_a, participant_b], "matches": logic.round_robin_matches([participant_a, participant_b])}
        data["matches"][0]["games"] = [[11, 5], [None, None], [None, 7]]
        _write_json(legacy / "index.json", {"competitions": ["autumn-open"]})
        _write_json(legacy / "autumn-open" / "meta.json", competition)
        _write_json(legacy / "autumn-open" / event / "meta.json", event_meta)
        _write_json(legacy / "autumn-open" / event / "round_robin" / "metadata.json", {"type": "round_robin", "entrants": entrants})
        _write_json(legacy / "autumn-open" / event / "round_robin" / "data.json", data)
        public = self.root / "webpage" / "database"
        shutil.copytree(legacy, public)
        _write_json(public / "index.json", {"competitions": [{**competition, "name": "过期公示"}]})
        _write_json(public / "autumn-open" / "index.json", {"competition": competition, "events": [event_meta]})
        _write_json(public / "autumn-open" / event / "index.json", {"competition": competition, "event": event_meta, "stages": {}})
        old_document = self.root / "docs" / "webpage" / "database" / "autumn-open" / event / "index.json.md"
        old_document.parent.mkdir(parents=True)
        old_document.write_text("obsolete aggregate index", encoding="utf-8")
        stale = json.loads(json.dumps(data))
        stale["matches"][0]["games"] = [[11, 5]]
        _write_json(public / "autumn-open" / event / "round_robin" / "data.json", stale)

        database = Database(self.root)
        self.assertEqual(database.base, public)
        self.assertFalse(legacy.exists())
        self.assertEqual(json.loads((public / "index.json").read_text(encoding="utf-8")), {"competitions": ["autumn-open"]})
        self.assertEqual(database.get_event("autumn-open", event)["stages"]["round_robin"]["data"], data)
        self.assertEqual([path.relative_to(public).as_posix() for path in public.rglob("index.json")], ["index.json"])
        self.assertFalse(old_document.exists())
        self.assertTrue((old_document.parent / "round_robin" / "data.json.md").exists())

    def test_existing_deployment_is_self_contained_and_reconcile_keeps_scores(self) -> None:
        database = self._database()
        event = self._round_robin(database)
        build_site(self.root)
        match_id = database.get_event("autumn-open", event)["stages"]["round_robin"]["data"]["matches"][0]["id"]
        database.mutate_match("autumn-open", event, "round_robin", match_id, "add_game", {"a": 11, "b": 5})
        database.mutate_match("autumn-open", event, "round_robin", match_id, "add_game", {"a": None, "b": 7})
        webpage = self.root / "webpage"
        before = _files(webpage)
        # A deployed site contains only its control assets and canonical JSON.
        self.assertFalse((self.root / "frontend").exists())
        self.assertEqual(build_site(self.root), webpage)
        self.assertEqual(_files(webpage), before)
        for filename in ("index.html", "app.js", "style.css"):
            self.assertTrue((webpage / filename).is_file())
        html = (webpage / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("__MODE__", html)
        self.assertNotIn("__ASSET_PREFIX__", html)
        self.assertEqual([path.relative_to(webpage / "database").as_posix() for path in (webpage / "database").rglob("index.json")], ["index.json"])

    def test_invalid_slugs_cannot_reach_outside_canonical_database(self) -> None:
        database = self._database()
        event = self._round_robin(database)
        sentinel = self.root / "outside.json"
        sentinel.write_text("keep", encoding="utf-8")
        for invalid in ("../outside", "../../outside", "autumn-open/../outside", r"..\outside"):
            with self.subTest(slug=invalid), self.assertRaises(DataError):
                database.delete_competition(invalid)
            with self.subTest(event=invalid), self.assertRaises(DataError):
                database.get_event("autumn-open", invalid)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertEqual(database.get_event("autumn-open", event)["event"]["slug"], event)


if __name__ == "__main__":
    unittest.main()
