"""Input request type for bond future basis analytics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from cqfi.bond_futures import BondFuture
from cqfi.delivery_basket import DeliveryBasket
from cqfi.numeric_term_structure import NumericTermStructure


class DeliveryDateChoice(str, Enum):
    """Where inside the delivery period basis is measured.

    ``MINMAX`` is the seller's choice: the business day on which some
    deliverable bond has the lowest net basis. On a contract with a single
    delivery day it is the same day as ``FIRST`` and ``LAST``.
    """

    FIRST = "first"
    LAST = "last"
    MINMAX = "minmax"


def parse_delivery_request(
    value: date | str | DeliveryDateChoice | None,
) -> date | DeliveryDateChoice:
    """Parse a delivery date, a ``YYYY-MM-DD`` string, or ``first``/``last``/``minmax``.

    ``None`` is the seller's choice (``minmax``).

    Raises:
        ValueError: If *value* is not a date or one of the choice names.
    """
    if value is None:
        return DeliveryDateChoice.MINMAX
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, DeliveryDateChoice):
        return value
    text = str(value).strip().lower()
    try:
        return DeliveryDateChoice(text)
    except ValueError:
        pass
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        names = ", ".join(choice.value for choice in DeliveryDateChoice)
        raise ValueError(
            f"delivery date {value!r} must be a date (YYYY-MM-DD) or one of {names}"
        ) from exc


@dataclass(frozen=True)
class BondFutureInput:
    """Inputs required to compute basis analytics for a delivery basket.

    Attributes:
        delivery_basket: The deliverable bonds, whose membership was fixed by
            the contract's delivery month.
        bond_future: The dated contract being analysed.
        trade_date: Date the analytics are computed as of.  It need not be
            related to the delivery month — a basket's membership is fixed
            once, but it can be valued on any earlier date.
        delivery: Day basis is measured to. A ``date``, a ``YYYY-MM-DD``
            string, or ``first`` / ``last`` / ``minmax``. ``None`` and
            ``minmax`` ask for the seller's choice: the business day in the
            delivery period on which a deliverable bond has the lowest net
            basis. A date that is not a business day in that period is rejected.
        repo_term_structure: Financing curve applied to every deliverable
            bond, unless a member of ``delivery_basket`` carries its own via
            ``DeliveryBasket.add(..., repo_term_structure=...)`` or
            ``DeliveryBasket.set_repo_term_structure``, in which case the
            per-bond curve wins for that bond only. When neither is set, the
            bond curve's own forward rate to delivery is used instead.
        curve_label: Which curve collection to discount with.
        futures_price: Observed futures price.  When ``None`` the price is
            implied so that the cheapest-to-deliver bond's net basis is zero.
        shift_bp: Size of the parallel zero-curve shift used for delta and
            gamma, in basis points.
    """

    delivery_basket: DeliveryBasket
    bond_future: BondFuture
    trade_date: date
    delivery: date | str | DeliveryDateChoice | None = None
    repo_term_structure: NumericTermStructure | None = None
    curve_label: str = "BOND_ZERO"
    futures_price: float | None = None
    shift_bp: float = 1.0

    def __post_init__(self) -> None:
        if self.shift_bp <= 0:
            raise ValueError(f"shift_bp must be positive, got {self.shift_bp}")
        if self.futures_price is not None and self.futures_price <= 0:
            raise ValueError(
                f"futures_price must be positive, got {self.futures_price}"
            )
        horizon = self.resolved_delivery_date()
        if horizon is None:
            horizon = self.bond_future.delivery_end_date()
        if self.trade_date > horizon:
            raise ValueError(
                f"trade_date {self.trade_date.isoformat()} is after the delivery "
                f"date {horizon.isoformat()}"
            )

    @classmethod
    def from_basket(
        cls,
        basket: DeliveryBasket,
        trade_date: date,
        **kwargs,
    ) -> BondFutureInput:
        """Build a request from a basket, taking its contract along."""
        return cls(
            delivery_basket=basket,
            bond_future=basket.bond_future,
            trade_date=trade_date,
            **kwargs,
        )

    def settlement_date(self) -> date:
        """Return the bond settlement date implied by the trade date."""
        return self.bond_future.convention.issuer().settlement_date(self.trade_date)

    def resolved_delivery_date(self) -> date | None:
        """Return the requested delivery day, or ``None`` for seller's choice.

        ``first`` and ``last`` are the first and last business days of the
        delivery period. An explicit date must itself be one of those business
        days. ``minmax`` is resolved later, once every bond has been carried
        to every day in the period.

        Raises:
            ValueError: If the request does not name a legal delivery day.
        """
        spec = parse_delivery_request(self.delivery)
        days = self.bond_future.delivery_business_days()
        if not days:
            raise ValueError(
                f"{self.bond_future} has no business day in its delivery period"
            )
        if spec is DeliveryDateChoice.MINMAX:
            return None
        if spec is DeliveryDateChoice.FIRST:
            return days[0]
        if spec is DeliveryDateChoice.LAST:
            return days[-1]
        if spec not in days:
            raise ValueError(
                f"delivery date {spec.isoformat()} is not a business day in the "
                f"{self.bond_future} delivery period "
                f"({days[0].isoformat()} to {days[-1].isoformat()})"
            )
        return spec
