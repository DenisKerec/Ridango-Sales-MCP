"""answer(query, kb?) — the headline entry point.

Pipeline:
  1. Classify intent (regex + alias lookups)
  2. Branch:
     - relational intent → templated SQL via sql_builder
     - else              → FTS retrieval via retriever
  3. Format an answer payload with citations, intent debug, and matched rows.

No LLM dependency. Deterministic.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from kb2.semantic import intent as intent_mod
from kb2.semantic import retriever, sql_builder


def answer(
    session: Session,
    query: str,
    kb_slug: str | None = None,
) -> dict[str, Any]:
    intent = intent_mod.classify(session, query)
    rows: list[dict[str, Any]] = []
    sql_used: str | None = None
    chunks: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []

    if intent.intent == "active_tenders":
        rows, sql_used = sql_builder.active_tenders(
            session,
            country=intent.params.get("country"),
            product_scope=intent.params.get("product_scope"),
            active_only=intent.params.get("active_only", True),
        )
        answer_md = _format_tenders(rows, intent.params)

    elif intent.intent == "current_supplier":
        rows, sql_used = sql_builder.current_supplier(
            session,
            operator_slug=intent.params["operator_slug"],
            product_scope=intent.params.get("product_scope"),
        )
        answer_md = _format_current_supplier(rows, intent.params)

    elif intent.intent == "operators_using_vendor":
        rows, sql_used = sql_builder.operators_using_vendor(
            session,
            vendor_slug=intent.params["vendor_slug"],
            product_scope=intent.params.get("product_scope"),
            country=intent.params.get("country"),
        )
        answer_md = _format_operators_using_vendor(rows, intent.params)

    elif intent.intent == "vendors_for_product":
        rows, sql_used = sql_builder.vendors_for_product(
            session,
            product_scope=intent.params.get("product_scope"),
            country=intent.params.get("country"),
        )
        answer_md = _format_vendors_for_product(rows, intent.params)

    elif intent.intent == "capabilities_by_classification":
        rows, sql_used = sql_builder.capabilities_by_classification(
            session,
            classification=intent.params.get("classification"),
            product=intent.params.get("product"),
        )
        answer_md = _format_capabilities(rows, intent.params)

    elif intent.intent == "competitors_in_country":
        rows, sql_used = sql_builder.competitors_in_country(
            session, country=intent.params["country"]
        )
        answer_md = _format_competitors(rows, intent.params)

    else:  # general_rag fallback
        hits = retriever.hybrid_search(session, query, kb_slug=kb_slug, k=12)
        chunks = hits["chunks"]
        entities = hits["entities"]
        answer_md = _format_rag(query, chunks, entities)

    return {
        "query": query,
        "intent": intent.intent,
        "intent_explanation": intent.explanation,
        "intent_params": intent.params,
        "kb_scope": kb_slug,
        "answer_md": answer_md,
        "rows": rows,
        "row_count": len(rows),
        "chunks": chunks,
        "entities": entities,
        "sql": sql_used,
    }


# ── Formatters — terse markdown summaries that go into answer_md ─────────────


def _format_tenders(rows: list[dict], params: dict) -> str:
    if not rows:
        return f"No tenders match (country={params.get('country')}, product={params.get('product_scope')})."
    head = (
        f"**Found {len(rows)} tender(s)**"
        + (f" in {params['country']}" if params.get("country") else "")
        + (f" for {params['product_scope']}" if params.get("product_scope") else "")
        + ":\n"
    )
    lines = [head]
    for r in rows[:25]:
        deadline = r.get("deadline") or "?"
        value = r.get("value_eur")
        value_s = f" (€{int(value):,})" if value else ""
        op_name = r.get("operator_name") or "—"
        prio = r.get("priority") or ""
        lines.append(
            f"- **{r['name']}** [{r['country']}] — operator: {op_name}; "
            f"deadline: {deadline}{value_s}; priority: {prio}; slug: `{r['slug']}`"
        )
    if len(rows) > 25:
        lines.append(f"\n_…and {len(rows) - 25} more._")
    return "\n".join(lines)


def _format_current_supplier(rows: list[dict], params: dict) -> str:
    if not rows:
        return f"No suppliers recorded for `{params.get('operator_slug')}`."
    op = rows[0].get("operator_name") or params.get("operator_slug")
    lines = [f"**{op}** ({rows[0].get('country', '?')}) currently uses:\n"]
    for r in rows:
        product = r["relation_kind"].removeprefix("current_").removesuffix("_supplier")
        conf = f" _[{r['confidence']}]_" if r.get("confidence") else ""
        lines.append(f"- **{product.upper()}** → {r['vendor_name']} (`{r['vendor_slug']}`){conf}")
    return "\n".join(lines)


def _format_operators_using_vendor(rows: list[dict], params: dict) -> str:
    vendor = params.get("vendor_name") or params.get("vendor_slug")
    if not rows:
        return f"No operators recorded as using `{vendor}`."
    by_country: dict[str, list[dict]] = {}
    for r in rows:
        by_country.setdefault(r.get("country") or "?", []).append(r)
    lines = [f"**{vendor}** is the recorded supplier for **{len(rows)}** operator(s):\n"]
    for country, ops in sorted(by_country.items()):
        lines.append(f"\n**{country}** ({len(ops)} operators):")
        for r in ops[:30]:
            product = r["relation_kind"].removeprefix("current_").removesuffix("_supplier")
            lines.append(f"  - {r['operator_name']} — {product.upper()} (`{r['operator_slug']}`)")
        if len(ops) > 30:
            lines.append(f"  - _…and {len(ops) - 30} more in {country}._")
    return "\n".join(lines)


def _format_vendors_for_product(rows: list[dict], params: dict) -> str:
    if not rows:
        return "No vendors found."
    product = params.get("product_scope") or "any product"
    country = params.get("country")
    head = f"**{len(rows)} vendor(s)** active for **{product.upper()}**" + (
        f" in **{country}**" if country else ""
    )
    lines = [head + ":\n"]
    for r in rows[:30]:
        op_count = r.get("operator_count", 0)
        lines.append(
            f"- **{r['vendor_name']}** (`{r['vendor_slug']}`, hq={r.get('hq_country') or '?'}) "
            f"— {op_count} operator(s)"
        )
    return "\n".join(lines)


def _format_capabilities(rows: list[dict], params: dict) -> str:
    if not rows:
        return "No capabilities match those filters."
    cls = params.get("classification") or "ALL"
    prod = params.get("product") or "ALL"
    by_kb: dict[str, list[dict]] = {}
    for r in rows:
        by_kb.setdefault(r["kb_slug"], []).append(r)
    lines = [f"**{len(rows)} capabilities** (classification={cls}, product={prod}):\n"]
    for kb_slug, caps in by_kb.items():
        lines.append(f"\n### {kb_slug} ({len(caps)})")
        for c in caps[:30]:
            ev = f" — _{c['evidence']}_" if c.get("evidence") else ""
            lines.append(f"- **{c['name']}** [{c['classification']}/{c['confidence'] or '?'}] (`{c['slug']}`){ev}")
        if len(caps) > 30:
            lines.append(f"  _…and {len(caps) - 30} more in {kb_slug}._")
    return "\n".join(lines)


def _format_competitors(rows: list[dict], params: dict) -> str:
    if not rows:
        return f"No competitors found in {params['country']}."
    lines = [f"**{len(rows)} competitor(s)** active in **{params['country']}**:\n"]
    for r in rows[:25]:
        cats = ",".join((r.get("supplier_kinds") or [])[:5])
        lines.append(
            f"- **{r['name']}** (`{r['slug']}`, hq={r.get('hq_country') or '?'}) — "
            f"{r.get('operator_count', 0)} operators; kinds: {cats}"
        )
    return "\n".join(lines)


def _format_rag(query: str, chunks: list[dict], entities: list[dict]) -> str:
    parts = [f"**General search for:** {query!r}\n"]
    if entities:
        parts.append(f"\n### Matching entities ({len(entities)})")
        for e in entities[:8]:
            cls = f" [{e['classification']}]" if e.get("classification") else ""
            parts.append(f"- **{e['name']}** ({e['kind']}{cls}, kb={e['kb_slug']}, slug=`{e['slug']}`)")
    if chunks:
        parts.append(f"\n### Top chunks ({len(chunks)})")
        for c in chunks[:8]:
            heading = " › ".join(c.get("heading_path") or []) or c.get("section_name") or ""
            parts.append(
                f"- **{c.get('doc_name')}** — {heading} (kb={c['kb_slug']}, chunk_id={c['chunk_id']})"
            )
            if c.get("snippet"):
                parts.append(f"  > {c['snippet']}")
    if not entities and not chunks:
        parts.append("_No matches._")
    return "\n".join(parts)
