"""Vectorised numpy kernels: (rolling) OLS / logit and covariance / correlation matrices.

All windows are processed at once as ``sliding_window_view`` batches, so a
rolling fit over ``m`` windows costs a handful of batched BLAS calls rather
than ``m`` Python-level fits.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

_EPS = 1e-12
_DCOR_MAX_BYTES = 600_000_000


def _windows(a: np.ndarray, window: int, step: int) -> np.ndarray:
    """Strided windows over axis 0 of a 2-D array -> ``(m, window, k)`` view."""
    return sliding_window_view(a, window, axis=0)[::step].transpose(0, 2, 1)


def window_ends(n: int, window: int, step: int) -> np.ndarray:
    """Row index of the last observation in each window."""
    return np.arange(window - 1, n, step)


@dataclass(frozen=True)
class OlsFit:
    """Batched OLS results; row ``i`` belongs to window ``i``."""

    intercept: np.ndarray  # (m,)
    beta: np.ndarray  # (m, k)
    t_stat: np.ndarray  # (m, k)
    r2: np.ndarray  # (m,)


def rolling_ols(x: np.ndarray, y: np.ndarray, window: int, step: int = 1) -> OlsFit:
    """OLS of ``y`` on ``x`` (with intercept) over every window; ``window=len(y)`` = full sample.

    Args:
        x: ``(n, k)`` regressors.
        y: ``(n,)`` target.
        window: Observations per window (> k + 1).
        step: Distance between consecutive window ends.
    """
    n, k = x.shape
    if window <= k + 1 or window > n:
        raise ValueError(f"window must be in ({k + 1}, {n}], got {window}")
    xw = _windows(x, window, step)  # (m, w, k)
    yw = _windows(y[:, None], window, step)[..., 0]  # (m, w)
    xc = xw - xw.mean(axis=1, keepdims=True)
    yc = yw - yw.mean(axis=1, keepdims=True)
    xtx = np.einsum("mwk,mwl->mkl", xc, xc)
    xty = np.einsum("mwk,mw->mk", xc, yc)
    inv = np.linalg.pinv(xtx, hermitian=True)
    beta = np.einsum("mkl,ml->mk", inv, xty)
    resid = yc - np.einsum("mwk,mk->mw", xc, beta)
    ssr = np.einsum("mw,mw->m", resid, resid)
    sst = np.einsum("mw,mw->m", yc, yc)
    se = np.sqrt(np.einsum("mkk->mk", inv) * (ssr / (window - k - 1))[:, None])
    degenerate = np.einsum("mkk->mk", xtx).min(axis=1) < _EPS  # constant regressor
    with np.errstate(divide="ignore", invalid="ignore"):
        t_stat = np.where(se > 0, beta / se, np.nan)
        r2 = np.where(sst > _EPS, 1.0 - ssr / sst, np.nan)
    intercept = yw.mean(axis=1) - np.einsum("mk,mk->m", beta, xw.mean(axis=1))
    beta[degenerate] = t_stat[degenerate] = np.nan
    intercept[degenerate] = r2[degenerate] = np.nan
    return OlsFit(intercept, beta, t_stat, r2)


@dataclass(frozen=True)
class LogitFit:
    """Batched logistic-regression results in the original (unstandardised) units."""

    intercept: np.ndarray  # (m,)
    beta: np.ndarray  # (m, k)
    z_stat: np.ndarray  # (m, k) Wald z (ridge-penalised information matrix)
    converged: np.ndarray  # (m,) bool


def _sigmoid(eta: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(eta, -30, 30)))


def rolling_logit(
    x: np.ndarray,
    y: np.ndarray,
    window: int,
    step: int = 1,
    l2: float = 1e-2,
    max_iter: int = 30,
) -> LogitFit:
    """Logistic regression of binary ``y`` on ``x`` over every window (batched Newton / IRLS).

    Features are standardised over the full sample, so ``l2`` is scale-free; a small
    ridge keeps separated windows finite. Windows with a single outcome class are NaN.

    Args:
        x: ``(n, k)`` regressors.
        y: ``(n,)`` outcomes in {0, 1}.
        window: Observations per window; ``len(y)`` for one full-sample fit.
        step: Distance between window ends.
        l2: Ridge penalty on the standardised slopes (not the intercept).
        max_iter: Newton iteration cap.
    """
    n, k = x.shape
    if window <= k + 1 or window > n:
        raise ValueError(f"window must be in ({k + 1}, {n}], got {window}")
    mu, sd = x.mean(axis=0), x.std(axis=0)
    sd = np.where(sd > _EPS, sd, 1.0)
    zw = _windows((x - mu) / sd, window, step)
    zw = np.concatenate([np.ones(zw.shape[:2] + (1,)), zw], axis=2)  # (m, w, k+1)
    yw = _windows(y[:, None].astype(float), window, step)[..., 0]
    ridge = np.diag([0.0] + [l2] * k)
    b = np.zeros((zw.shape[0], k + 1))
    active = np.ones(len(b), dtype=bool)
    for _ in range(max_iter):
        p = _sigmoid(np.einsum("mwk,mk->mw", zw, b))
        grad = np.einsum("mwk,mw->mk", zw, yw - p) - b @ ridge
        info = np.einsum("mwk,mw,mwl->mkl", zw, p * (1 - p), zw) + ridge
        delta = np.linalg.solve(info, grad[..., None])[..., 0]
        delta = np.clip(delta, -5, 5) * active[:, None]
        b += delta
        active &= np.abs(delta).max(axis=1) > 1e-8
        if not active.any():
            break
    p = _sigmoid(np.einsum("mwk,mk->mw", zw, b))
    info = np.einsum("mwk,mw,mwl->mkl", zw, p * (1 - p), zw) + ridge
    se = np.sqrt(np.einsum("mkk->mk", np.linalg.inv(info)))
    one_class = yw.min(axis=1) == yw.max(axis=1)
    beta = b[:, 1:] / sd
    intercept = b[:, 0] - (b[:, 1:] * mu / sd).sum(axis=1)
    z_stat = b[:, 1:] / se[:, 1:]
    beta[one_class] = z_stat[one_class] = np.nan
    intercept[one_class] = np.nan
    return LogitFit(intercept, beta, z_stat, ~active & ~one_class)


def logit_probability(intercept: float, beta: np.ndarray, scenario: np.ndarray) -> float:
    """P(y=1) for one predictor vector under fitted coefficients."""
    return float(_sigmoid(np.asarray(intercept + beta @ scenario)))


def _rank_columns(x: np.ndarray) -> np.ndarray:
    """Average ranks per column (ties share the mean rank)."""
    import polars as pl

    return pl.DataFrame(x).select(pl.all().rank("average")).to_numpy()


def _distance_correlation(x: np.ndarray) -> np.ndarray:
    """All-pairs distance correlation via one Gram matrix of double-centred distances."""
    n, p = x.shape
    if p * n * n * 4 > _DCOR_MAX_BYTES:
        raise ValueError(
            f"distance correlation on {n} observations x {p} series is too large; "
            "use a shorter period or fewer series"
        )
    a = np.empty((p, n * n), dtype=np.float32)
    for j in range(p):
        d = np.abs(x[:, j, None] - x[None, :, j])
        d = d - d.mean(axis=0, keepdims=True) - d.mean(axis=1, keepdims=True) + d.mean()
        a[j] = d.ravel()
    dcov2 = (a @ a.T).astype(float) / (n * n)
    sd = np.sqrt(np.clip(np.diag(dcov2), 0, None))
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.sqrt(np.clip(dcov2, 0, None)) / np.sqrt(np.outer(sd, sd))
    np.fill_diagonal(out, 1.0)
    return out


def correlation_matrix(x: np.ndarray, method: str = "pearson") -> np.ndarray:
    """Correlation matrix of the columns of ``x`` (``pearson``, ``spearman``, ``distance``)."""
    if method == "pearson":
        return np.atleast_2d(np.corrcoef(x, rowvar=False))
    if method == "spearman":
        return np.atleast_2d(np.corrcoef(_rank_columns(x), rowvar=False))
    if method == "distance":
        return _distance_correlation(x)
    raise ValueError("method must be one of: pearson, spearman, distance")


def covariance_matrix(x: np.ndarray, method: str = "pearson") -> np.ndarray:
    """Covariance of the columns of ``x`` (``spearman`` = covariance of the ranks)."""
    if method == "pearson":
        return np.atleast_2d(np.cov(x, rowvar=False))
    if method == "spearman":
        return np.atleast_2d(np.cov(_rank_columns(x), rowvar=False))
    raise ValueError("covariance method must be one of: pearson, spearman")
