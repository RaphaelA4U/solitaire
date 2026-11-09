"""Smoke tests: drive the real TUI headlessly with Textual's Pilot.

Covers the menu, keyboard play, mouse clicks, drag and drop, double-click,
auto-finish, saving/continuing, and the statistics bookkeeping.
"""

import pytest

from solitaire_app import config
from solitaire_app.game import FOUNDATIONS, SUITS, Card, Klondike
from solitaire_app.ui.app import (
    CARD_H,
    AsciiMenu,
    Board,
    GameScreen,
    MenuScreen,
    SolitaireApp,
    TextPage,
)

SIZE = (100, 40)


def clear(game):
    for p in game.piles:
        game.piles[p] = []


def card_xy(board: Board, pile: str, index: int = -1) -> tuple[int, int]:
    """A point inside the visible part of a card (or slot) on the board."""
    board.render()  # make sure the hit rectangles are current
    for x0, y0, x1, y1, p, i in reversed(board._rects):
        if p == pile and (i == index or index == -1 and i == len(board.game.piles[p]) - 1):
            return x0 + 2, y0
    raise AssertionError(f"no rect for {pile}[{index}]")


async def start(pilot, app) -> GameScreen:
    await pilot.pause()
    assert isinstance(app.screen, GameScreen)
    return app.screen


@pytest.mark.asyncio
async def test_menu_boots_and_enter_deals():
    config.save_settings(dict(config.load_settings(), winnable=False))
    app = SolitaireApp(no_update=True)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        menu = app.screen.query_one(AsciiMenu)
        assert menu.has_focus
        assert menu.visible_items()[0].id == "new"  # nothing saved yet
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, GameScreen)


@pytest.mark.asyncio
async def test_new_game_from_menu_is_a_proven_winnable_deal():
    app = SolitaireApp(no_update=True)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(400):
            await pilot.pause(0.05)
            if isinstance(app.screen, GameScreen):
                break
        assert isinstance(app.screen, GameScreen)
        g = app.screen.game
        assert g.winnable
        from solitaire_app.solver import solution
        from solitaire_app.game import Klondike
        assert solution(Klondike(seed=g.seed, draw=g.draw, scoring=g.scoring)) is not None


@pytest.mark.asyncio
async def test_menu_choices_persist():
    app = SolitaireApp(no_update=True)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("down", "right")  # Draw: 1 -> 3
        await pilot.pause()
        assert config.load_settings()["draw"] == 3
        await pilot.press("down", "right")  # Scoring: standard -> vegas
        await pilot.pause()
        assert config.load_settings()["scoring"] == "vegas"


@pytest.mark.asyncio
async def test_help_and_stats_pages():
    app = SolitaireApp(no_update=True)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, TextPage)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)


@pytest.mark.asyncio
async def test_keyboard_draw_undo_and_pick_drop():
    app = SolitaireApp(no_update=True, initial={"draw": 1, "scoring": "standard", "seed": 11})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        g = screen.game
        await pilot.press("d")
        await pilot.pause()
        assert len(g.piles["waste"]) == 1 and g.moves == 1
        await pilot.press("u")
        await pilot.pause()
        assert not g.piles["waste"]
        # Arrange a known position: red 7 on column 1, black 8 on column 2.
        clear(g)
        g.piles["t0"] = [Card(7, "H", True)]
        g.piles["t1"] = [Card(8, "S", True)]
        g.piles["stock"] = [Card(13, "C", False)]
        screen.board.cursor = ("t0", 0)
        await pilot.press("enter")
        await pilot.pause()
        assert screen.board.selection == ("t0", 0)
        await pilot.press("2")  # drop on column 2
        await pilot.pause()
        assert [c.label for c in g.piles["t1"]] == ["8♠", "7♥"]
        assert screen.board.selection is None


