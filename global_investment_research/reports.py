"""Mandate reports built on the monitor output. Pure functions: data in, HTML out."""
from __future__ import annotations

import pandas as pd

from . import html as H
from .income import income_row
from .monitor import MARKET_PROXIES, fmt_pct

# Qualitative theses carried over from the 9 Oct 2026 Codex research session. They are
# hypotheses to verify, shown beside today's price evidence, never as fresh findings.
PRIOR_THESES = {
    "NVDA": "Data-centre demand; watch customer capex durability and export rules.",
    "MSFT": "Cloud growth must convert AI capex into free cash flow.",
    "AVGO": "Custom accelerators + networking; verify organic growth and concentration.",
    "ANET": "AI networking demand; valuation needs cash conversion to keep up.",
    "NOW": "Enterprise workflow software; margins and dilution to improve.",
    "GOOGL": "Normalise one-off gains before judging valuation.",
    "META": "Watch margin stabilisation after heavy AI spend.",
    "AMZN": "Latest-quarter fundamentals were incomplete in the prior screen.",
    "REL": "High-margin data/analytics; verify organic growth.",
    "LSEG": "Market data + infrastructure; verify organic growth and leverage.",
    "BARC": "Value case: earnings improving, price/book below 1x; check capital and credit.",
    "BA.": "Defence: verify funded orders and backlog execution.",
    "AZN": "Growth persists; latest cash conversion weakened.",
    "RR.": "Growth story with a high valuation hurdle.",
    "GSK": "Needs an explanation for a sharp reported margin decline.",
    "A17U": "Prior first pick for 6-8% income: steady distributions; check refinancing.",
    "J69U": "Steady record, yield slightly under target.",
    "CJLU": "Rising distributions; reconcile capex funding.",
    "ME8U": "Meets target but trailing distribution fell ~6%.",
    "M44U": "High yield with a declining distribution record.",
    "N2IU": "High yield with a declining distribution record.",
}


def one_year(m) -> str:
    """1y return, marked † when the vendor history spans 51 rather than 52 weeks."""
    mark = "†" if m.get("ret_1y_bars") and m["ret_1y_bars"] < 52 else ""
    return H.pct(m["ret_1y"]) + mark


def _spark(prices: pd.DataFrame, sym: str) -> str:
    return H.sparkline(prices[sym].dropna().tolist()) if sym in prices else ""


METRIC_HEADERS = ["Symbol", "Name", "Sector", "Last", "1w", "1m", "3m", "1y", "From 1y high", "Vol (ann.)", "Trend", "1y path"]
METRIC_NUMERIC = {3, 4, 5, 6, 7, 8, 9}


def metric_rows(syms, mon, meta, prices):
    rows = []
    for s in syms:
        m = mon["metrics"].get(s)
        if not m:
            rows.append([f"<b>{H.esc(s)}</b>", H.esc(meta[s]["name"]), H.esc(meta[s]["sector"])] +
                        ['<span class="mut">no data</span>'] + [""] * 8)
            continue
        rows.append([f"<b>{H.esc(s)}</b>", H.esc(meta[s]["name"]), H.esc(meta[s]["sector"]),
                     H.num(m["last"]), H.pct(m["ret_1w"]), H.pct(m["ret_1m"]), H.pct(m["ret_3m"]), one_year(m),
                     H.pct(m["from_high"]), H.pct(m["vol"], signed=False), H.trend_chip(m["trend"]["state"]),
                     _spark(prices, s)])
    return rows


def _footer(mon, meta):
    srcs = sorted({m.get("source", "?") for m in meta.values()})
    dq = mon["data_quality"]
    return (f"Data: {H.esc(', '.join(srcs))}; weekly closes to {mon['as_of']} in each instrument's quote currency "
            f"(price return, dividends excluded unless stated). {dq['with_prices']}/{dq['instruments_configured']} instruments "
            f"have prices. Missing: {H.esc(', '.join(dq['missing']) or 'none')}. "
            + (f"Rates: {H.esc(dq['rates'])}. " if dq.get("rates") else "")
            + (f"Macro: {H.esc(dq['macro'])}. " if dq.get("macro") else "") + "Trend = price vs 10- and 40-week averages. "
            "† 1-year figure spans 51 weeks where the vendor returned 52 weekly bars. "
            "Research screen only, not investment advice; verify against issuer filings before acting.")


