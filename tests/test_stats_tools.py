"""Stats kernels and LLM tools: regression (rolling), logistic, covariance / correlation matrices."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from cqfi.stats import kernels as K
from cqfi.stats import tools as T
from cqfi.stats.data import apply_lookback, parse_spec

N = 300


@pytest.fixture(scope="module")
def rng() -> np.random.Generator:
    return np.random.default_rng(7)


@pytest.fixture
def ycs_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """USA/CHE 10Y par rates (CHE moves 0.6x USA + noise), USDCHF and USDJPY."""
    rng = np.random.default_rng(1)
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(N)]
    usa = 4 + np.cumsum(rng.normal(0, 0.05, N))
    che = 1 + 0.6 * (usa - usa[0]) + np.cumsum(rng.normal(0, 0.02, N))
    jpy_ret = rng.normal(0, 0.005, N)
    chf_ret = -0.8 * jpy_ret + rng.normal(0, 0.004, N)  # USDCHF moves against USDJPY
    usdjpy = 140 * np.exp(np.cumsum(jpy_ret))
    usdchf = 0.9 * np.exp(np.cumsum(chf_ret))
    path = tmp_path / "ycs_data.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE par_rates (source TEXT, date TEXT, Y010p0 REAL, Y002p0 REAL)")
        conn.execute("CREATE TABLE swap_par_rates (ccy TEXT, coupon_period TEXT, coupon_period_fixed TEXT, "
                     "\"index\" TEXT, date TEXT, Y010p0 REAL)")
        conn.execute("CREATE TABLE repo_rfr_rates (ccy TEXT, coupon_period TEXT, \"index\" TEXT, date TEXT, Y010p0 REAL)")
        conn.execute("CREATE TABLE spotfx (source TEXT, date TEXT, USDJPY REAL, USDCHF REAL)")
        for i, d in enumerate(days):
            iso = d.isoformat()
            conn.execute("INSERT INTO par_rates VALUES ('USA', ?, ?, ?)", (iso, usa[i], usa[i] - 0.3))
            conn.execute("INSERT INTO par_rates VALUES ('CHE', ?, ?, ?)", (iso, che[i], che[i] - 0.2))
            conn.execute("INSERT INTO swap_par_rates VALUES ('USD', '3M', '6M', 'UNSPECIFIED', ?, ?)", (iso, usa[i] + 0.2))
            conn.execute("INSERT INTO swap_par_rates VALUES ('EUR', '6M', '1Y', 'ESTR', ?, ?)", (iso, che[i] + 1))
            conn.execute("INSERT INTO swap_par_rates VALUES ('EUR', '3M', '1Y', 'EURIBOR', ?, ?)", (iso, che[i] + 1.1))
            conn.execute("INSERT INTO repo_rfr_rates VALUES ('USD', '3M', 'SOFR', ?, ?)", (iso, usa[i] - 0.5))
            conn.execute("INSERT INTO spotfx VALUES ('spot_fx_rates', ?, ?, ?)", (iso, usdjpy[i], usdchf[i]))
    monkeypatch.setattr(T, "_db", lambda: path)
    return path


# ── kernels ──────────────────────────────────────────────────────────────────


def test_rolling_ols_matches_lstsq(rng):
    x = rng.normal(size=(120, 2))
    y = 0.5 + x @ [1.5, -0.7] + rng.normal(size=120)
    fit = K.rolling_ols(x, y, 20)
    for i in (0, 37, 100):
        z = np.c_[np.ones(20), x[i : i + 20]]
        ref = np.linalg.lstsq(z, y[i : i + 20], rcond=None)[0]
        assert np.allclose(fit.intercept[i], ref[0]) and np.allclose(fit.beta[i], ref[1:])
    assert fit.beta.shape == (101, 2)


def test_rolling_ols_step_and_window_ends():
    assert K.window_ends(100, 20, 5).tolist() == list(range(19, 100, 5))
    x = np.random.default_rng(0).normal(size=(100, 1))
    assert K.rolling_ols(x, x[:, 0] * 2, 20, step=5).beta.shape[0] == len(K.window_ends(100, 20, 5))


def test_rolling_ols_constant_regressor_is_nan():
    x = np.ones((30, 1))
    fit = K.rolling_ols(x, np.arange(30.0), 10)
    assert np.isnan(fit.beta).all()


def test_rolling_ols_rejects_bad_window():
    with pytest.raises(ValueError):
        K.rolling_ols(np.zeros((10, 1)), np.zeros(10), 50)


def test_logit_recovers_coefficients(rng):
    x = rng.normal(size=(5000, 1))
    y = (rng.random(5000) < 1 / (1 + np.exp(-(0.3 + 1.2 * x[:, 0])))).astype(float)
    fit = K.rolling_logit(x, y, 5000)
    assert abs(fit.beta[0, 0] - 1.2) < 0.15 and abs(fit.intercept[0] - 0.3) < 0.1
    assert fit.converged[0]


def test_logit_single_class_window_is_nan():
    x = np.random.default_rng(0).normal(size=(40, 1))
    y = np.zeros(40)
    assert np.isnan(K.rolling_logit(x, y, 20).beta).all()


def test_logit_separation_stays_finite():
    x = np.linspace(-1, 1, 40)[:, None]
    y = (x[:, 0] > 0).astype(float)
    assert np.isfinite(K.rolling_logit(x, y, 40).beta).all()


def _brute_dcor(a, b):
    def centred(v):
        d = np.abs(v[:, None] - v[None, :])
        return d - d.mean(0) - d.mean(1)[:, None] + d.mean()

    A, B = centred(a), centred(b)
    return np.sqrt((A * B).mean() / np.sqrt((A * A).mean() * (B * B).mean()))


def test_distance_correlation_matches_reference(rng):
    x = rng.normal(size=(80, 3))
    x[:, 1] = x[:, 0] ** 2
    got = K.correlation_matrix(x, "distance")
    assert np.isclose(got[0, 1], _brute_dcor(x[:, 0], x[:, 1]), atol=1e-5)
    assert np.allclose(np.diag(got), 1) and np.allclose(got, got.T)
    assert abs(K.correlation_matrix(x, "pearson")[0, 1]) < 0.4 < got[0, 1]  # non-linear dependence


def test_spearman_matches_scipy(rng):
    stats = pytest.importorskip("scipy.stats")
    x = rng.normal(size=(100, 4))
    assert np.allclose(K.correlation_matrix(x, "spearman"), stats.spearmanr(x).statistic)


def test_covariance_matches_numpy(rng):
    x = rng.normal(size=(50, 3))
    assert np.allclose(K.covariance_matrix(x), np.cov(x, rowvar=False))


def test_unknown_methods_rejected():
    with pytest.raises(ValueError):
        K.correlation_matrix(np.zeros((5, 2)), "kendall")
    with pytest.raises(ValueError):
        K.covariance_matrix(np.zeros((5, 2)), "distance")


# ── data helpers ─────────────────────────────────────────────────────────────


def test_parse_spec_variants():
    assert parse_spec("par:CHE:10Y").label == "par:CHE:10Y"
    assert parse_spec("par:Switzerland:10y").source == "CHE"
    assert parse_spec("fx:CHF").column == "USDCHF"
    assert parse_spec("fx:yen").column == "USDJPY"


@pytest.mark.parametrize("bad", ["foo:USA:10Y", "par:USA", "par:USA:99Y", "fx:US;DROP", "par:ZZZ:10Y"])
def test_parse_spec_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_spec(bad)


def test_lookback_anchors_on_latest_observation():
    import polars as pl

    df = pl.DataFrame({"date": [date(2024, 1, 1) + timedelta(days=i) for i in range(100)], "v": range(100)})
    last = df["date"].max()
    month = apply_lookback(df, "1M")
    assert month["date"].min() > date(last.year, last.month - 1, last.day) - timedelta(days=1)
    assert apply_lookback(df, "20D").height == 20
    assert apply_lookback(df, None).height == 100
    with pytest.raises(ValueError):
        apply_lookback(df, "soon")


# ── tools ────────────────────────────────────────────────────────────────────


def test_rolling_beta_default_window_20(ycs_db):
    out = T.linear_regression("par:CHE:10Y", ["par:USA:10Y"], rolling=True)
    assert out["status"] == "success"
    assert out["n_obs"] == N - 1  # first difference drops one row
    assert out["rows_thinned_to"] == T.MAX_ROWS
    assert "window" in out["explanation"] and "20 observations" in out["explanation"]
    assert abs(out["summary"]["beta[par:USA:10Y]"]["mean"] - 0.6) < 0.1
    assert out["columns"][:2] == ["date", "intercept"]


def test_payload_is_json_safe(ycs_db):
    import json

    out = T.linear_regression("par:CHE:10Y", ["par:USA:10Y"], rolling=True, window=30, step=10)
    assert isinstance(out["rows"][0]["date"], str)
    json.dumps(out, allow_nan=False)  # no dates / NaN left
    mixed = T.correlation_matrix(["par:USA:10Y", "fx:USDJPY"])
    assert "log returns" in mixed["explanation"] and "first differences" in mixed["explanation"]


def test_rolling_step_reduces_rows(ycs_db):
    out = T.linear_regression("par:CHE:10Y", ["par:USA:10Y"], rolling=True, window=30, step=10)
    assert len(out["rows"]) == len(range(29, N - 1, 10))
    assert out["rows_thinned_to"] is None


def test_static_regression_table(ycs_db):
    out = T.linear_regression("par:CHE:10Y", ["par:USA:10Y", "par:USA:2Y"])
    assert [r["term"] for r in out["rows"]] == ["intercept", "par:USA:10Y", "par:USA:2Y"]


def test_regression_target_cannot_be_predictor(ycs_db):
    out = T.linear_regression("par:CHE:10Y", ["par:CHE:10Y"])
    assert out["status"] == "error"


def test_window_larger_than_data_errors(ycs_db):
    out = T.linear_regression("par:CHE:10Y", ["par:USA:10Y"], rolling=True, lookback="1M", window=60)
    assert out["status"] == "error" and "exceeds" in out["message"]


def test_correlation_matrix_default_pearson_and_lookback(ycs_db):
    out = T.correlation_matrix(["par:USA:10Y", "par:CHE:10Y", "par:USA:2Y"], lookback="3M")
    assert out["status"] == "success" and "Pearson" in out["explanation"]
    assert len(out["rows"]) == 3
    assert out["rows"][0]["par:USA:10Y"] == pytest.approx(1.0)
    assert out["rows"][0]["par:CHE:10Y"] == pytest.approx(out["rows"][1]["par:USA:10Y"])
    assert out["n_obs"] < 100


@pytest.mark.parametrize("method", ["pearson", "spearman", "distance"])
def test_correlation_methods(ycs_db, method):
    out = T.correlation_matrix(["par:USA:10Y", "par:CHE:10Y"], method=method)
    assert out["status"] == "success"
    assert out["rows"][0]["par:CHE:10Y"] > 0.3


def test_covariance_matrix(ycs_db):
    out = T.covariance_matrix(["par:USA:10Y", "par:CHE:10Y"])
    assert out["status"] == "success"
    assert out["rows"][0]["par:CHE:10Y"] == pytest.approx(out["rows"][1]["par:USA:10Y"])
    bad = T.covariance_matrix(["par:USA:10Y", "par:CHE:10Y"], method="distance")
    assert bad["status"] == "error"


def test_matrix_needs_two_series(ycs_db):
    assert T.correlation_matrix(["par:USA:10Y"])["status"] == "error"


def test_logistic_conditional_probability_matches_frequencies(ycs_db):
    # CHF strengthens (USDCHF down) given JPY weakens (USDJPY up)
    out = T.logistic_regression("fx:USDCHF", ["fx:USDJPY"], target_event="down", predictor_event="up")
    assert out["status"] == "success"
    s = out["summary"]
    emp = s["empirical"]
    scen = {x["scenario"]: x["probability"] for x in s["scenarios"]}
    assert scen["fx:USDJPY event"] == pytest.approx(emp["event_hits"] / emp["event_count"], abs=0.03)
    assert "moved down" in out["explanation"] and "'up' move" in out["explanation"]


def test_logistic_continuous_predictor_and_rolling(ycs_db):
    out = T.logistic_regression("fx:USDCHF", ["fx:USDJPY"], target_event="up")
    assert any("+1 sd" in x["scenario"] for x in out["summary"]["scenarios"])
    roll = T.logistic_regression("fx:USDCHF", ["fx:USDJPY"], rolling=True, lookback="6M")
    assert roll["status"] == "success"
    assert {"beta[fx:USDJPY]", "z[fx:USDJPY]", "prob[fx:USDJPY]"} <= set(roll["columns"])


def test_logistic_rejects_levels_and_bad_event(ycs_db):
    assert T.logistic_regression("fx:USDCHF", ["fx:USDJPY"], transform="level")["status"] == "error"
    assert T.logistic_regression("fx:USDCHF", ["fx:USDJPY"], target_event="sideways")["status"] == "error"


def test_tools_registered_with_schemas():
    names = {t.name for t in T.STATS_LC_TOOLS}
    assert names == {"linear_regression", "logistic_regression", "correlation_matrix", "covariance_matrix"}
    schema = next(t for t in T.STATS_LC_TOOLS if t.name == "linear_regression").args
    assert {"target", "predictors", "rolling", "window", "lookback"} <= set(schema)


def test_rolling_speed_budget(ycs_db):
    import time

    t0 = time.perf_counter()
    T.linear_regression("par:CHE:10Y", ["par:USA:10Y"], rolling=True)
    assert time.perf_counter() - t0 < 1.0


# ── swap / repo series ───────────────────────────────────────────────────────


def test_parse_swap_and_repo_specs():
    s = parse_spec("swap:usd:10y")
    assert (s.table, s.source, s.label, s.filters) == ("swap_par_rates", "USD", "swap:USD:10Y", ())
    s = parse_spec("swap:EUR:5Y:6M:1Y:estr")
    assert s.filters == (("coupon_period", "6M"), ("coupon_period_fixed", "1Y"), ("index", "ESTR"))
    assert s.label == "swap:EUR:5Y:6M:1Y:ESTR"
    r = parse_spec("repo:USD:2Y:3M:SOFR")
    assert r.table == "repo_rfr_rates" and r.filters == (("coupon_period", "3M"), ("index", "SOFR"))
    assert parse_spec("repo:USD:2Y:SOFR").filters == (("index", "SOFR"),)


@pytest.mark.parametrize("bad", ["swap:USD", "swap:USD:10Y:3M:6M:9M", "repo:USD:2Y:3M:6M", "par:USA:10Y:3M",
                                 "swap:US:10Y", "swap:USD:10Y:a;b"])
def test_parse_variant_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_spec(bad)


def test_swap_unique_variant_resolves_and_is_explained(ycs_db):
    out = T.linear_regression("swap:USD:10Y", ["par:USA:10Y"], rolling=True, window=30)
    assert out["status"] == "success"
    assert "coupon_period=3M, coupon_period_fixed=6M" in out["explanation"]
    assert abs(out["summary"]["beta[par:USA:10Y]"]["mean"] - 1.0) < 0.05


def test_ambiguous_swap_lists_options_and_qualifier_fixes_it(ycs_db):
    err = T.correlation_matrix(["swap:EUR:10Y", "par:USA:10Y"])
    assert err["status"] == "error" and "Ambiguous" in err["message"]
    assert "ESTR" in err["message"] and "EURIBOR" in err["message"]
    ok = T.correlation_matrix(["swap:EUR:10Y:ESTR", "swap:EUR:10Y:EURIBOR"])
    assert ok["status"] == "success" and ok["rows"][0]["swap:EUR:10Y:EURIBOR"] > 0.9


def test_unknown_variant_reports_available(ycs_db):
    out = T.covariance_matrix(["swap:USD:10Y:SOFR", "par:USA:10Y"])
    assert out["status"] == "error" and "No matching" in out["message"] and "UNSPECIFIED" in out["message"]


def test_repo_series_and_mixed_tables(ycs_db):
    out = T.correlation_matrix(["repo:USD:10Y", "swap:USD:10Y", "par:CHE:10Y", "fx:USDJPY"], method="spearman")
    assert out["status"] == "success" and len(out["rows"]) == 4
    assert "repo curve" in out["explanation"] and "log returns" in out["explanation"]
    assert out["rows"][0]["swap:USD:10Y"] > 0.9


def test_unsupported_currency_errors(ycs_db):
    assert T.linear_regression("swap:GBP:10Y", ["par:USA:10Y"])["status"] == "error"
