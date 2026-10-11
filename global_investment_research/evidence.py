"""Rule Evidence page: does the Decision Desk's rule set work on long weekly history?"""
from __future__ import annotations

import pandas as pd

from . import html as H

CSS = """
svg.curve{width:100%;height:auto;display:block}
svg.curve .g{stroke:var(--rule)}
svg.curve .s{fill:none;stroke:var(--accent);stroke-width:2}
svg.curve .b{fill:none;stroke:var(--muted);stroke-width:1.5;stroke-dasharray:4 3}
svg.curve text{fill:var(--muted);font:11px var(--f-data)}
.verdict{background:var(--panel);border:1px solid var(--rule);border-left:4px solid var(--accent);border-radius:6px;padding:14px 16px}
"""


def curve_svg(c, w=760, h=240) -> str:
    s, b = c["strategy"], c["benchmark"]
    lo, hi = min(s + b), max(s + b)
    n = len(s)
    px = lambda i: 50 + i * (w - 60) / (n - 1)  # noqa: E731
    py = lambda v: 10 + (hi - v) / ((hi - lo) or 1) * (h - 40)  # noqa: E731
    path = lambda xs: "M" + " L".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(xs))  # noqa: E731
    ticks = "".join(f'<line class="g" x1="50" x2="{w-10}" y1="{py(v):.1f}" y2="{py(v):.1f}"/>'
                    f'<text x="4" y="{py(v)+4:.1f}">{v:.2f}</text>' for v in (lo, (lo + hi) / 2, hi))
    d = c["dates"]
    return (f'<svg class="curve" viewBox="0 0 {w} {h}" role="img" aria-label="Growth of 1 unit: rule strategy vs equal-weight benchmark">'
            f'{ticks}<path class="b" d="{path(b)}"/><path class="s" d="{path(s)}"/>'
            f'<text x="50" y="{h-8}">{d[0]}</text><text x="{w-90}" y="{h-8}">{d[-1]}</text>'
            f'<text x="60" y="24">— rule strategy · - - equal weight</text></svg>')


