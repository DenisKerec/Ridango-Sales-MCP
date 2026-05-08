"""Templated SQL per relational intent.

One function per intent. Each returns (rows: list[dict], debug_sql: str). All
parameters are bound — never string-formatted into SQL.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


# ── 1. active_tenders ─────────────────────────────────────────────────────────


def active_tenders(
    session: Session,
    *,
    country: str | None = None,
    product_scope: str | None = None,
    active_only: bool = True,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], str]:
    sql = """
        SELECT
          t.slug,
          t.name,
          t.properties->>'country'              AS country,
          t.properties->>'status'               AS status,
          t.properties->>'product_scope'        AS product_scope,
          (t.properties->>'value_eur')::bigint  AS value_eur,
          t.properties->>'procurement_deadline' AS deadline,
          t.properties->>'ridango_priority'     AS priority,
          op.slug                               AS operator_slug,
          op.name                               AS operator_name
        FROM kb2.entity t
        LEFT JOIN kb2.relation r
               ON r.src_id = t.id AND r.kind = 'issued_by'
        LEFT JOIN kb2.entity op
               ON op.id = r.dst_id
        WHERE t.kind = 'tender'
          AND (CAST(:country AS TEXT) IS NULL OR t.properties->>'country' = :country)
          AND (CAST(:product AS TEXT) IS NULL OR t.properties->>'product_scope' = :product
               OR :product = ANY(SELECT jsonb_array_elements_text(t.properties->'product_tags')))
          AND (NOT :active_only OR t.properties->>'status' = 'active')
        ORDER BY t.properties->>'procurement_deadline' DESC NULLS LAST
        LIMIT :limit
    """
    rows = session.execute(
        text(sql),
        {"country": country, "product": product_scope, "active_only": active_only, "limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows], sql


# ── 2. current_supplier (for one operator) ────────────────────────────────────


def current_supplier(
    session: Session,
    *,
    operator_slug: str,
    product_scope: str | None = None,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], str]:
    rel_filter = (
        "AND r.kind = 'current_' || :product || '_supplier'"
        if product_scope
        else "AND r.kind LIKE 'current_%_supplier'"
    )
    sql = f"""
        SELECT
          op.slug                          AS operator_slug,
          op.name                          AS operator_name,
          op.properties->>'country'        AS country,
          v.slug                           AS vendor_slug,
          v.name                           AS vendor_name,
          r.kind                           AS relation_kind,
          r.properties->>'confidence'      AS confidence,
          r.properties->>'contract_status' AS contract_status,
          r.properties->>'evidence'        AS evidence
        FROM kb2.entity op
        JOIN kb2.relation r ON r.src_id = op.id
        JOIN kb2.entity   v ON v.id = r.dst_id
        WHERE op.slug = :operator_slug
          {rel_filter}
        ORDER BY r.kind
        LIMIT :limit
    """
    rows = session.execute(
        text(sql),
        {"operator_slug": operator_slug, "product": product_scope, "limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows], sql


# ── 3. operators_using_vendor (reverse of #2) ────────────────────────────────


def operators_using_vendor(
    session: Session,
    *,
    vendor_slug: str,
    product_scope: str | None = None,
    country: str | None = None,
    limit: int = 100,
) -> tuple[list[dict[str, Any]], str]:
    rel_filter = (
        "AND r.kind = 'current_' || :product || '_supplier'"
        if product_scope
        else "AND r.kind LIKE 'current_%_supplier'"
    )
    sql = f"""
        SELECT
          v.slug                           AS vendor_slug,
          v.name                           AS vendor_name,
          op.slug                          AS operator_slug,
          op.name                          AS operator_name,
          op.properties->>'country'        AS country,
          r.kind                           AS relation_kind,
          r.properties->>'confidence'      AS confidence,
          r.properties->>'contract_status' AS contract_status
        FROM kb2.entity v
        JOIN kb2.relation r ON r.dst_id = v.id
        JOIN kb2.entity   op ON op.id = r.src_id
        WHERE v.slug = :vendor_slug
          {rel_filter}
          AND (CAST(:country AS TEXT) IS NULL OR op.properties->>'country' = :country)
        ORDER BY op.properties->>'country', op.name
        LIMIT :limit
    """
    rows = session.execute(
        text(sql),
        {"vendor_slug": vendor_slug, "product": product_scope, "country": country, "limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows], sql


# ── 4. vendors_for_product (generic, no specific operator) ───────────────────


def vendors_for_product(
    session: Session,
    *,
    product_scope: str | None = None,
    country: str | None = None,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], str]:
    rel_filter = (
        "AND r.kind = 'current_' || :product || '_supplier'"
        if product_scope
        else "AND r.kind LIKE 'current_%_supplier'"
    )
    sql = f"""
        SELECT
          v.slug                          AS vendor_slug,
          v.name                          AS vendor_name,
          v.properties->>'country'        AS hq_country,
          COUNT(DISTINCT op.id)           AS operator_count,
          array_agg(DISTINCT op.properties->>'country') FILTER (
            WHERE op.properties->>'country' IS NOT NULL
          )                               AS operator_countries
        FROM kb2.entity v
        JOIN kb2.relation r ON r.dst_id = v.id
        JOIN kb2.entity   op ON op.id = r.src_id
        WHERE v.kind = 'company'
          AND v.properties->>'role' = 'competitor'
          {rel_filter}
          AND (CAST(:country AS TEXT) IS NULL OR op.properties->>'country' = :country)
        GROUP BY v.id
        ORDER BY operator_count DESC
        LIMIT :limit
    """
    rows = session.execute(
        text(sql),
        {"product": product_scope, "country": country, "limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows], sql


# ── 5. capabilities_by_classification ─────────────────────────────────────────


def capabilities_by_classification(
    session: Session,
    *,
    classification: str | None = None,
    product: str | None = None,
    limit: int = 100,
) -> tuple[list[dict[str, Any]], str]:
    sql = """
        SELECT
          kb.slug                       AS kb_slug,
          c.slug,
          c.name,
          c.classification,
          c.confidence,
          c.properties->>'effort_tier'  AS effort_tier,
          c.properties->>'evidence_raw' AS evidence,
          c.properties->'section_path'  AS section_path
        FROM kb2.entity c
        JOIN kb2.knowledge_base kb ON kb.id = c.kb_id
        WHERE c.kind = 'capability'
          AND (CAST(:classification AS TEXT) IS NULL OR c.classification = :classification)
          AND (CAST(:product AS TEXT) IS NULL OR kb.slug LIKE '%' || :product || '%')
        ORDER BY
          CASE c.classification
            WHEN 'GAP' THEN 0 WHEN 'PARTIAL_FIT' THEN 1 WHEN 'UNCONFIRMED' THEN 2
            WHEN 'FIT' THEN 3 WHEN 'OUT_OF_SCOPE' THEN 4 ELSE 5
          END,
          c.name
        LIMIT :limit
    """
    rows = session.execute(
        text(sql),
        {"classification": classification, "product": product, "limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows], sql


# ── 6. competitors_in_country ────────────────────────────────────────────────


def competitors_in_country(
    session: Session,
    *,
    country: str,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], str]:
    sql = """
        SELECT
          v.slug,
          v.name,
          v.properties->>'country'         AS hq_country,
          v.properties->>'product_category' AS product_category,
          COUNT(DISTINCT op.id)            AS operator_count,
          array_agg(DISTINCT r.kind)       AS supplier_kinds
        FROM kb2.entity v
        JOIN kb2.relation r ON r.dst_id = v.id AND r.kind LIKE 'current_%_supplier'
        JOIN kb2.entity   op ON op.id = r.src_id
        WHERE v.kind = 'company'
          AND v.properties->>'role' = 'competitor'
          AND op.properties->>'country' = :country
        GROUP BY v.id
        ORDER BY operator_count DESC
        LIMIT :limit
    """
    rows = session.execute(text(sql), {"country": country, "limit": limit}).mappings().all()
    return [dict(r) for r in rows], sql