@pytest.mark.asyncio
async def test_mouse_click_stock_click_click_and_drag():
    app = SolitaireApp(no_update=True, initial={"draw": 1, "scoring": "standard", "seed": 12})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        g, board = screen.game, screen.board
        await pilot.click(Board, offset=card_xy(board, "stock"))
        await pilot.pause()
        assert len(g.piles["waste"]) == 1

        clear(g)
        g.piles["t0"] = [Card(7, "H", True)]
        g.piles["t1"] = [Card(8, "S", True)]
        g.piles["t2"] = [Card(8, "C", True)]
        g.piles["stock"] = [Card(13, "C", False)]
        board.refresh()
        # click-click
        await pilot.click(Board, offset=card_xy(board, "t0", 0))
        await pilot.pause(0.5)
        assert board.selection == ("t0", 0)
        await pilot.click(Board, offset=card_xy(board, "t1", 0))
        await pilot.pause(0.5)
        assert [c.label for c in g.piles["t1"]] == ["8♠", "7♥"]
        # drag: press on the 7, release on the other black 8
        await pilot.mouse_down(Board, offset=card_xy(board, "t1", 1))
        await pilot.mouse_up(Board, offset=card_xy(board, "t2", 0))
        await pilot.pause()
        assert [c.label for c in g.piles["t2"]] == ["8♣", "7♥"]
        assert len(g.piles["t1"]) == 1


@pytest.mark.asyncio
async def test_double_click_sends_home():
    app = SolitaireApp(no_update=True, initial={"draw": 1, "scoring": "standard", "seed": 13})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        g, board = screen.game, screen.board
        clear(g)
        g.piles["t3"] = [Card(9, "S", False), Card(1, "D", True)]
        g.piles["stock"] = [Card(13, "C", False)]
        board.refresh()
        xy = card_xy(board, "t3", 1)
        await pilot.click(Board, offset=xy)
        await pilot.click(Board, offset=xy)
        await pilot.pause()
        assert sum(len(g.piles[f]) for f in FOUNDATIONS) == 1
        assert g.piles["t3"][0].up  # the card underneath turned over


def sure_win(screen) -> None:
    g = screen.game
    clear(g)
    for i, s in enumerate(SUITS):
        g.piles[FOUNDATIONS[i]] = [Card(r, s, True) for r in range(1, 12)]
    g.piles["t0"] = [Card(13, "S", True), Card(12, "H", True)]
    g.piles["t1"] = [Card(13, "H", True), Card(12, "S", True)]
    g.piles["t2"] = [Card(13, "D", True), Card(12, "C", True)]
    g.piles["waste"] = [Card(13, "C", True), Card(12, "D", True)]
    g.started = True
    screen._after_action()


async def wait_won(pilot, g) -> None:
    for _ in range(80):
        await pilot.pause(0.05)
        if g.is_won():
            break
    await pilot.pause(0.2)


@pytest.mark.asyncio
async def test_sure_win_asks_then_enter_finishes_and_records():
    config.save_settings(dict(config.load_settings(), win_animation=False))
    app = SolitaireApp(no_update=True, initial={"draw": 1, "scoring": "standard", "seed": 14})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        sure_win(screen)
        await pilot.pause(0.3)
        assert screen._offer and not screen.game.is_won()  # asked, not done by itself
        assert "sure win" in screen._note_text
        await pilot.press("enter")
        await wait_won(pilot, screen.game)
        assert screen.game.is_won()
        stats = config.load_stats()
        assert len(stats["games"]) == 1 and stats["games"][0]["won"]
        assert config.load_game_snapshot() is None
        assert screen.board.banner is None  # animation off


@pytest.mark.asyncio
async def test_sure_win_decline_then_c_finishes():
    config.save_settings(dict(config.load_settings(), win_animation=False))
    app = SolitaireApp(no_update=True, initial={"draw": 1, "scoring": "standard", "seed": 14})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        sure_win(screen)
        await pilot.press("escape")
        await pilot.pause()
        assert not screen._offer and isinstance(app.screen, GameScreen)  # esc did not leave
        await pilot.press("d")  # keep playing: no new question
        await pilot.pause()
        assert not screen._offer
        await pilot.press("c")
        await wait_won(pilot, screen.game)
        assert screen.game.is_won()


