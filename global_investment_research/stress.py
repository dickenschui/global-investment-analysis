"""Historical stress test: what this week's book would have done in named past crises.

The shocks are measured, not assumed. Each episode is a dated window in our own weekly IBKR history
(data/history/weekly_long), every instrument's move is its own close-to-close price return over that
window, and foreign moves are converted to the owner's base currency (GBP, config/investor.json) with the
FX pairs in the same history. Nothing is filled or interpolated: an instrument or FX pair without a close
near both dates gives None, with the reason recorded.

Scope: price returns only (dividends excluded, so income names look slightly worse than they were),
weekly closes (an intra-week low is not seen), current weights held unchanged through the window
(no stops, no rebalancing). A past episode is one draw of history, not a forecast or a worst case.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from .data import REPO_ROOT

DEFAULT_MANDATE = REPO_ROOT / "config" / "investor.json"

# Start = the week of the US equity high before the sell-off; end = the week of the closing low. Weekly closes are
# labelled with their week-ending Friday, so a date resolves to the close on or just before it.
EPISODES: tuple[dict, ...] = (
    {"name": "Global financial crisis", "start": "2007-10-12", "end": "2009-03-06",
     "why": "S&P 500 closing high (9-11 Oct 2007) to the bear-market low week (6-9 Mar 2009): the full credit "
            "crisis peak to trough. Tests equity beta and banks; GBP fell about 30% against USD, which cushions a "
            "GBP investor's foreign holdings."},
    {"name": "Euro crisis", "start": "2011-04-29", "end": "2011-10-03",
     "why": "S&P 500 high of 29 Apr 2011 to the 3 Oct 2011 low: euro-area sovereign stress plus the US downgrade "
            "in August. 3 Oct 2011 is a Monday, so the weekly close used is Friday 30 Sep 2011."},
    {"name": "China/oil", "start": "2015-05-22", "end": "2016-02-12",
     "why": "S&P 500 high (21 May 2015) to the 11 Feb 2016 low: China A-share crash, the August 2015 renminbi "
            "devaluation and oil near USD 26. Tests HK/China, energy and Singapore income."},
    {"name": "Covid crash", "start": "2020-02-14", "end": "2020-03-20",
     "why": "Week of the 19 Feb 2020 high to the last weekly close before the 23 Mar 2020 low: the fastest crash in "
            "the history. Weekly data cannot see the Monday low itself."},
    {"name": "2022 rates shock", "start": "2022-01-07", "end": "2022-10-14",
     "why": "Week after the 3 Jan 2022 high to the week of the 12 Oct 2022 low: inflation and rate rises took "
            "shares and bonds down together and de-rated growth/tech. GBP hit a record low in September 2022."},
)

# Broad market fund that stands in for a name with no history in a window (only when proxies=True).
MARKET_PROXY = {"US": "SPY", "UK": "ISF", "SG": "ES3", "HK": "2800", "China": "3188"}

# Trading currency by venue, used only where the IBKR feed returned no currency (HK and China rows).
# IBKR's Stock Connect contracts (SEHKNTL Shanghai, SEHKSZSE/CHINEXT Shenzhen) are settled in offshore CNH.
EXCHANGE_CURRENCY = {"SEHK": "HKD", "SEHKNTL": "CNH", "SEHKSZSE": "CNH", "CHINEXT": "CNH"}


def load_mandate(path: str | Path = DEFAULT_MANDATE) -> dict:
    """Base currency, drawdown tolerance and SG yield band from the owner's mandate file."""
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    tol = cfg["drawdown_tolerance"]
    if not (isinstance(tol, (int, float)) and 0 < tol < 1):
        raise ValueError("drawdown_tolerance must be a fraction between 0 and 1")
    return {"base_currency": cfg["base_currency"], "drawdown_tolerance": float(tol),
            "sg_yield_band": cfg.get("sleeves", {}).get("sg_income", {}).get("yield_band")}


