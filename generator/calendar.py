import json
import random
import urllib.error
import urllib.request
from dataclasses import dataclass

from .svg import GLOW_FILTER, GLYPHS, document, esc
from .theme import Theme

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
LEVEL_OPACITY = (0.12, 0.35, 0.55, 0.78, 1.0)

MARGIN = 20
PITCH = 15
GRID_TOP = 40
ROWS = 7
CELL = 13  # drawn width of a week column; the remaining 2px of the pitch is the gap
GLYPH_BASELINE = 11  # glyph baseline below the top of its row
MONTH_Y = 24
HEAD_HEIGHT = 45
HEAD_START = -50
FOOTER_GAP = 22
BOTTOM_MARGIN = 13
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


def _error_message(error: object) -> str:
    if isinstance(error, dict) and "message" in error:
        return str(error["message"])
    return str(error)


def parse_calendar(payload: dict) -> Calendar:
    """Turn a GitHub GraphQL response body into a Calendar, or raise CalendarError."""
    if not isinstance(payload, dict):
        raise CalendarError("unexpected response: not a JSON object")
    if payload.get("errors"):
        raise CalendarError("GitHub API error: " + "; ".join(_error_message(e) for e in payload["errors"]))
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
                if not isinstance(date, str) or not isinstance(count, int):
                    raise CalendarError("unexpected response: malformed contribution day")
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


def _style(theme: Theme, animated: bool, grid_bottom: int) -> str:
    # classes: m=month label, d0-d4=day glyph by level, g=glyph group, f=footer, u=updated, hd=rain head
    rules = [
        f".m{{font:10px {MONO};fill:{theme.muted}}}",
        f".g{{font:12px {MONO};text-anchor:middle}}",
        f".f{{font:12px {MONO};fill:{theme.text}}}",
        f".u{{font:12px {MONO};fill:{theme.muted};text-anchor:end}}",
    ]
    rules += [f".d{level}{{fill-opacity:{opacity:g}}}" for level, opacity in enumerate(LEVEL_OPACITY)]
    if animated:
        rules.append(".hd{animation-name:r;animation-timing-function:linear;animation-iteration-count:infinite}")
        rules.append(
            f"@keyframes r{{from{{transform:translateY({HEAD_START}px)}}to{{transform:translateY({grid_bottom + 10}px)}}}}"
        )
    return "".join(rules)


def render_calendar(cal: Calendar, theme: Theme, animated: bool, generated_on: str) -> str:
    week_count = len(cal.weeks)
    width = 2 * MARGIN + week_count * PITCH
    grid_bottom = GRID_TOP + ROWS * PITCH
    footer_y = grid_bottom + FOOTER_GAP
    height = footer_y + BOTTOM_MARGIN

    plain, glowing = [], []
    for w, week in enumerate(cal.weeks):
        x = _num(MARGIN + w * PITCH + CELL / 2)
        first_row = ROWS - len(week) if w == 0 else 0  # a partial first week hangs from the bottom
        for j, day in enumerate(week):
            y = GRID_TOP + (first_row + j) * PITCH + GLYPH_BASELINE
            glyph = random.Random(day.date).choice(GLYPHS)
            cell = f'<text class="d{day.level}" x="{x}" y="{y}">{esc(glyph)}</text>'
            (glowing if theme.glow and day.level == 4 else plain).append(cell)

    defs = []
    if theme.glow:
        defs.append(GLOW_FILTER)
    if animated:
        defs.append(f'<clipPath id="grid"><rect x="0" y="{GRID_TOP}" width="{width}" height="{grid_bottom - GRID_TOP}"/></clipPath>')
        defs.append(
            '<linearGradient id="hg" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{theme.accent}" stop-opacity="0"/>'
            f'<stop offset="0.5" stop-color="{theme.accent}" stop-opacity="0.55"/>'
            f'<stop offset="1" stop-color="{theme.accent}" stop-opacity="0"/>'
            "</linearGradient>"
        )

    body = []
    if defs:
        body.append(f"<defs>{''.join(defs)}</defs>")
    for index, label in _month_labels(cal.weeks):
        body.append(f'<text class="m" x="{MARGIN + index * PITCH}" y="{MONTH_Y}">{label}</text>')
    glyphs = "\n".join(plain)
    if glowing:
        glyphs += '\n<g filter="url(#glow)">\n' + "\n".join(glowing) + "\n</g>"
    body.append(f'<g class="g" fill="{theme.accent}">\n{glyphs}\n</g>')
    if animated:
        heads = []
        for w in range(week_count):
            rng = random.Random(w)
            duration = round(rng.uniform(3.5, 8), 1)
            delay = round(rng.uniform(0, duration), 1)
            heads.append(
                f'<rect class="hd" x="{MARGIN + w * PITCH}" y="0" width="{CELL}" height="{HEAD_HEIGHT}" fill="url(#hg)" '
                f'style="animation-duration:{duration:.1f}s;animation-delay:-{delay:.1f}s"/>'
            )
        body.append('<g clip-path="url(#grid)">\n' + "\n".join(heads) + "\n</g>")
    body.append(f'<text class="f" x="{MARGIN}" y="{footer_y}">{esc(f"{cal.total:,} contributions in the last year")}</text>')
    body.append(f'<text class="u" x="{width - MARGIN}" y="{footer_y}">{esc(f"updated {generated_on}")}</text>')

    title = "Contribution calendar"
    desc = (
        f"{cal.total:,} contributions in the last year as of {generated_on}, "
        "drawn as green matrix rain; brighter glyphs mark busier days."
    )
    return document(width, height, title, desc, _style(theme, animated, grid_bottom), "\n".join(body))
