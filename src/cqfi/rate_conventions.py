"""Currency-level market conventions for swap and repo curve construction.

Sovereign conventions live on :class:`cqfi.issuers.IssuerProfile`; swap and
repo/RFR curves are quoted per *currency*, so they get their own table here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import QuantLib as ql

from cqfi.curve_keys import normalize_currency


@dataclass(frozen=True)
class CurrencyConventions:
    """Calendar, spot lag and day counts for one currency's rate curves.

    Attributes:
        ccy: ISO currency code.
        calendar_factory: Builds the settlement calendar.
        settlement_days: Spot lag in business days.
        float_day_count: Floating-leg / money-market day count (also used for
            both legs of par-style repo quotes).
        fixed_day_count: Swap fixed-leg day count.
        business_convention: Date-roll convention for both legs.
    """

    ccy: str
    calendar_factory: Callable[[], ql.Calendar]
    settlement_days: int
    float_day_count: ql.DayCounter
    fixed_day_count: ql.DayCounter
    business_convention: int = ql.ModifiedFollowing

    def calendar(self) -> ql.Calendar:
        return self.calendar_factory()

    def ql_currency(self) -> ql.Currency:
        return getattr(ql, f"{self.ccy}Currency")()


_A360 = ql.Actual360()
_A365 = ql.Actual365Fixed()
_T360 = ql.Thirty360(ql.Thirty360.BondBasis)

CURRENCY_CONVENTIONS: dict[str, CurrencyConventions] = {
    c.ccy: c
    for c in (
        CurrencyConventions(
            "USD", lambda: ql.UnitedStates(ql.UnitedStates.Settlement), 2, _A360, _T360
        ),
        CurrencyConventions("EUR", ql.TARGET, 2, _A360, _T360),
        CurrencyConventions(
            "GBP",
            lambda: ql.UnitedKingdom(ql.UnitedKingdom.Settlement),
            0,
            _A365,
            _A365,
        ),
        CurrencyConventions("JPY", ql.Japan, 2, _A360, _A365),
        CurrencyConventions("CHF", ql.Switzerland, 2, _A360, _T360),
        CurrencyConventions(
            "AUD", lambda: ql.Australia(ql.Australia.Settlement), 1, _A365, _A365
        ),
        CurrencyConventions("NZD", ql.NewZealand, 2, _A365, _A365),
        CurrencyConventions(
            "CAD", lambda: ql.Canada(ql.Canada.Settlement), 0, _A365, _A365
        ),
        CurrencyConventions("NOK", ql.Norway, 2, _A360, _T360),
        CurrencyConventions("SEK", ql.Sweden, 2, _A360, _T360),
    )
}


def resolve_currency_conventions(ccy: str) -> CurrencyConventions:
    """Return conventions for *ccy* or raise ``ValueError`` listing supported codes."""
    code = normalize_currency(ccy)
    try:
        return CURRENCY_CONVENTIONS[code]
    except KeyError:
        supported = ", ".join(sorted(CURRENCY_CONVENTIONS))
        raise ValueError(
            f"No rate-curve conventions for currency {code!r}. Supported: {supported}"
        ) from None
