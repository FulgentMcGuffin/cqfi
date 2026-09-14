---
name: debug-bond-pricing
description: Diagnose bond pricing errors, wrong yields, or missing analytics
triggers:
  - "pricing wrong"
  - "yield incorrect"
  - "analytics missing"
  - "bond fails"
  - "price error"
  - "QuantLib error"
edges:
  - target: context/conventions.md
    condition: when understanding the state-restoration pattern
  - target: context/architecture.md
    condition: when understanding the pricing → cache → output flow
grounds_to:
  - node: "method:898d2cb9ef786ab658e7f3f4e2170870"
    fingerprint: "mh:64:0xc5f1d3e8a2b9"
last_updated: 2026-09-14
---

# Debug Bond Pricing Failures

## Context

Bond pricing flows through [`QuantlibAnalyticsCalculator.compute_bond_analytics()`](mex://method:898d2cb9ef786ab658e7f3f4e2170870), which:
1. Loads issuer profile and curve from market context
2. Builds the bond object with QuantLib conventions (day-count, frequency)
3. Prices it on the curve
4. Computes analytics (yield, duration, convexity, etc.)

Failures occur at: bond construction (bad dates/coupon), curve building (interpolation), pricing (engine setup), or analytics (missing convention).

## Task: Pricing Command Returns Error

### Steps

1. **Reproduce the error**
   - CLI: `price cmt USA 2024-10-15 5y` or `price bond USBT1234 2024-10-15`
   - Note the exact error message

2. **Check bond exists**
   - `bond_analytics: SELECT * FROM bond_universe WHERE bond_id = '<id>'`
   - Verify maturity, coupon, and issue date are valid (not NULL, not in future)

3. **Check curve exists**
   - CLI: `/mctx USA 2024-10-15` (this builds market context)
   - If it fails, ycs_data is missing rates for that issuer/date

4. **Isolate the failure point**
   - Add logging to `compute_bond_analytics()` before each major step:
     - Bond construction
     - Curve loading
     - Pricing engine setup
     - Analytics computation
   - Re-run and capture the exact line that fails

5. **Verify issuer profile**
   - Check `src/cqfi/quantlib/issuers.py` for the issuer code
   - Ensure day-count, frequency, and settlement convention are correct
   - QuantLib mismatch (e.g., wrong day-count) causes silent or loudly wrong prices

## Task: Pricing Returns, but Values Are Wrong

### Steps

1. **Validate input**
   - Maturity date is after settlement date
   - Coupon is positive (or 0 for zero-coupon)
   - Trade date is before or equal to today (no look-ahead)

2. **Cross-check with external pricer**
   - Use QuantLib directly in a test script to price the same bond
   - Manually verify the curve (zero rates) are sensible

3. **Check curve interpolation**
   - Curve method defaults to `LINEAR_ZERO`; verify that's what you want
   - Test alternative interpolation in CLI: `compute_bond_analytics(..., interpolation=CUBIC_ZERO)`

4. **Inspect cached result**
   - `cache: SELECT * FROM bond_analytics WHERE bond_id = '<id>' AND trade_date = '<date>'`
   - If cached and wrong, check if the trade_date or coupon was different when cached
   - Cached results are not auto-invalidated; manual deletion required

5. **Check QuantLib settings pollution**
   - Verify no other code left QuantLib's evaluation date or settings in a dirty state
   - The code uses try/finally to restore, but check for uncaught exceptions

## Task: Analytics Computation Hangs or Times Out

### Steps

1. **Check for infinite loops**
   - Large batches (>1000 bonds) on slow curves can take hours
   - Monitor disk I/O and CPU; if flat, likely a QuantLib blocking operation

2. **Reduce batch size**
   - Test with 1 bond first: `price bond <id>`
   - Then test with 5-10 bonds to isolate slow issuer/date combinations

3. **Check curve fitting**
   - Certain interpolation methods (cubic spline) are slower than linear
   - Try `LINEAR_ZERO` to see if it's the fitting step

4. **Check for DB locks**
   - If writing to cache, verify no other process has `quant_cache_db` locked
   - Use `fuser quant_cache_db.sqlite` (Linux) or `tasklist` (Windows) to find holders

## Gotchas

- **QuantLib state pollution:** Evaluation date or settings left dirty by prior code → wrong prices. Check finally blocks.
- **Silent failures:** QuantLib can return NaN or 0.0 instead of raising an error. Always check output is sensible.
- **Cached stale results:** `quant_cache_db` caches forever unless explicitly cleared. Manual row deletion is required.
- **Issuer convention mismatch:** Day-count, compounding, frequency mismatches cause pricing divergence from market quotes.
- **Curve extrapolation:** If requesting yield beyond curve, some methods will fail; others will flat-forward. Check `issuers.py` default.

## Verify

- [ ] Bond exists in `bond_universe` with valid dates and coupon
- [ ] Curve exists for the issuer/date (test with `/mctx`)
- [ ] Issuer conventions in `issuers.py` match market (day-count, frequency, settlement)
- [ ] QuantLib try/finally blocks restore state after pricing
- [ ] Cached results have been invalidated if formula/convention changed
- [ ] Pricing output is sensible (not NaN, sign is correct, magnitude is reasonable)

## Debug

- **"Bond not found"** → check `bond_universe` table, verify bond_id format
- **"No rates available"** → check ycs_data for the issuer/date, try `/mctx` first
- **"Pricing engine failed"** → verify issuer in `issuers.py`, day-count and frequency
- **"Hangs indefinitely"** → reduce batch size, check DB locks, try LINEAR_ZERO
- **"Values are wrong"** → cross-check with QuantLib directly, inspect cached row, check QuantLib state

## Update Scaffold

- [ ] If a new issuer is added, document its conventions and any quirks
- [ ] If pricing bugs are found and fixed, add specific test case to `tests/test_quantlib_analytics_calculator.py`
