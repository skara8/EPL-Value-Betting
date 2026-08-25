from __future__ import annotations

import multiprocessing


if __name__ == "__main__":
    # PyInstaller's multiprocessing hook must run before importing the GUI. This
    # prevents spawned worker processes from recursively launching the app.
    multiprocessing.freeze_support()

    import v14_runtime_hook  # noqa: F401 - retain parser compatibility patch
    from main_v22 import main

    main()
