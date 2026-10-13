"""Independently verify the large test's before/after API snapshots.

Read-only, standard library only. No project backend modules are imported.
Run: python tmp/large-test-results-20261013/verify_results.py
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import json
from pathlib import Path
import sys


EXPECTED = {
    "test-men-single": (140, 64),
    "test-women-single": (31, 16),
    "test-men-double": (30, 16),
    "test-women-double": (4, None),
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def compare(actual, expected, label):
    """Give the first useful differing JSON path rather than a huge dump."""
    require(type(actual) is type(expected), f"{label}: type changed")
    if isinstance(actual, dict):
        require(actual.keys() == expected.keys(), f"{label}: keys changed")
        for key in expected:
            compare(actual[key], expected[key], f"{label}.{key}")
    elif isinstance(actual, list):
        require(len(actual) == len(expected), f"{label}: length changed")
        for index, (a, b) in enumerate(zip(actual, expected)):
            compare(a, b, f"{label}[{index}]")
    else:
        require(actual == expected, f"{label}: {actual!r} != {expected!r}")


def without(record, *keys):
    return {k: v for k, v in record.items() if k not in keys}


def match_statistics(match, label):
    require(match["finished"] is True, f"{label}: match not finished")
    games = match["games"]
    require(len(games) in (2, 3), f"{label}: expected 2 or 3 games")
    won = [0, 0]
    balls = [0, 0]
    for position, score in enumerate(games):
        require(isinstance(score, list) and len(score) == 2, f"{label}: invalid score pair")
        require(all(type(x) is int and x >= 0 for x in score), f"{label}: scores must be nonnegative integers")
        hi, lo = max(score), min(score)
        require((hi == 11 and lo <= 9) or (lo >= 10 and hi == lo + 2),
                f"{label} game {position + 1}: illegal table tennis score {score}")
        require(max(won) < 2, f"{label}: played after one side already won two games")
        winner_side = int(score[1] > score[0])
        won[winner_side] += 1
        for side in (0, 1):
            balls[side] += score[side]
    require(sorted(won) in ([0, 2], [1, 2]), f"{label}: not a best-of-three match")
    return {"winner": match["a"] if won[0] == 2 else match["b"],
            "a": match["a"], "b": match["b"], "won": won, "balls": balls}


def ratio(numerator, denominator):
    # First coordinate orders positive/zero above every finite Fraction.
    return (int(denominator == 0 and numerator > 0),
            Fraction(numerator, denominator) if denominator else Fraction(0))


def independent_standings(participant_count, statistics, draw_order):
    """Rank from aggregate mini-league tables and recursively refine ties.

    Every inner comparison uses the original group tied on total wins;
    smaller ties never create a new mini league. Recursive depth controls
    which trace fields are populated, allowing full JSON trace comparison.
    """
    require(isinstance(draw_order, list) and len(draw_order) == participant_count,
            "ranking_draw_order must exist for every completed pool")
    require(all(type(index) is int for index in draw_order) and
            sorted(draw_order) == list(range(participant_count)),
            "ranking_draw_order must be a complete index permutation")
    total_wins = Counter(record["winner"] for record in statistics)
    rows = {index: {"index": index, "rank": None, "points": total_wins[index],
                    "tied_points": None, "game_ratio": None,
                    "point_ratio": None, "draw_rank": None}
            for index in range(participant_count)}
    global_pools = defaultdict(list)
    for index in range(participant_count):
        global_pools[total_wins[index]].append(index)

    final_order = []
    for wins in sorted(global_pools, reverse=True):
        pool = global_pools[wins]
        if len(pool) == 1:
            final_order.extend(pool)
            continue
        mini = {index: {"wins": 0, "games_for": 0, "games_against": 0,
                        "balls_for": 0, "balls_against": 0} for index in pool}
        for record in statistics:
            if record["a"] not in mini or record["b"] not in mini:
                continue
            mini[record["winner"]]["wins"] += 1
            for side, index in enumerate((record["a"], record["b"])):
                mini[index]["games_for"] += record["won"][side]
                mini[index]["games_against"] += record["won"][1 - side]
                mini[index]["balls_for"] += record["balls"][side]
                mini[index]["balls_against"] += record["balls"][1 - side]

        def refine(indices, depth=0):
            if len(indices) == 1:
                return indices
            if depth == 3:
                ordered = sorted(indices, key=draw_order.index)
                for rank, index in enumerate(ordered, 1):
                    rows[index]["draw_rank"] = rank
                return ordered
            grouped = defaultdict(list)
            for index in indices:
                if depth == 0:
                    value = mini[index]["wins"]
                    rows[index]["tied_points"] = value
                else:
                    key = "games" if depth == 1 else "balls"
                    numerator = mini[index][key + "_for"]
                    denominator = mini[index][key + "_against"]
                    rows[index]["game_ratio" if depth == 1 else "point_ratio"] = {
                        "won": numerator, "lost": denominator}
                    value = ratio(numerator, denominator)
                grouped[value].append(index)
            return [index for value in sorted(grouped, reverse=True)
                    for index in refine(grouped[value], depth + 1)]

        final_order.extend(refine(pool))
    for rank, index in enumerate(final_order, 1):
        rows[index]["rank"] = rank
    return {"order": final_order, "rows": [rows[index] for index in final_order]}


def verify_pool(before, after, label):
    mutable = ("matches", "standings", "ranking_draw_order")
    compare(without(after, *mutable), without(before, *mutable), label)
    require(len(after["matches"]) == len(before["matches"]), f"{label}: match count changed")
    count = len(after["participants"])
    expected_pairs = {(a, b) for a in range(count) for b in range(a + 1, count)}
    pairs, match_ids, records = set(), set(), []
    for old, new in zip(before["matches"], after["matches"]):
        match_label = f"{label}.{new['id']}"
        compare(without(new, "games", "finished"), without(old, "games", "finished"), match_label)
        require(new["id"] not in match_ids, f"{label}: duplicate match ID")
        match_ids.add(new["id"])
        require(type(new["a"]) is int and type(new["b"]) is int, f"{match_label}: invalid index")
        pair = tuple(sorted((new["a"], new["b"])))
        require(pair in expected_pairs and pair not in pairs, f"{match_label}: invalid or duplicate fixture")
        pairs.add(pair)
        records.append(match_statistics(new, match_label))
    compare(sorted(pairs), sorted(expected_pairs), f"{label}: complete fixture")
    draw_order = after["ranking_draw_order"]
    if before.get("ranking_draw_order") is not None:
        compare(draw_order, before["ranking_draw_order"], f"{label}: persisted draw order")
    expected = independent_standings(count, records, draw_order)
    compare(after["standings"], expected, label + ".standings")
    reached = {field: sum(row[field] is not None for row in expected["rows"])
               for field in ("tied_points", "game_ratio", "point_ratio", "draw_rank")}
    return {"pool": label, "participants": count, "matches": len(records),
            "match_scores": dict(Counter("2:0" if len(match["games"]) == 2 else "2:1"
                                          for match in after["matches"])),
            "ranking_comparison_participants": reached,
            "order": expected["order"]}


def verify_snapshots(before, after):
    compare(without(after, "competitions"), without(before, "competitions"), "snapshot")
    compare(len(after["competitions"]), len(before["competitions"]), "competition count")
    checked, pool_reports = {}, []
    for old_comp, new_comp in zip(before["competitions"], after["competitions"]):
        compare(without(new_comp, "event_details"), without(old_comp, "event_details"), "competition")
        compare(len(new_comp["event_details"]), len(old_comp["event_details"]), "event count")
        for old_event, new_event in zip(old_comp["event_details"], new_comp["event_details"]):
            slug = old_event["event"]["slug"]
            if slug not in EXPECTED:
                compare(new_event, old_event, slug)
                continue
            require(slug not in checked, f"{slug}: duplicate event")
            compare(without(new_event, "stages"), without(old_event, "stages"), slug)
            old_stages, new_stages = old_event["stages"], new_event["stages"]
            compare(list(new_stages), list(old_stages), slug + ".stage names")
            stage = "group" if "group" in old_stages else "round_robin"
            old_stage, new_stage = old_stages[stage], new_stages[stage]
            compare(without(new_stage, "data"), without(old_stage, "data"), slug + ".metadata")
            entrant_count, expected_qualifiers = EXPECTED[slug]
            compare(len(new_stage["metadata"]["entrants"]), entrant_count, slug + ".entrant count")
            old_data, new_data = old_stage["data"], new_stage["data"]
            event_reports = []
            if stage == "round_robin":
                event_reports.append(verify_pool(old_data, new_data, slug + ".round_robin"))
            else:
                compare(without(new_data, "groups"), without(old_data, "groups"), slug + ".group data")
                compare(len(new_data["groups"]), len(old_data["groups"]), slug + ".group count")
                for old_group, new_group in zip(old_data["groups"], new_data["groups"]):
                    event_reports.append(verify_pool(old_group, new_group, f"{slug}.group-{old_group['id']}"))
                compare(sum(len(group["participants"]) for group in new_data["groups"]),
                        entrant_count, slug + ".drawn participant count")
                seeded_groups = min(sum(entrant["seed"] > 0 for entrant in new_stage["metadata"]["entrants"]),
                                    len(new_data["groups"]))
                qualified = []
                for group_index, group in enumerate(new_data["groups"]):
                    for rank, index in enumerate(group["standings"]["order"][:group["qualifiers"]]):
                        seed = max(seeded_groups - group_index, 0) if rank == 0 else -1
                        qualified.append({"participant": group["participants"][index], "seed": seed})
                compare(len(qualified), expected_qualifiers, slug + ".qualifier count")
                old_ko, new_ko = old_stages["knockout"], new_stages["knockout"]
                compare(without(new_ko, "metadata"), without(old_ko, "metadata"), slug + ".knockout unchanged")
                compare(without(new_ko["metadata"], "entrants"), without(old_ko["metadata"], "entrants"),
                        slug + ".knockout metadata")
                compare(new_ko["metadata"]["entrants"], qualified, slug + ".knockout entrants and seeds")
                require(new_ko["data"]["drawn"] is False, slug + ": knockout was drawn")
                ko_matches = new_ko["data"]["matches"] + ([new_ko["data"]["bronze_match"]]
                              if new_ko["data"].get("bronze_match") else [])
                for match in ko_matches:
                    require(not match["games"] and not match["finished"], slug + ": knockout has scores or completion")
            checked[slug] = {"entrants": entrant_count, "pools": len(event_reports),
                             "matches": sum(report["matches"] for report in event_reports),
                             "qualified": expected_qualifiers}
            pool_reports.extend(event_reports)
    compare(set(checked), set(EXPECTED), "expected four events")
    total_matches = sum(event["matches"] for event in checked.values())
    compare(total_matches, 333, "total completed matches")
    return {"ok": True, "total_matches": total_matches, "events": checked,
            "tie_cases": [report for report in pool_reports
                          if report["ranking_comparison_participants"]["tied_points"]],
            "all_pools": pool_reports}


def verify_plan(plan, after):
    details = {event["event"]["slug"]: event for comp in after["competitions"]
               for event in comp["event_details"]}
    count = 0
    cases = Counter()
    for event in plan["events"]:
        data = details[event["slug"]]["stages"][event["stage"]]["data"]
        pools = data["groups"] if event["stage"] == "group" else [data]
        require(len(pools) == len(event["groups"]), event["slug"] + ": planned pool count")
        for planned, actual in zip(event["groups"], pools):
            label = f"{event['slug']}.{planned['id']}"
            if event["stage"] == "group":
                compare(actual["id"], planned["id"], label + ".id")
            compare(actual["participants"], planned["participants"], label + ".participants")
            require(len(actual["matches"]) == len(planned["matches"]), label + ": planned match count")
            for intended, played in zip(planned["matches"], actual["matches"]):
                for field in ("id", "a", "b", "games"):
                    compare(played[field], intended[field], label + "." + field)
                count += 1
            if planned.get("expected_order") is not None:
                compare(actual["standings"]["order"], planned["expected_order"], label + ".expected_order")
            cases[planned["case"]] += 1
    compare(count, 333, "matches exactly matching plan")
    return {"matches": count, "cases": dict(cases)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, default=Path(__file__).with_name("before.json"))
    parser.add_argument("--after", type=Path, default=Path(__file__).with_name("after.json"))
    parser.add_argument("--plan", type=Path, default=Path(__file__).with_name("plan.json"))
    args = parser.parse_args()
    before = json.loads(args.before.read_text(encoding="utf-8-sig"))
    after = json.loads(args.after.read_text(encoding="utf-8-sig"))
    report = verify_snapshots(before, after)
    if args.plan.exists():
        report["plan"] = verify_plan(json.loads(args.plan.read_text(encoding="utf-8-sig")), after)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
