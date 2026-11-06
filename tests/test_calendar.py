import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import xml.etree.ElementTree as ET
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
    return [el for el in root.iter(SVG_NS + "text") if (el.get("class") or "").startswith("d")]


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
                "4,003 contributions in the last year as of 2026-11-06, drawn as green matrix rain; "
                "brighter glyphs mark busier days.",
            )
            self.assertEqual(root.get("viewBox"), "0 0 835 180")

    def test_one_glyph_per_day(self):
        for name, theme, animated, svg in self.each():
            self.assertEqual(len(glyph_cells(ET.fromstring(svg))), FIXTURE_DAYS)

    def test_glyph_grid_geometry_and_levels(self):
        root = ET.fromstring(render_calendar(self.cal, LIGHT, False, "2026-11-06"))
        cells = glyph_cells(root)
        # first week has 3 days and sits in the bottom three rows; the second week fills all seven
        first = cells[0]
        self.assertEqual((float(first.get("x")), float(first.get("y"))), (26.5, 40 + 4 * 15 + 11))
        fourth = cells[3]
        self.assertEqual(float(fourth.get("x")), 41.5)
        self.assertEqual(float(fourth.get("y")), 40 + 0 * 15 + 11)
        # day 1 (count 7) is level 2, day 3 (count 21) level 4
        self.assertEqual(cells[1].get("class"), "d2")
        self.assertEqual(cells[3].get("class"), "d4")
        # last week: 4 days from the top row, at week column 52
        last = cells[-1]
        self.assertEqual((float(last.get("x")), float(last.get("y"))), (20 + 52 * 15 + 6.5, 40 + 3 * 15 + 11))

    def test_level_opacities(self):
        svg = render_calendar(self.cal, LIGHT, False, "2026-11-06")
        for level, opacity in enumerate(["0.12", "0.35", "0.55", "0.78", "1"]):
            self.assertRegex(svg, rf"\.d{level}\{{fill-opacity:{re.escape(opacity)}\}}")

    def test_glyph_is_chosen_per_date_from_the_matrix_set(self):
        a = render_calendar(self.cal, DARK, False, "2026-11-06")
        b = render_calendar(self.cal, DARK, True, "2026-11-07")
        glyphs = lambda svg: [c.text for c in glyph_cells(ET.fromstring(svg))]
        self.assertEqual(glyphs(a), glyphs(b))
        from generator.svg import GLYPHS

        self.assertTrue(all(g in GLYPHS for g in glyphs(a)))
        self.assertGreater(len(set(glyphs(a))), 10)

    def test_month_labels_sit_above_the_first_week_of_each_month(self):
        for name, theme, animated, svg in self.each():
            labels = [
                (round((float(el.get("x")) - 20) / 15), el.text)
                for el in ET.fromstring(svg).iter(SVG_NS + "text")
                if el.get("class") == "m"
            ]
            self.assertEqual(labels, FIXTURE_MONTHS)
            for el in ET.fromstring(svg).iter(SVG_NS + "text"):
                if el.get("class") == "m":
                    self.assertEqual(el.get("y"), "24")

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
            footer = [el for el in root.iter(SVG_NS + "text") if el.get("class") in ("f", "u")]
            texts = {el.get("class"): el for el in footer}
            self.assertEqual(texts["f"].text, "4,003 contributions in the last year")
            self.assertEqual(texts["u"].text, "updated 2026-11-06")
            self.assertEqual(texts["f"].get("y"), str(40 + 7 * 15 + 22))
            self.assertEqual(texts["u"].get("y"), str(40 + 7 * 15 + 22))
            self.assertEqual((texts["f"].get("x"), texts["u"].get("x")), ("20", "815"))
            self.assertIn("text-anchor:end", svg)

    def test_animated_variants_have_one_head_per_week(self):
        for name, theme, animated, svg in self.each():
            if not animated:
                continue
            root = ET.fromstring(svg)
            heads = [el for el in root.iter(SVG_NS + "rect") if el.get("class") == "hd"]
            self.assertEqual(len(heads), 53)
            self.assertTrue(all(h.get("width") == "13" and h.get("height") == "45" for h in heads))
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
        self.assertEqual(stops, [("#00ff00", "0"), ("#00ff00", "0.55"), ("#00ff00", "0")])

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

    def test_glow_covers_only_level_four_glyphs(self):
        root = ET.fromstring(render_calendar(self.cal, DARK, False, "2026-11-06"))
        glowing = [g for g in root.iter(SVG_NS + "g") if g.get("filter") == "url(#glow)"]
        self.assertEqual(len(glowing), 1)
        classes = {t.get("class") for t in glowing[0].iter(SVG_NS + "text")}
        self.assertEqual(classes, {"d4"})

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
        res = run_cli("--login", "x", "--fixture", str(FIXTURE), "--out", str(self.out))
        self.assertEqual(res.returncode, 0, res.stderr)
        text = (self.out / "calendar-dark.svg").read_text(encoding="utf-8")
        self.assertRegex(text, r"updated \d{4}-\d{2}-\d{2}<")

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

    def test_invalid_date_is_a_usage_error(self):
        for value in ("2026-13-01", "20261106", "tomorrow"):
            with self.subTest(value):
                res = run_cli("--login", "x", "--fixture", str(FIXTURE), "--out", str(self.out), "--date", value)
                self.assertEqual(res.returncode, 2)
                self.assertEqual(self.files(), [])

    def test_calendar_is_not_part_of_the_static_asset_registry(self):
        from generator.__main__ import ASSETS

        self.assertFalse([rel for rel, _ in ASSETS if "calendar" in rel])


if __name__ == "__main__":
    unittest.main()
