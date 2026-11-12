"""`solitaire --verify`: a record is replayed from its seed and its log, and
every claim in it (won, score, moves, time) must match the replay."""

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from solitaire_app.game import TABLEAU, Klondike
from solitaire_app.solver import solution
from solitaire_app.verify import MAX_LOG_ENTRIES, run, verify_record, verify_text

REPO = Path(__file__).resolve().parent.parent
WIN_SEED = 51486  # a deal the solver wins quickly


class Player:
    """Plays a deal through the engine with a clock, like the UI does."""

    def __init__(self, seed: int, draw: int = 1, scoring: str = "standard", step: int = 2):
        self.game = Klondike(seed=seed, draw=draw, scoring=scoring)
        self.step = step

    def tick(self) -> Klondike:
        self.game.elapsed += self.step
        return self.game

    def record(self, **extra) -> dict:
        g = self.game
        entry = {"won": g.is_won(), "draw": g.draw, "scoring": g.scoring, "score": g.score,
                 "moves": g.moves, "time": int(g.elapsed), "seed": g.seed,
                 "date": "2026-11-12T12:00:00", "version": "1.6.0", "log": list(g.log)}
        entry.update(extra)
        return entry


def _card(c: int) -> tuple:
    return c % 13 + 1, "SHDC"[c // 13]


def won_game(seed: int = WIN_SEED, draw: int = 1, scoring: str = "standard") -> Player:
    """Win the deal with the solver's line, played move by move (with one
    stock turn undone at the start, so undo is in the log too)."""
    p = Player(seed, draw, scoring)
    line = solution(Klondike(seed=seed, draw=draw, scoring=scoring))
    assert line is not None
    assert p.tick().draw_stock() and p.tick().undo()
    g = p.game
    for move, saved in line:
        kind = move[0]
        if kind == "tf":
            cards = g.piles[TABLEAU[move[1]]]
            assert p.tick().move(TABLEAU[move[1]], len(cards) - 1, g.foundation_for(cards[-1]))
        elif kind in ("wf", "wt"):
            want = _card(saved[0])
            while not (g.top("waste") and (g.top("waste").rank, g.top("waste").suit) == want):
                assert p.tick().draw_stock()
            dst = g.foundation_for(g.top("waste")) if kind == "wf" else TABLEAU[move[3]]
            assert p.tick().move("waste", len(g.piles["waste"]) - 1, dst)
        else:
            col = TABLEAU[move[1]]
            first_up = next(k for k, c in enumerate(g.piles[col]) if c.up)
            assert p.tick().move(col, first_up + move[2], TABLEAU[move[3]])
    assert g.is_won()
    return p


@pytest.fixture(scope="module")
def won_record() -> dict:
    return won_game().record()


def test_a_real_win_verifies(won_record):
    result = verify_record(won_record)
    assert result["ok"] is True, result["reason"]
    assert result["won"] is True
    assert result["score"] == won_record["score"] > 0
    assert result["time"] == won_record["time"] == int(won_record["log"][-1].split("@")[1])
    assert result["moves"] == won_record["moves"] == len(won_record["log"])
    assert result["seed"] == WIN_SEED
    assert result["settings"] == {"draw": "1", "scoring": "standard"}
    assert result["game"] == "solitaire" and result["reason"] == ""


@pytest.mark.parametrize("seed,draw,scoring", [(11, 1, "vegas"), (11, 1, "vegas_cumulative"),
                                               (WIN_SEED, 1, "none"), (2, 3, "standard")])
def test_other_settings_verify(seed, draw, scoring):
    record = won_game(seed, draw, scoring).record()
    result = verify_record(record)
    assert result["ok"] is True, result["reason"]
    assert result["won"] is True
    assert result["score"] == record["score"]
    assert result["settings"] == {"draw": str(draw), "scoring": scoring}


def test_a_lost_game_verifies_as_lost():
    p = Player(WIN_SEED)
    for _ in range(5):
        assert p.tick().draw_stock()
    record = p.record()
    record["time"] += 7  # the clock ran on until the deal was abandoned
    p.game.elapsed = record["time"]
    record["score"] = p.game.score
    result = verify_record(record)
    assert result["ok"] is True, result["reason"]
    assert result["won"] is False
    assert result["moves"] == 5


def _tampered(record: dict, **changes) -> dict:
    out = json.loads(json.dumps(record))
    for key, value in changes.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


@pytest.mark.parametrize("name,change", [
    ("lower time", lambda r: _tampered(r, time=r["time"] - 1)),
    ("higher score", lambda r: _tampered(r, score=r["score"] + 1)),
    ("won flipped", lambda r: _tampered(r, won=False)),
    ("fewer moves", lambda r: _tampered(r, moves=r["moves"] - 1)),
    ("illegal move added", lambda r: _tampered(r, log=["f0:0>t0@0"] + r["log"])),
    ("move after the win", lambda r: _tampered(r, log=r["log"] + ["d@" + str(r["time"])])),
    ("last move dropped", lambda r: _tampered(r, log=r["log"][:-1])),
    ("other draw", lambda r: _tampered(r, draw=3)),
    ("missing seed", lambda r: _tampered(r, seed=None)),
    ("other seed", lambda r: _tampered(r, seed=r["seed"] + 1)),
    ("unknown draw", lambda r: _tampered(r, draw=2)),
    ("unknown scoring", lambda r: _tampered(r, scoring="golf")),
    ("seed as text", lambda r: _tampered(r, seed=str(r["seed"]))),
    ("clock backwards", lambda r: _tampered(r, log=r["log"][:3] + ["d@0"] + r["log"][3:])),
    ("garbled action", lambda r: _tampered(r, log=["t9:0>t1@0"] + r["log"])),
    ("action not a string", lambda r: _tampered(r, log=[5] + r["log"])),
    ("no log", lambda r: _tampered(r, log=None)),
    ("empty log", lambda r: _tampered(r, log=[])),
])
def test_tampering_fails(won_record, name, change):
    result = verify_record(change(won_record))
    assert result["ok"] is False, name
    assert result["reason"]


def test_too_fast_fails():
    p = Player(WIN_SEED, scoring="none", step=0)
    for _ in range(9):
        p.game.draw_stock()
    p.game.elapsed = 2
    assert p.game.draw_stock()
    result = verify_record(p.record())
    assert result["ok"] is False
    assert "per move" in result["reason"]
    p.game.log[-1] = "d@3"
    assert verify_record(p.record(time=3))["ok"] is True  # exactly 0.3 s per move


@pytest.mark.parametrize("text", ["", "not json", "[1, 2]", "42", "null", '"x"', "[" * 50_000,
                                  '{"seed": 1e400}', '{"seed": ' + "9" * 5000 + "}"])
def test_malformed_input_never_crashes(text):
    result = verify_text(text)
    assert result["ok"] is False and result["reason"]


def test_huge_log_is_refused_quickly(won_record):
    started = time.monotonic()
    log = ["d@1000"] * (MAX_LOG_ENTRIES + 1)
    assert verify_record(_tampered(won_record, log=log))["reason"] == "the log is too long"
    big = _tampered(won_record, log=["d@1000"] * MAX_LOG_ENTRIES, time=1000, won=False)
    assert verify_record(big)["ok"] is False
    assert time.monotonic() - started < 10


def test_run_prints_one_json_line_and_writes_nothing(won_record, tmp_path):
    out = io.StringIO()
    assert run(io.StringIO(json.dumps(won_record)), out) == 0
    lines = out.getvalue().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["ok"] is True
    out = io.StringIO()
    assert run(io.StringIO("x" * 2_000_000), out) == 1
    assert json.loads(out.getvalue())["reason"] == "the record is too large"
    assert list(tmp_path.iterdir()) == []  # the isolated config dir stays empty


def test_main_verify_skips_update_and_ui(won_record, monkeypatch, capsys):
    import solitaire_app.__main__ as entry
    import solitaire_app.ui.app as ui
    import solitaire_app.update as update

    def boom(*_a, **_k):
        raise AssertionError("must not run on --verify")

    monkeypatch.setattr(update, "quick_sync_update", boom)
    monkeypatch.setattr(ui, "run", boom)
    monkeypatch.setattr(sys, "argv", ["solitaire", "--verify"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(won_record)))
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def _cli(stdin: str, home: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SOLITAIRE_", "XDG_"))}
    env["HOME"] = str(home)
    return subprocess.run([sys.executable, "-m", "solitaire_app", "--verify"], input=stdin,
                          capture_output=True, text=True, env=env, cwd=REPO, timeout=60)


def test_cli_verify_in_an_empty_home(won_record, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    good = _cli(json.dumps(won_record), home)
    assert good.returncode == 0, good.stderr
    assert json.loads(good.stdout)["ok"] is True
    bad = _cli(json.dumps(dict(won_record, score=won_record["score"] + 50)), home)
    assert bad.returncode == 1 and json.loads(bad.stdout)["ok"] is False
    junk = _cli("{oops", home)
    assert junk.returncode == 1 and json.loads(junk.stdout)["reason"] == "not valid JSON"
    assert junk.stderr == "" and bad.stderr == ""
    assert list(home.iterdir()) == []
