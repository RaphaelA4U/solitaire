"""The solver only calls a deal winnable when it really is: every found
solution is replayed move by move in the real rules engine."""

import random

import pytest

from solitaire_app.game import FOUNDATIONS, SUITS, TABLEAU, Klondike
from solitaire_app.solver import find_winnable_seed, solution


def _card(c: int) -> tuple[int, str]:
    return c % 13 + 1, SUITS[c // 13]


def replay(game: Klondike, line: list) -> None:
    for move, saved in line:
        kind = move[0]
        if kind == "tf":
            col = TABLEAU[move[1]]
            cards = game.piles[col]
            assert game.move(col, len(cards) - 1, game.foundation_for(cards[-1]))
        elif kind in ("wf", "wt"):
            rank, suit = _card(saved[0])
            for _ in range(200):
                top = game.top("waste")
                if top and (top.rank, top.suit) == (rank, suit):
                    break
                assert game.draw_stock(), "talon card not reachable"
            else:
                raise AssertionError("talon card never came up")
            idx = len(game.piles["waste"]) - 1
            dst = game.foundation_for(game.piles["waste"][-1]) if kind == "wf" else TABLEAU[move[3]]
            assert game.move("waste", idx, dst)
        else:
            col, i, dst = TABLEAU[move[1]], move[2], TABLEAU[move[3]]
            first_up = next(k for k, c in enumerate(game.piles[col]) if c.up)
            assert game.move(col, first_up + i, dst)
    assert game.is_won()


@pytest.mark.parametrize("draw,scoring", [(1, "standard"), (3, "standard"), (1, "vegas"), (3, "vegas")])
def test_every_solution_replays_to_a_win(draw, scoring):
    rng = random.Random(draw * 10 + len(scoring))
    solved = 0
    for _ in range(25):
        seed = rng.randint(1, 999_999)
        line = solution(Klondike(seed=seed, draw=draw, scoring=scoring), node_limit=30_000)
        if line is None:
            continue
        solved += 1
        replay(Klondike(seed=seed, draw=draw, scoring=scoring), line)
    assert solved >= 1


def test_find_winnable_seed():
    seed = find_winnable_seed(draw=3, time_budget=30, rng=random.Random(5))
    assert seed is not None
    assert solution(Klondike(seed=seed, draw=3)) is not None
