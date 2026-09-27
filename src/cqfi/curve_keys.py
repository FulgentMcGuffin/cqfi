"""Identifiers for currency-level swap and repo curves.

Both key types share a similar shape (currency, coupon period(s), index) but
are deliberately distinct classes: a :class:`SwapCurveKey` names a
fixed-for-floating swap par curve (derivatives pricing), a
:class:`RepoCurveKey` names a repo / risk-free funding curve (financing and
discounting). Neither is accepted where the other is expected.

Field values are validated on construction; this is also what makes it safe
to interpolate them into SQL in :mod:`cqfi.data.rates_loader`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import QuantLib as ql

UNSPECIFIED_INDEX = "UNSPECIFIED"

_CCY_RE = re.compile(r"^[A-Z]{3}$")
_PERIOD_RE = re.compile(r"^(\d+)([DWMY])$")
_INDEX_RE = re.compile(r"^[A-Z0-9 _-]+$")
_PERIOD_UNITS = {"D": ql.Days, "W": ql.Weeks, "M": ql.Months, "Y": ql.Years}


def _check(value: str, pattern: re.Pattern[str], what: str) -> str:
    normalized = value.strip().upper()
    if not pattern.match(normalized):
        raise ValueError(f"Invalid {what} {value!r}")
    return normalized


def normalize_currency(ccy: str) -> str:
    """Validate and upper-case an ISO currency code."""
    return _check(ccy, _CCY_RE, "currency")


def ql_period(period: str) -> ql.Period:
    """Convert a validated period string such as ``"6M"`` to :class:`ql.Period`."""
    match = _PERIOD_RE.match(period)
    if match is None:
        raise ValueError(f"Invalid period {period!r}")
    return ql.Period(int(match.group(1)), _PERIOD_UNITS[match.group(2)])


def period_years(period: str) -> float:
    """Approximate length of *period* in years (for tenor comparisons)."""
    match = _PERIOD_RE.match(period)
    if match is None:
        raise ValueError(f"Invalid period {period!r}")
    n, unit = int(match.group(1)), match.group(2)
    return n / {"D": 365.0, "W": 52.0, "M": 12.0, "Y": 1.0}[unit]


@dataclass(frozen=True)
class SwapCurveKey:
    """Swap par curve identifier (``swap_par_rates`` table).

    Attributes:
        currency: ISO currency code, e.g. ``USD``.
        float_period: Floating-leg coupon period, e.g. ``3M``.
        fixed_period: Fixed-leg coupon period, e.g. ``6M``.
        index: Floating index name, ``UNSPECIFIED`` when not given.
    """

    currency: str
    float_period: str
    fixed_period: str
    index: str = UNSPECIFIED_INDEX

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", normalize_currency(self.currency))
        object.__setattr__(
            self, "float_period", _check(self.float_period, _PERIOD_RE, "float_period")
        )
        object.__setattr__(
            self, "fixed_period", _check(self.fixed_period, _PERIOD_RE, "fixed_period")
        )
        object.__setattr__(self, "index", _check(self.index, _INDEX_RE, "index"))

    def __str__(self) -> str:
        return (
            f"SWAP {self.currency} {self.fixed_period}/{self.float_period} {self.index}"
        )


@dataclass(frozen=True)
class RepoCurveKey:
    """Repo / risk-free funding curve identifier (``repo_rfr_rates`` table).

    Attributes:
        currency: ISO currency code, e.g. ``EUR``.
        period: Coupon period of the par-style repo quotes, e.g. ``6M``.
        index: Repo/RFR index name, ``UNSPECIFIED`` when not given.
    """

    currency: str
    period: str
    index: str = UNSPECIFIED_INDEX

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", normalize_currency(self.currency))
        object.__setattr__(self, "period", _check(self.period, _PERIOD_RE, "period"))
        object.__setattr__(self, "index", _check(self.index, _INDEX_RE, "index"))

    def __str__(self) -> str:
        return f"REPO {self.currency} {self.period} {self.index}"
