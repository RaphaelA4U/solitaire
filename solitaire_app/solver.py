"""A Klondike solver, used to deal only games that can be won.

Depth-first search with a transposition table and a node budget. It only
ever plays legal moves, so a deal it solves is winnable for certain; a
deal it gives up on is simply skipped (it may still be winnable). That
keeps "winnable deals only" honest without having to be a perfect solver.

The stock and waste are modelled as one "talon" list in draw order plus a
position (how many cards are on the waste). From any position the solver
knows which talon cards can be reached by turning the stock (draw 1: all
of them; draw 3: every third, shifting as cards are played), including
recycles while passes remain (Vegas limits them).
"""

from __future__ import annotations

import random
import time
from typing import Callable, Optional

from .game import SUITS, TABLEAU, Klondike

# card = suit_index * 13 + (rank - 1); suits in SUITS order (S H D C)
RED = (False, True, True, False)


def _rank(c: int) -> int:
    return c % 13 + 1


def _suit(c: int) -> int:
    return c // 13


def _red(c: int) -> bool:
    return RED[c // 13]


def _encode(card) -> int:
    return SUITS.index(card.suit) * 13 + card.rank - 1


class _Budget(Exception):
    pass


class Solver:
    def __init__(self, game: Klondike, node_limit: int = 60_000) -> None:
        self.draw = game.draw
        self.max_passes = game.max_passes  # None = unlimited
        self.node_limit = node_limit
        self.nodes = 0
        # Tableau: (hidden cards tuple, face-up cards tuple) per column.
        self.hidden = []
        self.up = []
        for t in TABLEAU:
            cards = game.piles[t]
            self.hidden.append([_encode(c) for c in cards if not c.up])
            self.up.append([_encode(c) for c in cards if c.up])
        self.found = [0, 0, 0, 0]  # cards per suit on the foundations
        for f in ("f0", "f1", "f2", "f3"):
            for c in game.piles[f]:
                self.found[SUITS.index(c.suit)] = max(self.found[SUITS.index(c.suit)], c.rank)
        # Talon in draw order: waste bottom..top, then stock top..bottom.
        self.talon = [_encode(c) for c in game.piles["waste"]] + [
            _encode(c) for c in reversed(game.piles["stock"])
        ]
        self.pos = len(game.piles["waste"])
        self.passes = game.passes
        self.seen: set = set()
        self.path: list = []  # the line being searched; the solution after a win

    # -- helpers -----------------------------------------------------------
    def _key(self):
        cols = tuple(sorted((len(h), tuple(u)) for h, u in zip(self.hidden, self.up)))
        talon_key = (tuple(self.talon), self.pos if self.draw == 3 or self.max_passes else 0)
        passes = self.passes if self.max_passes else 0
        return (tuple(self.found), cols, talon_key, passes)

    def _fits_found(self, c: int) -> bool:
        return self.found[_suit(c)] == _rank(c) - 1

    def _fits_on(self, c: int, col: int) -> bool:
        up = self.up[col]
        if not up:
            return not self.hidden[col] and _rank(c) == 13
        top = up[-1]
        return _rank(top) == _rank(c) + 1 and _red(top) != _red(c)

    def _safe_auto(self, c: int) -> bool:
        """Playing this card home can never hurt (the classic safe rule)."""
        r = _rank(c)
        if r <= 2:
            return True
        if _red(c):
            return self.found[0] >= r - 1 and self.found[3] >= r - 1
        return self.found[1] >= r - 1 and self.found[2] >= r - 1

    def _reachable(self) -> list[tuple[int, int]]:
        """(talon index, passes used to get there) for every playable talon card."""
        n = len(self.talon)
        if n == 0:
            return []
        out: list[tuple[int, int]] = []
        seen: set = set()
        if self.draw == 1:
            # This pass: the waste top, then every card still in the stock.
            out = [(i, 0) for i in range(max(self.pos - 1, 0), n)]
            if self.max_passes is None or self.passes < self.max_passes:
                # After a recycle: the cards buried under the waste top.
                out += [(i, 1) for i in range(0, self.pos - 1)]
            return out
        # Draw 3: the top after each turn of three.
        p = self.pos
        if p > 0:
            out.append((p - 1, 0))
            seen.add(p - 1)
        while p < n:
            p = min(p + 3, n)
            if p - 1 not in seen:
                out.append((p - 1, 0))
                seen.add(p - 1)
        extra = 1
        while self.max_passes is None or self.passes + extra <= self.max_passes:
            p, added = 0, False
            while p < n:
                p = min(p + 3, n)
                if p - 1 not in seen:
                    out.append((p - 1, extra))
                    seen.add(p - 1)
                    added = True
            if not added or self.max_passes is None:
                break
            extra += 1
        return out

    # -- search ------------------------------------------------------------
    def solve(self) -> bool:
        try:
            return self._dfs()
        except _Budget:
            return False

    def _dfs(self) -> bool:
        self.nodes += 1
        if self.nodes > self.node_limit:
            raise _Budget
        undo: list = []
        self._auto_play(undo)
        # No try/finally on purpose: after a win or an exhausted budget the
        # board is never used again, so only a dead end gets undone.
        if sum(self.found) == 52:
            return True
        key = self._key()
        if key not in self.seen:
            self.seen.add(key)
            for move in self._moves():
                saved = self._apply(move)
                if self._dfs():
                    return True
                self._revert(move, saved)
        for action in reversed(undo):
            self._revert(*action)
        return False

    def _auto_play(self, undo: list) -> None:
        changed = True
        while changed:
            changed = False
            for col in range(7):
                up = self.up[col]
                if up and self._fits_found(up[-1]) and self._safe_auto(up[-1]):
                    move = ("tf", col)
                    undo.append((move, self._apply(move)))
                    changed = True
            if self.pos > 0:
                c = self.talon[self.pos - 1]
                if self._fits_found(c) and self._safe_auto(c):
                    move = ("wf", self.pos - 1, 0)
                    undo.append((move, self._apply(move)))
                    changed = True

    def _moves(self) -> list:
        moves = []
        # 1. Tableau tops home.
        for col in range(7):
            up = self.up[col]
            if up and self._fits_found(up[-1]):
                moves.append(("tf", col))
        reach = self._reachable()
        # 2. Talon cards home.
        for idx, extra in reach:
            if self._fits_found(self.talon[idx]):
                moves.append(("wf", idx, extra))
        # 3. Whole face-up runs that turn over a card (or free a column for a king).
        reveal = []
        for col in range(7):
            up = self.up[col]
            if not up:
                continue
            base = up[0]
            for dst in range(7):
                if dst == col or not self._fits_on(base, dst):
                    continue
                if self.hidden[col]:
                    reveal.append((-len(self.hidden[col]), ("tt", col, 0, dst)))
                elif self.up[dst]:
                    # Empties a column: only useful when a king can use it.
                    reveal.append((1, ("tt", col, 0, dst)))
                break
        reveal.sort(key=lambda r: r[0])
        moves.extend(m for _, m in reveal)
        # 4. Talon cards onto the tableau.
        for idx, extra in reach:
            c = self.talon[idx]
            for dst in range(7):
                if self._fits_on(c, dst):
                    moves.append(("wt", idx, extra, dst))
                    break
        # 5. Partial runs that uncover a card that can go home.
        for col in range(7):
            up = self.up[col]
            for i in range(1, len(up)):
                if not self._fits_found(up[i - 1]):
                    continue
                for dst in range(7):
                    if dst != col and self._fits_on(up[i], dst):
                        moves.append(("tt", col, i, dst))
                        break
        return moves

    def _apply(self, move):
        saved = self._do(move)
        self.path.append((move, saved))
        return saved

    def _do(self, move):
        kind = move[0]
        if kind == "tf":
            col = move[1]
            c = self.up[col].pop()
            self.found[_suit(c)] += 1
            flipped = self._flip(col)
            return (c, flipped)
        if kind in ("wf", "wt"):
            idx, extra = move[1], move[2]
            c = self.talon.pop(idx)
            saved = (self.pos, self.passes)
            self.pos = idx
            self.passes += extra
            if kind == "wf":
                self.found[_suit(c)] += 1
            else:
                self.up[move[3]].append(c)
            return (c, saved)
        # tt
        col, i, dst = move[1], move[2], move[3]
        run = self.up[col][i:]
        del self.up[col][i:]
        self.up[dst].extend(run)
        flipped = self._flip(col)
        return (len(run), flipped)

    def _revert(self, move, saved) -> None:
        self.path.pop()
        kind = move[0]
        if kind == "tf":
            c, flipped = saved
            col = move[1]
            if flipped:
                self.hidden[col].append(self.up[col].pop())
            self.found[_suit(c)] -= 1
            self.up[col].append(c)
            return
        if kind in ("wf", "wt"):
            c, (pos, passes) = saved
            if kind == "wf":
                self.found[_suit(c)] -= 1
            else:
                self.up[move[3]].pop()
            self.talon.insert(move[1], c)
            self.pos, self.passes = pos, passes
            return
        count, flipped = saved
        col, dst = move[1], move[3]
        if flipped:
            self.hidden[col].append(self.up[col].pop())
        run = self.up[dst][-count:]
        del self.up[dst][-count:]
        self.up[col].extend(run)

    def _flip(self, col: int) -> bool:
        if not self.up[col] and self.hidden[col]:
            self.up[col].append(self.hidden[col].pop())
            return True
        return False


def solution(game: Klondike, node_limit: int = 60_000) -> Optional[list]:
    """The winning line as (move, saved) pairs, or None."""
    solver = Solver(game, node_limit)
    return list(solver.path) if solver.solve() else None


def is_winnable(seed: int, draw: int = 1, scoring: str = "standard", node_limit: int = 60_000) -> bool:
    return Solver(Klondike(seed=seed, draw=draw, scoring=scoring), node_limit).solve()


def find_winnable_seed(
    draw: int = 1,
    scoring: str = "standard",
    time_budget: float = 20.0,
    node_limit: int = 60_000,
    rng: Optional[random.Random] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Optional[int]:
    """A deal number that is proven winnable, or None when time runs out."""
    rng = rng or random.SystemRandom()
    deadline = time.monotonic() + time_budget
    while time.monotonic() < deadline:
        if should_stop is not None and should_stop():
            return None
        seed = rng.randint(1, 999_999)
        if is_winnable(seed, draw, scoring, node_limit):
            return seed
    return None
