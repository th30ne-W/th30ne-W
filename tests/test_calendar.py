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


def glyph_cells(root):
    return [el for el in root.iter(SVG_NS + "text") if "d" in (el.get("class") or "").split()]


def squares_of(root):
    """Day squares (grid first, legend after) in document order, glow-wrapped ones included."""
    return [el for el in root.iter(SVG_NS + "rect") if re.fullmatch(r"c\d", el.get("class") or "")]


def ancestors(parents, el):
    while el in parents:
        el = parents[el]
        yield el


class RenderCalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cal = parse_calendar(load_payload())

    def each(self):
        for name, theme, animated in VARIANTS:
            with self.subTest(name):
                yield name, theme, animated, render_calendar(self.cal, theme, animated, "2026-11-06")

    def test_parses_with_title_and_desc(self):
        for name, theme, animated, svg in self.each():
            root = ET.fromstring(svg)
            self.assertEqual(root.find(SVG_NS + "title").text, "Contribution calendar")
            self.assertEqual(
                root.find(SVG_NS + "desc").text,
                "4,003 contributions in the last year as of 2026-11-06. Each square is one day; "
                "brighter squares with matrix glyphs mark busier days, empty days are dim.",
            )
            self.assertEqual(root.get("viewBox"), "0 0 856 180")

    def test_one_square_per_day_and_a_glyph_only_on_active_days(self):
        active = sum(1 for week in self.cal.weeks for day in week if day.level >= 1)
        for name, theme, animated, svg in self.each():
            root = ET.fromstring(svg)
            parents = {child: parent for parent in root.iter() for child in parent}
            in_legend = lambda el: any(p.get("class") == "legend" for p in ancestors(parents, el))
            squares = [el for el in squares_of(root) if not in_legend(el)]
            glyphs = [el for el in glyph_cells(root) if not in_legend(el)]
            self.assertEqual(len(squares), FIXTURE_DAYS)
            self.assertEqual(len(glyphs), active)
            self.assertTrue(0 < active < FIXTURE_DAYS)
            self.assertTrue(all(g.get("class").split()[1] != "d0" for g in glyph_cells(root)))

    def test_square_grid_geometry_and_levels(self):
        root = ET.fromstring(render_calendar(self.cal, LIGHT, False, "2026-11-06"))
        cells = squares_of(root)
        # first week has 3 days and sits in the bottom three rows; the second week fills all seven
        first = cells[0]
        self.assertEqual((float(first.get("x")), float(first.get("y"))), (44, 40 + 4 * 15))
        self.assertEqual((first.get("width"), first.get("height"), first.get("rx")), ("12", "12", "2"))
        fourth = cells[3]
        self.assertEqual((float(fourth.get("x")), float(fourth.get("y"))), (59, 40))
        # day 1 (count 7) is level 2, day 3 (count 21) level 4
        self.assertEqual(cells[1].get("class"), "c2")
        self.assertEqual(cells[3].get("class"), "c4")
        # last week: 4 days from the top row, at week column 52
        last = cells[FIXTURE_DAYS - 1]
        self.assertEqual((float(last.get("x")), float(last.get("y"))), (44 + 52 * 15, 40 + 3 * 15))

    def test_level_fill_opacity_rises_strictly(self):
        for name, theme, animated, svg in self.each():
            opacities = [
                float(re.search(rf"\.c{level}\{{fill:{theme.accent};fill-opacity:([\d.]+)\}}", svg).group(1))
                for level in range(5)
            ]
            self.assertEqual(opacities, sorted(set(opacities)))
            self.assertGreaterEqual(opacities[0], 0.08)  # empty days stay faintly visible
            self.assertEqual(opacities[-1], 1.0)

    def test_glyph_is_chosen_per_date_from_the_matrix_set(self):
        a = render_calendar(self.cal, DARK, False, "2026-11-06")
        b = render_calendar(self.cal, DARK, True, "2026-11-07")
        glyphs = lambda svg: [c.text for c in glyph_cells(ET.fromstring(svg))]
        self.assertEqual(glyphs(a), glyphs(b))
        from generator.svg import GLYPHS

        self.assertTrue(all(g in GLYPHS for g in glyphs(a)))
        self.assertGreater(len(set(glyphs(a))), 10)

    def test_weekday_labels_sit_left_of_rows_two_four_and_six(self):
        for name, theme, animated, svg in self.each():
            labels = {
                el.text: (float(el.get("x")), float(el.get("y")))
                for el in ET.fromstring(svg).iter(SVG_NS + "text")
                if el.text in ("Mon", "Wed", "Fri")
            }
            self.assertEqual(set(labels), {"Mon", "Wed", "Fri"})
            self.assertEqual([labels[d][1] for d in ("Mon", "Wed", "Fri")], [40 + 1 * 15 + 9, 40 + 3 * 15 + 9, 40 + 5 * 15 + 9])
            for x, _ in labels.values():
                self.assertLess(x, 44)
            self.assertIn("text-anchor:end", svg)

    def test_legend_repeats_the_five_levels_between_less_and_more(self):
        for name, theme, animated, svg in self.each():
            root = ET.fromstring(svg)
            parents = {child: parent for parent in root.iter() for child in parent}
            texts = {el.text: el for el in root.iter(SVG_NS + "text")}
            less, more = texts["Less"], texts["More"]
            legend = parents[less]
            self.assertIs(parents[more], legend)
            squares = [el for el in legend.iter(SVG_NS + "rect")]
            self.assertEqual([el.get("class") for el in squares], [f"c{level}" for level in range(5)])
            self.assertEqual([g.get("class").split()[1] for g in glyph_cells(legend)], ["d1", "d2", "d3", "d4"])
            xs = [float(el.get("x")) for el in squares]
            self.assertEqual(xs, sorted(xs))
            self.assertLess(float(less.get("x")), xs[0])
            self.assertGreater(float(more.get("x")), xs[-1])
            self.assertEqual(more.get("x"), "836")  # right-aligned to the grid's right edge
            self.assertEqual(more.get("y"), str(40 + 7 * 15 + 22))

    def test_month_labels_sit_above_the_first_week_of_each_month(self):
        for name, theme, animated, svg in self.each():
            labels = [
                (round((float(el.get("x")) - 44) / 15), el.text)
                for el in ET.fromstring(svg).iter(SVG_NS + "text")
                if el.get("y") == "24"
            ]
            self.assertEqual(labels, FIXTURE_MONTHS)

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
        for name, theme, animated, svg in self.each():
            root = ET.fromstring(svg)
            footer = [el for el in root.iter(SVG_NS + "text") if el.get("class") == "f"]
            self.assertEqual(len(footer), 1)
            self.assertEqual(footer[0].text, "4,003 contributions in the last year · updated 2026-11-06")
            self.assertEqual((footer[0].get("x"), footer[0].get("y")), ("44", str(40 + 7 * 15 + 22)))

    def test_animated_variants_have_one_head_per_week(self):
        for name, theme, animated, svg in self.each():
            if not animated:
                continue
            root = ET.fromstring(svg)
            heads = [el for el in root.iter(SVG_NS + "rect") if el.get("class") == "hd"]
            self.assertEqual(len(heads), 53)
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
        for name, theme, animated, svg in self.each():
            if animated:
                continue
            self.assertNotIn("@keyframes", svg)
            self.assertNotIn("animation", svg)
            self.assertNotIn("linearGradient", svg)
            self.assertNotIn('class="hd"', svg)

    def test_within_size_budget(self):
        for name, theme, animated, svg in self.each():
            self.assertLessEqual(len(svg.encode("utf-8")), BUDGET)

    def test_is_self_contained(self):
        for name, theme, animated, svg in self.each():
            self.assertNotIn("<script", svg)
            self.assertNotIn("href=", svg)
            for match in re.finditer(r"url\(", svg):
                rest = svg[match.start():]
                self.assertTrue(rest.startswith("url(data:") or rest.startswith("url(#"), rest[:30])

    def test_theme_colours_and_glow(self):
        for name, theme, animated, svg in self.each():
            if theme is DARK:
                self.assertIn("#00ff00", svg)
                self.assertNotIn("#087a37", svg)
                self.assertIn('filter="url(#glow)"', svg)
                self.assertIn('<filter id="glow"', svg)
            else:
                self.assertIn("#087a37", svg)
                self.assertNotIn("#00ff00", svg)
                self.assertNotIn("glow", svg)

    def test_glow_covers_exactly_the_level_four_cells_in_dark(self):
        for animated in (False, True):
            root = ET.fromstring(render_calendar(self.cal, DARK, animated, "2026-11-06"))
            parents = {child: parent for parent in root.iter() for child in parent}

            def filtered(el):
                while el is not None:
                    if el.get("filter") == "url(#glow)":
                        return True
                    el = parents.get(el)
                return False

            squares = [el for el in root.iter(SVG_NS + "rect") if re.fullmatch(r"c\d", el.get("class") or "")]
            by_level = {}
            for el in squares:
                by_level.setdefault(el.get("class"), set()).add(filtered(el))
            self.assertEqual(by_level, {"c0": {False}, "c1": {False}, "c2": {False}, "c3": {False}, "c4": {True}})
            glyphs = glyph_cells(root)
            self.assertTrue(glyphs)
            for glyph in glyphs:
                self.assertEqual(filtered(glyph), "d4" in glyph.get("class").split(), glyph.get("class"))

    def test_no_glow_in_light(self):
        root = ET.fromstring(render_calendar(self.cal, LIGHT, False, "2026-11-06"))
        self.assertFalse([el for el in root.iter() if el.get("filter")])

    def test_render_is_deterministic(self):
        for name, theme, animated in VARIANTS:
            a = render_calendar(self.cal, theme, animated, "2026-11-06")
            b = render_calendar(parse_calendar(load_payload()), theme, animated, "2026-11-06")
            self.assertEqual(a, b)

    def test_generated_on_is_escaped(self):
        svg = render_calendar(self.cal, LIGHT, False, "<&>")
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

    def test_fixture_writes_the_four_variants(self):
        res = run_cli("--login", "x", "--fixture", str(FIXTURE), "--out", str(self.out), "--date", "2026-11-06")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(
            self.files(),
            ["calendar-dark-static.svg", "calendar-dark.svg", "calendar-light-static.svg", "calendar-light.svg"],
        )
        for path in self.out.iterdir():
            text = path.read_text(encoding="utf-8")
            self.assertIn("updated 2026-11-06", text)
            ET.fromstring(text)
        cal = parse_calendar(load_payload())
        self.assertEqual(
            (self.out / "calendar-light-static.svg").read_text(encoding="utf-8"),
            render_calendar(cal, LIGHT, False, "2026-11-06"),
        )
        self.assertIn("@keyframes", (self.out / "calendar-dark.svg").read_text(encoding="utf-8"))
        self.assertNotIn("@keyframes", (self.out / "calendar-dark-static.svg").read_text(encoding="utf-8"))

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
