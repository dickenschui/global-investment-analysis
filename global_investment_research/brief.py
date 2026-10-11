"""One-page weekly brief: the call, the actions, the risks. Generated from engine output."""
from __future__ import annotations

from collections import Counter

from . import html as H
from .monitor import fmt_pct

BRIEF_CSS = """
.call{background:var(--panel);border:1px solid var(--rule);border-left:4px solid var(--accent);border-radius:6px;padding:16px 18px;display:grid;gap:6px}
.call p{margin:0;max-width:80ch}
.call .lead{font:600 1.15rem/1.4 var(--f-display)}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
.two>div{min-width:0;display:grid;gap:8px;align-content:start}
h3{font:600 .8rem/1.2 var(--f-data);letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:0}
"""


def _sector_concentration(acc, risk_budget=1.0):
    if not acc:
        return None, 0.0, 0.0
    w = Counter()
    for c in acc:
        w[c["sector"]] += (c["size"] or 0) * risk_budget
    total = sum(w.values())
    top, top_w = w.most_common(1)[0]
    return top, top_w, total


def brief_page(dec: dict, mon: dict, standalone=True, opt: dict | None = None, confidence: str | None = None) -> str:
    calls, p = dec["calls"], dec["posture"]
    acc = [c for c in calls if c["stance"] == "Accumulate"]
    counts = Counter(c["stance"] for c in calls)
    mk = mon["markets"]
    leaders = sorted(((m, v["proxy_metrics"]["ret_3m"]) for m, v in mk.items() if v["proxy_metrics"]),
                     key=lambda x: -x[1])
    best, worst = leaders[0], leaders[-1]
    top_sector, top_w, total_w = _sector_concentration(acc, p["risk_budget"])
    inc = sorted((c for c in calls if c.get("income_call") and c["income_call"]["stance"] == "Accumulate"),
                 key=lambda c: -c["income"]["yield"])

    # Headline from the data: where the Accumulate calls sit vs where the Avoid calls sit.
    by_mkt = {}
    for c in calls:
        if c["kind"] == "stock" or c["kind"] == "index_proxy":
            s = by_mkt.setdefault(c["market"], [0, 0, 0])
            s[0] += c["stance"] == "Accumulate"
            s[1] += c["stance"] == "Avoid"
            s[2] += 1
    pos = [m for m, (a, v, n) in by_mkt.items() if a / n >= 0.25]
    neg = [m for m, (a, v, n) in by_mkt.items() if v / n >= 0.5]
    acc_sectors = [s for s, _ in Counter(c["sector"] for c in acc).most_common(2)]
    lead = (f"Constructive on {', '.join(pos) or 'no market'}"
            + (f" (led by {' and '.join(acc_sectors)})" if acc_sectors else "")
            + f"; defensive on {', '.join(neg) or 'no market'}. Risk budget {p['risk_budget']*100:.0f}%.")
    body = ('<div class="call">'
            f'<p class="lead">{H.esc(lead)}</p>'
            f'<p>{H.esc(p["risk_note"])} Best market over 3 months: {best[0]} {fmt_pct(best[1])}; '
            f'weakest: {worst[0]} {fmt_pct(worst[1])}. Decisions this week: {counts["Accumulate"]} Accumulate, '
            f'{counts["Hold"]} Hold, {counts["Watch"]} Watch, {counts["Avoid"]} Avoid.</p>'
            + (f'<p class="warn" style="margin-top:6px">{H.esc(confidence)}</p>' if confidence else "") + '</div>')

    rows = [[f"<b>{H.esc(c['symbol'])}</b> {H.esc(c['name'])}", H.esc(c["sector"]), H.num(c["last"]),
             f"{c['entry'][0]:,.2f}–{c['entry'][1]:,.2f}", H.num(c["stop"]), H.pct(c["size"] * p["risk_budget"], signed=False),
             H.pct(c["ret_3m"])] for c in acc]
    body += H.section("Actions: Accumulate list", H.table(
        ["Instrument", "Sector", "Last", "Buy zone", "Exit below", "Max weight", "3m"], rows, numeric={2, 4, 5, 6}),
        f"Combined maximum weight {total_w*100:.1f}% of the portfolio. Largest sector: {H.esc(top_sector or 'n/a')} "
        f"at {top_w*100:.1f}%. Keep any one sector under 30% of equity.")

    tilts = "".join(f"<li><b>{H.esc(t['action'])}.</b> <span class=\"mut\">{H.esc(t['because'])}.</span></li>" for t in p["tilts"])
    inc_items = "".join(
        f"<li><b>{H.esc(c['symbol'])}</b> {H.esc(c['name'])}: yield {c['income']['yield']*100:.2f}%. "
        f"Buy a third now, a third at {c['income_call']['price_for_7pct']:.3f} (7% yield), the rest after the price trend repairs.</li>"
        for c in inc)
    body += ('<section class="two">'
             f'<div><h3>Portfolio tilts</h3><ul class="notes">{tilts}</ul></div>'
             f'<div><h3>Singapore income (6–8%)</h3><ul class="notes">{inc_items or "<li>No names in band.</li>"}</ul></div>'
             "</section>")

    if opt and opt["ideas"]:
        orows = [[f"<b>{H.esc(i['symbol'])}</b> {H.esc(i['structure'])}",
                  H.esc(" / ".join(f"{'Buy' if l['qty'] > 0 else 'Sell'} {l['strike']:g}{l['right']}" for l in i["legs"])),
                  i["expiry"], f"{i['debit_per_contract']:,.0f}", fmt_pct(i["breakeven_move"]),
                  "open" if i["reward_risk"] is None else f"{i['reward_risk']:.1f} : 1", f"{i['prob_profit']*100:.0f}%"]
                 for i in opt["ideas"][:4]]
        body += H.section("Speculative sleeve: top option ideas", H.table(
            ["Idea", "Legs", "Expiry", "Cost / contract", "Move to breakeven", "Reward : risk", "Prob. of profit"],
            orows, numeric={3, 4, 5, 6}),
            f"Defined risk, capped at {opt['policy']['sleeve_cap']*100:.0f}% of the portfolio in total and "
            f"{opt['policy']['per_trade']*100:.0f}% per trade. Full list and payoff charts on the Options Sleeve page.")

    reading = {r["topic"]: r for r in mon["regime"]}
    risks = []
    if top_sector:
        risks.append(f"Concentration: {H.esc(top_sector)} is {top_w*100:.1f}% of the {total_w*100:.1f}% buy list; "
                     "one sector shock hits several names at once.")
    pf = dec.get("portfolio")
    if pf and pf["clusters"]:
        big = max(pf["clusters"], key=lambda g: g["risk_before"])
        risks.append(f"Overlapping bets: {H.esc(', '.join(big['members']))} move together; buying all of them is one "
                     f"position of {big['risk_before']*100:.1f}% risk, not {len(big['members'])} separate ones. Portfolio "
                     f"volatility is {pf['vol']*100:.1f}%, against {pf['vol_if_independent']*100:.1f}% if the names were independent.")
    if any(c["sector"] == "Energy" for c in acc) and "Oil" in reading:
        risks.append(f"Oil reversal: energy calls rest on the oil trend ({H.esc(reading['Oil']['evidence'])}); "
                     "it can reverse fast on supply news.")
    if "rising" in reading.get("Rates", {}).get("reading", ""):
        risks.append("Rates: long yields are rising; a further jump would hit long-duration growth stocks and REITs together.")
    risks.append("Model limit: calls use price trend only. A strong trend can hide a stretched valuation; check before buying.")
    if "strengthening" in reading.get("US dollar", {}).get("reading", ""):
        risks.append("Dollar: a reversal would take back the currency gain on US assets for a GBP-based investor, and Asia could rebound quickly.")
    body += H.section("Pre-mortem: why this could fail, most likely first", H.notes(risks))
    body += H.section("Next steps", H.notes([
        "Pass the five-question checklist (Handbook §6) for each Accumulate name you act on.",
        "Enter exit levels as alerts before buying; log each decision in the Decision Book.",
        "Next refresh: Saturday. Readings that flip appear under 'What changed' in the Monitor.",
    ]))
    footer = ("Mechanical outputs from price-trend, momentum and yield rules on IBKR weekly closes. No valuation, costs or taxes. "
              "Not personalised advice.")
    page = H.page("Weekly Investment Brief", f"Week to {dec['as_of']}",
                  "The week's call in one page: posture, actions with levels, income plan and the risks to watch.",
                  body, footer, standalone)
    return page.replace(f"<style>{H.CSS}</style>", f"<style>{H.CSS}{BRIEF_CSS}</style>", 1)