def monitor_report(mon, meta, prices, changes=None, standalone=True) -> str:
    tiles = []
    for mk, proxy in MARKET_PROXIES.items():
        pm = mon["markets"][mk]["proxy_metrics"]
        br = mon["markets"][mk]["breadth"]
        if pm:
            up = "n/a" if br["pct_uptrend"] is None else f"{br['pct_uptrend'] * 100:.0f}%"
            tiles.append((f"{mk} · {proxy}", H.pct(pm["ret_3m"]),
                          f"3m · 1w {fmt_pct(pm['ret_1w'])} · trend {pm['trend']['state'] or 'n/a'} · "
                          f"{up} of stocks up-trending"))
    body = H.section("Markets at a glance", H.tiles(tiles),
                     "Three-month price return of each market's ETF proxy, with stock breadth from the monitored names.")
    regime = [[H.esc(r["topic"]), f"<b>{H.esc(r['reading'])}</b>", H.esc(r["evidence"])] for r in mon["regime"]]
    body += H.section("Macro backdrop", H.table(["Topic", "Reading", "Evidence"], regime, wrap={2}),
                      "Rule-based readings from price trends. Each states the numbers it rests on.")
    if changes:
        body += H.section("What changed since the last run", H.notes(
            [f"<b>{H.esc(c['topic'])}</b>: {H.esc(c['from'])} → {H.esc(c['to'])} (since {c['since']})" for c in changes]))
    links = [r for r in mon["macro_links"] if r["full"] is not None]
    links.sort(key=lambda r: (not r["stable"], -abs(r["full"])))
    lrows = [[H.esc(r["market"]), H.esc(r["factor"]), H.num(r["full"]), H.num(r["first_half"]), H.num(r["second_half"]),
              H.chip("stable", "up") if r["stable"] else H.chip("unstable", "")] for r in links]
    body += H.section("How markets move with macro factors",
                      H.table(["Market", "Factor", "Corr (full)", "1st half", "2nd half", "Pattern"], lrows, numeric={2, 3, 4}),
                      "Correlation of weekly returns. A pattern counts as stable only if both halves of the year agree in sign "
                      "and each is at least 0.3 in size; unstable links are shown so they are not over-read.")
    sec = [[H.esc(r["sector"]), H.esc(r["symbol"]), H.pct(r["ret_1m"]), H.pct(r["ret_3m"]), H.pct(r["ret_1y"]),
            H.trend_chip(r["trend"])] for r in mon["us_sectors"]]
    body += H.section("US sector rotation", H.table(["Sector", "ETF", "1m", "3m", "1y", "Trend"], sec, numeric={2, 3, 4}),
                      "Ranked by three-month return.")
    for mk in ["US", "UK", "HK", "China", "SG"]:
        br = mon["markets"][mk]["breadth"]
        syms = [s for s, m in meta.items() if m["market"] == mk and m["kind"] in ("stock", "index_proxy")]
        lead = ", ".join(br["leaders"]) or "n/a"
        lag = ", ".join(br["laggards"]) or "n/a"
        body += H.section(f"{mk} stocks", H.table(METRIC_HEADERS, metric_rows(syms, mon, meta, prices), METRIC_NUMERIC),
                          f"3m leaders: {H.esc(lead)}. Laggards: {H.esc(lag)}. Median 3m return {fmt_pct(br['median_ret_3m'])}.")
    xs = [s for s, m in meta.items() if m["kind"] in ("commodity_proxy", "macro_proxy", "fx")]
    body += H.section("Gold, commodities, rates and FX", H.table(METRIC_HEADERS, metric_rows(xs, mon, meta, prices), METRIC_NUMERIC),
                      "Futures exposure is tracked through exchange-traded proxies (GLD, SLV, CPER, USO, UNG, DBA), which avoids "
                      "contract-roll gaps in continuous futures series. FX pairs are quoted as base/quote.")
    if mon["events"]:
        ev = [[e["date"], H.esc(e["event"]), *[H.pct(e["reactions"][m]) for m in MARKET_PROXIES]] for e in mon["events"]]
        body += H.section("Market reaction to logged macro events", H.table(["Date", "Event", *MARKET_PROXIES], ev, numeric={2, 3, 4, 5, 6}))
    return H.page("Global Markets Monitor", f"Week to {mon['as_of']}",
                  "HK, China, SG, UK and US equities with sectors, gold, commodities, rates and FX, linked to the macro backdrop.",
                  body, _footer(mon, meta), standalone)


