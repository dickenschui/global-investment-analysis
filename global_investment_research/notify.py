"""Weekly digest for email (HTML, inline styles) and Telegram (short HTML message).

Telegram delivery uses the Bot API with TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from the
environment; nothing is stored in the repo. Email delivery is done by the caller (the weekly
routine uses the Gmail connector) with the HTML from email_html().
"""
from __future__ import annotations

import html
import json
import os
import urllib.parse
import urllib.request

LINKS = {
    "Pack (start here)": "https://claude.ai/artifact/4VjjnbhJUG3a7Bh78RWvLf",
    "Weekly Brief": "https://claude.ai/artifact/Fz1Pc2fafD3y3yA6HKm5jp",
    "Decision Desk": "https://claude.ai/artifact/SSQ1VK33aGvevMkNsappyW",
    "Options Sleeve": "https://claude.ai/artifact/556rrpW6C2Zb8Sjya6Z789",
    "Rule Evidence": "https://claude.ai/artifact/2FxetPUFQZ5hfF7LyyiehH",
    "Handbook": "https://claude.ai/artifact/BdVG4ieQAZrjassRaoXWTi",
}


def _pct(x, signed=True):
    return "n/a" if x is None else (f"{x*100:+.1f}%" if signed else f"{x*100:.1f}%")


def top_option_ideas(opt, n=5):
    seen, out = set(), []
    for i in (opt or {}).get("ideas", []):
        if i["symbol"] not in seen:
            seen.add(i["symbol"])
            out.append(i)
        if len(out) == n:
            break
    return out


def digest(dec: dict, mon: dict, opt: dict | None = None, changes: list | None = None, confidence: str | None = None) -> dict:
    calls = dec["calls"]
    acc = [c for c in calls if c["stance"] == "Accumulate"]
    avoid = [c for c in calls if c["stance"] == "Avoid"]
    inc = [c for c in calls if c.get("income_call") and c["income_call"]["stance"] == "Accumulate"]
    inc.sort(key=lambda c: -c["income"]["yield"])
    markets = [(m, v["proxy"], v["proxy_metrics"]) for m, v in mon["markets"].items() if v["proxy_metrics"]]
    return {
        "as_of": dec["as_of"],
        "risk_note": dec["posture"]["risk_note"],
        "tilts": [t["action"] for t in dec["posture"]["tilts"]],
        "markets": [(m, p, pm["ret_1w"], pm["ret_3m"], pm["trend"]["state"]) for m, p, pm in markets],
        "accumulate": [(c["symbol"], c["name"], c["last"], c["stop"], c["size"]) for c in acc],
        "avoid_count": len(avoid),
        "income": [(c["symbol"], c["name"], c["income"]["yield"], c["income_call"]["price_for_7pct"]) for c in inc],
        "options": [(i["symbol"], i["structure"], " / ".join(f"{'Buy' if l['qty'] > 0 else 'Sell'} {l['strike']:g}{l['right']}"
                     for l in i["legs"]), i["expiry"], i["debit_per_contract"], i["prob_profit"]) for i in top_option_ideas(opt)],
        "changes": changes or [],
        "confidence": confidence,
    }


def telegram_text(d: dict) -> str:
    e = html.escape
    lines = [f"<b>Weekly investment brief · week to {d['as_of']}</b>", e(d["risk_note"]), ""]
    if d.get("confidence"):
        lines += [f"⚠️ {e(d['confidence'])}", ""]
    if d["changes"]:
        lines += ["<b>Changed since last week</b>"] + [f"• {e(c['topic'])}: {e(c['from'])} → {e(c['to'])}" for c in d["changes"]] + [""]
    lines.append("<b>Markets (1w / 3m)</b>")
    lines += [f"• {m}: {_pct(w)} / {_pct(q)} ({t or 'n/a'})" for m, _, w, q, t in d["markets"]]
    lines += ["", f"<b>Accumulate ({len(d['accumulate'])})</b>"]
    lines += [f"• {e(s)}: exit &lt; {stop:,.2f}, max {_pct(sz, False)}" for s, _, _, stop, sz in d["accumulate"]]
    if d["income"]:
        lines += ["", "<b>SG income (6–8%)</b>"] + [f"• {e(s)} {_pct(y, False)}; add at {p:.3f} (7%)" for s, _, y, p in d["income"]]
    if d["options"]:
        lines += ["", "<b>Options sleeve (max 1% each, 5% total)</b>"]
        lines += [f"• {e(s)} {e(st)}: {e(legs)} {exp}, ${cost:,.0f}/contract, PoP {pop*100:.0f}%" for s, st, legs, exp, cost, pop in d["options"]]
    lines += ["", " · ".join(f'<a href="{u}">{e(k)}</a>' for k, u in LINKS.items()),
              "<i>Rule-based research, not personalised advice. Re-price before trading.</i>"]
    return "\n".join(lines)


