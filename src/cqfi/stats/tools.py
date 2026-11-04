"""LLM tools: (rolling) linear / logistic regression and covariance / correlation matrices.

Each tool returns a JSON-able dict with ``columns``/``rows`` (so the GUI can table and
plot it), compact ``summary`` statistics and a plain-language ``explanation`` that the
LLM is told to relay. Row output is thinned to ``MAX_ROWS`` to protect the LLM context.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from langchain_core.tools import StructuredTool

from cqfi.config import get_settings
from cqfi.stats import kernels as K
from cqfi.stats.data import (
    apply_lookback,
    apply_transform,
    load_aligned,
    parse_spec,
    resolve_specs,
)

MAX_ROWS = 250
DEFAULT_WINDOW = 20
_DECIMALS = 6
_TRANSFORM_TEXT = {
    "level": "levels (no transformation)",
    "diff": "first differences (change in the rate, in rate units, i.e. percentage points)",
    "pct_change": "simple percentage changes",
    "log_return": "log returns",
}


def _db() -> Path:
    return get_settings().ycs_db_path


def _prepare(series: list[str], start, end, lookback, transform):
    specs = resolve_specs(_db(), [parse_spec(s) for s in dict.fromkeys(series)])
    frame, used = apply_transform(load_aligned(_db(), specs, start, end), specs, transform)
    return specs, apply_lookback(frame, lookback), used


def _thin(df: pl.DataFrame) -> tuple[pl.DataFrame, bool]:
    """Evenly thin to at most MAX_ROWS rows, always keeping the last."""
    if df.height <= MAX_ROWS:
        return df, False
    idx = np.unique(np.linspace(0, df.height - 1, MAX_ROWS).round().astype(int))
    return df[idx], True


def _clean(obj: Any) -> Any:
    """NaN -> None (valid JSON) throughout nested summary data."""
    if isinstance(obj, float):
        return None if obj != obj else obj
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def _payload(df: pl.DataFrame, **extra: Any) -> dict:
    df = df.with_columns(pl.col(pl.Float64).fill_nan(None).round(_DECIMALS), pl.col(pl.Date).cast(pl.String))
    thin, thinned = _thin(df)
    return {"status": "success", "columns": thin.columns, "rows": thin.to_dicts(),
            "rows_thinned_to": thin.height if thinned else None, **_clean(extra)}


def _error(exc: Exception) -> dict:
    return {"status": "error", "message": f"{type(exc).__name__}: {exc}"}


def _period(frame: pl.DataFrame) -> str:
    return f"{frame['date'].min()} to {frame['date'].max()} ({frame.height} observations)"


def _notes(specs) -> str:
    """Resolved swap/repo curve variants, as a sentence tail (empty when none)."""
    return "".join(f"{s.note}. " for s in specs if s.note)


def _transform_text(used: dict[str, str]) -> str:
    groups: dict[str, list[str]] = {}
    for label, how in used.items():
        groups.setdefault(how, []).append(label)
    if len(groups) == 1:
        return _TRANSFORM_TEXT[next(iter(groups))]
    return "; ".join(f"{_TRANSFORM_TEXT[how]} for {', '.join(labels)}" for how, labels in groups.items())


def _window_plan(n: int, rolling: bool, window: int, step: int) -> tuple[int, int]:
    if not rolling:
        return n, 1
    if step < 1:
        raise ValueError("step must be >= 1")
    if window > n:
        raise ValueError(f"window {window} exceeds the {n} available observations")
    return window, step


def _coef_summary(names: list[str], series: np.ndarray, dates: list) -> dict:
    """latest / mean / min / max per coefficient across windows."""
    return {
        name: {"latest": float(col[-1]), "mean": float(np.nanmean(col)),
               "min": float(np.nanmin(col)), "max": float(np.nanmax(col))}
        for name, col in zip(names, series.T)
    } | {"latest_window_end": str(dates[-1])}


def linear_regression(
    target: str,
    predictors: list[str],
    rolling: bool = False,
    window: int = DEFAULT_WINDOW,
    step: int = 1,
    transform: str = "auto",
    start: str | None = None,
    end: str | None = None,
    lookback: str | None = None,
) -> dict:
    """Ordinary least squares of one series on one or more others; optionally rolling over time.

    Use for betas / sensitivities / hedge ratios (e.g. "rolling betas of the Swiss 10y par
    rate versus the US 10y par rate" -> target "par:CHE:10Y", predictors ["par:USA:10Y"],
    rolling True). With rolling=True a regression is fitted on every sliding window of
    `window` observations (default 20) advancing `step` observations (default 1 = daily).
    After the call, explain to the user what was done using the returned `explanation`
    (series, transformation, period, window, how to read the beta) and quote key numbers
    from `summary`.

    Series format: "par:<issuer>:<tenor>" or "zero:<issuer>:<tenor>" (e.g. "par:CHE:10Y"),
    "swap:<ccy>:<tenor>" (swap par rate, e.g. "swap:USD:10Y") or "repo:<ccy>:<tenor>" (repo/RFR
    rate), or "fx:<pair>" (e.g. "fx:USDCHF", "fx:JPY"). Swap and repo series are different curves,
    never interchangeable. If a currency has several swap/repo variants the tool errors and lists
    them; then append the periods and index, e.g. "swap:EUR:5Y:6M:1Y:ESTR" (float period, fixed
    period, index) or "repo:EUR:2Y:3M:ESTR" (period, index).

    Args:
        target: Dependent series, in the series format above.
        predictors: Independent series, in the series format above.
        rolling: True for sliding-window fits over time; False for one fit on the whole period.
        window: Observations per sliding window when rolling (default 20).
        step: Observations between consecutive window ends when rolling (default 1).
        transform: "auto" (default: rates -> first differences, FX -> log returns), "level",
            "diff", "pct_change" or "log_return". Prefer changes for stationarity.
        start: Optional first date "YYYY-MM-DD".
        end: Optional last date "YYYY-MM-DD".
        lookback: Optional period ending at the latest available observation, e.g. "1M",
            "3M", "1Y", "20D"; use for "last month" / "last year" questions.

    Returns:
        Dict with status, explanation, summary and a table (columns, rows). Rolling: one row
        per window end with intercept, beta[...], t[...] and r2. Static: one row per term.
    """
    try:
        specs, frame, used = _prepare([target, *predictors], start, end, lookback, transform)
        tgt, preds = specs[0], specs[1:]
        if tgt.label in {p.label for p in preds}:
            raise ValueError("target must not also be a predictor")
        n = frame.height
        w, st = _window_plan(n, rolling, window, step)
        x = frame.select(p.label for p in preds).to_numpy()
        fit = K.rolling_ols(x, frame[tgt.label].to_numpy(), w, st)
        names = [p.label for p in preds]
        ends = frame["date"].gather(K.window_ends(n, w, st).tolist())
        common = (f"Linear regression (OLS with intercept) of {tgt.label} on {', '.join(names)}, "
                  f"using {_transform_text(used)}. Data: {_period(frame)}. {_notes(specs)}")
        if rolling:
            table = pl.DataFrame({"date": ends, "intercept": fit.intercept})
            for j, nm in enumerate(names):
                table = table.with_columns(pl.Series(f"beta[{nm}]", fit.beta[:, j]),
                                           pl.Series(f"t[{nm}]", fit.t_stat[:, j]))
            table = table.with_columns(pl.Series("r2", fit.r2))
            explanation = common + (
                f"Rolling fit over sliding windows of {w} observations advancing {st} at a time "
                f"({len(ends)} windows; dates are window ends). beta = change in {tgt.label} per unit "
                "change in the predictor within each window; t = beta / standard error; r2 = share of "
                "variance explained. Short windows give noisy betas.")
            summary = _coef_summary([f"beta[{m}]" for m in names], fit.beta, ends.to_list()) \
                | {"mean_r2": float(np.nanmean(fit.r2))}
        else:
            table = pl.DataFrame({
                "term": ["intercept", *names],
                "coef": [fit.intercept[0], *fit.beta[0]],
                "t_stat": [float("nan"), *fit.t_stat[0]],
            })
            explanation = common + (
                f"One regression over the whole period; r2 = {fit.r2[0]:.3f}. coef = change in "
                f"{tgt.label} per unit change in each predictor; t_stat = coef / standard error.")
            summary = {"r2": float(fit.r2[0]), "n_obs": n}
        return _payload(table, message=explanation, explanation=explanation, summary=summary, n_obs=n)
    except Exception as exc:  # tool boundary: report, never raise into the agent loop
        return _error(exc)


def _event_series(values: np.ndarray, event: str) -> np.ndarray:
    if event not in ("up", "down"):
        raise ValueError("event must be 'up' or 'down'")
    return (values > 0 if event == "up" else values < 0).astype(float)


def _scenarios(names, binary, sds, intercept, beta) -> list[dict]:
    """P(target event) when one predictor takes its event / +-1 sd value and the others are 0."""
    k = len(names)
    out = [{"scenario": "no predictor event" if binary else "all predictors unchanged",
            "probability": K.logit_probability(intercept, beta, np.zeros(k))}]
    for j, nm in enumerate(names):
        for label, val in ([(f"{nm} event", 1.0)] if binary else
                           [(f"{nm} +1 sd move", sds[j]), (f"{nm} -1 sd move", -sds[j])]):
            v = np.zeros(k)
            v[j] = val
            out.append({"scenario": label, "probability": K.logit_probability(intercept, beta, v)})
    return out


def logistic_regression(
    target: str,
    predictors: list[str],
    target_event: str = "up",
    predictor_event: str | None = None,
    rolling: bool = False,
    window: int = DEFAULT_WINDOW,
    step: int = 1,
    transform: str = "auto",
    start: str | None = None,
    end: str | None = None,
    lookback: str | None = None,
) -> dict:
    """Logistic regression for the probability that the target series moves up/down given other moves.

    The target becomes a 0/1 event ("target went up" / "went down" over one observation). Use for
    questions like "what is the probability of the Swiss franc strengthening if the yen weakens
    over the last year?". FX direction: USDxxx pairs (USDCHF, USDJPY, USDCAD, ...) RISE when USD
    strengthens, i.e. xxx WEAKENS, so "CHF strengthens" = fx:USDCHF target_event "down" and "JPY
    weakens" = fx:USDJPY predictor_event "up"; xxxUSD pairs (EURUSD, GBPUSD, AUDUSD, NZDUSD) rise
    when xxx strengthens. Set predictor_event to use "predictor moved up/down" as 0/1 conditions
    (then the scenarios give P(target event | predictor event) directly); leave it None to use the
    size of the predictor move. rolling=True fits every sliding window (default 20 observations,
    step 1). After the call, explain to the user what was done using the returned `explanation`
    and quote the scenario probabilities and the number of observations (small samples are weak
    evidence).

    Series format: "par:<issuer>:<tenor>" or "zero:<issuer>:<tenor>" (e.g. "par:CHE:10Y"),
    "swap:<ccy>:<tenor>" (swap par rate, e.g. "swap:USD:10Y") or "repo:<ccy>:<tenor>" (repo/RFR
    rate), or "fx:<pair>" (e.g. "fx:USDCHF", "fx:JPY"). Swap and repo series are different curves,
    never interchangeable. If a currency has several swap/repo variants the tool errors and lists
    them; then append the periods and index, e.g. "swap:EUR:5Y:6M:1Y:ESTR" (float period, fixed
    period, index) or "repo:EUR:2Y:3M:ESTR" (period, index).

    Args:
        target: Series whose move defines the event, in the series format above.
        predictors: Conditioning series, in the series format above.
        target_event: "up" (target rose) or "down" (target fell). Default "up".
        predictor_event: None to use predictor move sizes; "up" or "down" to use 0/1 indicators of
            the predictor rising/falling.
        rolling: True for sliding-window fits over time; False for one fit on the whole period.
        window: Observations per sliding window when rolling (default 20).
        step: Observations between consecutive window ends when rolling (default 1).
        transform: Change transform: "auto" (default: rates -> differences, FX -> log returns),
            "diff", "pct_change" or "log_return". Levels are not allowed.
        start: Optional first date "YYYY-MM-DD".
        end: Optional last date "YYYY-MM-DD".
        lookback: Optional period ending at the latest observation, e.g. "1M", "1Y".

    Returns:
        Dict with status, explanation, summary (coefficients, odds ratios, scenario probabilities,
        base rate) and a table (columns, rows). Rolling: per window end, intercept, beta[...],
        z[...] and prob[...] (event probability when that predictor has its event / +1 sd move).
    """
    try:
        if transform == "level":
            raise ValueError("logistic regression needs a change transform, not 'level'")
        specs, frame, used = _prepare([target, *predictors], start, end, lookback, transform)
        tgt, preds = specs[0], specs[1:]
        if tgt.label in {p.label for p in preds}:
            raise ValueError("target must not also be a predictor")
        names = [p.label for p in preds]
        n = frame.height
        w, st = _window_plan(n, rolling, window, step)
        y = _event_series(frame[tgt.label].to_numpy(), target_event)
        raw = frame.select(names).to_numpy()
        x = raw if predictor_event is None else _event_series(raw, predictor_event)
        if not rolling and (y.min() == y.max()):
            raise ValueError(f"{tgt.label} never had a '{target_event}' move (or always did) in this period")
        fit = K.rolling_logit(x, y, w, st)
        binary = predictor_event is not None
        sds = x.std(axis=0)
        ends = frame["date"].gather(K.window_ends(n, w, st).tolist())
        cond = (f"predictors as 0/1 indicators of a '{predictor_event}' move" if binary
                else "predictors as the size of their moves")
        common = (f"Logistic regression of the event '{tgt.label} moved {target_event}' (1 = yes, 0 = no) on "
                  f"{', '.join(names)}, {cond}, using {_transform_text(used)}. Data: {_period(frame)}; "
                  f"the event occurred in {y.mean():.1%} of observations. {_notes(specs)}A small ridge penalty "
                  "stabilises fits; windows where the event is constant are NaN. ")
        if rolling:
            table = pl.DataFrame({"date": ends, "intercept": fit.intercept})
            probs = []
            for j, nm in enumerate(names):
                val = 1.0 if binary else sds[j]
                probs.append(K._sigmoid(fit.intercept + fit.beta[:, j] * val))
                table = table.with_columns(pl.Series(f"beta[{nm}]", fit.beta[:, j]),
                                           pl.Series(f"z[{nm}]", fit.z_stat[:, j]),
                                           pl.Series(f"prob[{nm}]", probs[-1]))
            explanation = common + (
                f"Rolling fit over sliding windows of {w} observations advancing {st} at a time "
                f"({len(ends)} windows; dates are window ends). prob[...] = fitted probability of the "
                f"event when that predictor has its {'event' if binary else '+1 sd move'} and the others "
                "are 0. Windows this short are noisy.")
            summary = _coef_summary([f"prob[{m}]" for m in names], np.column_stack(probs), ends.to_list())
        else:
            b, a = fit.beta[0], float(fit.intercept[0])
            table = pl.DataFrame({
                "term": ["intercept", *names], "coef": [a, *b],
                "odds_ratio": [float("nan"), *np.exp(b)], "z_stat": [float("nan"), *fit.z_stat[0]],
            })
            scen = _scenarios(names, binary, sds, a, b)
            explanation = common + (
                "One fit over the whole period. odds_ratio > 1 means the predictor raises the event "
                "probability; scenarios list fitted probabilities.")
            summary = {"base_rate": float(y.mean()), "n_obs": n, "scenarios": scen}
            if binary and len(names) == 1:
                hit, miss = y[x[:, 0] == 1], y[x[:, 0] == 0]
                summary["empirical"] = {"event_count": int(hit.size), "event_hits": int(hit.sum()),
                                        "no_event_count": int(miss.size), "no_event_hits": int(miss.sum())}
        return _payload(table, message=explanation, explanation=explanation, summary=summary, n_obs=n)
    except Exception as exc:
        return _error(exc)


def _matrix_result(kind: str, series: list[str], method: str, start, end, lookback, transform, fn) -> dict:
    try:
        specs, frame, used = _prepare(series, start, end, lookback, transform)
        if len(specs) < 2:
            raise ValueError("provide at least two distinct series")
        if frame.height < 4:
            raise ValueError(f"only {frame.height} overlapping observations; need at least 4")
        names = [s.label for s in specs]
        mat = fn(frame.select(names).to_numpy(), method)
        table = pl.DataFrame({"series": names, **{nm: mat[:, j] for j, nm in enumerate(names)}})
        explanation = (f"{method.capitalize()} {kind} matrix of {len(names)} series using "
                       f"{_transform_text(used)}. Data: {_period(frame)}. {_notes(specs)}"
                       + ("Values are in -1..1; 1 = perfect positive co-movement. " if kind == "correlation" else
                          "Diagonal = variances; units are the product of the series' units. ")
                       + {"pearson": "Pearson measures linear association.",
                          "spearman": "Spearman uses ranks (monotonic association, robust to outliers).",
                          "distance": "Distance correlation is in 0..1 and detects non-linear dependence "
                                      "(0 = independent), so it has no sign."}[method])
        return _payload(table, message=explanation, explanation=explanation,
                        summary={"n_obs": frame.height, "period": _period(frame)}, n_obs=frame.height)
    except Exception as exc:
        return _error(exc)


def correlation_matrix(
    series: list[str],
    method: str = "pearson",
    transform: str = "auto",
    start: str | None = None,
    end: str | None = None,
    lookback: str | None = None,
) -> dict:
    """Correlation matrix across several series (e.g. "correlation matrix of all the 10y par rates for the last month").

    Each series is one row/column; list every requested series explicitly (e.g. one "par:<issuer>:10Y"
    per issuer). After the call, explain what was done using the returned `explanation`, then
    highlight the strongest and weakest pairs and mention the number of observations.

    Series format: "par:<issuer>:<tenor>" or "zero:<issuer>:<tenor>" (e.g. "par:CHE:10Y"),
    "swap:<ccy>:<tenor>" (swap par rate, e.g. "swap:USD:10Y") or "repo:<ccy>:<tenor>" (repo/RFR
    rate), or "fx:<pair>" (e.g. "fx:USDCHF", "fx:JPY"). Swap and repo series are different curves,
    never interchangeable. If a currency has several swap/repo variants the tool errors and lists
    them; then append the periods and index, e.g. "swap:EUR:5Y:6M:1Y:ESTR" (float period, fixed
    period, index) or "repo:EUR:2Y:3M:ESTR" (period, index).

    Args:
        series: Two or more series in the series format above, for example one 10Y par
            series per issuer asked about.
        method: "pearson" (default), "spearman" (rank) or "distance" (non-linear dependence, 0..1).
        transform: "auto" (default: rates -> first differences, FX -> log returns), "level",
            "diff", "pct_change" or "log_return".
        start: Optional first date "YYYY-MM-DD".
        end: Optional last date "YYYY-MM-DD".
        lookback: Optional period ending at the latest observation, e.g. "1M", "3M", "1Y", "20D".

    Returns:
        Dict with status, explanation, summary and a matrix table (columns, rows).
    """
    return _matrix_result("correlation", series, method, start, end, lookback, transform, K.correlation_matrix)


def covariance_matrix(
    series: list[str],
    method: str = "pearson",
    transform: str = "auto",
    start: str | None = None,
    end: str | None = None,
    lookback: str | None = None,
) -> dict:
    """Covariance matrix across several series (sample covariance, n-1 denominator).

    After the call, explain what was done using the returned `explanation`.

    Series format: "par:<issuer>:<tenor>" or "zero:<issuer>:<tenor>" (e.g. "par:CHE:10Y"),
    "swap:<ccy>:<tenor>" (swap par rate, e.g. "swap:USD:10Y") or "repo:<ccy>:<tenor>" (repo/RFR
    rate), or "fx:<pair>" (e.g. "fx:USDCHF", "fx:JPY"). Swap and repo series are different curves,
    never interchangeable. If a currency has several swap/repo variants the tool errors and lists
    them; then append the periods and index, e.g. "swap:EUR:5Y:6M:1Y:ESTR" (float period, fixed
    period, index) or "repo:EUR:2Y:3M:ESTR" (period, index).

    Args:
        series: Two or more series in the series format above.
        method: "pearson" (default, ordinary covariance) or "spearman" (covariance of the ranks).
        transform: "auto" (default: rates -> first differences, FX -> log returns), "level",
            "diff", "pct_change" or "log_return".
        start: Optional first date "YYYY-MM-DD".
        end: Optional last date "YYYY-MM-DD".
        lookback: Optional period ending at the latest observation, e.g. "1M", "3M", "1Y", "20D".

    Returns:
        Dict with status, explanation, summary and a matrix table (columns, rows).
    """
    return _matrix_result("covariance", series, method, start, end, lookback, transform, K.covariance_matrix)


STATS_FUNCS = {f.__name__: f for f in (linear_regression, logistic_regression, correlation_matrix, covariance_matrix)}
STATS_LC_TOOLS = [
    StructuredTool.from_function(func=f, name=name, parse_docstring=True) for name, f in STATS_FUNCS.items()
]
