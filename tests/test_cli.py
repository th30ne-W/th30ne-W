import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from generator.__main__ import ASSETS, check_assets, write_assets

REPO = Path(__file__).resolve().parent.parent


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "generator", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


class RegistryAndCliTests(unittest.TestCase):
    def test_registry_is_empty_in_core(self):
        self.assertEqual(ASSETS, [])

    def test_build_check_passes_with_empty_registry(self):
        res = run_cli("build", "--check")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout, "")

    def test_build_succeeds_with_empty_registry(self):
        res = run_cli("build")
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_unknown_command_fails(self):
        self.assertNotEqual(run_cli("bogus").returncode, 0)


class BuildSemanticsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.assets = [
            ("assets/a/one.svg", lambda: "<svg>one é</svg>"),
            ("assets/b.svg", lambda: "<svg>two</svg>"),
        ]

    def test_write_creates_dirs_and_exact_utf8_bytes(self):
        write_assets(self.assets, self.root)
        self.assertEqual((self.root / "assets/a/one.svg").read_bytes(), "<svg>one é</svg>".encode("utf-8"))
        self.assertEqual((self.root / "assets/b.svg").read_bytes(), b"<svg>two</svg>")

    def test_check_reports_nothing_when_in_sync(self):
        write_assets(self.assets, self.root)
        self.assertEqual(check_assets(self.assets, self.root), [])

    def test_check_reports_missing_files(self):
        self.assertEqual(check_assets(self.assets, self.root), ["assets/a/one.svg", "assets/b.svg"])

    def test_check_reports_only_differing_files(self):
        write_assets(self.assets, self.root)
        (self.root / "assets/b.svg").write_text("<svg>stale</svg>", encoding="utf-8")
        self.assertEqual(check_assets(self.assets, self.root), ["assets/b.svg"])

    def test_check_does_not_write(self):
        check_assets(self.assets, self.root)
        self.assertFalse((self.root / "assets").exists())

    def test_newline_bytes_are_not_translated(self):
        write_assets([("x.svg", lambda: "a\nb\n")], self.root)
        self.assertEqual((self.root / "x.svg").read_bytes(), b"a\nb\n")
        self.assertEqual(check_assets([("x.svg", lambda: "a\nb\n")], self.root), [])


if __name__ == "__main__":
    unittest.main()
