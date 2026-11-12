# Changelog

## v1.6.0 (2026-11-12)
- `--seed N` works like `--deal N` (the same flag in every game), and the game log records the version.

## v1.5.1 (2026-11-09)
- Saved games carry a `progress` count (cards home), so a beheer race reads every race game the same way.

## v1.5.0 (2026-11-09)
- Review (v, or "Review last game" in the menu): every move of the game, and for a lost deal the move after which it could no longer be won, with a move that would have kept it winnable.
- Chat battles: `solitaire --battle ID` gives everyone of a beheer battle the same winnable deal; reopening carries on with it.
- `--deal N` carries on with a saved game of that same deal instead of starting over.
- The menu no longer cuts off the ">" behind "Vegas (cumulative)".

## v1.4.0 (2026-11-03)
- Every move of every deal is kept in `~/.config/solitaire/games.jsonl` (undos included, with the clock time), so games can be replayed and reviewed afterwards. About 1.5 KB per deal.
- The clock pauses when the terminal is in the background, and after two minutes without a key or a click. The status bar then shows "paused".

## v1.3.0 (2026-11-03)
- Back to two colors, red and black, so what fits on what is clear at a glance.
- Suits within a color are told apart by the pip: hearts and spades solid, diamonds and clubs outlined.
- The menu shows the four aces, centered.
- Menu: a space between "Win animation" and its value.

## v1.2.1 (2026-11-03)
- CI only: the test run uses Ubuntu 24.04 again, which still has Python 3.9.

## v1.2.0 (2026-11-03)
- Bigger cards: rank and suit in the corners and a large pip in the middle.
- A four-color deck: hearts red, diamonds blue, clubs green, spades in your text color.
- Small terminals (or a very long column) fall back to the compact cards.

## v1.1.0 (2026-11-02)
- Winnable deals only (default on): a built-in solver plays every deal through first. Switch to random deals in the menu.
- A sure win now asks first: enter finishes it for you, esc keeps playing (c finishes later).
- Win fireworks with your time, moves, and score (default on, switch off in the menu; any key skips).

## v1.0.0 (2026-11-02)
- Klondike solitaire in the terminal, in the same ASCII style as yahtzee.
- Mouse: click to pick up and drop, drag and drop, double-click to send a card home.
- Keyboard: arrows, enter, 1 to 7 to drop on a column, d to draw, a/f to auto-move.
- Draw 1 or draw 3; standard, Vegas, and cumulative Vegas scoring.
- Unlimited undo, hints, a "no more moves" warning, and auto-finish for sure wins.
- Numbered deals (`--deal N`), auto-save and continue, statistics with streaks.
- One-line installer and Claude-style auto-update on every start.
