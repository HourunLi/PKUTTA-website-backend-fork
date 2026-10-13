"""Persist standalone round-robin standings using the group ranking rules."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from backend import logic
from backend.storage import Database


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPARISON_FIELDS = ("tied_points", "game_ratio", "point_ratio", "draw_rank")


class RevisionEightStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = PROJECT_ROOT / "tmp" / "tests"
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = Database(self.root)
        self.db.create_competition({
            "slug": "revision-eight", "name": "修订八", "start_date": "2026-10-13",
            "end_date": "2026-10-14", "venue": "测试体育馆",
        })

    def create_round_robin(self, names: str = "ABC", discipline: str = "men") -> str:
        return self.db.create_event("revision-eight", {
            "discipline": discipline, "format": "single", "schedule": "round-robin",
            "csv": "\n".join(f"{name},学院{index},{index}" for index, name in enumerate(names)),
        })["event"]["slug"]

    def data(self, event: str, database: Database | None = None) -> dict:
        return (database or self.db).get_event("revision-eight", event)["stages"]["round_robin"]["data"]

    def data_path(self, event: str) -> Path:
        return self.root / "webpage" / "database" / "revision-eight" / event / "round_robin" / "data.json"

    def change(self, event: str, match_id: str, action: str, payload: dict, index: int | None = None) -> dict:
        return self.db.mutate_match(
            "revision-eight", event, "round_robin", match_id, action, payload, index
        )["stages"]["round_robin"]["data"]

    def finish(self, event: str, scores: dict[tuple[int, int], list]) -> dict:
        for match in self.data(event)["matches"]:
            for a, b in scores[(match["a"], match["b"])]:
                self.change(event, match["id"], "add_game", {"a": a, "b": b})
            self.change(event, match["id"], "edit_match", {"finished": True})
        return self.data(event)

    def test_creation_has_no_standings_before_all_matches_finish(self) -> None:
        event = self.create_round_robin("ABCD")
        before = self.data(event)
        self.assertIsNone(before["standings"])
        self.assertIsNone(before["ranking_draw_order"])
        self.assertEqual(before["count"], 4)
        self.assertEqual(len(before["matches"]), 6)
        self.assertTrue(all(match["referee"] == "" for match in before["matches"]))
        first = before["matches"][0]
        self.change(event, first["id"], "add_game", {"a": 11, "b": 5})
        after = self.change(event, first["id"], "edit_match", {"finished": True})
        self.assertIsNone(after["standings"])
        self.assertEqual(after["participants"], before["participants"])
        self.assertEqual([match["id"] for match in after["matches"]], [match["id"] for match in before["matches"]])

    def test_uses_group_ratios_instead_of_legacy_total_games_and_points(self) -> None:
        event = self.create_round_robin()
        data = self.finish(event, {
            (0, 1): [[11, 5]] * 3 + [[5, 11]] * 2,
            (0, 2): [[0, 11]] * 3,
            (1, 2): [[11, 0]] * 3,
        })
        self.assertEqual(data["standings"]["order"], [1, 2, 0])
        self.assertEqual(logic.rank_round_robin(data["participants"], data["matches"]), [1, 0, 2])
        self.assertEqual(data["standings"], logic.group_standings(
            data["participants"], data["matches"], draw_order=data["ranking_draw_order"]
        ))
        self.assertTrue(all(row["point_ratio"] is None for row in data["standings"]["rows"]))

    def test_random_tie_order_survives_metadata_restart_and_unfinish(self) -> None:
        event = self.create_round_robin()
        before = self.finish(event, {(0, 1): [[11, 5]], (0, 2): [[5, 11]], (1, 2): [[11, 5]]})
        self.assertTrue(all(row["draw_rank"] is not None for row in before["standings"]["rows"]))
        self.assertEqual(before["standings"]["order"], before["ranking_draw_order"])
        match = before["matches"][0]
        after = self.change(event, match["id"], "edit_match", {"referee": "张老师", "time": "14:00", "table": "3"})
        self.assertEqual(after["standings"], before["standings"])
        self.assertEqual(after["ranking_draw_order"], before["ranking_draw_order"])
        reopened = self.data(event, Database(self.root))
        self.assertEqual(reopened, after)
        pending = self.change(event, match["id"], "edit_match", {"finished": False})
        self.assertIsNone(pending["standings"])
        self.assertEqual(pending["ranking_draw_order"], before["ranking_draw_order"])
        finished = self.change(event, match["id"], "edit_match", {"finished": True})
        self.assertEqual(finished["standings"], before["standings"])

    def test_every_score_action_refreshes_standings_and_keeps_draw_order(self) -> None:
        event = self.create_round_robin()
        before = self.finish(event, {(0, 1): [[11, 5]], (0, 2): [[5, 11]], (1, 2): [[11, 5]]})
        match = next(match for match in before["matches"] if (match["a"], match["b"]) == (0, 2))
        edited = self.change(event, match["id"], "edit_game", {"a": 1, "b": 11}, 0)
        self.assertEqual(edited["standings"]["order"], [2, 1, 0])
        self.assertEqual(edited["ranking_draw_order"], before["ranking_draw_order"])
        added = self.change(event, match["id"], "add_game", {"a": 1, "b": 11})
        self.assertEqual(added["standings"], logic.group_standings(
            added["participants"], added["matches"], draw_order=before["ranking_draw_order"]
        ))
        removed = self.change(event, match["id"], "delete_game", {}, 1)
        self.assertEqual(removed["standings"], edited["standings"])
        incomplete = self.change(event, match["id"], "delete_game", {}, 0)
        self.assertIsNone(incomplete["standings"])
        self.assertFalse(next(item for item in incomplete["matches"] if item["id"] == match["id"])["finished"])
        self.assertEqual(incomplete["ranking_draw_order"], before["ranking_draw_order"])

    def test_walkovers_use_zero_and_leave_unused_trace_cells_empty(self) -> None:
        event = self.create_round_robin("ABCD")
        data = self.finish(event, {
            (0, 1): [[None, " WO "]], (0, 2): [[0, 11]], (0, 3): [[11, 5]],
            (1, 2): [[11, "wo"]], (1, 3): [[11, 5]], (2, 3): [[11, 5]],
        })
        self.assertEqual(data["standings"]["order"], [1, 2, 0, 3])
        rows = data["standings"]["rows"]
        self.assertEqual([row["points"] for row in rows], [2, 2, 2, 0])
        self.assertEqual(rows[0]["game_ratio"], {"won": 1, "lost": 0})
        self.assertTrue(all(rows[-1][field] is None for field in COMPARISON_FIELDS))
        second_event = self.create_round_robin("XY", "women")
        both = self.finish(second_event, {(0, 1): [["wo", "wo"]]})
        self.assertEqual([row["points"] for row in both["standings"]["rows"]], [0, 0])
        self.assertEqual([row["game_ratio"] for row in both["standings"]["rows"]], [{"won": 0, "lost": 0}] * 2)
        self.assertEqual(both["standings"]["order"], both["ranking_draw_order"])

    def test_restart_upgrades_legacy_data_without_reordering_or_losing_scores(self) -> None:
        event = self.create_round_robin()
        data = self.finish(event, {(0, 1): [[11, 5]], (0, 2): [[11, 5]], (1, 2): [[11, 5]]})
        first = data["matches"][0]
        first.update(referee="李老师", time="9:00", table="2")
        data.pop("standings")
        data.pop("ranking_draw_order")
        data["matches"][1].pop("referee")
        preserved = copy.deepcopy(data)
        self.data_path(event).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        migrated = self.data(event, Database(self.root))
        self.assertEqual(migrated["participants"], preserved["participants"])
        expected_matches = copy.deepcopy(preserved["matches"])
        expected_matches[1]["referee"] = ""
        self.assertEqual(migrated["matches"], expected_matches)
        self.assertEqual(migrated["standings"]["order"], [0, 1, 2])
        self.assertEqual(json.loads(self.data_path(event).read_text(encoding="utf-8")), migrated)
        self.assertEqual(self.data(event, Database(self.root)), migrated)

    def test_missing_duplicate_and_invalid_pairings_cannot_finish_the_stage(self) -> None:
        event = self.create_round_robin()
        complete = self.finish(event, {(0, 1): [[11, 5]], (0, 2): [[11, 5]], (1, 2): [[11, 5]]})
        variants = []
        missing = copy.deepcopy(complete)
        missing["matches"].pop()
        variants.append(missing)
        duplicate = copy.deepcopy(complete)
        duplicate["matches"][2].update(a=0, b=1)
        variants.append(duplicate)
        for a, b in ((0, 0), (False, 1), (0, 3), ("0", 1)):
            invalid = copy.deepcopy(complete)
            invalid["matches"][0].update(a=a, b=b)
            variants.append(invalid)
        for variant in variants:
            with self.subTest(matches=variant["matches"]):
                original = copy.deepcopy(variant)
                Database._refresh_round_robin_standings(variant)
                self.assertIsNone(variant["standings"])
                self.assertEqual(variant["matches"], original["matches"])
                self.assertEqual(variant["ranking_draw_order"], complete["ranking_draw_order"])

    def test_invalid_old_draw_order_is_repaired_without_changing_matches(self) -> None:
        event = self.create_round_robin()
        data = self.finish(event, {(0, 1): [[11, 5]], (0, 2): [[5, 11]], (1, 2): [[11, 5]]})
        data["ranking_draw_order"] = [0, 0, 2]
        before = copy.deepcopy(data["matches"])
        self.data_path(event).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        migrated = self.data(event, Database(self.root))
        self.assertEqual(sorted(migrated["ranking_draw_order"]), [0, 1, 2])
        self.assertEqual(migrated["matches"], before)
        self.assertEqual(migrated["standings"]["order"], migrated["ranking_draw_order"])

    def test_single_entrant_is_a_complete_zero_match_round_robin(self) -> None:
        event = self.create_round_robin("A")
        data = self.data(event)
        self.assertEqual(data["matches"], [])
        self.assertEqual(data["standings"]["order"], [0])
        self.assertEqual(data["standings"]["rows"][0]["rank"], 1)


if __name__ == "__main__":
    unittest.main()
