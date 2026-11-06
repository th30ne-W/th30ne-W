import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from generator.hero import render_hero
from generator.theme import DARK, LIGHT

SVG_NS = "{http://www.w3.org/2000/svg}"
BUDGET = 65_536

# (asset path, theme, layout, animated)
VARIANTS = [
    ("assets/hero/hero-dark.svg", DARK, "desktop", True),
    ("assets/hero/hero-light.svg", LIGHT, "desktop", True),
    ("assets/hero/hero-dark-static.svg", DARK, "desktop", False),
    ("assets/hero/hero-light-static.svg", LIGHT, "desktop", False),
    ("assets/hero/hero-mobile-dark.svg", DARK, "mobile", True),
    ("assets/hero/hero-mobile-light.svg", LIGHT, "mobile", True),
    ("assets/hero/hero-mobile-dark-static.svg", DARK, "mobile", False),
    ("assets/hero/hero-mobile-light-static.svg", LIGHT, "mobile", False),
]

TITLE = "William Nunes \u2014 matrix rain banner"
DESC = (
    "Name plate for William Nunes: Where AI agents meet real work. "
    "Focus areas: AI Agents, Workflow Automation, Secure by Design. "
    "Tagline: AI agents in production."
)


class HeroVariantTests(unittest.TestCase):
    def each(self):
        for path, theme, layout, animated in VARIANTS:
            with self.subTest(path):
                yield path, theme, layout, animated, render_hero(theme, layout, animated)

    def test_parses_with_title_and_desc(self):
        for path, theme, layout, animated, svg in self.each():
            root = ET.fromstring(svg)
            self.assertEqual(root.find(SVG_NS + "title").text, TITLE)
            self.assertEqual(root.find(SVG_NS + "desc").text, DESC)
            # mobile: 380 tall in the brief, 410 so the tagline has room between the chips and the plate border
            self.assertEqual(root.get("viewBox"), "0 0 840 300" if layout == "desktop" else "0 0 420 410")

    def test_within_size_budget(self):
        for path, theme, layout, animated, svg in self.each():
            self.assertLessEqual(len(svg.encode("utf-8")), BUDGET)

    def test_is_self_contained(self):
        for path, theme, layout, animated, svg in self.each():
            self.assertNotIn("<script", svg)
            self.assertNotIn("href=", svg)
            for match in re.finditer(r"url\(", svg):
                rest = svg[match.start():]
                self.assertTrue(rest.startswith("url(data:") or rest.startswith("url(#"), rest[:30])

    def test_embeds_both_orbitron_weights(self):
        for path, theme, layout, animated, svg in self.each():
            self.assertIn("font-weight:500;", svg)
            self.assertIn("font-weight:700;", svg)

    def test_motion_only_in_animated_variants(self):
        for path, theme, layout, animated, svg in self.each():
            for word in ("@keyframes", "animation"):
                if animated:
                    self.assertIn(word, svg)
                else:
                    self.assertNotIn(word, svg)

    def test_cursor_blink_is_animated_only(self):
        for path, theme, layout, animated, svg in self.each():
            self.assertEqual("@keyframes b{" in svg, animated)

    def test_hero_markup_avoids_rain_names(self):
        # the rain primitive owns classes c/h/o1-o9 and keyframes `f`; the hero adds none of its own
        for path, theme, layout, animated, svg in self.each():
            self.assertEqual(svg.count("@keyframes f{"), 1 if animated else 0)
            self.assertEqual(svg.count('class="c"'), 48 if layout == "desktop" else 24)

    def test_palette_matches_theme(self):
        for path, theme, layout, animated, svg in self.each():
            other = LIGHT if theme is DARK else DARK
            self.assertIn(theme.accent, svg)
            self.assertNotIn(other.accent, svg)

    def test_glow_only_in_dark(self):
        for path, theme, layout, animated, svg in self.each():
            self.assertEqual('filter="url(#glow)"' in svg, theme is DARK)

    def test_text_content(self):
        for path, theme, layout, animated, svg in self.each():
            root = ET.fromstring(svg)
            texts = [
                "".join(el.itertext()) for el in root.iter(SVG_NS + "text") if el.get("class") != "c"
            ]
            self.assertIn("AI Agents", texts)
            self.assertIn("Workflow Automation", texts)
            self.assertIn("Secure by Design", texts)
            self.assertIn("> AI agents in production", texts)
            if layout == "desktop":
                self.assertIn("WILLIAM NUNES", texts)
                self.assertIn("Where AI agents meet real work.", texts)
            else:
                for line in ("WILLIAM", "NUNES", "Where AI agents", "meet real work."):
                    self.assertIn(line, texts)
            for text in texts:
                self.assertTrue(text.isascii(), text)

    def test_deterministic(self):
        for path, theme, layout, animated, svg in self.each():
            self.assertEqual(svg, render_hero(theme, layout, animated))

    def test_rejects_unknown_layout(self):
        with self.assertRaises(ValueError):
            render_hero(DARK, "tablet", True)


class HeroRegistryTests(unittest.TestCase):
    def test_build_writes_the_eight_heroes_through_the_cli(self):
        repo = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("generator", "fonts"):
                shutil.copytree(repo / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
            result = subprocess.run(
                [sys.executable, "-m", "generator", "build"], cwd=root, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for path, theme, layout, animated in VARIANTS:
                with self.subTest(path):
                    self.assertTrue((root / path).is_file())
                    self.assertEqual((root / path).read_text("utf-8"), render_hero(theme, layout, animated))


if __name__ == "__main__":
    unittest.main()
