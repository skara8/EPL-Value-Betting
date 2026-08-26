from __future__ import annotations

import multiprocessing
import sys


if __name__ == "__main__":
    multiprocessing.freeze_support()

    # Keep only compatibility hooks that do not alter the V2.4 probability
    # model. In particular, do NOT import v23_edge_patch/v23_strategy_patch:
    # those deliberately construct market-derived fair probabilities, whereas
    # V2.4 must remain bookmaker-independent.
    import v14_runtime_hook  # noqa: F401
    import v22_import_compat  # noqa: F401
    import v23_matching_patch  # noqa: F401

    if "--self-test" in sys.argv:
        import main_v24  # noqa: F401
        from self_test_v24 import run
        raise SystemExit(run())

    from main_v24 import main
    main()
