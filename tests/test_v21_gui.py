import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import v14_runtime_hook  # noqa: F401
import main_v20_release  # noqa: F401 - applies V2 palette compatibility shim
from main_v21 import V21App  # noqa: E402


class V21GuiSmokeTests(unittest.TestCase):
    def test_v21_dashboard_and_validation_construct(self):
        try:
            app = V21App()
        except Exception as exc:
            self.fail(f"V2.1 GUI failed to initialise: {exc}")
        try:
            self.assertEqual(len(app.notebook.tabs()), 6)
            self.assertTrue(hasattr(app, "analysis_progress"))
            self.assertTrue(hasattr(app, "v21_robust_count_var"))
            self.assertTrue(hasattr(app, "v21_avg_clv_var"))
            self.assertEqual(app.best_pick_kicker.cget("text"), "BEST CURRENT ROBUST EDGE")
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