def evidence_page(res: dict, standalone=True) -> str:
    v, st = res["verdict"], res["strategy"]
    bs = res["by_stance"]
    acc13, avo13 = bs["Accumulate"]["xs13"]["mean"], bs["Avoid"]["xs13"]["mean"]
    level = confidence_level(res)
    if level == "HIGH":
        headline = "The trend rules add value on this history, and the result holds in both halves."
    elif level == "MEDIUM":
        headline = "The rules point the right way and beat equal weight, but the edge is not statistically strong."
    else:
        headline = ("Following the current rules has not beaten simply holding everything equally on this history: "
                    "treat the calls as weak evidence.")
    adopted = [x["variant"] for x in res.get("variants", []) if x.get("passes_adoption_test")]
    alt_line = (f"Alternative rule passing the adoption test: {', '.join(adopted)}." if adopted else
                "No alternative rule passes the adoption test, so the live rule is unchanged.")
    t = res["t_accumulate_vs_avoid_13w"]
    body = (f'<div class="verdict"><p class="lead" style="font:600 1.1rem/1.4 var(--f-display);margin:0 0 6px">{H.esc(headline)}</p>'
            f'<p class="mut" style="margin:0">Over 13 weeks, Accumulate names beat the average instrument by {H.pct(acc13)} and Avoid names by '
            f'{H.pct(avo13)}. Accumulate minus Avoid t-statistic: {"n/a" if t is None else f"{t:.2f}"} (non-overlapping samples; ≥2 is conventionally meaningful). '
            f'{H.esc(alt_line)}</p></div>')
    body += H.section("Rule strategy vs equal weight", H.tiles([
        ("Strategy CAGR", H.pct(st["strategy"]["cagr"]), f"Benchmark {H.pct(st['benchmark']['cagr'])}"),
        ("Sharpe ratio", H.num(st["strategy"]["sharpe"]), f"Benchmark {H.num(st['benchmark']['sharpe'])}"),
        ("Max drawdown", H.pct(st["strategy"]["max_drawdown"]), f"Benchmark {H.pct(st['benchmark']['max_drawdown'])}"),
        ("Average invested", H.pct(st["avg_invested"], signed=False), "Rest held in cash at 0%"),
    ]) + curve_svg(st["curve"]),
        "Each week, hold equal weights in every instrument rated Accumulate or Hold (price above both averages), cash otherwise. "
        f"Benchmark holds every instrument equally. {st['weeks']} weeks from {st['start']} to {st['end']}.")
    rows = []
    for name in ("Accumulate", "Hold", "Watch", "Avoid"):
        b = bs[name]
        rows.append([H.chip(name, {"Accumulate": "up", "Avoid": "down", "Watch": "warn"}.get(name, "")),
                     f"{b['fwd13']['n']:,}", H.pct(b["fwd4"]["mean"]), H.pct(b["fwd13"]["mean"]), H.pct(b["fwd26"]["mean"]),
                     H.pct(b["xs13"]["mean"]), H.pct(b["xs13"]["hit"], signed=False),
                     H.pct(b["xs13_first_half"]["mean"]), H.pct(b["xs13_second_half"]["mean"])])
    body += H.section("What happened after each call", H.table(
        ["Call", "Observations", "Next 4w", "Next 13w", "Next 26w", "13w vs average", "Beat average", "1st half", "2nd half"],
        rows, numeric=set(range(1, 9))),
        f"Mean forward price return after each weekly call. '13w vs average' removes the market move. Halves split at {res['split_date']}.")
    if res.get("variants"):
        vr = []
        yn = lambda b: "—" if b is None else H.chip("yes", "up") if b else H.chip("no", "down")  # noqa: E731
        for x in res["variants"]:
            short, _, rest = x["variant"].partition(" (")
            short = short.split(":")[0]
            detail = rest.rstrip(")") if rest else x["variant"].partition(": ")[2]
            vr.append([f'<b>{H.esc(short)}</b><br><span class="mut" style="font-size:.85em">{H.esc(detail)}</span>', H.pct(x["cagr"]), H.num(x["sharpe"]), H.pct(x["max_drawdown"]),
                       H.pct(x["invested"], signed=False),
                       "—" if x.get("median_names_held") is None else f"{x['median_names_held']:.0f}",
                       yn(x["beats_benchmark_both_halves"]),
                       "—" if x.get("thirds_won") is None else f"{x['thirds_won']} of 3",
                       "—" if x.get("active_t") is None else f"{x['active_t']:.2f}",
                       yn(x.get("passes_adoption_test"))])
        body += H.section("Alternative rules on the same history", H.table(
            ["Rule", "CAGR", "Sharpe", "Max fall", "Invested", "Names held", "Both halves",
             "Thirds", "Active t", "Adopt"], vr, numeric={1, 2, 3, 4, 5, 7, 8}),
            "Adoption test: a better Sharpe ratio than always-invested equal weight in both halves and in all three thirds, "
            "and an active return (rule minus benchmark) with a t-statistic of at least 2. Winning one split by a hair, "
            "or by holding a handful of names, is not enough.")
    yrs = [x for x in res.get("variants", []) if x.get("years")]
    if yrs:
        bench = next(x for x in yrs if x["variant"].startswith("Benchmark"))
        cols = [bench] + [x for x in yrs if x is not bench]
        label = lambda x: x["variant"].split(" (")[0].split(":")[0]  # noqa: E731
        yr_rows = []
        for y in sorted(bench["years"]):
            b = bench["years"][y]
            yr_rows.append([f"<b>{y}</b>" + (" " + H.chip("falling market", "down") if b <= -0.10 else "")]
                           + [H.pct(x["years"].get(y)) for x in cols])
        body += H.section("Year by year", H.table(["Year"] + [H.esc(label(x)) for x in cols], yr_rows,
                                                   numeric=set(range(1, len(cols) + 1))),
                          "Compounded return of each rule in each calendar year. Years where the equal-weight benchmark lost 10% or "
                          "more are marked: a trend rule earns its keep there, by stepping aside. Partial first and last years.")
    body += H.section("How to use this", H.notes([
        "Supported rules earn more trust. If a stance fails in either half, size it down and lean harder on the checklist.",
        "The weekly run re-tests these rules and logs any deterioration in the Improvement Log.",
    ]))
    body += H.section("Limits of this test", H.notes([
        "Survivorship: today's instrument list is used for the whole period; past losers that left the list are missing.",
        "Price returns in local currency, weekly closes, no trading costs, taxes or dividends.",
        "The macro stress overlay (VIX risk budget) is not applied historically.",
        "Instruments join the test when their history starts (for example XLC in 2018); each week the rule and the "
        "benchmark use the same set of instruments.",
        f"{len(res['instruments'])} instruments, {res['observations']:,} weekly observations from {res['first_date']}: still a small sample for a market regime test.",
    ] + [f"Bad print excluded: {f['symbol']} {f['date']} closed {f['close']:g} between {f['previous']:g} and {f['next']:g} "
         "(a unit error at the source; the raw file is kept as returned)." for f in res.get("data_fixes", [])]))
    span = round((pd.Timestamp(res["last_date"]) - pd.Timestamp(res["first_date"])).days / 365.25)
    page = H.page("Rule Evidence", f"Backtest {res['first_date']} to {res['last_date']}",
                  f"Walk-forward test of the Decision Desk rules on {span} years of weekly prices, using only data available at each date.",
                  body, "Interactive Brokers weekly closes. Research evidence, not a promise of future results.", standalone)
    return page.replace(f"<style>{H.CSS}</style>", f"<style>{H.CSS}{CSS}</style>", 1)


def confidence_level(res: dict) -> str:
    """HIGH needs calls that rank well in both halves, significantly, AND a rule portfolio that beats equal weight
    risk-adjusted; MEDIUM drops the significance requirement; anything else is LOW."""
    v = res["verdict"]
    beats = v.get("strategy_beats_benchmark_risk_adjusted", False)
    if v["supported_both_halves"] and v["significant"] and beats:
        return "HIGH"
    return "MEDIUM" if v["supported_both_halves"] and beats else "LOW"


def confidence_note(res: dict) -> str:
    """One-paragraph rule-confidence statement for the Desk and Brief, from the latest backtest."""
    st = res["strategy"]
    bs = res["by_stance"]
    level = confidence_level(res)
    return (f"Rule confidence: {level}. On {len(res['instruments'])} instruments from {res['first_date']} to {res['last_date']}, "
            f"Accumulate calls returned {bs['Accumulate']['xs13']['mean']*100:+.1f}% versus the average instrument over the next "
            f"13 weeks and Avoid calls {bs['Avoid']['xs13']['mean']*100:+.1f}%. Following the calls returned "
            f"{st['strategy']['cagr']*100:.1f}% a year against {st['benchmark']['cagr']*100:.1f}% for holding everything equally"
            f" (worst fall {st['strategy']['max_drawdown']*100:.0f}% vs {st['benchmark']['max_drawdown']*100:.0f}%). "
            + ("Treat Accumulate and Avoid as risk-control levels, not as return forecasts; keep a diversified core and size new "
               "positions at half the Max size until a rule passes the test." if level == "LOW" else
               "Use the calls with normal sizing, and keep the checklist."))
