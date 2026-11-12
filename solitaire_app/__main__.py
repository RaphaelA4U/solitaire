"""Entry point: `solitaire` or `python -m solitaire_app`."""

from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="solitaire",
        description="Klondike solitaire in your terminal. Run without arguments for the menu.",
    )
    parser.add_argument("-V", "--version", action="store_true", help="show the version")
    parser.add_argument(
        "--profile", metavar="NAME",
        help="use a separate profile (own settings, stats, saved game)",
    )
    parser.add_argument(
        "--new", action="store_true", help="skip the menu and deal a new game right away"
    )
    parser.add_argument("--resume", action="store_true", help="reopen the saved game right away")
    parser.add_argument("--no-update", action="store_true", help="skip the update check")
    parser.add_argument("--draw", type=int, choices=(1, 3), help="cards per stock turn")
    parser.add_argument(
        "--scoring", metavar="S",
        help="standard | vegas | vegas_cumulative | none",
    )
    parser.add_argument(
        "--deal", "--seed", type=int, metavar="N", dest="deal",
        help="play deal number N (1-999999); the same number is the same deal",
    )
    parser.add_argument(
        "--battle", type=int, metavar="ID",
        help="play chat battle ID (from beheer): everyone gets the same winnable deal",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="read one games.jsonl record on stdin, replay it and print the verdict as JSON",
    )
    args = parser.parse_args()

    if args.verify:
        # Before anything else: no profile, no update check, no settings, no
        # UI. Not even a .pyc file is written on this path.
        sys.dont_write_bytecode = True
        from .verify import run as run_verify

        sys.exit(run_verify())

    if args.profile:
        os.environ["SOLITAIRE_PROFILE"] = args.profile

    if args.version:
        from . import __version__

        print(f"solitaire v{__version__}")
        return

    # Claude-style eager update: pull before the UI starts, then re-exec so
    # you are always ON the latest version, not merely notified about it.
    if not args.no_update:
        from .update import quick_sync_update

        if quick_sync_update():
            os.execv(
                sys.executable,
                [sys.executable, "-m", "solitaire_app", *sys.argv[1:], "--no-update"],
            )

    from .game import SCORINGS

    if args.scoring and args.scoring not in SCORINGS:
        parser.error(f"unknown scoring: {args.scoring} (choose from {', '.join(SCORINGS)})")
    if args.deal is not None and not 1 <= args.deal <= 999_999:
        parser.error("deal numbers run from 1 to 999999")

    initial = None
    if args.battle is not None:
        initial = {"draw": 1, "scoring": "standard", "seed": None, "battle": args.battle}
    elif args.new or args.draw or args.scoring or args.deal is not None:
        from .config import load_settings

        settings = load_settings()
        initial = {
            "draw": args.draw or int(settings.get("draw", 1)),
            "scoring": args.scoring or str(settings.get("scoring", "standard")),
            "seed": args.deal,
        }

    from .ui.app import run

    run(no_update=args.no_update, initial=initial, resume=args.resume)


if __name__ == "__main__":
    main()
