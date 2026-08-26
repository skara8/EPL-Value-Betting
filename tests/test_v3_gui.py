import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import v14_runtime_hook  # noqa: F401
import main_v20_release  # noqa: F401 - palette compatibility shim
from main_v3_release import V3ReleaseApp  # noqa: E402


class V3GuiSmokeTests(unittest.TestCase):
    def test_v3_scientific_pages_construct(self):
        try:
            app = V3ReleaseApp()
        except Exception as exc:
            self.fail(f"V3 GUI failed to initialise: {exc}")
        try:
            self.assertEqual(len(app.notebook.tabs()), 6)
            self.assertTrue(hasattr(app, "independent_model_tab"))
            self.assertTrue(hasattr(app, "v3_lab_tab"))
            self.assertTrue(hasattr(app, "v3_walk_button"))
            self.assertTrue(hasattr(app, "v3_challenger_tree"))
            research_names = [app.research_book.tab(tab, "text") for tab in app.research_book.tabs()]
            self.assertIn("V3 laboratory", research_names)
            analysis_names = [app.analysis_book.tab(tab, "text") for tab in app.analysis_book.tabs()]
            self.assertIn("Independent model", analysis_names)
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
