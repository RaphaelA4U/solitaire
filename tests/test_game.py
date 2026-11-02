"""Rules engine: dealing, legality, scoring, undo, end-of-game detection."""

from solitaire_app.game import FOUNDATIONS, SUITS, TABLEAU, Card, Klondike


def empty_game(**kw) -> Klondike:
    g = Klondike(seed=1, **kw)
    for p in g.piles:
        g.piles[p] = []
    return g


def test_deal_is_complete_and_classic():
    g = Klondike(seed=42)
    cards = [c for pile in g.piles.values() for c in pile]
    assert len(cards) == 52
    assert len({(c.rank, c.suit) for c in cards}) == 52
    for i, t in enumerate(TABLEAU):
        assert len(g.piles[t]) == i + 1
        assert g.piles[t][-1].up
        assert not any(c.up for c in g.piles[t][:-1])
    assert len(g.piles["stock"]) == 24
    assert g.piles["waste"] == []


def test_same_seed_same_deal():
    a, b = Klondike(seed=123), Klondike(seed=123)
    assert a.to_dict()["piles"] == b.to_dict()["piles"]
    assert Klondike(seed=124).to_dict()["piles"] != a.to_dict()["piles"]


def test_draw_one_and_three_and_recycle():
    g = Klondike(seed=5, draw=3)
    g.draw_stock()
    assert len(g.piles["waste"]) == 3 and all(c.up for c in g.piles["waste"])
    while g.piles["stock"]:
        g.draw_stock()
    assert len(g.piles["waste"]) == 24
    assert g.can_recycle()
    g.draw_stock()
    assert len(g.piles["stock"]) == 24 and not g.piles["waste"]
    assert g.passes == 2


def test_vegas_draw_one_has_a_single_pass():
    g = Klondike(seed=5, draw=1, scoring="vegas")
    assert g.score == -52
    while g.piles["stock"]:
        g.draw_stock()
    assert not g.can_recycle()
    assert not g.draw_stock()


def test_alternating_colors_and_kings_on_empty():
    g = empty_game()
    g.piles["t0"] = [Card(8, "S", True)]
    g.piles["t1"] = [Card(7, "H", True)]
    g.piles["t2"] = [Card(7, "C", True)]
    g.piles["t3"] = [Card(13, "D", True)]
    assert g.can_move("t1", 0, "t0")       # red 7 on black 8
    assert not g.can_move("t2", 0, "t0")   # black on black
    assert not g.can_move("t0", 0, "t4")   # only kings to an empty column
    assert g.can_move("t3", 0, "t4")


def test_runs_move_together_and_reveal_scores():
    g = empty_game()
    g.piles["t0"] = [Card(2, "C", False), Card(9, "S", True), Card(8, "H", True)]
    g.piles["t1"] = [Card(10, "D", True)]
    assert g.move("t0", 1, "t1")
    assert [c.label for c in g.piles["t1"]] == ["10♦", "9♠", "8♥"]
    assert g.piles["t0"][0].up  # turned over automatically
    assert g.base_score == 5


def test_foundation_order_and_best_target():
    g = empty_game()
    g.piles["waste"] = [Card(1, "H", True)]
    assert g.best_target("waste", 0) in FOUNDATIONS
    dst = g.auto_move("waste", 0)
    g.piles["t0"] = [Card(3, "H", True)]
    assert not g.can_move("t0", 0, dst)
    g.piles["t1"] = [Card(2, "H", True)]
    assert g.can_move("t1", 0, dst)
    assert not g.can_move("t1", 0, "f1" if dst != "f1" else "f2")


def test_undo_restores_everything_but_counts():
    g = Klondike(seed=9)
    before = g.to_dict()["piles"]
    g.draw_stock()
    g.draw_stock()
    assert g.undo() and g.undo()
    assert g.to_dict()["piles"] == before
    assert g.moves == 4
    assert not g.undo()


def test_save_roundtrip():
    g = Klondike(seed=77, draw=3, scoring="vegas")
    g.draw_stock()
    g.elapsed = 33
    h = Klondike.from_dict(g.to_dict())
    assert h.to_dict() == g.to_dict()
    assert h.undo()


def _nearly_won() -> Klondike:
    """All cards face up in descending alternating runs: a sure win."""
    g = empty_game()
    for i, s in enumerate(SUITS):
        g.piles[FOUNDATIONS[i]] = [Card(r, s, True) for r in range(1, 9)]
    # Remaining 9..K of each suit, as two alternating-color runs per column.
    g.piles["t0"] = [Card(13, "S", True), Card(12, "H", True), Card(11, "C", True),
                     Card(10, "D", True), Card(9, "S", True)]
    g.piles["t1"] = [Card(13, "H", True), Card(12, "C", True), Card(11, "D", True),
                     Card(10, "S", True), Card(9, "H", True)]
    g.piles["t2"] = [Card(13, "C", True), Card(12, "D", True), Card(11, "S", True),
                     Card(10, "H", True), Card(9, "C", True)]
    g.piles["t3"] = [Card(13, "D", True), Card(12, "S", True), Card(11, "H", True),
                     Card(10, "C", True)]
    g.piles["stock"] = [Card(9, "D", False)]
    return g


def test_autocomplete_plan_finishes():
    g = _nearly_won()
    plan = g.autocomplete_plan()
    assert plan is not None
    sim = g.clone()
    for src, _, dst in plan:
        if src == "draw":
            assert sim.draw_stock()
        else:
            assert sim.move(src, len(sim.piles[src]) - 1, dst)
    assert sim.is_won()
    assert not g.is_won()  # planning never touches the real game


def test_autocomplete_needs_face_up_cards():
    g = Klondike(seed=3)
    assert g.autocomplete_plan() is None


def test_stuck_detection():
    g = empty_game(scoring="vegas")  # draw 1 vegas: no recycling
    g.piles["t0"] = [Card(5, "S", False), Card(2, "S", True)]
    g.piles["t1"] = [Card(9, "H", True)]
    g.piles["stock"] = [Card(4, "D", False)]
    assert not g.has_moves()      # the only stock card fits nowhere
    g.piles["stock"] = [Card(8, "S", False), Card(4, "D", False)]  # 4D drawn first
    assert g.has_moves()          # 8 of spades (2nd draw) goes on the 9 of hearts
    g.draw_stock()
    g.draw_stock()
    assert g.move("waste", 1, "t1")
    assert not g.has_moves()      # 4 of diamonds left, still useless, no passes left
    g.piles["waste"].append(Card(1, "S", True))
    assert g.has_moves()


def test_hints_prefer_foundation_and_reveals():
    g = empty_game()
    g.piles["t0"] = [Card(4, "C", False), Card(1, "S", True)]
    g.piles["t1"] = [Card(6, "D", False), Card(9, "H", True)]
    g.piles["t2"] = [Card(10, "C", True)]
    moves = g.legal_moves()
    assert moves[0].src == "t0" and moves[0].dst in FOUNDATIONS
    assert any(m.src == "t1" and m.dst == "t2" for m in moves)


def test_no_pointless_king_shuffle_hint():
    g = empty_game()
    g.piles["t0"] = [Card(13, "S", True)]
    assert not [m for m in g.legal_moves(include_draw=False) if m.src == "t0"]


def test_standard_score_time_and_bonus():
    g = _nearly_won()
    g.base_score = 100
    g.elapsed = 95
    assert g.score == 100 - 18
    for src, _, dst in g.autocomplete_plan():
        if src == "draw":
            g.draw_stock()
        else:
            g.move(src, len(g.piles[src]) - 1, dst)
    assert g.is_won()
    assert g.score > 7000  # time bonus 700000 / 95
