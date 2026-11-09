"""Move-by-move review of a solitaire game, built from its action log.

The solver checks the position after every action (with a node budget), so
the review can point at the move after which the deal could no longer be
won, and suggest a move that would have kept it winnable. From a position
that cannot be won, no move can make it winnable again (only an undo can),
so most positions after the turning point need no search at all.
"""

from __future__ import annotations

from typing import Callable, Optional

from .game import FOUNDATIONS, TABLEAU, Klondike
from .solver import Solver

NODE_LIMIT = 80_000

PILE_NAMES = {"stock": "stock", "waste": "waste",
              **{f: "foundation" for f in FOUNDATIONS},
              **{t: f"column {i + 1}" for i, t in enumerate(TABLEAU)}}


def _fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _fingerprint(game: Klondike) -> tuple:
    return tuple((p, tuple((c.rank, c.suit, c.up) for c in cards)) for p, cards in game.piles.items()) + (
        game.passes,)


def _winnable(game: Klondike, node_limit: int) -> Optional[bool]:
    """True = a win was found, False = proven lost, None = gave up (budget)."""
    solver = Solver(game, node_limit)
    if solver.solve():
        return True
    return None if solver.nodes > node_limit else False


def describe(game: Klondike, action: str) -> str:
    """A readable line for one logged action, given the game BEFORE it."""
    if action == "d":
        if game.piles["stock"]:
            drawn = game.piles["stock"][-min(game.draw, len(game.piles["stock"])):]
            return "draw " + " ".join(c.label for c in reversed(drawn))
        return "turn the waste over (a new pass through the stock)"
    if action == "u":
        return "undo"
    src, rest = action.split(":", 1)
    index, dst = rest.split(">", 1)
    src = "waste" if src == "w" else src
    cards = game.piles[src][int(index):]
    if not cards:
        return action
    what = cards[0].label + (f" (+{len(cards) - 1})" if len(cards) > 1 else "")
    text = f"{what}: {PILE_NAMES[src]} to {PILE_NAMES[dst]}"
    below = game.piles[src][int(index) - 1] if int(index) > 0 else None
    if src in TABLEAU and below is not None and not below.up:
        text += ", turns a card over"
    return text


def _apply(game: Klondike, action: str) -> None:
    if action == "d":
        game.draw_stock()
    elif action == "u":
        game.undo()
    else:
        src, rest = action.split(":", 1)
        index, dst = rest.split(">", 1)
        game.move("waste" if src == "w" else src, int(index), dst)


def _good_alternative(game: Klondike, node_limit: int) -> Optional[str]:
    """A move from this position that keeps the deal winnable."""
    options: list[tuple[str, int, str]] = []
    for src in ["waste", *TABLEAU, *FOUNDATIONS]:
        cards = game.piles[src]
        for idx in range(len(cards)):
            if not cards[idx].up or (src not in TABLEAU and idx != len(cards) - 1):
                continue
            for dst in TABLEAU + FOUNDATIONS:
                if dst != src and game.can_move(src, idx, dst):
                    options.append((src, idx, dst))
    for src, idx, dst in options:
        trial = game.clone()
        line = describe(trial, f"{'w' if src == 'waste' else src}:{idx}>{dst}")
        trial.move(src, idx, dst)
        if _winnable(trial, node_limit):
            return line
    trial = game.clone()
    if trial.draw_stock() and _winnable(trial, node_limit):
        return describe(game, "d")
    return None


def review_lines(seed: int, draw: int, scoring: str, log: list[str], won: bool,
                 progress: Optional[Callable[[int, int], None]] = None,
                 node_limit: int = NODE_LIMIT) -> list[str]:
    """The review as rich-markup lines."""
    game = Klondike(seed=seed, draw=draw, scoring=scoring)
    actions = [(e.partition("@")[0], int(e.partition("@")[2] or 0)) for e in log]
    known: dict[tuple, Optional[bool]] = {}
    rows: list[tuple[int, str, str, Optional[bool]]] = []   # (time, action, text, winnable after)
    turning: list[int] = []
    alternatives: dict[int, Optional[str]] = {}

    start = _winnable(game, node_limit)
    known[_fingerprint(game)] = start
    state = start
    for n, (action, clock) in enumerate(actions):
        if progress:
            progress(n, len(actions))
        before = game.clone()
        text = describe(game, action)
        _apply(game, action)
        fp = _fingerprint(game)
        if fp in known:
            after = known[fp]
        elif state is not True and action != "u":
            after = state  # from a lost position every move stays lost
        else:
            after = _winnable(game, node_limit)
        known[fp] = after
        if state is True and after is not True and action != "u":
            turning.append(n)
            alternatives[n] = _good_alternative(before, node_limit)
        rows.append((clock, action, text, after))
        state = after
    if progress:
        progress(len(actions), len(actions))

    undos = sum(1 for a, _ in actions if a == "u")
    passes = game.passes
    total = actions[-1][1] if actions else 0
    lines = [
        f"[b]Deal #{seed}[/b]  draw {draw}, {scoring.replace('_', ' ')} scoring",
        f"  {'[bold #4dd0e1]Won[/]' if won else 'Not won'} in {_fmt_time(total)}, {len(actions)} actions, "
        f"{undos} undo{'s' if undos != 1 else ''}, {passes} pass{'es' if passes != 1 else ''} through the stock.",
        "",
    ]
    if start is not True:
        lines += ["  The computer found no win from the start of this deal (a random deal),",
                  "  so there is no single move to point at.", ""]
    elif won:
        lines += ["  [bold #4dd0e1]Well played.[/] The deal stayed winnable all the way.", ""]
        if passes > 1 and draw == 1:
            lines.append(f"  Each extra pass through the stock costs 100 points ({passes - 1} here).")
    elif turning:
        n = turning[-1]
        clock, _, text, _ = rows[n]
        lines += [
            f"  [bold red]The turning point: action {n + 1} at {_fmt_time(clock)}[/], {text}.",
            "  Before it the deal could still be won; after it the computer found no win.",
        ]
        alt = alternatives.get(n)
        if alt:
            lines.append(f"  A move that kept it winnable: [bold #ff9e3d]{alt}[/].")
        if len(turning) > 1:
            lines.append(f"  (You lost the winning line {len(turning)} times; undo brought it back before.)")
        lines.append("")
    else:
        lines += ["  The deal is still winnable from where you stopped.", ""]

    lines.append("[b u]Every action[/b u]")
    for n, (clock, action, text, after) in enumerate(rows):
        style = ""
        mark = "  "
        if n in turning:
            style, mark = "bold red", "> "
        elif action == "u":
            style = "dim"
        line = f"{mark}{n + 1:>3}  {_fmt_time(clock):>5}  {text}"
        lines.append(f"[{style}]{line}[/]" if style else line)
    return lines
