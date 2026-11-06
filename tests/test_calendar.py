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
    CELL,
    GLYPH_BASELINE,
    GRID_TOP,
    LAYOUTS,
    PITCH,
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


def style_of(root):
    return root.find(SVG_NS + "style").text


def resolved_opacity(root, el):
    """fill-opacity an element ends up with through its classes in the stylesheet."""
    found = None
    for token in (el.get("class") or "").split():
        match = re.search(rf"\.{re.escape(token)}\{{[^}}]*fill-opacity:([\d.]+)", style_of(root))
        if match:
            found = float(match.group(1))
    return found


def cell_rects(root):
    """Every 12x12 day square, grid and legend alike, in document order."""
    return [el for el in root.iter(SVG_NS + "rect") if (el.get("width"), el.get("height")) == ("12", "12")]


GRID_BOTTOM = GRID_TOP + 7 * PITCH


def grid_squares(root):
    return [el for el in cell_rects(root) if float(el.get("y")) < GRID_BOTTOM]


def glyph_texts(root):
    return [el for el in root.iter(SVG_NS + "text") if el.text and len(el.text) == 1 and el.text in GLYPHS]


def glyphs_on(root, square):
    """Glyph texts drawn on top of a square, found by position."""
    x, y = float(square.get("x")), float(square.get("y"))
    return [
        t
        for t in glyph_texts(root)
        if abs(float(t.get("x")) - (x + CELL / 2)) < 0.01 and y < float(t.get("y")) <= y + CELL
    ]


def filtered(parents, el):
    """True when el, or an ancestor, carries the glow filter."""
    while el is not None:
        if el.get("filter") == "url(#glow)":
            return True
        el = parents.get(el)
    return False


def parent_map(root):
    return {child: parent for parent in root.iter() for child in parent}


def window_start(cal, layout):
    lay = LAYOUTS[layout]
    return max(0, len(cal.weeks) - lay.weeks) if lay.weeks else 0


def day_cells(cal, layout):
    """(day, x, y) of every day square the layout draws, from the layout's own constants."""
    lay = LAYOUTS[layout]
    start = window_start(cal, layout)
    for w, week in enumerate(cal.weeks[start:]):
        first_row = 7 - len(week) if start + w == 0 else 0
        for j, day in enumerate(week):
            yield day, lay.left + w * PITCH, GRID_TOP + (first_row + j) * PITCH


MOBILE_MONTHS = [(0, "May"), (3, "Jun"), (8, "Jul"), (12, "Aug"), (16, "Sep"), (21, "Oct"), (25, "Nov")]


class RenderCalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cal = parse_calendar(load_payload())

    def each(self, layouts=("desktop", "mobile")):
        for name, theme, animated in VARIANTS:
            for layout in layouts:
                with self.subTest(f"{layout} {name}"):
                    yield name, theme, animated, layout, render_calendar(self.cal, theme, animated, "2026-11-06", layout)

    def test_parses_with_title_and_desc(self):
        for name, theme, animated, layout, svg in self.each():
            root = ET.fromstring(svg)
            self.assertEqual(root.find(SVG_NS + "title").text, "Contribution calendar")
            self.assertEqual(
                root.find(SVG_NS + "desc").text,
                "4,003 contributions in the last year as of 2026-11-06. Each square is one day; "
                "brighter squares with matrix glyphs mark busier days, empty days are dim.",
            )
            self.assertEqual(root.get("viewBox"), {"desktop": "0 0 856 180", "mobile": "0 0 435 202"}[layout])

    def test_month_labels_stay_inside_the_image(self):
        for name, theme, animated, layout, svg in self.each():
            root = ET.fromstring(svg)
            width = float(root.get("viewBox").split()[2])
            size = LAYOUTS[layout].label
            for el in root.iter(SVG_NS + "text"):
                if el.get("y") == "24":  # monospace glyphs are ~0.62em wide
                    self.assertLessEqual(float(el.get("x")) + len(el.text) * size * 0.62, width, el.text)

    def test_unknown_layout_is_rejected(self):
        with self.assertRaises(ValueError):
            render_calendar(self.cal, DARK, False, "2026-11-06", "tablet")

    def test_layout_defaults_to_desktop(self):
        self.assertEqual(
            render_calendar(self.cal, DARK, False, "2026-11-06"),
            render_calendar(self.cal, DARK, False, "2026-11-06", "desktop"),
        )

    def test_one_square_per_day_and_a_glyph_only_on_active_days(self):
        for name, theme, animated, layout, svg in self.each():
            root = ET.fromstring(svg)
            squares = grid_squares(root)
            expected = list(day_cells(self.cal, layout))
            self.assertEqual(len(squares), len(expected))
            self.assertEqual({(float(s.get("x")), float(s.get("y"))) for s in squares}, {(x, y) for _, x, y in expected})
            for day, x, y in expected:
                (square,) = [s for s in squares if (float(s.get("x")), float(s.get("y"))) == (x, y)]
                self.assertEqual(len(glyphs_on(root, square)), 1 if day.level >= 1 else 0, day)
            self.assertTrue(any(d.level == 0 for d, _, _ in expected) and any(d.level >= 1 for d, _, _ in expected))

    def test_desktop_draws_every_day_and_mobile_the_last_26_weeks(self):
        total_days = sum(len(w) for w in self.cal.weeks)
        window_days = sum(len(w) for w in self.cal.weeks[-26:])
        self.assertEqual(total_days, FIXTURE_DAYS)
        self.assertEqual(len(list(day_cells(self.cal, "desktop"))), total_days)
        self.assertEqual(len(list(day_cells(self.cal, "mobile"))), window_days)
        for name, theme, animated, layout, svg in self.each():
            self.assertEqual(len(grid_squares(ET.fromstring(svg))), total_days if layout == "desktop" else window_days)
        self.assertEqual(window_days, 26 * 7 - 3)  # the fixture's last week has 4 days

    def test_square_grid_geometry_and_levels(self):
        root = ET.fromstring(render_calendar(self.cal, LIGHT, False, "2026-11-06"))
        cells = grid_squares(root)
        # first week has 3 days and sits in the bottom three rows; the second week fills all seven
        first = cells[0]
        self.assertEqual((float(first.get("x")), float(first.get("y"))), (44, 40 + 4 * 15))
        self.assertEqual((first.get("width"), first.get("height"), first.get("rx")), ("12", "12", "2"))
        fourth = cells[3]
        self.assertEqual((float(fourth.get("x")), float(fourth.get("y"))), (59, 40))
        # last week: 4 days from the top row, at week column 52
        last = cells[FIXTURE_DAYS - 1]
        self.assertEqual((float(last.get("x")), float(last.get("y"))), (44 + 52 * 15, 40 + 3 * 15))

    def test_mobile_grid_starts_with_a_full_week_at_the_mobile_margin(self):
        cells = grid_squares(ET.fromstring(render_calendar(self.cal, LIGHT, False, "2026-11-06", "mobile")))
        self.assertEqual((float(cells[0].get("x")), float(cells[0].get("y"))), (34, 40))
        last = cells[-1]
        self.assertEqual((float(last.get("x")), float(last.get("y"))), (34 + 25 * 15, 40 + 3 * 15))

    def test_level_fill_opacity_rises_strictly(self):
        for name, theme, animated, layout, svg in self.each():
            root = ET.fromstring(svg)
            legend_cells = [s for s in cell_rects(root) if float(s.get("y")) >= GRID_BOTTOM]
            opacities = [resolved_opacity(root, s) for s in legend_cells]
            self.assertEqual(len(opacities), 5)
            self.assertEqual(opacities, sorted(set(opacities)))
            self.assertGreaterEqual(opacities[0], 0.08)  # empty days stay faintly visible
            self.assertEqual(opacities[-1], 1.0)

    def test_glyph_is_chosen_per_date_from_the_matrix_set(self):
        a = render_calendar(self.cal, DARK, False, "2026-11-06")
        b = render_calendar(self.cal, DARK, True, "2026-11-07")
        glyphs = lambda svg: [c.text for c in glyph_texts(ET.fromstring(svg))]
        self.assertEqual(glyphs(a), glyphs(b))
        self.assertTrue(all(g in GLYPHS for g in glyphs(a)))
        self.assertGreater(len(set(glyphs(a))), 10)

    def test_a_day_has_the_same_glyph_in_both_layouts(self):
        by_pos = lambda layout: {
            day.date: glyphs_on(root, s)[0].text
            for root in [ET.fromstring(render_calendar(self.cal, LIGHT, False, "2026-11-06", layout))]
            for day, x, y in day_cells(self.cal, layout)
            if day.level >= 1
            for s in grid_squares(root)
            if (float(s.get("x")), float(s.get("y"))) == (x, y)
        }
        desktop, mobile = by_pos("desktop"), by_pos("mobile")
        self.assertTrue(mobile)
        self.assertTrue(all(desktop[date] == glyph for date, glyph in mobile.items()))

    def test_weekday_labels_sit_left_of_rows_two_four_and_six(self):
        for name, theme, animated, layout, svg in self.each():
            labels = {
                el.text: (float(el.get("x")), float(el.get("y")))
                for el in ET.fromstring(svg).iter(SVG_NS + "text")
                if el.text in ("Mon", "Wed", "Fri")
            }
            self.assertEqual(set(labels), {"Mon", "Wed", "Fri"})
            self.assertEqual([labels[d][1] for d in ("Mon", "Wed", "Fri")], [40 + 1 * 15 + 9, 40 + 3 * 15 + 9, 40 + 5 * 15 + 9])
            for x, _ in labels.values():
                self.assertLess(x, LAYOUTS[layout].left)
            self.assertIn("text-anchor:end", svg)

    def test_legend_repeats_the_five_levels_between_less_and_more(self):
        for name, theme, animated, layout, svg in self.each():
            root = ET.fromstring(svg)
            texts = {el.text: el for el in root.iter(SVG_NS + "text")}
            less, more = texts["Less"], texts["More"]
            row_y = float(less.get("y"))
            self.assertEqual(float(more.get("y")), row_y)
            self.assertGreater(row_y, GRID_BOTTOM)  # below the grid, on the footer row
            cells = sorted(
                (s for s in cell_rects(root) if float(s.get("y")) + 9 == row_y), key=lambda s: float(s.get("x"))
            )
            self.assertEqual(len(cells), 5)
            xs = [float(s.get("x")) for s in cells]
            self.assertTrue(all(float(less.get("x")) < x < float(more.get("x")) for x in xs))
            self.assertEqual(xs, sorted(set(xs)))
            opacities = [resolved_opacity(root, s) for s in cells]
            self.assertEqual(opacities, sorted(set(opacities)))
            self.assertEqual([len(glyphs_on(root, s)) for s in cells], [0, 1, 1, 1, 1])
            width = float(root.get("viewBox").split()[2])
            self.assertEqual(float(more.get("x")), width - LAYOUTS[layout].margin)  # right-aligned
            # Less and More keep the same breathing room to the squares (monospace advance ~0.6em)
            advance = 0.6 * LAYOUTS[layout].label
            gap_before = xs[0] - float(less.get("x"))  # Less is end-anchored
            gap_after = (float(more.get("x")) - 4 * advance) - (xs[-1] + CELL)  # More is end-anchored
            self.assertGreaterEqual(gap_before, 5)
            self.assertGreaterEqual(gap_after, gap_before - 0.01)

    def test_month_labels_sit_above_the_first_week_of_each_month(self):
        for name, theme, animated, layout, svg in self.each():
            left = LAYOUTS[layout].left
            labels = [
                (round((float(el.get("x")) - left) / 15), el.text)
                for el in ET.fromstring(svg).iter(SVG_NS + "text")
                if el.get("y") == "24"
            ]
            self.assertEqual(labels, FIXTURE_MONTHS if layout == "desktop" else MOBILE_MONTHS)

    def test_colliding_leading_month_label_is_dropped(self):
        # a first week starting on the last days of October, the next week already in November
        weeks = (
            (Day("2025-10-30", 1, 1), Day("2025-10-31", 0, 0)),
            (Day("2025-11-02", 0, 0),) * 7,
            (Day("2025-11-09", 0, 0),) * 7,
            (Day("2025-11-16", 0, 0),) * 7,
        )
        svg = render_calendar(Calendar(1, weeks), LIGHT, False, "2025-11-20")
        labels = [el.text for el in ET.fromstring(svg).iter(SVG_NS + "text") if el.get("class") == "m"]
        self.assertEqual(labels, ["Nov"])

    def test_footer_text_and_position(self):
        for name, theme, animated, layout, svg in self.each():
            root = ET.fromstring(svg)
            texts = [(el.text, float(el.get("x")), float(el.get("y"))) for el in root.iter(SVG_NS + "text")]
            left = LAYOUTS[layout].left
            footer = sorted((t for t in texts if t[1] == left and t[2] > GRID_BOTTOM), key=lambda t: t[2])
            if layout == "desktop":
                self.assertEqual(
                    footer, [("4,003 contributions in the last year · updated 2026-11-06", 44, 40 + 7 * 15 + 22)]
                )
            else:
                # wrapped: full-year total, then the update date on the legend row
                self.assertEqual(
                    footer,
                    [
                        ("4,003 contributions in the last year", 34, 40 + 7 * 15 + 22),
                        ("updated 2026-11-06", 34, 40 + 7 * 15 + 44),
                    ],
                )

    def test_mobile_is_sized_for_a_phone_column(self):
        root = ET.fromstring(render_calendar(self.cal, DARK, False, "2026-11-06", "mobile"))
        width = float(root.get("viewBox").split()[2])
        desktop = float(ET.fromstring(render_calendar(self.cal, DARK, False, "2026-11-06")).get("viewBox").split()[2])
        # in the same phone column a square is ~2x as large on screen as in the desktop image
        self.assertGreaterEqual(desktop / width, 1.95)

    def test_animated_variants_have_one_head_per_week(self):
        for name, theme, animated, layout, svg in self.each():
            if not animated:
                continue
            root = ET.fromstring(svg)
            heads = [el for el in root.iter(SVG_NS + "rect") if el.get("class") == "hd"]
            self.assertEqual(len(heads), 53 if layout == "desktop" else 26)
            self.assertTrue(all(h.get("width") == "12" and h.get("height") == "45" for h in heads))
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

    def test_glow_covers_the_glyphs_of_level_four_days_only_in_dark(self):
        for layout in ("desktop", "mobile"):
            for animated in (False, True):
                with self.subTest(f"{layout} animated={animated}"):
                    root = ET.fromstring(render_calendar(self.cal, DARK, animated, "2026-11-06", layout))
                    parents = parent_map(root)
                    lay = LAYOUTS[layout]
                    seen = set()
                    for day, x, y in day_cells(self.cal, layout):
                        if day.level == 0:
                            continue
                        # the glyph sits at the centre of its square, GLYPH_BASELINE below the top
                        (glyph,) = [
                            t
                            for t in glyph_texts(root)
                            if (float(t.get("x")), float(t.get("y"))) == (x + CELL / 2, y + GLYPH_BASELINE)
                        ]
                        self.assertEqual(filtered(parents, glyph), day.level == 4, day)
                        seen.add(day.level)
                    self.assertEqual(seen, {1, 2, 3, 4})
                    # the day squares themselves glow exactly with their glyphs
                    for day, x, y in day_cells(self.cal, layout):
                        (square,) = [s for s in grid_squares(root) if (float(s.get("x")), float(s.get("y"))) == (x, y)]
                        self.assertEqual(filtered(parents, square), day.level == 4, day)

    def test_no_glow_in_light(self):
        for layout in ("desktop", "mobile"):
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
