# knowledge-base-2 — Ontology reference

This document is loaded at startup by the semantic layer's intent classifier and SQL builder. It is the **authoritative vocabulary** for entity kinds, relation kinds, and the relational-intent → SQL mapping.

It is also written as a guide for humans authoring new entities or aliases via the admin UI.

## Entity kinds

Each kind maps to `kb2.entity.kind`. All entities share the same physical table; `kind` is just TEXT (no DB enum) so adding a new kind requires only a Python `EntityKind` enum entry — no migration.

### `product`
A Ridango offering shipped to customers (AVL, AFC, MaaS, RTPI, …).
- `properties.type` — e.g. `"avl"`, `"afc"`, `"rtpi"`
- `properties.version` — semver or year
- `properties.owner_team` — team that owns the codebase
- Typical relations: `meets_standard`, `satisfies_legal`, `has_customer`, `part_of` (← capability), `gap_for` (← tender)

### `capability`
A functional capability of a product. Lives **under** a product via `part_of`.
- `properties.domain` — e.g. `"avl"`, `"depot-management"`, `"fare-evasion"`
- `properties.classification` — `FIT | PARTIAL_FIT | GAP | UNCONFIRMED | OUT_OF_SCOPE` (denormalized into the column for index)
- `properties.confidence` — `HIGH | MEDIUM | LOW`
- Typical relations: `part_of` (→ product), `depends_on` (→ capability), `cites` (← document)

### `standard`
A compliance/technical standard the products are measured against.
- `properties.standard_id` — e.g. `"ISO 27001"`, `"GDPR"`, `"PCI-DSS"`, `"EN 50128"`
- `properties.level` — e.g. `"certified"`, `"compliant"`, `"aware"`
- `properties.issued_by` — `"ISO"`, `"EU"`, `"PCI SSC"`
- `properties.version` — `"2022"`, `"v4"`
- Typical relations: `meets_standard` (← product), `defined_in` (→ document)

### `legal_requirement`
A jurisdiction-specific rule, distinct from a technical standard.
- `properties.jurisdiction` — ISO 3166 country/region code, e.g. `"EE"`, `"EU"`, `"KW"`
- `properties.title` — short title (`"Public Procurement Act"`)
- `properties.citation` — formal citation
- `properties.effective_date`
- Typical relations: `satisfies_legal` (← product), `cites` (← tender document)

### `company`
An organization. Role disambiguates customer / competitor / partner.
- `properties.country` — ISO 3166
- `properties.role` — `"customer" | "competitor" | "partner" | "supplier"` (multi-valued allowed)
- `properties.parent_id` — slug of parent company if subsidiary
- Typical relations: `has_customer` (← product), `competitor_of`, `subsidiary_of`, `acquired_by`

### `tender`
A historical outcome record for a tender — the queryable competitive intelligence layer (won/lost/no-bid by us or competitors). Active-tender bid analysis reads tender entities; outcomes are written to them after the fact.
- `properties.tender_id` — external identifier (e.g. `"HSL-2022-AFC-01"`)
- `properties.country` — ISO 3166 (e.g. `"FI"`, `"EE"`, `"LV"`)
- `properties.year` — int (e.g. `2022`)
- `properties.lot` — lot number if multi-lot (optional)
- `properties.status` — `"active" | "closed"` (lifecycle state)
- `properties.our_outcome` — `"won" | "lost" | "no-bid" | "shortlisted" | "active"`
- `properties.product_scope` — `"avl" | "afc" | "mtb"` (which Ridango product class)
- `properties.value_eur` — int (optional, contract value)
- Typical relations: `won_by` (→ company; competitor wins only — Ridango wins are implicit), `issued_by` (→ company; procuring authority), `gap_for` (→ capability; what we lost on), `cites`
- Lives in the `tenders` KB workspace.

### `document`
A source artifact ingested by the pipeline (PDF / DOCX / MD / HTML).
- `properties.source` — `"s3"` / `"local"` / `"confluence"` / …
- `properties.ingested_at`
- `properties.page_count`
- `properties.content_type`
- Every `kb2.chunk` has `entity_id` pointing at a `document`. Every `kb2.blob_meta` has `entity_id` pointing at a `document`.

