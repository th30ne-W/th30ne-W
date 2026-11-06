import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from datetime import date, datetime, timezone
from functools import partial
from pathlib import Path

from .calendar import CalendarError, fetch_calendar, parse_calendar, render_calendar
from .cards import render_card
from .content import PROJECTS
from .hero import render_hero
from .theme import THEMES

ROOT = Path(__file__).resolve().parent.parent

# (path relative to the repo root, thunk returning the file's text)
ASSETS: list[tuple[str, Callable[[], str]]] = []

for _layout, _prefix in (("desktop", "hero"), ("mobile", "hero-mobile")):
    for _animated, _suffix in ((True, ""), (False, "-static")):
        for _theme in THEMES:
            ASSETS.append(
                (
                    f"assets/hero/{_prefix}-{_theme.name}{_suffix}.svg",
                    partial(render_hero, _theme, _layout, _animated),
                )
            )

for _project in PROJECTS:
    for _theme in THEMES:
        ASSETS.append((f"assets/cards/{_project.slug}-{_theme.name}.svg", partial(render_card, _project, _theme)))


def write_assets(assets: list[tuple[str, Callable[[], str]]], root: Path) -> None:
    for rel, render in assets:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(render().encode("utf-8"))


def check_assets(assets: list[tuple[str, Callable[[], str]]], root: Path) -> list[str]:
    """Return the relative paths of assets that are missing or differ from the render."""
    stale = []
    for rel, render in assets:
        path = root / rel
        if not path.is_file() or path.read_bytes() != render().encode("utf-8"):
            stale.append(rel)
    return stale


def iso_date(text: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {text!r}")
    try:
        date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a valid date: {text!r}") from None
    return text


def run_calendar(args: argparse.Namespace) -> int:
    try:
        if args.fixture:
            try:
                with open(args.fixture, encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, ValueError) as exc:
                raise CalendarError(f"cannot read fixture {args.fixture}: {exc}") from exc
            cal = parse_calendar(payload)
        else:
            token = os.environ.get("GH_TOKEN")
            if not token:
                print("calendar: GH_TOKEN is not set", file=sys.stderr)
                return 2
            cal = fetch_calendar(args.login, token)
    except CalendarError as exc:
        print(f"calendar: {exc}", file=sys.stderr)
        return 1

    generated_on = args.date or datetime.now(timezone.utc).date().isoformat()
    out = Path(args.out)
    # render everything before touching the output directory
    rendered = [
        (f"calendar-{theme.name}{suffix}.svg", render_calendar(cal, theme, animated, generated_on).encode("utf-8"))
        for animated, suffix in ((True, ""), (False, "-static"))
        for theme in THEMES
    ]
    out.mkdir(parents=True, exist_ok=True)
    for name, data in rendered:
        (out / name).write_bytes(data)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m generator")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="render every static asset into assets/")
    build.add_argument("--check", action="store_true", help="compare with files on disk instead of writing")
    calendar = sub.add_parser("calendar", help="render the contribution calendar SVGs from GitHub GraphQL")
    calendar.add_argument("--login", required=True, help="GitHub login whose calendar to draw")
    calendar.add_argument("--out", required=True, help="directory to write the four SVGs into")
    calendar.add_argument("--fixture", help="read a saved GraphQL response instead of calling the API")
    calendar.add_argument("--date", type=iso_date, help="generation date, YYYY-MM-DD (default: today, UTC)")
    args = parser.parse_args(argv)

    if args.command == "calendar":
        return run_calendar(args)
    if args.check:
        stale = check_assets(ASSETS, ROOT)
        for rel in stale:
            print(f"stale: {rel}")
        return 1 if stale else 0
    write_assets(ASSETS, ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
