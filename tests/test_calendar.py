import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from generator.calendar import (
    Calendar,
    CalendarError,
    Day,
    fetch_calendar,
    parse_calendar,
    render_calendar,
)
from generator.svg import GLYPHS
from generator.theme import DARK, LIGHT

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "calendar.json"
SVG_NS = "{http://www.w3.org/2000/svg}"
BUDGET = 92_160

# The fixture holds 364 days; sum((i * 7) % 23 for i in range(364)).
FIXTURE_TOTAL = 4003
FIXTURE_DAYS = 364
# First-day month label per week index, as drawn for the fixture.
FIXTURE_MONTHS = [
    (0, "Nov"), (4, "Dec"), (9, "Jan"), (13, "Feb"), (17, "Mar"), (22, "Apr"), (26, "May"),
    (30, "Jun"), (35, "Jul"), (39, "Aug"), (43, "Sep"), (48, "Oct"), (52, "Nov"),
]

# (file name, theme, animated)
VARIANTS = [
    ("calendar-dark.svg", DARK, True),
    ("calendar-light.svg", LIGHT, True),
    ("calendar-dark-static.svg", DARK, False),
    ("calendar-light-static.svg", LIGHT, False),
]


def load_payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def graphql_days(payload):
    return payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]


class ParseCalendarTests(unittest.TestCase):
    def test_fixture_shape_and_total(self):
        cal = parse_calendar(load_payload())
        self.assertEqual(len(cal.weeks), 53)
        self.assertEqual(cal.total, FIXTURE_TOTAL)
        self.assertEqual([len(w) for w in (cal.weeks[0], cal.weeks[1], cal.weeks[-1])], [3, 7, 4])
        self.assertEqual(sum(len(w) for w in cal.weeks), FIXTURE_DAYS)
        self.assertEqual(cal.weeks[0][0].date, "2025-11-08")
        self.assertEqual(cal.weeks[-1][-1].date, "2026-11-06")

    def test_levels_map_from_enum_names(self):
        first = parse_calendar(load_payload()).weeks[0]
        # counts 0, 7, 14 -> NONE, SECOND_QUARTILE, THIRD_QUARTILE
        self.assertEqual(first, (Day("2025-11-08", 0, 0), Day("2025-11-09", 7, 2), Day("2025-11-10", 14, 3)))

    def test_all_five_levels_are_understood(self):
        levels = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}
        for name, number in levels.items():
            payload = load_payload()
            graphql_days(payload)[0]["contributionDays"][0]["contributionLevel"] = name
            self.assertEqual(parse_calendar(payload).weeks[0][0].level, number, name)

    def test_unknown_level_raises(self):
        payload = load_payload()
        graphql_days(payload)[3]["contributionDays"][2]["contributionLevel"] = "FIFTH_QUARTILE"
        with self.assertRaisesRegex(CalendarError, "FIFTH_QUARTILE"):
            parse_calendar(payload)

    def test_graphql_errors_raise_with_message(self):
        with self.assertRaisesRegex(CalendarError, "Bad things"):
            parse_calendar({"errors": [{"message": "Bad things"}], "data": None})

    def test_malformed_errors_field_raises_calendar_error(self):
        for errors in (True, 1, "boom", ["boom"], [1], [{"nope": 1}], [{"message": "ok"}, "boom"], {"message": "x"}):
            with self.subTest(errors=errors), self.assertRaisesRegex(CalendarError, "GitHub API error"):
                parse_calendar({"errors": errors, "data": None})

    def test_empty_errors_field_is_ignored(self):
        for errors in (None, [], False):
            payload = load_payload()
            payload["errors"] = errors
            self.assertEqual(len(parse_calendar(payload).weeks), 53)

    def test_missing_user_raises(self):
        with self.assertRaisesRegex(CalendarError, "user"):
            parse_calendar({"data": {"user": None}})

    def test_malformed_payloads_raise_calendar_error(self):
        for payload in ({}, {"data": None}, {"data": {"user": {}}}, [], {"data": {"user": {"contributionsCollection": 3}}}):
            with self.subTest(payload=payload), self.assertRaises(CalendarError):
                parse_calendar(payload)

    def test_malformed_day_raises(self):
        payload = load_payload()
        del graphql_days(payload)[0]["contributionDays"][0]["date"]
        with self.assertRaises(CalendarError):
            parse_calendar(payload)

    def test_invalid_dates_raise(self):
        for bad in ("", "2026-00-01", "2026-13-01", "2026-02-30", "20261106", "2026-1-6", "2026-11-06T00:00:00", " 2026-11-06", "\u0662026-11-06", None, 20261106, ["2026-11-06"]):
            payload = load_payload()
            graphql_days(payload)[2]["contributionDays"][1]["date"] = bad
            with self.subTest(date=bad), self.assertRaisesRegex(CalendarError, "date"):
                parse_calendar(payload)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FetchCalendarTests(unittest.TestCase):
    def fetch(self, urlopen):
        with mock.patch("urllib.request.urlopen", urlopen):
            return fetch_calendar("th30ne-W", "tok123")

    def test_posts_the_graphql_query_with_auth_and_returns_the_calendar(self):
        urlopen = mock.Mock(return_value=FakeResponse(FIXTURE.read_bytes()))
        cal = self.fetch(urlopen)
        self.assertEqual((len(cal.weeks), cal.total), (53, FIXTURE_TOTAL))

        (request,), kwargs = urlopen.call_args
        self.assertEqual(kwargs, {"timeout": 30})
        self.assertEqual(request.full_url, "https://api.github.com/graphql")
        self.assertEqual(request.get_method(), "POST")
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual(headers["authorization"], "bearer tok123")
        self.assertEqual(headers["user-agent"], "th30ne-W-profile")
        self.assertEqual(headers["content-type"], "application/json")
        body = json.loads(request.data)
        self.assertEqual(body["variables"], {"login": "th30ne-W"})
        self.assertEqual(
            body["query"],
            "query($login:String!){user(login:$login){contributionsCollection{contributionCalendar"
            "{totalContributions weeks{contributionDays{date contributionCount contributionLevel}}}}}}",
        )

    def test_http_error_raises(self):
        err = urllib.error.HTTPError("https://api.github.com/graphql", 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        self.addCleanup(err.close)
        with self.assertRaisesRegex(CalendarError, "401") as ctx:
            self.fetch(mock.Mock(side_effect=err))
        self.assertNotIn("tok123", str(ctx.exception))

    def test_network_errors_raise(self):
        for exc in (urllib.error.URLError("no route"), TimeoutError("timed out")):
            with self.subTest(exc=exc), self.assertRaises(CalendarError):
                self.fetch(mock.Mock(side_effect=exc))

    def test_errors_in_body_raise(self):
        body = json.dumps({"errors": [{"message": "Could not resolve"}]}).encode()
        with self.assertRaisesRegex(CalendarError, "Could not resolve"):
            self.fetch(mock.Mock(return_value=FakeResponse(body)))

    def test_user_null_raises(self):
        body = json.dumps({"data": {"user": None}}).encode()
        with self.assertRaisesRegex(CalendarError, "user"):
            self.fetch(mock.Mock(return_value=FakeResponse(body)))

    def test_non_json_body_raises(self):
        with self.assertRaises(CalendarError):
            self.fetch(mock.Mock(return_value=FakeResponse(b"<html>")))


MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MOBILE_MONTHS = [(0, "May"), (3, "Jun"), (8, "Jul"), (12, "Aug"), (16, "Sep"), (21, "Oct"), (25, "Nov")]
LAYOUTS = ("desktop", "mobile")
# Spec-owned facts: the desktop image draws every week, the mobile one the last 26; neighbouring cells
# are 15 units apart.
LAYOUT_WEEKS = {"desktop": 53, "mobile": 26}
PITCH = 15
VIEWBOX = {"desktop": "0 0 856 180", "mobile": "0 0 435 202"}


class Seen:
    """What a viewer can observe in a rendered calendar: cells, glyphs and labels found by geometry."""

    def __init__(self, svg):
        self.root = ET.fromstring(svg)
        self.parents = {child: parent for parent in self.root.iter() for child in parent}
        self.css = self.root.find(SVG_NS + "style").text
        self.width = float(self.root.get("viewBox").split()[2])
        self.texts = list(self.root.iter(SVG_NS + "text"))
        self.glyphs = [t for t in self.texts if t.text and len(t.text) == 1 and t.text in GLYPHS]
        squares = [r for r in self.root.iter(SVG_NS + "rect") if r.get("width") == r.get("height")]
        self.size = float(squares[0].get("width"))
        self.less, self.more = self.text("Less"), self.text("More")
        row_y = float(self.less.get("y"))
        on_legend_row = lambda r: float(r.get("y")) < row_y <= float(r.get("y")) + self.size
        self.legend = sorted((r for r in squares if on_legend_row(r)), key=lambda r: float(r.get("x")))
        by_x = {}
        for r in squares:
            if not on_legend_row(r):
                by_x.setdefault(float(r.get("x")), []).append(r)
        self.columns = [sorted(by_x[x], key=lambda r: float(r.get("y"))) for x in sorted(by_x)]
        self.first_x = float(self.columns[0][0].get("x"))
        self.grid_bottom = max(float(r.get("y")) for col in self.columns for r in col) + self.size
        self.row_ys = sorted({float(r.get("y")) for col in self.columns for r in col})

    def text(self, content):
        (found,) = [t for t in self.texts if t.text == content]
        return found

    def font_px(self, el):
        size = None
        for token in (el.get("class") or "").split():
            match = re.search(rf"\.{re.escape(token)}\{{font:(?:700 )?(\d+)px", self.css)
            if match:
                size = float(match.group(1))
        return size

    def anchor(self, el):
        """text-anchor an element ends up with: own attribute, class rules, then inherited, default start."""
        while el is not None:
            if el.get("text-anchor"):
                return el.get("text-anchor")
            for token in (el.get("class") or "").split():
                match = re.search(rf"\.{re.escape(token)}\{{[^}}]*text-anchor:(\w+)", self.css)
                if match:
                    return match.group(1)
            el = self.parents.get(el)
        return "start"

    def opacity(self, el):
        found = None
        for token in (el.get("class") or "").split():
            match = re.search(rf"\.{re.escape(token)}\{{[^}}]*fill-opacity:([\d.]+)", self.css)
            if match:
                found = float(match.group(1))
        return found

    def glyphs_on(self, square):
        x, y = float(square.get("x")), float(square.get("y"))
        return [
            t
            for t in self.glyphs
            if abs(float(t.get("x")) - (x + self.size / 2)) < 0.01 and y < float(t.get("y")) <= y + self.size
        ]

    def filtered(self, el):
        while el is not None:
            if el.get("filter") == "url(#glow)":
                return True
            el = self.parents.get(el)
        return False

    def days(self, cal):
        """(day, cell) pairs: observed columns matched to the fixture's last N weeks, in order."""
        weeks = cal.weeks[-len(self.columns):]
        for column, week in zip(self.columns, weeks):
            assert len(column) == len(week), (len(column), len(week))
            yield from zip(week, column)


class RenderCalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cal = parse_calendar(load_payload())

    def each(self, layouts=LAYOUTS):
        for name, theme, animated in VARIANTS:
            for layout in layouts:
                with self.subTest(f"{layout} {name}"):
                    try:
                        yield name, theme, animated, layout, render_calendar(self.cal, theme, animated, "2026-11-06", layout)
                    except GeneratorExit:
                        # the consumer stopped early (e.g. an assertion failed): close quietly, so subTest
                        # does not record the GeneratorExit as an extra error
                        return

    def test_parses_with_title_and_desc(self):
        for name, theme, animated, layout, svg in self.each():
            root = ET.fromstring(svg)
            self.assertEqual(root.find(SVG_NS + "title").text, "Contribution calendar")
            self.assertEqual(
                root.find(SVG_NS + "desc").text,
                "4,003 contributions in the last year as of 2026-11-06. Each square is one day; "
                "brighter squares with matrix glyphs mark busier days, empty days are dim.",
            )
            self.assertEqual(root.get("viewBox"), VIEWBOX[layout])

    def test_unknown_layout_is_rejected(self):
        with self.assertRaises(ValueError):
            render_calendar(self.cal, DARK, False, "2026-11-06", "tablet")

    def test_layout_defaults_to_desktop(self):
        self.assertEqual(
            render_calendar(self.cal, DARK, False, "2026-11-06"),
            render_calendar(self.cal, DARK, False, "2026-11-06", "desktop"),
        )

    def test_desktop_draws_every_day_and_mobile_the_last_26_weeks(self):
        total_days = sum(len(w) for w in self.cal.weeks)
        window_days = sum(len(w) for w in self.cal.weeks[-26:])
        self.assertEqual(total_days, FIXTURE_DAYS)
        self.assertEqual(window_days, 26 * 7 - 3)  # the fixture's last week has 4 days
        for name, theme, animated, layout, svg in self.each():
            seen = Seen(svg)
            self.assertEqual(len(seen.columns), LAYOUT_WEEKS[layout])
            self.assertEqual(sum(len(c) for c in seen.columns), total_days if layout == "desktop" else window_days)
            self.assertEqual(len(list(seen.days(self.cal))), total_days if layout == "desktop" else window_days)

    def test_cells_form_a_week_by_day_grid(self):
        for name, theme, animated, layout, svg in self.each():
            seen = Seen(svg)
            xs = [float(col[0].get("x")) for col in seen.columns]
            self.assertEqual([b - a for a, b in zip(xs, xs[1:])], [PITCH] * (len(xs) - 1))
            self.assertEqual(len(seen.row_ys), 7)
            self.assertEqual([b - a for a, b in zip(seen.row_ys, seen.row_ys[1:])], [PITCH] * 6)
            for col in seen.columns:
                ys = [float(r.get("y")) for r in col]
                self.assertEqual([b - a for a, b in zip(ys, ys[1:])], [PITCH] * (len(ys) - 1))
            # the last week starts at the top row; a partial first week hangs from the bottom row
            self.assertEqual(float(seen.columns[-1][0].get("y")), seen.row_ys[0])
            if layout == "desktop":
                self.assertEqual(len(seen.columns[0]), 3)
                self.assertEqual(float(seen.columns[0][-1].get("y")), seen.row_ys[-1])
            else:
                self.assertEqual(len(seen.columns[0]), 7)

    def test_a_glyph_only_on_active_days(self):
        for name, theme, animated, layout, svg in self.each():
            seen = Seen(svg)
            levels = set()
            for day, cell in seen.days(self.cal):
                self.assertEqual(len(seen.glyphs_on(cell)), 1 if day.level >= 1 else 0, day)
                levels.add(day.level)
            self.assertEqual(levels, {0, 1, 2, 3, 4})
            self.assertEqual(len(seen.glyphs), sum(1 for d, _ in seen.days(self.cal) if d.level >= 1) + 4)  # + legend

    def test_brighter_days_get_a_higher_fill_opacity(self):
        for name, theme, animated, layout, svg in self.each():
            seen = Seen(svg)
            by_level = {}
            for day, cell in seen.days(self.cal):
                by_level.setdefault(day.level, set()).add(seen.opacity(cell))
            self.assertTrue(all(len(v) == 1 for v in by_level.values()), by_level)
            opacities = [by_level[level].pop() for level in range(5)]
            self.assertEqual(opacities, sorted(set(opacities)))
            self.assertGreaterEqual(opacities[0], 0.08)  # empty days stay faintly visible
            self.assertEqual(opacities[-1], 1.0)

    def test_glyph_is_chosen_per_date_from_the_matrix_set(self):
        a = render_calendar(self.cal, DARK, False, "2026-11-06")
        b = render_calendar(self.cal, DARK, True, "2026-11-07")
        glyphs = lambda svg: [c.text for c in Seen(svg).glyphs]
        self.assertEqual(glyphs(a), glyphs(b))
        self.assertGreater(len(set(glyphs(a))), 10)

    def test_a_day_has_the_same_glyph_in_both_layouts(self):
        def by_date(layout):
            seen = Seen(render_calendar(self.cal, LIGHT, False, "2026-11-06", layout))
            return {day.date: seen.glyphs_on(cell)[0].text for day, cell in seen.days(self.cal) if day.level >= 1}

        desktop, mobile = by_date("desktop"), by_date("mobile")
        self.assertTrue(mobile)
        self.assertTrue(all(desktop[date] == glyph for date, glyph in mobile.items()))

    def test_weekday_labels_sit_left_of_rows_two_four_and_six(self):
        for name, theme, animated, layout, svg in self.each():
            seen = Seen(svg)
            for label, row in (("Mon", 1), ("Wed", 3), ("Fri", 5)):
                el = seen.text(label)
                top = seen.row_ys[row]
                self.assertTrue(top < float(el.get("y")) <= top + seen.size, label)
                self.assertLess(float(el.get("x")), seen.first_x)
            self.assertIn("text-anchor:end", svg)

    def test_legend_repeats_the_five_levels_between_less_and_more(self):
        for name, theme, animated, layout, svg in self.each():
            seen = Seen(svg)
            less, more, cells = seen.less, seen.more, seen.legend
            self.assertEqual(float(more.get("y")), float(less.get("y")))
            self.assertGreater(float(less.get("y")), seen.grid_bottom)  # on the footer row, below the grid
            self.assertEqual(len(cells), 5)
            xs = [float(s.get("x")) for s in cells]
            self.assertTrue(all(float(less.get("x")) < x < float(more.get("x")) for x in xs))
            self.assertEqual(xs, sorted(set(xs)))
            opacities = [seen.opacity(s) for s in cells]
            self.assertEqual(opacities, sorted(set(opacities)))
            self.assertEqual([len(seen.glyphs_on(s)) for s in cells], [0, 1, 1, 1, 1])
            # right-aligned with the grid's right edge
            self.assertEqual(float(more.get("x")), float(seen.columns[-1][0].get("x")) + seen.size)
            # Less and More keep the same breathing room to the squares (monospace advance ~0.6em)
            advance = 0.6 * seen.font_px(less)
            gap_before = xs[0] - float(less.get("x"))  # Less is end-anchored
            gap_after = (float(more.get("x")) - 4 * advance) - (xs[-1] + seen.size)  # More is end-anchored
            self.assertGreaterEqual(gap_before, 5)
            self.assertGreaterEqual(gap_after, gap_before - 0.01)

    def test_month_labels_sit_above_the_first_week_of_each_month(self):
        for name, theme, animated, layout, svg in self.each():
            seen = Seen(svg)
            labels = [
                (round((float(el.get("x")) - seen.first_x) / PITCH), el.text)
                for el in seen.texts
                if el.text in MONTH_NAMES and float(el.get("y")) < seen.row_ys[0]
            ]
            self.assertEqual(labels, FIXTURE_MONTHS if layout == "desktop" else MOBILE_MONTHS)

    def test_labels_stay_inside_the_image(self):
        for name, theme, animated, layout, svg in self.each():
            seen = Seen(svg)
            for el in seen.texts:
                size = seen.font_px(el)
                if size is None:
                    continue
                x, advance = float(el.get("x")), 0.62 * size * len(el.text)
                anchor = seen.anchor(el)
                start, end = {
                    "start": (x, x + advance),
                    "end": (x - advance, x),
                    "middle": (x - advance / 2, x + advance / 2),
                }[anchor]
                self.assertGreaterEqual(start, 0, el.text)
                self.assertLessEqual(end, seen.width, el.text)

    def test_colliding_leading_month_label_is_dropped(self):
        # a first week starting on the last days of October, the next week already in November
        weeks = (
            (Day("2025-10-30", 1, 1), Day("2025-10-31", 0, 0)),
            (Day("2025-11-02", 0, 0),) * 7,
            (Day("2025-11-09", 0, 0),) * 7,
            (Day("2025-11-16", 0, 0),) * 7,
        )
        seen = Seen(render_calendar(Calendar(1, weeks), LIGHT, False, "2025-11-20"))
        self.assertEqual([el.text for el in seen.texts if el.text in MONTH_NAMES], ["Nov"])

    def test_footer_text_and_position(self):
        for name, theme, animated, layout, svg in self.each():
            seen = Seen(svg)
            footer = sorted(
                (t for t in seen.texts if float(t.get("x")) == seen.first_x and float(t.get("y")) > seen.grid_bottom),
                key=lambda t: float(t.get("y")),
            )
            legend_y = float(seen.less.get("y"))
            if layout == "desktop":
                self.assertEqual([t.text for t in footer], ["4,003 contributions in the last year · updated 2026-11-06"])
                self.assertEqual(float(footer[0].get("y")), legend_y)
            else:
                # wrapped: full-year total, then the update date on the legend row
                self.assertEqual([t.text for t in footer], ["4,003 contributions in the last year", "updated 2026-11-06"])
                self.assertEqual(float(footer[1].get("y")), legend_y)
                self.assertGreater(float(footer[1].get("y")), float(footer[0].get("y")))

    def test_mobile_is_sized_for_a_phone_column(self):
        mobile = Seen(render_calendar(self.cal, DARK, False, "2026-11-06", "mobile"))
        desktop = Seen(render_calendar(self.cal, DARK, False, "2026-11-06"))
        # in the same phone column a square is ~2x as large on screen as in the desktop image
        self.assertGreaterEqual(desktop.width / mobile.width, 1.95)

    def test_animated_variants_have_one_head_per_week(self):
        for name, theme, animated, layout, svg in self.each():
            if not animated:
                continue
            root = ET.fromstring(svg)
            seen = Seen(svg)
            heads = [el for el in root.iter(SVG_NS + "rect") if el.get("class") == "hd"]
            self.assertEqual(len(heads), LAYOUT_WEEKS[layout])
            self.assertTrue(all(float(h.get("width")) == seen.size and h.get("height") == "45" for h in heads))
            self.assertIn("@keyframes", svg)
            self.assertIn("translateY(-50px)", svg)
            self.assertIn("translateY(155px)", svg)
            self.assertIn("linearGradient", svg)
            for head in heads:
                style = head.get("style")
                duration = float(re.search(r"animation-duration:([\d.]+)s", style).group(1))
                delay = float(re.search(r"animation-delay:-([\d.]+)s", style).group(1))
                self.assertTrue(3.5 <= duration <= 8, duration)
                self.assertTrue(0 <= delay <= duration, (delay, duration))

    def test_gradient_fades_in_and_out(self):
        root = ET.fromstring(render_calendar(self.cal, DARK, True, "2026-11-06"))
        grad = root.find(f".//{SVG_NS}linearGradient")
        stops = [(s.get("stop-color"), s.get("stop-opacity")) for s in grad.iter(SVG_NS + "stop")]
        self.assertEqual(stops, [("#00ff00", "0"), ("#00ff00", "0.25"), ("#00ff00", "0")])

    def test_still_variants_have_no_animation_or_heads(self):
        for name, theme, animated, layout, svg in self.each():
            if animated:
                continue
            self.assertNotIn("@keyframes", svg)
            self.assertNotIn("animation", svg)
            self.assertNotIn("linearGradient", svg)
            self.assertNotIn('class="hd"', svg)

    def test_within_size_budget(self):
        for name, theme, animated, layout, svg in self.each():
            self.assertLessEqual(len(svg.encode("utf-8")), BUDGET)

    def test_is_self_contained(self):
        for name, theme, animated, layout, svg in self.each():
            self.assertNotIn("<script", svg)
            self.assertNotIn("href=", svg)
            for match in re.finditer(r"url\(", svg):
                rest = svg[match.start():]
                self.assertTrue(rest.startswith("url(data:") or rest.startswith("url(#"), rest[:30])

    def test_theme_colours_and_glow(self):
        for name, theme, animated, layout, svg in self.each():
            if theme is DARK:
                self.assertIn("#00ff00", svg)
                self.assertNotIn("#087a37", svg)
                self.assertIn('filter="url(#glow)"', svg)
                self.assertIn('<filter id="glow"', svg)
            else:
                self.assertIn("#087a37", svg)
                self.assertNotIn("#00ff00", svg)
                self.assertNotIn("glow", svg)

    def test_glow_covers_level_four_days_only_in_dark(self):
        for layout in LAYOUTS:
            for animated in (False, True):
                with self.subTest(f"{layout} animated={animated}"):
                    seen = Seen(render_calendar(self.cal, DARK, animated, "2026-11-06", layout))
                    for day, cell in seen.days(self.cal):
                        self.assertEqual(seen.filtered(cell), day.level == 4, day)
                        for glyph in seen.glyphs_on(cell):
                            self.assertEqual(seen.filtered(glyph), day.level == 4, day)
                    # the legend's brightest square glows like the grid's
                    self.assertEqual([seen.filtered(s) for s in seen.legend], [False] * 4 + [True])

    def test_no_glow_in_light(self):
        for layout in LAYOUTS:
            root = ET.fromstring(render_calendar(self.cal, LIGHT, False, "2026-11-06", layout))
            self.assertFalse([el for el in root.iter() if el.get("filter")])

    def test_render_is_deterministic(self):
        for name, theme, animated in VARIANTS:
            for layout in LAYOUTS:
                a = render_calendar(self.cal, theme, animated, "2026-11-06", layout)
                b = render_calendar(parse_calendar(load_payload()), theme, animated, "2026-11-06", layout)
                self.assertEqual(a, b)

    def test_generated_on_is_escaped(self):
        for layout in LAYOUTS:
            svg = render_calendar(self.cal, LIGHT, False, "<&>", layout)
            ET.fromstring(svg)
            self.assertIn("updated &lt;&amp;&gt;", svg)


def clean_env():
    env = os.environ.copy()
    env.pop("GH_TOKEN", None)
    return env


def run_cli(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "generator", "calendar", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=clean_env() if env is None else env,
    )


class CalendarCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.out = self.tmp / "out"

    def files(self):
        return sorted(p.name for p in self.out.iterdir()) if self.out.exists() else []

    def test_fixture_writes_the_eight_variants(self):
        res = run_cli("--login", "x", "--fixture", str(FIXTURE), "--out", str(self.out), "--date", "2026-11-06")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(
            self.files(),
            sorted(
                f"calendar-{prefix}{theme}{suffix}.svg"
                for prefix in ("", "mobile-")
                for theme in ("dark", "light")
                for suffix in ("", "-static")
            ),
        )
        self.assertEqual(len(self.files()), 8)
        for path in self.out.iterdir():
            text = path.read_text(encoding="utf-8")
            self.assertIn("updated 2026-11-06", text)
            ET.fromstring(text)
        cal = parse_calendar(load_payload())
        for name, theme, animated, layout in (
            ("calendar-light-static.svg", LIGHT, False, "desktop"),
            ("calendar-mobile-dark.svg", DARK, True, "mobile"),
            ("calendar-mobile-light-static.svg", LIGHT, False, "mobile"),
        ):
            self.assertEqual(
                (self.out / name).read_text(encoding="utf-8"), render_calendar(cal, theme, animated, "2026-11-06", layout)
            )
        self.assertIn("@keyframes", (self.out / "calendar-dark.svg").read_text(encoding="utf-8"))
        self.assertNotIn("@keyframes", (self.out / "calendar-dark-static.svg").read_text(encoding="utf-8"))
        for theme in ("dark", "light"):
            self.assertIn("@keyframes", (self.out / f"calendar-mobile-{theme}.svg").read_text(encoding="utf-8"))
            self.assertNotIn("animation", (self.out / f"calendar-mobile-{theme}-static.svg").read_text(encoding="utf-8"))

    def test_date_defaults_to_today_utc(self):
        before = datetime.now(timezone.utc).date()
        res = run_cli("--login", "x", "--fixture", str(FIXTURE), "--out", str(self.out))
        after = datetime.now(timezone.utc).date()
        self.assertEqual(res.returncode, 0, res.stderr)
        text = (self.out / "calendar-dark.svg").read_text(encoding="utf-8")
        match = re.search(r"updated (\d{4}-\d{2}-\d{2})<", text)
        self.assertIsNotNone(match)
        # a run that straddles UTC midnight may legitimately land on either day
        self.assertIn(match.group(1), {before.isoformat(), after.isoformat()})

    def test_missing_token_without_fixture_exits_2(self):
        res = run_cli("--login", "x", "--out", str(self.out))
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stderr, "calendar: GH_TOKEN is not set\n")
        self.assertEqual(self.files(), [])

    def test_empty_token_counts_as_missing(self):
        env = clean_env()
        env["GH_TOKEN"] = ""
        res = run_cli("--login", "x", "--out", str(self.out), env=env)
        self.assertEqual((res.returncode, res.stderr), (2, "calendar: GH_TOKEN is not set\n"))

    def test_calendar_error_exits_1_and_leaves_existing_files_alone(self):
        payload = load_payload()
        graphql_days(payload)[5]["contributionDays"][0]["contributionLevel"] = "BOGUS"
        bad = self.tmp / "bad.json"
        bad.write_text(json.dumps(payload), encoding="utf-8")
        self.out.mkdir()
        (self.out / "calendar-dark.svg").write_bytes(b"sentinel")
        before = {p.name: p.read_bytes() for p in self.out.iterdir()}

        res = run_cli("--login", "x", "--fixture", str(bad), "--out", str(self.out), "--date", "2026-11-06")
        self.assertEqual(res.returncode, 1)
        self.assertTrue(res.stderr.startswith("calendar: "), res.stderr)
        self.assertIn("BOGUS", res.stderr)
        self.assertEqual({p.name: p.read_bytes() for p in self.out.iterdir()}, before)

    def test_unreadable_fixture_exits_1(self):
        res = run_cli("--login", "x", "--fixture", str(self.tmp / "nope.json"), "--out", str(self.out))
        self.assertEqual(res.returncode, 1)
        self.assertTrue(res.stderr.startswith("calendar: "), res.stderr)
        self.assertEqual(self.files(), [])

    def test_invalid_date_in_fixture_exits_1_and_writes_nothing(self):
        payload = load_payload()
        graphql_days(payload)[0]["contributionDays"][0]["date"] = "2026-13-01"
        bad = self.tmp / "bad-date.json"
        bad.write_text(json.dumps(payload), encoding="utf-8")
        res = run_cli("--login", "x", "--fixture", str(bad), "--out", str(self.out), "--date", "2026-11-06")
        self.assertEqual(res.returncode, 1)
        self.assertTrue(res.stderr.startswith("calendar: "), res.stderr)
        self.assertIn("date", res.stderr)
        self.assertEqual(self.files(), [])

    def test_invalid_date_is_a_usage_error(self):
        for value in ("2026-13-01", "20261106", "tomorrow"):
            with self.subTest(value):
                res = run_cli("--login", "x", "--fixture", str(FIXTURE), "--out", str(self.out), "--date", value)
                self.assertEqual(res.returncode, 2)
                self.assertEqual(self.files(), [])

    def test_build_does_not_touch_the_calendar(self):
        # the calendar needs live data, so `build` must neither write nor change calendar files
        root = self.tmp / "repo"
        for name in ("generator", "fonts"):
            shutil.copytree(REPO / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
        calendar_dir = root / "assets" / "calendar"
        calendar_dir.mkdir(parents=True)
        (calendar_dir / "calendar-dark.svg").write_bytes(b"sentinel")
        res = subprocess.run(
            [sys.executable, "-m", "generator", "build"], cwd=root, capture_output=True, text=True, env=clean_env()
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((root / "assets" / "hero").is_dir())
        self.assertEqual([p.name for p in calendar_dir.iterdir()], ["calendar-dark.svg"])
        self.assertEqual((calendar_dir / "calendar-dark.svg").read_bytes(), b"sentinel")


if __name__ == "__main__":
    unittest.main()
