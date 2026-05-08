"""kb2 MCP server (stdio transport).

Exposes the knowledge base via FastMCP tools. Run:

    uv run kb2 mcp serve

Tools:
  - query_knowledge_base — NL → cited answer (semantic layer entry point)
  - kb_list / kb_stats / kb_ontology
  - kb_search          — FTS over entities (use websearch syntax)
  - kb_search_chunks   — FTS over chunks (richer for prose)
  - kb_entity          — full entity payload + relations
  - kb_chunk           — single chunk by id
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from kb2.db import get_session
from kb2.reader import (
    get_chunk,
    get_entity,
    list_kbs,
    ontology,
    search_chunks,
    search_entities,
    stats,
)
from kb2.semantic.service import answer

mcp = FastMCP(
    "ridango-kb-2",
    instructions=(
        "Ridango knowledge base (kb2). Six workspaces: avl-product, afc-product, "
        "mtb-product, market-intelligence, tenders, legal. Schema: typed entity graph "
        "(company / tender / capability / document / topic) + 6,400 directed relations "
        "(current_avl_supplier, current_afc_supplier, issued_by, etc.) + 2,300 chunks "
        "with FTS. Always use query_knowledge_base for natural-language questions; it "
        "auto-routes to the right SQL template (active tenders, current suppliers, "
        "capability gaps, vendors-for-product, competitors-in-country) or falls back "
        "to FTS retrieval. Use kb_search for keyword lookups and kb_entity for full "
        "detail including relations + aliases. The relation kinds vocab is in "
        "kb_ontology()."
    ),
)


# ── Discovery ────────────────────────────────────────────────────────────────


@mcp.tool()
def kb_list() -> list[dict[str, Any]]:
    """List all knowledge-base workspaces and their entity counts."""
    with get_session() as session:
        return list_kbs(session)


@mcp.tool()
def kb_stats(kb: str | None = None) -> dict[str, Any]:
    """Counts of entities (by kind), capabilities (by classification), relations, chunks. Optional kb scope."""
    with get_session() as session:
        return stats(session, kb_slug=kb)


@mcp.tool()
def kb_ontology() -> dict[str, Any]:
    """Live vocabulary — entity kinds, relation kinds (with counts), classifications. Use this to know what relation kinds exist before querying."""
    with get_session() as session:
        return ontology(session)


# ── Semantic / NL entry point ────────────────────────────────────────────────


@mcp.tool()
def query_knowledge_base(query: str, kb: str | None = None) -> dict[str, Any]:
    """Natural-language query → cited answer. Auto-routes to SQL when possible.

    Examples that hit relational templates:
      - "what AFC tenders are active in Estonia?"     → active_tenders
      - "who supplies AVL to STB Bucuresti?"          → current_supplier
      - "who uses INIT?" / "what does Kontron supply?"→ operators_using_vendor
      - "what AVL gaps do we have?"                   → capabilities_by_classification
      - "who do we compete with in Germany?"          → competitors_in_country

    Anything else falls back to FTS retrieval over chunks + entities.

    Args:
      query: the user's natural-language question
      kb: optional KB slug to scope the search (only affects RAG fallback)

    Returns:
      {answer_md, intent, intent_explanation, intent_params, rows, row_count,
       chunks, entities, sql, kb_scope, query}
    """
    with get_session() as session:
        return answer(session, query, kb_slug=kb)


# ── Direct primitives ────────────────────────────────────────────────────────


@mcp.tool()
def kb_search(
    q: str,
    kb: str | None = None,
    kind: str | None = None,
    classification: str | None = None,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Full-text search over entities (websearch syntax). Filter by kb/kind/classification.

    kind: one of company, tender, capability, document, topic
    classification: FIT, PARTIAL_FIT, GAP, UNCONFIRMED, OUT_OF_SCOPE
    """
    with get_session() as session:
        return search_entities(
            session, q, kb_slug=kb, kind=kind, classification=classification, limit=limit
        )


@mcp.tool()
def kb_search_chunks(q: str, kb: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
    """Full-text search over chunks (level-0 leaves). Returns snippets with doc + heading_path."""
    with get_session() as session:
        return search_chunks(session, q, kb_slug=kb, limit=limit)


@mcp.tool()
def kb_entity(kb: str, slug: str) -> dict[str, Any]:
    """Full entity payload — body, properties, all relations (in/out), aliases, chunk count.

    `slug` accepts an entity slug or any of its aliases (within the same KB).
    """
    with get_session() as session:
        ent = get_entity(session, kb, slug)
    if ent is None:
        return {"error": f"not found: kb={kb} slug={slug}"}
    return ent


@mcp.tool()
def kb_chunk(chunk_id: int) -> dict[str, Any]:
    """One chunk by id, with the parent doc's slug + KB."""
    with get_session() as session:
        c = get_chunk(session, chunk_id)
    if c is None:
        return {"error": f"chunk not found: {chunk_id}"}
    return c


def run_stdio_server() -> None:
    mcp.run()


if __name__ == "__main__":
    run_stdio_server()
