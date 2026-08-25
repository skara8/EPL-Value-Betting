import v14_runtime_hook  # noqa: F401 - retain parser compatibility patch
import main_v20_release  # noqa: F401 - applies the V2 palette compatibility shim
from main_v21 import main


if __name__ == "__main__":
    main()
