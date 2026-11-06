import argparse
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m generator")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="render every static asset into assets/")
    build.add_argument("--check", action="store_true", help="compare with files on disk instead of writing")
    args = parser.parse_args(argv)

    if args.check:
        stale = check_assets(ASSETS, ROOT)
        for rel in stale:
            print(f"stale: {rel}")
        return 1 if stale else 0
    write_assets(ASSETS, ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
