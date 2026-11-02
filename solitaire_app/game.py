"""Klondike rules engine: the single source of truth for the game.

Piles are addressed by short ids:
  "stock", "waste", "f0".."f3" (foundations), "t0".."t6" (tableau).

Every state-changing call pushes an undo snapshot first, so the UI never
has to think about undo. Scoring follows the classic desktop rules
(standard or Vegas); time is tracked by the caller via `elapsed`.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from typing import Any, Optional

SUITS = "SHDC"  # spades, hearts, diamonds, clubs
SUIT_SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
RED_SUITS = {"H", "D"}
RANK_LABELS = {1: "A", 11: "J", 12: "Q", 13: "K"}

FOUNDATIONS = [f"f{i}" for i in range(4)]
TABLEAU = [f"t{i}" for i in range(7)]
ALL_PILES = ["stock", "waste", *FOUNDATIONS, *TABLEAU]

SCORINGS = ["standard", "vegas", "vegas_cumulative", "none"]
SCORING_LABELS = {
    "standard": "Standard",
    "vegas": "Vegas",
    "vegas_cumulative": "Vegas (cumulative)",
    "none": "None",
}
SCORING_INFO = {
    "standard": "Points for every good move, minus time; a time bonus when you win.",
    "vegas": "Pay $52 per deal, win $5 per card on the foundations. Limited passes.",
    "vegas_cumulative": "Vegas, but your bankroll carries over from deal to deal.",
    "none": "Just play. No points, no pressure.",
}

UNDO_LIMIT = 1000
SAVED_UNDO_LIMIT = 200


def rank_label(rank: int) -> str:
    return RANK_LABELS.get(rank, str(rank))


@dataclass
class Card:
    rank: int  # 1 (ace) .. 13 (king)
    suit: str  # one of SUITS
    up: bool = False

    @property
    def red(self) -> bool:
        return self.suit in RED_SUITS

    @property
    def label(self) -> str:
        return f"{rank_label(self.rank)}{SUIT_SYMBOLS[self.suit]}"

    def to_list(self) -> list:
        return [self.rank, self.suit, 1 if self.up else 0]

    @classmethod
    def from_list(cls, data: list) -> "Card":
        return cls(int(data[0]), str(data[1]), bool(data[2]))


@dataclass
class Move:
    src: str
    index: int          # position in the source pile of the first card moved
    dst: str
    priority: int = 0   # higher = better (hints)
    reason: str = ""


def new_seed() -> int:
    return random.SystemRandom().randint(1, 999_999)


class Klondike:
    def __init__(
        self,
        seed: Optional[int] = None,
        draw: int = 1,
        scoring: str = "standard",
        bankroll: int = 0,
    ) -> None:
        self.seed = seed if seed is not None else new_seed()
        self.draw = 3 if draw == 3 else 1
        self.scoring = scoring if scoring in SCORINGS else "standard"
        self.piles: dict[str, list[Card]] = {p: [] for p in ALL_PILES}
        self.base_score = -52 if self.vegas else 0
        self.bankroll_start = bankroll if self.scoring == "vegas_cumulative" else 0
        self.moves = 0
        self.passes = 1          # passes through the stock (1 = the first)
        self.elapsed = 0.0       # seconds, maintained by the UI
        self.started = False     # timer starts at the first action
        self.winnable = False    # proven winnable by the solver when dealt
        self.history: list[dict] = []
        self._deal()

    # -- setup -----------------------------------------------------------
    @property
    def vegas(self) -> bool:
        return self.scoring in ("vegas", "vegas_cumulative")

    def _deal(self) -> None:
        deck = [Card(rank, suit) for suit in SUITS for rank in range(1, 14)]
        random.Random(self.seed).shuffle(deck)
        for col in range(7):
            for row in range(col + 1):
                card = deck.pop()
                card.up = row == col
                self.piles[TABLEAU[col]].append(card)
        self.piles["stock"] = deck  # top of the stock = last element

    # -- (de)serialisation & undo ----------------------------------------
    def _state(self) -> dict[str, Any]:
        return {
            "piles": {p: [c.to_list() for c in cards] for p, cards in self.piles.items()},
            "base_score": self.base_score,
            "moves": self.moves,
            "passes": self.passes,
        }

    def _load_state(self, state: dict[str, Any]) -> None:
        self.piles = {
            p: [Card.from_list(c) for c in state["piles"].get(p, [])] for p in ALL_PILES
        }
        self.base_score = int(state["base_score"])
        self.moves = int(state["moves"])
        self.passes = int(state["passes"])

    def _checkpoint(self) -> None:
        self.history.append(self._state())
        if len(self.history) > UNDO_LIMIT:
            del self.history[: len(self.history) - UNDO_LIMIT]
        self.started = True

    def can_undo(self) -> bool:
        return bool(self.history)

    def undo(self) -> bool:
        if not self.history:
            return False
        moves = self.moves
        self._load_state(self.history.pop())
        self.moves = moves + 1  # undo counts as a move, like the classics
        return True

    def to_dict(self) -> dict[str, Any]:
        data = self._state()
        data.update(
            seed=self.seed,
            draw=self.draw,
            scoring=self.scoring,
            bankroll_start=self.bankroll_start,
            elapsed=self.elapsed,
            started=self.started,
            winnable=self.winnable,
            history=self.history[-SAVED_UNDO_LIMIT:],
        )
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Klondike":
        game = cls(
            seed=int(data["seed"]),
            draw=int(data.get("draw", 1)),
            scoring=str(data.get("scoring", "standard")),
        )
        game._load_state(data)
        game.bankroll_start = int(data.get("bankroll_start", 0))
        game.elapsed = float(data.get("elapsed", 0.0))
        game.started = bool(data.get("started", False))
        game.winnable = bool(data.get("winnable", False))
        game.history = list(data.get("history", []))
        return game

    def clone(self) -> "Klondike":
        other = copy.copy(self)
        other.piles = {p: [Card(c.rank, c.suit, c.up) for c in cards] for p, cards in self.piles.items()}
        other.history = []
        return other

    # -- scoring ---------------------------------------------------------
    @property
    def max_passes(self) -> Optional[int]:
        if self.vegas:
            return 3 if self.draw == 3 else 1
        return None

    def foundation_cards(self) -> int:
        return sum(len(self.piles[f]) for f in FOUNDATIONS)

    @property
    def score(self) -> int:
        if self.scoring == "none":
            return 0
        if self.vegas:
            return self.base_score
        score = self.base_score - 2 * int(self.elapsed // 10)
        if self.is_won() and self.elapsed >= 30:
            score += int(700_000 // self.elapsed)
        return max(0, score)

    @property
    def bankroll(self) -> int:
        """Vegas cumulative: the running total including this deal."""
        return self.bankroll_start + self.base_score

    def _add(self, standard: int = 0, vegas: int = 0) -> None:
        if self.scoring == "standard":
            self.base_score = max(0, self.base_score + standard)
        elif self.vegas:
            self.base_score += vegas

    # -- queries ---------------------------------------------------------
    def top(self, pile: str) -> Optional[Card]:
        cards = self.piles[pile]
        return cards[-1] if cards else None

    def is_won(self) -> bool:
        return all(len(self.piles[f]) == 13 for f in FOUNDATIONS)

    def can_draw(self) -> bool:
        return bool(self.piles["stock"]) or self.can_recycle()

    def can_recycle(self) -> bool:
        if self.piles["stock"] or not self.piles["waste"]:
            return False
        return self.max_passes is None or self.passes < self.max_passes

    def movable_from(self, pile: str, index: int) -> bool:
        """May the cards from `index` up be picked up from `pile`?"""
        cards = self.piles.get(pile)
        if not cards or not (0 <= index < len(cards)):
            return False
        if pile == "stock":
            return False
        if pile == "waste" or pile in FOUNDATIONS:
            return index == len(cards) - 1
        run = cards[index:]
        if not all(c.up for c in run):
            return False
        for lower, upper in zip(run, run[1:]):
            if upper.rank != lower.rank - 1 or upper.red == lower.red:
                return False
        return True

    def accepts(self, dst: str, card: Card, count: int = 1) -> bool:
        """Can `card` (heading a run of `count` cards) be placed on `dst`?"""
        pile = self.piles.get(dst)
        if pile is None or dst in ("stock", "waste"):
            return False
        top = pile[-1] if pile else None
        if dst in FOUNDATIONS:
            if count != 1:
                return False
            if top is None:
                return card.rank == 1
            return top.suit == card.suit and card.rank == top.rank + 1
        if top is None:
            return card.rank == 13
        return top.up and top.red != card.red and card.rank == top.rank - 1

    def can_move(self, src: str, index: int, dst: str) -> bool:
        if src == dst or not self.movable_from(src, index):
            return False
        cards = self.piles[src]
        return self.accepts(dst, cards[index], len(cards) - index)

    def foundation_for(self, card: Card) -> Optional[str]:
        """The foundation this card can go on (its suit's, or an empty one)."""
        empty = None
        for f in FOUNDATIONS:
            top = self.top(f)
            if top is None:
                if card.rank == 1 and empty is None:
                    empty = f
            elif top.suit == card.suit and card.rank == top.rank + 1:
                return f
        return empty

    def best_target(self, src: str, index: int) -> Optional[str]:
        """Where a double-click sends these cards: foundation first, then a
        tableau column with cards, then an empty column (for a lone king
        that is not already alone)."""
        if not self.movable_from(src, index):
            return None
        cards = self.piles[src]
        card = cards[index]
        if index == len(cards) - 1 and src not in FOUNDATIONS:
            f = self.foundation_for(card)
            if f:
                return f
        count = len(cards) - index
        empty = None
        start = int(src[1]) if src in TABLEAU else -1
        # Scan tableau columns starting right of the source (feels natural).
        order = TABLEAU[start + 1:] + TABLEAU[: start + 1] if start >= 0 else TABLEAU
        for t in order:
            if t == src:
                continue
            if self.piles[t]:
                if self.accepts(t, card, count):
                    return t
            elif empty is None and self.accepts(t, card, count):
                if not (src in TABLEAU and index == 0):
                    empty = t
        return empty

    # -- actions ---------------------------------------------------------
    def draw_stock(self) -> bool:
        stock, waste = self.piles["stock"], self.piles["waste"]
        if stock:
            self._checkpoint()
            for _ in range(min(self.draw, len(stock))):
                card = stock.pop()
                card.up = True
                waste.append(card)
            self.moves += 1
            return True
        if self.can_recycle():
            self._checkpoint()
            while waste:
                card = waste.pop()
                card.up = False
                stock.append(card)
            self.passes += 1
            self.moves += 1
            if self.draw == 1:
                self._add(standard=-100)
            elif self.passes > 3:
                self._add(standard=-20)
            return True
        return False

    def move(self, src: str, index: int, dst: str) -> bool:
        if not self.can_move(src, index, dst):
            return False
        self._checkpoint()
        cards = self.piles[src]
        run = cards[index:]
        del cards[index:]
        self.piles[dst].extend(run)
        self.moves += 1
        to_found = dst in FOUNDATIONS
        if src == "waste":
            self._add(standard=10 if to_found else 5, vegas=5 if to_found else 0)
        elif src in TABLEAU and to_found:
            self._add(standard=10, vegas=5)
        elif src in FOUNDATIONS and dst in TABLEAU:
            self._add(standard=-15, vegas=-5)
        if src in TABLEAU and cards and not cards[-1].up:
            cards[-1].up = True
            self._add(standard=5)
        return True

    def auto_move(self, src: str, index: int) -> Optional[str]:
        dst = self.best_target(src, index)
        if dst and self.move(src, index, dst):
            return dst
        return None

    # -- hints & end-of-game detection -------------------------------------
    def legal_moves(self, include_draw: bool = True) -> list[Move]:
        """Useful moves, best first. Pointless shuffles are left out."""
        moves: list[Move] = []
        waste_top = self.top("waste")
        if waste_top:
            f = self.foundation_for(waste_top)
            if f:
                moves.append(Move("waste", len(self.piles["waste"]) - 1, f, 90 + (14 - waste_top.rank),
                                  "to the foundation"))
        for t in TABLEAU:
            cards = self.piles[t]
            if not cards:
                continue
            top = cards[-1]
            f = self.foundation_for(top)
            if f:
                reveals = len(cards) > 1 and not cards[-2].up
                moves.append(Move(t, len(cards) - 1, f, 90 + (14 - top.rank) + (8 if reveals else 0),
                                  "to the foundation"))
        for t in TABLEAU:
            cards = self.piles[t]
            first_up = next((i for i, c in enumerate(cards) if c.up), None)
            if first_up is None:
                continue
            for dst in TABLEAU:
                if dst == t or not self.can_move(t, first_up, dst):
                    continue
                if first_up > 0:
                    hidden = first_up
                    moves.append(Move(t, first_up, dst, 70 + hidden, "turns over a card"))
                elif self.piles[dst]:
                    # Empties a column: worth it when a king is waiting.
                    if self._king_waiting(t):
                        moves.append(Move(t, 0, dst, 40, "frees a column for a king"))
            # Moving part of a face-up run is only useful when the card it
            # uncovers can go up to the foundation.
            for i in range(first_up + 1, len(cards)):
                below = cards[i - 1]
                if self.foundation_for(below) is None:
                    continue
                for dst in TABLEAU:
                    if dst != t and self.can_move(t, i, dst):
                        moves.append(Move(t, i, dst, 60, "uncovers a foundation card"))
                        break
        if waste_top:
            for dst in TABLEAU:
                if self.can_move("waste", len(self.piles["waste"]) - 1, dst):
                    empty = not self.piles[dst]
                    moves.append(Move("waste", len(self.piles["waste"]) - 1, dst, 30 if empty else 50,
                                      "plays the waste card"))
                    break
        if include_draw and self.can_draw():
            moves.append(Move("stock", 0, "waste", 10,
                              "turns over the stock" if self.piles["stock"] else "recycles the waste"))
        moves.sort(key=lambda m: -m.priority)
        return moves

    def _king_waiting(self, exclude: str) -> bool:
        for t in TABLEAU:
            if t == exclude:
                continue
            cards = self.piles[t]
            first_up = next((i for i, c in enumerate(cards) if c.up), None)
            if first_up is not None and first_up > 0 and cards[first_up].rank == 13:
                return True
        if any(c.rank == 13 for c in self.piles["waste"] + self.piles["stock"]):
            return True
        return False

    def has_moves(self) -> bool:
        """False when the game is stuck: nothing useful on the table and no
        stock card (over all remaining passes) that can be played."""
        if self.is_won():
            return True
        if self.legal_moves(include_draw=False):
            return True
        sim = self.clone()
        seen = set()
        for _ in range(400):
            if not sim.draw_stock():
                return False
            key = (len(sim.piles["stock"]), len(sim.piles["waste"]), sim.passes > (sim.max_passes or 0))
            top = sim.top("waste")
            if top is not None:
                if sim.foundation_for(top):
                    return True
                for t in TABLEAU:
                    if sim.accepts(t, top):
                        return True
            sig = (key, top.label if top else "")
            if sig in seen and sim.max_passes is None:
                return False
            seen.add(sig)
        return False

    def autocomplete_plan(self) -> Optional[list[tuple[str, int, str]]]:
        """If the game can be finished with foundation moves and draws only,
        the steps to do so (('draw', 0, '') for a stock turn)."""
        if self.is_won():
            return None
        if any(not c.up for t in TABLEAU for c in self.piles[t]):
            return None
        sim = self.clone()
        steps: list[tuple[str, int, str]] = []
        idle_draws = 0
        while not sim.is_won():
            moved = False
            for src in ["waste", *TABLEAU]:
                cards = sim.piles[src]
                if not cards:
                    continue
                f = sim.foundation_for(cards[-1])
                if f:
                    sim.move(src, len(cards) - 1, f)
                    steps.append((src, len(cards), f))
                    moved = True
                    idle_draws = 0
                    break
            if moved:
                continue
            if not sim.draw_stock():
                return None
            steps.append(("draw", 0, ""))
            idle_draws += 1
            if idle_draws > 2 * 52 + 2:
                return None
        return steps
