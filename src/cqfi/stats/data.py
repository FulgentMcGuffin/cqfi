"""Load aligned rate / FX series from ycs_data for the statistics tools."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from cqfi.curve_keys import _INDEX_RE, _PERIOD_RE, _check, normalize_currency
from cqfi.db_backend import source_class_for
from cqfi.issuers import resolve_issuer
from cqfi.ycs_tenors import TENOR_COLUMNS, column_to_label, label_to_column

_KINDS = {"par": "par_rates", "par_rates": "par_rates", "zero": "zero_rates", "zero_rates": "zero_rates",
          "swap": "swap_par_rates", "swap_par_rates": "swap_par_rates",
          "repo": "repo_rfr_rates", "repo_rfr_rates": "repo_rfr_rates",
          "fx": "spotfx", "spotfx": "spotfx"}
_SHORT = {"par_rates": "par", "zero_rates": "zero", "swap_par_rates": "swap", "repo_rfr_rates": "repo"}
# Currency-keyed tables: DB columns identifying a curve variant, in spec order.
_VARIANT_COLUMNS = {"swap_par_rates": ("coupon_period", "coupon_period_fixed", "index"),
                    "repo_rfr_rates": ("coupon_period", "index")}
# Currency / word -> USD pair column (mirrors the fx_pairs vocabulary in ycs_data.yaml).
_FX_ALIASES = {
    "EUR": "EURUSD", "EURO": "EURUSD", "GBP": "GBPUSD", "STERLING": "GBPUSD", "POUND": "GBPUSD",
    "JPY": "USDJPY", "YEN": "USDJPY", "CAD": "USDCAD", "AUD": "AUDUSD", "NZD": "NZDUSD",
    "CHF": "USDCHF", "SWISS FRANC": "USDCHF", "CNY": "USDCNY", "INR": "USDINR", "MXN": "USDMXN",
    "BRL": "USDBRL", "RUB": "USDRUB", "KRW": "USDKRW",
}
_PAIR_RE = re.compile(r"^[A-Z]{6}$")
_LOOKBACK_RE = re.compile(r"^\s*(\d+)\s*([DWMY])\w*\s*$", re.IGNORECASE)
TRANSFORMS = ("auto", "level", "diff", "pct_change", "log_return")

SPEC_HELP = (
    '"par:<issuer>:<tenor>" or "zero:<issuer>:<tenor>" (e.g. "par:CHE:10Y"), '
    '"swap:<ccy>:<tenor>[:<float period>[:<fixed period>]][:<index>]" or '
    '"repo:<ccy>:<tenor>[:<period>][:<index>]" (e.g. "swap:USD:10Y", "swap:EUR:5Y:6M:1Y:ESTR"), '
    'or "fx:<pair>" (e.g. "fx:USDCHF", "fx:JPY")'
)


@dataclass(frozen=True)
class SeriesSpec:
    """One requested series: source table, issuer source code, value column, display label."""

    table: str
    source: str | None
    column: str
    label: str
    filters: tuple[tuple[str, str], ...] = ()  # swap/repo curve-variant columns -> values
    note: str = ""  # resolved curve variant, for the explanation

    @property
    def is_rate(self) -> bool:
        return self.table != "spotfx"


def parse_spec(text: str) -> SeriesSpec:
    """Parse ``kind:issuer:tenor`` / ``fx:pair`` into a :class:`SeriesSpec` (validated, injection-safe)."""
    parts = [p.strip() for p in text.split(":")]
    table = _KINDS.get(parts[0].lower())
    if table is None:
        raise ValueError(f"Bad series {text!r}. Use {SPEC_HELP}.")
    if table == "spotfx":
        if len(parts) != 2:
            raise ValueError(f"Bad FX series {text!r}. Use fx:<pair>, e.g. fx:USDCHF.")
        key = parts[1].upper()
        pair = _FX_ALIASES.get(key, key)
        if not _PAIR_RE.match(pair):
            raise ValueError(f"Unknown FX pair {parts[1]!r}; use a pair like USDCHF or a currency like CHF.")
        return SeriesSpec(table, None, pair, f"fx:{pair}")
    if len(parts) < 3 or (len(parts) > 3 and table not in _VARIANT_COLUMNS):
        raise ValueError(f"Bad rate series {text!r}. Use {SPEC_HELP}.")
    column = label_to_column(parts[2]) or (parts[2] if parts[2] in TENOR_COLUMNS else None)
    if column is None:
        raise ValueError(f"Unknown tenor {parts[2]!r}; use e.g. 2Y, 5Y, 10Y, 30Y.")
    kind, tenor = _SHORT[table], column_to_label(column)
    if table in _VARIANT_COLUMNS:
        ccy = normalize_currency(parts[1])
        filters = _parse_variant(table, parts[3:], text)
        suffix = "".join(f":{v}" for _, v in filters)
        return SeriesSpec(table, ccy, column, f"{kind}:{ccy}:{tenor}{suffix}", filters)
    issuer = resolve_issuer(parts[1])
    return SeriesSpec(table, issuer.source_code, column, f"{kind}:{issuer.source_code}:{tenor}")


def _parse_variant(table: str, extras: list[str], text: str) -> tuple[tuple[str, str], ...]:
    """Periods (float, then fixed for swaps) and an index name, validated; periods are positional."""
    is_period = [bool(_PERIOD_RE.match(e.strip().upper())) for e in extras]
    periods = [_check(e, _PERIOD_RE, "period") for e, p in zip(extras, is_period) if p]
    names = [_check(e, _INDEX_RE, "index") for e, p in zip(extras, is_period) if not p]
    columns = _VARIANT_COLUMNS[table]
    if len(periods) > len(columns) - 1 or len(names) > 1:
        raise ValueError(f"Too many curve qualifiers in {text!r}. Use {SPEC_HELP}.")
    return tuple(zip(columns, periods)) + tuple(("index", n) for n in names)


def resolve_specs(db_path: Path | str, specs: list[SeriesSpec]) -> list[SeriesSpec]:
    """Pin swap/repo specs to one stored curve variant (the error lists the options when ambiguous)."""
    return [_resolve_variant(db_path, s) if s.table in _VARIANT_COLUMNS else s for s in specs]


def _resolve_variant(db_path: Path | str, spec: SeriesSpec) -> SeriesSpec:
    cols = _VARIANT_COLUMNS[spec.table]
    select = ", ".join(f'"{c}"' for c in cols)
    sql = (f"SELECT {select}, MIN(date) AS first_date, MAX(date) AS last_date FROM {spec.table} "
           f"WHERE ccy = '{spec.source}' GROUP BY {select}")
    with source_class_for(db_path)(db_path, read_only=True) as db:
        rows = db.run_query(sql).to_dicts()
    wanted = dict(spec.filters)
    matches = [r for r in rows if all(str(r[c]).upper() == v for c, v in wanted.items())]

    def describe(r: dict) -> str:
        span = f"{str(r['first_date'])[:10]} to {str(r['last_date'])[:10]}"
        return ", ".join(f"{c}={r[c]}" for c in cols) + f" ({span})"

    if len(matches) != 1:
        options = matches or rows
        raise LookupError(
            f"{'Ambiguous' if matches else 'No matching'} {_SHORT[spec.table]} curve for {spec.source} "
            f"with {wanted or 'no qualifiers'}. Add periods/index to the series. "
            f"Available: {'; '.join(describe(r) for r in options) or 'none'}"
        )
    row = matches[0]
    return SeriesSpec(spec.table, spec.source, spec.column, spec.label,
                      tuple((c, str(row[c])) for c in cols),
                      note=f"{spec.label} uses the {_SHORT[spec.table]} curve {describe(row)}")


def _fetch_group(db_path: Path | str, table: str, source: str | None, filters: tuple[tuple[str, str], ...],
                 cols: list[str], start: str | None, end: str | None) -> pl.DataFrame:
    select = ", ".join(f'"{c}"' for c in cols)
    source_col = "ccy" if table in _VARIANT_COLUMNS else "source"
    where = [f"{source_col} = '{source}'"] if source else []
    where += [f"\"{c}\" = '{v}'" for c, v in filters]
    where += [f"date >= '{start}'"] if start else []
    where += [f"date <= '{end}'"] if end else []
    sql = f"SELECT date, {select} FROM {table}" + (f" WHERE {' AND '.join(where)}" if where else "")
    with source_class_for(db_path)(db_path, read_only=True) as db:
        frame = db.run_query(sql)
    return frame.with_columns(pl.col("date").cast(pl.String).str.slice(0, 10).str.to_date())


def load_aligned(db_path: Path | str, specs: list[SeriesSpec], start: str | None = None,
                 end: str | None = None) -> pl.DataFrame:
    """Date-sorted frame of the requested series (columns = labels), inner-joined, nulls dropped."""
    groups: dict[tuple, list[SeriesSpec]] = {}
    for s in specs:
        groups.setdefault((s.table, s.source, s.filters), []).append(s)
    frames = []
    for (table, source, filters), members in groups.items():
        cols = list(dict.fromkeys(m.column for m in members))
        frame = _fetch_group(db_path, table, source, filters, cols, start, end)
        frames.append(frame.select("date", *[pl.col(m.column).alias(m.label) for m in members]))
    out = frames[0]
    for frame in frames[1:]:
        out = out.join(frame, on="date", how="inner")
    out = out.sort("date").drop_nulls()
    if out.is_empty():
        raise LookupError("No overlapping observations for the requested series and period.")
    return out


def apply_transform(df: pl.DataFrame, specs: list[SeriesSpec], mode: str) -> tuple[pl.DataFrame, dict[str, str]]:
    """Transform each series; returns the frame (first row dropped for changes) and label -> transform used."""
    if mode not in TRANSFORMS:
        raise ValueError(f"transform must be one of {', '.join(TRANSFORMS)}")
    used = {s.label: ("diff" if s.is_rate else "log_return") if mode == "auto" else mode for s in specs}
    exprs = []
    for label, how in used.items():
        col = pl.col(label)
        if how in ("pct_change", "log_return") and df[label].min() <= 0:
            raise ValueError(f"{label} has non-positive values; use transform 'diff' or 'level'.")
        exprs.append({
            "level": col,
            "diff": col.diff(),
            "pct_change": col.pct_change(),
            "log_return": col.log().diff(),
        }[how].alias(label))
    out = df.select("date", *exprs)
    return (out if set(used.values()) == {"level"} else out.slice(1)), used


def _months_back(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def apply_lookback(df: pl.DataFrame, lookback: str | None) -> pl.DataFrame:
    """Keep dates in ``(last - lookback, last]`` where ``last`` is the latest observation (e.g. "1M", "1Y", "20D")."""
    if not lookback:
        return df
    match = _LOOKBACK_RE.match(lookback)
    if not match:
        raise ValueError(f"Bad lookback {lookback!r}; use e.g. 20D, 2W, 1M, 1Y.")
    n, unit = int(match.group(1)), match.group(2).upper()
    last = df["date"].max()
    cutoff = {"D": lambda: last - timedelta(days=n), "W": lambda: last - timedelta(weeks=n),
              "M": lambda: _months_back(last, n), "Y": lambda: _months_back(last, 12 * n)}[unit]()
    return df.filter(pl.col("date") > cutoff)
