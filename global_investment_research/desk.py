"""Decision Desk page: posture -> actions -> sizing -> checklist. Interactive, self-contained."""
from __future__ import annotations

import json

from . import html as H
from .monitor import fmt_pct
from .reports import PRIOR_THESES

STANCE_TONE = {"Accumulate": "up", "Hold": "", "Watch": "warn", "Avoid": "down"}

DESK_CSS = """
.controls{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:end;background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px 14px}
.controls label{display:grid;gap:4px;font:500 .72rem/1.2 var(--f-data);letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.controls input,.controls select{font:500 .95rem var(--f-data);padding:6px 8px;border:1px solid var(--rule);border-radius:4px;background:var(--bg);color:var(--ink);min-width:0}
.controls input:focus-visible,.controls select:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.posture{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,2fr);gap:16px;align-items:start}
.card{align-content:start}
@media (max-width:760px){.posture{grid-template-columns:1fr}}
.big{font:600 2.4rem/1 var(--f-data);color:var(--accent)}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:14px 16px;display:grid;gap:8px;min-width:0}
ol.check{margin:0;padding-left:1.2rem;display:grid;gap:8px;max-width:85ch}
ol.check b{font-weight:600}
.lanes{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.lane h3{margin:0;font:600 .8rem/1.2 var(--f-data);letter-spacing:.06em;text-transform:uppercase}
.lane ul{margin:0;padding:0;list-style:none;display:grid;gap:6px}
.lane li{display:flex;justify-content:space-between;gap:8px;font-size:.88rem;border-bottom:1px dashed var(--rule);padding-bottom:4px}
.lane li span:last-child{font-family:var(--f-data);color:var(--muted);white-space:nowrap}
"""


def _rng(e, d=2):
    if not e:
        return '<span class="mut">n/a</span>'
    return f"{e[0]:,.{d}f}–{e[1]:,.{d}f}"


