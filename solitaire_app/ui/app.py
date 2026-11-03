"""The Solitaire TUI, Claude-terminal style (same rules as the yahtzee app).

Design rules:
- The terminal's own colors: default background and foreground everywhere,
  with purple as the selection/focus accent and orange for hints.
- Everything is text and ASCII. No buttons, no dialogs, no popups: the menu
  is arrow-navigable text, confirmations do not exist (games auto-save), and
  help/stats are full pages you enter and leave with escape.
- Arrow keys work everywhere; the mouse works everywhere: hover highlights,
  click to pick up and drop, drag and drop, double-click to send home.
"""

from __future__ import annotations

import math
import os
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rich.text import Text
from textual import events, on, work
from textual.worker import get_current_worker
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Static

from .. import __version__
from ..config import (
    add_pooled_deal,
    bankroll,
    deal_pool_key,
    load_deal_pool,
    take_pooled_deal,
    clear_saved_game,
    load_game_snapshot,
    load_settings,
    record_game,
    save_game_snapshot,
    save_settings,
    stats_summary,
)
from ..game import (
    FOUNDATIONS,
    SCORING_INFO,
    SCORING_LABELS,
    SCORINGS,
    TABLEAU,
    Card,
    Klondike,
    Move,
)
from ..solver import find_winnable_seed
from ..update import REPO_DIR, check_and_update

ACCENT = "#875fff"   # Claude-style purple, leaning slightly blue
HINT = "#ff9e3d"     # orange: hints (never yellow)
BACK = "#5f5faf"     # card backs: a muted take on the accent
WINC = "#4dd0e1"     # cyan: wins and good news

# figlet "DOS Rebel"
LOGO = """\
                  ████   ███   █████               ███
                 ░░███  ░░░   ░░███               ░░░
  █████   ██████  ░███  ████  ███████    ██████   ████  ████████   ██████
 ███░░   ███░░███ ░███ ░░███ ░░░███░    ░░░░░███ ░░███ ░░███░░███ ███░░███
░░█████ ░███ ░███ ░███  ░███   ░███      ███████  ░███  ░███ ░░░ ░███████
 ░░░░███░███ ░███ ░███  ░███   ░███ ███ ███░░███  ░███  ░███     ░███░░░
 ██████ ░░██████  █████ █████  ░░█████ ░░████████ █████ █████    ░░██████
░░░░░░   ░░░░░░  ░░░░░ ░░░░░    ░░░░░   ░░░░░░░░ ░░░░░ ░░░░░      ░░░░░░
"""

CARD_W = 9           # compact cards, for small terminals
CARD_H = 5
BIG_W = 11           # the normal size: rank, suit and a big pip
BIG_H = 8
DOUBLE_CLICK = 0.4  # seconds
IDLE_PAUSE = 120.0  # the clock stops after this long without a key or mouse

# Win fireworks: rockets climb, burst into suit symbols, sparks fall and fade.
FX_DT = 0.05            # seconds per frame
FX_DURATION = 7.0       # seconds, unless a key or click skips it
FX_GRAVITY = 14.0       # cells per second squared
FX_COLORS = [ACCENT, "red", WINC, HINT, "bold"]
FX_SYMBOLS = "♠♥♦♣*+."


def fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


# Two colors, so red-on-black stays easy to read, and within each color
# one suit is drawn solid and the other open: hearts and spades are solid,
# diamonds and clubs are outlined.
SUIT_STYLES = {
    "H": "bold #ff5f5f",   # red
    "D": "bold #ff5f5f",   # red
    "C": "bold",           # the terminal's own text color (black or white)
    "S": "bold",
}

# Big pips for the middle of a card, drawn with half blocks (7 x 4 cells).
PIPS = {
    "H": (" ▄▄ ▄▄ ", "███████", " ▀███▀ ", "   ▀   "),
    "D": ("  ▄▀▄  ", "▄▀   ▀▄", "▀▄   ▄▀", "  ▀▄▀  "),
    "S": ("  ▄█▄  ", "▄█████▄", "██▀█▀██", "  ▄█▄  "),
    "C": (" ▄▀▀▀▄ ", "▄▄▀▄▀▄▄", "█▄ ▄ ▄█", "  ▄█▄  "),
}


def suit_style(card: Card) -> str:
    return SUIT_STYLES.get(card.suit, "bold")


# ---------------------------------------------------------------------------
# Drawing: a tiny character canvas, converted to one rich Text per frame
# ---------------------------------------------------------------------------


class Canvas:
    def __init__(self, width: int, height: int) -> None:
        self.w = max(1, width)
        self.h = max(1, height)
        self.chars = [[" "] * self.w for _ in range(self.h)]
        self.styles = [[""] * self.w for _ in range(self.h)]

    def put(self, x: int, y: int, text: str, style: str = "") -> None:
        if not 0 <= y < self.h:
            return
        row_c, row_s = self.chars[y], self.styles[y]
        for i, ch in enumerate(text):
            xx = x + i
            if 0 <= xx < self.w:
                row_c[xx] = ch
                row_s[xx] = style

    def to_text(self) -> Text:
        out = Text(no_wrap=True, overflow="crop")
        for y in range(self.h):
            buf, run = "", None
            for ch, st in zip(self.chars[y], self.styles[y]):
                if st != run:
                    if buf:
                        out.append(buf, style=run or "")
                    buf, run = "", st
                buf += ch
            if buf:
                out.append(buf.rstrip() if not run else buf, style=run or "")
            if y < self.h - 1:
                out.append("\n")
        return out