def email_html(d: dict) -> str:
    e = html.escape
    th = 'style="text-align:left;padding:6px 8px;border-bottom:1px solid #d9dee5;font:600 12px Arial;color:#5b6673"'
    td = 'style="padding:6px 8px;border-bottom:1px solid #eef2f6;font:13px Arial;color:#17202a"'
    tdn = 'style="padding:6px 8px;border-bottom:1px solid #eef2f6;font:13px Arial;color:#17202a;text-align:right"'

    def table(headers, rows, numeric=()):
        h = "".join(f"<th {th}>{e(x)}</th>" for x in headers)
        b = "".join("<tr>" + "".join(f"<td {tdn if i in numeric else td}>{c}</td>" for i, c in enumerate(r)) + "</tr>" for r in rows)
        return f'<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;margin:6px 0 18px">{h and "<tr>"+h+"</tr>"}{b}</table>'

    h2 = lambda t: f'<h2 style="font:600 16px Georgia,serif;color:#17202a;margin:18px 0 4px">{e(t)}</h2>'  # noqa: E731
    parts = [f'<div style="max-width:680px;margin:0 auto;font:14px/1.5 Arial;color:#17202a">',
             f'<p style="font:500 11px Arial;letter-spacing:.08em;color:#1d5c8a;margin:0">WEEK TO {d["as_of"]}</p>',
             '<h1 style="font:600 22px Georgia,serif;margin:4px 0 8px">Weekly Investment Brief</h1>',
             f'<p style="margin:0 0 6px">{e(d["risk_note"])}</p>']
    if d.get("confidence"):
        parts.append(f'<p style="margin:8px 0;padding:10px 12px;border-left:4px solid #9a6a00;background:#fff8e6">{e(d["confidence"])}</p>')
    if d["changes"]:
        parts += [h2("Changed since last week"), "<ul>" + "".join(f"<li>{e(c['topic'])}: {e(c['from'])} → {e(c['to'])}</li>" for c in d["changes"]) + "</ul>"]
    parts += [h2("Markets"), table(["Market", "Proxy", "1 week", "3 months", "Trend"],
                                   [[m, p, _pct(w), _pct(q), t or "n/a"] for m, p, w, q, t in d["markets"]], numeric=(2, 3))]
    parts += [h2("Portfolio tilts"), "<ul>" + "".join(f"<li>{e(t)}</li>" for t in d["tilts"]) + "</ul>"]
    parts += [h2(f"Accumulate ({len(d['accumulate'])}) · {d['avoid_count']} on Avoid"),
              table(["Ticker", "Name", "Last", "Exit below", "Max weight"],
                    [[f"<b>{e(s)}</b>", e(n), f"{last:,.2f}", f"{stop:,.2f}", _pct(sz, False)] for s, n, last, stop, sz in d["accumulate"]],
                    numeric=(2, 3, 4))]
    if d["income"]:
        parts += [h2("Singapore income, 6–8% band"),
                  table(["Ticker", "Name", "Yield", "Add at (7% yield)"],
                        [[f"<b>{e(s)}</b>", e(n), _pct(y, False), f"{p:.3f}"] for s, n, y, p in d["income"]], numeric=(2, 3))]
    if d["options"]:
        parts += [h2("Options sleeve: top ideas (1% each, 5% cap)"),
                  table(["Idea", "Legs", "Expiry", "Cost / contract", "Prob. of profit"],
                        [[f"<b>{e(s)}</b> {e(st)}", e(legs), exp, f"${c:,.0f}", f"{pop*100:.0f}%"] for s, st, legs, exp, c, pop in d["options"]],
                        numeric=(3, 4))]
    parts += [h2("Open the pack"), "<p>" + " · ".join(f'<a href="{u}" style="color:#1d5c8a">{e(k)}</a>' for k, u in LINKS.items()) + "</p>",
              '<p style="font:12px Arial;color:#5b6673;margin-top:18px">Rule-based research from Interactive Brokers weekly closes; '
              "no valuation, costs or taxes. Not personalised advice. Re-price live before trading. The Decision Book (Excel) is attached when available.</p></div>"]
    return "".join(parts)


def send_telegram(text: str, token: str | None = None, chat_id: str | None = None, opener=urllib.request.urlopen) -> dict:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with opener(req, timeout=20) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram refused the message: {resp.get('description')}")
    return resp
