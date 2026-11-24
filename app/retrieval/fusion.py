from collections import defaultdict

from app.schemas.retrieval import RetrievedChunk


def reciprocal_rank_fusion(
    ranked_lists: list[list[RetrievedChunk]], *, k: int = 60
) -> list[RetrievedChunk]:
    if k < 1:
        raise ValueError("Rank smoothing must be positive")
    totals: dict[str, float] = defaultdict(float)
    documents: dict[str, RetrievedChunk] = {}
    order: dict[str, int] = {}
    for results in ranked_lists:
        seen: set[str] = set()
        for position, item in enumerate(results, 1):
            if item.chunk_id in seen:
                continue
            seen.add(item.chunk_id)
            if item.chunk_id not in documents:
                documents[item.chunk_id] = item
                order[item.chunk_id] = len(order)
            totals[item.chunk_id] += 1 / (position + k)
    identifiers = sorted(totals, key=lambda key: (-totals[key], order[key]))
    return [
        documents[key].model_copy(
            deep=True,
            update={
                "rank": rank,
                "score": totals[key],
                "source": "rrf",
            },
        )
        for rank, key in enumerate(identifiers, 1)
    ]
