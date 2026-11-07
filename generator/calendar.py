import datetime
import json
import random
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .svg import GLOW_FILTER, GLYPHS, document, esc
from .theme import Theme

__all__ = ["Day", "Calendar", "CalendarError", "parse_calendar", "fetch_calendar", "render_calendar"]

GRAPHQL_URL = "https://api.github.com/graphql"
QUERY = (
    "query($login:String!){user(login:$login){contributionsCollection{contributionCalendar"
    "{totalContributions weeks{contributionDays{date contributionCount contributionLevel}}}}}}"
)
LEVELS = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
LEVEL_OPACITY = (0.12, 0.32, 0.52, 0.76, 1.0)  # cell fill-opacity per level, 0 = no contributions


@dataclass(frozen=True)
class Layout:
    weeks: int | None  # show only the last N weeks (None = all)
    left: int  # left margin: room for the Mon/Wed/Fri labels
    margin: int  # right edge margin
    label: int  # px, month/weekday/legend labels
    footer: int  # px, footer text
    wrap_footer: bool  # total on one line, update date and legend on the next


LAYOUTS = {
    "desktop": Layout(weeks=None, left=44, margin=20, label=11, footer=13, wrap_footer=False),
    # sized for a phone column: 26 weeks in ~430 units, so squares are about twice as large on screen
    "mobile": Layout(weeks=26, left=34, margin=14, label=12, footer=14, wrap_footer=True),
}

PITCH = 15
GRID_TOP = 40
ROWS = 7
CELL = 12  # drawn size of a day square; the remaining 3px of the pitch is the gap
GLYPH_BASELINE = 9.5  # glyph baseline below the top of its cell
MONTH_Y = 24
HEAD_HEIGHT = 45
HEAD_START = -50
FOOTER_GAP = 22
BOTTOM_MARGIN = 13
LEGEND_GAP = 6  # between Less/More and the legend squares
WEEKDAY_LABELS = ((1, "Mon"), (3, "Wed"), (5, "Fri"))  # (row, label), weeks start on Sunday
# Month labels closer than this many weeks would overlap (a 3-letter label is ~18px wide).
MIN_LABEL_WEEKS = 3


class CalendarError(Exception):
    pass


@dataclass(frozen=True)
class Day:
    date: str
    count: int
    level: int  # 0..4


@dataclass(frozen=True)
class Calendar:
    total: int
    weeks: tuple[tuple[Day, ...], ...]


def _errors_message(errors: object) -> str:
    """Message for a truthy GraphQL `errors` value, whatever shape it has."""
    if isinstance(errors, list) and all(isinstance(e, dict) and "message" in e for e in errors):
        return "GitHub API error: " + "; ".join(str(e["message"]) for e in errors)
    return "GitHub API error: unexpected errors payload"


