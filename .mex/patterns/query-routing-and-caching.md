---
name: query-routing-and-caching
description: Understand how queries are routed to datasets and cached, or add a new data source
triggers:
  - "query routing"
  - "dataset"
  - "cache"
  - "mcp-data"
  - "new source"
  - "semantics"
edges:
  - target: context/conventions.md
    condition: when understanding the pydantic model pattern for caching
  - target: context/stack.md
    condition: when working with mcp-data or framecache libraries
grounds_to:
  - node: "function:25c293fd2a55f62370aaec081f0ed5c9"
    fingerprint: "mh:64:0xa2f7d1c9e4b5"
  - node: "function:2318f0e28f325f60a34eb30e7cec4d26"
    fingerprint: "mh:64:0x3e6d2a8b1f9c"
last_updated: 2026-09-14
---

# Query Routing and Caching

## Context

Queries are routed to one of three datasets:
1. **INPUT** (`ycs_data.duckdb`) — yield curves (zero rates, par rates), read-only source
2. **CACHE** (`quant_cache_db.sqlite`) — analytics results (bond_analytics, cmt_analytics), writable cache
3. **BOND_ANALYTICS** (`bond_analytics_db.sqlite`) — static bond universe and market data

[`route_query()`](mex://function:25c293fd2a55f62370aaec081f0ed5c9) infers the dataset from keywords (e.g., "zero rate" → INPUT, "CMT" → CACHE). [`_query_dataset()`](mex://function:2318f0e28f325f60a34eb30e7cec4d26) then executes direct commands (/bond, /price, /mctx) or calls mcp-data LLM agent for natural-language SQL planning.

All dataset definitions are in `AppSettings.mcp_datasets` (loaded from `config/cqfi.yaml`), which configures mcp-data server instances.

## Task: Debug Query Routing

### Steps

1. **Identify the routed dataset**
   - Print the query and look for keywords: "yield", "zero" → INPUT; "CMT", "price", "PV" → CACHE; "bond", "issuer" → BOND_ANALYTICS
   - Explicit prefix overrides inference: `input:`, `cache:`, `bond_analytics:` prefix

2. **Check dataset configuration** in `config/cqfi.yaml`
   - Verify the database path exists and is readable
   - Verify mcp-data `semantics` directory has a `.yaml` schema

3. **Inspect mcp-data schema** (if LLM mode)
   - Schema file describes table structure; if it's stale, LLM will plan wrong SQL
   - Rebuild: `mex graph query who-calls "AppSettings"` to find schema loaders

4. **Test the query manually**
   - CLI rule-based: `input: SELECT * FROM zero_rates LIMIT 1`
   - LLM mode: `What are the 10Y yield rates?` (should hit INPUT dataset)

## Task: Add a New Data Source

### Steps

1. **Create or obtain database** (DuckDB or SQLite)
   - Follow naming convention: `config.yaml` references it

2. **Write semantics file** (`semantics/your_dataset.yaml`)
   - mcp-data uses this to understand tables/columns
   - Example:
     ```yaml
     tables:
       your_table:
         description: What this table contains
         columns:
           id: { type: "integer", description: "Row ID" }
           value: { type: "float", description: "Numeric value" }
     ```

3. **Register in `AppSettings.mcp_datasets`** (`config/cqfi.yaml`)
   ```yaml
   datasets:
     your_dataset:
       db: path/to/your_dataset.db
       semantics: semantics/your_dataset.yaml
       keywords: ["keyword1", "keyword2"]  # for inference
   ```

4. **Test routing**
   - CLI: `your_dataset: SELECT * FROM your_table`
   - Or use keyword inference: query mentioning "keyword1" auto-routes to your_dataset

## Gotchas

- **Keywords must be unique** — if "price" is in multiple datasets' keywords, routing is ambiguous
- **Semantics file out of sync** — LLM agent will plan SQL for stale schema; rebuild if tables change
- **Read-only source data** — `ycs_data` must never be written; all writes go to cache dbs only
- **Path resolution** — paths in `config/cqfi.yaml` are resolved relative to project root or via env overrides

## Verify

- [ ] Query routes to the expected dataset (check CLI output or logs)
- [ ] Database file exists at the configured path
- [ ] Semantics file is valid YAML and describes the schema
- [ ] Keywords don't conflict with other datasets
- [ ] In LLM mode, mcp-data server starts without errors
- [ ] Test query returns expected results

## Debug

- **Query ambiguous:** Check keywords across all datasets; add `prefix:` to disambiguate
- **Semantics out of sync:** Rebuild schema, test with `input: SELECT * FROM table_name`
- **mcp-data fails:** Check Claude API key, network, semantics file format
- **Wrong database returned:** Add explicit prefix to query, check `AppSettings.mcp_datasets` in code

## Update Scaffold

- [ ] If a new dataset is added permanently, document it in `context/architecture.md`
- [ ] Update pattern with real gotchas if routing becomes more complex
