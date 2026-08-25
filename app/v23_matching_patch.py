from __future__ import annotations

from difflib import SequenceMatcher

import engine


_ORIGINAL = engine.team_similarity

# Words that add little identity across international club feeds. We only use
# this list for a secondary token-containment score; the original full-name
# similarity remains available and wins whenever it is stronger.
_GENERIC = {
    "club", "de", "del", "do", "da", "das", "dos", "the", "and",
    "atletico", "athletic", "deportivo", "sporting", "association",
    "sociedad", "futbol", "football", "calcio", "sc", "ac", "cd", "ca",
}


def _tokens(value: str) -> set[str]:
    return {token for token in engine.normalise_text(value).split() if token and token not in _GENERIC}


def enhanced_team_similarity(a: str, b: str) -> float:
    base = _ORIGINAL(a, b)
    if base >= 0.93:
        return base

    na, nb = engine.normalise_text(a), engine.normalise_text(b)
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return base

    common = ta & tb
    shorter = min(len(ta), len(tb))
    containment = len(common) / max(1, shorter)
    union = ta | tb
    jaccard = len(common) / max(1, len(union))

    # Common provider discrepancy: one feed uses a short traditional club name
    # while another includes city/province/organisation text. Require either
    # two shared distinctive tokens or one long distinctive token to avoid
    # matching generic one-word club names too aggressively.
    distinctive_single = len(common) == 1 and len(next(iter(common))) >= 6
    if containment >= 0.99 and (len(common) >= 2 or distinctive_single):
        return max(base, 0.94)

    token_score = 0.58 * containment + 0.42 * jaccard
    sequence = SequenceMatcher(None, na, nb).ratio()
    return max(base, token_score, sequence)


engine.team_similarity = enhanced_team_similarity
