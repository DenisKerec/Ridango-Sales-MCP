# Ridango Sales MCP

An **MCP server** for querying Ridango's sales knowledge base — products, capabilities, market intelligence, vendors, tenders. Read-only client; the database is hosted and populated centrally.

Wire it into Claude Desktop / Code, ask natural-language questions, get cited answers.

## What you can ask

```
"who supplies AVL to STB Bucuresti?"
"who uses INIT for AVL?"
"what AFC tenders are active in the Nordics?"
"what are the AVL gaps?"
"who do we compete with in Germany?"
"how does headway control work"          ← falls back to FTS over the docs
```

The semantic layer auto-routes each query to one of seven SQL templates, or falls back to full-text search across the document corpus.

## Quick setup

### 1. Install

Requires Python 3.12+ and [uv](https://github.com/astral-sh/uv).

```bash
git clone git@github.com:DenisKerec/Ridango-Sales-MCP.git
cd Ridango-Sales-MCP
uv sync
```

### 2. Get the DB connection string

The kb2 database is hosted on the Ridango AWS RDS instance and reachable over the company VPN. **Ask Denis** for the `KB2_DATABASE_URL` value. You'll need the VPN connected to reach it.

### 3. Configure

```bash
cp .env.example .env
# Edit .env — paste the URL Denis sent you
```

### 4. Smoke test

```bash
uv run kb2 list                                      # → 6 workspaces
uv run kb2 stats                                     # → ~2,300 entities, ~6,400 relations
uv run kb2 ask "who supplies AVL to STB Bucuresti?"  # → cited answer
```

## Wire into Claude Code

One command — replace the path with the absolute path to your clone:

```bash
claude mcp add ridango-kb-2 --scope user -- \
  uv --directory /abs/path/to/Ridango-Sales-MCP run kb2 mcp serve
```

Verify it registered and is reachable:

```bash
claude mcp list
# expected line:
# ridango-kb-2: uv --directory /abs/path/to/Ridango-Sales-MCP run kb2 mcp serve - ✓ Connected
```

That's it. Restart any open Claude Code session (or run `/mcp` to reload), then ask a natural-language question — Claude will pick the right `ridango-kb-2` tool automatically. The server reads `KB2_DATABASE_URL` from the `.env` next to the repo, so you don't need to put credentials into the Claude config.

To remove or update later:

```bash
claude mcp remove ridango-kb-2
```

## Wire into Claude Desktop

Edit the config file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

If the file already has an `mcpServers` block, merge this entry into it:

```json
{
  "mcpServers": {
    "ridango-kb-2": {
      "command": "uv",
      "args": ["--directory", "/abs/path/to/Ridango-Sales-MCP", "run", "kb2", "mcp", "serve"],
      "env": {
        "KB2_DATABASE_URL": "postgresql+psycopg://user:%21pwd%21@host:5432/db"
      }
    }
  }
}
```

Restart Claude Desktop. The eight tools below are now callable in any chat.

> Why `env` on Desktop but not Code? Claude Desktop launches the server from a fresh process tree without your shell's working directory, so `.env` doesn't get auto-loaded — you set credentials inline. Claude Code spawns from a context that picks up the repo's `.env`.

## Tools exposed

| Tool | Purpose |
|---|---|
| `query_knowledge_base(query, kb?)` | NL → cited answer; auto-routes to SQL template or FTS |
| `kb_list()` | Enumerate workspaces with entity counts |
| `kb_stats(kb?)` | Counts by entity kind, capability classification, relations, chunks |
| `kb_ontology()` | Live vocabulary — entity kinds, relation kinds (with counts), classifications |
| `kb_search(q, kb?, kind?, classification?, limit?)` | FTS over entity bodies (websearch syntax) |
| `kb_search_chunks(q, kb?, limit?)` | FTS over chunk bodies — richer for prose questions |
| `kb_entity(kb, slug)` | Full entity payload — body, properties, in/out relations, aliases |
| `kb_chunk(chunk_id)` | Single chunk by id with parent doc info |

## What's in the knowledge base

Six workspaces (call `kb_list()` to confirm counts):

- **`avl-product`** — Ridango AVL/TMS capability map (FIT/PARTIAL_FIT/GAP classifications)
- **`afc-product`** — Ridango AFC capability map
- **`mtb-product`** — Ridango Mobile Ticketing
- **`market-intelligence`** — operators (PTOs/PTAs), vendors (competitors), consultants, per-country market briefs
- **`tenders`** — active opportunities (OPPORTUNITY_REGISTER)
- **`legal`** — NDAs, MSAs, compliance docs (sparse for now)

Behind that:

- ~1,700 companies (operators / vendors / consultants)
- ~290 capabilities (classified FIT / PARTIAL_FIT / GAP / UNCONFIRMED / OUT_OF_SCOPE)
- ~160 active tenders
- ~6,400 typed graph edges (`current_avl_supplier`, `current_afc_supplier`, `issued_by`, `pipeline_opportunity`, …)
- ~2,300 chunks with FTS via Postgres tsvector

## Semantic intents

The classifier in `kb2.semantic.intent` is rule-based — regex over verbs (`tender`/`supplier`/`gap`/`vendor`), country names (50+ ISO mappings), product scopes (afc/avl/rtpi/cemv/…), classifications (FIT/GAP/…), plus alias-table lookups for entity mentions. No LLM dependency; deterministic.

| Intent | Triggered by | What it returns |
|---|---|---|
| `active_tenders` | "tender(s)", "opportunity"/"opportunities", "rfp", "bid" | filter `kind='tender'` by country, product_scope, status |
| `current_supplier` | "supplier"/"supplies"/"uses" + an operator-mention | from operator → `current_*_supplier` → vendor |
| `operators_using_vendor` | same, but with a vendor-mention | reverse of above (one vendor → many operators) |
| `vendors_for_product` | "vendors" / "suppliers" without specific operator | distinct vendors active for a product, optionally by country |
| `capabilities_by_classification` | "capability"/"capabilities", "FIT"/"GAP"/"PARTIAL"/"UNCONFIRMED" | filter `kind='capability'` by classification + KB |
| `competitors_in_country` | "competitor(s)" + country | distinct vendors with `current_*_supplier` edges to operators in country |
| `general_rag` | fallback | FTS over chunks + entities |

See `ONTOLOGY.md` for the full vocabulary and SQL templates.

## Troubleshooting

**`pg_isready: no response`** — VPN isn't routing you to the RDS endpoint. Re-check the VPN client.

**`auth failed` / wrong password** — special characters in the URL form must be percent-encoded (`!` → `%21`, `#` → `%23`). Or use the split `KB2_DB_USER`/`KB2_DB_PASSWORD`/… form, which handles encoding for you.

**`relation "kb2.entity" does not exist`** — connected to the wrong DB or the wrong schema. The DB is `procurement_surv` and the schema is `kb2`.

**MCP server starts but Claude Desktop can't see tools** — check `~/Library/Logs/Claude/mcp*.log` (macOS). Most often `uv` isn't on the PATH that Claude Desktop runs with — use a full path to `uv` in the `command` field, or replace `uv run kb2 mcp serve` with the absolute path to the venv's `kb2` binary.

## License

Internal — Ridango.