def _num(v) -> float | None:
    """Python float, or None for missing / non-finite."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _none_frame(index, columns) -> pd.DataFrame:
    """Object frame of None, so a missing cell reads as None rather than NaN."""
    return pd.DataFrame([[None] * len(columns) for _ in range(len(index))], index=index, columns=columns, dtype=object)


def _close_at(s: pd.Series, when: pd.Timestamp, max_gap_days: int) -> tuple[pd.Timestamp | None, float | None, str]:
    s = s.dropna()
    s = s[s.index <= when]
    if s.empty:
        return None, None, f"no close on or before {when.date().isoformat()}"
    d = s.index[-1]
    if (when - d).days > max_gap_days:
        return None, None, (f"last close before {when.date().isoformat()} is {d.date().isoformat()}, "
                            f"more than {max_gap_days} days earlier")
    return d, float(s.iloc[-1]), ""


def episode_detail(prices: pd.DataFrame, episodes=EPISODES, max_gap_days: int = 7) -> list[dict]:
    """Per instrument and episode: the closes and dates used, the price return, or None and why.
    A close counts for a date only if it is on it or at most `max_gap_days` before it (one weekly bar)."""
    rows = []
    for ep in episodes:
        start, end = pd.Timestamp(ep["start"]), pd.Timestamp(ep["end"])
        if end <= start:
            raise ValueError(f"{ep['name']}: end must be after start")
        for sym in prices.columns:
            d0, p0, why0 = _close_at(prices[sym], start, max_gap_days)
            d1, p1, why1 = _close_at(prices[sym], end, max_gap_days)
            ok = p0 is not None and p1 is not None and p0 > 0
            rows.append({"instrument": sym, "episode": ep["name"],
                         "start_used": d0.date().isoformat() if d0 is not None else None, "start_close": p0,
                         "end_used": d1.date().isoformat() if d1 is not None else None, "end_close": p1,
                         "return": p1 / p0 - 1 if ok else None,
                         "reason": "" if ok else "; ".join(x for x in (why0, why1) if x) or "non-positive start close"})
    return rows


def episode_returns(prices: pd.DataFrame, episodes=EPISODES, max_gap_days: int = 7) -> pd.DataFrame:
    """Instrument x episode local-currency price return; None where either close is missing (never filled)."""
    names = [ep["name"] for ep in episodes]
    out = _none_frame(list(prices.columns), names)
    for r in episode_detail(prices, episodes, max_gap_days):
        out.at[r["instrument"], r["episode"]] = r["return"]
    return out


def value_in(ccy: str, base: str, quotes: dict) -> tuple[float | None, str]:
    """Units of `base` per unit of `ccy` from FX pairs named 'A.B' (= B per A), directly or via USD.
    `quotes` maps pair -> level, or pair -> gross return (1 + r): the algebra is the same, so a cross's gross
    return is exactly the product of its legs' gross returns. Returns (value, route) or (None, reason)."""
    if ccy == base:
        return 1.0, "same currency"

    def direct(a, b):
        q = _num(quotes.get(f"{a}.{b}"))
        if q is not None and q > 0:
            return q, f"{a}.{b}"
        q = _num(quotes.get(f"{b}.{a}"))
        if q is not None and q > 0:
            return 1 / q, f"1/{b}.{a}"
        return None, None

    v, route = direct(ccy, base)
    if v is not None:
        return v, route
    if "USD" in (ccy, base):
        return None, f"no {ccy}/{base} FX pair in the history for this window"
    a, ra = direct(ccy, "USD")
    b, rb = direct("USD", base)
    if a is None or b is None:
        missing = [p for p, r in ((f"{ccy}/USD", ra), (f"USD/{base}", rb)) if r is None]
        return None, f"no {' or '.join(missing)} FX pair in the history for this window"
    return a * b, f"{ra} x {rb}"


def currencies(meta: dict) -> tuple[dict, list[dict]]:
    """Quote currency per instrument from the loader's meta; where IBKR returned none, the venue's trading
    currency (EXCHANGE_CURRENCY), recorded in the notes. Unknown stays None."""
    out, notes = {}, []
    for sym, m in meta.items():
        c = m.get("currency")
        if c:
            out[sym] = str(c).upper()
            continue
        c = EXCHANGE_CURRENCY.get(m.get("exchange") or "")
        out[sym] = c
        notes.append({"instrument": sym, "reason": f"feed gave no currency; {c} taken from venue {m.get('exchange')}"
                      if c else f"feed gave no currency and venue {m.get('exchange')} is not mapped"})
    return out, notes


