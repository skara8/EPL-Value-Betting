import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import v14_runtime_hook  # noqa: F401
import v22_import_compat  # noqa: F401
import v23_matching_patch  # noqa: F401
from main_v24 import V24App  # noqa: E402


class V24GuiSmokeTests(unittest.TestCase):
    def test_v24_gui_constructs_with_independent_model_tab(self):
        try:
            app = V24App()
        except Exception as exc:
            self.fail(f"V2.4 GUI failed to initialise: {exc}")
        try:
            self.assertEqual(len(app.notebook.tabs()), 6)
            self.assertTrue(hasattr(app, "analysis_book"))
            labels = [app.analysis_book.tab(tab_id, "text") for tab_id in app.analysis_book.tabs()]
            self.assertIn("Independent model", labels)
            self.assertTrue(hasattr(app, "v24_model_tree"))
            self.assertTrue(hasattr(app, "v24_model_status_var"))
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