@pytest.mark.asyncio
async def test_win_fireworks_show_and_any_key_only_skips():
    app = SolitaireApp(no_update=True, initial={"draw": 1, "scoring": "standard", "seed": 14})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        sure_win(screen)
        await pilot.press("enter")
        await wait_won(pilot, screen.game)
        await pilot.pause(0.6)
        assert screen.board.banner is not None
        assert screen.board.particles  # rockets/sparks in the air
        await pilot.press("n")  # skips the show, does NOT deal a new game
        await pilot.pause()
        assert screen.board.banner is None and app.screen is screen
        await pilot.press("n")
        for _ in range(100):
            await pilot.pause(0.05)
            if app.screen is not screen:
                break
        assert isinstance(app.screen, GameScreen) and app.screen is not screen


@pytest.mark.asyncio
async def test_save_continue_and_new_deal_counts_loss():
    app = SolitaireApp(no_update=True, initial={"draw": 3, "scoring": "standard", "seed": 15})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        await pilot.press("d")
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        menu = app.screen.query_one(AsciiMenu)
        assert menu.visible_items()[0].id == "continue"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, GameScreen)
        resumed = app.screen.game
        assert resumed.seed == 15 and len(resumed.piles["waste"]) == 3
        assert resumed.can_undo()
        await pilot.press("n")
        for _ in range(400):  # a winnable deal may need a solver search first
            await pilot.pause(0.05)
            if isinstance(app.screen, GameScreen) and app.screen.game is not resumed:
                break
        assert isinstance(app.screen, GameScreen) and app.screen.game.moves == 0
        stats = config.load_stats()
        assert [g["won"] for g in stats["games"]] == [False]
        # The abandoned deal is kept with its moves, and replays to where it was.
        logs = config.load_game_logs()
        assert len(logs) == 1 and logs[0]["seed"] == 15 and logs[0]["log"][0].startswith("d@")
        again = Klondike.replay(15, 3, "standard", logs[0]["log"])
        assert len(again.piles["waste"]) == 3


@pytest.mark.asyncio
async def test_clock_pauses_in_background_and_when_idle():
    app = SolitaireApp(no_update=True, initial={"draw": 1, "scoring": "standard", "seed": 8})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        await pilot.press("d")
        g = screen.game
        t = g.elapsed
        screen._tick()
        assert g.elapsed == t + 1
        app.on_app_blur()
        screen._tick()
        assert g.elapsed == t + 1 and "paused" in screen._status_text().plain
        app.on_app_focus()
        screen._tick()
        assert g.elapsed == t + 2
        app.last_input -= 1000  # walked away from the keyboard
        screen._tick()
        assert g.elapsed == t + 2
        await pilot.press("h")  # any key starts it again
        t2 = g.elapsed  # the real one-second timer may have ticked meanwhile
        screen._tick()
        assert g.elapsed == t2 + 1


@pytest.mark.asyncio
async def test_small_terminal_does_not_crash():
    app = SolitaireApp(no_update=True, initial={"draw": 1, "scoring": "vegas", "seed": 16})
    async with app.run_test(size=(60, 20)) as pilot:
        screen = await start(pilot, app)
        for key in ["d", "d", "left", "down", "enter", "h", "escape", "up", "a", "f", "c"]:
            await pilot.press(key)
        await pilot.pause()
        assert screen.board.size.height > 0
        assert screen.board.ch == CARD_H  # compact cards when space is short


@pytest.mark.asyncio
async def test_big_cards_two_colors():
    from solitaire_app.ui.app import BIG_H, BIG_W, PIPS, SUIT_STYLES

    app = SolitaireApp(no_update=True, initial={"draw": 1, "scoring": "standard", "seed": 8})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        board = screen.board
        assert (board.cw, board.ch) == (BIG_W, BIG_H)
        text = board.render()
        # Every face-up tableau top shows its big pip in its own suit color.
        for pile in ("t0", "t1", "t2", "t3", "t4", "t5", "t6"):
            card = screen.game.piles[pile][-1]
            assert PIPS[card.suit][1] in text.plain
        # Two colors (red and black), told apart within a color by solid
        # versus outlined pips.
        assert SUIT_STYLES["H"] == SUIT_STYLES["D"] != SUIT_STYLES["S"] == SUIT_STYLES["C"]
        assert len({PIPS[s] for s in "HDSC"}) == 4
        styles = {str(span.style) for span in text.spans}
        top_suits = {screen.game.piles[p][-1].suit for p in ("t0", "t1", "t2", "t3", "t4", "t5", "t6")}
        for suit in top_suits:
            assert SUIT_STYLES[suit] in styles


