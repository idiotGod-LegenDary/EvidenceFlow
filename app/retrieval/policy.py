from app.business.dialogue import policy_topic
from app.schemas.retrieval import RetrievedChunk


def prioritize_policy(query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    topic = policy_topic(query)
    if topic is None:
        return chunks
    return sorted(
        chunks,
        key=lambda c: (
            topic in (c.section or ""),
            topic in c.title,
            c.content.count(topic),
        ),
        reverse=True,
    )