def to_base_returns(local_returns: pd.DataFrame, fx_returns: pd.DataFrame, currency_of: dict,
                    base: str = "GBP") -> tuple[pd.DataFrame, list[dict]]:
    """Convert local price returns to base-currency returns: (1 + r_local) x (1 + r_fx) - 1, where r_fx is the
    episode change in the base value of one unit of the local currency, built from the FX rows of `fx_returns`
    (an episode_returns frame containing pairs such as GBP.USD, USD.SGD, EUR.GBP).
    Returns (frame, notes); a cell is None with a note when its currency or FX pair is missing."""
    out = _none_frame(local_returns.index, local_returns.columns)
    notes = []
    for ep in local_returns.columns:
        gross = {p: (1 + v) for p, v in ((p, _num(fx_returns.at[p, ep])) for p in fx_returns.index
                                         if "." in str(p)) if v is not None}
        for sym in local_returns.index:
            r = _num(local_returns.at[sym, ep])
            if r is None:
                continue
            ccy = currency_of.get(sym)
            if not ccy:
                notes.append({"instrument": sym, "episode": ep, "reason": "quote currency unknown"})
                continue
            g, route = value_in(ccy, base, gross)
            if g is None:
                notes.append({"instrument": sym, "episode": ep, "reason": route})
                continue
            out.at[sym, ep] = (1 + r) * g - 1
    return out, notes


