from __future__ import annotations

# V1.9 re-exported most of the V1.7 dashboard palette but not the two blue
# constants. V2 imports the V1.9 shell for backwards compatibility, so expose
# those constants before loading the V2 modules rather than modifying old
# version files that remain useful for regression testing.
import main_v19
from main_v17 import BLUE_BG, BLUE_DARK

main_v19.BLUE_BG = BLUE_BG
main_v19.BLUE_DARK = BLUE_DARK

from main_v20_final import V20FinalApp, LOGGER  # noqa: E402


def main() -> None:
    try:
        V20FinalApp().mainloop()
    except Exception:
        LOGGER.exception("Fatal V2.0 application error")
        raise


if __name__ == "__main__":
    main()
