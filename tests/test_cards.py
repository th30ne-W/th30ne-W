import dataclasses
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from generator.cards import render_card
from generator.content import PROJECTS, Project
from generator.theme import DARK, LIGHT, THEMES

REPO = Path(__file__).resolve().parent.parent

SVG_NS = "{http://www.w3.org/2000/svg}"
BUDGET = 20_480
MAX_LINES = 5
MAX_CHARS = 54

# Independent source of truth: slug -> (title, kind, url, description, tags), as written in the site's _data/projects.yml
EXPECTED = {
    "dockstay": (
        "DockStay",
        "app",
        "https://github.com/th30ne-W/DockStay",
        "Menu-bar macOS app that keeps the Dock pinned to the display you choose and recovers automatically "
        "after sleep, display changes or disconnects.",
        "#Swift #SwiftUI #macOS #CI",
    ),
    "nude-notes": (
        "Nude Notes",
        "app",
        "https://github.com/th30ne-W/Nude-Notes",
        "Translucent menu-bar Markdown notes for macOS: global shortcut, multi-window notes, pinning, "
        "search and auto-save to plain files.",
        "#Tauri #Rust #React #TypeScript #macOS",
    ),
    "laya": (
        "Laya Computer Use",
        "private",
        "https://williamnunes.com/projects/",
        "Local macOS computer-use agent in which every action decision comes from an on-device Apple Silicon "
        "model, paired with capture tooling and an offline evaluation harness that scores decision strategies.",
        "#Python #macOS #MLX #AI #Automation",
    ),
    "homelab": (
        "Homelab",
        "private",
        "https://williamnunes.com/projects/",
        "Single-node Proxmox lab running n8n, Home Assistant and an AI agent, managed with Ansible: "
        "snapshot-guarded weekly updates, generated state snapshots, ADRs and runbooks.",
        "#Proxmox #Ansible #n8n #HomeAssistant #Runbooks",
    ),
}


def texts_at(root, y, theme_fill=None):
    """Text of every non-rain <text> whose baseline is y (and, optionally, whose fill matches)."""
    return [
        "".join(el.itertext())
        for el in root.iter(f"{SVG_NS}text")
        if float(el.get("y")) == y and (theme_fill is None or el.get("fill") == theme_fill)
    ]


def description_lines(root, theme):
    """Description lines: left-aligned body text between the title (y=40) and the tags (y=200)."""
    found = [
        (float(el.get("y")), "".join(el.itertext()))
        for el in root.iter(f"{SVG_NS}text")
        if el.get("x") == "20" and el.get("fill") == theme.text
    ]
    found.sort()
    ys = [y for y, _ in found]
    assert all(40 < y < 200 for y in ys), ys
    assert all(b - a == 18 for a, b in zip(ys, ys[1:])), ys
    return [text for _, text in found]



class ContentTests(unittest.TestCase):
    def test_projects_are_the_four_cards_in_order(self):
        self.assertEqual([p.slug for p in PROJECTS], ["dockstay", "nude-notes", "laya", "homelab"])

    def test_project_text_is_verbatim_from_the_site(self):
        for p in PROJECTS:
            with self.subTest(p.slug):
                self.assertEqual((p.title, p.kind, p.url, p.description, p.tags), EXPECTED[p.slug])


