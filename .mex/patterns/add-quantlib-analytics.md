---
name: add-quantlib-analytics
description: Add a new bond analytics metric to QuantlibAnalyticsCalculator
triggers:
  - "new analytics"
  - "add metric"
  - "compute analytics"
  - "bond metrics"
edges:
  - target: context/conventions.md
    condition: when following naming/structure patterns for new methods
  - target: context/architecture.md
    condition: when understanding the data flow from pricing to cache
grounds_to:
  - node: "method:a0f366842e00f4d621e6d27991ced4d3"
    fingerprint: "mh:64:0x7f8c3a9d5e1b6"
last_updated: 2026-09-14
---

# Add QuantLib Analytics Metric

## Context

New metrics are added to the [`QuantlibAnalyticsCalculator`](mex://class:fd95f24c418905b47bec5714d35cdf9c) class, specifically in [`_bond_metrics_from_priced_bond()`](mex://method:a0f366842e00f4d621e6d27991ced4d3). All bond and CMT calculations flow through this class, which wraps QuantLib and persists results via `CacheRegistry`.

Never add metric calculation directly to CLI tools or cache layer. Always add to the calculator, then call it from the appropriate entry point.

## Steps

1. **Add to `FixedIncomeAnalyticsOutput` pydantic model** (`src/cqfi/quantlib/types.py`)
   - Define the new field with type hint and docstring
   - Example: `duration_years: float = Field(..., description="Modified duration in years")`

2. **Implement calculation in `_bond_metrics_from_priced_bond()`** (`src/cqfi/quantlib/quantlib_analytics_calculator.py`)
   - Calculate the metric from the priced bond object
   - Wrap QuantLib calls in try/finally to restore state (evaluation date, settings)
   - Never leave QuantLib in a dirty state

3. **Test the calculation**
   - Add a test case to `tests/test_quantlib_analytics_calculator.py`
   - Use real bond data from `ycs_data` or a fixture
   - Verify the metric makes sense (e.g., positive duration, reasonable yield)

4. **Update cache table schema** (if the metric didn't exist before)
   - `CacheRegistry` flattens all fields into `bond_analytics` table
   - Run `mex graph query who-calls "CacheRegistry"` to find caching code
   - Ensure new column is added via migration or rebuild

5. **Call from appropriate CLI/GUI entry point**
   - Direct command: `price bond …` or `/calc bond_id`
   - LLM tool: tool definition in `cli_tools.py` already wraps `compute_bond_analytics()`

## Gotchas

- **QuantLib state pollution:** Always save/restore settings like evaluation date in finally blocks
- **No floating-point comparisons:** Test with tol ranges, not exact equality
- **Cached results won't update:** If a metric changes, clear `quant_cache_db` or manually delete stale rows
- **Missing issuer profile:** The issuer must be defined in `issuers.py` with QuantLib conventions

## Verify

- [ ] New field in `FixedIncomeAnalyticsOutput` dataclass
- [ ] Calculation implemented in `_bond_metrics_from_priced_bond()`
- [ ] QuantLib state is restored (finally block present)
- [ ] Unit test passes with real bond data
- [ ] Metric values are sensible (no NaNs, signs are correct, ranges are reasonable)
- [ ] Cache table schema was updated if needed

## Debug

If the new metric is missing or wrong:
- Check `FixedIncomeAnalyticsOutput` has the field
- Verify `_bond_metrics_from_priced_bond()` was called (breakpoint or logging)
- Inspect cached row in `quant_cache_db`: `SELECT * FROM bond_analytics WHERE bond_id = '<id>'`
- Clear cache and recompute: `quant_cache_db` is writable, delete stale rows or drop table

## Update Scaffold

- [ ] Update `context/conventions.md` if new patterns emerge
- [ ] If this becomes a repeating task, update this pattern with real gotchas
