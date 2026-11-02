"""Settings, statistics, and the saved game in ~/.config/solitaire/.

Set SOLITAIRE_PROFILE (or run `solitaire --profile name`) to use a separate
profile directory with its own settings, stats, and saved game.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".config" / "solitaire"
_profile = os.environ.get("SOLITAIRE_PROFILE", "").strip()
if _profile:
    CONFIG_DIR = CONFIG_DIR / "profiles" / _profile
SETTINGS_FILE = CONFIG_DIR / "settings.json"
STATS_FILE = CONFIG_DIR / "stats.json"
SAVE_FILE = CONFIG_DIR / "saved_game.json"
DEALS_FILE = CONFIG_DIR / "winnable_deals.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "draw": 1,                  # 1 | 3 cards per stock turn
    "scoring": "standard",      # standard | vegas | vegas_cumulative | none
    "winnable": True,           # deal only games the solver proved winnable
    "win_animation": True,      # fireworks when you win
    "timer": True,              # show the clock
    "last_seen_version": None,  # for the what's-new note after updates
}


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        tmp.replace(path)
    except OSError:
        pass


def load_settings() -> dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    settings.update(_read_json(SETTINGS_FILE, {}))
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    _write_json(SETTINGS_FILE, settings)


def save_game_snapshot(snapshot: dict[str, Any]) -> None:
    _write_json(SAVE_FILE, snapshot)


def load_game_snapshot() -> dict[str, Any] | None:
    snapshot = _read_json(SAVE_FILE, None)
    return snapshot if isinstance(snapshot, dict) else None


def clear_saved_game() -> None:
    try:
        SAVE_FILE.unlink()
    except OSError:
        pass


# -- a small pool of pre-solved deals, so "New game" is instant -----------


def deal_pool_key(draw: int, scoring: str) -> str:
    return f"draw{3 if draw == 3 else 1}-{'vegas' if str(scoring).startswith('vegas') else 'standard'}"


_POOL_LOCK = threading.Lock()  # the pool is topped up from a worker thread


def load_deal_pool() -> dict[str, list[int]]:
    pool = _read_json(DEALS_FILE, {})
    return pool if isinstance(pool, dict) else {}


def take_pooled_deal(key: str) -> int | None:
    with _POOL_LOCK:
        pool = load_deal_pool()
        seeds = pool.get(key) or []
        if not seeds:
            return None
        seed = int(seeds.pop(0))
        pool[key] = seeds
        _write_json(DEALS_FILE, pool)
        return seed


def add_pooled_deal(key: str, seed: int) -> None:
    with _POOL_LOCK:
        pool = load_deal_pool()
        pool.setdefault(key, []).append(int(seed))
        _write_json(DEALS_FILE, pool)


# -- statistics --------------------------------------------------------------


def load_stats() -> dict[str, Any]:
    stats = _read_json(STATS_FILE, {})
    if not isinstance(stats, dict):
        stats = {}
    stats.setdefault("games", [])
    stats.setdefault("bankroll", 0)
    return stats


def bankroll() -> int:
    return int(load_stats().get("bankroll", 0))


def record_game(entry: dict[str, Any], bankroll_after: int | None = None) -> None:
    """Record one finished or abandoned deal (abandoning = a loss)."""
    stats = load_stats()
    stats["games"].append(dict(entry, date=datetime.now().isoformat(timespec="seconds")))
    if bankroll_after is not None:
        stats["bankroll"] = int(bankroll_after)
    _write_json(STATS_FILE, stats)


def _fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _streaks(games: list[dict]) -> tuple[int, int]:
    best = cur = 0
    for g in games:
        if g.get("won"):
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return cur, best


def _block(title: str, games: list[dict]) -> list[str]:
    n = len(games)
    won = [g for g in games if g.get("won")]
    cur, best = _streaks(games)
    lines = [
        f"[b]{title}[/b]",
        f"  Played {n:>4}   won {len(won):>4} ({100 * len(won) / n:.0f}%)   "
        f"streak {cur} (best {best})",
    ]
    if won:
        times = [g.get("time", 0) for g in won]
        moves = [g.get("moves", 0) for g in won]
        lines.append(
            f"  Fastest win {_fmt_time(min(times))}   average {_fmt_time(sum(times) / len(times))}   "
            f"fewest moves {min(moves)}"
        )
    scored = [g for g in games if g.get("scoring") == "standard"]
    if scored:
        lines.append(f"  High score {max(g.get('score', 0) for g in scored)}")
    return lines


def stats_summary() -> list[str]:
    stats = load_stats()
    games = stats["games"]
    if not games:
        return [
            "No games recorded yet. Every deal counts once you have made a",
            "move: a win, or a loss when you start a new deal instead.",
        ]
    lines = _block("Overall", games)
    for draw in (1, 3):
        subset = [g for g in games if int(g.get("draw", 1)) == draw]
        if subset:
            lines += [""] + _block(f"Draw {draw}", subset)
    vegas = [g for g in games if str(g.get("scoring", "")).startswith("vegas")]
    if vegas:
        lines += [
            "",
            "[b]Vegas[/b]",
            f"  Deals {len(vegas)}   best deal ${max(g.get('score', 0) for g in vegas)}   "
            f"cumulative bankroll ${stats.get('bankroll', 0)}",
        ]
    return lines
