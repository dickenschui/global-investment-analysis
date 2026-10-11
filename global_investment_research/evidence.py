"""Rule Evidence page: does the Decision Desk's rule set work on 5 years of history?"""
from __future__ import annotations

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
            f'<text x="{w-200}" y="20">— rule strategy   - - equal weight</text></svg>')


def evidence_page(res: dict, standalone=True) -> str:
    v, st = res["verdict"], res["strategy"]
    bs = res["by_stance"]
    acc13, avo13 = bs["Accumulate"]["xs13"]["mean"], bs["Avoid"]["xs13"]["mean"]
    if v["supported_both_halves"] and v["significant"]:
        headline = "The trend rules add value on this history, and the result holds in both halves."
    elif v["supported_both_halves"]:
        headline = "The rules point the right way in both halves, but the edge is not statistically strong."
    else:
        headline = "The rules are not consistently supported on this history: treat the calls as weak evidence."
    t = res["t_accumulate_vs_avoid_13w"]
    body = (f'<div class="verdict"><p class="lead" style="font:600 1.1rem/1.4 var(--f-display);margin:0 0 6px">{H.esc(headline)}</p>'
            f'<p class="mut" style="margin:0">Over 13 weeks, Accumulate names beat the average instrument by {H.pct(acc13)} and Avoid names by '
            f'{H.pct(avo13)}. Accumulate minus Avoid t-statistic: {"n/a" if t is None else f"{t:.2f}"} (non-overlapping samples; ≥2 is conventionally meaningful).</p></div>')
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
        for x in res["variants"]:
            both = x["beats_benchmark_both_halves"]
            vr.append([H.esc(x["variant"]), H.pct(x["cagr"]), H.num(x["sharpe"]), H.pct(x["max_drawdown"]),
                       H.pct(x["invested"], signed=False), H.num(x["sharpe_first_half"]), H.num(x["sharpe_second_half"]),
                       "—" if x["instruments_where_signal_helped"] is None else f"{x['instruments_where_signal_helped']} of {x['instruments']}",
                       "—" if both is None else H.chip("yes", "up") if both else H.chip("no", "down")])
        body += H.section("Alternative rules on the same history", H.table(
            ["Rule", "CAGR", "Sharpe", "Max drawdown", "Time invested", "Sharpe 1st half", "Sharpe 2nd half",
             "Signal helped", "Beats benchmark in both halves"], vr, numeric={1, 2, 3, 4, 5, 6}),
            "A rule only earns adoption if it beats always-invested equal weight on a risk-adjusted basis in both halves. "
            "'Signal helped' counts instruments whose 13-week return was higher when the rule was on than when it was off.")
    body += H.section("How to use this", H.notes([
        "Supported rules earn more trust. If a stance fails in either half, size it down and lean harder on the checklist.",
        "The weekly run re-tests these rules and logs any deterioration in the Improvement Log.",
    ]))
    body += H.section("Limits of this test", H.notes([
        "Survivorship: today's instrument list is used for the whole period; past losers that left the list are missing.",
        "Price returns in local currency, weekly closes, no trading costs, taxes or dividends.",
        "The macro stress overlay (VIX risk budget) is not applied historically.",
        f"{len(res['instruments'])} instruments, {res['observations']:,} weekly observations from {res['first_date']}: still a small sample for a market regime test.",
    ]))
    page = H.page("Rule Evidence", f"Backtest {res['first_date']} to {res['last_date']}",
                  "Walk-forward test of the Decision Desk rules on five years of weekly prices, using only data available at each date.",
                  body, "Interactive Brokers weekly closes. Research evidence, not a promise of future results.", standalone)
    return page.replace(f"<style>{H.CSS}</style>", f"<style>{H.CSS}{CSS}</style>", 1)