class CardTests(unittest.TestCase):
    def each(self):
        for project in PROJECTS:
            for theme in THEMES:
                with self.subTest(f"{project.slug}-{theme.name}"):
                    yield project, theme, render_card(project, theme)

    def test_parses_with_title_and_desc(self):
        for project, theme, svg in self.each():
            root = ET.fromstring(svg)
            self.assertEqual(root.get("viewBox"), "0 0 410 220")
            self.assertEqual(root.find(f"{SVG_NS}title").text, project.title)
            self.assertEqual(root.find(f"{SVG_NS}desc").text, project.description)

    def test_within_size_budget(self):
        for project, theme, svg in self.each():
            self.assertLessEqual(len(svg.encode("utf-8")), BUDGET)

    def test_is_self_contained(self):
        for project, theme, svg in self.each():
            self.assertNotIn("<script", svg)
            self.assertNotIn("href=", svg)
            for match in re.finditer(r"url\(", svg):
                rest = svg[match.start() :]
                self.assertTrue(rest.startswith("url(data:") or rest.startswith("url(#"), rest[:30])

    def test_has_no_motion(self):
        for project, theme, svg in self.each():
            for word in ("@keyframes", "animation", "<animate", "<set"):
                self.assertNotIn(word, svg)

    def test_embeds_both_orbitron_weights(self):
        for project, theme, svg in self.each():
            self.assertIn("font-weight:500;", svg)
            self.assertIn("font-weight:700;", svg)

    def test_palette_matches_theme(self):
        for project, theme, svg in self.each():
            other = LIGHT if theme is DARK else DARK
            self.assertIn(theme.accent, svg)
            self.assertNotIn(other.accent, svg)

    def test_glow_only_in_dark(self):
        for project, theme, svg in self.each():
            self.assertEqual('filter="url(#glow)"' in svg, theme is DARK)

    def test_description_wraps_losslessly_within_limits(self):
        for project, theme, svg in self.each():
            lines = description_lines(ET.fromstring(svg), theme)
            self.assertTrue(1 <= len(lines) <= MAX_LINES)
            for line in lines:
                self.assertLessEqual(len(line), MAX_CHARS)
            self.assertEqual(" ".join(lines), project.description)

    def test_orbitron_text_is_uppercase_ascii(self):
        for project, theme, svg in self.each():
            root = ET.fromstring(svg)
            title = texts_at(root, 40, theme.accent)
            kind = texts_at(root, 26, theme.accent)
            self.assertEqual(title, [project.title.upper()])
            self.assertEqual(len(kind), 1)
            for text in title + kind:
                self.assertTrue(text.isascii() and text == text.upper(), text)

    def test_tags_are_shown_verbatim(self):
        for project, theme, svg in self.each():
            self.assertEqual(texts_at(ET.fromstring(svg), 200, theme.muted), [project.tags])

    def test_kind_label_matches_project(self):
        for project, theme, svg in self.each():
            expected = "APP" if project.slug in ("dockstay", "nude-notes") else "PRIVATE"
            self.assertEqual(texts_at(ET.fromstring(svg), 26, theme.accent), [expected])

    def test_deterministic(self):
        for project, theme, svg in self.each():
            self.assertEqual(svg, render_card(project, theme))

    def test_markup_is_escaped(self):
        project = Project("x", "A&B <c>", "app", "R&D <b> tags", "#x", "https://example.com/")
        root = ET.fromstring(render_card(project, DARK))
        self.assertEqual(root.find(f"{SVG_NS}title").text, "A&B <c>")

    def test_overlong_description_raises(self):
        long = Project("x", "X", "app", " ".join(["word"] * 81)[:400], "#x", "https://example.com/")
        self.assertEqual(len(long.description), 400)
        with self.assertRaises(ValueError):
            render_card(long, DARK)

    def test_single_overlong_word_raises(self):
        word = Project("x", "X", "app", "x" * 400, "#x", "https://example.com/")
        with self.assertRaisesRegex(ValueError, "54"):
            render_card(word, DARK)
        just_over = Project("x", "X", "app", "ok " + "x" * 55, "#x", "https://example.com/")
        with self.assertRaises(ValueError):
            render_card(just_over, DARK)
        exact = Project("x", "X", "app", "x" * 54, "#x", "https://example.com/")
        self.assertEqual(description_lines(ET.fromstring(render_card(exact, DARK)), DARK), ["x" * 54])

    def test_five_lines_is_the_limit(self):
        # 5 lines of 54 chars fit; a sixth does not
        five = Project("x", "X", "app", " ".join(["a" * 53] * 5), "#x", "https://example.com/")
        self.assertEqual(len(description_lines(ET.fromstring(render_card(five, DARK)), DARK)), 5)
        six = dataclasses.replace(five, description=" ".join(["a" * 53] * 6))
        with self.assertRaises(ValueError):
            render_card(six, DARK)


class CardRegistryTests(unittest.TestCase):
    def test_build_writes_the_eight_cards_through_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("generator", "fonts"):
                shutil.copytree(REPO / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
            result = subprocess.run(
                [sys.executable, "-m", "generator", "build"], cwd=root, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for project in PROJECTS:
                for theme in THEMES:
                    path = root / "assets" / "cards" / f"{project.slug}-{theme.name}.svg"
                    self.assertTrue(path.is_file(), path)
                    self.assertEqual(path.read_text("utf-8"), render_card(project, theme))


if __name__ == "__main__":
    unittest.main()
