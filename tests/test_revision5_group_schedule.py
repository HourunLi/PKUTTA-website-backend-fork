"""Group plans propagate to matches, while match edits remain independent."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import logic
from backend.storage import DataError, Database


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class GroupScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = PROJECT_ROOT / "tmp" / "tests"
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.database = Database(Path(temporary.name))
        self.database.create_competition({
            "slug": "group-plan", "name": "小组计划测试", "start_date": "2026-10-12",
            "end_date": "2026-10-13", "venue": "体育馆",
        })
        self.event = self.database.create_event("group-plan", {
            "discipline": "men", "format": "single", "schedule": "group-knockout",
            "csv": "甲,北大,6\n乙,北大,5\n丙,北大,4\n丁,北大,3\n戊,北大,2\n己,北大,1",
            "group_spec": "2*3(2)", "knockout_rounds": 2,
        })["event"]["slug"]

    def groups(self) -> list[dict]:
        return self.database.get_event("group-plan", self.event)["stages"]["group"]["data"]["groups"]

    def plan(self, group_id: int, **values: str) -> dict:
        return self.database.update_group("group-plan", self.event, group_id, values)

    def draw(self) -> dict:
        return self.database.draw_stage("group-plan", self.event, "group")

    def test_group_defaults_and_plan_before_draw(self) -> None:
        for group in self.groups():
            self.assertEqual((group["scheduled_time"], group["scheduled_table"]), ("", ""))
            self.assertTrue(all(match["games"] == [] for match in group["matches"]))
        self.plan(1, scheduled_time=" 14:30 ", scheduled_table=" 3 ")
        self.draw()
        first, second = self.groups()
        self.assertEqual((first["scheduled_time"], first["scheduled_table"]), ("14:30", "3"))
        self.assertTrue(all((match["time"], match["table"]) == ("14:30", "3") for match in first["matches"]))
        self.assertEqual((second["scheduled_time"], second["scheduled_table"]), ("", ""))

    def test_group_update_overwrites_all_matches_without_reverse_propagation(self) -> None:
        self.draw()
        matches = self.groups()[0]["matches"]
        for match in matches:
            self.database.mutate_match(
                "group-plan", self.event, "group", match["id"], "edit_match", {"time": "旧时间", "table": "旧桌号"}
            )
        self.plan(1, scheduled_time="15:00", scheduled_table="8")
        for match in self.groups()[0]["matches"]:
            self.assertEqual((match["time"], match["table"]), ("15:00", "8"))

        first_id = matches[0]["id"]
        self.database.mutate_match(
            "group-plan", self.event, "group", first_id, "edit_match", {"time": "15:20", "table": "9"}
        )
        first = self.groups()[0]
        self.assertEqual((first["scheduled_time"], first["scheduled_table"]), ("15:00", "8"))
        self.assertEqual((first["matches"][0]["time"], first["matches"][0]["table"]), ("15:20", "9"))
        self.assertTrue(all((match["time"], match["table"]) == ("15:00", "8") for match in first["matches"][1:]))

        # A partial group patch overwrites only that field on every match.
        self.plan(1, scheduled_table="")
        first = self.groups()[0]
        self.assertEqual(first["scheduled_table"], "")
        self.assertTrue(all(match["table"] == "" for match in first["matches"]))
        self.assertEqual(first["matches"][0]["time"], "15:20")

    def test_redraw_preserves_group_plan_and_applies_it_to_new_matches(self) -> None:
        self.draw()
        self.plan(1, scheduled_time="明天 09:30", scheduled_table="A1")
        self.plan(2, scheduled_time="明天 10:30", scheduled_table="A2")
        first_id = self.groups()[0]["matches"][0]["id"]
        self.database.mutate_match(
            "group-plan", self.event, "group", first_id, "edit_match", {"time": "实际 09:40", "table": "B3"}
        )
        self.draw()
        for group in self.groups():
            expected = (f"明天 {('09:30' if group['id'] == 1 else '10:30')}", f"A{group['id']}")
            self.assertEqual((group["scheduled_time"], group["scheduled_table"]), expected)
            self.assertTrue(all((match["time"], match["table"]) == expected for match in group["matches"]))

    def test_reject_invalid_group_or_schedule_without_modifying_data(self) -> None:
        before = self.groups()
        for group_id in (0, 3, True, "1"):
            with self.subTest(group_id=group_id), self.assertRaises(DataError):
                self.database.update_group("group-plan", self.event, group_id, {"scheduled_time": "10:00"})
        for payload in ({}, {"time": "10:00"}, {"scheduled_time": None}, {"scheduled_table": 2}):
            with self.subTest(payload=payload), self.assertRaises(DataError):
                self.database.update_group("group-plan", self.event, 1, payload)
        self.assertEqual(self.groups(), before)

    def test_draw_logic_has_independent_empty_schedule_defaults(self) -> None:
        entrants = [{"participant": {"kind": "player", "members": [str(index)], "team": ""}, "seed": 0}
                    for index in range(4)]
        groups = logic.draw_groups(entrants, [{"count": 2, "size": 2, "qualifiers": 1}], rng_seed=1)
        self.assertEqual([(group["scheduled_time"], group["scheduled_table"]) for group in groups], [("", ""), ("", "")])

    def test_http_group_route_calls_database_with_integer_id(self) -> None:
        from backend import server

        handler = object.__new__(server.AdminHandler)
        handler.command = "PATCH"
        handler._body = lambda: {"scheduled_time": "11:00", "scheduled_table": "6"}
        with patch.object(server, "DATA", self.database):
            result = handler._route_api(["api", "competitions", "group-plan", "events", self.event, "groups", "1"])
        first = result["stages"]["group"]["data"]["groups"][0]
        self.assertEqual((first["scheduled_time"], first["scheduled_table"]), ("11:00", "6"))
        with patch.object(server, "DATA", self.database), self.assertRaises(DataError):
            handler._route_api(["api", "competitions", "group-plan", "events", self.event, "groups", "0"])


if __name__ == "__main__":
    unittest.main()
