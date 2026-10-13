"""Plan and apply the authorized 333-match test through localhost admin APIs."""
from __future__ import annotations

import argparse
import copy
import json
import random
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

DIRECTORY = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:4000"
RNG = random.Random(2026101308)


def read(name):
    return json.loads((DIRECTORY / name).read_text(encoding="utf-8-sig"))


def save(name, value):
    (DIRECTORY / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def api(path, method="GET", payload=None):
    if method != "GET":
        assert re.fullmatch(r"/api/competitions/test/events/test-(men-single|women-single|men-double|women-double)/stages/(group|round_robin)/matches/[a-z0-9-]+(/games)?", path)
        assert (method == "POST" and path.endswith("/games")) or (method == "PATCH" and not path.endswith("/games"))
    encoded = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(BASE + path, data=encoded, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {path} returned HTTP {error.code}: {body}") from error


def win_game(winner):
    if RNG.random() < 0.18:
        loser_score = RNG.randrange(10, 16)
        score = [loser_score + 2, loser_score]
    else:
        score = [11, RNG.randrange(3, 10)]
    return score if winner == 0 else score[::-1]


def normal_games(winner):
    if RNG.random() < 0.48:
        order = [winner, 1 - winner] if RNG.random() < 0.5 else [1 - winner, winner]
        return [win_game(side) for side in [*order, winner]]
    return [win_game(winner), win_game(winner)]


def template(kind):
    repeated = lambda a, b: [[a, b], [a, b]]
    if kind == "head_to_head":
        return {(0, 1): repeated(11, 7), (0, 2): repeated(11, 7), (0, 3): repeated(7, 11),
                (1, 2): repeated(11, 7), (1, 3): repeated(11, 7), (2, 3): repeated(11, 7)}
    if kind == "game_ratio":
        return {(0, 1): repeated(11, 5), (0, 2): [[5, 11], [11, 5], [5, 11]],
                (1, 2): [[11, 5], [5, 11], [11, 5]],
                (0, 3): repeated(11, 5), (1, 3): repeated(11, 5), (2, 3): repeated(11, 5)}
    if kind == "point_ratio":
        return {(0, 1): repeated(11, 4), (0, 2): repeated(9, 11), (1, 2): repeated(11, 7),
                (0, 3): repeated(11, 5), (1, 3): repeated(11, 5), (2, 3): repeated(11, 5)}
    if kind == "lottery":
        return {(0, 1): repeated(11, 7), (0, 2): repeated(7, 11), (1, 2): repeated(11, 7)}
    if kind == "original_tie_pool":
        return {(0, 1): [[11, 5], [5, 11], [11, 5]], (0, 2): repeated(11, 0),
                (0, 3): repeated(5, 11), (0, 4): repeated(0, 11), (1, 2): repeated(11, 5),
                (1, 3): repeated(11, 5), (1, 4): repeated(5, 11), (2, 3): repeated(11, 5),
                (2, 4): repeated(11, 5), (3, 4): repeated(11, 5)}
    raise ValueError(kind)


SPECIAL = {
    ("test-men-single", 1): ("head_to_head", [0, 1, 2, 3]),
    ("test-men-single", 2): ("game_ratio", [0, 2, 1, 3]),
    ("test-men-single", 21): ("original_tie_pool", [1, 4, 3, 2, 0]),
    ("test-women-single", 1): ("point_ratio", [0, 2, 1, 3]),
    ("test-men-double", 7): ("lottery", None),
    ("test-women-double", None): ("point_ratio", [0, 2, 1, 3]),
}


def validate_match(games):
    assert len(games) in (2, 3)
    wins = [0, 0]
    for index, (a, b) in enumerate(games):
        assert isinstance(a, int) and isinstance(b, int)
        high, low = max(a, b), min(a, b)
        assert (high == 11 and 0 <= low <= 9) or (high >= 12 and low == high - 2)
        assert max(wins) < 2
        wins[int(b > a)] += 1
        if index < len(games) - 1:
            assert max(wins) < 2
    assert sorted(wins) in ([0, 2], [1, 2])


def make_plan():
    snapshot = read("before.json")
    assert len(snapshot["competitions"]) == 1
    competition = snapshot["competitions"][0]
    assert competition["competition"]["slug"] == "test"
    plan = {"competition": "test", "events": [], "total_matches": 0}
    for detail in competition["event_details"]:
        slug = detail["event"]["slug"]
        stage = "round_robin" if slug == "test-women-double" else "group"
        data = detail["stages"][stage]["data"]
        groups = data["groups"] if stage == "group" else [{**data, "id": None}]
        event_plan = {"slug": slug, "name": detail["event"]["name"], "stage": stage, "groups": []}
        if stage == "group":
            assert data["drawn"] is True
            assert detail["stages"]["knockout"]["data"]["drawn"] is False
        for group in groups:
            count = len(group["participants"])
            case, expected = SPECIAL.get((slug, group["id"]), ("overall_wins", None))
            if case == "overall_wins":
                expected = RNG.sample(range(count), count)
                ranks = {person: rank for rank, person in enumerate(expected)}
                scores = None
            else:
                scores = template(case)
                assert len(scores) == len(group["matches"])
            group_plan = {"id": group["id"], "case": case, "participants": group["participants"],
                          "expected_order": expected, "matches": []}
            for match in group["matches"]:
                assert not match["games"] and not match["finished"], "Pre-existing scores must not be overwritten"
                a, b = match["a"], match["b"]
                if scores is None:
                    games = normal_games(0 if ranks[a] < ranks[b] else 1)
                else:
                    games = copy.deepcopy(scores[min(a, b), max(a, b)])
                    if a > b:
                        games = [game[::-1] for game in games]
                validate_match(games)
                group_plan["matches"].append({"id": match["id"], "a": a, "b": b, "games": games})
            plan["total_matches"] += len(group_plan["matches"])
            event_plan["groups"].append(group_plan)
        plan["events"].append(event_plan)
    assert plan["total_matches"] == 333
    save("plan.json", plan)
    for event in plan["events"]:
        count = sum(len(group["matches"]) for group in event["groups"])
        print(f"{event['slug']}: {count} matches, {len(event['groups'])} groups/stages")
        for group in event["groups"]:
            if group["case"] != "overall_wins":
                print(f"  group {group['id']}: {group['case']}, expected indices {group['expected_order']}")


def assert_identity(original, actual):
    assert original["competition"] == actual["competition"]
    assert original["event"] == actual["event"]
    for name, original_stage in original["stages"].items():
        actual_stage = actual["stages"][name]
        if name == "knockout":
            assert original_stage["data"] == actual_stage["data"], "Knockout bracket changed unexpectedly"
            continue
        assert original_stage["metadata"] == actual_stage["metadata"]
        old_data, new_data = original_stage["data"], actual_stage["data"]
        old_groups = old_data["groups"] if name == "group" else [old_data]
        new_groups = new_data["groups"] if name == "group" else [new_data]
        assert len(old_groups) == len(new_groups)
        for old_group, new_group in zip(old_groups, new_groups):
            for key, value in old_group.items():
                if key not in ("matches", "standings", "ranking_draw_order"):
                    assert new_group[key] == value, f"Group field changed: {key}"
            assert len(old_group["matches"]) == len(new_group["matches"])
            for old_match, new_match in zip(old_group["matches"], new_group["matches"]):
                for key, value in old_match.items():
                    if key not in ("games", "finished"):
                        assert new_match[key] == value, f"Match field changed: {key}"


def apply_plan():
    plan = read("plan.json")
    baseline = {d["event"]["slug"]: d for d in read("before.json")["competitions"][0]["event_details"]}
    current = {event["slug"]: api(f"/api/competitions/test/events/{event['slug']}") for event in plan["events"]}
    # Validate every target before the first mutation; reruns may only resume our exact score prefixes.
    for event in plan["events"]:
        detail = current[event["slug"]]
        assert_identity(baseline[event["slug"]], detail)
        actual_data = detail["stages"][event["stage"]]["data"]
        actual_groups = actual_data["groups"] if event["stage"] == "group" else [actual_data]
        for group, actual_group in zip(event["groups"], actual_groups):
            for desired, match in zip(group["matches"], actual_group["matches"]):
                assert desired["id"] == match["id"]
                assert match["games"] == desired["games"][:len(match["games"])], "Unexpected user score changes"
                assert not match["finished"] or match["games"] == desired["games"]
    completed = 0
    for event in plan["events"]:
        detail = current[event["slug"]]
        for group_index, group in enumerate(event["groups"]):
            data = detail["stages"][event["stage"]]["data"]
            actual_group = data["groups"][group_index] if event["stage"] == "group" else data
            existing = {match["id"]: match for match in actual_group["matches"]}
            for desired in group["matches"]:
                match = existing[desired["id"]]
                endpoint = f"/api/competitions/test/events/{event['slug']}/stages/{event['stage']}/matches/{match['id']}"
                for a, b in desired["games"][len(match["games"]):]:
                    detail = api(endpoint + "/games", "POST", {"a": a, "b": b})
                if not match["finished"]:
                    detail = api(endpoint, "PATCH", {"finished": True})
                completed += 1
            final_data = detail["stages"][event["stage"]]["data"]
            final_group = final_data["groups"][group_index] if event["stage"] == "group" else final_data
            expected = group["expected_order"]
            actual_order = final_group["standings"]["order"]
            assert expected is None or actual_order == expected, f"Ranking mismatch: {event['slug']} group {group['id']}"
            if group["case"] == "lottery":
                assert actual_order == final_group["ranking_draw_order"]
            print(f"{completed}/333 {event['slug']} group {group['id']}: {group['case']} -> {actual_order}", flush=True)
    after = api("/api/data")
    save("after.json", after)
    for detail in after["competitions"][0]["event_details"]:
        assert_identity(baseline[detail["event"]["slug"]], detail)
    print("All 333 matches saved through score/completion endpoints. No submission endpoint was called.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("plan", "apply"))
    arguments = parser.parse_args()
    make_plan() if arguments.operation == "plan" else apply_plan()
