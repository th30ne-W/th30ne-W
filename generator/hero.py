import random

from .fonts import font_face
from .svg import GLOW_FILTER, RAIN_FONT, document, esc, rain_columns
from .theme import Theme

TITLE = "William Nunes \u2014 matrix rain banner"
DESC = (
    "Name plate for William Nunes: Where AI agents meet real work. "
    "Focus areas: AI Agents, Workflow Automation, Secure by Design. "
    "Tagline: AI agents in production."
)

CHIPS = ("AI Agents", "Workflow Automation", "Secure by Design")
TAGLINE = "> AI agents in production"

ORBITRON = "Orbitron, 'Arial Black', sans-serif"
PITCH = 17.5
CHIP_HEIGHT = 26
CHIP_GAP = 10
# The tagline is one monospace line; its width is pinned with textLength so the
# cursor that follows it lands in the same place whichever monospace font renders it.
TAGLINE_WIDTH = round(len(TAGLINE) * 7.8, 1)
CURSOR_GAP = 4
CURSOR_WIDTH = 8
CURSOR_HEIGHT = 14

LAYOUTS = ("desktop", "mobile")


def _num(value: float) -> str:
    return f"{round(value, 2):g}"


def _style(animated: bool) -> str:
    # classes: n=name, s=headline, l=chip label, t=tagline, k=cursor (rain owns c, h, o1-o9 and keyframes f)
    rules = [
        font_face(500),
        font_face(700),
        f".n{{font-family:{ORBITRON};font-weight:700;letter-spacing:.06em;text-anchor:middle}}",
        f".s{{font-family:{ORBITRON};font-weight:500;text-anchor:middle}}",
        f".l{{font-family:{ORBITRON};font-weight:500;font-size:12px;text-anchor:middle}}",
        f".t{{font:{RAIN_FONT}}}",
    ]
    if animated:
        rules.append("@keyframes b{50%{opacity:0}}")
        rules.append(".k{animation:b 1s steps(1) infinite}")
    return "".join(rules)


def _name(theme: Theme, x: float, y: float, size: int, label: str) -> str:
    glow = ' filter="url(#glow)"' if theme.glow else ""
    return f'<text class="n" font-size="{size}" x="{_num(x)}" y="{y}" fill="{theme.accent}"{glow}>{esc(label)}</text>'


def _headline(theme: Theme, x: float, y: float, size: int, label: str) -> str:
    return f'<text class="s" font-size="{size}" x="{_num(x)}" y="{y}" fill="{theme.text}">{esc(label)}</text>'


def _chip(theme: Theme, left: float, top: float, label: str) -> str:
    width = len(label) * 8 + 24
    return (
        f'<rect x="{_num(left)}" y="{top}" width="{width}" height="{CHIP_HEIGHT}" rx="13" '
        f'fill="none" stroke="{theme.accent}"/>'
        f'<text class="l" x="{_num(left + width / 2)}" y="{top + 17}" fill="{theme.accent}">{esc(label)}</text>'
    )


def _chip_row(theme: Theme, center: float, top: float) -> str:
    widths = [len(label) * 8 + 24 for label in CHIPS]
    left = center - (sum(widths) + CHIP_GAP * (len(CHIPS) - 1)) / 2
    parts = []
    for label, width in zip(CHIPS, widths):
        parts.append(_chip(theme, left, top, label))
        left += width + CHIP_GAP
    return "".join(parts)


def _chip_stack(theme: Theme, center: float, tops: tuple[int, ...]) -> str:
    parts = []
    for label, top in zip(CHIPS, tops):
        parts.append(_chip(theme, center - (len(label) * 8 + 24) / 2, top, label))
    return "".join(parts)


def _tagline(theme: Theme, center: float, y: int) -> str:
    total = TAGLINE_WIDTH + CURSOR_GAP + CURSOR_WIDTH
    left = center - total / 2
    return (
        f'<text class="t" x="{_num(left)}" y="{y}" fill="{theme.muted}" '
        f'textLength="{_num(TAGLINE_WIDTH)}" lengthAdjust="spacing">{esc(TAGLINE)}</text>'
        f'<rect class="k" x="{_num(left + TAGLINE_WIDTH + CURSOR_GAP)}" y="{y - 11}" '
        f'width="{CURSOR_WIDTH}" height="{CURSOR_HEIGHT}" fill="{theme.accent}"/>'
    )


def _plate(theme: Theme, x: int, y: int, width: int, height: int) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="8" fill="{theme.bg}" '
        f'fill-opacity=".85" stroke="{theme.accent}" stroke-opacity=".7"/>'
    )


def _desktop(theme: Theme, animated: bool) -> tuple[int, int, str]:
    width, height = 840, 300
    rain = rain_columns(random.Random(7), 48, width, height, 12, PITCH, animated, theme)
    body = [
        f'<rect width="{width}" height="{height}" fill="{theme.bg}"/>',
        f'<g opacity=".45">{rain}</g>',
        _plate(theme, 120, 60, 600, 180),
        _name(theme, 420, 118, 40, "WILLIAM NUNES"),
        _headline(theme, 420, 152, 17, "Where AI agents meet real work."),
        _chip_row(theme, 420, 170),
        _tagline(theme, 420, 222),
    ]
    return width, height, "\n".join(body)


def _mobile(theme: Theme, animated: bool) -> tuple[int, int, str]:
    # Stacked chips end at y=322; the tagline sits 23px below them and the plate ends 15px
    # under its baseline, so both gaps around the tagline are roughly equal.
    width, height = 420, 410
    rain = rain_columns(random.Random(11), 24, width, height, 12, PITCH, animated, theme)
    body = [
        f'<rect width="{width}" height="{height}" fill="{theme.bg}"/>',
        f'<g opacity=".45">{rain}</g>',
        _plate(theme, 20, 50, 380, 310),
        _name(theme, 210, 112, 36, "WILLIAM"),
        _name(theme, 210, 152, 36, "NUNES"),
        _headline(theme, 210, 192, 16, "Where AI agents"),
        _headline(theme, 210, 214, 16, "meet real work."),
        _chip_stack(theme, 210, (232, 264, 296)),
        _tagline(theme, 210, 345),
    ]
    return width, height, "\n".join(body)


def render_hero(theme: Theme, layout: str, animated: bool) -> str:
    if layout == "desktop":
        width, height, body = _desktop(theme, animated)
    elif layout == "mobile":
        width, height, body = _mobile(theme, animated)
    else:
        raise ValueError(f"unknown hero layout {layout!r}; expected one of {LAYOUTS}")
    if theme.glow:
        body = f"<defs>{GLOW_FILTER}</defs>\n{body}"
    return document(width, height, TITLE, DESC, _style(animated), body)
