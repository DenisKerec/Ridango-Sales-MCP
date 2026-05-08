"""Read-side helpers: search, entity detail, chunk fetch, stats. KB-scoped."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kb2.models import Alias, Chunk, Entity, KnowledgeBase, Relation


def list_kbs(session: Session) -> list[dict[str, Any]]:
    counts = dict(
        session.execute(
            select(Entity.kb_id, func.count(Entity.id)).group_by(Entity.kb_id)
        ).all()
    )
    out: list[dict[str, Any]] = []
    for kb in session.execute(select(KnowledgeBase).order_by(KnowledgeBase.slug)).scalars():
        out.append(
            {
                "slug": kb.slug,
                "name": kb.name,
                "description": kb.description,
                "status": kb.status,
                "entity_count": counts.get(kb.id, 0),
            }
        )
    return out


def stats(session: Session, kb_slug: str | None = None) -> dict[str, Any]:
    base_filter = []
    if kb_slug:
        kb_id = session.execute(
            select(KnowledgeBase.id).where(KnowledgeBase.slug == kb_slug)
        ).scalar_one_or_none()
        if kb_id is None:
            return {"error": f"unknown kb: {kb_slug}"}
        base_filter.append(Entity.kb_id == kb_id)

    by_kind = dict(
        session.execute(
            select(Entity.kind, func.count(Entity.id))
            .where(*base_filter)
            .group_by(Entity.kind)
        ).all()
    )
    by_class = dict(
        session.execute(
            select(Entity.classification, func.count(Entity.id))
            .where(*base_filter, Entity.classification.is_not(None))
            .group_by(Entity.classification)
        ).all()
    )
    rel_q = select(func.count(Relation.id))
    chunk_q = select(func.count(Chunk.id))
    if base_filter:
        rel_q = rel_q.join(Entity, Entity.id == Relation.src_id).where(*base_filter)
        chunk_q = chunk_q.join(Entity, Entity.id == Chunk.entity_id).where(*base_filter)

    return {
        "kb_slug": kb_slug,
        "entities_by_kind": by_kind,
        "capabilities_by_classification": by_class,
        "relations": session.execute(rel_q).scalar_one(),
        "chunks": session.execute(chunk_q).scalar_one(),
    }


def search_entities(
    session: Session,
    q: str,
    *,
    kb_slug: str | None = None,
    kind: str | None = None,
    classification: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """FTS over entity body_tsv."""
    rank = func.ts_rank_cd(Entity.body_tsv, func.websearch_to_tsquery("simple", q))
    snippet = func.ts_headline(
        "simple",
        Entity.body_md,
        func.websearch_to_tsquery("simple", q),
        "MaxFragments=1, MaxWords=30, MinWords=5, ShortWord=2",
    )
    stmt = (
        select(
            KnowledgeBase.slug.label("kb_slug"),
            Entity.slug,
            Entity.kind,
            Entity.name,
            Entity.classification,
            Entity.confidence,
            snippet.label("snippet"),
            rank.label("score"),
        )
        .join(KnowledgeBase, KnowledgeBase.id == Entity.kb_id)
        .where(Entity.body_tsv.op("@@")(func.websearch_to_tsquery("simple", q)))
        .order_by(rank.desc())
        .limit(limit)
    )
    if kb_slug:
        stmt = stmt.where(KnowledgeBase.slug == kb_slug)
    if kind:
        stmt = stmt.where(Entity.kind == kind)
    if classification:
        stmt = stmt.where(Entity.classification == classification)
    return [
        {
            "kb_slug": r.kb_slug,
            "slug": r.slug,
            "kind": r.kind,
            "name": r.name,
            "classification": r.classification,
            "confidence": r.confidence,
            "snippet": r.snippet,
            "score": float(r.score) if r.score is not None else None,
        }
        for r in session.execute(stmt).all()
    ]


def search_chunks(
    session: Session,
    q: str,
    *,
    kb_slug: str | None = None,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """FTS over chunk body_tsv. Returns chunks with parent doc info."""
    rank = func.ts_rank_cd(Chunk.body_tsv, func.websearch_to_tsquery("simple", q))
    snippet = func.ts_headline(
        "simple",
        Chunk.body_md,
        func.websearch_to_tsquery("simple", q),
        "MaxFragments=1, MaxWords=40, MinWords=10, ShortWord=2",
    )
    stmt = (
        select(
            Chunk.id,
            Chunk.ord,
            Chunk.heading_path,
            Chunk.section_name,
            Chunk.page_number,
            Chunk.source_filename,
            Entity.slug.label("doc_slug"),
            Entity.name.label("doc_name"),
            KnowledgeBase.slug.label("kb_slug"),
            snippet.label("snippet"),
            rank.label("score"),
        )
        .join(Entity, Entity.id == Chunk.entity_id)
        .join(KnowledgeBase, KnowledgeBase.id == Entity.kb_id)
        .where(Chunk.body_tsv.op("@@")(func.websearch_to_tsquery("simple", q)))
        .where(Chunk.level == 0)  # leaves only
        .order_by(rank.desc())
        .limit(limit)
    )
    if kb_slug:
        stmt = stmt.where(KnowledgeBase.slug == kb_slug)
    return [
        {
            "chunk_id": r.id,
            "ord": r.ord,
            "heading_path": r.heading_path,
            "section_name": r.section_name,
            "page_number": r.page_number,
            "source_filename": r.source_filename,
            "doc_slug": r.doc_slug,
            "doc_name": r.doc_name,
            "kb_slug": r.kb_slug,
            "snippet": r.snippet,
            "score": float(r.score) if r.score is not None else None,
        }
        for r in session.execute(stmt).all()
    ]


def get_entity(
    session: Session, kb_slug: str, slug_or_alias: str, *, depth: int = 1
) -> dict[str, Any] | None:
    """Full entity payload with relations + aliases + chunk count. `slug_or_alias`
    is matched against entity.slug first, then alias.alias within the same KB."""
    ent = session.execute(
        select(Entity)
        .join(KnowledgeBase, KnowledgeBase.id == Entity.kb_id)
        .where(KnowledgeBase.slug == kb_slug, Entity.slug == slug_or_alias)
    ).scalar_one_or_none()

    if ent is None:
        eid = session.execute(
            select(Alias.entity_id)
            .join(KnowledgeBase, KnowledgeBase.id == Alias.kb_id)
            .where(KnowledgeBase.slug == kb_slug, Alias.alias.ilike(slug_or_alias))
        ).scalar_one_or_none()
        if eid is None:
            return None
        ent = session.get(Entity, eid)
        if ent is None:
            return None

    aliases = [
        a.alias
        for a in session.execute(select(Alias).where(Alias.entity_id == ent.id)).scalars()
    ]
    out_rels = _collect_relations(session, ent.id, outgoing=True)
    in_rels = _collect_relations(session, ent.id, outgoing=False)
    chunk_count = session.execute(
        select(func.count(Chunk.id)).where(Chunk.entity_id == ent.id)
    ).scalar_one()

    return {
        "kb_slug": kb_slug,
        "id": ent.id,
        "slug": ent.slug,
        "kind": ent.kind,
        "name": ent.name,
        "status": ent.status,
        "classification": ent.classification,
        "confidence": ent.confidence,
        "body_md": ent.body_md,
        "properties": ent.properties or {},
        "metadata": ent.metadata_ or {},
        "aliases": aliases,
        "out_relations": out_rels,
        "in_relations": in_rels,
        "chunk_count": chunk_count,
        "created_at": ent.created_at.isoformat() if ent.created_at else None,
        "updated_at": ent.updated_at.isoformat() if ent.updated_at else None,
    }


def _collect_relations(
    session: Session, entity_id: int, *, outgoing: bool, limit: int = 200
) -> list[dict[str, Any]]:
    side = Relation.src_id if outgoing else Relation.dst_id
    other = Relation.dst_id if outgoing else Relation.src_id
    OtherEntity = Entity.__table__.alias("other_entity")
    OtherKB = KnowledgeBase.__table__.alias("other_kb")
    stmt = (
        select(
            Relation.id,
            Relation.kind,
            Relation.properties,
            Relation.weight,
            OtherKB.c.slug.label("other_kb_slug"),
            OtherEntity.c.slug.label("other_slug"),
            OtherEntity.c.name.label("other_name"),
            OtherEntity.c.kind.label("other_kind"),
        )
        .join(OtherEntity, OtherEntity.c.id == other)
        .join(OtherKB, OtherKB.c.id == OtherEntity.c.kb_id)
        .where(side == entity_id)
        .order_by(Relation.kind, OtherEntity.c.name)
        .limit(limit)
    )
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "properties": r.properties or {},
            "weight": float(r.weight) if r.weight is not None else None,
            "other_kb_slug": r.other_kb_slug,
            "other_slug": r.other_slug,
            "other_name": r.other_name,
            "other_kind": r.other_kind,
        }
        for r in session.execute(stmt).all()
    ]


def get_chunk(session: Session, chunk_id: int) -> dict[str, Any] | None:
    row = session.execute(
        select(
            Chunk,
            Entity.slug.label("doc_slug"),
            Entity.name.label("doc_name"),
            KnowledgeBase.slug.label("kb_slug"),
        )
        .join(Entity, Entity.id == Chunk.entity_id)
        .join(KnowledgeBase, KnowledgeBase.id == Entity.kb_id)
        .where(Chunk.id == chunk_id)
    ).first()
    if row is None:
        return None
    c = row.Chunk
    return {
        "id": c.id,
        "kb_slug": row.kb_slug,
        "doc_slug": row.doc_slug,
        "doc_name": row.doc_name,
        "ord": c.ord,
        "level": c.level,
        "parent_id": c.parent_id,
        "heading_path": c.heading_path,
        "section_name": c.section_name,
        "page_number": c.page_number,
        "source_filename": c.source_filename,
        "document_type": c.document_type,
        "body_md": c.body_md,
    }


def ontology(session: Session) -> dict[str, Any]:
    """Live vocabulary — what kinds and relation_kinds actually exist in the DB."""
    kinds = dict(
        session.execute(
            select(Entity.kind, func.count(Entity.id)).group_by(Entity.kind).order_by(Entity.kind)
        ).all()
    )
    rel_kinds = dict(
        session.execute(
            select(Relation.kind, func.count(Relation.id))
            .group_by(Relation.kind)
            .order_by(func.count(Relation.id).desc())
        ).all()
    )
    classifications = dict(
        session.execute(
            select(Entity.classification, func.count(Entity.id))
            .where(Entity.classification.is_not(None))
            .group_by(Entity.classification)
        ).all()
    )
    return {
        "entity_kinds": kinds,
        "relation_kinds": rel_kinds,
        "classifications": classifications,
    }