### `clause`
A specific clause inside a contract document.
- `properties.clause_number`
- `properties.document_id` — slug of parent document
- Typical relations: `cites` (→ legal_requirement)

### `hardware`
Physical device — validators, AVL boxes, depot displays, ticket vending machines.
- `properties.model`
- `properties.manufacturer`
- Typical relations: `part_of` (→ product)

### `module`
A software module (repo / package).
- `properties.repo`
- `properties.language`
- Typical relations: `part_of` (→ product), `depends_on`

### `topic`
A vendor-neutral concept ("ITxPT", "GTFS-RT", "headway regulation"). Useful for cross-cutting search.
- `properties.aliases` — list of accepted spellings (also mirrored in `kb2.alias`)

## Relation kinds

Each relation row is a directed edge `(src_id, dst_id, kind)`. Cross-KB relations are allowed (`src` and `dst` may live in different `kb2.knowledge_base` workspaces).

| Relation kind        | src kind        | dst kind            | Semantic                                                |
|----------------------|-----------------|---------------------|---------------------------------------------------------|
| `meets_standard`     | product         | standard            | product satisfies the standard                          |
| `complies_with`      | product         | standard            | alias of `meets_standard` (kept for legacy FTS hits)    |
| `satisfies_legal`    | product         | legal_requirement   | product complies with the legal rule                    |
| `has_customer`       | product         | company             | this company uses (or used) this product                |
| `won_by`             | tender          | company             | competitor won the tender. **Ridango wins are implicit** — no `won_by` row when our_outcome='won' |
| `issued_by`          | tender          | company             | the procuring authority (e.g. HSL Helsinki, Tallinna LT) |
| `gap_for`            | tender          | capability          | what we lost on (capability gap that decided it)        |
| `cites`              | document        | any                 | document references / evidences entity                  |
| `mentions`           | (chunk array)   | any                 | NER-extracted; v1.1; lives in `chunk.mentioned_entity_ids`, **not** as relation rows |
| `part_of`            | capability / hardware / module | product | rolls up under                                |
| `depends_on`         | capability / module | capability / module | functional dependency                              |
| `competitor_of`      | company         | company             | symmetric (write both directions)                       |
| `subsidiary_of`      | company         | company             | child → parent                                          |
| `acquired_by`        | company         | company             | acquired company → acquirer                             |
| `defined_in`         | standard        | document            | standard's authoritative source                         |
| `supersedes`         | document        | document            | newer version replaces older                            |

## Classification + confidence

Used on `entity.classification` and `entity.confidence` (denormalized to columns; also indexable from `properties`):

| Classification | Meaning                                                |
|----------------|--------------------------------------------------------|
| `FIT`          | Product fully delivers this capability / standard      |
| `PARTIAL_FIT`  | Product partially delivers; gap is small               |
| `GAP`          | Not delivered; would need development                  |
| `UNCONFIRMED`  | Claimed but not yet evidenced                          |
| `OUT_OF_SCOPE` | Not applicable to this product                         |

| Confidence | Meaning                                                |
|------------|--------------------------------------------------------|
| `HIGH`     | Multiple independent citations or production evidence  |
| `MEDIUM`   | Single strong citation                                 |
| `LOW`      | Internal claim, no external evidence                   |

## Relational intent → SQL templates

The semantic layer's `sql_builder` exposes one Python function per relational intent. **Never use raw NL→SQL via LLM** — too unsafe for a sales tool. The intent classifier picks one of these; if none match, fall through to general RAG.

### `list_products_by_standard`
> "Which products meet ISO 27001?"

```sql
SELECT p.slug, p.name, p.classification, p.confidence,
       array_agg(DISTINCT r.source_id) FILTER (WHERE r.source_id IS NOT NULL) AS evidence_doc_ids
FROM kb2.entity p
JOIN kb2.relation r ON r.src_id = p.id AND r.kind IN ('meets_standard', 'complies_with')
JOIN kb2.entity s   ON s.id = r.dst_id AND s.kind = 'standard'
WHERE p.kind = 'product'
  AND (s.name ILIKE :standard_name OR s.slug = :standard_slug
       OR EXISTS (SELECT 1 FROM kb2.alias a WHERE a.entity_id = s.id AND a.alias ILIKE :standard_name))
GROUP BY p.id;
```

