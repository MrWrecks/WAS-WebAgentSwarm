import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Build machine-binding patterns without embedding real machine paths in publishable docs/config.
FORBIDDEN = [
    re.compile("wsl" + r"\.localhost", re.I),
    re.compile("WSL" + r"_[A-Za-z0-9_.-]+", re.I),
    re.compile(r"[A-Za-z]:\\Users\\(?!<)[^\\\s]+"),
]


class PortabilityTests(unittest.TestCase):
    def test_publishable_tree_has_no_machine_bound_workspace_paths(self):
        offenders = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if ".git" in path.parts or "node_modules" in path.parts or "__pycache__" in path.parts:
                continue
            if path == ROOT / "scripts" / "release_check.py" or path == Path(__file__).resolve():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if any(pattern.search(text) for pattern in FORBIDDEN):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual([], offenders, "machine-bound workspace paths found: " + ", ".join(sorted(offenders)))


if __name__ == "__main__":
    unittest.main()
