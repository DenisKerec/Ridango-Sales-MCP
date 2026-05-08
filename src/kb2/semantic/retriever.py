"""FTS retriever — chunks + entities, with stopword filter + alias expansion.

v1: FTS-only via Postgres `simple` tsvector. Two pre-FTS query rewrites:

  1. **Stopword filter** — strips question/article noise ("what does X require?")
     so signal tokens dominate.
  2. **Alias expansion** — detects entity mentions in the query against
     `kb2.alias`, then ORs in every alias of every matched entity. Lifts recall
     for queries that name an entity by one of its many surface forms (e.g.
     "INIT" → matches chunks that say "INIT AG" or "Innovation in Traffic").

Both steps are cheap; together they materially improve general_rag for
naturally-phrased questions.

Vector search + RRF fusion is deferred to v1.1.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kb2.models import Alias
from kb2.reader import search_chunks, search_entities

# Common English question / article noise. Kept short on purpose — we want
# to err on the side of leaving content tokens in.
STOPWORDS: frozenset[str] = frozenset(
    {
        # interrogatives
        "what", "who", "where", "when", "why", "how", "which", "whose",
        # auxiliaries
        "is", "are", "was", "were", "be", "been", "being",
        "do", "does", "did", "have", "has", "had",
        "can", "could", "would", "should", "will", "may", "might",
        # determiners / pronouns
        "the", "a", "an", "this", "that", "these", "those",
        "our", "my", "your", "their", "his", "her", "its",
        "we", "i", "you", "they", "us", "them", "me",
        # connectives / prepositions
        "and", "but", "if", "as", "than",
        "of", "in", "on", "at", "to", "for", "by", "with", "from", "about",
        "into", "over", "under",
        # filler verbs that don't carry topic signal
        "tell", "show", "give", "list", "find", "get", "see", "know", "want",
        # politeness
        "please", "kindly", "thanks", "thank",
        # misc
        "vs", "also", "any", "some", "all",
    }
)

# Reserved tsquery operator words — keep these out of the cleaned query
# so they don't accidentally affect the OR-expansion.
TSQUERY_RESERVED: frozenset[str] = frozenset({"or", "and", "not"})


def _clean_query(q: str) -> str:
    """Lowercase, strip noise punctuation, drop stopwords. Preserve hyphens
    and slashes inside tokens (so 'VDV-453' and 'cad/avl' survive)."""
    # Replace anything that's not a word char, hyphen, underscore, slash, dot, plus,
    # or whitespace with a space. This kills `?`, `!`, `,`, `'`, `"`, `(`, `)`, etc.
    cleaned = re.sub(r"[^\w\s\-_/\.\+]", " ", q.lower())
    tokens = [
        t for t in cleaned.split()
        if t and t not in STOPWORDS and t not in TSQUERY_RESERVED
    ]
    # If we filtered everything (e.g. the query was just "what is the"), fall back
    # to the original — better to retrieve something than nothing.
    return " ".join(tokens) if tokens else q


def _expand_with_aliases(session: Session, q: str) -> str:
    """Detect entity mentions in `q` (against the alias table) and OR-expand
    the FTS query with every alias of every matched entity.

    Returns the original `q` unchanged if no entities were detected."""
    if not q.strip():
        return q

    ql = q.lower()
    alias_rows = session.execute(select(Alias.alias, Alias.entity_id)).all()
    # Match longer aliases first so 'INIT AG' beats 'INIT' for the same query.
    alias_rows.sort(key=lambda r: len(r.alias or ""), reverse=True)

    matched_eids: set[int] = set()
    for row in alias_rows:
        a = (row.alias or "").lower().strip()
        if not a or len(a) < 3:
            continue
        if re.search(rf"(?<![\w]){re.escape(a)}(?![\w])", ql):
            matched_eids.add(row.entity_id)

    if not matched_eids:
        return q

    # Pull every alias of every matched entity. Cap to avoid mega-queries.
    expansion_rows = session.execute(
        select(Alias.alias).where(Alias.entity_id.in_(matched_eids))
    ).all()
    aliases: set[str] = set()
    for r in expansion_rows:
        a = (r.alias or "").strip()
        if a and 3 <= len(a) <= 80:
            aliases.add(a)
    if not aliases:
        return q

    # Cap expansion at 30 aliases — a giant OR-tree starts hurting tsquery perf
    # and rarely improves recall after the top dozen surface forms.
    capped = sorted(aliases, key=len, reverse=True)[:30]
    quoted = [f'"{a}"' if " " in a or "-" in a else a for a in capped]
    expansion_str = " OR ".join(quoted)
    return f"{q} OR {expansion_str}"


def hybrid_search(
    session: Session,
    q: str,
    *,
    kb_slug: str | None = None,
    k: int = 12,
) -> dict[str, Any]:
    """V1: FTS-only over chunks + entities, with stopword filter + alias expansion.

    Returns:
      {chunks: [...], entities: [...], expanded_query: str}
    """
    cleaned = _clean_query(q)
    expanded = _expand_with_aliases(session, cleaned)
    chunks = search_chunks(session, expanded, kb_slug=kb_slug, limit=k)
    entities = search_entities(session, expanded, kb_slug=kb_slug, limit=max(5, k // 2))
    return {"chunks": chunks, "entities": entities, "expanded_query": expanded}