@pytest.mark.asyncio
async def test_review_page_in_game_and_from_menu():
    from solitaire_app.ui.app import ReviewPage

    app = SolitaireApp(no_update=True, initial={"draw": 1, "scoring": "standard", "seed": 8})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        await pilot.press("d", "d", "v")
        assert isinstance(app.screen, ReviewPage)
        for _ in range(200):
            await pilot.pause(0.05)
            body = str(app.screen.query_one("#page-body").render())
            if "Every action" in body:
                break
        assert "Every action" in body and "draw" in body
        await pilot.press("escape")
        assert app.screen is screen
        # Leaving for a new deal records the game; the menu then offers its review.
        await pilot.press("n")
        for _ in range(100):
            await pilot.pause(0.05)
            if isinstance(app.screen, GameScreen) and app.screen is not screen:
                break
        await pilot.press("escape")
        await pilot.pause()
        menu = app.screen.query_one(AsciiMenu)
        assert menu.item("review").visible
        assert config.load_game_logs()[-1]["seed"] == 8


@pytest.mark.asyncio
async def test_same_deal_number_resumes_the_saved_game():
    app = SolitaireApp(no_update=True, initial={"draw": 1, "scoring": "standard", "seed": 77})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        await pilot.press("d", "d", "escape")
        await pilot.pause()
    app = SolitaireApp(no_update=True, initial={"draw": 1, "scoring": "standard", "seed": 77})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        assert screen.game.seed == 77 and len(screen.game.piles["waste"]) == 2
    assert config.load_stats()["games"] == []  # nothing counted as a loss


@pytest.mark.asyncio
async def test_menu_is_wide_enough_for_every_choice():
    app = SolitaireApp(no_update=True)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        menu = app.screen.query_one(AsciiMenu)
        widest = max(len(line) for line in menu.render().plain.splitlines())
        for item in menu.items:
            for i in range(len(item.choices)):
                item.index = i
                widest = max(widest, max(len(l) for l in menu.render().plain.splitlines()))
        assert menu.get_content_width(None, None) >= widest


@pytest.mark.asyncio
async def test_battle_deal_is_shared_winnable_and_resumes():
    from solitaire_app.solver import battle_seed, is_winnable

    seed = battle_seed(12)
    assert seed == battle_seed(12) and is_winnable(seed, 1, "standard")
    app = SolitaireApp(no_update=True, initial={"draw": 1, "scoring": "standard", "seed": None, "battle": 12})
    async with app.run_test(size=SIZE) as pilot:
        for _ in range(200):
            await pilot.pause(0.05)
            if isinstance(app.screen, GameScreen):
                break
        g = app.screen.game
        assert g.seed == seed and g.battle == 12 and "Battle" in app.screen._status_text().plain
        await pilot.press("d", "r")  # restart in a battle = undo everything, same game
        assert app.screen.game is g and g.battle == 12 and not g.piles["waste"]
        await pilot.press("d", "escape")
        await pilot.pause()
    assert config.load_game_snapshot()["game"]["battle"] == 12
    app = SolitaireApp(no_update=True, initial={"draw": 1, "scoring": "standard", "seed": None, "battle": 12})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        assert screen.game.battle == 12 and len(screen.game.piles["waste"]) == 1
        await pilot.press("n")  # leaving the battle counts as giving up
        for _ in range(100):
            await pilot.pause(0.05)
            if isinstance(app.screen, GameScreen) and app.screen.game.battle is None:
                break
    entry = config.load_stats()["games"][-1]
    assert entry["battle"] == 12 and entry["won"] is False