def desk_page(dec: dict, mon: dict, meta: dict, standalone=True, confidence: str | None = None) -> str:
    p = dec["posture"]
    calls = dec["calls"]
    counts = {s: sum(c["stance"] == s for c in calls) for s in ("Accumulate", "Hold", "Watch", "Avoid")}

    warn = (f'<div class="card" style="border-left:4px solid var(--warn)"><b>{H.esc(confidence.split(".")[0])}</b>'
            f'<span class="mut">{H.esc(confidence.split(".", 1)[1].strip())} See the Rule Evidence page.</span></div>') if confidence else ""
    # 1. Posture
    tilts = "".join(f"<li><b>{H.esc(t['action'])}</b><br><span class=\"mut\">Because: {H.esc(t['because'])}</span></li>"
                    for t in p["tilts"])
    body = warn + H.section("1 · This week's posture", (
        '<div class="posture"><div class="card"><span class="k mut">Risk budget to deploy</span>'
        f'<span class="big">{p["risk_budget"]*100:.0f}%</span><span class="mut">{H.esc(p["risk_note"])}</span></div>'
        f'<div class="card"><span class="k mut">Portfolio tilts</span><ul class="notes">{tilts or "<li>No tilts triggered.</li>"}</ul></div></div>'),
        "Start here. The posture sets how much risk to take before any single-name decision.")

    # 2. Action lanes
    lanes = ""
    for s in ("Accumulate", "Hold", "Watch", "Avoid"):
        items = [c for c in calls if c["stance"] == s][:8]
        lis = "".join(f"<li><span><b>{H.esc(c['symbol'])}</b> {H.esc(c['name'])}</span><span>{c['market']}</span></li>" for c in items)
        more = counts[s] - len(items)
        lanes += (f'<div class="card lane"><h3 class="{STANCE_TONE[s]}">{s} · {counts[s]}</h3><ul>{lis}</ul>'
                  + (f'<span class="mut">+{more} more in the table below</span>' if more > 0 else "") + "</div>")
    body += H.section("2 · Decisions at a glance", f'<div class="lanes">{lanes}</div>',
                      "Accumulate = new money allowed within the size limit. Hold = keep, do not add. "
                      "Watch = no position until the trigger. Avoid = no new money; review existing holdings against their exit level.")

    # 3. Interactive action table with sizing
    controls = ('<div class="controls">'
                '<label for="pv">Portfolio value<input id="pv" type="number" min="0" step="1000" value="1000000"></label>'
                '<label for="mk">Market<select id="mk"><option value="">All</option>'
                + "".join(f'<option>{m}</option>' for m in ("US", "UK", "HK", "China", "SG", "Cross-asset")) +
                '</select></label><label for="st">Decision<select id="st"><option value="">All</option>'
                + "".join(f'<option>{s}</option>' for s in counts) + '</select></label></div>')
    rows = []
    for c in calls:
        tone = STANCE_TONE[c["stance"]]
        size = c["size"] if c["stance"] == "Accumulate" else None
        rows.append(
            f'<tr data-mk="{H.esc(c["market"])}" data-st="{c["stance"]}">'
            f'<td><b>{H.esc(c["symbol"])}</b><br><span class="mut">{H.esc(c["name"])}</span></td>'
            f'<td>{H.chip(c["stance"], tone)}</td>'
            f'<td class="wrap-text">{H.esc(c["rule"])}</td>'
            f'<td class="n">{H.num(c["last"])}</td>'
            f'<td class="n">{_rng(c["entry"])}</td>'
            + (f'<td class="n">exit &lt; {H.num(c["stop"])}<br><span class="mut">{fmt_pct(-c["stop_distance"])} away</span></td>'
               if c.get("stop") else
               f'<td class="n">revisit &gt; {H.num(c["trigger"])}<br><span class="mut">weekly close</span></td>' if c.get("trigger") else
               '<td class="n">—</td>') +
            f'<td class="n">{H.pct(size, signed=False) if size else "—"}</td>'
            f'<td class="n amt" data-size="{size or 0}" data-rb="{p["risk_budget"]}">—</td>'
            f'<td class="n">{H.pct(c["ret_3m"])}</td><td class="n">{H.num(c["momentum"])}</td></tr>')
    table = ('<div class="tbl"><table id="calls"><thead><tr><th>Instrument</th><th>Decision</th><th>Why</th><th>Last</th>'
             '<th>Buy zone</th><th>Key level (weekly close)</th><th>Max size</th><th>Amount</th><th>3m</th><th>Momentum</th>'
             '</tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")
    body += H.section("3 · Action table with position sizing", controls + table,
                      "Max size risks 0.5% of the portfolio if the exit level is hit (capped at 8% per stock, 20% per ETF), "
                      "scaled by this week's risk budget. Amount updates from your portfolio value. Prices are in each "
                      "instrument's quote currency (London in pounds, Hong Kong in HKD, China A in CNY).")

    # 4. Income desk
    inc = [c for c in calls if c.get("income")]
    inc.sort(key=lambda c: (STANCE_ORDER_INC.get(c["income_call"]["stance"], 9), -(c["income"]["yield"] or 0)))
    irows = [[f"<b>{H.esc(c['symbol'])}</b> {H.esc(c['name'])}", H.chip(c["income_call"]["stance"], STANCE_TONE[c["income_call"]["stance"]]),
              H.pct(c["income"]["yield"], signed=False, digits=2), H.num(c["income"]["price"], 3),
              H.num(c["income_call"].get("price_for_7pct"), 3), H.num(c["income_call"].get("price_for_8pct"), 3),
              H.pct(c["income"]["yield_after_cut"]["20%"], signed=False, digits=2), H.pct(c["ret_1y"]),
              H.esc(c["income_call"]["rule"]), H.esc(PRIOR_THESES.get(c["symbol"], ""))] for c in inc]
    body += H.section("4 · Singapore income desk (6–8% target)",
                      H.table(["Name", "Decision", "Trailing yield", "Price", "Price for 7%", "Price for 8%", "Yield if cut 20%",
                               "1y price", "Plan", "Distribution record (9 Oct note)"],
                              irows, numeric={2, 3, 4, 5, 6, 7}, wrap={8, 9}),
                      "The income call is separate from the price-trend call: a REIT can be a staged income buy while its price trend is down. "
                      "Use the 7% and 8% prices as limit-order levels for the second and third tranche.")

    # 5. FX desk
    frows = [[H.esc(f["pair"]), H.trend_chip(f["state"]), H.esc(f["view"]), H.pct(f["ret_3m"])] for f in dec["fx"]]
    body += H.section("5 · FX desk", H.table(["Pair", "Trend", "Read", "3m"], frows, numeric={3}),
                      "Use this to decide whether to hedge. A USD-based investor holding UK or SG assets loses when GBP/USD "
                      "or SGD weaken; a GBP- or SGD-based investor holding US assets gains when USD strengthens.")

    # 6. Checklist
    body += H.section("6 · Before you act: the five-question checklist", (
        '<ol class="check">'
        "<li><b>Assumption:</b> write the one thing that must be true for this trade to work (e.g. 'AI capex keeps growing through 2027').</li>"
        "<li><b>Strongest counter-case:</b> name the best argument against it, and what data would confirm it.</li>"
        "<li><b>Pre-mortem:</b> it is six months later and the trade lost 20%. List the three most likely reasons, most probable first.</li>"
        "<li><b>Exit is pre-committed:</b> the exit level in the Key level column is entered as an alert or stop before buying.</li>"
        "<li><b>Size within limits:</b> amount ≤ the Max size column; the sector is not already over-weight; log the decision.</li>"
        "</ol>"), "Every Accumulate decision passes all five before money moves. Record it in config/decision-log.csv.")

    # 7. What would change the view
    flips = [
        "Volatility: VIX above 25 cuts the risk budget to 50%; above 16 cuts it to 75%.",
        "Rates: TLT reclaiming its 40-week average would end the 'yields rising' tilt and reopen duration.",
        "Dollar: UUP falling below its 40-week average would remove the hedge-non-USD tilt and favour HK/EM.",
        "China/HK: the 3188 or 2800 ETF outperforming SPY over 3 months would end the underweight tilt.",
        "Single names: a weekly close below the exit level flips Accumulate/Hold to Avoid; a reclaim of both averages flips Avoid to Watch.",
    ]
    body += H.section("7 · What would change the view", H.notes([H.esc(x) for x in flips]),
                      "These are the triggers to check each week. Each one is mechanical, so the call does not drift with mood.")

    script = """<script>
(()=>{const pv=document.getElementById('pv'),mk=document.getElementById('mk'),st=document.getElementById('st');
const fmt=new Intl.NumberFormat(undefined,{maximumFractionDigits:0});
function upd(){const v=Math.max(0,+pv.value||0);
document.querySelectorAll('#calls tbody tr').forEach(tr=>{const okm=!mk.value||tr.dataset.mk===mk.value,oks=!st.value||tr.dataset.st===st.value;tr.hidden=!(okm&&oks);
const a=tr.querySelector('.amt'),s=+a.dataset.size,rb=+a.dataset.rb;a.textContent=s>0?fmt.format(s*rb*v):'—';});
try{localStorage.setItem('desk.pv',pv.value)}catch(e){}}
try{const s=localStorage.getItem('desk.pv');if(s)pv.value=s}catch(e){}
[pv,mk,st].forEach(el=>el.addEventListener('input',upd));upd();})();
</script>"""
    lede = (f"Week to {dec['as_of']}: {counts['Accumulate']} Accumulate, {counts['Hold']} Hold, "
            f"{counts['Watch']} Watch, {counts['Avoid']} Avoid across HK, China, SG, UK, US and cross-asset proxies. "
            "Rules are in the Investment Handbook; every call states its reason, entry, exit and size.")
    footer = ("Calls are mechanical outputs of documented price-trend, momentum and yield rules on IBKR weekly closes. They are not "
              "personalised advice and do not include fundamentals, valuation, taxes or costs: complete the checklist and verify "
              "issuer filings before acting.")
    page = H.page("Decision Desk", f"Week to {dec['as_of']}", H.esc(lede), body + script, footer, standalone)
    return page.replace(f"<style>{H.CSS}</style>", f"<style>{H.CSS}{DESK_CSS}</style>", 1)


STANCE_ORDER_INC = {"Accumulate": 0, "Watch": 1, "Hold": 2, "Avoid": 3}


def decisions_json(dec: dict) -> str:
    return json.dumps(dec, indent=1, default=float)
