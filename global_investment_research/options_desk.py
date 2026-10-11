"""Speculative Options Sleeve page: rules first, then ideas with payoff charts and sizing."""
from __future__ import annotations

from . import html as H
from . import options as O
from .monitor import fmt_pct

CSS = """
.ideas{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
.idea{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:14px 16px;display:grid;gap:8px;min-width:0;align-content:start}
.idea h3{margin:0;font:600 1.05rem/1.3 var(--f-display)}
.kv{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;font-size:.86rem}
.kv span:nth-child(odd){color:var(--muted)}
.kv span:nth-child(even){font-family:var(--f-data);font-variant-numeric:tabular-nums;text-align:right}
svg.pay{width:100%;height:auto;display:block}
svg.pay .zero{stroke:var(--rule)}
svg.pay .line{fill:none;stroke:var(--accent);stroke-width:2}
svg.pay .spot{stroke:var(--muted);stroke-dasharray:3 3}
svg.pay text{fill:var(--muted);font:10px var(--f-data)}
.warnbox{border:1px solid var(--warn);border-radius:6px;padding:12px 14px;background:var(--panel)}
.controls{display:flex;flex-wrap:wrap;gap:12px;align-items:end;background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px 14px}
.controls label{display:grid;gap:4px;font:500 .72rem/1.2 var(--f-data);letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.controls input{font:500 .95rem var(--f-data);padding:6px 8px;border:1px solid var(--rule);border-radius:4px;background:var(--bg);color:var(--ink)}
"""


def payoff_svg(idea, w=300, h=120) -> str:
    S = idea["underlying"]
    legs = [(l["qty"], l["right"], l["strike"]) for l in idea["legs"]]
    xs = [S * (0.75 + 0.5 * i / 60) for i in range(61)]
    ys = [(O.payoff(legs, x) - idea["debit"]) * O.MULTIPLIER for x in xs]
    lo, hi = min(ys + [0]), max(ys + [0])
    rng = (hi - lo) or 1
    px = lambda x: 48 + (x - xs[0]) / (xs[-1] - xs[0]) * (w - 58)  # noqa: E731
    py = lambda y: 8 + (hi - y) / rng * (h - 28)  # noqa: E731
    path = "M" + " L".join(f"{px(x):.1f},{py(y):.1f}" for x, y in zip(xs, ys))
    return (f'<svg class="pay" viewBox="0 0 {w} {h}" role="img" aria-label="Profit or loss per contract at expiry">'
            f'<line class="zero" x1="48" x2="{w-10}" y1="{py(0):.1f}" y2="{py(0):.1f}"/>'
            f'<line class="spot" x1="{px(S):.1f}" x2="{px(S):.1f}" y1="8" y2="{h-20}"/>'
            f'<path class="line" d="{path}"/>'
            f'<text x="2" y="{py(hi)+4:.0f}">{hi:+,.0f}</text><text x="2" y="{py(lo)+4:.0f}">{lo:+,.0f}</text>'
            f'<text x="{px(xs[0]):.0f}" y="{h-6}">-25%</text><text x="{px(S)-10:.0f}" y="{h-6}">spot</text>'
            f'<text x="{px(xs[-1])-26:.0f}" y="{h-6}">+25%</text></svg>')


def legs_text(i) -> str:
    parts = []
    for l in i["legs"]:
        side = "Buy" if l["qty"] > 0 else "Sell"
        parts.append(f"{side} {i['expiry']} {l['strike']:g} {'call' if l['right'] == 'C' else 'put'}")
    return " / ".join(parts)


