from __future__ import annotations

import multiprocessing
import sys


if __name__ == "__main__":
    # PyInstaller's multiprocessing hook must run before importing any GUI code.
    # Worker subprocesses are intercepted here instead of recursively launching
    # a second copy of the desktop application.
    multiprocessing.freeze_support()

    import v14_runtime_hook  # noqa: F401 - retain parser compatibility patch
    import v22_import_compat  # noqa: F401 - inherited palette compatibility

    if "--self-test" in sys.argv:
        # Import the real GUI module chain as part of the frozen smoke test so
        # missing inherited symbols are caught before an installer is released.
        import main_v22  # noqa: F401
        from self_test_v22 import run

        raise SystemExit(run())

    from main_v22 import main

    main()
