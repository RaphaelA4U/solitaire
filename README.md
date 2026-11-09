# Solitaire TUI

Klondike solitaire in your terminal, Claude-terminal style: your terminal's
own colors, everything in ASCII, no buttons and no dialogs. Play with the
**mouse** (click, drag and drop, double-click to send a card home) or
entirely with the **keyboard** (arrows, enter, 1 to 7). Unlimited undo,
hints, winnable deals only (checked by a built-in solver), a finish
question for sure wins, win fireworks, standard and Vegas scoring, and
statistics.

Made the same way as [yahtzee](https://github.com/RaphaelA4U/yahtzee):
one-line install, Claude-style auto-update on every start.

## Install

One line for macOS, Linux, WSL, iSH on iOS, web/SSH terminals, and Git Bash
on Windows:

```sh
U=https://raw.githubusercontent.com/RaphaelA4U/solitaire/main/install.sh; (curl -fsSL $U 2>/dev/null || wget -qO- $U) | sh
```

On native Windows without Git Bash, use PowerShell:

```powershell
iwr -useb https://raw.githubusercontent.com/RaphaelA4U/solitaire/main/install.ps1 | iex
```

Then start the game with:

```sh
solitaire
```

Requirements: `git` and `python3` 3.9+ (the installer offers to install
missing ones via your package manager). Everything lives in `~/.solitaire`;
the `solitaire` command is linked into `~/.local/bin`.

## Auto-update

When you type `solitaire`, a pending update is pulled before the game
starts (4 second timeout, so offline starts stay fast). An update that lands
while you sit in the menu restarts the game right away; mid-game it applies
on the next start, and your game is saved either way.

## How to play

Build the four suits on the foundations (top right) from ace to king. On the
tableau you stack downwards, alternating a red and a black suit; only a king
goes into an empty column. Click the stock (top left) to turn over cards.

Red and black stay the only two colors. Within each color one suit is solid
and the other open: hearts and spades are solid, diamonds and clubs outlined.
Cards are drawn big (rank, suit, and a large pip) when the terminal
has room, and compact on small terminals.

| Mouse | |
|---|---|
| click | pick up a card (with everything on top), click again where it goes |
| drag | drag a card or run onto another pile |
| double-click | send the card to the best spot (foundation first) |

| Keys | |
|---|---|
| arrows | move the cursor; up/down in a column picks how many cards |
| enter / space | pick up, or drop on the cursor pile |
| 1 to 7 | jump to a column; drops there when you hold cards |
| d | draw from the stock |
| a | auto-move the card under the cursor |
| f | send the card under the cursor to its foundation |
| c | finish a sure win for you |
| u / ctrl+z | undo |
| h | hint (press again for the next) |
| v | review: every move, and where a lost deal was lost |
| n / r | new deal / restart this deal |
| esc | put cards down, or back to the menu (auto-saved) |
| ? | help, F2 screenshot (SVG into Downloads) |

## Options

- **Draw**: 1 card per turn, or 3 (the classic, harder game).
- **Scoring**: Standard (points per move, minus time, time bonus on a win),
  Vegas ($52 per deal, $5 per foundation card, limited passes), Vegas
  cumulative (the bankroll carries over), or none.
- **Deals**: "winnable only" (default) lets a solver play every deal through
  before you get it, so each one can be won; "random" deals anything. A few
  solved deals are kept ready in the background, so a new game is instant.
- **Win animation**: fireworks when you win (default on; any key or click
  skips them).

Once every card is face up and the win is certain, the game asks whether it
should finish for you: enter yes, esc keep playing (c finishes later).

Every deal has a number. `solitaire --deal 123456` replays that exact deal.

```
solitaire --new                 deal right away with your saved settings
solitaire --draw 3 --deal 42    a specific deal with draw 3
solitaire --scoring vegas       standard | vegas | vegas_cumulative | none
solitaire --resume              reopen the saved game
solitaire --battle ID           a beheer chat battle: the same winnable deal for everyone
solitaire --profile NAME        separate settings, stats, and save
```

Settings, statistics, and the saved game live in `~/.config/solitaire/`.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

Releases: bump the version in `pyproject.toml` and
`solitaire_app/__init__.py` (both), add a CHANGELOG entry, commit as
`vX.Y.Z title`, tag `vX.Y.Z`, and push main plus the tag. Installed copies
update themselves on their next start.