def options_page(res: dict, standalone=True) -> str:
    pol = res["policy"]
    body = H.section("Read this first", (
        '<div class="warnbox"><ul class="notes">'
        f"<li><b>Sleeve cap {pol['sleeve_cap']*100:.0f}% of portfolio</b> in total premium; at most "
        f"{pol['per_trade']*100:.0f}% per trade. This money can go to zero.</li>"
        "<li><b>Defined risk only:</b> bought calls and puts, and debit spreads. No selling naked options, no margin.</li>"
        "<li><b>Expect most trades to lose.</b> The payoff comes from a few winners; size so a string of losses is affordable.</li>"
        f"<li><b>Exits are fixed in advance:</b> take profit at +{pol['take_profit']*100:.0f}% on long options (or 75% of a spread's "
        f"maximum gain), cut at −{pol['stop_loss']*100:.0f}% of premium, close or roll with {pol['time_stop_days']} days left.</li>"
        "<li>Probabilities use the option market's own implied volatility. They show what the price assumes; "
        "the edge, if any, comes only from the trend call being right.</li>"
        "</ul></div>"))

    controls = ('<div class="controls"><label for="pv">Portfolio value<input id="pv" type="number" min="0" step="1000" '
                'value="1000000"></label><span class="mut" id="cap"></span></div>')
    cards = ""
    for i in res["ideas"]:
        tone = "up" if i["direction"] == "bull" else "down"
        rr = f"{i['reward_risk']:.1f} : 1" if i["reward_risk"] is not None else "open-ended"
        scen = " · ".join(f"{k} {v*100:+.0f}%" for k, v in i["scenarios"].items())
        thin = f'<span class="warn">Thin open interest at {", ".join(f"{k:g}" for k in i["thin_strikes"])}</span>' if i["thin_strikes"] else ""
        cards += (
            f'<div class="idea"><h3>{H.esc(i["symbol"])} · {H.esc(i["structure"])} {H.chip(i["direction"], tone)}</h3>'
            f'<span class="mut">{H.esc(legs_text(i))}</span>'
            f'{payoff_svg(i)}'
            '<div class="kv">'
            f'<span>Underlying</span><span>{i["underlying"]:,.2f}</span>'
            f'<span>Cost per contract</span><span>{i["debit_per_contract"]:,.0f}</span>'
            f'<span>Breakeven at expiry</span><span>{i["breakeven"]:,.2f} ({fmt_pct(i["breakeven_move"])})</span>'
            f'<span>Max gain : max loss</span><span>{rr}</span>'
            f'<span>Probability of profit</span><span>{i["prob_profit"]*100:.0f}%</span>'
            f'<span>Return if 1σ move ({fmt_pct(i["one_sd_move"])})</span><span>{i["return_at_1sd"]*100:+.0f}%</span>'
            f'<span>Implied volatility</span><span>{i["iv"]*100:.0f}%</span>'
            f'<span>Days to expiry</span><span>{i["days"]}</span>'
            f'<span>Contracts at 1% risk</span><span class="ct" data-debit="{i["debit_per_contract"]:.4f}">{i["contracts_at_1pct"]}</span>'
            '</div>'
            f'<span class="mut">Return on premium at expiry if the stock moves {H.esc(scen)}</span>'
            f'<span class="mut">Why: Decision Desk says {H.esc(i["stance"])}. Prices: {H.esc(i["price_basis"])}.</span>{thin}</div>')
    body += H.section("Trade ideas, ranked", controls + f'<div class="ideas">{cards or "<p>No ideas passed the filters.</p>"}</div>',
                      "Ranked by return on premium if the underlying makes a one-standard-deviation move (from its implied "
                      "volatility) in the trade's favour, weighted by the probability of profit. Chart: profit or loss per contract at expiry (y) against the "
                      "underlying price from −25% to +25% (x); the dashed line marks today's price.")
    if res["skipped"]:
        body += H.section("Screened out", H.table(["Underlying", "Reason"],
                          [[H.esc(s["symbol"]), H.esc(s["reason"])] for s in res["skipped"]], wrap={1}),
                          "No idea is forced: underlyings without a clear trend call or a tradable quote are left out.")
    body += H.section("How to place one", H.notes([
        "Use a limit order at or below the mid price shown; never pay the full ask on a wide market.",
        "For spreads, enter both legs as one combination order so you never hold one leg alone.",
        "Set the exit alerts the same day: underlying exit level from the Decision Desk, −50% premium, and the 21-day time stop.",
        "Log the trade in the Decision Book with the assumption, counter-case and pre-mortem.",
    ]))
    script = """<script>(()=>{const pv=document.getElementById('pv'),cap=document.getElementById('cap');
const f=new Intl.NumberFormat(undefined,{maximumFractionDigits:0});
function u(){const v=Math.max(0,+pv.value||0);cap.textContent='Sleeve cap '+f.format(v*%CAP%)+' · per trade '+f.format(v*%PT%);
document.querySelectorAll('.ct').forEach(e=>{const d=+e.dataset.debit;e.textContent=d>0?String(Math.floor(v*%PT%/d)):'—'});
try{localStorage.setItem('opt.pv',pv.value)}catch(e){}}
try{const s=localStorage.getItem('opt.pv');if(s)pv.value=s}catch(e){}pv.addEventListener('input',u);u();})();</script>"""
    script = script.replace("%CAP%", str(pol["sleeve_cap"])).replace("%PT%", str(pol["per_trade"]))
    footer = ("Option quotes from Interactive Brokers, retrieved on a weekend (last session's values). Verify live quotes before "
              "trading. Options can expire worthless; this is a speculative research screen, not personalised advice.")
    page = H.page("Speculative Options Sleeve", f"Week to {res['as_of']}",
                  "Small, defined-risk option trades that express the Decision Desk's strongest trend calls, sized to a hard cap.",
                  body + script, footer, standalone)
    return page.replace(f"<style>{H.CSS}</style>", f"<style>{H.CSS}{CSS}</style>", 1)