def clean_history(prices: pd.DataFrame, exceptions: list[dict] | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """The backtest's own source-error screens (documented exceptions, quote-unit breaks, isolated bad prints),
    so a stress never measures a data error. A blanked close is skipped: the lookup then takes the previous
    weekly close only if it is within max_gap_days, otherwise the cell is None."""
    from .backtest import apply_exceptions, drop_bad_prints, drop_before_level_breaks
    prices, excluded = apply_exceptions(prices, exceptions)
    prices, breaks = drop_before_level_breaks(prices)
    prices, fixes = drop_bad_prints(prices)
    return prices, excluded + breaks + fixes


def base_episode_returns(prices: pd.DataFrame, meta: dict, episodes=EPISODES, base: str = "GBP",
                         exceptions: list[dict] | None = None) -> dict:
    """Everything a caller needs in one step: cleaned history -> local returns -> base returns, with notes."""
    clean, fixes = clean_history(prices, exceptions)
    local = episode_returns(clean, episodes)
    ccy, ccy_notes = currencies(meta)
    based, fx_notes = to_base_returns(local, local, ccy, base)
    return {"local": local, "base": based, "currency_of": ccy, "base_currency": base,
            "notes": {"data_fixes": fixes, "currency": ccy_notes, "fx": fx_notes},
            "episodes": [dict(ep) for ep in episodes]}


def portfolio_stress(weights: dict, base_returns: pd.DataFrame, proxies: bool = False, *,
                     market_of: dict | None = None, tolerance: float | None = None) -> list[dict]:
    """Base-currency return of a long-only book in each episode, weights held fixed; cash (1 - sum of weights)
    earns 0. A name with no return in a window uses its market proxy only when `proxies` is True, and is listed.
    `loss` is the signed portfolio return (negative = loss). The tolerance verdict is given only when every
    weight is covered; otherwise the uncovered weight could move either way and the verdict is None."""
    if tolerance is None:
        tolerance = load_mandate()["drawdown_tolerance"]
    if proxies and market_of is None:
        raise ValueError("proxies=True needs market_of (instrument -> market)")
    w = {s: _num(v) for s, v in weights.items()}
    if any(v is None or v < 0 for v in w.values()):
        raise ValueError("weights must be finite and non-negative (long-only)")
    gross = math.fsum(w.values())
    if gross > 1 + 1e-9:
        raise ValueError(f"weights sum to {gross:.4f} > 1: leverage is not modelled")
    out = []
    for ep in base_returns.columns:
        def ret(sym):
            return _num(base_returns.at[sym, ep]) if sym in base_returns.index else None
        contrib, proxied, missing = {}, [], []
        for sym, wt in w.items():
            if wt == 0:
                continue
            r = ret(sym)
            if r is None and proxies:
                px = MARKET_PROXY.get((market_of or {}).get(sym))
                if px and px != sym and ret(px) is not None:
                    proxied.append({"instrument": sym, "proxy": px, "weight": wt})
                    r = ret(px)
            if r is None:
                missing.append({"instrument": sym, "weight": wt,
                                "reason": "no base-currency return in this window"
                                + ("" if proxies else " (proxies off)")})
                continue
            contrib[sym] = wt * r
        covered = math.fsum(wt for s, wt in w.items() if s in contrib)
        missing_w = math.fsum(m["weight"] for m in missing)
        loss = math.fsum(contrib.values())
        complete = not missing
        out.append({"episode": ep, "loss": loss, "covered_weight": covered, "cash_weight": 1 - gross,
                    "missing_weight": missing_w, "complete": complete, "proxied": proxied, "missing": missing,
                    "contributions": contrib, "tolerance": tolerance,
                    "breaches_tolerance": (loss < -tolerance) if complete else None})
    return out


def weights_from_calls(calls: list[dict], scale: float = 1.0, stances=("Accumulate", "Hold")) -> dict:
    """Desk sizes for the chosen stances, times `scale` (1.0 full, 0.5 half)."""
    return {c["symbol"]: c["size"] * scale for c in calls
            if c.get("stance") in stances and _num(c.get("size")) is not None and c["size"] > 0}


def desk_check(calls: list[dict], base_returns: pd.DataFrame, market_of: dict, scales=(1.0, 0.5),
               proxies: bool = True, tolerance: float | None = None) -> list[dict]:
    """Episode results for this week's Accumulate+Hold sizes at each scale, against the mandate tolerance."""
    return [{"scale": s, "weights": (wts := weights_from_calls(calls, s)),
             "results": portfolio_stress(wts, base_returns, proxies, market_of=market_of, tolerance=tolerance)}
            for s in scales]


# --- Singapore income sleeve ---------------------------------------------------------------------------------

def sg_income_stress(principal_base: float, weight: float, yield_: float, payout_cut: float,
                     price_change: float, fx_change: float) -> dict:
    """Base-currency capital and annual income of the SG sleeve before and after a shock.
    fx_change is the change in the base value of one SGD (not the quoted pair). Income after = capital x
    yield x (1 - payout_cut) x (1 + fx_change), as the reference income_stress; no reinvestment or tax."""
    vals = {"principal_base": principal_base, "weight": weight, "yield_": yield_, "payout_cut": payout_cut,
            "price_change": price_change, "fx_change": fx_change}
    for k, v in vals.items():
        if _num(v) is None:
            raise ValueError(f"{k} must be a finite number")
    if principal_base <= 0:
        raise ValueError("principal_base must be positive")
    for k in ("weight", "yield_", "payout_cut"):
        if not 0 <= vals[k] <= 1:
            raise ValueError(f"{k} must be between 0 and 1")
    for k in ("price_change", "fx_change"):
        if vals[k] < -1:
            raise ValueError(f"{k} cannot be below -100%")
    capital = principal_base * weight
    income_before = capital * yield_
    income_after = capital * yield_ * (1 - payout_cut) * (1 + fx_change)
    capital_after = capital * (1 + price_change) * (1 + fx_change)
    return {"capital_before": capital, "capital_after": capital_after, "capital_change": capital_after - capital,
            "income_before": income_before, "income_after": income_after,
            "income_change": income_after - income_before,
            "income_yield_on_original": income_after / capital if capital else None,
            "portfolio_capital_change": (capital_after - capital) / principal_base,
            "assumptions": vals,
            "scope": "Annual run-rate income after the cut, converted at the post-shock FX; capital is price "
                     "and FX only. Not a forecast of payouts."}


def sg_shocks(local_returns: pd.DataFrame, base: str = "GBP", proxy: str = "ES3") -> list[dict]:
    """Measured SG shocks per episode: the proxy's local price change and the change in the base value of one
    SGD, both from the same history. None with a reason where the proxy or FX history does not cover it."""
    out = []
    for ep in local_returns.columns:
        price = _num(local_returns.at[proxy, ep]) if proxy in local_returns.index else None
        gross = {p: 1 + v for p, v in ((p, _num(local_returns.at[p, ep])) for p in local_returns.index
                                       if "." in str(p)) if v is not None}
        g, route = value_in("SGD", base, gross)
        reasons = ([f"{proxy} has no history across this window"] if price is None else []) + \
                  ([route] if g is None else [])
        out.append({"episode": ep, "price_change": price, "fx_change": None if g is None else g - 1,
                    "fx_route": route if g is not None else None, "reason": "; ".join(reasons)})
    return out


def units_per_base(prices: pd.DataFrame, currency: str, base: str = "GBP") -> tuple[float | None, str | None]:
    """Latest units of `currency` per one unit of `base` (e.g. SGD per GBP = GBP.USD x USD.SGD), using the
    last date on which every needed pair has a close. Returns (rate, date) or (None, None)."""
    fx = [c for c in prices.columns if "." in str(c) and len(str(c)) == 7]
    for d in reversed(prices.index):
        row = {p: prices.at[d, p] for p in fx}
        v, _ = value_in(base, currency, row)
        if v is not None:
            return v, d.date().isoformat()
    return None, None


def sg_basket(calls: list[dict], sgd_per_gbp: float, stances=None, band=None) -> dict:
    """Equal-weight basket of SG names with a measured sustainable yield (Desk income rows: yield_sustainable,
    the lower of trailing and four-year average). Names without one are excluded and listed, never assumed."""
    if _num(sgd_per_gbp) is None or sgd_per_gbp <= 0:
        raise ValueError("sgd_per_gbp must be a positive number")
    if band is None:
        band = load_mandate().get("sg_yield_band")
    members, excluded = [], []
    for c in calls:
        if c.get("market") != "SG" or (stances is not None and c.get("stance") not in stances):
            continue
        inc = c.get("income") or {}
        y, price = _num(inc.get("yield_sustainable")), _num(inc.get("price"))
        if y is None or price is None or price <= 0:
            excluded.append({"symbol": c["symbol"], "stance": c.get("stance"),
                             "reason": "no measured sustainable yield" if y is None else "no price"})
            continue
        members.append({"symbol": c["symbol"], "stance": c.get("stance"), "yield_sustainable": y,
                        "price_sgd": price, "cash_sgd": y * price, "price_gbp": price / sgd_per_gbp,
                        "cash_gbp": y * price / sgd_per_gbp,
                        "in_band": bool(band) and band[0] <= y <= band[1]})
    basket = math.fsum(m["yield_sustainable"] for m in members) / len(members) if members else None
    return {"members": members, "excluded": excluded, "basket_yield": basket, "sgd_per_gbp": sgd_per_gbp,
            "yield_band": band, "in_band_count": sum(m["in_band"] for m in members),
            "basis": "Equal weights; each name's measured sustainable yield. Not a forecast."}


# --- Weekly report summary ------------------------------------------------------------------------------------

SG_CUTS = (0.0, 0.2)


def summary(calls: list[dict], prices: pd.DataFrame, meta: dict, exceptions: list[dict] | None = None) -> dict:
    """Compact, JSON-ready stress block for decisions.json: this week's Accumulate+Hold book at full and half size
    through each episode in GBP, and the SG income sleeve under the measured ES3 and SGD shocks. SG changes are
    relative to the sleeve itself (no sleeve weight is assumed)."""
    mandate = load_mandate()
    eb = base_episode_returns(prices, meta, base=mandate["base_currency"], exceptions=exceptions)
    market = {s: m.get("market") for s, m in meta.items()}
    blocks = desk_check(calls, eb["base"], market, scales=(1.0, 0.5), proxies=True)
    why = {e["name"]: e for e in EPISODES}
    rows = []
    for i, ep in enumerate(eb["base"].columns):
        full, half = blocks[0]["results"][i], blocks[1]["results"][i]
        rows.append({"episode": ep, "start": why[ep]["start"], "end": why[ep]["end"], "why": why[ep]["why"],
                     "full": full["loss"], "half": half["loss"], "complete": full["complete"],
                     "breach_full": full["breaches_tolerance"], "breach_half": half["breaches_tolerance"],
                     "proxied": sorted(p["instrument"] for p in full["proxied"]),
                     "missing": sorted(m["instrument"] for m in full["missing"]),
                     "missing_weight": full["missing_weight"]})
    out = {"base_currency": mandate["base_currency"], "tolerance": mandate["drawdown_tolerance"],
           "gross_full": math.fsum(blocks[0]["weights"].values()), "episodes": rows, "sg_income": None,
           "data_fixes": len(eb["notes"]["data_fixes"]),
           "limits": "Weekly closes, price only (no dividends), weights held fixed with no exits, cash at 0%. Names "
                     "without history use their market fund where listed; a window with uncovered weight gets no verdict."}
    rate, rate_date = units_per_base(prices, "SGD", mandate["base_currency"])
    if rate:
        basket = sg_basket(calls, rate)
        if basket["basket_yield"] is not None:
            sg = []
            for s in sg_shocks(eb["local"], mandate["base_currency"]):
                if s["price_change"] is None or s["fx_change"] is None:
                    sg.append({"episode": s["episode"], "reason": s["reason"]})
                    continue
                for cut in SG_CUTS:
                    r = sg_income_stress(1.0, 1.0, basket["basket_yield"], cut, s["price_change"], s["fx_change"])
                    sg.append({"episode": s["episode"], "payout_cut": cut, "price_change": s["price_change"],
                               "fx_change": s["fx_change"], "income_change": r["income_change"] / r["income_before"],
                               "capital_change": r["capital_change"] / r["capital_before"]})
            out["sg_income"] = {"basket_yield": basket["basket_yield"], "names": len(basket["members"]),
                                "in_band": basket["in_band_count"], "sgd_per_gbp": rate, "rate_date": rate_date,
                                "rows": sg}
    return out
