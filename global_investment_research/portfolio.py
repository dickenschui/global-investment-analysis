"""Currency-aware, leakage-free minimum-variance allocation.

Chronology: weights are fitted only on the training window, applied at the first bar
after training, and evaluated on returns strictly after that bar.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.optimize import minimize


def to_base_currency(prices: pd.DataFrame, currency: dict[str, str], fx_to_base: dict[str, pd.Series],
                     base: str = "USD") -> pd.DataFrame:
    """Convert each column to base currency using contemporaneous FX (no fill across dates).

    fx_to_base[ccy] must be the number of base units per 1 unit of ccy.
    """
    out = {}
    for col in prices:
        ccy = currency[col]
        if ccy == base:
            out[col] = prices[col]
            continue
        if ccy not in fx_to_base:
            raise KeyError(f"{col}: no FX series to convert {ccy}->{base}")
        fx = fx_to_base[ccy].reindex(prices.index)
        out[col] = prices[col] * fx
    return pd.DataFrame(out)


def ledoit_wolf(returns: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf shrinkage towards scaled identity (Ledoit & Wolf, 2004)."""
    x = returns - returns.mean(axis=0)
    n, p = x.shape
    sample = x.T @ x / n
    mu = np.trace(sample) / p
    target = mu * np.eye(p)
    d2 = np.linalg.norm(sample - target, "fro") ** 2
    b2 = sum(np.linalg.norm(np.outer(row, row) - sample, "fro") ** 2 for row in x) / n**2
    shrink = 0.0 if d2 == 0 else min(b2, d2) / d2
    return shrink * target + (1 - shrink) * sample


def min_variance_weights(train_returns: pd.DataFrame, max_weight: float = 1.0) -> pd.Series:
    r = train_returns.dropna()
    p = r.shape[1]
    if max_weight * p < 1 - 1e-9:
        raise ValueError("max_weight too small for a fully invested portfolio")
    cov = ledoit_wolf(r.to_numpy())
    res = minimize(lambda w: w @ cov @ w, np.full(p, 1 / p), method="SLSQP",
                   bounds=[(0.0, max_weight)] * p,
                   constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1}],
                   options={"ftol": 1e-12, "maxiter": 500})
    if not res.success:
        raise RuntimeError(f"optimiser failed: {res.message}")
    w = np.clip(res.x, 0, None)
    return pd.Series(w / w.sum(), index=r.columns)


def portfolio_stats(r: pd.Series, bars_per_year: int) -> dict:
    r = r.dropna()
    if r.empty:
        return {"bars": 0, "total_return": None, "ann_vol": None, "max_drawdown": None}
    wealth = (1 + r).cumprod()
    return {
        "bars": int(len(r)),
        "total_return": float(wealth.iloc[-1] - 1),
        "ann_vol": float(r.std(ddof=1) * math.sqrt(bars_per_year)) if len(r) > 1 else None,
        "max_drawdown": float((wealth / wealth.cummax() - 1).min()),
    }


def backtest_min_variance(prices: pd.DataFrame, train_fraction: float = 0.7, max_weight: float = 0.4,
                          bars_per_year: int = 52) -> dict:
    """Fit on the first train_fraction of common bars; buy-and-hold afterwards vs equal weight."""
    common = prices.dropna()
    if len(common) < 20:
        raise ValueError("need at least 20 common bars")
    split = int(len(common) * train_fraction)
    train, after = common.iloc[:split], common.iloc[split:]
    w = min_variance_weights(train.pct_change().dropna(), max_weight)
    alloc_date = after.index[0]
    # Fixed shares bought at the allocation close; returns start on the next bar.
    units = w / after.iloc[0]
    value = after.mul(units, axis=1).sum(axis=1)
    eq_units = (pd.Series(1 / len(w), index=w.index)) / after.iloc[0]
    eq_value = after.mul(eq_units, axis=1).sum(axis=1)
    return {
        "weights": {k: float(v) for k, v in w.items()},
        "training": {"start": train.index[0].date().isoformat(), "end": train.index[-1].date().isoformat(), "bars": len(train)},
        "allocation_date": alloc_date.date().isoformat(),
        "evaluation": {
            "min_variance": portfolio_stats(value.pct_change().iloc[1:], bars_per_year),
            "equal_weight": portfolio_stats(eq_value.pct_change().iloc[1:], bars_per_year),
        },
    }
