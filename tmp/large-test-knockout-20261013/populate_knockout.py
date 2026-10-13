"""Plan and record the test competition's knockout results without submission.

This uses the same Database.mutate_match path as the local admin HTTP service.
Run --prepare once before changing scores, then --apply. Re-running --apply
continues from a matching prefix of already saved games.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend import logic
from backend.storage import Database, ROOT


DIRECTORY = Path(__file__).resolve().parent
COMPETITION = "test"
EVENTS = ("test-men-single", "test-women-single", "test-men-double")
RNG = random.Random(2026101311)
COMPETITOR_KINDS = {"player", "pair", "team"}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def identity(participant: dict) -> str:
    return json.dumps(participant, ensure_ascii=False, sort_keys=True)


def label(participant: dict) -> str:
    return " / ".join(participant.get("members", [])) or participant.get("team", "")


def game(winner: int) -> list[int]:
    if RNG.random() < 0.12:
        loser_score = RNG.randint(10, 13)
        points = [loser_score + 2, loser_score]
    else:
        points = [11, RNG.randint(5, 9)]
    return points if winner == 0 else points[::-1]


def best_of_three(winner: int) -> list[list[int]]:
    if RNG.random() < 0.43:
        order = [winner, 1 - winner, winner] if RNG.random() < 0.5 else [1 - winner, winner, winner]
    else:
        order = [winner, winner]
    return [game(side) for side in order]


def build_event_plan(event: str, metadata: dict, initial: dict) -> dict:
    assert initial["drawn"] and initial["results"] is None
    assert all(not match["games"] and not match["finished"] for match in initial["matches"])
    assert initial["bronze_match"] and not initial["bronze_match"]["games"]
    assert all(slot["kind"] in COMPETITOR_KINDS for slot in initial["slots"])
    assert len(initial["slots"]) == 2 ** initial["rounds"]

    seed_by_person = {identity(entry["participant"]): entry["seed"] for entry in metadata["entrants"]}
    assert len(seed_by_person) == len(initial["slots"])
    strength = {}
    for entry in metadata["entrants"]:
        seed = entry["seed"]
        # Seeds usually prevail; evenly matched unseeded entrants vary by draw.
        strength[identity(entry["participant"])] = (
            100 + seed * 8 if seed > 0 else 72 if seed == 0 else 60
        ) + RNG.uniform(-8, 8)

    def pick_winner(match: dict, round_index: int) -> int:
        a, b = match["a"], match["b"]
        assert a["kind"] in COMPETITOR_KINDS and b["kind"] in COMPETITOR_KINDS, (event, match["id"])
        a_strength, b_strength = strength[identity(a)], strength[identity(b)]
        preferred = 0 if a_strength >= b_strength else 1
        # A few close early pairings produce plausible upsets.
        if round_index < initial["rounds"] - 2 and abs(a_strength - b_strength) < 18 and RNG.random() < 0.14:
            return 1 - preferred
        return preferred

    stage = copy.deepcopy(initial)
    entries = []
    for round_index in range(initial["rounds"]):
        matches = [m for m in stage["matches"] if m["round"] == round_index]
        for match in matches:
            side = pick_winner(match, round_index)
            scores = best_of_three(side)
            entries.append({
                "id": match["id"], "round": round_index + 1,
                "a": copy.deepcopy(match["a"]), "b": copy.deepcopy(match["b"]),
                "winner": label(match["a"] if side == 0 else match["b"]), "games": scores,
            })
            match["games"] = scores
            match["finished"] = True
        stage = logic.refresh_knockout_bracket(stage)
        if round_index == initial["rounds"] - 2:
            bronze = stage["bronze_match"]
            side = pick_winner(bronze, round_index)
            scores = best_of_three(side)
            entries.append({
                "id": "bronze", "round": "bronze",
                "a": copy.deepcopy(bronze["a"]), "b": copy.deepcopy(bronze["b"]),
                "winner": label(bronze["a"] if side == 0 else bronze["b"]), "games": scores,
            })
            bronze["games"] = scores
            bronze["finished"] = True
            stage = logic.refresh_knockout_bracket(stage)
    assert stage["results"] is not None
    return {"matches": entries, "results": stage["results"]}


def prepare() -> None:
    backup = DIRECTORY / "database-before"
    if backup.exists() or (DIRECTORY / "plan.json").exists():
        raise RuntimeError("Backup or plan already exists; refusing to replace it")
    source = ROOT / "webpage" / "database" / COMPETITION
    if not source.is_dir():
        raise RuntimeError(f"Competition not found: {source}")
    shutil.copytree(source, backup)
    plan = {"competition": COMPETITION, "events": {}}
    for event in EVENTS:
        stage_dir = backup / event / "knockout"
        metadata = read_json(stage_dir / "metadata.json")
        initial = read_json(stage_dir / "data.json")
        plan["events"][event] = build_event_plan(event, metadata, initial)
    write_json(DIRECTORY / "plan.json", plan)
    for event, value in plan["events"].items():
        print(event, len(value["matches"]), "playable matches including bronze")
    print("Plan and full competition backup saved at", DIRECTORY)


def apply() -> None:
    plan = read_json(DIRECTORY / "plan.json")
    backup = DIRECTORY / "database-before"
    if plan["competition"] != COMPETITION or set(plan["events"]) != set(EVENTS):
        raise RuntimeError("Unexpected plan")
    database = Database(ROOT)
    for event in EVENTS:
        before_dir = backup / event / "knockout"
        original_metadata = read_json(before_dir / "metadata.json")
        original_data = read_json(before_dir / "data.json")
        current_metadata = read_json(ROOT / "webpage" / "database" / COMPETITION / event / "knockout" / "metadata.json")
        if current_metadata != original_metadata:
            raise RuntimeError(f"{event}: entrant metadata changed since backup")
        saved = 0
        for entry in plan["events"][event]["matches"]:
            _, _, stage = database._stage(COMPETITION, event, "knockout")
            if not stage["drawn"] or stage["slots"] != original_data["slots"] or stage["rounds"] != original_data["rounds"]:
                raise RuntimeError(f"{event}: knockout draw changed since backup")
            match = database._find_match(stage, "knockout", entry["id"])
            if match["a"] != entry["a"] or match["b"] != entry["b"]:
                raise RuntimeError(f"{event} {entry['id']}: opponents changed")
            games = match["games"]
            expected = entry["games"]
            if games != expected[:len(games)] or len(games) > len(expected):
                raise RuntimeError(f"{event} {entry['id']}: scores differ from plan")
            if match["finished"] and games != expected:
                raise RuntimeError(f"{event} {entry['id']}: prematurely finished")
            if not match["finished"]:
                for score in expected[len(games):]:
                    database.mutate_match(COMPETITION, event, "knockout", entry["id"], "add_game", {"a": score[0], "b": score[1]})
                database.mutate_match(COMPETITION, event, "knockout", entry["id"], "edit_match", {"finished": True})
            saved += 1
        print(event, saved, "completed matches including bronze")
    print("All knockout matches saved through Database.mutate_match. No submit operation was called.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        prepare()
    else:
        apply()
