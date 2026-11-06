import random
import re
import unittest
import xml.etree.ElementTree as ET

from generator.fonts import font_face
from generator.svg import GLOW_FILTER, GLYPHS, document, esc, rain_columns
from generator.theme import DARK, LIGHT, THEMES

SVG_NS = "{http://www.w3.org/2000/svg}"


def rain(animated, theme=DARK, seed=1, count=6, width=105, height=200, glyphs=12, pitch=17.5):
    return rain_columns(random.Random(seed), count, width, height, glyphs, pitch, animated, theme)


def parse_rain(markup):
    return ET.fromstring(f'<svg xmlns="http://www.w3.org/2000/svg">{markup}</svg>')


class ThemeTests(unittest.TestCase):
    def test_palettes(self):
        self.assertEqual(
            (DARK.name, DARK.bg, DARK.accent, DARK.text, DARK.muted, DARK.glow),
            ("dark", "#0d0d0d", "#00ff00", "#e6ffe6", "#9fe9a8", True),
        )
        self.assertEqual(
            (LIGHT.name, LIGHT.bg, LIGHT.accent, LIGHT.text, LIGHT.muted, LIGHT.glow),
            ("light", "#eef2f1", "#087a37", "#0d0d0d", "#2f5d43", False),
        )
        self.assertEqual(THEMES, (DARK, LIGHT))


class EscapeTests(unittest.TestCase):
    def test_escapes_markup(self):
        self.assertEqual(esc("<a&b>"), "&lt;a&amp;b&gt;")


class DocumentTests(unittest.TestCase):
    def test_parses_with_title_and_desc(self):
        out = document(840, 300, "A <title>", "A & desc", ".x{fill:red}", '<rect width="1" height="1"/>')
        root = ET.fromstring(out)
        self.assertEqual(root.tag, SVG_NS + "svg")
        self.assertEqual(root.get("viewBox"), "0 0 840 300")
        self.assertEqual(root.get("role"), "img")
        self.assertEqual(root.get("aria-labelledby"), "t d")
        title = root.find(SVG_NS + "title")
        desc = root.find(SVG_NS + "desc")
        self.assertEqual((title.get("id"), title.text), ("t", "A <title>"))
        self.assertEqual((desc.get("id"), desc.text), ("d", "A & desc"))
        self.assertEqual(root.find(SVG_NS + "style").text, ".x{fill:red}")
        self.assertIsNotNone(root.find(SVG_NS + "rect"))

    def test_glow_filter_is_valid_markup(self):
        root = ET.fromstring(f'<svg xmlns="http://www.w3.org/2000/svg">{GLOW_FILTER}</svg>')
        flt = root.find(SVG_NS + "filter")
        self.assertEqual(flt.get("id"), "glow")
        self.assertEqual(flt.find(SVG_NS + "feGaussianBlur").get("stdDeviation"), "2")


class FontTests(unittest.TestCase):
    def test_font_face_700(self):
        self.assertTrue(
            font_face(700).startswith(
                "@font-face{font-family:Orbitron;font-weight:700;src:url(data:font/woff2;base64,"
            )
        )

    def test_font_face_500_ends_with_format(self):
        css = font_face(500)
        self.assertIn("font-weight:500;", css)
        self.assertTrue(css.endswith('format("woff2")}'))

    def test_font_face_rejects_other_weights(self):
        with self.assertRaises(ValueError):
            font_face(600)


class RainTests(unittest.TestCase):
    def test_still_has_no_animation(self):
        out = rain(animated=False)
        self.assertNotIn("animation", out)
        self.assertNotIn("@keyframes", out)

    def test_animated_uses_css_keyframes_only(self):
        out = rain(animated=True)
        self.assertIn("@keyframes f{from{transform:translateY(-180px)}to{transform:translateY(220px)}}", out)
        self.assertNotIn("<animate", out)
        self.assertNotIn("<script", out)

    def test_urls_are_data_or_fragment(self):
        for animated in (True, False):
            for url in re.findall(r"url\([^)]*", rain(animated)):
                self.assertTrue(url.startswith(("url(data:", "url(#")), url)

    def test_one_column_per_count_at_pitch_centres(self):
        root = parse_rain(rain(animated=True))
        cols = root.findall(SVG_NS + "text")
        self.assertEqual(len(cols), 6)
        for i, col in enumerate(cols):
            self.assertEqual(col.get("class"), "c")
            spans = col.findall(SVG_NS + "tspan")
            self.assertEqual(len(spans), 12)
            for span in spans:
                self.assertEqual(float(span.get("x")), i * 17.5 + 17.5 / 2)
                self.assertEqual(span.get("dy"), "15")

    def test_head_is_last_glyph_in_text_colour(self):
        theme = LIGHT
        root = parse_rain(rain(animated=False, theme=theme))
        style = root.find(SVG_NS + "style").text
        self.assertRegex(style, r"\.h\{fill:" + re.escape(theme.text) + r"\}")
        for col in root.findall(SVG_NS + "text"):
            spans = col.findall(SVG_NS + "tspan")
            self.assertEqual(spans[-1].get("class"), "h")
            self.assertTrue(all(s.get("class") != "h" for s in spans[:-1]))
            self.assertEqual(col.get("fill"), theme.accent)

    def test_opacity_classes_defined_and_used(self):
        root = parse_rain(rain(animated=False))
        style = root.find(SVG_NS + "style").text
        for n in range(1, 10):
            self.assertIn(f".o{n}{{", style)
        used = {s.get("class") for s in root.iter(SVG_NS + "tspan")}
        self.assertTrue({"o1", "h"} <= used)

    def test_glyphs_come_from_glyph_set(self):
        root = parse_rain(rain(animated=False))
        for span in root.iter(SVG_NS + "tspan"):
            self.assertIn(span.text, GLYPHS)

    def test_animated_columns_carry_duration_and_negative_delay(self):
        root = parse_rain(rain(animated=True))
        for col in root.findall(SVG_NS + "text"):
            m = re.fullmatch(r"animation-duration:(\d+\.\d)s;animation-delay:-(\d+\.?\d*)s", col.get("style"))
            self.assertIsNotNone(m, col.get("style"))
            dur, delay = float(m.group(1)), float(m.group(2))
            self.assertTrue(4.5 <= dur <= 9.5)
            self.assertTrue(0 <= delay <= dur)
            self.assertIsNone(col.get("transform"))

    def test_still_columns_have_integer_translate_in_range(self):
        root = parse_rain(rain(animated=False, height=200))
        for col in root.findall(SVG_NS + "text"):
            m = re.fullmatch(r"translate\(0 (-?\d+)\)", col.get("transform"))
            self.assertIsNotNone(m, col.get("transform"))
            self.assertTrue(-180 <= int(m.group(1)) <= 200)
            self.assertIsNone(col.get("style"))

    def test_deterministic_for_same_seed(self):
        self.assertEqual(rain(True, seed=7), rain(True, seed=7))
        self.assertNotEqual(rain(True, seed=7), rain(True, seed=8))

    def test_font_is_system_monospace(self):
        style = parse_rain(rain(False)).find(SVG_NS + "style").text
        self.assertIn("13px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", style)


if __name__ == "__main__":
    unittest.main()
