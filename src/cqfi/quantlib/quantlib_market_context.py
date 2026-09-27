"""Market data containers for curves and FX used in QuantLib analytics.

Example
-------
Build a bond curve handle and register it on a :class:`QuantlibMarketContext`::

    from datetime import date

    import polars as pl

    from cqfi.issuers import resolve_issuer
    from cqfi.quantlib.quantlib_curve import ql_build_zero_curve
    from cqfi.quantlib.quantlib_market_context import (
        FXC,
        QuantLibCurveCollection,
        QuantlibMarketContext,
    )

    as_of = date(2020, 1, 2)
    issuer = resolve_issuer("USA")

    rates_df = pl.DataFrame(
        {
            "tenor_column": ["Y001p0", "Y005p0", "Y010p0"],
            "tenor_label": ["1Y", "5Y", "10Y"],
            "tenor_years": [1.0, 5.0, 10.0],
            "rate_pct": [1.5, 2.0, 2.5],
        }
    )
    curve_handle, _ = ql_build_zero_curve(issuer, as_of, rates_df)

    curves = QuantLibCurveCollection(as_of=as_of)
    curves.set_bond_curve("USA", curve_handle)

    fx = FXC(as_of=as_of)
    fx.set_rate("AUD", "USD", 1.45)

    ctx = QuantlibMarketContext()
    ctx.set_curve_collection(curves)
    ctx.set_fxc(fx)

    # curve_handle is a ql.YieldTermStructureHandle; retrieve via issuer code:
    usa_curve = ctx.curve_collection().bond_curve("USA")
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import polars as pl
import QuantLib as ql

from cqfi.config import get_settings
from cqfi.curve_keys import RepoCurveKey, SwapCurveKey
from cqfi.data.rates_loader import (
    REPO_TABLE,
    list_rate_curve_keys,
    load_curve_rates,
    load_repo_rates,
    load_swap_rates,
)
from cqfi.issuers import IssuerProfile, RateType, resolve_issuer
from cqfi.quantlib.quantlib_curve import (
    ZeroCurveBuildOptions,
    ql_build_repo_curve,
    ql_build_swap_curve,
)

SWAP_PAR_LABEL = "SWAP_PAR"
REPO_RFR_LABEL = "REPO_RFR"


def _normalize_ccy(code: str) -> str:
    return code.strip().upper()


def _normalize_issuer(code: str) -> str:
    return code.strip().upper()


def _as_of_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


@dataclass
class FXC:
    """Cross table of FX rates as of a spot or forward date.

    :meth:`rate` takes two currency codes and returns how many units of the
    *first* currency are required for one unit of the *second* — e.g.
    ``rate("AUD", "USD")`` is the number of AUD needed to buy 1 USD, and
    ``rate("USD", "AUD")`` returns the reciprocal when only the AUD/USD quote
    has been stored.
    """

    as_of: date | datetime
    _rates: dict[tuple[str, str], float] = field(default_factory=dict, repr=False)

    def set_rate(self, ccy_for: str, ccy_per: str, value: float) -> None:
        """Store *value* units of *ccy_for* per 1 unit of *ccy_per*."""
        base = _normalize_ccy(ccy_for)
        quote = _normalize_ccy(ccy_per)
        if base == quote:
            if value != 1.0:
                raise ValueError("Identity FX rate must be 1.0")
            return
        self._rates[(base, quote)] = float(value)

    def rate(self, ccy_for: str, ccy_per: str) -> float:
        """Return units of *ccy_for* required for 1 unit of *ccy_per*."""
        base = _normalize_ccy(ccy_for)
        quote = _normalize_ccy(ccy_per)
        if base == quote:
            return 1.0

        direct = self._rates.get((base, quote))
        if direct is not None:
            return direct

        inverse = self._rates.get((quote, base))
        if inverse is not None:
            if inverse == 0.0:
                raise ValueError(f"Cannot invert zero FX rate for {quote}/{base}")
            return 1.0 / inverse

        raise KeyError(f"No FX rate for {base}/{quote} as of {self.as_of}")

    def known_pairs(self) -> list[tuple[str, str]]:
        """Return stored (ccy_for, ccy_per) pairs (not reciprocals)."""
        return list(self._rates.keys())

    def __or__(self, other: FXC) -> FXC:
        """Merge two FX tables with the same ``as_of`` date.

        Stored rate pairs are combined; *other* wins on duplicate keys.
        """
        if not isinstance(other, FXC):
            return NotImplemented
        if _as_of_date(self.as_of) != _as_of_date(other.as_of):
            raise ValueError(
                f"Cannot merge FX tables with different as_of dates: "
                f"{self.as_of!r} vs {other.as_of!r}"
            )
        return FXC(
            as_of=self.as_of,
            _rates={**self._rates, **other._rates},
        )

    def __ror__(self, other: FXC) -> FXC:
        if not isinstance(other, FXC):
            return NotImplemented
        return other | self


@dataclass
class QuantLibCurveCollection:
    """Yield curves as of a single valuation date or datetime.

    Bond curves are indexed by sovereign issuer code (``USA``, ``DEU``, …) as
    built by :func:`cqfi.quantlib.quantlib_curve.ql_build_zero_curve`.  Swap
    curves are indexed by :class:`SwapCurveKey` and repo/funding curves by the
    distinct :class:`RepoCurveKey`.
    """

    as_of: date | datetime
    _bond_curves: dict[str, ql.YieldTermStructureHandle] = field(
        default_factory=dict, repr=False
    )
    _swap_curves: dict[SwapCurveKey, ql.YieldTermStructureHandle] = field(
        default_factory=dict, repr=False
    )
    _repo_curves: dict[RepoCurveKey, ql.YieldTermStructureHandle] = field(
        default_factory=dict, repr=False
    )

    def set_bond_curve(
        self,
        issuer: str,
        curve: ql.YieldTermStructure | ql.YieldTermStructureHandle,
    ) -> None:
        """Register a government bond curve for *issuer*."""
        code = _normalize_issuer(issuer)
        if isinstance(curve, ql.YieldTermStructureHandle):
            self._bond_curves[code] = curve
        else:
            self._bond_curves[code] = ql.YieldTermStructureHandle(curve)

    def bond_curve(self, issuer: str) -> ql.YieldTermStructureHandle:
        """Return the bond curve handle for *issuer*."""
        code = _normalize_issuer(issuer)
        try:
            return self._bond_curves[code]
        except KeyError as exc:
            raise KeyError(
                f"No bond curve for issuer {code!r} as of {self.as_of}"
            ) from exc

    def bond_issuers(self) -> list[str]:
        """Issuer codes with a registered bond curve."""
        return sorted(self._bond_curves.keys())

    @staticmethod
    def _as_handle(
        curve: ql.YieldTermStructure | ql.YieldTermStructureHandle,
    ) -> ql.YieldTermStructureHandle:
        if isinstance(curve, ql.YieldTermStructureHandle):
            return curve
        return ql.YieldTermStructureHandle(curve)

    def set_swap_curve(
        self,
        key: SwapCurveKey,
        curve: ql.YieldTermStructure | ql.YieldTermStructureHandle,
    ) -> None:
        """Register a swap par curve under *key*."""
        if not isinstance(key, SwapCurveKey):
            raise TypeError(f"Expected SwapCurveKey, got {type(key).__name__}")
        self._swap_curves[key] = self._as_handle(curve)

    def swap_curve(self, key: SwapCurveKey) -> ql.YieldTermStructureHandle:
        """Return the swap curve handle for *key*; ``KeyError`` when absent."""
        try:
            return self._swap_curves[key]
        except KeyError as exc:
            raise KeyError(f"No swap curve {key} as of {self.as_of}") from exc

    def swap_curve_keys(self) -> list[SwapCurveKey]:
        """Registered swap curve keys."""
        return list(self._swap_curves.keys())

    def set_repo_curve(
        self,
        key: RepoCurveKey,
        curve: ql.YieldTermStructure | ql.YieldTermStructureHandle,
    ) -> None:
        """Register a repo/funding curve under *key*."""
        if not isinstance(key, RepoCurveKey):
            raise TypeError(f"Expected RepoCurveKey, got {type(key).__name__}")
        self._repo_curves[key] = self._as_handle(curve)

    def repo_curve(self, key: RepoCurveKey) -> ql.YieldTermStructureHandle:
        """Return the repo curve handle for *key*; ``KeyError`` when absent."""
        try:
            return self._repo_curves[key]
        except KeyError as exc:
            raise KeyError(f"No repo curve {key} as of {self.as_of}") from exc

    def repo_curve_keys(self) -> list[RepoCurveKey]:
        """Registered repo curve keys."""
        return list(self._repo_curves.keys())

    def __or__(self, other: QuantLibCurveCollection) -> QuantLibCurveCollection:
        """Merge two collections with the same ``as_of`` date.

        Bond, swap and repo curve dicts are combined; *other* wins on duplicate keys.
        """
        if not isinstance(other, QuantLibCurveCollection):
            return NotImplemented
        if _as_of_date(self.as_of) != _as_of_date(other.as_of):
            raise ValueError(
                f"Cannot merge curve collections with different as_of dates: "
                f"{self.as_of!r} vs {other.as_of!r}"
            )
        return QuantLibCurveCollection(
            as_of=self.as_of,
            _bond_curves={**self._bond_curves, **other._bond_curves},
            _swap_curves={**self._swap_curves, **other._swap_curves},
            _repo_curves={**self._repo_curves, **other._repo_curves},
        )

    def __ror__(self, other: QuantLibCurveCollection) -> QuantLibCurveCollection:
        if not isinstance(other, QuantLibCurveCollection):
            return NotImplemented
        return other | self


def ql_build_curve_collections(
    date_issuer_pairs: Iterable[tuple[date, IssuerProfile]],
    db_path: str | Path | None = None,
    *,
    curve_options: ZeroCurveBuildOptions | None = None,
) -> list[QuantLibCurveCollection]:
    """Build one :class:`QuantLibCurveCollection` per valuation date.

    *date_issuer_pairs* must contain unique ``(date, issuer)`` combinations.
    Pairs sharing the same date are grouped into a single collection whose
    ``_bond_curves`` dict holds one entry per issuer.  The returned list is
    sorted by date ascending with no duplicate ``as_of`` values.

    Pillar rates are loaded from *db_path* via
    :func:`cqfi.data.rates_loader.load_curve_rates`.  When *db_path*
    is ``None``, ``paths.ycs_db`` from ``config/cqfi.yaml`` is used.

    Parameters
    ----------
    date_issuer_pairs:
        ``(valuation_date, issuer)`` pairs to build curves for.
    db_path:
        Path to the rates database.  Defaults to ``ycs_db`` from
        ``config/cqfi.yaml`` (respecting ``CQFI_CONFIG`` / ``CQFI_YCS_DB``).
    curve_options:
        Curve construction options forwarded to :func:`ql_build_zero_curve`.
        Defaults to :class:`ZeroCurveBuildOptions` when ``None``.

    Returns
    -------
    list[QuantLibCurveCollection]
        One collection per distinct valuation date, ascending.
    """
    if curve_options is None:
        curve_options = ZeroCurveBuildOptions()

    resolved_db_path = db_path if db_path is not None else get_settings().ycs_db_path

    by_date: dict[date, list[IssuerProfile]] = defaultdict(list)
    seen: set[tuple[date, str]] = set()

    for val_date, issuer in date_issuer_pairs:
        key = (val_date, issuer.source_code)
        if key in seen:
            raise ValueError(
                f"Duplicate (date, issuer) pair: {val_date.isoformat()}, "
                f"{issuer.source_code!r}"
            )
        seen.add(key)
        by_date[val_date].append(issuer)

    collections: list[QuantLibCurveCollection] = []
    for val_date in sorted(by_date):
        collection = QuantLibCurveCollection(as_of=val_date)
        for issuer in by_date[val_date]:
            rates_df = load_curve_rates(
                resolved_db_path,
                issuer,
                val_date,
                rate_type=curve_options.rate_type,
            )
            curve_handle, _ = curve_options.build(issuer, val_date, rates_df)
            collection.set_bond_curve(issuer.source_code, curve_handle)
        collections.append(collection)

    return collections


_BOND_CURVE_LABELS: dict[RateType, str] = {
    RateType.ZERO: "BOND_ZERO",
    RateType.PAR: "BOND_PAR",
}


def ql_build_market_context(
    trade_date: date,
    issuers: list[str],
    db_path: str | Path | None = None,
    *,
    curve_options: ZeroCurveBuildOptions | None = None,
) -> QuantlibMarketContext:
    """Build a :class:`QuantlibMarketContext` with bond curves for *trade_date*.

    Resolves each issuer name via :func:`cqfi.issuers.resolve_issuer`,
    builds curves with :func:`ql_build_curve_collections`, and registers the
    resulting collection under ``"BOND_ZERO"`` or ``"BOND_PAR"`` depending on
    *curve_options* ``rate_type``.

    Parameters
    ----------
    trade_date:
        Valuation date for all bond curves.
    issuers:
        Sovereign issuer codes (e.g. ``"USA"``, ``"DEU"``).
    db_path:
        Forwarded to :func:`ql_build_curve_collections`.
    curve_options:
        Curve construction options.  Defaults to :class:`ZeroCurveBuildOptions`
        when ``None``.

    Returns
    -------
    QuantlibMarketContext
        Context with one labelled bond curve collection.
    """
    if not issuers:
        raise ValueError("At least one issuer is required")

    if curve_options is None:
        curve_options = ZeroCurveBuildOptions()

    date_issuer_pairs = [
        (trade_date, resolve_issuer(issuer)) for issuer in issuers
    ]
    collections = ql_build_curve_collections(
        date_issuer_pairs,
        db_path,
        curve_options=curve_options,
    )
    if len(collections) != 1:
        raise RuntimeError(
            f"Expected one curve collection for {trade_date.isoformat()}, "
            f"got {len(collections)}"
        )

    label = _BOND_CURVE_LABELS[curve_options.rate_type]
    context = QuantlibMarketContext()
    context.set_curve_collection(collections[0], label=label, curve_options=curve_options)
    return context


@dataclass
class QuantlibMarketContext:
    """Aggregated market data: curve collections and FX cross tables.

    Multiple labelled instances are supported (e.g. spot vs forward FX, or
    alternative curve sets).  Use ``"default"`` as the conventional label when
    only one instance of each type is present.
    """

    as_of: date | None = None
    curve_collections: dict[str, QuantLibCurveCollection] = field(default_factory=dict)
    curve_collection_options: dict[str, ZeroCurveBuildOptions] = field(
        default_factory=dict, repr=False
    )
    fx_rates: dict[str, FXC] = field(default_factory=dict)
    curve_diagnostics: dict[tuple[str, SwapCurveKey | RepoCurveKey], pl.DataFrame] = field(
        default_factory=dict, repr=False
    )

    def set_curve_collection(
        self,
        collection: QuantLibCurveCollection,
        label: str = "default",
        curve_options: ZeroCurveBuildOptions | None = None,
    ) -> None:
        if self.as_of is not None and collection.as_of != self.as_of:
            raise ValueError(f"Collection as_of {collection.as_of} does not match context as_of {self.as_of}")
        if self.as_of is None:
            self.as_of = collection.as_of
        if label in self.curve_collections:
            self.curve_collections[label] = self.curve_collections[label] | collection
        else:
            self.curve_collections[label] = collection
        if curve_options is not None:
            self.curve_collection_options[label] = curve_options

    def has(self, issuer: str, label: str = "BOND_ZERO") -> bool:
        if label not in self.curve_collections:
            return False
        return _normalize_issuer(issuer) in self.curve_collections[label].bond_issuers()

    def _resolve_curve_options(self, label: str) -> ZeroCurveBuildOptions:
        if label in self.curve_collection_options:
            return self.curve_collection_options[label]
        for rate_type, bond_label in _BOND_CURVE_LABELS.items():
            if bond_label == label:
                return ZeroCurveBuildOptions(rate_type=rate_type)
        return ZeroCurveBuildOptions()

    def ensure_bond_curve(
        self,
        issuer: str,
        label: str = "BOND_ZERO",
        db_path: str | Path | None = None,
    ) -> ql.YieldTermStructureHandle:
        """Build and register a bond curve for *issuer* when absent from *label*."""
        code = _normalize_issuer(issuer)
        if self.has(code, label):
            return self.curve_collection(label).bond_curve(code)

        if self.as_of is None:
            raise ValueError(
                "Cannot build a bond curve before the context as_of date is set"
            )

        curve_options = self._resolve_curve_options(label)
        profile = resolve_issuer(code)
        val_date = _as_of_date(self.as_of)
        resolved_db_path = db_path if db_path is not None else get_settings().ycs_db_path
        rates_df = load_curve_rates(
            resolved_db_path,
            profile,
            val_date,
            rate_type=curve_options.rate_type,
        )
        curve_handle, _ = curve_options.build(profile, val_date, rates_df)

        if label in self.curve_collections:
            self.curve_collections[label].set_bond_curve(code, curve_handle)
        else:
            collection = QuantLibCurveCollection(as_of=self.as_of)
            collection.set_bond_curve(code, curve_handle)
            self.set_curve_collection(collection, label=label, curve_options=curve_options)

        return curve_handle

    def _rate_curve_collection(self, label: str) -> QuantLibCurveCollection:
        if self.as_of is None:
            raise ValueError("Cannot build a curve before the context as_of date is set")
        if label not in self.curve_collections:
            self.set_curve_collection(QuantLibCurveCollection(as_of=self.as_of), label=label)
        return self.curve_collections[label]

    def ensure_repo_curve(
        self,
        key: RepoCurveKey,
        db_path: str | Path | None = None,
    ) -> ql.YieldTermStructureHandle:
        """Build and register the repo curve for *key* under ``REPO_RFR`` when absent."""
        collection = self._rate_curve_collection(REPO_RFR_LABEL)
        if key in collection.repo_curve_keys():
            return collection.repo_curve(key)
        val_date = _as_of_date(self.as_of)
        db = db_path if db_path is not None else get_settings().ycs_db_path
        handle, diag = ql_build_repo_curve(key, val_date, load_repo_rates(db, key, val_date))
        collection.set_repo_curve(key, handle)
        self.curve_diagnostics[(REPO_RFR_LABEL, key)] = diag
        return handle

    def ensure_swap_curve(
        self,
        key: SwapCurveKey,
        db_path: str | Path | None = None,
    ) -> ql.YieldTermStructureHandle:
        """Build and register the swap curve for *key* under ``SWAP_PAR`` when absent.

        Swaps are discounted off the same-currency repo curve (dual-curve). When
        no repo quotes exist for the currency/date, the curve self-discounts and
        its diagnostics carry ``discounting="self"``.
        """
        collection = self._rate_curve_collection(SWAP_PAR_LABEL)
        if key in collection.swap_curve_keys():
            return collection.swap_curve(key)
        val_date = _as_of_date(self.as_of)
        db = db_path if db_path is not None else get_settings().ycs_db_path
        rates_df = load_swap_rates(db, key, val_date)
        discount = None
        repo_keys = [
            k
            for k in list_rate_curve_keys(db, REPO_TABLE, val_date, key.currency)
            if k.index == key.index
        ]
        if repo_keys:
            try:
                discount = self.ensure_repo_curve(repo_keys[0], db)
            except LookupError:
                discount = None
        handle, diag = ql_build_swap_curve(key, val_date, rates_df, discount)
        collection.set_swap_curve(key, handle)
        self.curve_diagnostics[(SWAP_PAR_LABEL, key)] = diag
        return handle

    def curve_collection(self, label: str = "default") -> QuantLibCurveCollection:
        try:
            return self.curve_collections[label]
        except KeyError as exc:
            raise KeyError(
                f"No curve collection labelled {label!r}"
            ) from exc

    def curve_build_options(self, label: str = "default") -> ZeroCurveBuildOptions:
        """Return the :class:`ZeroCurveBuildOptions` registered for *label*."""
        try:
            return self.curve_collection_options[label]
        except KeyError as exc:
            raise KeyError(
                f"No curve build options for collection labelled {label!r}"
            ) from exc

    def set_fxc(self, fxc: FXC, label: str = "default") -> None:
        if label in self.fx_rates:
            raise ValueError(f"FX table labelled {label} already exists")
        if self.as_of is not None and fxc.as_of != self.as_of:
            raise ValueError(f"FX table as_of {fxc.as_of} does not match context as_of {self.as_of}")
        if self.as_of is None:
            self.as_of = fxc.as_of
        self.fx_rates[label] = fxc

    def fxc(self, label: str = "default") -> FXC:
        try:
            return self.fx_rates[label]
        except KeyError as exc:
            raise KeyError(f"No FX table labelled {label!r}") from exc

    def curve_collection_labels(self) -> list[str]:
        return sorted(self.curve_collections.keys())

    def fxc_labels(self) -> list[str]:
        return sorted(self.fx_rates.keys())


from cqfi.quantlib.quantlib_market_context_manager import auto_register_market_context

auto_register_market_context(QuantlibMarketContext)
