"""End-to-end checks for the live JSON workflow and public website."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from backend import logic
from backend.storage import Database, DataError
from generator.build import build_site


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _entrant(name: str, seed: int) -> dict:
    return {"participant": {"kind": "player", "members": [name], "team": "北大"}, "seed": seed}


class LogicTests(unittest.TestCase):
    def test_match_winner_ignores_incomplete_games(self) -> None:
        match = {"finished": True, "games": [[None, None], [11, None], [11, 5]]}
        self.assertEqual(logic.match_winner_side(match), 0)
        self.assertEqual(logic.match_winner_side({"finished": True, "games": [[5, 11]]}), 1)
        for games in (
            [[None, None]],
            [[11, None]],
            [[11, 5], [5, 11], [None, 7]],
            [[11, 11]],
            [[None, -1]],
            [[True, None]],
        ):
            with self.subTest(games=games), self.assertRaises(ValueError):
                logic.match_winner_side({"finished": True, "games": games})

    def test_group_draw_prioritizes_high_seeds(self) -> None:
        people = [_entrant(str(index), index + 1) for index in range(6)]
        changed_seeds = [{**person, "seed": 100 - index} for index, person in enumerate(people)]
        groups = [{"count": 2, "size": 3, "qualifiers": 2}]
        first = logic.draw_groups(people, groups, rng_seed=23)
        changed = logic.draw_groups(changed_seeds, groups, rng_seed=23)
        self.assertEqual(first[0]["participants"][0]["members"], ["5"])
        self.assertEqual(first[1]["participants"][0]["members"], ["4"])
        self.assertEqual(changed[0]["participants"][0]["members"], ["0"])
        self.assertEqual(changed[1]["participants"][0]["members"], ["1"])

    def test_knockout_byes_and_name_tiebreak(self) -> None:
        stage = logic.draw_knockout([_entrant("甲", 1), _entrant("乙", 2), _entrant("丙", 3)], 2, rng_seed=7)
        self.assertEqual(sum(slot["kind"] == "bye" for slot in stage["slots"]), 1)
        self.assertEqual(len(stage["matches"]), 3)
        final = stage["matches"][-1]
        self.assertEqual(sum(final[side]["kind"] != "undecided" for side in ("a", "b")), 1)

        participants = [_entrant(name, index + 1)["participant"] for index, name in enumerate("甲乙丙")]
        matches = logic.round_robin_matches(participants)
        self.assertIsNone(logic.rank_round_robin(participants, matches))
        # The cycle is tied on wins, games, points, and mutual results, so
        # names settle the order.
        for match, game in zip(matches, ((11, 5), (5, 11), (11, 5))):
            match["games"] = [list(game)]
            match["finished"] = True
        self.assertEqual(logic.rank_round_robin(participants, matches), [2, 1, 0])

    def test_bronze_match_and_top_eight_wait_for_every_result(self) -> None:
        stage = logic.draw_knockout([_entrant(name, index + 1) for index, name in enumerate("甲乙丙丁戊己庚辛")], 3, rng_seed=8)
        for round_index in range(3):
            for match in stage["matches"]:
                if match["round"] == round_index:
                    match["games"] = [[11, 5]]
                    match["finished"] = True
            stage = logic.refresh_knockout_bracket(stage)
        bronze = stage["bronze_match"]
        self.assertEqual(bronze["id"], "bronze")
        self.assertEqual({bronze["a"]["kind"], bronze["b"]["kind"]}, {"player"})
        self.assertIsNone(stage["results"])
        main_scores = [match["games"] for match in stage["matches"]]

        bronze["games"] = [[5, 11]]
        bronze["finished"] = True
        stage = logic.refresh_knockout_bracket(stage)
        self.assertEqual([match["games"] for match in stage["matches"]], main_scores)
        self.assertEqual([item["rank"] for item in stage["results"]], [1, 2, 3, 4, 5, 5, 5, 5])
        fifth_names = [item["participant"]["members"][0] for item in stage["results"] if item["rank"] == 5]
        self.assertEqual(fifth_names, sorted(fifth_names))
        self.assertEqual(logic.knockout_results(stage), stage["results"])

        semifinal = next(match for match in stage["matches"] if match["round"] == 1)
        semifinal["games"] = [[5, 11]]
        changed = logic.refresh_knockout_bracket(stage)
        self.assertFalse(changed["bronze_match"]["finished"])
        self.assertEqual(changed["bronze_match"]["games"], [])
        self.assertFalse(changed["matches"][-1]["finished"])
        self.assertIsNone(changed["results"])

    def test_three_players_award_bronze_without_playable_bronze_match(self) -> None:
        stage = logic.draw_knockout([_entrant(name, index + 1) for index, name in enumerate("甲乙丙")], 2, rng_seed=7)
        for match in stage["matches"]:
            if match["round"] == 0 and all(match[side]["kind"] == "player" for side in ("a", "b")):
                match["games"] = [[11, 5]]
                match["finished"] = True
        stage = logic.refresh_knockout_bracket(stage)
        stage["matches"][-1]["games"] = [[11, 5]]
        stage["matches"][-1]["finished"] = True
        stage = logic.refresh_knockout_bracket(stage)
        self.assertIn("bye", {stage["bronze_match"][side]["kind"] for side in ("a", "b")})
        self.assertEqual([item["rank"] for item in stage["results"]], [1, 2, 3])


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = PROJECT_ROOT / "tmp" / "tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "webpage").mkdir()
        for name in ("index.html", "app.js", "style.css"):
            shutil.copyfile(PROJECT_ROOT / "webpage" / name, self.root / "webpage" / name)
        self.database = Database(self.root)
        self.competition = self.database.create_competition({
            "slug": "autumn-open", "name": "秋季赛", "start_date": "2026-10-12",
            "end_date": "2026-10-13", "venue": "北京大学体育馆",
        })["competition"]

    def test_create_draw_score_and_live_public_files(self) -> None:
        event = self.database.create_event("autumn-open", {
            "discipline": "men", "format": "single", "schedule": "group-knockout",
            "csv": "选手姓名,队伍,种子级别\n甲,北大,1\n乙,北大,2\n丙,北大,3\n丁,北大,4\n",
            "group_spec": "2*2(1)", "knockout_rounds": 1,
        })["event"]
        event_slug = event["slug"]
        self.assertFalse(self.database.get_event("autumn-open", event_slug)["stages"]["group"]["data"]["drawn"])
        detail = self.database.draw_stage("autumn-open", event_slug, "group")
        groups = detail["stages"]["group"]["data"]["groups"]
        self.assertEqual([len(group["participants"]) for group in groups], [2, 2])
        for group in groups:
            match_id = group["matches"][0]["id"]
            self.database.mutate_match("autumn-open", event_slug, "group", match_id, "add_game", {"a": 11, "b": 5})
            self.database.mutate_match("autumn-open", event_slug, "group", match_id, "edit_match", {"finished": True})
        detail = self.database.draw_stage("autumn-open", event_slug, "knockout")
        self.assertTrue(detail["stages"]["knockout"]["data"]["drawn"])
        self.assertEqual(len(detail["stages"]["knockout"]["metadata"]["entrants"]), 2)

        self.database.create_competition({
            "slug": "spring-open", "name": "春季赛", "start_date": "2026-04-12",
            "end_date": "2026-04-13", "venue": "北京大学体育馆",
        })
        build_site(self.root)
        public = self.root / "webpage" / "database"
        listing = json.loads((public / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(listing, {"competitions": ["autumn-open", "spring-open"]})
        live_data = json.loads((public / "autumn-open" / event_slug / "knockout" / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(live_data, self.database.get_event("autumn-open", event_slug)["stages"]["knockout"]["data"])
        self.assertFalse((public / "autumn-open" / event_slug / "index.json").exists())
        self.assertTrue((self.root / "docs" / "webpage" / "database" / "autumn-open" / event_slug / "knockout" / "data.json.md").exists())

        # Changing a qualifying result invalidates any drawn knockout bracket.
        first_match = groups[0]["matches"][0]["id"]
        changed = self.database.mutate_match(
            "autumn-open", event_slug, "group", first_match, "edit_game", {"a": 5, "b": 11}, game_index=0
        )
        self.assertFalse(changed["stages"]["knockout"]["data"]["drawn"])

    def test_nullable_game_lifecycle_and_round_robin_rank(self) -> None:
        event_slug = self.database.create_event("autumn-open", {
            "discipline": "women", "format": "single", "schedule": "round-robin",
            "csv": "甲,北大,1\n乙,北大,2",
        })["event"]["slug"]
        match_id = self.database.get_event("autumn-open", event_slug)["stages"]["round_robin"]["data"]["matches"][0]["id"]

        def state() -> dict:
            return self.database.get_event("autumn-open", event_slug)["stages"]["round_robin"]["data"]

        def change(action: str, payload: dict | None = None, index: int | None = None) -> dict:
            return self.database.mutate_match(
                "autumn-open", event_slug, "round_robin", match_id, action, payload, game_index=index
            )

        change("add_game", {"a": None, "b": None})
        self.assertEqual(state()["matches"][0]["games"], [[None, None]])
        with self.assertRaises(DataError):
            change("edit_match", {"finished": True})
        change("edit_game", {"a": 11, "b": None}, 0)
        with self.assertRaises(DataError):
            change("edit_match", {"finished": True})
        change("edit_game", {"a": 11, "b": 5}, 0)
        change("edit_match", {"finished": True})
        self.assertEqual(logic.rank_round_robin(state()["participants"], state()["matches"]), [0, 1])

        change("add_game", {"a": None, "b": None})
        self.assertTrue(state()["matches"][0]["finished"])
        change("edit_game", {"a": None, "b": 5}, 0)
        self.assertFalse(state()["matches"][0]["finished"])
        self.assertIsNone(logic.rank_round_robin(state()["participants"], state()["matches"]))
        change("edit_game", {"a": 5, "b": 11}, 0)
        change("edit_match", {"finished": True})
        self.assertEqual(logic.rank_round_robin(state()["participants"], state()["matches"]), [1, 0])
        change("add_game", {"a": None, "b": None})
        change("delete_game", index=0)
        self.assertEqual(state()["matches"][0]["games"], [[None, None]])
        self.assertFalse(state()["matches"][0]["finished"])
        change("delete_game", index=0)
        self.assertEqual(state()["matches"][0]["games"], [])

        for payload in ({"a": -1, "b": None}, {"a": True, "b": None}, {"a": 3, "b": 3}, {"a": 3}):
            with self.subTest(payload=payload), self.assertRaises(DataError):
                change("add_game", payload)
        self.assertEqual(state()["matches"][0]["games"], [])

    def test_saving_match_discards_incomplete_games(self) -> None:
        event_slug = self.database.create_event("autumn-open", {
            "discipline": "women", "format": "single", "schedule": "round-robin",
            "csv": "甲,北大,1\n乙,北大,2",
        })["event"]["slug"]
        match_id = self.database.get_event("autumn-open", event_slug)["stages"]["round_robin"]["data"]["matches"][0]["id"]

        def change(action: str, payload: dict | None = None) -> dict:
            return self.database.mutate_match(
                "autumn-open", event_slug, "round_robin", match_id, action, payload
            )["stages"]["round_robin"]["data"]["matches"][0]

        change("add_game", {"a": None, "b": 7})
        with self.assertRaises(DataError):
            change("edit_match", {"finished": True})
        self.assertEqual(change("edit_match", {"table": "2", "finished": False})["games"], [])

        change("add_game", {"a": 11, "b": 5})
        change("add_game", {"a": None, "b": None})
        saved = change("edit_match", {"finished": True})
        self.assertEqual(saved["games"], [[11, 5]])
        self.assertTrue(saved["finished"])

        # Repair a previously saved, inconsistent match when only metadata is submitted.
        path = self.database.base / "autumn-open" / event_slug / "round_robin" / "data.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["matches"][0]["games"] = [[None, 7]]
        data["matches"][0]["finished"] = True
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        repaired = change("edit_match", {"time": "14:30"})
        self.assertEqual(repaired["games"], [])
        self.assertFalse(repaired["finished"])

    def test_live_database_preserves_temporary_admin_games(self) -> None:
        rr_slug = self.database.create_event("autumn-open", {
            "discipline": "women", "format": "single", "schedule": "round-robin",
            "csv": "甲,北大,1\n乙,北大,2",
        })["event"]["slug"]
        rr_match = self.database.get_event("autumn-open", rr_slug)["stages"]["round_robin"]["data"]["matches"][0]["id"]
        self.database.mutate_match("autumn-open", rr_slug, "round_robin", rr_match, "add_game", {"a": None, "b": 3})

        group_slug = self.database.create_event("autumn-open", {
            "discipline": "men", "format": "single", "schedule": "group-knockout",
            "csv": "甲,北大,1\n乙,北大,2\n丙,北大,3\n丁,北大,4",
            "group_spec": "2*2(1)", "knockout_rounds": 1,
        })["event"]["slug"]
        groups = self.database.draw_stage("autumn-open", group_slug, "group")["stages"]["group"]["data"]["groups"]
        group_match = groups[0]["matches"][0]["id"]
        self.database.mutate_match("autumn-open", group_slug, "group", group_match, "add_game", {"a": 4, "b": None})

        ko_slug = self.database.create_event("autumn-open", {
            "discipline": "mixed", "format": "single", "schedule": "knockout",
            "csv": "甲,北大,1\n乙,北大,2\n丙,北大,3\n丁,北大,4",
            "knockout_rounds": 2,
        })["event"]["slug"]
        self.database.draw_stage("autumn-open", ko_slug, "knockout")
        for match_id in ("r1-m1", "r1-m2"):
            self.database.mutate_match("autumn-open", ko_slug, "knockout", match_id, "add_game", {"a": 11, "b": 5})
            self.database.mutate_match("autumn-open", ko_slug, "knockout", match_id, "edit_match", {"finished": True})
        self.database.mutate_match("autumn-open", ko_slug, "knockout", "r1-m1", "add_game", {"a": None, "b": None})
        self.database.mutate_match("autumn-open", ko_slug, "knockout", "bronze", "add_game", {"a": None, "b": 7})

        original = {}
        for event_slug, stage_name in ((rr_slug, "round_robin"), (group_slug, "group"), (ko_slug, "knockout")):
            path = self.database.base / "autumn-open" / event_slug / stage_name / "data.json"
            original[(event_slug, stage_name)] = path.read_bytes()

        build_site(self.root)
        for event_slug, stage_name in original:
            source = self.database.base / "autumn-open" / event_slug / stage_name / "data.json"
            self.assertEqual(source.read_bytes(), original[(event_slug, stage_name)])

        public = self.root / "webpage" / "database" / "autumn-open"
        rr_data = json.loads((public / rr_slug / "round_robin" / "data.json").read_text(encoding="utf-8"))
        group_data = json.loads((public / group_slug / "group" / "data.json").read_text(encoding="utf-8"))
        ko_data = json.loads((public / ko_slug / "knockout" / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(rr_data["matches"][0]["games"], [[None, 3]])
        self.assertEqual(group_data["groups"][0]["matches"][0]["games"], [[4, None]])
        self.assertEqual(ko_data["matches"][0]["games"], [[11, 5], [None, None]])
        self.assertEqual(ko_data["bronze_match"]["games"], [[None, 7]])
        for event_slug, stage_name, data in (
            (rr_slug, "round_robin", rr_data), (group_slug, "group", group_data), (ko_slug, "knockout", ko_data)
        ):
            self.assertEqual(self.database.get_event("autumn-open", event_slug)["stages"][stage_name]["data"], data)
            self.assertFalse((public / event_slug / "index.json").exists())
        self.assertFalse((self.root / "database").exists())

    def test_incomplete_knockout_score_blocks_progression(self) -> None:
        event_slug = self.database.create_event("autumn-open", {
            "discipline": "mixed", "format": "single", "schedule": "knockout",
            "csv": "甲,北大,1\n乙,北大,2\n丙,北大,3\n丁,北大,4",
            "knockout_rounds": 2,
        })["event"]["slug"]
        self.database.draw_stage("autumn-open", event_slug, "knockout")

        def change(match_id: str, action: str, payload: dict | None = None, index: int | None = None) -> dict:
            return self.database.mutate_match(
                "autumn-open", event_slug, "knockout", match_id, action, payload, game_index=index
            )["stages"]["knockout"]["data"]

        data = change("r1-m1", "add_game", {"a": None, "b": None})
        self.assertEqual(data["matches"][0]["games"], [[None, None]])
        self.assertEqual(data["matches"][-1]["a"]["kind"], "undecided")
        with self.assertRaises(DataError):
            change("r1-m1", "edit_match", {"finished": True})
        change("r1-m1", "edit_game", {"a": 11, "b": 5}, 0)
        change("r1-m1", "edit_match", {"finished": True})
        change("r1-m2", "add_game", {"a": 11, "b": 5})
        data = change("r1-m2", "edit_match", {"finished": True})
        self.assertEqual(data["matches"][-1]["a"]["kind"], "player")
        self.assertEqual(data["matches"][-1]["b"]["kind"], "player")
        data = change("r1-m1", "add_game", {"a": None, "b": 7})
        self.assertTrue(data["matches"][0]["finished"])
        data = change("r1-m1", "edit_game", {"a": None, "b": 5}, 0)
        self.assertFalse(data["matches"][0]["finished"])
        self.assertEqual(data["matches"][-1]["a"]["kind"], "undecided")
        self.assertIsNone(data["results"])

    def test_reject_invalid_csv_and_unrepresentable_bracket(self) -> None:
        with self.assertRaises(DataError):
            self.database.create_event("autumn-open", {
                "discipline": "women", "format": "single", "schedule": "knockout",
                "csv": "甲,北大,1\n乙,北大,not-a-seed", "knockout_rounds": 1,
            })
        with self.assertRaises(DataError):
            self.database.create_event("autumn-open", {
                "discipline": "women", "format": "single", "schedule": "knockout",
                "csv": "甲,北大,1\n乙,北大,2", "knockout_rounds": 2,
            })

    def test_chinese_name_survives_draw_and_mojibake_is_rejected(self) -> None:
        created = self.database.create_event("autumn-open", {
            "discipline": "men", "format": "single", "schedule": "knockout",
            "csv": "选手姓名,队伍,种子级别\n张3,北大,1\n李4,北大,2\n",
            "knockout_rounds": 1,
        })["event"]
        drawn = self.database.draw_stage("autumn-open", created["slug"], "knockout")
        slots = drawn["stages"]["knockout"]["data"]["slots"]
        self.assertEqual({slot["members"][0] for slot in slots}, {"张3", "李4"})

        with self.assertRaisesRegex(DataError, "UTF-8.*GB18030"):
            self.database.create_event("autumn-open", {
                "discipline": "women", "format": "single", "schedule": "knockout",
                "csv": "选手姓名,队伍,种子级别\n��3,北大,1\n李4,北大,2\n",
                "knockout_rounds": 1,
            })
        self.assertEqual(self.database.get_competition("autumn-open")["competition"]["events"], [created["slug"]])

    def test_qualifier_order_change_preserves_bracket_if_membership_is_same(self) -> None:
        event_slug = self.database.create_event("autumn-open", {
            "discipline": "mixed", "format": "single", "schedule": "group-knockout",
            "csv": "甲,北大,1\n乙,北大,2\n丙,北大,3\n丁,北大,4",
            "group_spec": "2*2(2)", "knockout_rounds": 2,
        })["event"]["slug"]
        groups = self.database.draw_stage("autumn-open", event_slug, "group")["stages"]["group"]["data"]["groups"]
        for group in groups:
            match_id = group["matches"][0]["id"]
            self.database.mutate_match("autumn-open", event_slug, "group", match_id, "add_game", {"a": 11, "b": 5})
            self.database.mutate_match("autumn-open", event_slug, "group", match_id, "edit_match", {"finished": True})
        drawn = self.database.draw_stage("autumn-open", event_slug, "knockout")
        slots = drawn["stages"]["knockout"]["data"]["slots"]
        old_seeds = {
            item["participant"]["members"][0]: item["seed"]
            for item in drawn["stages"]["knockout"]["metadata"]["entrants"]
        }
        changed = self.database.mutate_match(
            "autumn-open", event_slug, "group", groups[0]["matches"][0]["id"],
            "edit_game", {"a": 5, "b": 11}, game_index=0,
        )
        self.assertTrue(changed["stages"]["knockout"]["data"]["drawn"])
        self.assertEqual(changed["stages"]["knockout"]["data"]["slots"], slots)
        new_seeds = {
            item["participant"]["members"][0]: item["seed"]
            for item in changed["stages"]["knockout"]["metadata"]["entrants"]
        }
        group_names = [person["members"][0] for person in groups[0]["participants"]]
        self.assertEqual(new_seeds[group_names[0]], old_seeds[group_names[1]])
        self.assertEqual(new_seeds[group_names[1]], old_seeds[group_names[0]])

    def test_old_knockout_json_adds_bronze_without_losing_main_scores(self) -> None:
        created = self.database.create_event("autumn-open", {
            "discipline": "men", "format": "single", "schedule": "knockout",
            "csv": "甲,北大,1\n乙,北大,2\n丙,北大,3\n丁,北大,4",
            "knockout_rounds": 2,
        })["event"]
        event_slug = created["slug"]
        drawn = self.database.draw_stage("autumn-open", event_slug, "knockout")
        for match in drawn["stages"]["knockout"]["data"]["matches"]:
            if match["round"] == 0:
                self.database.mutate_match("autumn-open", event_slug, "knockout", match["id"], "add_game", {"a": 11, "b": 5})
                self.database.mutate_match("autumn-open", event_slug, "knockout", match["id"], "edit_match", {"finished": True})
        self.database.mutate_match("autumn-open", event_slug, "knockout", "r2-m1", "add_game", {"a": 11, "b": 5})
        self.database.mutate_match("autumn-open", event_slug, "knockout", "r2-m1", "edit_match", {"finished": True})
        data_path = self.database.base / "autumn-open" / event_slug / "knockout" / "data.json"
        old_data = json.loads(data_path.read_text(encoding="utf-8"))
        old_data.pop("bronze_match")
        old_data.pop("results")
        data_path.write_text(json.dumps(old_data, ensure_ascii=False), encoding="utf-8")

        upgraded = self.database.get_event("autumn-open", event_slug)["stages"]["knockout"]["data"]
        self.assertEqual(upgraded["matches"], old_data["matches"])
        self.assertEqual(upgraded["bronze_match"]["id"], "bronze")
        self.assertIsNone(upgraded["results"])
        self.assertEqual(json.loads(data_path.read_text(encoding="utf-8")), old_data)

        build_site(self.root)
        public_data = json.loads((self.root / "webpage" / "database" / "autumn-open" / event_slug / "knockout" / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(public_data["bronze_match"], upgraded["bronze_match"])
        self.assertEqual(public_data, upgraded)
        self.assertFalse((self.database.base / "autumn-open" / event_slug / "index.json").exists())

        self.database.mutate_match("autumn-open", event_slug, "knockout", "bronze", "add_game", {"a": 11, "b": 5})
        finished = self.database.mutate_match("autumn-open", event_slug, "knockout", "bronze", "edit_match", {"finished": True})
        updated = finished["stages"]["knockout"]["data"]
        self.assertEqual(updated["matches"], old_data["matches"])
        self.assertEqual([item["rank"] for item in updated["results"]], [1, 2, 3, 4])
        self.assertTrue(json.loads(data_path.read_text(encoding="utf-8"))["bronze_match"]["finished"])


if __name__ == "__main__":
    unittest.main()
