"""Speculative options sleeve: defined-risk ideas priced from real chains.

Scope rules (HANDBOOK §10):
- Only long calls/puts and debit spreads: the maximum loss is the premium paid.
- The sleeve is capped at SLEEVE_CAP of the portfolio; each trade at PER_TRADE_RISK.
- Direction comes from the Decision Desk: Accumulate -> bullish, Avoid -> bearish.
- Probabilities use the market's own implied volatility (risk-neutral, lognormal).
  They describe what the price already assumes, not an edge.
"""
from __future__ import annotations

import math
from datetime import date

SLEEVE_CAP = 0.05          # total premium at risk across all option trades
PER_TRADE_RISK = 0.01      # premium at risk per trade
RISK_FREE = 0.04           # annual rate for pricing and probabilities (assumption)
MAX_SPREAD_PCT = 0.25      # skip quotes whose bid-ask spread exceeds 25% of mid
MIN_OPEN_INTEREST = 100    # flag thin contracts
TAKE_PROFIT = 1.0          # close at +100% on premium (long options)
STOP_LOSS = 0.5            # close at -50% of premium
TIME_STOP_DAYS = 21        # close or roll with 21 days left
MULTIPLIER = 100


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(S, K, T, sigma, right="C", r=RISK_FREE):
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if right == "C" else max(0.0, K - S)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if right == "C":
        return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def prob_above(S, level, T, sigma, r=RISK_FREE):
    """Risk-neutral probability that the price at expiry ends above `level`."""
    if T <= 0 or sigma <= 0:
        return 1.0 if S > level else 0.0
    d2 = (math.log(S / level) + (r - 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return _ncdf(d2)


def quote_mid(c: dict) -> tuple[float | None, str]:
    """Mid price and its basis; refuses wide or empty markets."""
    bid, ask, last = c.get("bid"), c.get("ask"), c.get("last")
    if bid and ask and ask >= bid > 0:
        mid = (bid + ask) / 2
        if (ask - bid) / mid > MAX_SPREAD_PCT:
            return None, f"spread {100*(ask-bid)/mid:.0f}% of mid: too wide"
        return mid, "bid/ask mid"
    if last and last > 0:
        return last, "last trade (no two-sided quote)"
    return None, "no price"


def normalise_iv(iv):
    if iv is None:
        return None
    return iv / 100 if iv > 3 else iv  # accept percent or fraction


def payoff(legs, S_T):
    """Value at expiry per share. legs: list of (qty, right, strike)."""
    v = 0.0
    for q, right, K in legs:
        v += q * (max(0.0, S_T - K) if right == "C" else max(0.0, K - S_T))
    return v


def analyse(structure: str, legs, debit, S, T, sigma) -> dict:
    """Breakeven, max loss/gain, risk-neutral probability of profit and scenario returns."""
    rights = {r for _, r, _ in legs}
    strikes = sorted(K for _, _, K in legs)
    bullish = rights == {"C"}
    if bullish:
        be = strikes[0] + debit
        pop = prob_above(S, be, T, sigma)
    else:
        be = strikes[-1] - debit
        pop = 1 - prob_above(S, be, T, sigma)
    width = strikes[-1] - strikes[0] if len(legs) == 2 else None
    max_gain = None if width is None else width - debit
    scen = {}
    for mv in (-0.2, -0.1, 0.0, 0.1, 0.2):
        scen[f"{int(mv*100):+d}%"] = payoff(legs, S * (1 + mv)) / debit - 1
    return {"structure": structure, "breakeven": be, "breakeven_move": be / S - 1, "max_loss": debit,
            "max_gain": max_gain, "reward_risk": None if max_gain is None else max_gain / debit,
            "prob_profit": pop, "scenarios": scen}


def _nearest(contracts, right, target):
    pool = [c for c in contracts if c["right"] == right and c.get("strike")]
    return min(pool, key=lambda c: abs(c["strike"] - target)) if pool else None


def build_ideas(chains: dict, dec: dict, as_of: str, portfolio: float = 1_000_000) -> dict:
    """chains: parsed options.json. Direction from the Decision Desk stance of each underlying."""
    stance = {c["symbol"]: c["stance"] for c in dec["calls"]}
    ideas, skipped = [], []
    asof = date.fromisoformat(as_of)
    for u in chains["underlyings"]:
        sym, S = u["symbol"], u.get("last")
        st = stance.get(sym, "Outside universe")
        if not S or not u.get("expiries"):
            skipped.append({"symbol": sym, "reason": "no underlying price or chain"})
            continue
        direction = "bull" if st in ("Accumulate",) else "bear" if st in ("Avoid",) else None
        if sym in ("FXI", "KWEB"):  # China/HK proxies: direction from the China and HK market calls
            hk = [c for c in dec["calls"] if c["symbol"] in ("2800", "3188")]
            direction = "bear" if hk and all(c["stance"] == "Avoid" for c in hk) else None
            st = "China/HK proxy: " + ", ".join(f"{c['symbol']} {c['stance']}" for c in hk)
        if direction is None:
            skipped.append({"symbol": sym, "reason": f"Decision Desk stance {st}: no directional edge"})
            continue
        exp = min(u["expiries"], key=lambda e: abs((date.fromisoformat(e["expiry"]) - asof).days - 70))
        T = (date.fromisoformat(exp["expiry"]) - asof).days / 365
        right = "C" if direction == "bull" else "P"
        sgn = 1 if direction == "bull" else -1
        atm = _nearest(exp["contracts"], right, S)
        otm = _nearest(exp["contracts"], right, S * (1 + sgn * 0.10))
        single = _nearest(exp["contracts"], right, S * (1 + sgn * 0.05))
        cands = []
        if atm and otm and atm["strike"] != otm["strike"]:
            cands.append(("Bull call spread" if direction == "bull" else "Bear put spread",
                          [(1, right, atm["strike"]), (-1, right, otm["strike"])], [atm, otm], [1, -1]))
        if single:
            cands.append(("Long call" if direction == "bull" else "Long put", [(1, right, single["strike"])], [single], [1]))
        for name, legs, cs, qtys in cands:
            mids, bases = [], []
            for c in cs:
                m, b = quote_mid(c)
                mids.append(m)
                bases.append(b)
            if None in mids:
                skipped.append({"symbol": sym, "reason": f"{name}: {'; '.join(bases)}"})
                continue
            debit = sum(q * m for q, m in zip(qtys, mids))
            if debit <= 0:
                skipped.append({"symbol": sym, "reason": f"{name}: non-positive debit from quotes"})
                continue
            iv = normalise_iv(cs[0].get("iv"))
            iv_basis = "contract IV"
            if iv is None:
                skipped.append({"symbol": sym, "reason": f"{name}: no implied volatility"})
                continue
            a = analyse(name, legs, debit, S, T, iv)
            per_trade = PER_TRADE_RISK * portfolio
            contracts = int(per_trade // (debit * MULTIPLIER))
            thin = [c["strike"] for c in cs if (c.get("open_interest") or 0) < MIN_OPEN_INTEREST]
            ideas.append({
                "symbol": sym, "direction": direction, "stance": st, "expiry": exp["expiry"], "days": round(T * 365),
                "underlying": S, "legs": [{"qty": q, "right": r, "strike": k} for q, r, k in legs],
                "debit": debit, "debit_per_contract": debit * MULTIPLIER, "iv": iv, "iv_basis": iv_basis,
                "price_basis": "; ".join(sorted(set(bases))), "contracts_at_1pct": contracts,
                "premium_at_risk": contracts * debit * MULTIPLIER, "thin_strikes": thin,
                "model_check": sum(q * bs_price(S, k, T, iv, r) for q, r, k in legs), **a,
            })
    # Rank on a like-for-like test: return on premium if the underlying makes a one-standard-deviation
    # move (from its own implied volatility) in the trade's favour, weighted by the probability of profit.
    for i in ideas:
        sgn = 1 if i["direction"] == "bull" else -1
        T = i["days"] / 365
        s1 = i["underlying"] * math.exp(sgn * i["iv"] * math.sqrt(T))
        legs = [(l["qty"], l["right"], l["strike"]) for l in i["legs"]]
        i["one_sd_move"] = s1 / i["underlying"] - 1
        i["return_at_1sd"] = payoff(legs, s1) / i["debit"] - 1
        i["score"] = i["prob_profit"] * max(i["return_at_1sd"], 0) - (1 - i["prob_profit"])
    ideas.sort(key=lambda i: -i["score"])
    return {"as_of": as_of, "ideas": ideas, "skipped": skipped,
            "policy": {"sleeve_cap": SLEEVE_CAP, "per_trade": PER_TRADE_RISK, "risk_free": RISK_FREE,
                       "take_profit": TAKE_PROFIT, "stop_loss": STOP_LOSS, "time_stop_days": TIME_STOP_DAYS}}
