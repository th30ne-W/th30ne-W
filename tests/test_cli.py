import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Runs the real CLI entry point (`generator.__main__.main`) against an isolated,
# non-empty registry and a temp root, so nothing under the repo is touched.
DRIVER = """
import sys
from pathlib import Path
import generator.__main__ as cli

cli.ROOT = Path(sys.argv[1])
cli.ASSETS[:] = [
    ("assets/a/one.svg", lambda: "<svg>one \\u00e9</svg>"),
    ("assets/b.svg", lambda: "a\\nb\\n"),
]
sys.exit(cli.main(sys.argv[2:]))
"""

ONE = "<svg>one é</svg>".encode("utf-8")
TWO = b"a\nb\n"


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "generator", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


class RealRegistryTests(unittest.TestCase):
    def test_build_check_passes_on_committed_assets(self):
        res = run_cli("build", "--check")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(res.stdout, "")

    def test_unknown_command_fails(self):
        self.assertNotEqual(run_cli("bogus").returncode, 0)


class FixtureRegistryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-c", DRIVER, str(self.root), *args],
            cwd=REPO,
            capture_output=True,
            text=True,
        )

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_build_writes_exact_utf8_bytes_and_creates_dirs(self):
        res = self.cli("build")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.snapshot(), {"assets/a/one.svg": ONE, "assets/b.svg": TWO})

    def test_check_passes_silently_when_in_sync(self):
        self.cli("build")
        res = self.cli("build", "--check")
        self.assertEqual((res.returncode, res.stdout), (0, ""), res.stderr)

    def test_check_reports_every_missing_file_and_writes_nothing(self):
        res = self.cli("build", "--check")
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertEqual(res.stdout, "stale: assets/a/one.svg\nstale: assets/b.svg\n")
        self.assertEqual(self.snapshot(), {})

    def test_check_reports_missing_and_differing_without_touching_files(self):
        self.cli("build")
        (self.root / "assets/a/one.svg").unlink()
        (self.root / "assets/b.svg").write_bytes(b"stale")
        before = self.snapshot()
        res = self.cli("build", "--check")
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertEqual(res.stdout, "stale: assets/a/one.svg\nstale: assets/b.svg\n")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(before, {"assets/b.svg": b"stale"})

    def test_check_lists_only_the_differing_file(self):
        self.cli("build")
        (self.root / "assets/b.svg").write_bytes(b"stale")
        res = self.cli("build", "--check")
        self.assertEqual((res.returncode, res.stdout), (1, "stale: assets/b.svg\n"), res.stderr)
        self.assertEqual(self.snapshot()["assets/b.svg"], b"stale")


if __name__ == "__main__":
    unittest.main()
