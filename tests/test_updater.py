import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from updater import external_installer_environment, parse_version


class VersionTests(unittest.TestCase):
    def test_version_parser(self):
        self.assertGreater(parse_version("v1.3.0"), parse_version("1.2.9"))
        self.assertEqual(parse_version("1.3.0"), (1, 3, 0))


class InstallerEnvironmentTests(unittest.TestCase):
    def test_private_pyinstaller_state_is_removed(self):
        source = {
            "PATH": r"C:\Windows\System32",
            "TEMP": r"C:\Temp",
            "_PYI_ARCHIVE_FILE": r"C:\Program Files\Football Value Betting\EPLValueBetting.exe",
            "_PYI_PARENT_PROCESS_LEVEL": "1",
            "_PYI_APPLICATION_HOME_DIR": r"C:\Temp\_MEI12345",
        }
        env = external_installer_environment(source)

        self.assertEqual(env["PATH"], source["PATH"])
        self.assertEqual(env["TEMP"], source["TEMP"])
        self.assertFalse(any(key.upper().startswith("_PYI_") for key in env))
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_existing_reset_flag_is_forced_on(self):
        env = external_installer_environment({"PYINSTALLER_RESET_ENVIRONMENT": "0"})
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")


if __name__ == "__main__":
    unittest.main()