def us_tech_report(mon, meta, prices, standalone=True) -> str:
    names = [s for s, m in meta.items() if m["group"] == "us_equity" and m["kind"] == "stock"]
    scored = [(s, mon["metrics"][s]) for s in names if s in mon["metrics"]]
    rows = []
    for s, m in sorted(scored, key=lambda x: (x[1]["momentum_score"] is None, -(x[1]["momentum_score"] or 0))):
        flags = []
        if m["trend"]["state"] == "down":
            flags.append(H.chip("down-trend", "down"))
        if m["from_high"] is not None and m["from_high"] < -0.2:
            flags.append(H.chip(f"{m['from_high']*100:.0f}% off high", "warn"))
        if m["vol"] is not None and m["vol"] > 0.5:
            flags.append(H.chip("vol > 50%", "warn"))
        rows.append([f"<b>{H.esc(s)}</b>", H.esc(meta[s]["sector"]), H.pct(m["ret_1m"]), H.pct(m["ret_3m"]), one_year(m),
                     H.num(m["momentum_score"]), H.trend_chip(m["trend"]["state"]), " ".join(flags) or H.chip("none"),
                     H.esc(PRIOR_THESES.get(s, "")), _spark(prices, s)])
    etf = [s for s in ("SPY", "QQQ", "SOXX", "IGV") if s in mon["metrics"]]
    t = [(s, H.pct(mon["metrics"][s]["ret_3m"]), f"3m · 1y {fmt_pct(mon['metrics'][s]['ret_1y'])}") for s in etf]
    body = H.section("Benchmarks", H.tiles(t), "Semiconductors (SOXX) versus software (IGV) shows where leadership sits.")
    body += H.section("Watchlist ranked by risk-adjusted momentum",
                      H.table(["Ticker", "Segment", "1m", "3m", "1y", "Momentum", "Trend", "Flags", "Prior thesis (9 Oct, to verify)", "1y path"],
                              rows, numeric={2, 3, 4, 5}, wrap={8}),
                      "Momentum = average of 3-month and 1-year return divided by annualised volatility. It ranks price "
                      "evidence only. Fundamentals are not refreshed in this run: the fundamentals feed had no credit.")
    return H.page("US Tech Watchlist", f"Week to {mon['as_of']}",
                  "Price trend, momentum and risk for large US technology names, beside the theses they were picked on.",
                  body, _footer(mon, meta), standalone)


