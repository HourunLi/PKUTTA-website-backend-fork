"""Independently audit the completed test competition knockout stages.

Read-only and standard-library-only. No backend module is imported.
Run: python tmp/large-test-knockout-20261013/verify_knockout.py

The backup directory is the copied ``webpage/database/test`` folder made by
``populate_knockout.py --prepare``. This verifier compares all JSON files in
that competition and permits changes only in the three knockout data files.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
EVENTS = {"test-men-single": 64, "test-women-single": 16, "test-men-double": 16}
COMPETITOR_KINDS = {"player", "pair", "team"}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def compare(actual, expected, label):
    """Compare JSON values and report the first divergent path."""
    require(type(actual) is type(expected), f"{label}: type differs")
    if isinstance(expected, dict):
        require(actual.keys() == expected.keys(), f"{label}: keys differ")
        for key in expected:
            compare(actual[key], expected[key], f"{label}.{key}")
    elif isinstance(expected, list):
        require(len(actual) == len(expected), f"{label}: length differs")
        for index, (a, e) in enumerate(zip(actual, expected)):
            compare(a, e, f"{label}[{index}]")
    else:
        require(actual == expected, f"{label}: {actual!r} != {expected!r}")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def name(participant):
    return " / ".join(str(x) for x in participant.get("members", [])) or str(participant.get("team", ""))


def identity(participant):
    return json.dumps(participant, ensure_ascii=False, sort_keys=True)


def verify_score(match, label):
    require(match["finished"] is True, f"{label}: unfinished match")
    require(match["a"]["kind"] in COMPETITOR_KINDS and match["b"]["kind"] in COMPETITOR_KINDS,
            f"{label}: match needs two real competitors")
    require(match["a"] != match["b"], f"{label}: competitor played itself")
    games = match["games"]
    require(isinstance(games, list) and len(games) in (2, 3), f"{label}: expected 2 or 3 games")
    won = [0, 0]
    for index, score in enumerate(games):
        require(isinstance(score, list) and len(score) == 2, f"{label}: invalid game {index + 1}")
        require(all(type(value) is int and value >= 0 for value in score),
                f"{label}: game {index + 1} scores must be nonnegative integers")
        high, low = max(score), min(score)
        require((high == 11 and low <= 9) or (low >= 10 and high == low + 2),
                f"{label}: illegal 11-point score {score}")
        require(max(won) < 2, f"{label}: game played after match had ended")
        won[int(score[1] > score[0])] += 1
    require(sorted(won) in ([0, 2], [1, 2]), f"{label}: not a best-of-three result")
    side = 0 if won[0] == 2 else 1
    return match["a" if side == 0 else "b"], match["b" if side == 0 else "a"], len(games)


def verify_fixture_fields(before, after, label):
    require(before.keys() == after.keys(), f"{label}: fixture keys changed")
    for key in ("id", "table", "time", "referee"):
        compare(after[key], before[key], label + "." + key)
    if "round" in before:
        for key in ("round", "index"):
            compare(after[key], before[key], label + "." + key)
    require(before["finished"] is False and before["games"] == [],
            f"{label}: backup already contains scores")


def verify_event(event, before, after, plan):
    label = event
    require(before["drawn"] is True and after["drawn"] is True, f"{label}: draw missing")
    for key in ("drawn", "rounds", "slots"):
        compare(after[key], before[key], f"{label}.{key}")
    rounds = after["rounds"]
    slots = after["slots"]
    require(type(rounds) is int and rounds >= 2, f"{label}: invalid number of rounds")
    require(len(slots) == EVENTS[event] == 2**rounds, f"{label}: bracket size changed")
    require(all(slot["kind"] in COMPETITOR_KINDS for slot in slots), f"{label}: a slot is not a competitor")
    require(len({identity(slot) for slot in slots}) == len(slots), f"{label}: duplicate participant in slots")
    require(len(before["matches"]) == len(after["matches"]) == len(slots) - 1,
            f"{label}: incorrect match count")

    old_matches = {match["id"]: match for match in before["matches"]}
    require(len(old_matches) == len(before["matches"]), f"{label}: duplicate match ID in backup")
    plan_matches = {match["id"]: match for match in plan["matches"]}
    require(len(plan_matches) == len(plan["matches"]) == len(after["matches"]) + 1,
            f"{label}: plan must contain each bracket match and bronze match")

    winners_by_round = []
    losers_by_round = []
    recorded = {}
    formats = Counter()
    for round_index in range(rounds):
        matches = [match for match in after["matches"] if match["round"] == round_index]
        count = 2 ** (rounds - round_index - 1)
        require(len(matches) == count, f"{label}: round {round_index + 1} count")
        require([match["index"] for match in matches] == list(range(count)),
                f"{label}: round {round_index + 1} indices or order")
        winners, losers = [], []
        for index, match in enumerate(matches):
            match_id = f"r{round_index + 1}-m{index + 1}"
            fixture_label = f"{label}.{match_id}"
            compare(match["id"], match_id, fixture_label + ".id")
            require(match_id in old_matches and match_id in plan_matches,
                    f"{fixture_label}: missing original or plan")
            verify_fixture_fields(old_matches[match_id], match, fixture_label)
            if round_index == 0:
                expected_a, expected_b = slots[2 * index : 2 * index + 2]
            else:
                expected_a, expected_b = winners_by_round[-1][2 * index : 2 * index + 2]
            compare(match["a"], expected_a, fixture_label + ".a")
            compare(match["b"], expected_b, fixture_label + ".b")
            winner, loser, game_count = verify_score(match, fixture_label)
            winners.append(winner)
            losers.append(loser)
            formats[f"2:{game_count - 2}"] += 1
            recorded[match_id] = match
        winners_by_round.append(winners)
        losers_by_round.append(losers)

    bronze = after["bronze_match"]
    old_bronze = before["bronze_match"]
    require(isinstance(bronze, dict) and isinstance(old_bronze, dict), f"{label}: bronze fixture missing")
    verify_fixture_fields(old_bronze, bronze, label + ".bronze")
    compare(bronze["id"], "bronze", label + ".bronze.id")
    for side, competitor in zip(("a", "b"), losers_by_round[-2]):
        compare(bronze[side], competitor, f"{label}.bronze.{side}")
    third, fourth, bronze_games = verify_score(bronze, label + ".bronze")
    formats[f"2:{bronze_games - 2}"] += 1
    recorded["bronze"] = bronze

    medalists = [winners_by_round[-1][0], losers_by_round[-1][0], third, fourth]
    expected_results = [{"rank": index + 1, "participant": competitor}
                        for index, competitor in enumerate(medalists)]
    if rounds >= 3:
        fifth_place = sorted(losers_by_round[-3], key=name)
        expected_results += [{"rank": 5, "participant": competitor} for competitor in fifth_place]
    compare(after["results"], expected_results, label + ".results")
    compare(plan["results"], expected_results, label + ".plan.results")

    require(set(recorded) == set(plan_matches), f"{label}: plan match IDs differ")
    for match_id, match in recorded.items():
        planned = plan_matches[match_id]
        fixture_label = f"{label}.plan.{match_id}"
        for key in ("id", "a", "b", "games"):
            compare(planned[key], match[key], fixture_label + "." + key)
        expected_round = "bronze" if match_id == "bronze" else match["round"] + 1
        compare(planned["round"], expected_round, fixture_label + ".round")
        winner, _, _ = verify_score(match, fixture_label)
        compare(planned["winner"], name(winner), fixture_label + ".winner")
    return {"entrants": len(slots), "rounds": rounds,
            "matches_including_bronze": len(recorded), "score_formats": dict(formats),
            "medalists": [name(participant) for participant in medalists]}


def verify_database(before_root, after_root, plan):
    require(before_root.is_dir(), f"Backup directory missing: {before_root}")
    require(after_root.is_dir(), f"Live database directory missing: {after_root}")
    before_files = {path.relative_to(before_root).as_posix(): path for path in before_root.rglob("*.json")}
    after_files = {path.relative_to(after_root).as_posix(): path for path in after_root.rglob("*.json")}
    require(before_files.keys() == after_files.keys(), "Competition JSON file set changed")
    require(plan["competition"] == "test" and set(plan["events"]) == set(EVENTS),
            "Unexpected plan competition or event set")
    changed_expected = {f"{event}/knockout/data.json" for event in EVENTS}
    for relative_path in before_files:
        if relative_path not in changed_expected:
            compare(read_json(after_files[relative_path]), read_json(before_files[relative_path]),
                    relative_path + " unchanged")

    reports = {}
    for event in EVENTS:
        relative_path = f"{event}/knockout/data.json"
        before = read_json(before_files[relative_path])
        after = read_json(after_files[relative_path])
        metadata = read_json(after_files[f"{event}/knockout/metadata.json"])
        require(Counter(identity(entry["participant"]) for entry in metadata["entrants"]) ==
                Counter(identity(slot) for slot in after["slots"]),
                f"{event}: slots do not match knockout entrants")
        reports[event] = verify_event(event, before, after, plan["events"][event])
    require(sum(report["matches_including_bronze"] for report in reports.values()) == 96,
            "Expected 93 main bracket matches plus 3 bronze matches")
    return {"ok": True, "total_matches_including_bronze": 96,
            "unchanged_json_files": len(before_files) - len(changed_expected), "events": reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-dir", type=Path, default=HERE / "database-before")
    parser.add_argument("--after-dir", type=Path, default=REPO / "webpage" / "database" / "test")
    parser.add_argument("--plan", type=Path, default=HERE / "plan.json")
    args = parser.parse_args()
    result = verify_database(args.before_dir, args.after_dir, read_json(args.plan))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
