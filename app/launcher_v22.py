import multiprocessing


if __name__ == "__main__":
    # Required by PyInstaller on Windows so spawned ProcessPool workers execute
    # the worker entry point instead of opening another copy of the GUI.
    multiprocessing.freeze_support()

    import main_v22
    from price_shop_v22 import fetch_best_prices_parallel
    from research_history_v22 import fetch_recent_epl_history

    # Football-Data uses short names such as "Man United" and "Man City".
    # Canonicalise them through the live-feed alias map before Elo/Poisson use.
    main_v22.fetch_recent_epl_history = fetch_recent_epl_history

    # Network-bound price feeds benefit from modest concurrency even when CPU
    # utilisation is low. Keep the cap inside price_shop_v22 to avoid bursts.
    main_v22.v21_module.fetch_best_prices = fetch_best_prices_parallel
    main_v22.main()
