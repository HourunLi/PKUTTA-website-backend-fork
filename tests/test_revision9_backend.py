"""Short event slugs and round-robin scheduling preserve existing scores."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import storage
from backend.storage import DataError, Database


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class RevisionNineBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = PROJECT_ROOT / "tmp" / "tests"
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = Database(self.root)
        self.db.create_competition({
            "slug": "autumn-open", "name": "秋季赛", "start_date": "2026-10-12",
            "end_date": "2026-10-13", "venue": "体育馆",
        })

    def create_event(self) -> str:
        return self.db.create_event("autumn-open", {
            "discipline": "women", "format": "double", "schedule": "round-robin",
            "csv": "甲,乙,一队,1\n丙,丁,二队,2\n戊,己,三队,0",
        })["event"]["slug"]

    def data(self, slug: str) -> dict:
        return self.db.get_event("autumn-open", slug)["stages"]["round_robin"]["data"]

    def test_new_event_slug_and_round_robin_schedule(self) -> None:
        event = self.create_event()
        self.assertEqual(event, "women-double")
        self.assertEqual(self.db.get_competition("autumn-open")["competition"]["events"], [event])
        data = self.data(event)
        self.assertEqual([data[field] for field in ("scheduled_time", "scheduled_table", "scheduled_referee")], ["", "", ""])
        self.assertEqual(len(data["matches"]), 3)

        self.db.update_round_robin_schedule("autumn-open", event, {
            "scheduled_time": " 10:30 ", "scheduled_table": " 6 ", "scheduled_referee": " 裁判甲 "
        })
        data = self.data(event)
        self.assertEqual([data[field] for field in ("scheduled_time", "scheduled_table", "scheduled_referee")], ["10:30", "6", "裁判甲"])
        self.assertTrue(all((match["time"], match["table"], match["referee"]) == ("10:30", "6", "裁判甲") for match in data["matches"]))

        match_id = data["matches"][0]["id"]
        self.db.mutate_match("autumn-open", event, "round_robin", match_id, "edit_match", {
            "time": "11:15", "table": "8", "referee": "裁判乙"
        })
        data = self.data(event)
        self.assertEqual((data["matches"][0]["time"], data["matches"][0]["table"]), ("11:15", "8"))
        self.assertEqual((data["scheduled_time"], data["scheduled_table"]), ("10:30", "6"))
        self.db.update_round_robin_schedule("autumn-open", event, {"scheduled_table": "7"})
        data = self.data(event)
        self.assertTrue(all(match["table"] == "7" for match in data["matches"]))
        self.assertEqual(data["matches"][0]["time"], "11:15")
        self.assertEqual(data["matches"][0]["referee"], "裁判乙")

        self.db = Database(self.root)
        self.assertEqual(self.data(event), data)

    def test_invalid_schedule_update_does_not_change_data(self) -> None:
        event = self.create_event()
        before = self.data(event)
        for invalid in ({}, {"time": "10:00"}, {"scheduled_time": None}, {"scheduled_table": 2}):
            with self.subTest(invalid=invalid), self.assertRaises(DataError):
                self.db.update_round_robin_schedule("autumn-open", event, invalid)
        self.assertEqual(self.data(event), before)

    def test_http_round_robin_schedule_route_uses_slugs_and_returns_updated_event(self) -> None:
        event = self.create_event()
        spec = importlib.util.spec_from_file_location(
            "backend._revision9_route_test", PROJECT_ROOT / "backend" / "server.py"
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        # server.py creates DATA at import time. Load an isolated module whose
        # ROOT points to this test's temporary database, never the live tree.
        with patch.object(storage, "ROOT", self.root):
            spec.loader.exec_module(module)
        handler = object.__new__(module.AdminHandler)
        handler.command = "PATCH"
        handler._body = lambda: {"scheduled_time": " 12:00 ", "scheduled_table": " 4 "}

        parts = ["api", "competitions", "autumn-open", "events", event, "round-robin", "schedule"]
        response = handler._route_api(parts)
        self.assertEqual(response["competition"]["slug"], "autumn-open")
        self.assertEqual(response["event"]["slug"], event)
        data = response["stages"]["round_robin"]["data"]
        self.assertEqual((data["scheduled_time"], data["scheduled_table"]), ("12:00", "4"))
        self.assertTrue(all((match["time"], match["table"]) == ("12:00", "4") for match in data["matches"]))
        self.assertEqual(self.data(event), data)

        with self.assertRaises(DataError):
            handler._route_api(parts[:4] + ["missing", "round-robin", "schedule"])

    def legacy_event(self) -> tuple[str, Path, Path]:
        short = self.create_event()
        old = f"autumn-open-{short}"
        competition = self.db.base / "autumn-open"
        source = competition / short
        target = competition / old
        source.rename(target)
        event_meta = json.loads((target / "meta.json").read_text(encoding="utf-8"))
        event_meta["slug"] = old
        write_json(target / "meta.json", event_meta)
        comp_meta = json.loads((competition / "meta.json").read_text(encoding="utf-8"))
        comp_meta["events"] = [old]
        write_json(competition / "meta.json", comp_meta)
        return short, source, target

    def test_legacy_event_migration_preserves_scores_and_is_idempotent(self) -> None:
        short, new_path, old_path = self.legacy_event()
        match_id = self.db.get_event("autumn-open", old_path.name)["stages"]["round_robin"]["data"]["matches"][0]["id"]
        self.db.mutate_match("autumn-open", old_path.name, "round_robin", match_id, "add_game", {"a": 11, "b": 8})
        self.db.mutate_match("autumn-open", old_path.name, "round_robin", match_id, "add_game", {"a": 11, "b": 9})
        self.db.mutate_match("autumn-open", old_path.name, "round_robin", match_id, "edit_match", {"finished": True})
        before_scores = self.db.get_event("autumn-open", old_path.name)["stages"]["round_robin"]["data"]["matches"][0]

        self.db = Database(self.root)
        self.assertFalse(old_path.exists())
        self.assertTrue(new_path.is_dir())
        self.assertEqual(self.db.get_competition("autumn-open")["competition"]["events"], [short])
        event = self.db.get_event("autumn-open", short)
        self.assertEqual(event["event"]["slug"], short)
        self.assertEqual(event["stages"]["round_robin"]["data"]["matches"][0], before_scores)
        stage_bytes = (new_path / "round_robin" / "data.json").read_bytes()
        self.db = Database(self.root)
        self.assertEqual((new_path / "round_robin" / "data.json").read_bytes(), stage_bytes)
        self.assertEqual(self.db.get_event("autumn-open", short)["stages"]["round_robin"]["data"]["matches"][0], before_scores)

    def test_legacy_migration_rejects_existing_target_before_mutation(self) -> None:
        short, new_path, old_path = self.legacy_event()
        shutil.copytree(old_path, new_path)
        meta_path = self.db.base / "autumn-open" / "meta.json"
        original_meta = meta_path.read_bytes()
        with self.assertRaisesRegex(DataError, "项目目录冲突"):
            Database(self.root)
        self.assertTrue(old_path.is_dir())
        self.assertTrue(new_path.is_dir())
        self.assertEqual(meta_path.read_bytes(), original_meta)

    def test_interrupted_legacy_rename_resumes(self) -> None:
        short, new_path, old_path = self.legacy_event()
        old_path.rename(new_path)
        self.db = Database(self.root)
        self.assertEqual(self.db.get_competition("autumn-open")["competition"]["events"], [short])
        self.assertEqual(self.db.get_event("autumn-open", short)["event"]["slug"], short)

    def test_migration_removes_only_empty_old_document_directories(self) -> None:
        short, _, old_path = self.legacy_event()
        docs = self.root / "docs" / "webpage" / "database" / "autumn-open"
        stale = docs / old_path.name / "round_robin" / "data.json.md"
        stale.parent.mkdir(parents=True)
        stale.write_text("旧项目文档", encoding="utf-8")
        empty_nested = docs / "orphan" / "nested"
        empty_nested.mkdir(parents=True)
        keep = docs / "notes" / "keep.txt"
        keep.parent.mkdir(parents=True)
        keep.write_text("保留", encoding="utf-8")

        self.db = Database(self.root)
        self.assertFalse((docs / old_path.name).exists())
        self.assertFalse((docs / "orphan").exists())
        self.assertEqual(keep.read_text(encoding="utf-8"), "保留")
        self.assertTrue((docs / short / "round_robin" / "data.json.md").is_file())


if __name__ == "__main__":
    unittest.main()
