"""Read-only access to zero/par rates in ycs_data.duckdb/sqlite."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from cqfi.curve_keys import RepoCurveKey, SwapCurveKey, normalize_currency
from cqfi.db_backend import source_class_for
from cqfi.issuers import IssuerProfile, RateType
from cqfi.ycs_tenors import (
    BOND_TENOR_COLUMNS,
    TENOR_COLUMN_TO_YEARS,
    TENOR_COLUMNS,
    column_to_label,
)

SWAP_TABLE = "swap_par_rates"
REPO_TABLE = "repo_rfr_rates"
_KEY_COLUMNS: dict[str, tuple[str, ...]] = {
    SWAP_TABLE: ("ccy", "coupon_period", "coupon_period_fixed", "index"),
    REPO_TABLE: ("ccy", "coupon_period", "index"),
}


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _melt_tenor_row(
    row: dict, what: str, columns: tuple[str, ...] = TENOR_COLUMNS
) -> pl.DataFrame:
    """Melt one wide tenor row into ``tenor_column, tenor_label, tenor_years, rate_pct``."""
    records = [
        {
            "tenor_column": col,
            "tenor_label": column_to_label(col),
            "tenor_years": TENOR_COLUMN_TO_YEARS[col],
            "rate_pct": float(row[col]),
        }
        for col in columns
        if row.get(col) is not None
    ]
    if not records:
        raise LookupError(f"All tenor columns are null for {what}")
    return pl.DataFrame(records).sort("tenor_years")


def load_curve_rates(
    db_path: Path | str,
    issuer: IssuerProfile,
    valuation_date: str | date,
    rate_type: RateType = RateType.ZERO,
) -> pl.DataFrame:
    """Load one row of pillar rates for (issuer, date) as a long-form DataFrame.

    Returns columns: tenor_column, tenor_label, tenor_years, rate_pct.
    """
    table = "zero_rates" if rate_type == RateType.ZERO else "par_rates"
    val_date = _parse_date(valuation_date)
    date_str = val_date.isoformat()

    with source_class_for(db_path)(db_path, read_only=True) as db:
        frame = db.run_query(f"""
            SELECT *
            FROM {table}
            WHERE source = '{issuer.source_code}'
              AND date = '{date_str}'
            """)

    if frame.is_empty():
        raise LookupError(
            f"No {rate_type.value} rates for {issuer.source_code} on {date_str}"
        )

    return _melt_tenor_row(
        frame.row(0, named=True), f"{issuer.source_code} on {date_str}", BOND_TENOR_COLUMNS
    )


def list_available_dates(
    db_path: Path | str,
    issuer: IssuerProfile,
    rate_type: RateType = RateType.ZERO,
) -> pl.DataFrame:
    """Return distinct valuation dates available for an issuer."""
    table = "zero_rates" if rate_type == RateType.ZERO else "par_rates"
    with source_class_for(db_path)(db_path, read_only=True) as db:
        return db.run_query(f"""
            SELECT date
            FROM {table}
            WHERE source = '{issuer.source_code}'
            ORDER BY date
            """)


def _load_keyed_rates(
    db_path: Path | str, table: str, filters: dict[str, str], as_of: str | date
) -> pl.DataFrame:
    date_str = _parse_date(as_of).isoformat()
    where = " AND ".join(f"\"{col}\" = '{val}'" for col, val in filters.items())
    with source_class_for(db_path)(db_path, read_only=True) as db:
        frame = db.run_query(
            f"SELECT * FROM {table} WHERE {where} AND date = '{date_str}'"
        )
    what = f"{table} {'/'.join(filters.values())} on {date_str}"
    if frame.is_empty():
        raise LookupError(f"No rows in {what}")
    return _melt_tenor_row(frame.row(0, named=True), what)


def load_swap_rates(
    db_path: Path | str, key: SwapCurveKey, as_of: str | date
) -> pl.DataFrame:
    """Load swap par pillar rates for *key* on *as_of* (long form, percent)."""
    return _load_keyed_rates(
        db_path,
        SWAP_TABLE,
        {
            "ccy": key.currency,
            "coupon_period": key.float_period,
            "coupon_period_fixed": key.fixed_period,
            "index": key.index,
        },
        as_of,
    )


def load_repo_rates(
    db_path: Path | str, key: RepoCurveKey, as_of: str | date
) -> pl.DataFrame:
    """Load repo/RFR par pillar rates for *key* on *as_of* (long form, percent)."""
    return _load_keyed_rates(
        db_path,
        REPO_TABLE,
        {"ccy": key.currency, "coupon_period": key.period, "index": key.index},
        as_of,
    )


def list_rate_curve_keys(
    db_path: Path | str,
    table: str,
    as_of: str | date | None = None,
    currency: str | None = None,
) -> list[SwapCurveKey] | list[RepoCurveKey]:
    """Distinct swap/repo curve keys in *table*, optionally for one date/currency."""
    columns = _KEY_COLUMNS[table]
    conditions = []
    if as_of is not None:
        conditions.append(f"date = '{_parse_date(as_of).isoformat()}'")
    if currency is not None:
        conditions.append(f"ccy = '{normalize_currency(currency)}'")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    select = ", ".join(f'"{c}"' for c in columns)
    with source_class_for(db_path)(db_path, read_only=True) as db:
        frame = db.run_query(f"SELECT DISTINCT {select} FROM {table} {where} ORDER BY {select}")
    rows = frame.rows()
    if table == SWAP_TABLE:
        return [SwapCurveKey(ccy, flt, fix, idx or "UNSPECIFIED") for ccy, flt, fix, idx in rows]
    return [RepoCurveKey(ccy, per, idx or "UNSPECIFIED") for ccy, per, idx in rows]
