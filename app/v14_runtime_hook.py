"""Small V1.4 compatibility patch applied before the market-analysis UI loads.

The base V1.3 team-similarity helper treats an empty comparison string as a
substring match. V1.4 parses generic Over/Under selections where there is no
team comparison, so empty strings must score zero instead.
"""

import engine


_original_team_similarity = engine.team_similarity


def safe_team_similarity(a: str, b: str) -> float:
    if not str(a or "").strip() or not str(b or "").strip():
        return 0.0
    return _original_team_similarity(a, b)


engine.team_similarity = safe_team_similarity
