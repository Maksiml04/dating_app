"""Детерминированное объединение рангов FTS и vector, без смешивания шкал."""
from collections import defaultdict

def reciprocal_rank_fusion(rankings: dict[str, list[str]], k: int = 60, weights: dict[str, float] | None = None) -> list[str]:
    if k <= 0:
        raise ValueError("k должен быть положительным")
    weights = weights or {}
    scores = defaultdict(float)
    for source, ids in rankings.items():
        seen = set()
        for rank, item in enumerate(ids, 1):
            if item in seen:
                continue
            seen.add(item)
            scores[item] += weights.get(source, 1.0) / (k + rank)
    return sorted(scores, key=lambda item: (-scores[item], item))
