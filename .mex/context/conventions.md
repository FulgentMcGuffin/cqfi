---
name: conventions
description: How code is written in this project — naming, structure, patterns, and style. Load when writing new code or reviewing existing code.
triggers:
  - "convention"
  - "pattern"
  - "naming"
  - "style"
  - "how should I"
  - "what's the right way"
edges:
  - target: context/architecture.md
    condition: when a convention depends on understanding the system structure
  - target: context/stack.md
    condition: when a convention involves library-specific patterns (Pydantic, QuantLib)
  - target: patterns/add-quantlib-analytics.md
    condition: when implementing new pricing or analytics metrics
  - target: patterns/query-routing-and-caching.md
    condition: when working with caching, database access, or data models
grounds_to: []
last_updated: 2026-09-20
---

# Conventions

<!-- Read broad, ground tight. Anchor concrete symbols while keeping prose readable:
```markdown
[`someFunction()`](mex://function:<tier-1-id>)
```
-->

## Naming

- **Files**: snake_case (`analytics_calculator.py`, `registry.py`), grouping by domain (e.g., `src/cqfi/quantlib/`, `src/cqfi/agent/`, `src/cqfi/cache/`)
- **Functions / Methods**: snake_case, verb-first (`compute_bond_analytics`, `get_cache_registry`, `route_query`)
- **Classes**: PascalCase, descriptive (`QuantlibAnalyticsCalculator`, `FixedIncomeAnalyticsOutput`, `CacheRegistry`)
- **Database tables / columns**: snake_case, plural for tables (`bond_analytics`, `cmt_analytics`, `calculation_log`)
- **Private methods / functions**: leading underscore (`_bond_curve`, `_uses_curve`)
- **Constants**: UPPER_SNAKE_CASE (`DEFAULT_CONFIG_PATH`, `MARGIN`)

## Structure

- **Module layout**: `src/cqfi/` is organized by domain: `agent/` (CLI), `gui/` (PySide6), `quantlib/` (pricing), `cache/` (persistence), `data/` (data loading), `config.py` (configuration)
- **CLI commands live in `src/cqfi/agent/cli.py`**: `route_query()` routes to dataset, `_query_dataset()` executes, direct commands (pricing, sessions, cache management) short-circuit LLM
- **All analytics outputs go through `QuantlibAnalyticsCalculator`**: `compute_bond_analytics()` and `compute_cmt_analytics()` are the single sources of truth for pricing
- **Caching is centralized in `CacheRegistry`**: all persistence to `quant_cache_db` or `bond_analytics_db` flows through `registry.py`, never direct SQL writes
- **Configuration is lazy-loaded**: `AppSettings.from_yaml()` called once at startup, cached globally in `config.py` via `get_settings()`
- **Tests live in `tests/`, named `test_*.py`**, co-located with the module they test (e.g., `tests/test_cmt.py` tests `src/cqfi/quantlib/cmt.py`)

## Patterns

**Pydantic models for all I/O**: Every function that accepts complex input or returns structured results uses a Pydantic model. Validates on construction, never mid-function.

**No silent failures in QuantLib**: All QuantLib operations are wrapped in try/finally to restore state (e.g., `ql.Settings.instance().evaluationDate`). Pricing failures raise ValueError with context, never return None.

**Single-responsibility cache registry**: All database writes to `quant_cache_db` or `bond_analytics_db` go through `CacheRegistry` methods. Never construct SQL directly. Calling code doesn't check if result exists — `CacheRegistry` handles deduplication, TTL, retries.

**LLM optional**: CLI and GUI both support offline rule-based mode (no Claude API key needed). LLM mode is always a code path, not a requirement. All tool definitions use the Anthropic SDK format.

## Verify Checklist

Before presenting any code:
- [ ] All complex inputs/outputs use Pydantic models, not dicts
- [ ] No writes to `ycs_data` — all analytics go to `quant_cache_db` or `bond_analytics_db` via `CacheRegistry`
- [ ] QuantLib operations restore state in finally block (evaluation date, settings)
- [ ] Pricing/analytics functions never write directly to DB — call `CacheRegistry` instead
- [ ] CLI and GUI both support offline mode (rule-based queries without LLM API)
- [ ] New files follow naming convention (snake_case, grouped by domain in `src/cqfi/`)
- [ ] Type hints on all function signatures, use `| None` not `Optional`
- [ ] No time-series lookups with future data — splits and features are chronological only

```

**Filesystem context (what actually exists):**
`./` contains: CLAUDE.md, IMPLEMENTATION_SUMMARY.md, LICENSE, README.md, batch_bond_analytics.py, config, data, docs, main.py, node_modules, notebooks, package-lock.json, package.json, pyproject.toml, resource, scripts, semantics, shipready_results, src, tests, uv.lock
