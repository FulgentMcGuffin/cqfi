---
name: setup
description: Dev environment setup and commands. Load when setting up the project for the first time or when environment issues arise.
triggers:
  - "setup"
  - "install"
  - "environment"
  - "getting started"
  - "how do I run"
  - "local development"
edges:
  - target: context/stack.md
    condition: when specific technology versions or library details are needed
  - target: context/architecture.md
    condition: when understanding how components connect during setup
  - target: patterns/debug-bond-pricing.md
    condition: when QuantLib installation or initialization fails
grounds_to: []
last_updated: 2026-09-14
---

# Setup

<!-- Commands and environment facts need no code grounding. For a concrete symbol:
```markdown
[`someFunction()`](mex://function:<tier-1-id>)
```
-->

## Prerequisites

- **Python 3.11+** (check `python --version`)
- **uv** package manager (`pip install uv` or from https://github.com/astral-sh/uv)
- **QuantLib** (installed via `uv sync`, requires pre-built wheel on Windows)
- **Access to data files**: `ycs_data.duckdb` or `ycs_data.sqlite` (read-only), path configured in `config/cqfi.yaml`

## First-time Setup

1. Clone repo and enter directory: `cd D:\Code\cqfi`
2. Sync environment: `uv sync` (installs all dependencies including QuantLib)
3. Copy `.env` for LLM mode: `copy .env.example .env` and set `ANTHROPIC_API_KEY` (optional, CLI works offline without it)
5. Verify setup: `uv run pytest tests/test_cmt.py -v` (runs a quick smoke test)
6. Launch CLI: `uv run cqfi` or GUI: `uv run cqfi-gui`

## Environment Variables

**Optional (LLM mode only):**
- `ANTHROPIC_API_KEY` — Claude API key; enables `--llm` flag and LLM chat in GUI. If unset, CLI and GUI both work offline with rule-based mode.

**Optional (overrides config file):**
- `CQFI_CONFIG` — path to YAML config file (default: `config/cqfi.yaml`)
- `CQFI_YCS_DB` — yield curve database path (overrides YAML)
- `CQFI_YCS_SEMANTICS` — mcp-data semantics directory (overrides YAML)
- `CQFI_BOND_ANALYTICS_DB` — bond analytics database path (overrides YAML)
- `CQFI_QUANT_CACHE_DB` — cache database path (overrides YAML)

**Debug (optional):**
- `CQFI_LANGSMITH` — set to `0` to disable LangSmith tracing (normally off by default)

## Common Commands

- **`uv run cqfi`** — Start CLI REPL in rule-based mode (offline, no LLM)
- **`uv run cqfi --llm`** — Start CLI REPL with LLM mode (requires `ANTHROPIC_API_KEY`)
- **`uv run cqfi-gui`** — Launch GUI chat window
- **`uv run pytest`** — Run all tests
- **`uv run pytest tests/test_cmt.py -v`** — Run specific test file with verbose output
- **`uv sync`** — Update/refresh environment from `pyproject.toml`
- **`uv add <package>`** — Add new dependency
- **`uv run python src/quantlib/analytics_calculator.py`** — Run module self-check (smoke test)

## Common Issues

**QuantLib import fails on Windows:** Pre-built wheels are required. If `import ql` fails, verify QuantLib is installed via `pip show QuantLib`. If missing, try `uv sync --force-reinstall`.

**Config file not found:** Set `CQFI_CONFIG` to the full path, or ensure `config/cqfi.yaml` exists. See CLAUDE.md for config format and options.

**`ycs_data.duckdb` not found:** Update the `paths.ycs_db` entry in `config/cqfi.yaml` to the correct absolute path. Path is validated at startup in `AppSettings.from_yaml()`.

**LLM mode throws 401 / 403 errors:** Verify `ANTHROPIC_API_KEY` is set correctly in `.env`. Errors from mcp-data indicate the API key is invalid or expired.

**Tests fail with "database is locked":** Close any other processes accessing the test database. If running tests in parallel, use `pytest -n 1` to disable parallelism.

```

**Filesystem context (what actually exists):**
`config/` contains: cqfi.yaml