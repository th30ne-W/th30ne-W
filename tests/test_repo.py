import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class ImageRefs(HTMLParser):
    """Collects every <img> and <source> in the README."""

    def __init__(self):
        super().__init__()
        self.imgs, self.paths = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img":
            self.imgs.append(attrs)
            self.paths.append(attrs.get("src", ""))
        elif tag == "source":
            self.paths.append(attrs.get("srcset", ""))


class ReadmeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.refs = ImageRefs()
        cls.refs.feed((REPO / "README.md").read_text(encoding="utf-8"))

    def test_every_local_image_path_exists(self):
        local = [p for p in self.refs.paths if not p.startswith("https://")]
        self.assertTrue(local)
        for path in local:
            with self.subTest(path):
                self.assertTrue((REPO / path).is_file(), f"README points at a missing file: {path}")

    def test_every_image_has_alt_text(self):
        for img in self.refs.imgs:
            with self.subTest(img.get("src")):
                self.assertTrue((img.get("alt") or "").strip(), f"missing alt: {img.get('src')}")


class WorkflowTests(unittest.TestCase):
    def test_actions_are_pinned_to_commit_shas(self):
        uses = [
            (wf.name, ref)
            for wf in sorted((REPO / ".github" / "workflows").glob("*.yml"))
            for ref in re.findall(r"^\s*-?\s*uses:\s*(\S+)", wf.read_text(encoding="utf-8"), re.M)
        ]
        self.assertTrue(uses)
        for name, ref in uses:
            with self.subTest(f"{name}: {ref}"):
                self.assertRegex(ref, r"@[0-9a-f]{40}$", "pin the action to a full commit SHA")


if __name__ == "__main__":
    unittest.main()
