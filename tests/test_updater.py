import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from updater import parse_version


class VersionTests(unittest.TestCase):
    def test_version_parser(self):
        self.assertGreater(parse_version("v1.3.0"), parse_version("1.2.9"))
        self.assertEqual(parse_version("1.3.0"), (1, 3, 0))


if __name__ == "__main__":
    unittest.main()
