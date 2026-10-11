"""Price analytics that work on any bar frequency (daily or weekly).

All functions take bars_per_year so a weekly series (52) and daily series (252) give
comparable annualised numbers. Lookbacks are expressed in calendar terms and converted
to bar counts via LOOKBACK_FRACTION.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

LOOKBACK_FRACTION = {"1w": 1 / 52, "1m": 1 / 12, "3m": 0.25, "6m": 0.5, "1y": 1.0}


def bars_for(label: str, bars_per_year: int) -> int:
    return max(1, round(LOOKBACK_FRACTION[label] * bars_per_year))


def infer_bars_per_year(index: pd.DatetimeIndex) -> int:
    """52 for weekly-spaced bars, 252 for daily; raises on anything else."""
    gaps = pd.Series(index).diff().dt.days.dropna()
    if gaps.empty:
        raise ValueError("need at least two bars to infer frequency")
    median = gaps.median()
    if 5 <= median <= 9:
        return 52
    if median <= 4:
        return 252
    raise ValueError(f"unsupported bar spacing: median {median} days")


def trailing_return(s: pd.Series, bars: int) -> float | None:
    s = s.dropna()
    if len(s) <= bars:
        return None
    return float(s.iloc[-1] / s.iloc[-1 - bars] - 1)


def annualised_vol(s: pd.Series, bars_per_year: int) -> float | None:
    r = s.dropna().pct_change().dropna()
    if len(r) < 8:
        return None
    return float(r.std(ddof=1) * math.sqrt(bars_per_year))


def max_drawdown(s: pd.Series) -> float | None:
    s = s.dropna()
    if s.empty:
        return None
    return float((s / s.cummax() - 1).min())


def drawdown_from_high(s: pd.Series) -> float | None:
    s = s.dropna()
    if s.empty:
        return None
    return float(s.iloc[-1] / s.max() - 1)


def ma_windows(bars_per_year: int) -> tuple[int, int]:
    """10/40 weeks on weekly bars (the rule as written in the Handbook and tested in the backtest),
    50/200 days on daily bars. Rounding 52*200/252 would give 41 weeks, which the backtest never tested."""
    if bars_per_year <= 60:
        return 10, 40
    return round(bars_per_year * 50 / 252), round(bars_per_year * 200 / 252)


def trend_state(s: pd.Series, bars_per_year: int) -> dict:
    """Classic 50/200-day structure (10/40 weeks on weekly bars).

    up    : price > short MA > long MA
    down  : price < short MA < long MA
    mixed : anything else; insufficient history -> None
    """
    s = s.dropna()
    short_n, long_n = ma_windows(bars_per_year)
    if len(s) < long_n:
        return {"state": None, "short_ma": None, "long_ma": None, "pct_vs_long_ma": None}
    short_ma, long_ma, last = s.iloc[-short_n:].mean(), s.iloc[-long_n:].mean(), s.iloc[-1]
    if last > short_ma > long_ma:
        state = "up"
    elif last < short_ma < long_ma:
        state = "down"
    else:
        state = "mixed"
    return {"state": state, "short_ma": float(short_ma), "long_ma": float(long_ma),
            "pct_vs_long_ma": float(last / long_ma - 1)}


def last_move_zscore(s: pd.Series) -> float | None:
    """Latest bar's return in standard deviations of the prior bars' returns."""
    r = s.dropna().pct_change().dropna()
    if len(r) < 12:
        return None
    hist = r.iloc[:-1]
    sd = hist.std(ddof=1)
    return None if sd == 0 else float((r.iloc[-1] - hist.mean()) / sd)


def instrument_metrics(s: pd.Series, bars_per_year: int) -> dict:
    out = {f"ret_{k}": trailing_return(s, bars_for(k, bars_per_year)) for k in LOOKBACK_FRACTION}
    # Vendors often return 52 rather than 53 weekly bars for "one year". Allow a one-bar
    # shortfall on the 1y figure only, and record the bars actually spanned.
    n_1y = bars_for("1y", bars_per_year)
    n_avail = int(s.notna().sum()) - 1
    out["ret_1y_bars"] = n_1y if out["ret_1y"] is not None else (n_avail if n_avail == n_1y - 1 else None)
    if out["ret_1y"] is None and out["ret_1y_bars"]:
        out["ret_1y"] = trailing_return(s, n_avail)
    out.update(
        vol=annualised_vol(s, bars_per_year),
        max_drawdown=max_drawdown(s),
        from_high=drawdown_from_high(s),
        last_move_z=last_move_zscore(s),
        last=float(s.dropna().iloc[-1]) if s.notna().any() else None,
        last_date=s.dropna().index[-1].date().isoformat() if s.notna().any() else None,
        bars=int(s.notna().sum()),
    )
    out.update(trend=trend_state(s, bars_per_year))
    r3, r12 = out["ret_3m"], out["ret_1y"]
    # Momentum score: 12-1 style blend, risk-scaled. None when any input is missing.
    out["momentum_score"] = (
        None if None in (r3, r12, out["vol"]) or not out["vol"] else float((0.5 * r3 + 0.5 * r12) / out["vol"])
    )
    return out


def returns_frame(prices: pd.DataFrame) -> pd.DataFrame:
    """Simple returns on bars where both ends exist; no forward filling across holidays."""
    return prices.pct_change(fill_method=None)


def split_correlation(returns: pd.DataFrame, a: str, b: str, min_obs: int = 12) -> dict:
    """Correlation in the first and second half of the common sample.

    A relationship is only labelled 'stable' if both halves agree in sign and each
    |rho| >= 0.3 — a deliberately simple out-of-sample check against chance patterns.
    """
    pair = returns[[a, b]].dropna()
    n = len(pair)
    if n < 2 * min_obs:
        return {"n": n, "full": None, "first_half": None, "second_half": None, "stable": False}
    half = n // 2
    first, second = pair.iloc[:half], pair.iloc[half:]
    c1, c2 = first[a].corr(first[b]), second[a].corr(second[b])
    full = pair[a].corr(pair[b])
    stable = bool(np.sign(c1) == np.sign(c2) and abs(c1) >= 0.3 and abs(c2) >= 0.3)
    return {"n": n, "full": float(full), "first_half": float(c1), "second_half": float(c2), "stable": stable}
