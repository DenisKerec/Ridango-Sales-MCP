"""FTS retriever — chunks + entities. Vector search deferred to v1.1."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from kb2.reader import search_chunks, search_entities


def hybrid_search(
    session: Session,
    q: str,
    *,
    kb_slug: str | None = None,
    k: int = 12,
) -> dict[str, list[dict[str, Any]]]:
    """V1: FTS-only over chunks + entities. Returns both lists.

    v1.1 will add pgvector cosine + RRF fusion. The signature stays the same;
    callers use the chunks list as the retrieval result.
    """
    chunks = search_chunks(session, q, kb_slug=kb_slug, limit=k)
    entities = search_entities(session, q, kb_slug=kb_slug, limit=max(5, k // 2))
    return {"chunks": chunks, "entities": entities}
