"""Exact expiry payoff of any option combination, and a quality screen for saved option chains.

expiry_analysis generalises options.analyse (which handles only one-direction verticals and single legs):
the expiry P&L of calls and puts on one underlying is piecewise linear in the price, with kinks only at the
strikes, so evaluating it at 0 and every strike, plus the slope beyond the highest strike, gives the whole
curve exactly. Breakevens are solved on each linear piece; nothing is sampled on a grid.

Scope: simultaneous European-style settlement at one expiry, a non-negative underlying, no early assignment,
fees, financing, margin or tax.
"""
from __future__ import annotations

import math
from datetime import date
from functools import wraps


def _check_finite(value, path="result"):
    if isinstance(value, dict):
        for k, v in value.items():
            _check_finite(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _check_finite(v, f"{path}[{i}]")
    elif isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value):
        raise ValueError(f"{path} is not a finite number")


def _finite_result(function):
    """Never return an overflowed or NaN number as if it were a valid risk figure."""
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            result = function(*args, **kwargs)
        except OverflowError as exc:
            raise ValueError("derived payoff value exceeds the finite numeric range") from exc
        _check_finite(result)
        return result
    return checked


def _fin(v, name) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{name} must be a finite number")
    return float(v)


def _legs(legs) -> list[tuple[float, str, float]]:
    """Accept (qty, right, strike) tuples or {'qty','right','strike'} dicts; qty > 0 long, < 0 short."""
    out = []
    for leg in legs:
        q, r, k = (leg["qty"], leg["right"], leg["strike"]) if isinstance(leg, dict) else leg
        q, k = _fin(q, "quantity"), _fin(k, "strike")
        if q == 0:
            raise ValueError("quantity must be non-zero")
        if r not in ("C", "P"):
            raise ValueError(f"right must be 'C' or 'P', got {r!r}")
        if k <= 0:
            raise ValueError("strike must be positive")
        out.append((q, r, k))
    if not out:
        raise ValueError("at least one leg is required")
    return out


def expiry_value(legs, spot: float) -> float:
    """Per-share value of the legs at expiry for an underlying price `spot`."""
    s = _fin(spot, "spot")
    if s < 0:
        raise ValueError("spot must be non-negative")
    return math.fsum(q * (max(0.0, s - k) if r == "C" else max(0.0, k - s)) for q, r, k in _legs(legs))


@_finite_result
def expiry_analysis(legs, net_debit: float, multiplier: float = 100) -> dict:
    """Exact expiry P&L per set of contracts: P&L(S) = multiplier x (value(S) - net_debit).
    net_debit is per share, positive when paid, negative for a net credit. Returns the P&L at each kink,
    breakevens, max profit / max loss (None when unbounded, with the flag set) and zero-P&L intervals."""
    lg = _legs(legs)
    debit = _fin(net_debit, "net debit")
    mult = _fin(multiplier, "multiplier")
    if mult <= 0:
        raise ValueError("multiplier must be positive")
    knots = sorted({0.0, *(k for _, _, k in lg)})
    pnl = [mult * (expiry_value(lg, s) - debit) for s in knots]
    # Beyond the highest strike every put is worthless and every call gains q per unit of price.
    tail = mult * math.fsum(q for q, r, _ in lg if r == "C")
    roots, flat = [], []
    for (a, b), (va, vb) in zip(zip(knots, knots[1:]), zip(pnl, pnl[1:])):
        if va == 0 and vb == 0:
            flat.append([a, b])
        elif (va < 0 < vb) or (vb < 0 < va):
            roots.append(a + (b - a) * (-va) / (vb - va))
    roots += [s for s, v in zip(knots, pnl) if v == 0]
    if tail != 0 and -pnl[-1] / tail > 0:
        roots.append(knots[-1] - pnl[-1] / tail)
    if tail == 0 and pnl[-1] == 0:
        flat.append([knots[-1], None])
    loss_unbounded, profit_unbounded = tail < 0, tail > 0
    lowest, highest = min(pnl), max(pnl)
    return {
        "knots": [{"spot": s, "pnl": v} for s, v in zip(knots, pnl)],
        "tail_slope": tail,
        "breakevens": sorted({round(x, 10) for x in roots}),
        "zero_pnl_intervals": flat,
        "max_loss": None if loss_unbounded else max(0.0, -lowest),
        "loss_unbounded": loss_unbounded,
        "max_profit": None if profit_unbounded else max(0.0, highest),
        "profit_unbounded": profit_unbounded,
        "net_debit": debit, "multiplier": mult,
        "scope": "Expiry only, one underlying, simultaneous settlement; excludes early assignment, fees, margin, "
                 "financing and tax.",
    }


# --- Option chain validation ---------------------------------------------------------------------------------

def _bad_number(v) -> bool:
    return isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)


def validate_chain(chains_json: dict, as_of: str | None = None) -> list[dict]:
    """Quote-quality issues in a saved options.json, one row per problem (a contract can have several).
    as_of defaults to the date of retrieved_at_utc. An empty list means no issue found, not that the quotes
    are executable."""
    if as_of is None:
        stamp = chains_json.get("retrieved_at_utc")
        as_of = stamp[:10] if stamp else None
    asof = date.fromisoformat(as_of) if as_of else None
    issues = []
    for u in chains_json.get("underlyings") or []:
        sym, seen = u.get("symbol"), set()
        for e in u.get("expiries") or []:
            exp = e.get("expiry")

            def add(c, issue):
                issues.append({"symbol": sym, "expiry": exp, "strike": c.get("strike"), "right": c.get("right"),
                               "issue": issue})
            try:
                exp_date = date.fromisoformat(exp)
            except (TypeError, ValueError):
                exp_date = None
            for c in e.get("contracts") or []:
                if exp_date is None:
                    add(c, f"expiry {exp!r} is not a date")
                elif asof and exp_date < asof:
                    add(c, f"expiry {exp} is before as-of {as_of}")
                right, strike = c.get("right"), c.get("strike")
                if right not in ("C", "P"):
                    add(c, f"right {right!r} is not C or P")
                if _bad_number(strike) or strike <= 0:
                    add(c, f"strike {strike!r} is not a positive number")
                key = (exp, strike, right)
                if key in seen:
                    add(c, "duplicate expiry/strike/right")
                seen.add(key)
                bid, ask = c.get("bid"), c.get("ask")
                bid_ok, ask_ok = not _bad_number(bid), not _bad_number(ask)
                if bid_ok and ask_ok and bid > ask:
                    add(c, f"crossed quote: bid {bid} > ask {ask}")
                if ask_ok and ask > 0 and (not bid_ok or bid <= 0):
                    add(c, "ask quoted with zero or missing bid")
                iv = c.get("iv")
                if iv is None:
                    add(c, "implied volatility missing")
                elif _bad_number(iv) or iv <= 0:
                    add(c, f"implied volatility {iv!r} is not a positive finite number")
    return issues
