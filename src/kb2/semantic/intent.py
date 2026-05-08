"""Rule-based intent classifier for kb2.

Pragmatic, regex-driven; no LLM. Designed against the data we actually have:
  - 1667 companies (operators / vendors / consultants)
  - 163 active tenders
  - 290 capabilities (FIT/PARTIAL_FIT/GAP/UNCONFIRMED/OUT_OF_SCOPE)
  - 6413 relations (current_avl_supplier, current_afc_supplier, issued_by, …)

Returns IntentResult(name, params). 'general_rag' is the fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kb2.models import Alias, Entity, KnowledgeBase

# Country codes appearing in our data — match ISO-2 + a few common aliases.
COUNTRY_NAMES = {
    "estonia": "EE", "ee": "EE",
    "finland": "FI", "fi": "FI",
    "sweden": "SE", "se": "SE",
    "norway": "NO", "no": "NO",
    "denmark": "DK", "dk": "DK",
    "latvia": "LV", "lv": "LV",
    "lithuania": "LT", "lt": "LT",
    "germany": "DE", "de": "DE",
    "france": "FR", "fr": "FR",
    "italy": "IT", "it": "IT",
    "spain": "ES", "es": "ES",
    "poland": "PL", "pl": "PL",
    "czech": "CZ", "czech republic": "CZ", "cz": "CZ",
    "slovakia": "SK", "sk": "SK",
    "hungary": "HU", "hu": "HU",
    "romania": "RO", "ro": "RO",
    "bulgaria": "BG", "bg": "BG",
    "uk": "UK", "united kingdom": "UK", "britain": "UK",
    "ireland": "IE", "ie": "IE",
    "netherlands": "NL", "holland": "NL", "nl": "NL",
    "belgium": "BE", "be": "BE",
    "austria": "AT", "at": "AT",
    "switzerland": "CH", "ch": "CH",
    "portugal": "PT", "pt": "PT",
    "greece": "GR", "gr": "GR",
    "morocco": "MA", "ma": "MA",
    "saudi arabia": "SA", "ksa": "SA", "sa": "SA",
    "uae": "AE", "ae": "AE",
    "kuwait": "KW", "kw": "KW",
    "qatar": "QA", "qa": "QA",
    "oman": "OM", "om": "OM",
    "bahrain": "BH", "bh": "BH",
    "israel": "IL", "il": "IL",
    "australia": "AU", "au": "AU",
    "new zealand": "NZ", "nz": "NZ",
    "south africa": "ZA", "za": "ZA",
    "us": "US", "usa": "US", "united states": "US",
    "canada": "CA", "ca": "CA",
    "india": "IN", "in": "IN",
    "singapore": "SG", "sg": "SG",
    "hong kong": "HK", "hk": "HK",
}

# product_scope tokens we recognise — maps a query word to a kb2 product_scope value.
PRODUCT_SCOPES = {
    "afc": "afc", "fare": "afc", "ticketing": "afc", "emv": "afc", "cemv": "afc",
    "avl": "avl", "cad": "avl", "tracking": "avl", "fleet": "avl",
    "rtpi": "rtpi", "passenger info": "rtpi",
    "mtb": "mtb", "mobile ticketing": "mtb",
    "cctv": "cctv",
    "obc": "obc", "on-board": "obc",
    "pis": "pis",
    "tvm": "tvm",
}

CLASSIFICATION_TOKENS = {
    "fit": "FIT", "fits": "FIT",
    "partial fit": "PARTIAL_FIT", "partial": "PARTIAL_FIT",
    "gap": "GAP", "gaps": "GAP",
    "unconfirmed": "UNCONFIRMED",
    "out of scope": "OUT_OF_SCOPE",
}


@dataclass
class IntentResult:
    intent: str
    params: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""


def _find_country(q: str) -> str | None:
    ql = q.lower()
    # Prefer multi-word match (e.g. "saudi arabia" before "sa")
    for name in sorted(COUNTRY_NAMES.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", ql):
            return COUNTRY_NAMES[name]
    return None


def _find_product(q: str) -> str | None:
    ql = q.lower()
    for token, scope in sorted(PRODUCT_SCOPES.items(), key=lambda kv: len(kv[0]), reverse=True):
        if re.search(rf"\b{re.escape(token)}\b", ql):
            return scope
    return None


def _find_classification(q: str) -> str | None:
    ql = q.lower()
    for token, value in sorted(CLASSIFICATION_TOKENS.items(), key=lambda kv: len(kv[0]), reverse=True):
        if re.search(rf"\b{re.escape(token)}\b", ql):
            return value
    return None


def _find_entity_mention(session: Session, q: str) -> dict[str, Any] | None:
    """Best-effort: find any entity in our DB whose alias or name matches a
    word/phrase in the query. Returns {kb_slug, slug, kind, name, properties}.
    Cheap-ish: capped to first hit, length-prioritised."""
    ql = q.lower()
    # Try alias table first — has the most aliases (~2k)
    alias_rows = session.execute(
        select(Alias.alias, Alias.entity_id)
    ).all()
    # Sort by alias length DESC so longer phrases beat substrings of themselves
    alias_rows.sort(key=lambda r: len(r.alias), reverse=True)
    hit_eid = None
    country_names_lower = set(COUNTRY_NAMES.keys())
    for row in alias_rows:
        a = (row.alias or "").lower().strip()
        if not a or len(a) < 3:
            continue
        # Skip aliases that are bare country names — those are handled by the
        # country-token detector and shouldn't anchor an "entity mention".
        if a in country_names_lower:
            continue
        if re.search(rf"(?<![\w]){re.escape(a)}(?![\w])", ql):
            hit_eid = row.entity_id
            break
    if hit_eid is None:
        # Fallback: try entity name (only check for non-trivial names to avoid noise)
        rows = session.execute(
            select(Entity.id, Entity.name).where(Entity.kind.in_(["company", "tender"]))
        ).all()
        rows.sort(key=lambda r: len(r.name or ""), reverse=True)
        for r in rows:
            n = (r.name or "").lower().strip()
            if not n or len(n) < 4:
                continue
            if re.search(rf"(?<![\w]){re.escape(n)}(?![\w])", ql):
                hit_eid = r.id
                break
    if hit_eid is None:
        return None

    ent = session.execute(
        select(Entity, KnowledgeBase.slug)
        .join(KnowledgeBase, KnowledgeBase.id == Entity.kb_id)
        .where(Entity.id == hit_eid)
    ).first()
    if ent is None:
        return None
    e, kb_slug = ent
    return {
        "kb_slug": kb_slug,
        "id": e.id,
        "slug": e.slug,
        "kind": e.kind,
        "name": e.name,
        "properties": e.properties or {},
    }


def classify(session: Session, q: str) -> IntentResult:
    """Map an NL query to one of our supported intents."""
    ql = q.lower().strip()

    country = _find_country(ql)
    product = _find_product(ql)
    classification = _find_classification(ql)
    entity = _find_entity_mention(session, ql)

    has_supplier_word = bool(
        re.search(r"\b(suppliers?|supplies|uses|using|deployed|installed|vendors?|vendor of)\b", ql)
    )
    has_tender_word = bool(
        re.search(r"\b(tenders?|opportunit(y|ies)|rfps?|bids?)\b", ql)
    )
    has_capability_word = bool(
        re.search(r"\b(capabilit(y|ies)|fit[-/]?gap|gap analysis|features?|gaps?|fits?)\b", ql)
    )
    has_competitor_word = bool(
        re.search(r"\b(competitors?|compete|competing|rivals?)\b", ql)
    )
    has_active_word = bool(
        re.search(r"\b(active|open|ongoing|hot|live|current(ly)?)\b", ql)
    )

    # ── 1. Active tenders ─────────────────────────────────────────────────────
    if has_tender_word:
        return IntentResult(
            intent="active_tenders",
            params={"country": country, "product_scope": product, "active_only": has_active_word},
            explanation=f"tender query (country={country}, product={product})",
        )

    # ── 2. Current supplier / who uses what ──────────────────────────────────
    if has_supplier_word:
        # Direction: if entity is a vendor → operators_using_vendor; else current_supplier
        if entity and entity["properties"].get("role") == "competitor":
            return IntentResult(
                intent="operators_using_vendor",
                params={
                    "vendor_slug": entity["slug"],
                    "vendor_name": entity["name"],
                    "country": country,
                    "product_scope": product,
                },
                explanation=f"vendor reverse-lookup ({entity['name']})",
            )
        if entity and entity["properties"].get("role") in ("operator", "authority"):
            return IntentResult(
                intent="current_supplier",
                params={
                    "operator_slug": entity["slug"],
                    "operator_name": entity["name"],
                    "product_scope": product,
                },
                explanation=f"operator supplier-lookup ({entity['name']})",
            )
        # Generic: vendors offering a product type in a country
        return IntentResult(
            intent="vendors_for_product",
            params={"product_scope": product, "country": country},
            explanation=f"vendors-for-product (country={country}, product={product})",
        )

    # ── 3. Capabilities / fit-gap ────────────────────────────────────────────
    if has_capability_word or classification:
        return IntentResult(
            intent="capabilities_by_classification",
            params={"classification": classification, "product": product},
            explanation=f"capability query (cls={classification}, product={product})",
        )

    # ── 4. Competitors in market ─────────────────────────────────────────────
    if has_competitor_word and country:
        return IntentResult(
            intent="competitors_in_country",
            params={"country": country},
            explanation=f"competitors in {country}",
        )

    # ── Fallback ─────────────────────────────────────────────────────────────
    return IntentResult(
        intent="general_rag",
        params={"q": q},
        explanation="no relational intent matched — falling back to FTS",
    )
