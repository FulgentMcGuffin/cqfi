---
name: architecture
description: How the major pieces of this project connect and flow. Load when working on system design, integrations, or understanding how components interact.
triggers:
  - "architecture"
  - "system design"
  - "how does X connect to Y"
  - "integration"
  - "flow"
edges:
  - target: context/stack.md
    condition: when specific technology details (QuantLib, mcp-data, PySide6) are needed
  - target: context/decisions.md
    condition: when understanding why the architecture is structured this way
  - target: context/conventions.md
    condition: when implementing components following project patterns
  - target: patterns/add-quantlib-analytics.md
    condition: when adding new pricing or analytics to the system
  - target: patterns/debug-bond-pricing.md
    condition: when diagnosing pricing failures or incorrect results
grounds_to:
  - node: "method:898d2cb9ef786ab658e7f3f4e2170870"
    fingerprint: "mh:64:0x4d1c3e8b2f9a5c7e"
  - node: "class:f207c80a0029b88b8c57b910811655f8"
    fingerprint: "mh:64:0xa9f3e2c1b8d4f6"
last_updated: 2026-09-14
---

# Architecture

<!-- Read broad, ground tight. Architecture usually grounds sparsely. When a
     specific symbol is worth navigating to, use this inline form:
```markdown
[`someFunction()`](mex://function:<tier-1-id>)
```
-->

## System Overview

User input (CLI query or GUI chat) → route to dataset (INPUT / CACHE / BOND_ANALYTICS) → if LLM mode, call mcp-data agent to plan SQL, else parse rule-based syntax → execute query/command → QuantLib pricing when needed → persist results to `quant_cache_db` via `CacheRegistry` → return structured output (metrics, analytics, bond data) → serialize for CLI (plain text) or GUI (tables + charts).

## Key Components

- **CLI REPL** (`src/cqfi/agent/cli.py`, `_amain`) — interactive query loop, routes queries to datasets, executes direct commands (pricing, session mgmt), calls LLM agent in --llm mode
- **QuantLib Analytics** (`src/cqfi/quantlib/quantlib_analytics_calculator.py`, `QuantlibAnalyticsCalculator`) — prices bonds and CMTs, computes yield, duration, convexity, analytics (yield rolls, market context)
- **Cache Registry** (`src/cqfi/cache/registry.py`, `CacheRegistry`) — wraps framecache, persists analytics to SQLite (`quant_cache_db`), maintains `bond_analytics` and `cmt_analytics` tables
- **Configuration System** (`src/cqfi/config.py`, `AppSettings`) — loads YAML paths + env overrides, resolves all runtime paths, maintains mcp-data dataset registry
- **GUI Chat** (`src/cqfi/gui/app.py`, `src/cqfi/gui/chat_dialog.py`) — PySide6 window with LLM worker thread, routes natural-language queries the same way CLI does, renders tables + charts

## External Dependencies

- **DuckDB / SQLite** (`ycs_data.duckdb` or `.sqlite`) — read-only yield curve database (zero rates, par rates, FX spot). Read-only constraint enforced; all writes go to `quant_cache_db` or `bond_analytics_db`.
- **QuantLib** — fixed-income pricing library; bootstraps yield curves, prices bonds/CMTs, computes analytics (yield, duration, convexity, z-spread).
- **mcp-data** — natural-language SQL planning; wraps Claude API to plan and execute queries against multiple datasets (ycs_data, quant_cache, bond_analytics) in LLM mode.
- **Claude API** (optional) — required only for LLM mode (`--llm`). Set `ANTHROPIC_API_KEY` in `.env` to enable; rule-based mode works offline.

## What Does NOT Exist Here

- No write access to source yield curve data (`ycs_data`) — this is a read-only reference database. Violations break data integrity guarantees.
- No look-ahead bias — time-series models and features cannot use future data. All splits are chronological.
- No user authentication / authorization — this is a research/analysis tool, not a multi-tenant service.
- No background job queue — batch bond/future analytics are standalone CLI processes that run in-process or via GUI dialogs, not a separate worker service.
- No external message brokering (no Kafka/RabbitMQ) — inter-process communication is via SQLite and in-memory caches only.