def draw_card(cv: Canvas, x: int, y: int, card: Card, border: str = "",
              selected: bool = False, compact_label: bool = False,
              w: int = BIG_W, h: int = BIG_H, pip: bool = True) -> None:
    """A full card at (x, y). Cards drawn later overlap earlier ones, which
    is how the tableau fans: only the top rows of covered cards stay visible."""
    inner = w - 2
    if not card.up:
        style = border or BACK
        cv.put(x, y, "+" + "-" * inner + "+", style)
        for r in range(1, h - 1):
            cv.put(x, y + r, "|", style)
            cv.put(x + 1, y + r, "░" * inner, BACK)
            cv.put(x + w - 1, y + r, "|", style)
        cv.put(x, y + h - 1, "+" + "-" * inner + "+", style)
        return
    edge = "+" + ("=" if selected else "-") * inner + "+"
    label = card.label
    lab = suit_style(card)
    cv.put(x, y, edge, border)
    if compact_label:
        # Squeezed fan: the rank rides in the top edge so it stays readable.
        cv.put(x + 1, y, label, lab)
    for r in range(1, h - 1):
        cv.put(x, y + r, "|", border)
        cv.put(x + 1, y + r, " " * inner, "")
        cv.put(x + w - 1, y + r, "|", border)
    cv.put(x + 1, y + 1, label, lab)
    cv.put(x + w - 1 - len(label), y + h - 2, label, lab)
    art = PIPS.get(card.suit) if pip else None
    if h >= 8 and art:
        for r, line in enumerate(art):
            for i, ch in enumerate(line):
                if ch != " ":
                    cv.put(x + 1 + (inner - 7) // 2 + i, y + 2 + r, ch, lab)
    elif pip:
        cv.put(x + 1 + inner // 2, y + h // 2, label[-1], lab)
    cv.put(x, y + h - 1, edge, border)


def draw_slot(cv: Canvas, x: int, y: int, mark: str, style: str = "",
              w: int = BIG_W, h: int = BIG_H) -> None:
    style = style or "dim"
    inner = w - 2
    cv.put(x, y, "." + "-" * inner + ".", style)
    for r in range(1, h - 1):
        text = f"{mark:^{inner}}" if r == h // 2 else " " * inner
        cv.put(x, y + r, ":" + text + ":", style)
    cv.put(x, y + h - 1, "'" + "-" * inner + "'", style)


# ---------------------------------------------------------------------------
# Shared text widgets (same look and feel as the yahtzee app)
# ---------------------------------------------------------------------------


@dataclass
class MenuItem:
    id: str
    label: str
    kind: str = "action"                      # "action" | "choice"
    choices: list = field(default_factory=list)
    index: int = 0
    visible: bool = True

    @property
    def value(self):
        return self.choices[self.index][1] if self.choices else None

    @property
    def value_label(self) -> str:
        return self.choices[self.index][0] if self.choices else ""


class AsciiMenu(Widget, can_focus=True):
    """A Claude-terminal style menu: text lines, arrows, hover, click."""

    BINDINGS = [
        Binding("up", "move(-1)", "Up", show=False),
        Binding("down", "move(1)", "Down", show=False),
        Binding("left", "adjust(-1)", "Left", show=False),
        Binding("right", "adjust(1)", "Right", show=False),
        Binding("space,enter", "activate", "Select", show=False),
    ]

    selected: reactive[int] = reactive(0)
    hovered: reactive[Optional[int]] = reactive(None)

    class Activated(Message):
        def __init__(self, item_id: str) -> None:
            self.item_id = item_id
            super().__init__()

    class Changed(Message):
        def __init__(self, item_id: str, value) -> None:
            self.item_id = item_id
            self.value = value
            super().__init__()

    class Highlighted(Message):
        def __init__(self, item_id: str) -> None:
            self.item_id = item_id
            super().__init__()

    def __init__(self, items: list[MenuItem], id: Optional[str] = None) -> None:
        super().__init__(id=id)
        self.items = items

    def visible_items(self) -> list[MenuItem]:
        return [i for i in self.items if i.visible]

    def get_content_width(self, container, viewport) -> int:
        width = 10
        for item in self.items:
            if item.kind == "choice":
                widest = max((len(lbl) for lbl, _ in item.choices), default=0)
                width = max(width, 2 + 13 + 2 + widest + 2)
            else:
                width = max(width, 2 + len(item.label))
        return width + 1

    def get_content_height(self, container, viewport, width) -> int:
        return len(self.visible_items())

    def item(self, item_id: str) -> MenuItem:
        return next(i for i in self.items if i.id == item_id)

    def set_visible(self, item_id: str, visible: bool) -> None:
        self.item(item_id).visible = visible
        vis = self.visible_items()
        self.selected = max(0, min(self.selected, len(vis) - 1))
        self.refresh(layout=True)

    def current_item(self) -> Optional[MenuItem]:
        vis = self.visible_items()
        return vis[self.selected] if vis else None

    def render(self) -> Text:
        out = Text(no_wrap=True)
        for idx, item in enumerate(self.visible_items()):
            selected = idx == self.selected and self.has_focus
            hovered = idx == self.hovered
            style = f"bold {ACCENT}" if selected else ("bold" if hovered else "")
            out.append("> " if selected else "  ", style=f"bold {ACCENT}" if selected else "")
            if item.kind == "choice":
                out.append(f"{item.label:<15}", style=style)
                out.append("< ", style="dim")
                out.append(item.value_label, style=style if selected or hovered else "")
                out.append(" >", style="dim")
            else:
                out.append(item.label, style=style)
            out.append("\n")
        return out

    def action_move(self, step: int) -> None:
        vis = self.visible_items()
        if vis:
            self.selected = (self.selected + step) % len(vis)

    def action_adjust(self, step: int) -> None:
        item = self.current_item()
        if item and item.kind == "choice" and item.choices:
            item.index = (item.index + step) % len(item.choices)
            self.refresh()
            self.post_message(self.Changed(item.id, item.value))

    def action_activate(self) -> None:
        item = self.current_item()
        if item is None:
            return
        if item.kind == "choice":
            self.action_adjust(1)
        else:
            self.post_message(self.Activated(item.id))

    def on_click(self, event: events.Click) -> None:
        vis = self.visible_items()
        if 0 <= event.y < len(vis):
            self.focus()
            self.selected = event.y
            self.action_activate()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        vis = self.visible_items()
        self.hovered = event.y if 0 <= event.y < len(vis) else None

    def on_leave(self) -> None:
        self.hovered = None

    def watch_selected(self, _) -> None:
        self.refresh()
        item = self.current_item()
        if item:
            self.post_message(self.Highlighted(item.id))

    def watch_hovered(self, _) -> None:
        self.refresh()

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()


class ActionBar(Static):
    """Footer key hints; the ones bound to an action are clickable."""

    hovered_idx: reactive[Optional[int]] = reactive(None)

    class Invoked(Message):
        def __init__(self, action: str) -> None:
            self.action = action
            super().__init__()

    def __init__(self, segments: list, id: Optional[str] = None) -> None:
        super().__init__(id=id)
        self.segments = segments
        self._ranges: list[tuple[int, int, int]] = []

    def set_segments(self, segments: list) -> None:
        self.segments = segments
        self.hovered_idx = None
        self.refresh()

    def render(self) -> Text:
        out = Text(" ", no_wrap=True)
        self._ranges = []
        for idx, (label, action) in enumerate(self.segments):
            start = len(out.plain)
            if action is None:
                out.append(label, style="dim")
            elif idx == self.hovered_idx:
                out.append(label, style=f"bold underline {ACCENT}")
            else:
                out.append(label)
            self._ranges.append((start, len(out.plain), idx))
            out.append("   ")
        return out

    def _idx_at(self, x: int) -> Optional[int]:
        for start, end, idx in self._ranges:
            if start <= x < end and self.segments[idx][1] is not None:
                return idx
        return None

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self.hovered_idx = self._idx_at(event.x)

    def on_leave(self) -> None:
        self.hovered_idx = None

    def on_click(self, event: events.Click) -> None:
        idx = self._idx_at(event.x)
        if idx is not None:
            self.post_message(self.Invoked(self.segments[idx][1]))

    def watch_hovered_idx(self, _) -> None:
        self.refresh()


class TextPage(Screen):
    """A scrollable full-screen text page. Escape (or q) goes back."""

    BINDINGS = [Binding("escape,q,backspace", "go_back", "Back")]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self.title_text = title
        self.body = body

    def compose(self) -> ComposeResult:
        yield Static(f" {self.title_text}", id="page-title", markup=True)
        with VerticalScroll(id="page-scroll"):
            yield Static(self.body, id="page-body", markup=True)
        yield ActionBar([("up/down scroll", None), ("esc back", "back")], id="page-footer")

    def on_mount(self) -> None:
        self.query_one("#page-scroll", VerticalScroll).focus()

    def on_action_bar_invoked(self, event: ActionBar.Invoked) -> None:
        if event.action == "back":
            self.app.pop_screen()

    def action_go_back(self) -> None:
        self.app.pop_screen()


HELP_TEXT = f"""[b]SOLITAIRE v{__version__}[/b]  [dim]Klondike[/dim]

[b u]Goal[/b u]
  Build all four suits on the foundations (top right), from ace to king.
  On the tableau you stack downwards, alternating a red suit and a black
  suit. Within each color one suit is solid and one is open: hearts and
  spades are solid, diamonds and clubs are outlined. Only a king (or a run that starts with one) goes into an
  empty column. Click the stock (top left) to turn over cards.

[b u]Mouse[/b u]
  [b]click[/b]          pick up a card (and everything on top of it), click again
                 where it should go
  [b]drag[/b]           drag a card or run onto another pile
  [b]double-click[/b]   send a card to the best spot (foundation first)
  [b]click stock[/b]    turn over cards; on an empty stock: start the next pass

[b u]Keys[/b u]
  [b]arrows[/b]         move the cursor; up/down in a column chooses how many
                 cards you pick up
  [b]enter / space[/b]  pick up, or drop what you hold on the cursor pile
  [b]1 to 7[/b]         jump to a column (drops there when you hold cards)
  [b]w[/b]              jump to the waste
  [b]d[/b]              draw from the stock
  [b]a[/b]              auto-move the card under the cursor (like double-click)
  [b]f[/b]              send the card under the cursor to its foundation
  [b]c[/b]              finish a sure win for you
  [b]u / ctrl+z[/b]     undo (as often as you like)
  [b]h[/b]              hint; press again for the next one
  [b]n[/b]              new deal        [b]r[/b]  restart this deal
  [b]esc[/b]            put the cards down, or back to the menu (auto-saved)
  [b]?[/b]              this page       [b]ctrl+q[/b]  quit       [b]F2[/b]  screenshot (SVG)

[b u]Options[/b u] [dim](main menu)[/dim]
  [b]Draw[/b]           1 card per turn (easier) or 3 (classic, harder).
  [b]Scoring[/b]        Standard: +10 to a foundation, +5 waste to tableau,
                 +5 per card turned over, -15 back from a foundation,
                 -2 per 10 seconds, a time bonus when you win.
                 Vegas: pay $52 per deal, win $5 per foundation card;
                 one pass through the stock with draw 1, three with draw 3.
                 Vegas (cumulative) keeps your bankroll between deals.
  [b]Deals[/b]          "winnable only" (default): a solver plays every deal through
                 before you get it, so each one can be won. "random" deals
                 anything, the way real cards fall.
  [b]Win animation[/b]  fireworks when you win; any key or click skips them.

[b u]Sure wins[/b u]
  Once every card is face up and the win is certain, the game asks whether
  it should finish for you: [b]enter[/b] yes, [b]esc[/b] keep playing. Changed your
  mind later? [b]c[/b] finishes it.

[b u]Deals[/b u]
  Every deal has a number (top left). `solitaire --deal 123456` plays that
  exact deal again, handy to challenge a friend.

[b u]Statistics[/b u]
  A deal counts once you have made a move: as a win, or as a loss when you
  start another deal instead. Quitting halfway is fine: the game is saved
  and "Continue" picks it up again.
  Every move of every deal is kept in ~/.config/solitaire/games.jsonl, so
  a game can be replayed and reviewed afterwards (about 1.5 KB a deal).

[b u]Clock[/b u]
  The clock only runs while you play: it pauses when the terminal is in the
  background, and after two minutes without a key or a click.

[b u]Updates[/b u]
  Like Claude Code, the game updates itself on start when a new version
  is out.
"""


def changelog_text() -> str:
    path = Path(REPO_DIR) / "CHANGELOG.md"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return "No changelog found."
    out = []
    for line in lines:
        if line.startswith("# "):
            continue
        out.append(f"[b]{line[3:]}[/b]" if line.startswith("## ") else line)
    return "\n".join(out).strip()


def _changelog_summary(version: str, max_lines: int = 6) -> Optional[str]:
    path = Path(REPO_DIR) / "CHANGELOG.md"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    out: list[str] = []
    in_section = False
    for line in lines:
        if line.startswith("## "):
            if in_section:
                break
            in_section = version in line
            continue
        if in_section and line.strip():
            out.append(line.strip().lstrip("- "))
            if len(out) >= max_lines:
                break
    return "\n".join(out) if out else None


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------

TOP_ROW = ["stock", "waste", None, *FOUNDATIONS]
POOL_SIZE = 3  # solved deals kept ready per draw/scoring mode


class Board(Widget, can_focus=True):
    """Draws the whole table and turns mouse input into pile/card hits."""

    class Clicked(Message):
        def __init__(self, pile: str, index: int, double: bool) -> None:
            self.pile = pile
            self.index = index
            self.double = double
            super().__init__()

    class SkipFx(Message):
        """A click during the win fireworks: skip them."""

    class Dropped(Message):
        def __init__(self, src: str, index: int, dst: str) -> None:
            self.src = src
            self.index = index
            self.dst = dst
            super().__init__()

    def __init__(self, game: Klondike, id: Optional[str] = None) -> None:
        super().__init__(id=id)
        self.game = game
        self.cursor: tuple[str, int] = ("stock", 0)
        self.selection: Optional[tuple[str, int]] = None
        self.hint: Optional[Move] = None
        self.hover: Optional[tuple[str, int]] = None
        self.drag_target: Optional[str] = None
        self.gap = 2
        self.cw, self.ch = BIG_W, BIG_H
        self._rects: list[tuple[int, int, int, int, str, int]] = []
        self._press: Optional[tuple[str, int]] = None
        self._dragging = False
        self._last_click: tuple[float, str, int] = (0.0, "", -1)
        self.banner: Optional[tuple[str, str]] = None
        self.particles: list[list] = []  # [x, y, vx, vy, char, style, life, rocket]
        self._fx_time = 0.0
        self._fx_next_rocket = 0.0
        self._fx_rng = random.Random()

    # -- win fireworks ---------------------------------------------------
    def start_fireworks(self, detail: str) -> None:
        self.banner = ("Y O U   W O N !", detail)
        self.particles = []
        self._fx_time = 0.0
        self._fx_next_rocket = 0.0
        self.refresh()

    def stop_fireworks(self) -> None:
        self.banner = None
        self.particles = []
        self.refresh()

    def step_fireworks(self) -> bool:
        """Advance one frame; False when the show is over."""
        rng = self._fx_rng
        w, h = max(self.size.width, 20), max(self.size.height, 10)
        self._fx_time += FX_DT
        if self._fx_time < FX_DURATION - 1.5 and self._fx_time >= self._fx_next_rocket:
            self._fx_next_rocket = self._fx_time + rng.uniform(0.15, 0.45)
            climb = rng.uniform(0.35, 0.8) * h
            vy = -(2 * FX_GRAVITY * climb) ** 0.5
            self.particles.append([rng.uniform(4, w - 4), h - 1, rng.uniform(-3, 3), vy,
                                   "|", "bold", 9.0, True])
        alive = []
        for p in self.particles:
            p[0] += p[2] * FX_DT
            p[1] += p[3] * FX_DT
            p[3] += FX_GRAVITY * FX_DT
            p[6] -= FX_DT
            if p[7] and p[3] >= -1.0:
                # Apex: burst into a ring of suit symbols in one color.
                color = rng.choice(FX_COLORS)
                for i in range(rng.randint(14, 22)):
                    angle = rng.uniform(0, 6.2832)
                    speed = rng.uniform(5, 12)
                    alive.append([p[0], p[1], math.cos(angle) * speed * 2, math.sin(angle) * speed - 3,
                                  rng.choice(FX_SYMBOLS), color, rng.uniform(1.0, 2.0), False])
                continue
            if p[6] > 0 and -2 < p[0] < w + 2 and p[1] < h + 1:
                alive.append(p)
        self.particles = alive
        self.refresh()
        return self._fx_time < FX_DURATION or bool(self.particles) and self._fx_time < FX_DURATION + 2

    def _draw_fireworks(self, cv: "Canvas") -> None:
        for x, y, _vx, _vy, ch, style, life, rocket in self.particles:
            if rocket:
                cv.put(int(x), int(y), ch, style)
                cv.put(int(x), int(y) + 1, "'", "dim")
            else:
                cv.put(int(x), int(y), ch, style if life > 0.45 else f"dim {style}")
        if self.banner:
            title, detail = self.banner
            inner = max(len(title), len(detail)) + 8
            x = max(0, (cv.w - inner - 2) // 2)
            y = max(0, (cv.h - 6) // 2)
            edge = "+" + "-" * inner + "+"
            cv.put(x, y, edge, f"bold {WINC}")
            for r, (text, style) in enumerate([("", ""), (title, f"bold {WINC}"),
                                                (detail, "bold"), ("", "")], start=1):
                cv.put(x, y + r, "|", f"bold {WINC}")
                cv.put(x + 1, y + r, f"{text:^{inner}}", style)
                cv.put(x + inner + 1, y + r, "|", f"bold {WINC}")
            cv.put(x, y + 5, edge, f"bold {WINC}")

    # -- geometry --------------------------------------------------------
    def pick_size(self) -> None:
        """Big cards when the terminal has room, compact ones otherwise. The
        tallest column (fanned one row per card) must fit below the top row."""
        tallest = max(len(self.game.piles[t]) for t in TABLEAU)
        need = BIG_H + 1 + max(tallest - 1 + BIG_H, 6 + BIG_H)
        big = self.size.width >= 7 * BIG_W + 6 and self.size.height >= need
        self.cw, self.ch = (BIG_W, BIG_H) if big else (CARD_W, CARD_H)
        self.gap = 2 if self.size.width >= 7 * self.cw + 12 else 1

    def board_width(self) -> int:
        return 7 * self.cw + 6 * self.gap

    def col_x(self, col: int) -> int:
        offset = max(0, (self.size.width - self.board_width()) // 2)
        return offset + col * (self.cw + self.gap)

    def tableau_layout(self, pile: str) -> list[int]:
        """Row offsets for every card of a tableau column; face-up cards
        fan two rows apart, squeezed to one when the column gets tall."""
        cards = self.game.piles[pile]
        avail = max(self.ch, self.size.height - (self.ch + 1))
        for up_step in (2, 1):
            ys, y = [], 0
            for i, card in enumerate(cards):
                ys.append(y)
                y += up_step if card.up else 1
            height = (ys[-1] + self.ch) if ys else self.ch
            if height <= avail or up_step == 1:
                return ys
        return []

    # -- highlight rules -------------------------------------------------
    def _style_for(self, pile: str, index: int, top: bool) -> tuple[str, bool]:
        """(border style, selected) for a card or an empty slot (index -1)."""
        sel = self.selection
        if sel and sel[0] == pile and index >= sel[1] >= 0:
            return f"bold {ACCENT}", True
        h = self.hint
        if h is not None:
            if h.src == pile and (index >= h.index or (pile == "stock" and top)):
                return f"bold {HINT}", False
            if h.dst == pile and top:
                return f"bold {HINT}", False
        if self.has_focus and self.cursor[0] == pile:
            if pile in TABLEAU:
                if index >= self.cursor[1] or (index == -1):
                    return f"bold {ACCENT}", False
            elif top:
                return f"bold {ACCENT}", False
        if self.drag_target == pile and top:
            return f"bold {ACCENT}", False
        hv = self.hover
        if hv and hv[0] == pile:
            if pile in TABLEAU and hv[1] >= 0 and index >= hv[1]:
                return "bold", False
            if pile not in TABLEAU and top:
                return "bold", False
        return "", False

    # -- rendering -------------------------------------------------------
    def render(self) -> Text:
        g = self.game
        self.pick_size()
        cw, ch = self.cw, self.ch
        cv = Canvas(self.size.width, self.size.height)
        rects: list[tuple[int, int, int, int, str, int]] = []

        # Top row: stock, waste, foundations.
        for col, pile in enumerate(TOP_ROW):
            if pile is None:
                continue
            x, y = self.col_x(col), 0
            cards = g.piles[pile]
            if pile == "waste" and g.draw == 3 and len(cards) > 1:
                fan = cards[-3:]
                for i, card in enumerate(fan):
                    idx = len(cards) - len(fan) + i
                    top = idx == len(cards) - 1
                    border, selected = self._style_for(pile, idx, top) if top else ("", False)
                    draw_card(cv, x + 4 * i, y, card, border, selected, w=cw, h=ch, pip=top)
                rects.append((x, y, x + 4 * (len(fan) - 1) + cw, y + ch, pile, len(cards) - 1))
                continue
            if cards:
                card = cards[-1]
                border, selected = self._style_for(pile, len(cards) - 1, True)
                draw_card(cv, x, y, card, border, selected, w=cw, h=ch)
                if pile == "stock" and len(cards) > 1:
                    cv.put(x + 1, y + ch, f"{len(cards):^{cw - 2}}", "dim")
            else:
                border, _ = self._style_for(pile, -1, True)
                if pile == "stock":
                    mark = "↻" if g.can_recycle() else "x"
                elif pile == "waste":
                    mark = ""
                else:
                    mark = "A"
                draw_slot(cv, x, y, mark, border, w=cw, h=ch)
            rects.append((x, y, x + cw, y + ch, pile, len(cards) - 1))

        # Tableau.
        base_y = ch + 1
        for col, pile in enumerate(TABLEAU):
            x = self.col_x(col)
            cards = g.piles[pile]
            # Backdrop: the whole column is a drop target.
            rects.append((x, base_y, x + cw, self.size.height, pile, -1))
            if not cards:
                border, _ = self._style_for(pile, -1, True)
                draw_slot(cv, x, base_y, "K", border, w=cw, h=ch)
                continue
            ys = self.tableau_layout(pile)
            squeezed = any(b - a == 1 and cards[i].up for i, (a, b) in enumerate(zip(ys, ys[1:])))
            for i, card in enumerate(cards):
                top = i == len(cards) - 1
                border, selected = self._style_for(pile, i, top)
                draw_card(cv, x, base_y + ys[i], card, border, selected,
                          compact_label=squeezed and not top, w=cw, h=ch)
                bottom = base_y + (ys[i + 1] if not top else ys[i] + ch)
                rects.append((x, base_y + ys[i], x + cw, bottom, pile, i))
        self._rects = rects
        if self.banner or self.particles:
            self._draw_fireworks(cv)
        return cv.to_text()

    # -- mouse -----------------------------------------------------------
    def hit(self, x: int, y: int) -> Optional[tuple[str, int]]:
        for x0, y0, x1, y1, pile, idx in reversed(self._rects):
            if x0 <= x < x1 and y0 <= y < y1:
                if pile in TABLEAU and idx == -1 and self.game.piles[pile]:
                    # Below the last card of a column: means the column itself.
                    return pile, -1
                return pile, idx
        return None

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.focus()
        if self.banner is not None:
            self._press = None
            self.post_message(self.SkipFx())
            return
        self._press = self.hit(event.x, event.y)
        self._dragging = False
        self.capture_mouse()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        target = self.hit(event.x, event.y)
        if event.button and self._press is not None:
            src = self._press
            if target and target[0] != src[0] and self.game.movable_from(src[0], src[1]):
                self._dragging = True
                self.drag_target = target[0] if self.game.can_move(src[0], src[1], target[0]) else None
                if self.selection != src:
                    self.selection = src
            else:
                self.drag_target = None
            self.refresh()
            return
        if target != self.hover:
            self.hover = target
            self.refresh()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        press, self._press = self._press, None
        target = self.hit(event.x, event.y)
        dragging, self._dragging = self._dragging, False
        self.drag_target = None
        # Pressed on one pile, released on another: a drag, even when the
        # terminal sent no motion events in between.
        if (press and target and target[0] != press[0]
                and self.game.movable_from(press[0], press[1])):
            self.post_message(self.Dropped(press[0], press[1], target[0]))
            return
        if dragging:
            self.selection = None
            self.refresh()
            return
        if target is None:
            self.post_message(self.Clicked("", -1, False))
            return
        now = time.monotonic()
        last_t, last_p, last_i = self._last_click
        double = now - last_t < DOUBLE_CLICK and last_p == target[0] and last_i == target[1]
        self._last_click = (0.0, "", -1) if double else (now, target[0], target[1])
        self.post_message(self.Clicked(target[0], target[1], double))

    def on_leave(self) -> None:
        if self.hover is not None:
            self.hover = None
            self.refresh()

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()

    def on_resize(self) -> None:
        self.refresh()


# ---------------------------------------------------------------------------
# The game screen
# ---------------------------------------------------------------------------

FOOTER = [
    ("arrows move", None),
    ("enter pick/drop", None),
    ("d draw", "draw"),
    ("u undo", "undo"),
    ("h hint", "hint"),
    ("n new deal", "new"),
    ("? help", "help"),
    ("esc menu", "menu"),
]
FOOTER_FINISH = [
    ("enter finish it", "finish"),
    ("esc keep playing", "decline"),
    ("u undo", "undo"),
    ("? help", "help"),
]
FOOTER_WON = [
    ("n new deal", "new"),
    ("r replay deal", "restart"),
    ("m menu", "menu"),
    ("ctrl+q quit", "quit"),
]


class GameScreen(Screen):
    BINDINGS = [
        Binding("left", "cursor(-1, 0)", show=False),
        Binding("right", "cursor(1, 0)", show=False),
        Binding("up", "cursor(0, -1)", show=False),
        Binding("down", "cursor(0, 1)", show=False),
        Binding("enter,space", "select", show=False),
        Binding("w", "goto_waste", show=False),
        Binding("d", "draw", show=False),
        Binding("a", "auto", show=False),
        Binding("f", "foundation", show=False),
        Binding("c", "finish", show=False),
        Binding("u,ctrl+z", "undo", show=False),
        Binding("h", "hint", show=False),
        Binding("n", "new_deal", show=False),
        Binding("r", "restart", show=False),
        Binding("m", "menu", show=False),
        Binding("escape", "escape", show=False),
        Binding("question_mark,f1", "help", show=False),
        Binding("ctrl+q", "quit_app", show=False),
    ]

    def __init__(self, game: Klondike) -> None:
        super().__init__()
        self.game = game
        self.recorded = False
        self._offer = False      # the "finish it for you?" question is showing
        self._declined = False   # ... and the player said no (asked once)
        self._fx_timer = None
        self._hint_list: list[Move] = []
        self._hint_idx = 0
        self._finishing = False
        self._note_text = ""

    def compose(self) -> ComposeResult:
        yield Static("", id="statusbar")
        yield Board(self.game, id="board")
        yield Static("", id="note", markup=True)
        yield ActionBar(FOOTER, id="footer-keys")

    @property
    def board(self) -> Board:
        return self.query_one(Board)

    def on_mount(self) -> None:
        self.board.focus()
        self.set_interval(1.0, self._tick)
        g = self.game
        if g.started:
            self.note(f"Welcome back. Deal #{g.seed}, {fmt_time(g.elapsed)} on the clock.")
        else:
            extra = ""
            if g.scoring == "vegas_cumulative":
                extra = f"  Bankroll ${g.bankroll_start}, this deal costs $52."
            elif g.scoring == "vegas":
                extra = "  This deal costs $52; every foundation card pays $5."
            proof = ", a winnable one" if g.winnable else ""
            self.note(f"Deal #{g.seed}{proof}. Draw {g.draw}, {SCORING_LABELS[g.scoring].lower()} scoring.{extra}")
        self._refresh_all()
        self._after_action(save=False)

    # -- status ----------------------------------------------------------
    def note(self, text: str) -> None:
        self._note_text = text
        try:
            self.query_one("#note", Static).update(f" {text}")
        except Exception:  # noqa: BLE001 - screen tearing down
            pass

    def _status_text(self) -> Text:
        g = self.game
        left = Text(" ")
        left.append("SOLITAIRE", style=f"bold {ACCENT}")
        left.append(f"  Deal #{g.seed}  Draw {g.draw}", style="dim")
        if g.max_passes:
            left.append(f"  Pass {min(g.passes, g.max_passes)}/{g.max_passes}", style="dim")
        right = Text()
        if g.scoring == "standard":
            right.append("Score ", style="dim")
            right.append(f"{g.score}", style="bold")
        elif g.vegas:
            amount = g.bankroll if g.scoring == "vegas_cumulative" else g.score
            right.append("Bankroll " if g.scoring == "vegas_cumulative" else "Vegas ", style="dim")
            right.append(f"{'-' if amount < 0 else ''}${abs(amount)}",
                         style="bold red" if amount < 0 else f"bold {WINC}")
        right.append("   Moves ", style="dim")
        right.append(f"{g.moves}", style="bold")
        if load_settings().get("timer", True):
            right.append("   ")
            if g.started and not g.is_won() and not self.app.clock_running():
                right.append("paused ", style="dim")
            right.append(fmt_time(g.elapsed), style="bold")
        right.append(" ")
        width = self.size.width or 80
        pad = max(1, width - left.cell_len - right.cell_len)
        return left + Text(" " * pad) + right

    def _refresh_all(self) -> None:
        try:
            self.query_one("#statusbar", Static).update(self._status_text())
            self.board.refresh()
        except Exception:  # noqa: BLE001
            pass

    def on_resize(self) -> None:
        self._refresh_all()

    def _tick(self) -> None:
        g = self.game
        if not g.started or g.is_won() or self.app.screen is not self:
            return
        if not self.app.clock_running():
            # Terminal in the background, or nobody at the keyboard.
            self.query_one("#statusbar", Static).update(self._status_text())
            return
        g.elapsed += 1
        self.query_one("#statusbar", Static).update(self._status_text())
        if int(g.elapsed) % 15 == 0:
            self._save()

    # -- bookkeeping after every action ----------------------------------
    def _save(self) -> None:
        if self.game.is_won():
            clear_saved_game()
            return
        save_game_snapshot({
            "version": __version__,
            "game": self.game.to_dict(),
        })

    def _after_action(self, save: bool = True) -> None:
        board = self.board
        board.hint = None
        self._hint_list = []
        g = self.game
        # Keep the cursor on something that exists.
        pile, idx = board.cursor
        cards = g.piles[pile]
        if pile in TABLEAU:
            first_up = next((i for i, c in enumerate(cards) if c.up), len(cards) - 1)
            board.cursor = (pile, max(0, min(max(idx, first_up), len(cards) - 1)))
        else:
            board.cursor = (pile, max(0, len(cards) - 1))
        if save:
            self._save()
        self._refresh_all()
        if g.is_won():
            self._won()
            return
        if self._finishing:
            return
        plan = g.autocomplete_plan()
        if plan is not None:
            if not self._declined:
                self._ask_finish()
            return
        if self._offer:  # undone back to before the sure win
            self._close_offer()
        if g.started and not g.has_moves():
            self.note("[red]No more moves.[/] [b]u[/b] undo, [b]r[/b] restart this deal, [b]n[/b] new deal.")

    def _won(self) -> None:
        g = self.game
        self.board.selection = None
        if not self.recorded:
            self.recorded = True
            record_game(self._stats_entry(won=True),
                        bankroll_after=g.bankroll if g.scoring == "vegas_cumulative" else None,
                        log=g.log)
            clear_saved_game()
        parts = [f"{fmt_time(g.elapsed)}", f"{g.moves} moves"]
        if g.scoring == "standard":
            parts.append(f"score {g.score}")
        elif g.vegas:
            parts.append(f"${g.score} this deal")
        self.note(f"[bold {WINC}]You won![/] {', '.join(parts)}.")
        self.query_one("#footer-keys", ActionBar).set_segments(FOOTER_WON)
        self._refresh_all()
        if load_settings().get("win_animation", True) and self._fx_timer is None:
            self.board.start_fireworks("   ".join(parts))
            self._fx_timer = self.set_interval(FX_DT, self._fx_step)

    def _fx_step(self) -> None:
        if not self.board.step_fireworks():
            self.stop_fireworks()

    def stop_fireworks(self) -> None:
        if self._fx_timer is not None:
            self._fx_timer.stop()
        self._fx_timer = None
        self.board.stop_fireworks()

    def _stats_entry(self, won: bool) -> dict:
        g = self.game
        return {
            "won": won,
            "draw": g.draw,
            "scoring": g.scoring,
            "score": g.score,
            "moves": g.moves,
            "time": int(g.elapsed),
            "seed": g.seed,
        }

    def abandon(self) -> None:
        """Count the current deal as a loss (when it was started, not won)."""
        g = self.game
        if self.recorded or not g.started or g.is_won():
            return
        self.recorded = True
        record_game(self._stats_entry(won=False),
                    bankroll_after=g.bankroll if g.scoring == "vegas_cumulative" else None,
                    log=g.log)
        clear_saved_game()

    # -- finishing a sure win ---------------------------------------------
    def _ask_finish(self) -> None:
        """Inline question, no dialog: finish it for you, or keep playing."""
        self._offer = True
        self.board.selection = None
        self.note(f"[bold {WINC}]Every card is face up: this is a sure win.[/] "
                  f"Finish it for you? [b]enter[/b] yes, [b]esc[/b] keep playing.")
        self.query_one("#footer-keys", ActionBar).set_segments(FOOTER_FINISH)

    def _close_offer(self) -> None:
        self._offer = False
        self.query_one("#footer-keys", ActionBar).set_segments(FOOTER)

    def action_decline(self) -> None:
        if not self._offer:
            return
        self._declined = True
        self._close_offer()
        self.note("Keep going. Press [b]c[/b] when you want it finished after all.")

    def _start_finish(self) -> None:
        if self._finishing:
            return
        if self._offer:
            self._close_offer()
        self._finishing = True
        self.board.selection = None
        self.note(f"[{WINC}]Sure win, finishing...[/]")
        self.set_timer(0.05, self._finish_step)

    def _finish_step(self) -> None:
        g = self.game
        if g.is_won() or not self.is_mounted:
            self._finishing = False
            if g.is_won():
                self._after_action()
            return
        moved = False
        for src in ["waste", *TABLEAU]:
            cards = g.piles[src]
            if cards and g.foundation_for(cards[-1]):
                g.move(src, len(cards) - 1, g.foundation_for(cards[-1]))
                moved = True
                break
        if not moved and not g.draw_stock():
            self._finishing = False
            self._after_action()
            return
        self._refresh_all()
        self.set_timer(0.06, self._finish_step)

    # -- actions ---------------------------------------------------------
    def _busy(self) -> bool:
        return self._finishing or bool(getattr(self.app, "_searching", False))

    def _try_move(self, src: str, index: int, dst: str) -> bool:
        g = self.game
        if g.move(src, index, dst):
            self.board.selection = None
            self.board.cursor = (dst, len(g.piles[dst]) - 1)
            if self._note_text and not g.is_won():
                self.note("")
            self._after_action()
            return True
        return False

    def _pick(self, pile: str, index: int) -> bool:
        if self.game.movable_from(pile, index):
            self.board.selection = (pile, index)
            self.board.refresh()
            return True
        return False

    def _draw(self) -> None:
        g = self.game
        self.board.selection = None
        if g.draw_stock():
            self._after_action()
        elif g.piles["waste"] and g.max_passes:
            self.note(f"No passes left ({g.max_passes} with Vegas draw {g.draw}).")
        else:
            self.note("The stock is empty.")

    def _activate(self, pile: str, index: int) -> None:
        """Enter or a click on (pile, index): pick up, drop, or draw."""
        board = self.board
        g = self.game
        if pile == "stock":
            self._draw()
            return
        sel = board.selection
        if sel:
            if sel[0] == pile:
                if index >= 0 and index != sel[1] and self._pick(pile, index):
                    return
                board.selection = None
                board.refresh()
                return
            if self._try_move(sel[0], sel[1], pile):
                return
            if index >= 0 and self._pick(pile, index):
                return
            board.selection = None
            board.refresh()
            self.note("That does not go there.")
            return
        if pile in TABLEAU and index >= 0 and not self._pick(pile, index):
            # Clicked a covered face-down card or similar: pick the run's top part.
            cards = g.piles[pile]
            first_up = next((i for i, c in enumerate(cards) if c.up), None)
            if first_up is not None and index < first_up:
                self._pick(pile, first_up)
            return
        if index >= 0:
            self._pick(pile, index)

    def _auto(self, pile: str, index: int) -> None:
        g = self.game
        cards = g.piles.get(pile, [])
        if index < 0:
            index = len(cards) - 1
        if index < 0:
            return
        before = self.board.selection
        self.board.selection = None
        dst = g.best_target(pile, index)
        if dst and self._try_move(pile, index, dst):
            return
        self.board.selection = before
        self.note("No place for that card right now.")
        self.board.refresh()

    @on(Board.Clicked)
    def _clicked(self, event: Board.Clicked) -> None:
        if self._busy() or self.game.is_won():
            return
        if not event.pile:
            self.board.selection = None
            self.board.refresh()
            return
        pile, index = event.pile, event.index
        cards = self.game.piles[pile]
        if pile in TABLEAU:
            self.board.cursor = (pile, index if index >= 0 else max(0, len(cards) - 1))
        else:
            self.board.cursor = (pile, max(0, len(cards) - 1))
        if event.double and pile != "stock":
            # The first click of the double already picked the card up.
            sel = self.board.selection
            if sel and sel[0] == pile:
                index = sel[1]
            self._auto(pile, index)
            return
        self._activate(pile, index)

    @on(Board.SkipFx)
    def _skip_fx(self, _: "Board.SkipFx") -> None:
        self.stop_fireworks()

    @on(Board.Dropped)
    def _dropped(self, event: Board.Dropped) -> None:
        if self._busy() or self.game.is_won():
            return
        if not self._try_move(event.src, event.index, event.dst):
            self.board.selection = None
            self.board.refresh()

    def on_action_bar_invoked(self, event: ActionBar.Invoked) -> None:
        action = {
            "draw": self.action_draw,
            "undo": self.action_undo,
            "hint": self.action_hint,
            "new": self.action_new_deal,
            "restart": self.action_restart,
            "finish": self.action_finish,
            "decline": self.action_decline,
            "help": self.action_help,
            "menu": self.action_menu,
            "quit": self.action_quit_app,
        }.get(event.action)
        if action:
            action()

    # Keyboard ----------------------------------------------------------
    async def on_key(self, event: events.Key) -> None:
        if self._fx_timer is not None:
            # Any key skips the celebration (and does nothing else).
            event.stop()
            event.prevent_default()
            self.stop_fireworks()
            return
        if event.character and event.character in "1234567" and len(event.character) == 1:
            event.stop()
            if self._busy() or self.game.is_won():
                return
            pile = TABLEAU[int(event.character) - 1]
            sel = self.board.selection
            if sel:
                if self._try_move(sel[0], sel[1], pile):
                    return
                self.note("That does not go there.")
            cards = self.game.piles[pile]
            self.board.cursor = (pile, max(0, len(cards) - 1))
            self.board.refresh()

    def action_cursor(self, dx: int, dy: int) -> None:
        if self._busy():
            return
        board, g = self.board, self.game
        pile, idx = board.cursor
        if pile in TABLEAU:
            col = TABLEAU.index(pile)
            cards = g.piles[pile]
            if dx:
                col = (col + dx) % 7
                new = TABLEAU[col]
                board.cursor = (new, max(0, len(g.piles[new]) - 1))
            elif dy < 0:
                first_up = next((i for i, c in enumerate(cards) if c.up), len(cards))
                if cards and idx > first_up:
                    board.cursor = (pile, idx - 1)
                else:
                    top = TOP_ROW[col] or "waste"
                    board.cursor = (top, max(0, len(g.piles[top]) - 1))
            elif dy > 0 and idx < len(cards) - 1:
                board.cursor = (pile, idx + 1)
        else:
            col = TOP_ROW.index(pile)
            if dx:
                col = (col + dx) % 7
                if TOP_ROW[col] is None:
                    col = (col + dx) % 7
                new = TOP_ROW[col]
                board.cursor = (new, max(0, len(g.piles[new]) - 1))
            elif dy > 0:
                new = TABLEAU[col]
                board.cursor = (new, max(0, len(g.piles[new]) - 1))
        board.refresh()

    def action_select(self) -> None:
        if self._busy() or self.game.is_won():
            return
        if self._offer:
            self._start_finish()
            return
        pile, idx = self.board.cursor
        cards = self.game.piles[pile]
        if pile not in TABLEAU:
            idx = len(cards) - 1
        elif not cards:
            idx = -1
        self._activate(pile, idx)

    def action_goto_waste(self) -> None:
        self.board.cursor = ("waste", max(0, len(self.game.piles["waste"]) - 1))
        self.board.refresh()

    def action_draw(self) -> None:
        if not self._busy() and not self.game.is_won():
            self._draw()

    def action_auto(self) -> None:
        if self._busy() or self.game.is_won():
            return
        sel = self.board.selection
        if sel:
            self._auto(*sel)
            return
        pile, idx = self.board.cursor
        if pile == "stock":
            self._draw()
            return
        if pile not in TABLEAU:
            idx = len(self.game.piles[pile]) - 1
        self._auto(pile, idx)

    def action_foundation(self) -> None:
        if self._busy() or self.game.is_won():
            return
        g = self.game
        pile = (self.board.selection or self.board.cursor)[0]
        cards = g.piles[pile]
        if pile == "stock" or not cards:
            return
        dst = g.foundation_for(cards[-1]) if pile not in FOUNDATIONS else None
        if not dst or not self._try_move(pile, len(cards) - 1, dst):
            self.note("That card cannot go to a foundation yet.")

    def action_finish(self) -> None:
        if self._busy() or self.game.is_won():
            return
        if self.game.autocomplete_plan() is not None:
            self._start_finish()
        else:
            self.note("Not a sure win yet: every card must be face up first.")

    def action_undo(self) -> None:
        if self._busy():
            return
        g = self.game
        if g.is_won():
            return
        if g.undo():
            self.board.selection = None
            self.note("Undone.")
            self._after_action()
        else:
            self.note("Nothing to undo.")

    def action_hint(self) -> None:
        if self._busy() or self.game.is_won():
            return
        g = self.game
        if not self._hint_list:
            self._hint_list = g.legal_moves()
            self._hint_idx = 0
        else:
            self._hint_idx = (self._hint_idx + 1) % len(self._hint_list)
        if not self._hint_list:
            self.note("[red]No moves left.[/] [b]u[/b] undo or [b]n[/b] new deal.")
            return
        move = self._hint_list[self._hint_idx]
        board = self.board
        board.selection = None
        board.hint = move
        n = len(self._hint_list)
        count = f" [dim]({self._hint_idx + 1}/{n}, h for the next)[/dim]" if n > 1 else ""
        if move.src == "stock":
            text = "Draw from the stock." if g.piles["stock"] else "Start the next pass through the stock."
        else:
            card = g.piles[move.src][move.index]
            if move.dst in FOUNDATIONS:
                text = f"Move [b]{card.label}[/b] to a foundation."
            else:
                text = f"Move [b]{card.label}[/b] to {self._pile_name(move.dst)}: {move.reason}."
        self.note(f"[{HINT}]HINT[/] {text}{count}")
        board.refresh()

    def _pile_name(self, pile: str) -> str:
        if pile in FOUNDATIONS:
            return "a foundation"
        if pile in TABLEAU:
            cards = self.game.piles[pile]
            return f"column {TABLEAU.index(pile) + 1}" + (f" (on {cards[-1].label})" if cards else " (empty)")
        return pile

    def action_new_deal(self) -> None:
        if self._finishing:
            return
        app = self.app
        assert isinstance(app, SolitaireApp)
        self.abandon()
        app.new_game(replace=True)

    def action_restart(self) -> None:
        if self._finishing:
            return
        app = self.app
        assert isinstance(app, SolitaireApp)
        self.abandon()
        app.new_game(replace=True, seed=self.game.seed, draw=self.game.draw,
                     scoring=self.game.scoring, winnable=self.game.winnable)

    def action_escape(self) -> None:
        if self._offer:
            self.action_decline()
            return
        if self.board.selection or self.board.hint:
            self.board.selection = None
            self.board.hint = None
            self.board.refresh()
            return
        self.action_menu()

    def action_menu(self) -> None:
        if not self.game.is_won():
            self._save()
        self.app.pop_screen()

    def action_help(self) -> None:
        self.app.push_screen(TextPage("Help", HELP_TEXT))

    def action_quit_app(self) -> None:
        if not self.game.is_won():
            self._save()
        self.app.exit()


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------

MENU_INFO = {
    "continue": "Pick up your saved game right where you left it.",
    "new": "Deal a new game with the settings below.",
    "draw": "Cards turned over per click on the stock. 3 is the classic, harder game.",
    "deals": "Winnable only: every deal is checked by a solver first, so it can always be won.",
    "anim": "Fireworks when you win (any key or click skips them).",
    "whatsnew": "What changed in recent updates.",
    "help": "Rules, mouse and keys, scoring.",
    "stats": "Wins, streaks, best times.",
    "quit": "See you next time.",
}


def menu_cards() -> str:
    """A little fan of the four aces for the menu."""
    picks = [Card(1, suit, True) for suit in "HCDS"]
    cv = Canvas(BIG_W + 3 * 6, BIG_H)
    for i, card in enumerate(picks):
        draw_card(cv, i * 6, 0, card, pip=i == len(picks) - 1)
    return cv.to_text()


class MenuScreen(Screen):
    BINDINGS = [
        Binding("question_mark,f1", "help", "Help"),
        Binding("escape,q,ctrl+q", "quit_app", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        settings = load_settings()
        scoring = settings.get("scoring", "standard")
        items = [
            MenuItem("continue", "Continue game"),
            MenuItem("new", "New game"),
            MenuItem("draw", "Draw", "choice", [("1 card", 1), ("3 cards", 3)],
                     index=1 if int(settings.get("draw", 1)) == 3 else 0),
            MenuItem("scoring", "Scoring", "choice", [(SCORING_LABELS[s], s) for s in SCORINGS],
                     index=SCORINGS.index(scoring) if scoring in SCORINGS else 0),
            MenuItem("deals", "Deals", "choice", [("winnable only", True), ("random", False)],
                     index=0 if settings.get("winnable", True) else 1),
            MenuItem("anim", "Win animation", "choice", [("on", True), ("off", False)],
                     index=0 if settings.get("win_animation", True) else 1),
            MenuItem("timer", "Clock", "choice", [("shown", True), ("hidden", False)],
                     index=0 if settings.get("timer", True) else 1),
            MenuItem("whatsnew", "What's new"),
            MenuItem("help", "Help"),
            MenuItem("stats", "Statistics"),
            MenuItem("quit", "Quit"),
        ]
        with Center(id="menu-center"):
            with Vertical(id="menu-box"):
                yield Static(LOGO, id="menu-logo")
                yield Static("S O L I T A I R E", id="menu-logo-small")
                yield Static(menu_cards(), id="menu-cards")
                yield Static(f"v{__version__}", id="menu-version")
                yield AsciiMenu(items, id="menu")
                yield Static("", id="menu-info", markup=True)
                yield ActionBar(
                    [("↑↓ select", None), ("←→ change", None), ("enter confirm", "activate"),
                     ("? help", "help"), ("q quit", "quit")],
                    id="menu-footer",
                )

    def on_mount(self) -> None:
        self._refresh_continue()
        self.query_one(AsciiMenu).focus()
        self._update_info()
        self._fit_art()

    def on_resize(self) -> None:
        self._fit_art()

    def _fit_art(self) -> None:
        height = self.size.height
        self.query_one("#menu-cards", Static).display = height >= 34
        big = height >= 26 and self.size.width >= 78
        self.query_one("#menu-logo", Static).display = big
        self.query_one("#menu-logo-small", Static).display = not big

    def on_screen_resume(self) -> None:
        self._refresh_continue()
        self._fit_art()
        self.query_one("#menu-cards", Static).update(menu_cards())
        self.query_one(AsciiMenu).focus()

    def _refresh_continue(self) -> None:
        menu = self.query_one(AsciiMenu)
        snap = load_game_snapshot()
        menu.set_visible("continue", bool(snap and snap.get("game")))
        self._update_info()

    def say(self, text: str) -> None:
        self.query_one("#menu-info", Static).update(f"[dim]{text}[/dim]")

    def _update_info(self) -> None:
        menu = self.query_one(AsciiMenu)
        item = menu.current_item()
        if item is None:
            return
        if item.id == "scoring":
            info = SCORING_INFO[str(item.value)]
            if item.value == "vegas_cumulative":
                info += f" Bankroll now: ${bankroll()}."
        elif item.id == "continue":
            snap = load_game_snapshot() or {}
            g = snap.get("game") or {}
            info = (f"Deal #{g.get('seed', '?')}, {g.get('moves', 0)} moves, "
                    f"{fmt_time(float(g.get('elapsed', 0)))} on the clock.")
        elif item.id == "new" and getattr(self.app, "_searching", False):
            info = "Shuffling a winnable deal..."
        elif item.id == "new" and load_game_snapshot():
            info = "Deal a new game. Your saved game then counts as a loss."
        else:
            info = MENU_INFO.get(item.id, "")
        self.query_one("#menu-info", Static).update(f"[dim]{info}[/dim]")

    @on(AsciiMenu.Highlighted)
    def _highlighted(self, _: AsciiMenu.Highlighted) -> None:
        self._update_info()

    @on(AsciiMenu.Changed)
    def _changed(self, event: AsciiMenu.Changed) -> None:
        settings = load_settings()
        key = {"draw": "draw", "scoring": "scoring", "deals": "winnable",
               "anim": "win_animation", "timer": "timer"}.get(event.item_id)
        if key:
            settings[key] = event.value
            save_settings(settings)
            app = self.app
            if isinstance(app, SolitaireApp):
                app.top_up_pool()
        self._update_info()

    @on(AsciiMenu.Activated)
    def _activated(self, event: AsciiMenu.Activated) -> None:
        app = self.app
        assert isinstance(app, SolitaireApp)
        if event.item_id == "continue":
            app.continue_game()
        elif event.item_id == "new":
            app.new_game()
        elif event.item_id == "whatsnew":
            app.push_screen(TextPage("What's new", changelog_text()))
        elif event.item_id == "help":
            self.action_help()
        elif event.item_id == "stats":
            app.push_screen(TextPage("Statistics", "\n".join(stats_summary())))
        elif event.item_id == "quit":
            app.exit()

    def action_help(self) -> None:
        self.app.push_screen(TextPage("Help", HELP_TEXT))

    def action_quit_app(self) -> None:
        self.app.exit()

    def on_action_bar_invoked(self, event: ActionBar.Invoked) -> None:
        if event.action == "activate":
            self.query_one(AsciiMenu).action_activate()
        elif event.action == "help":
            self.action_help()
        elif event.action == "quit":
            self.app.exit()


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------


class SolitaireApp(App):
    TITLE = f"Solitaire v{__version__}"

    BINDINGS = [Binding("f2", "screenshot", "Screenshot", show=False)]

    CSS = """
    Screen { background: ansi_default; color: ansi_default; }

    /* Menu */
    #menu-center { align: center middle; height: 1fr; }
    #menu-box { width: auto; height: auto; max-height: 100%; overflow-y: auto; scrollbar-size-vertical: 0; }
    #menu-logo { width: auto; }
    #menu-logo-small { width: auto; text-style: bold; margin-bottom: 1; }
    #menu-cards { width: 100%; content-align: center top; margin-top: 1; }
    #menu-version { color: ansi_bright_black; margin-top: 1; margin-bottom: 1; }
    AsciiMenu { width: auto; height: auto; padding-left: 4; }
    #menu-info { height: 2; margin-top: 1; padding-left: 4; }
    #menu-footer { color: ansi_bright_black; }

    /* Game */
    #statusbar { height: 1; }
    Board { height: 1fr; width: 1fr; margin-top: 1; }
    #note { height: 1; }
    #footer-keys { height: 1; color: ansi_bright_black; }

    /* Text pages */
    #page-title { height: 1; text-style: bold; }
    #page-scroll {
        height: 1fr; scrollbar-size-vertical: 1;
        scrollbar-background: ansi_default; scrollbar-color: ansi_bright_black;
        scrollbar-background-hover: ansi_default; scrollbar-color-hover: ansi_white;
        scrollbar-background-active: ansi_default; scrollbar-color-active: ansi_white;
    }
    #page-body { padding: 1 2; }
    #page-footer { height: 1; color: ansi_bright_black; }
    """

    def __init__(self, no_update: bool = False, initial: Optional[dict] = None,
                 resume: bool = False) -> None:
        super().__init__(ansi_color=True)
        self.no_update = no_update
        self.initial = initial
        self.resume = resume
        self._restart_args: Optional[list[str]] = None
        self._searching = False
        self.window_focused = True
        self.last_input = time.monotonic()

    # -- the game clock only runs while you are actually playing ---------
    def clock_running(self) -> bool:
        return self.window_focused and time.monotonic() - self.last_input < IDLE_PAUSE

    async def on_event(self, event: events.Event) -> None:
        if isinstance(event, (events.Key, events.MouseDown, events.MouseUp, events.MouseMove,
                              events.MouseScrollDown, events.MouseScrollUp)):
            self.last_input = time.monotonic()
        await super().on_event(event)

    def on_app_blur(self) -> None:
        self.window_focused = False

    def on_app_focus(self) -> None:
        self.window_focused = True
        self.last_input = time.monotonic()

    def on_mount(self) -> None:
        self.push_screen(MenuScreen())
        if self.initial:
            self.new_game(seed=self.initial.get("seed"), draw=self.initial.get("draw"),
                          scoring=self.initial.get("scoring"))
        elif self.resume:
            self.continue_game()
        self._whats_new()
        if not self.no_update:
            self._update_check()
        self.top_up_pool()

    # -- games -----------------------------------------------------------
    def _game_screen(self) -> Optional[GameScreen]:
        return next((s for s in self.screen_stack if isinstance(s, GameScreen)), None)

    def _abandon_saved(self) -> None:
        """A saved, started game that gets replaced counts as a loss."""
        snap = load_game_snapshot()
        if not snap or not snap.get("game"):
            return
        try:
            game = Klondike.from_dict(snap["game"])
        except Exception:  # noqa: BLE001 - unreadable save: just drop it
            clear_saved_game()
            return
        if game.started and not game.is_won():
            record_game(
                {"won": False, "draw": game.draw, "scoring": game.scoring, "score": game.score,
                 "moves": game.moves, "time": int(game.elapsed), "seed": game.seed},
                bankroll_after=game.bankroll if game.scoring == "vegas_cumulative" else None,
                log=game.log,
            )
        clear_saved_game()

    def _say(self, text: str) -> None:
        screen = self.screen
        if isinstance(screen, GameScreen):
            screen.note(text)
        elif isinstance(screen, MenuScreen):
            screen.say(text)

    def new_game(self, replace: bool = False, seed: Optional[int] = None,
                 draw: Optional[int] = None, scoring: Optional[str] = None,
                 winnable: bool = False) -> None:
        """Deal a game. Without a fixed deal number and with "winnable only"
        on, the deal comes from the pre-solved pool or a fresh solver search."""
        settings = load_settings()
        draw = int(draw or settings.get("draw", 1))
        scoring = scoring or str(settings.get("scoring", "standard"))
        if seed is None and settings.get("winnable", True):
            pooled = take_pooled_deal(deal_pool_key(draw, scoring))
            if pooled is not None:
                self._start_deal(pooled, draw, scoring, replace, winnable=True)
                return
            if self._searching:
                return
            self._searching = True
            self._say("Shuffling a winnable deal...")
            self._search_deal(draw, scoring, replace)
            return
        self._start_deal(seed, draw, scoring, replace, winnable=winnable)

    @work(thread=True, exclusive=True, group="deal")
    def _search_deal(self, draw: int, scoring: str, replace: bool) -> None:
        worker = get_current_worker()
        seed = find_winnable_seed(draw, scoring, time_budget=30.0,
                                  should_stop=lambda: worker.is_cancelled)
        if not worker.is_cancelled:
            self.call_from_thread(self._found_deal, seed, draw, scoring, replace)

    def _found_deal(self, seed: Optional[int], draw: int, scoring: str, replace: bool) -> None:
        self._searching = False
        if seed is None:
            self.notify("No winnable deal found in time; this one is random.", timeout=6)
        self._start_deal(seed, draw, scoring, replace, winnable=seed is not None)

    def _start_deal(self, seed: Optional[int], draw: int, scoring: str, replace: bool,
                    winnable: bool) -> None:
        self._abandon_saved()
        game = Klondike(seed=seed, draw=draw, scoring=scoring,
                        bankroll=bankroll() if scoring == "vegas_cumulative" else 0)
        game.winnable = winnable
        screen = GameScreen(game)
        if replace and isinstance(self.screen, GameScreen):
            self.switch_screen(screen)
        else:
            self.push_screen(screen)
        self.top_up_pool()

    def top_up_pool(self) -> None:
        """Keep a few solved deals ready for the current settings."""
        settings = load_settings()
        if settings.get("winnable", True):
            self._fill_pool(int(settings.get("draw", 1)), str(settings.get("scoring", "standard")))

    @work(thread=True, exclusive=True, group="pool")
    def _fill_pool(self, draw: int, scoring: str) -> None:
        worker = get_current_worker()
        key = deal_pool_key(draw, scoring)
        while not worker.is_cancelled and len(load_deal_pool().get(key, [])) < POOL_SIZE:
            seed = find_winnable_seed(draw, scoring, time_budget=30.0,
                                      should_stop=lambda: worker.is_cancelled)
            if seed is None or worker.is_cancelled:
                return
            add_pooled_deal(key, seed)

    def continue_game(self) -> bool:
        snap = load_game_snapshot()
        if not snap or not snap.get("game"):
            return False
        try:
            game = Klondike.from_dict(snap["game"])
        except Exception:  # noqa: BLE001
            clear_saved_game()
            return False
        self.push_screen(GameScreen(game))
        return True

    # -- screenshots -----------------------------------------------------
    def action_screenshot(self) -> None:
        path = self.save_terminal_screenshot()
        if path:
            self.notify(f"Screenshot saved: {path}", timeout=6)
        else:
            self.notify("Screenshot failed.", severity="warning", timeout=6)

    def save_terminal_screenshot(self) -> Optional[str]:
        import datetime
        import re

        try:
            svg = self.export_screenshot()
            svg = re.sub(r"<circle[^>]*/>", "", svg)
            svg = re.sub(
                r"<text[^>]*>[^<]*</text>",
                lambda m: "" if self.TITLE in m.group(0) else m.group(0),
                svg,
                count=3,
            )
            target = Path.home() / "Downloads"
            if not target.is_dir():
                target = Path.home()
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            path = target / f"solitaire-{stamp}.svg"
            path.write_text(svg)
            return str(path)
        except Exception:  # noqa: BLE001
            return None

    # -- updates ---------------------------------------------------------
    def _whats_new(self) -> None:
        from ..config import SETTINGS_FILE

        first_run = not SETTINGS_FILE.exists()
        settings = load_settings()
        if settings.get("last_seen_version") == __version__:
            return
        settings["last_seen_version"] = __version__
        save_settings(settings)
        if first_run:
            return
        summary = _changelog_summary(__version__)
        if summary:
            self.notify(summary, title=f"What's new in v{__version__}", timeout=12)

    @work(thread=True, exclusive=True, group="update")
    def _update_check(self) -> None:
        result = check_and_update()
        if result.status == "updated":
            self.call_from_thread(self._apply_background_update, result)
        elif result.status == "failed":
            self.call_from_thread(
                self.notify, result.message, title="Update", severity="warning", timeout=10
            )

    def _apply_background_update(self, result) -> None:
        """A background update landed: restart now from the menu, or tell
        the player it applies on the next start (the game is saved)."""
        if self._game_screen() is None:
            self.request_restart()
            return
        self.notify(
            f"Update installed: v{result.new_version or '?'}. It starts next time "
            f"(your game is saved).",
            title="Update",
            timeout=10,
        )

    def request_restart(self, resume: bool = False) -> None:
        self._restart_args = ["--no-update"] + (["--resume"] if resume else [])
        self.exit()


def run(no_update: bool = False, initial: Optional[dict] = None, resume: bool = False) -> None:
    app = SolitaireApp(no_update=no_update, initial=initial, resume=resume)
    app.run()
    if app._restart_args is not None:
        os.execv(sys.executable, [sys.executable, "-m", "solitaire_app", *app._restart_args])
