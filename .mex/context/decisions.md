---
name: decisions
description: Key architectural and technical decisions with reasoning. Load when making design choices or understanding why something is built a certain way.
triggers:
  - "why do we"
  - "why is it"
  - "decision"
  - "alternative"
  - "we chose"
edges:
  - target: context/architecture.md
    condition: when a decision relates to system structure
  - target: context/stack.md
    condition: when a decision relates to technology choice
  - target: patterns/debug-bond-pricing.md
    condition: when understanding why QuantLib is the single source of truth
  - target: patterns/query-routing-and-caching.md
    condition: when understanding why read/write databases are separated
grounds_to: []
last_updated: 2026-09-14
---

# Decisions

<!-- HOW TO USE THIS FILE:
     Each decision follows the format below.
     When a decision changes: DO NOT delete the old entry.
     Mark it as superseded, add the new entry above it.
     The history must be preserved — this is the event clock. -->

## Decision Log

### Separate read-only source DB from writable analytics/cache DBs
**Date:** 2024-06-01 (inferred from architecture)
**Status:** Active
**Decision:** `ycs_data` (yield curves, FX) is read-only; all analytics and cache live in `quant_cache_db` and `bond_analytics_db`.
**Reasoning:** Enforces data integrity and prevents accidental corruption of the source of truth. Analytics are reproducible only if they can be recomputed from a fixed input.
**Alternatives considered:** Single database with separate tables (rejected — too easy to accidentally overwrite source data). Microservices with separate DBs (rejected — over-engineered for a single-machine tool).
**Consequences:** All writes must go through `CacheRegistry`, never direct SQL. Code audit must verify no writes touch `ycs_data`.

### LLM is optional, rule-based mode always available
**Date:** 2024-07-15 (inferred from CLI structure)
**Status:** Active
**Decision:** CLI and GUI support both LLM-powered natural-language queries and offline rule-based syntax. LLM requires Claude API key but is never mandatory.
**Reasoning:** Allows offline usage, reduces vendor lock-in, testing/debugging without API calls, clear separation between rule-based and AI logic.
**Alternatives considered:** LLM-only (rejected — too high friction, breaks offline workflows). Rule-based only (rejected — defeats the purpose of LLM integration). Async fallback (rejected — adds complexity).
**Consequences:** All direct commands (`/price`, `/bond`, `/mctx`, etc.) are rule-based. LLM augments with natural-language planning but never owns the data access layer.

### QuantLib as the single source of truth for pricing
**Date:** 2024-08-01 (inferred from quantlib module structure)
**Status:** Active
**Decision:** All bond pricing and analytics flow through `QuantlibAnalyticsCalculator`. No alternative pricing engines or hand-rolled calculations.
**Reasoning:** QuantLib is battle-tested, handles curve interpolation/fitting edge cases, maintains QuantLib conventions (day-count, calendars) consistently.
**Alternatives considered:** Building our own pricer (rejected — reinventing wheels, hard to debug). Caching third-party API results (rejected — adds operational complexity, slower).
**Consequences:** Any pricing bug or assumption change requires an audit of all cached results. New analytics methods must be added to `QuantlibAnalyticsCalculator` class.
