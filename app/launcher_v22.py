from __future__ import annotations

import multiprocessing
import sys


if __name__ == "__main__":
    # PyInstaller's multiprocessing hook must run before importing any GUI code.
    # Worker subprocesses are intercepted here instead of recursively launching
    # a second copy of the desktop application.
    multiprocessing.freeze_support()

    import v14_runtime_hook  # noqa: F401 - retain parser compatibility patch

    if "--self-test" in sys.argv:
        from self_test_v22 import run

        raise SystemExit(run())

    from main_v22 import main

    main()
