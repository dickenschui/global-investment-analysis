"""Dividend-income screen: trailing cash yield, target-band fit and payout-cut stress."""
from __future__ import annotations

import pandas as pd


def trailing_cash(dividends: list[dict], as_of: pd.Timestamp, days: int = 365) -> tuple[float | None, int]:
    """Sum of cash amounts with ex-date in (as_of - days, as_of]. None when no records."""
    if not dividends:
        return None, 0
    start = as_of - pd.Timedelta(days=days)
    total, n = 0.0, 0
    for d in dividends:
        ex = pd.Timestamp(d["ex_date"])
        ex = ex.tz_convert(None) if ex.tzinfo else ex
        if start < ex <= as_of and d.get("amount") is not None:
            total += float(d["amount"])
            n += 1
    return (total if n else None), n


def reconcile_scale(computed: float | None, vendor_pct: float | None) -> tuple[float | None, str]:
    """Detect pence-vs-pound (or cents) scale mismatches against the vendor's yield."""
    if computed is None:
        return (vendor_pct / 100 if vendor_pct is not None else None), "vendor yield only (no dividend records)"
    if vendor_pct in (None, 0):
        return computed, "computed from dividend records; no vendor yield to cross-check"
    ratio = computed / (vendor_pct / 100)
    if 80 <= ratio <= 120:
        return computed / 100, "dividend currency unit 100x price unit (e.g. GBP vs GBp); rescaled"
    if 0.008 <= ratio <= 0.0125:
        return computed * 100, "price unit 100x dividend unit; rescaled"
    if 0.75 <= ratio <= 1.33:
        return computed, "computed; agrees with vendor yield within 33%"
    return computed, f"computed; DIFFERS from vendor yield {vendor_pct:.2f}% (ratio {ratio:.2f}) - verify"


def income_row(symbol: str, price: float, as_of: pd.Timestamp, dividends: list[dict], vendor_yield_pct: float | None,
               band: tuple[float, float] = (0.06, 0.08)) -> dict:
    cash, n = trailing_cash(dividends, as_of)
    raw_yield = None if cash is None else cash / price
    y, basis = reconcile_scale(raw_yield, vendor_yield_pct)
    if y is None:
        fit = "no data"
    elif y < band[0]:
        fit = "below target"
    elif y <= band[1]:
        fit = "in target"
    else:
        fit = "above target (check sustainability)"
    return {
        "symbol": symbol, "price": price, "trailing_cash": cash, "payments": n,
        "yield": y, "yield_basis": basis, "vendor_yield_pct": vendor_yield_pct, "target_fit": fit,
        "yield_after_cut": {f"{int(c*100)}%": (None if y is None else y * (1 - c)) for c in (0.1, 0.2, 0.3)},
    }
