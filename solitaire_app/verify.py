"""`solitaire --verify`: replay one games.jsonl record and check its claims.

The record is read as JSON from stdin. The deal is rebuilt from `seed` and
`draw` with the engine's own code (cards stored in a record are never
trusted), every action in `log` is applied with the engine's own rules, and
won, score, moves and time are computed, not copied. One JSON line goes to
stdout; the exit code is 0 when the record holds up and 1 when it does not.

This path never writes a file, never starts the UI, never checks for
updates and never touches the network: the server runs it in a sandbox
without network, and the record is the only input. It only imports the
rules engine (not config, ui or update).
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any, Optional

from . import __version__
from .game import SCORINGS, Klondike

MAX_INPUT_BYTES = 1_000_000   # a real record is a few KB
MAX_LOG_ENTRIES = 20_000      # a real game is a few hundred actions
MIN_SECONDS_PER_MOVE = 0.3

# "d@12" stock turn, "u@40" undo, "t3:4>f0@57" move (w = waste); @n = clock.
_ENTRY = re.compile(r"(d|u|(w|t[0-6]|f[0-3]):(\d{1,2})>(t[0-6]|f[0-3]))@(\d{1,7})")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def verify_record(record: Any) -> dict[str, Any]:
    """Replay one record; the result in the `--verify` output shape."""
    result: dict[str, Any] = {
        "ok": False, "game": "solitaire", "seed": None, "settings": {},
        "won": None, "time": None, "score": None, "moves": None,
        "version": __version__, "reason": "",
    }

    def fail(reason: str) -> dict[str, Any]:
        result["ok"] = False
        result["reason"] = reason
        return result

    if not isinstance(record, dict):
        return fail("the record is not a JSON object")

    seed = record.get("seed")
    if seed is None:
        return fail("no seed")
    if not _is_int(seed) or not 1 <= seed <= 999_999:
        return fail("seed must be a whole number from 1 to 999999")
    result["seed"] = seed

    draw = record.get("draw")
    if isinstance(draw, bool) or draw not in (1, 3, "1", "3"):
        return fail(f"unknown draw: {str(draw)[:20]}")
    draw = int(draw)
    scoring = record.get("scoring")
    if scoring not in SCORINGS:
        return fail(f"unknown scoring: {str(scoring)[:20]}")
    result["settings"] = {"draw": str(draw), "scoring": scoring}

    log = record.get("log")
    if not isinstance(log, list):
        return fail("no log")
    if not log:
        return fail("the log is empty")
    if len(log) > MAX_LOG_ENTRIES:
        return fail("the log is too long")

    # vegas_cumulative scores a single deal exactly like vegas (the record's
    # score is the deal's own result; the carried-over bankroll is not part of
    # it), so one game is replayed and scored as plain vegas.
    engine_scoring = "vegas" if scoring == "vegas_cumulative" else scoring
    game = Klondike(seed=seed, draw=draw, scoring=engine_scoring)

    clock = 0
    for n, entry in enumerate(log, start=1):
        if game.is_won():
            return fail(f"the log goes on after the win (action {n})")
        match = _ENTRY.fullmatch(entry) if isinstance(entry, str) else None
        if match is None:
            return fail(f"unparsable action {n}")
        action, src, index, dst, at = match.groups()
        if int(at) < clock:
            return fail(f"the clock runs backwards at action {n}")
        clock = int(at)
        game.elapsed = float(clock)
        if action == "d":
            legal = game.draw_stock()
        elif action == "u":
            legal = game.undo()
        else:
            legal = game.move("waste" if src == "w" else src, int(index), dst)
        if not legal:
            return fail(f"illegal action {n}: {action}")

    won = game.is_won()
    result.update(won=won, time=clock, moves=game.moves)

    claimed_won = record.get("won")
    if not isinstance(claimed_won, bool):
        return fail("the record does not say whether it was won")
    claimed_time = record.get("time")
    if not _is_number(claimed_time):
        return fail("the record has no time")
    claimed_score = record.get("score")
    if not _is_int(claimed_score):
        return fail("the record has no score")
    claimed_moves = record.get("moves")
    if not _is_int(claimed_moves):
        return fail("the record has no moves")

    # A won deal ends with its last action. A lost deal ends when it is
    # abandoned, and the clock may have run on after the last action, so a
    # later time is fine there. The score is taken at
    # that end time: more time can only lower it.
    end = clock
    if not won and claimed_time >= clock:
        end = claimed_time
        game.elapsed = float(end)
    result["score"] = game.score

    if claimed_won != won:
        return fail("the log does not end in a win" if claimed_won else "the log ends in a win the record denies")
    if claimed_moves != game.moves:
        return fail(f"the record claims {claimed_moves} moves, the replay has {game.moves}")
    if claimed_time != end:
        return fail(f"the record claims {claimed_time} s, the replay ends at {clock} s")
    if claimed_score != game.score:
        return fail(f"the record claims score {claimed_score}, the replay scores {game.score}")
    if end * 10 < game.moves * int(MIN_SECONDS_PER_MOVE * 10):
        return fail(f"{game.moves} moves in {end} s is faster than {MIN_SECONDS_PER_MOVE} s per move")

    result["ok"] = True
    return result


def verify_text(text: str) -> dict[str, Any]:
    """Parse and verify; any unexpected input gives ok: false, never a traceback."""
    try:
        record = json.loads(text)
    except (ValueError, RecursionError):
        return verify_record(None) | {"reason": "not valid JSON"}
    try:
        return verify_record(record)
    except Exception:  # noqa: BLE001 - malformed input must never crash the verifier
        return verify_record(None) | {"reason": "the record could not be replayed"}


def run(stdin: Optional[Any] = None, stdout: Optional[Any] = None) -> int:
    """Read one record from stdin, print one JSON line, return the exit code."""
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    try:
        source = getattr(stdin, "buffer", stdin)
        raw = source.read(MAX_INPUT_BYTES + 1)
    except (OSError, ValueError):
        raw = ""
    too_large = len(raw) > MAX_INPUT_BYTES
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if too_large:
        result = verify_record(None) | {"reason": "the record is too large"}
    else:
        result = verify_text(raw)
    stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    stdout.flush()
    return 0 if result["ok"] else 1