### `list_products_by_legal`
> "Which products satisfy GDPR?"

```sql
SELECT p.slug, p.name, p.classification, l.properties->>'jurisdiction' AS jurisdiction
FROM kb2.entity p
JOIN kb2.relation r ON r.src_id = p.id AND r.kind = 'satisfies_legal'
JOIN kb2.entity l   ON l.id = r.dst_id AND l.kind = 'legal_requirement'
WHERE p.kind = 'product'
  AND (l.name ILIKE :legal_name OR l.properties->>'citation' ILIKE :legal_name);
```

### `list_customers_by_product`
> "Who are AFC customers?" / "Does Ridango have a customer for AFC in Kuwait?"

```sql
SELECT c.slug, c.name, c.properties->>'country' AS country, c.properties->>'role' AS role
FROM kb2.entity p
JOIN kb2.relation r ON r.src_id = p.id AND r.kind = 'has_customer'
JOIN kb2.entity c   ON c.id = r.dst_id AND c.kind = 'company'
WHERE p.kind = 'product'
  AND (p.name ILIKE :product_name OR p.properties->>'type' = :product_type)
  AND (:country IS NULL OR c.properties->>'country' = :country);
```

### `list_products_by_customer`
> "What does Tallinn use?"

```sql
SELECT p.slug, p.name, p.properties->>'type' AS product_type
FROM kb2.entity c
JOIN kb2.relation r ON r.dst_id = c.id AND r.kind = 'has_customer'
JOIN kb2.entity p   ON p.id = r.src_id AND p.kind = 'product'
WHERE c.kind = 'company' AND c.name ILIKE :company_name;
```

### `compare_products_on_standard`
> "Which Ridango products are stronger on ISO 27001?"

```sql
SELECT p.slug, p.name, p.classification, p.confidence
FROM kb2.entity p
JOIN kb2.relation r ON r.src_id = p.id AND r.kind = 'meets_standard'
JOIN kb2.entity s   ON s.id = r.dst_id
WHERE p.kind = 'product' AND s.name ILIKE :standard_name
ORDER BY
  CASE p.classification WHEN 'FIT' THEN 0 WHEN 'PARTIAL_FIT' THEN 1 WHEN 'GAP' THEN 2 ELSE 3 END,
  CASE p.confidence     WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 WHEN 'LOW' THEN 2 ELSE 3 END;
```

### `find_gaps_for_tender`
> "What gaps did the Riga tender have?"

```sql
SELECT cap.slug, cap.name, cap.properties->>'domain' AS domain
FROM kb2.entity t
JOIN kb2.relation r ON r.src_id = t.id AND r.kind = 'gap_for'
JOIN kb2.entity cap ON cap.id = r.dst_id AND cap.kind = 'capability'
WHERE t.kind = 'tender' AND (t.name ILIKE :tender_name OR t.properties->>'tender_id' = :tender_id);
```

### `competitors_in_country`
> "Who do we compete with in Estonia?"

```sql
SELECT DISTINCT c.slug, c.name, c.properties->>'country' AS country
FROM kb2.entity c
WHERE c.kind = 'company'
  AND c.properties->>'role' LIKE '%competitor%'
  AND c.properties->>'country' = :country;
```

## Fallback: `general_rag`

If no relational intent matches, the retriever does a plain hybrid search over `kb2.chunk`. The answer is built from the top-k leaf chunks (with parent-window expansion), and citations point at the source documents.

## Adding a new intent

1. Add a Python function in `src/kb2/semantic/sql_builder.py` returning a parameterized SQL string.
2. Add detection rules to `src/kb2/semantic/intent.py` (regex over the alias table + a Claude Haiku fallback prompt with the intent name and one example).
3. Add a row to the table above and a test in `tests/test_sql_builder.py`.
4. **No DB migration needed.**

## Adding a new entity kind

1. Append a member to `EntityKind` in `src/kb2/models.py`.
2. Document the kind in this file (Entity kinds section).
3. If it has standard relations, document those in the relations table here.
4. **No DB migration needed.**
