---
name: stack
description: Technology stack, library choices, and the reasoning behind them. Load when working with specific technologies or making decisions about libraries and tools.
triggers:
  - "library"
  - "package"
  - "dependency"
  - "which tool"
  - "technology"
edges:
  - target: context/decisions.md
    condition: when the reasoning behind a tech choice is needed
  - target: context/conventions.md
    condition: when understanding how to use a technology in this codebase
  - target: patterns/query-routing-and-caching.md
    condition: when working with mcp-data or framecache libraries
grounds_to: []
last_updated: 2026-09-14
---

# Stack

<!-- Keep grounding sparse here. For a concrete wrapper or adapter mention, use:
```markdown
[`someFunction()`](mex://function:<tier-1-id>)
```
-->

## Core Technologies

- **Python 3.11+** — primary language, all code type-hinted with PEP 8 strict
- **QuantLib** (latest wheel) — fixed-income pricing, yield curve modeling, bond/CMT analytics
- **PySide6** — GUI framework for desktop application, frameless windows with custom resize
- **polars** — data manipulation (preferred over pandas for performance)
- **plotnine** — ggplot2-style charting for GUI and CLI rendering
- **SQLite** — persistent cache storage (`quant_cache_db`, `bond_analytics_db`), read-only `ycs_data` source

## Key Libraries

- **mcp-data** — LLM-powered SQL query planning; wraps Claude API to translate natural-language questions into structured SQL
- **framecache** — local SQLite result caching with optional TTL; `CacheRegistry` wraps it for persistence
- **anthropic** (Claude SDK) — Claude API client for LLM mode; optional, only loaded when `--llm` flag or `ANTHROPIC_API_KEY` is set
- **pydantic v2** — data validation; all request/response types (e.g., `BondAnalyticsInput`, `FixedIncomeAnalyticsOutput`)
- **pyyaml** — configuration file parsing (`config/cqfi.yaml`)
- **pytest** — all tests use pytest style, no unittest
- **uv** — package manager; all Python runs through `uv run`, never global or conda

## What We Deliberately Do NOT Use

- No pandas — use polars only for performance and stricter typing
- No TensorFlow — use PyTorch for neural networks (if any ML code added)
- No FastAPI/Flask — this is a CLI + GUI tool, not an API server
- No async/await for I/O — threading (GUI worker threads) for responsiveness, no asyncio event loop

## Version Constraints
<!-- Only fill this if there are important version-specific things to know.
     Leave empty if there are no meaningful version constraints.
     Example: "We are on React 17, not 18 — concurrent features are not available." -->