def uk_fx_report(mon, meta, prices, standalone=True) -> str:
    uk = [s for s, m in meta.items() if m["group"] == "uk"]
    body = H.section("UK equities", H.table(METRIC_HEADERS + ["Prior thesis (9 Oct, to verify)"],
                     [r + [H.esc(PRIOR_THESES.get(s, ""))] for r, s in zip(metric_rows(uk, mon, meta, prices), uk)],
                     METRIC_NUMERIC, wrap={12}),
                     "London prices are shown in pounds (weekly closes); live exchange quotes are in pence. Returns are unaffected by the unit.")
    sectors: dict[str, list[float]] = {}
    for s in uk:
        m = mon["metrics"].get(s)
        if m and m["ret_3m"] is not None and meta[s]["kind"] == "stock":
            sectors.setdefault(meta[s]["sector"], []).append(m["ret_3m"])
    srows = sorted(((k, v) for k, v in sectors.items()), key=lambda kv: -sum(kv[1]) / len(kv[1]))
    body += H.section("UK sectors by 3-month return",
                      H.table(["Sector", "Names", "Average 3m"], [[H.esc(k), str(len(v)), H.pct(sum(v) / len(v))] for k, v in srows], numeric={1, 2}),
                      "Equal-weighted average of the monitored names in each sector; small samples, read as direction only.")
    fx = [s for s, m in meta.items() if m["kind"] == "fx"]
    body += H.section("FX", H.table(METRIC_HEADERS, metric_rows(fx, mon, meta, prices), METRIC_NUMERIC),
                      "A rising GBP/USD means sterling strengthening; for a USD-based investor in UK shares, that adds to returns.")
    return H.page("UK Equities and FX", f"Week to {mon['as_of']}", "UK large caps by sector and the currency pairs that drive their returns in other currencies.",
                  body, _footer(mon, meta), standalone)


def sg_income_report(mon, meta, prices, band=(0.06, 0.08), standalone=True) -> str:
    sg = [s for s, m in meta.items() if m["group"] == "sg" and m["kind"] == "stock"]
    as_of = pd.Timestamp(mon["as_of"])
    rows, data, keys = [], [], []
    for s in sg:
        m = mon["metrics"].get(s)
        snap = meta[s].get("snapshot") or {}
        price = snap.get("last") or (m["last"] if m else None)
        if price is None:
            rows.append([f"<b>{H.esc(s)}</b>", H.esc(meta[s]["name"])] + ['<span class="mut">no data</span>'] + [""] * 8)
            keys.append(-1.0)
            continue
        inc = income_row(s, price, as_of, meta[s].get("dividends", []), snap.get("dividend_yield_pct"), band)
        data.append(inc)
        tone = {"in target": "up", "below target": "", "above target (check sustainability)": "warn"}.get(inc["target_fit"], "")
        rows.append([f"<b>{H.esc(s)}</b>", H.esc(meta[s]["name"]), H.num(price, 3),
                     H.pct(inc["yield"], signed=False, digits=2), H.chip(inc["target_fit"], tone),
                     H.pct(inc["yield_after_cut"]["20%"], signed=False, digits=2),
                     one_year(m) if m else "", H.pct(m["from_high"]) if m else "",
                     H.trend_chip(m["trend"]["state"]) if m else "", H.esc(inc["yield_basis"]),
                     H.esc(PRIOR_THESES.get(s, ""))])
        # Rank: in-band first, then by yield descending.
        keys.append((1.0 if inc["target_fit"] == "in target" else 0.0) + (inc["yield"] or 0))
    rows = [r for _, r in sorted(zip(keys, rows), key=lambda kr: -kr[0])]
    in_band = [d["symbol"] for d in data if d["target_fit"] == "in target"]
    body = H.section("Target fit", H.tiles([
        ("Target band", f"{band[0]*100:.0f}–{band[1]*100:.0f}%", "trailing cash yield"),
        ("Names in band", str(len(in_band)), H.esc(", ".join(in_band) or "none")),
        ("Screened", str(len(data)), f"of {len(sg)} configured")]))
    body += H.section("Income screen", H.table(
        ["Ticker", "Name", "Price (SGD)", "Trailing yield", "Fit", "Yield if cut 20%", "1y price", "From 1y high", "Trend", "Yield basis", "Prior note (9 Oct)"],
        rows, numeric={2, 3, 5, 6, 7}, wrap={9, 10}),
        "Trailing yield = cash distributions with ex-date in the last 365 days ÷ latest price, cross-checked against the broker's "
        "dividend yield. Income stability is not capital stability: check the 1-year price column alongside the yield.")
    return H.page("Singapore Income Watchlist", f"Week to {mon['as_of']}",
                  f"Singapore REITs, trusts, banks and telecoms screened for a {band[0]*100:.0f}–{band[1]*100:.0f}% long-term cash yield.",
                  body, _footer(mon, meta), standalone)
