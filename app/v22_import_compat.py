"""V2.2.1 compatibility shim for inherited V2 colour constants.

V2.0 imports BLUE_BG/BLUE_DARK from main_v19, but main_v19 only imported the
non-blue palette names from main_v18.  Patch the inherited module namespace
before importing the V2 GUI chain.  This is intentionally tiny and can be
removed when the versioned UI modules are consolidated.
"""

import main_v19
from main_v18 import BLUE_BG, BLUE_DARK

main_v19.BLUE_BG = BLUE_BG
main_v19.BLUE_DARK = BLUE_DARK
