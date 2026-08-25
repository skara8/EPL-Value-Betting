import multiprocessing


if __name__ == "__main__":
    # Required by PyInstaller on Windows so spawned ProcessPool workers execute
    # the worker entry point instead of opening another copy of the GUI.
    multiprocessing.freeze_support()
    from main_v22 import main

    main()
