import ast
import importlib
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent


class TestSeamTests(unittest.TestCase):
    """Tests reach the generator only through each module's `__all__`; the CLI only as a subprocess."""

    def test_tests_import_only_public_interfaces(self):
        for path in sorted(TESTS.glob("test_*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        with self.subTest(f"{path.name}:{node.lineno} import {alias.name}"):
                            self.assertFalse(
                                alias.name.split(".")[0] == "generator",
                                "use `from generator.<module> import <public name>`",
                            )
                elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "generator":
                    with self.subTest(f"{path.name}:{node.lineno} from {node.module}"):
                        self.assertNotEqual(node.module, "generator.__main__", "run the CLI as a subprocess")
                        public = set(getattr(importlib.import_module(node.module), "__all__", ()))
                        private = sorted(a.name for a in node.names if a.name not in public)
                        self.assertEqual(private, [], f"not in {node.module}.__all__")


if __name__ == "__main__":
    unittest.main()
