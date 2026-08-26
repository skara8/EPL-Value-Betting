from __future__ import annotations

import multiprocessing
import sys


if __name__ == "__main__":
    multiprocessing.freeze_support()

    import v14_runtime_hook  # noqa: F401
    import v22_import_compat  # noqa: F401
    import v23_matching_patch  # noqa: F401

    if "--self-test" in sys.argv:
        # Import the complete V3 GUI stack so packaging/import regressions fail
        # the Windows build before an installer can be published.
        import main_v3  # noqa: F401
        from self_test_v24 import run
        raise SystemExit(run())

    from main_v3 import main
    main()
