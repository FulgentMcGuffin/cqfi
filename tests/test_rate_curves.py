"""Swap and repo curves: keys, conventions, loaders, bootstrap and market context."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest
import QuantLib as ql

from cqfi.curve_keys import RepoCurveKey, SwapCurveKey, period_years, ql_period
from cqfi.data.rates_loader import (
    REPO_TABLE,
    SWAP_TABLE,
    list_rate_curve_keys,
    load_curve_rates,
    load_repo_rates,
    load_swap_rates,
)
from cqfi.issuers import resolve_issuer
from cqfi.quantlib.quantlib_curve import ql_build_repo_curve, ql_build_swap_curve
from cqfi.quantlib.quantlib_market_context import (
    REPO_RFR_LABEL,
    SWAP_PAR_LABEL,
    QuantLibCurveCollection,
    QuantlibMarketContext,
)
from cqfi.quantlib.quantlib_market_context_manager import QuantlibMarketContextManager
from cqfi.rate_conventions import CURRENCY_CONVENTIONS, resolve_currency_conventions

AS_OF = date(2024, 1, 2)
_CTX = "cqfi.quantlib.quantlib_market_context"


@pytest.fixture(autouse=True)
def _clear_manager():
    QuantlibMarketContextManager.instance().clear()
    yield
    QuantlibMarketContextManager.instance().clear()


def _rates(shift: float = 0.0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "tenor_column": [
                "Y000p25",
                "Y000p5",
                "Y001p0",
                "Y002p0",
                "Y005p0",
                "Y010p0",
                "Y030p0",
            ],
            "tenor_label": ["3M", "6M", "1Y", "2Y", "5Y", "10Y", "30Y"],
            "tenor_years": [0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
            "rate_pct": [r + shift for r in (3.0, 3.1, 3.2, 3.4, 3.6, 3.8, 3.7)],
        }
    )


# --------------------------------------------------------------------------- keys


def test_keys_normalize():
    key = SwapCurveKey(" usd ", "3m", "6m", "sofr")
    assert (key.currency, key.float_period, key.fixed_period, key.index) == (
        "USD",
        "3M",
        "6M",
        "SOFR",
    )
    assert RepoCurveKey("eur", "6m") == RepoCurveKey("EUR", "6M", "UNSPECIFIED")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"currency": "US", "period": "3M"},
        {"currency": "USD", "period": "3X"},
        {"currency": "USD'; DROP TABLE x;--", "period": "3M"},
        {"currency": "USD", "period": "3M", "index": "SOFR' OR '1'='1"},
    ],
)
def test_repo_key_rejects_invalid(kwargs):
    with pytest.raises(ValueError):
        RepoCurveKey(**kwargs)


def test_swap_and_repo_keys_are_distinct_types():
    swap_key = SwapCurveKey("EUR", "6M", "6M")
    repo_key = RepoCurveKey("EUR", "6M")
    assert swap_key != repo_key
    collection = QuantLibCurveCollection(as_of=AS_OF)
    handle = ql.YieldTermStructureHandle(
        ql.FlatForward(ql.Date(2, 1, 2024), 0.03, ql.Actual365Fixed())
    )
    with pytest.raises(TypeError):
        collection.set_repo_curve(swap_key, handle)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        collection.set_swap_curve(repo_key, handle)  # type: ignore[arg-type]


def test_period_helpers():
    assert ql_period("6M") == ql.Period(6, ql.Months)
    assert period_years("3M") == pytest.approx(0.25)
    assert period_years("1Y") == 1.0


# ------------------------------------------------------------------ conventions


@pytest.mark.parametrize(
    "ccy", ["AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NOK", "NZD", "SEK", "USD"]
)
def test_every_quoted_currency_has_conventions(ccy):
    conv = resolve_currency_conventions(ccy.lower())
    assert conv.ccy == ccy
    assert conv.ql_currency().code() == ccy
    assert conv.calendar().isBusinessDay(ql.Date(2, 1, 2024)) in (True, False)


def test_unknown_currency_raises():
    with pytest.raises(ValueError, match="Supported"):
        resolve_currency_conventions("XXX")
    assert "USD" in CURRENCY_CONVENTIONS


# --------------------------------------------------------------------- loaders


@pytest.fixture
def ycs_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "ycs.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE swap_par_rates (date TEXT, ccy TEXT, coupon_period TEXT, "
            'coupon_period_fixed TEXT, "index" TEXT, Y001p0 REAL, Y006p0 REAL, Y010p0 REAL)'
        )
        conn.execute(
            "CREATE TABLE repo_rfr_rates (date TEXT, ccy TEXT, coupon_period TEXT, "
            '"index" TEXT, Y001p0 REAL, Y010p0 REAL)'
        )
        conn.execute(
            "CREATE TABLE zero_rates (source TEXT, date TEXT, Y001p0 REAL, Y006p0 REAL)"
        )
        conn.executemany(
            "INSERT INTO swap_par_rates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("2024-01-02", "USD", "3M", "6M", "UNSPECIFIED", 4.0, 3.9, 3.8),
                ("2024-01-02", "EUR", "6M", "1Y", "UNSPECIFIED", 3.0, 2.8, 2.6),
                ("2024-01-02", "CAD", "3M", "6M", "UNSPECIFIED", None, None, None),
            ],
        )
        conn.execute(
            "INSERT INTO repo_rfr_rates VALUES ('2024-01-02', 'USD', '3M', 'UNSPECIFIED', 3.7, 3.5)"
        )
        conn.execute("INSERT INTO zero_rates VALUES ('USA', '2024-01-02', 4.1, 4.0)")
    return db_path


def test_load_swap_rates_filters_by_key(ycs_db: Path):
    frame = load_swap_rates(ycs_db, SwapCurveKey("EUR", "6M", "1Y"), AS_OF)
    assert frame["tenor_label"].to_list() == ["1Y", "6Y", "10Y"]
    assert frame["rate_pct"].to_list() == pytest.approx([3.0, 2.8, 2.6])


def test_load_repo_rates(ycs_db: Path):
    frame = load_repo_rates(ycs_db, RepoCurveKey("USD", "3M"), "2024-01-02")
    assert frame["tenor_years"].to_list() == [1.0, 10.0]


def test_all_null_or_missing_rows_raise(ycs_db: Path):
    with pytest.raises(LookupError, match="null"):
        load_swap_rates(ycs_db, SwapCurveKey("CAD", "3M", "6M"), AS_OF)
    with pytest.raises(LookupError, match="No rows"):
        load_repo_rates(ycs_db, RepoCurveKey("EUR", "6M"), AS_OF)


def test_list_rate_curve_keys(ycs_db: Path):
    swaps = list_rate_curve_keys(ycs_db, SWAP_TABLE, AS_OF)
    assert SwapCurveKey("USD", "3M", "6M") in swaps and len(swaps) == 3
    assert list_rate_curve_keys(ycs_db, REPO_TABLE, currency="usd") == [
        RepoCurveKey("USD", "3M")
    ]
    assert list_rate_curve_keys(ycs_db, REPO_TABLE, currency="EUR") == []


def test_bond_loader_ignores_swap_only_tenors(ycs_db: Path):
    frame = load_curve_rates(ycs_db, resolve_issuer("USA"), AS_OF)
    assert frame["tenor_column"].to_list() == ["Y001p0"]


# ------------------------------------------------------------------- bootstrap


def test_dual_curve_swap_reprices_par_quotes():
    repo, repo_diag = ql_build_repo_curve(
        RepoCurveKey("USD", "3M"), AS_OF, _rates(-0.3)
    )
    key = SwapCurveKey("USD", "3M", "6M")
    swap, diag = ql_build_swap_curve(key, AS_OF, _rates(), repo)
    assert set(repo_diag["discounting"]) == {"self"}
    assert set(diag["discounting"]) == {"repo"}
    assert diag.columns[0] == "tenor_years"

    conv = resolve_currency_conventions("USD")
    index = ql.IborIndex(
        "t",
        ql_period("3M"),
        conv.settlement_days,
        conv.ql_currency(),
        conv.calendar(),
        conv.business_convention,
        False,
        conv.float_day_count,
        swap,
    )
    for years, rate in ((2, 3.4), (5, 3.6), (10, 3.8)):
        fair = ql.MakeVanillaSwap(
            ql.Period(years, ql.Years),
            index,
            0.0,
            ql.Period(0, ql.Days),
            fixedLegTenor=ql_period("6M"),
            fixedLegDayCount=conv.fixed_day_count,
            fixedLegConvention=conv.business_convention,
            fixedLegCalendar=conv.calendar(),
            pricingEngine=ql.DiscountingSwapEngine(repo),
        ).fairRate()
        assert fair * 100 == pytest.approx(rate, abs=1e-8)


# -------------------------------------------------------------- market context


def test_collection_merge_includes_repo_curves():
    handle = ql.YieldTermStructureHandle(
        ql.FlatForward(ql.Date(2, 1, 2024), 0.03, ql.Actual365Fixed())
    )
    a = QuantLibCurveCollection(as_of=AS_OF)
    a.set_repo_curve(RepoCurveKey("USD", "3M"), handle)
    b = QuantLibCurveCollection(as_of=AS_OF)
    b.set_swap_curve(SwapCurveKey("USD", "3M", "6M"), handle)
    merged = a | b
    assert merged.repo_curve_keys() == [RepoCurveKey("USD", "3M")]
    assert merged.swap_curve_keys() == [SwapCurveKey("USD", "3M", "6M")]
    with pytest.raises(KeyError):
        merged.repo_curve(RepoCurveKey("EUR", "6M"))


def test_ensure_swap_curve_discounts_off_repo():
    ctx = QuantlibMarketContext(as_of=AS_OF)
    with (
        patch(f"{_CTX}.load_swap_rates", return_value=_rates()),
        patch(f"{_CTX}.load_repo_rates", return_value=_rates(-0.3)) as repo_loader,
        patch(f"{_CTX}.list_rate_curve_keys", return_value=[RepoCurveKey("USD", "3M")]),
    ):
        key = SwapCurveKey("USD", "3M", "6M")
        handle = ctx.ensure_swap_curve(key, db_path="/fake.duckdb")
        assert ctx.ensure_swap_curve(key, db_path="/fake.duckdb") is handle
    assert repo_loader.call_count == 1
    assert ctx.curve_collection_labels() == [REPO_RFR_LABEL, SWAP_PAR_LABEL]
    assert set(ctx.curve_diagnostics[(SWAP_PAR_LABEL, key)]["discounting"]) == {"repo"}
    assert QuantlibMarketContextManager.instance().get(AS_OF) is ctx


def test_ensure_swap_curve_self_discounts_without_repo():
    ctx = QuantlibMarketContext(as_of=AS_OF)
    key = SwapCurveKey("NOK", "1Y", "1Y")
    with (
        patch(f"{_CTX}.load_swap_rates", return_value=_rates()),
        patch(f"{_CTX}.list_rate_curve_keys", return_value=[]),
    ):
        ctx.ensure_swap_curve(key, db_path="/fake.duckdb")
    assert set(ctx.curve_diagnostics[(SWAP_PAR_LABEL, key)]["discounting"]) == {"self"}
    assert REPO_RFR_LABEL not in ctx.curve_collection_labels()


# ------------------------------------------------------------------ LLM tools


@pytest.fixture
def tools_db(ycs_db: Path):
    settings = type("S", (), {"ycs_db_path": ycs_db})()
    with (
        patch("cqfi.cli_tools.get_settings", return_value=settings),
        patch(f"{_CTX}.get_settings", return_value=settings),
    ):
        yield ycs_db


def test_get_curve_swap_returns_run_sql_shape(tools_db):
    from cqfi.cli_tools import get_curve

    result = get_curve("2024-01-02", "swap", "usd")
    assert result["status"] == "success", result
    assert result["curve"] == "SWAP USD 6M/3M UNSPECIFIED"
    assert result["columns"][0] == "tenor_years"
    assert result["row_count"] == len(result["rows"]) == 3
    assert {row["discounting"] for row in result["rows"]} == {"repo"}


def test_get_curve_unknown_or_missing_curve_is_error(tools_db):
    from cqfi.cli_tools import get_curve

    assert get_curve("2024-01-02", "repo", "EUR")["status"] == "error"
    assert (
        get_curve("2024-01-02", "swap", "USD", float_period="6M")["status"] == "error"
    )
    assert get_curve("2024-01-02", "fra", "USD")["status"] == "error"  # type: ignore[arg-type]


def test_list_curves(tools_db):
    from cqfi.cli_tools import list_curves

    result = list_curves("2024-01-02", "repo")
    assert result["rows"] == [
        {
            "curve_type": "repo",
            "currency": "USD",
            "float_period": "3M",
            "fixed_period": "3M",
            "index": "UNSPECIFIED",
        }
    ]


def test_curve_command(tools_db):
    from cqfi.cli_tools import CURVE_HELP_TEXT, execute_curve_command

    assert execute_curve_command("/dlv x") is None
    assert execute_curve_command("/curve")["message"] == CURVE_HELP_TEXT
    result = execute_curve_command("/curve repo usd 2024-01-02")
    assert result["status"] == "success"
    assert isinstance(result["dataframe"], pl.DataFrame)
    assert result["dataframe"].height == 2
