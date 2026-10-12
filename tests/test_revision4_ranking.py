"""Ranking priorities and the one-pass tied-player comparison."""

from __future__ import annotations

import unittest

from backend.logic import rank_round_robin


def _players(*names: str) -> list[dict]:
    return [{"kind": "player", "members": [name], "team": ""} for name in names]


def _match(a: int, b: int, *games: tuple[int | None, int | None]) -> dict:
    return {"a": a, "b": b, "finished": True, "games": [list(game) for game in games]}


def _games(a: int, b: int, count: int) -> list[tuple[int, int]]:
    return [(a, b)] * count


class RoundRobinRankingTests(unittest.TestCase):
    def test_waits_until_every_match_is_finished(self) -> None:
        matches = [_match(0, 1, (11, 5)), _match(0, 2, (11, 5))]
        matches[1]["finished"] = False
        self.assertIsNone(rank_round_robin(_players("甲", "乙", "丙"), matches))

    def test_match_wins_outrank_more_won_games(self) -> None:
        matches = [
            _match(0, 1, (11, 9), (9, 11), (11, 9), (9, 11), (11, 9)),
            _match(0, 2, (11, 5)),
            _match(0, 3, (11, 5)),
            _match(1, 2, *_games(11, 5, 3)),
            _match(1, 3, *_games(11, 5, 3)),
            _match(2, 3, (11, 5)),
        ]
        # Player 0: three match wins, five game wins.
        # Player 1: two match wins, eight game wins.
        self.assertEqual(rank_round_robin(_players("甲", "乙", "丙", "丁"), matches), [0, 1, 2, 3])

    def test_won_games_break_a_match_win_tie(self) -> None:
        matches = [
            _match(0, 1, (11, 9), (9, 11), (11, 9)),
            _match(0, 2, (8, 11)),
            _match(1, 2, *_games(11, 5, 3)),
        ]
        self.assertEqual(rank_round_robin(_players("甲", "乙", "丙"), matches), [1, 0, 2])

    def test_points_include_points_scored_in_a_lost_game(self) -> None:
        matches = [
            _match(0, 1, (11, 0)),
            _match(0, 2, (5, 11)),
            _match(1, 2, (11, 0)),
        ]
        # All have one match and one game won. Player 0's five points in
        # the lost game put it first; players 1 and 2 each score 11.
        self.assertEqual(rank_round_robin(_players("Z", "Zed", "Able"), matches), [0, 1, 2])

    def test_exact_overall_tie_rechecks_only_tied_players(self) -> None:
        matches = [
            _match(0, 1, *_games(11, 0, 3), *_games(0, 11, 2)),
            _match(0, 2, (0, 11)),
            _match(1, 2, *_games(11, 0, 2), (0, 11)),
            _match(0, 3, *_games(11, 0, 2)),
            _match(1, 3, (11, 0)),
            _match(2, 3, *_games(11, 0, 3)),
        ]
        # Players 0, 1, 2 all have two match wins, five game wins, and
        # 55 points overall. Their mutual game wins are 3, 4, and 2.
        self.assertEqual(rank_round_robin(_players("甲", "乙", "丙", "丁"), matches), [1, 0, 2, 3])

    def test_mutual_points_break_a_tie_after_mutual_wins_and_games(self) -> None:
        matches = [
            _match(0, 1, (11, 1)),
            _match(0, 2, (1, 12)),
            _match(1, 2, (11, 1)),
            _match(0, 3, (11, 0)),
            _match(1, 3, (11, 0)),
            _match(2, 3, (10, 0)),
        ]
        # The first three players all have two match wins, two game wins,
        # and 23 points overall. In their own matches, player 2 scored 13
        # points; players 0 and 1 scored 12 each.
        self.assertEqual(rank_round_robin(_players("Zed", "Able", "Y", "D"), matches), [2, 1, 0, 3])

    def test_name_breaks_a_remaining_tie(self) -> None:
        matches = [
            _match(0, 1, (11, 5)),
            _match(0, 2, (5, 11)),
            _match(1, 2, (11, 5)),
        ]
        # The three-way cycle stays equal after the mutual comparison.
        self.assertEqual(rank_round_robin(_players("Zoe", "Amy", "Kai"), matches), [1, 2, 0])

    def test_unentered_game_does_not_add_games_or_points(self) -> None:
        matches = [
            _match(0, 1, (11, 5), (999, None)),
            _match(0, 2, (5, 11)),
            _match(1, 2, (11, 5)),
        ]
        self.assertEqual(rank_round_robin(_players("Zoe", "Amy", "Kai"), matches), [1, 2, 0])


if __name__ == "__main__":
    unittest.main()