def _valid_date(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def parse_calendar(payload: dict) -> Calendar:
    """Turn a GitHub GraphQL response body into a Calendar, or raise CalendarError."""
    if not isinstance(payload, dict):
        raise CalendarError("unexpected response: not a JSON object")
    if payload.get("errors"):
        raise CalendarError(_errors_message(payload["errors"]))
    data = payload.get("data")
    if not isinstance(data, dict):
        raise CalendarError("unexpected response: no data")
    user = data.get("user")
    if user is None:
        raise CalendarError("user not found")
    try:
        calendar = user["contributionsCollection"]["contributionCalendar"]
        total = calendar["totalContributions"]
        raw_weeks = calendar["weeks"]
        weeks = []
        for raw_week in raw_weeks:
            days = []
            for raw_day in raw_week["contributionDays"]:
                name = raw_day["contributionLevel"]
                if name not in LEVELS:
                    raise CalendarError(f"unknown contribution level: {name!r}")
                date, count = raw_day["date"], raw_day["contributionCount"]
                if not _valid_date(date):
                    raise CalendarError(f"invalid contribution date: {date!r}")
                if not isinstance(count, int):
                    raise CalendarError("unexpected response: malformed contribution count")
                days.append(Day(date, count, LEVELS[name]))
            if not days:
                raise CalendarError("unexpected response: empty contribution week")
            weeks.append(tuple(days))
    except (KeyError, TypeError) as exc:
        raise CalendarError(f"unexpected response shape: {exc!r}") from exc
    if not isinstance(total, int) or not weeks:
        raise CalendarError("unexpected response: no contribution data")
    return Calendar(total, tuple(weeks))


def fetch_calendar(login: str, token: str) -> Calendar:
    body = json.dumps({"query": QUERY, "variables": {"login": login}}).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"bearer {token}",
            "User-Agent": "th30ne-W-profile",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise CalendarError(f"GitHub API returned HTTP {exc.code} {exc.reason}") from exc
    except OSError as exc:  # URLError, timeouts, connection failures
        raise CalendarError(f"could not reach GitHub API: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise CalendarError("GitHub API returned a response that is not JSON") from exc
    return parse_calendar(payload)


def _num(value: float) -> str:
    return f"{round(value, 2):g}"


def _month_labels(weeks: tuple[tuple[Day, ...], ...]) -> list[tuple[int, str]]:
    """(week index, label) for the first week whose first day falls in each new month."""
    labels = []
    previous = None
    for index, week in enumerate(weeks):
        month = int(week[0].date[5:7])
        if month != previous:
            labels.append((index, MONTHS[month - 1]))
            previous = month
    # drop a label that would collide with the next one (a short, partial first week)
    return [
        label
        for i, label in enumerate(labels)
        if i + 1 == len(labels) or labels[i + 1][0] - label[0] >= MIN_LABEL_WEEKS
    ]


def _style(theme: Theme, animated: bool, grid_bottom: int, lay: Layout) -> str:
    # classes: m=label (month, weekday, legend), w=right-aligned, c0-c4=day square by level,
    # d/d1-d4=glyph on a square, f=footer text, hd=rain head
    rules = [
        f".m{{font:{lay.label}px {MONO};fill:{theme.muted}}}",
        ".w{text-anchor:end}",
        f".f{{font:{lay.footer}px {MONO};fill:{theme.text}}}",
        f".d{{font:700 10px {MONO};text-anchor:middle}}",
        f".d1,.d2{{fill:{theme.text}}}",
        f".d3,.d4{{fill:{theme.bg}}}",
    ]
    rules += [f".c{level}{{fill:{theme.accent};fill-opacity:{opacity:g}}}" for level, opacity in enumerate(LEVEL_OPACITY)]
    if animated:
        rules.append(".hd{animation-name:r;animation-timing-function:linear;animation-iteration-count:infinite}")
        rules.append(
            f"@keyframes r{{from{{transform:translateY({HEAD_START}px)}}to{{transform:translateY({grid_bottom + 10}px)}}}}"
        )
    return "".join(rules)


def _square(level: int, x: float, y: float, glyph: str) -> str:
    """A day square, plus its glyph when the day has contributions (level >= 1)."""
    out = f'<rect class="c{level}" x="{_num(x)}" y="{_num(y)}" width="{CELL}" height="{CELL}" rx="2"/>'
    if level >= 1:
        out += f'<text class="d d{level}" x="{_num(x + CELL / 2)}" y="{_num(y + GLYPH_BASELINE)}">{esc(glyph)}</text>'
    return out


def render_calendar(cal: Calendar, theme: Theme, animated: bool, generated_on: str, layout: str = "desktop") -> str:
    try:
        lay = LAYOUTS[layout]
    except KeyError:
        raise ValueError(f"unknown calendar layout: {layout!r}") from None
    start = max(0, len(cal.weeks) - lay.weeks) if lay.weeks else 0
    weeks = cal.weeks[start:]
    week_count = len(weeks)
    width = lay.left + week_count * PITCH - (PITCH - CELL) + lay.margin
    grid_bottom = GRID_TOP + ROWS * PITCH
    footer_y = grid_bottom + FOOTER_GAP
    # the mobile footer wraps: total on the first line, update date and legend on the second
    legend_y = footer_y + FOOTER_GAP if lay.wrap_footer else footer_y
    height = legend_y + BOTTOM_MARGIN

    plain, glowing = [], []
    for w, week in enumerate(weeks):
        x = lay.left + w * PITCH
        first_row = ROWS - len(week) if start + w == 0 else 0  # a partial first week hangs from the bottom
        for j, day in enumerate(week):
            y = GRID_TOP + (first_row + j) * PITCH
            glyph = random.Random(day.date).choice(GLYPHS)
            (glowing if theme.glow and day.level == 4 else plain).append(_square(day.level, x, y, glyph))

    defs = []
    if theme.glow:
        defs.append(GLOW_FILTER)
    if animated:
        defs.append(f'<clipPath id="grid"><rect x="0" y="{GRID_TOP}" width="{width}" height="{grid_bottom - GRID_TOP}"/></clipPath>')
        defs.append(
            '<linearGradient id="hg" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{theme.accent}" stop-opacity="0"/>'
            f'<stop offset="0.5" stop-color="{theme.accent}" stop-opacity="0.25"/>'
            f'<stop offset="1" stop-color="{theme.accent}" stop-opacity="0"/>'
            "</linearGradient>"
        )

    body = []
    if defs:
        body.append(f"<defs>{''.join(defs)}</defs>")
    for index, label in _month_labels(weeks):
        body.append(f'<text class="m" x="{lay.left + index * PITCH}" y="{MONTH_Y}">{label}</text>')
    for row, label in WEEKDAY_LABELS:
        body.append(f'<text class="m w" x="{lay.left - 6}" y="{GRID_TOP + row * PITCH + 9}">{label}</text>')
    cells = "\n".join(plain)
    if glowing:
        cells += '\n<g filter="url(#glow)">\n' + "\n".join(glowing) + "\n</g>"
    body.append(f"<g>\n{cells}\n</g>")
    if animated:
        heads = []
        for w in range(week_count):
            rng = random.Random(start + w)
            duration = round(rng.uniform(3.5, 8), 1)
            delay = round(rng.uniform(0, duration), 1)
            heads.append(
                f'<rect class="hd" x="{lay.left + w * PITCH}" y="0" width="{CELL}" height="{HEAD_HEIGHT}" fill="url(#hg)" '
                f'style="animation-duration:{duration:.1f}s;animation-delay:-{delay:.1f}s"/>'
            )
        body.append('<g clip-path="url(#grid)">\n' + "\n".join(heads) + "\n</g>")

    total = f"{cal.total:,} contributions in the last year"
    updated = f"updated {generated_on}"
    if lay.wrap_footer:
        body.append(f'<text class="f" x="{lay.left}" y="{footer_y}">{esc(total)}</text>')
        body.append(f'<text class="f" x="{lay.left}" y="{legend_y}">{esc(updated)}</text>')
    else:
        body.append(f'<text class="f" x="{lay.left}" y="{footer_y}">{esc(f"{total} · {updated}")}</text>')
    # legend, right-aligned on the last footer row: Less, the five levels as drawn, More
    more_x = width - lay.margin
    more_width = 4 * 0.6 * lay.label  # "More" in the monospace label font
    cells_x = more_x - more_width - LEGEND_GAP - (4 * PITCH + CELL)
    legend = [f'<text class="m w" x="{_num(cells_x - LEGEND_GAP)}" y="{legend_y}">Less</text>']
    for level in range(5):
        glyph = random.Random(f"legend-{level}").choice(GLYPHS)
        square = _square(level, cells_x + level * PITCH, legend_y - 9, glyph)
        legend.append(f'<g filter="url(#glow)">{square}</g>' if theme.glow and level == 4 else square)
    legend.append(f'<text class="m w" x="{more_x}" y="{legend_y}">More</text>')
    body.append('<g class="legend">\n' + "\n".join(legend) + "\n</g>")

    title = "Contribution calendar"
    desc = (
        f"{cal.total:,} contributions in the last year as of {generated_on}. "
        "Each square is one day; brighter squares with matrix glyphs mark busier days, empty days are dim."
    )
    return document(width, height, title, desc, _style(theme, animated, grid_bottom, lay), "\n".join(body))
