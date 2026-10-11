"""Investment Handbook: one source rendered to Markdown (repo) and HTML (shareable page).

Policy numbers come from decisions.py so the handbook can never drift from the engine.
"""
from __future__ import annotations

from . import decisions as D
from . import html as H

P = lambda t: ("p", t)        # noqa: E731
L = lambda *xs: ("ul", xs)    # noqa: E731
N = lambda *xs: ("ol", xs)    # noqa: E731
T = lambda hdr, *rows: ("table", (hdr, rows))  # noqa: E731

SECTIONS = [
    ("1. Purpose and mandate", [
        P("This handbook turns weekly market data into decisions you can act on and audit. It covers equities in Hong Kong, "
          "mainland China, Singapore, the UK and the US, plus sectors, gold, commodities, rates and FX."),
        T(["Mandate", "Objective", "Horizon", "Primary tool"],
          ["Global growth equities", "Compound capital with controlled drawdowns", "6–12 months", "Decision Desk §3"],
          ["Singapore income", "6–8% sustainable cash yield, accepting price volatility", "3+ years", "Decision Desk §4"],
          ["Macro and FX", "Set the risk budget and hedges", "Weekly", "Decision Desk §1, §5"]),
        P("The tool ranks evidence. It does not replace judgement on valuation, accounting quality or personal tax, "
          "and it never places orders."),
    ]),
    ("2. The weekly routine (45 minutes, Saturday or Sunday)", [
        N("Refresh data: collect the IBKR snapshot (docs/DATA_COLLECTION.md) and run `global-research monitor` (it writes the Decision Desk).",
          "Read the posture first (Desk §1). It sets the risk budget for the week before any stock decision.",
          "Check 'What changed since the last run' in the Monitor. A flipped macro reading outranks any single-name view.",
          "Review existing positions against their exit levels (`global-research review`). Breaches are handled first.",
          "Work the Accumulate list top-down. Each candidate passes the five-question checklist (§6) or is skipped.",
          "Income: set limit orders at the 7% and 8% yield prices for staged tranches (Desk §4).",
          "Log every decision, including decisions not to act, in config/decision-log.csv."),
    ]),
    ("3. Decision rules (single names and ETFs)", [
        P("Trend uses weekly closes: the 10-week average (≈50-day) and 40-week average (≈200-day). "
          "Momentum = average of 3-month and 1-year return ÷ annualised volatility."),
        T(["Decision", "Rule", "What you do"],
          ["Accumulate", "Price > 10w > 40w average, momentum ≥ 0.5, price ≤ 20% above 40w, market not stressed",
           "New money allowed up to Max size; buy in the zone between the 10w average and today's price"],
          ["Hold", f"Up-trend but momentum < 0.5, or > {int(D.EXTENDED*100)}% above the 40w average",
           "Keep the position; do not add; add only on a pullback toward the 10w average"],
          ["Watch", "Trend mixed (price between the averages)", "No position. Revisit when the weekly close is above both averages"],
          ["Avoid", "Price < 10w < 40w average", "No new money. Existing holdings: exit or write down why you are keeping them"]),
        P(f"Exit level = {int(D.STOP_BUFFER*100)}% below the 40-week average, judged on a weekly close to avoid intraday noise."),
    ]),
    ("4. Position sizing and risk limits", [
        T(["Limit", "Value", "Why"],
          ["Risk per position", f"{D.RISK_PER_POSITION*100:.1f}% of portfolio", "A stopped-out position costs at most this much"],
          ["Single stock cap", f"{int(D.MAX_POSITION*100)}%", "No single company can sink the portfolio"],
          ["ETF cap", f"{int(D.MAX_POSITION_ETF*100)}%", "Broad funds are diversified, so they can be larger"],
          ["Risk budget", "100% calm · 75% elevated (VIX 16–25) · 50% stressed (VIX > 25)", "Size down when markets are fragile"]),
        P("Size = risk per position ÷ distance to the exit level, capped, then multiplied by the week's risk budget. "
          "A stock 10% above its exit level gets 0.5% ÷ 10% = 5% of the portfolio."),
        L("Sector limit: no more than 30% of equity in one sector; check the Accumulate list for concentration before adding.",
          "Currency: report the portfolio in your base currency; hedge when the FX desk shows your base currency strengthening."),
    ]),
    ("5. Macro playbook", [
        T(["Reading", "Tilt"],
          ["Long yields rising (TLT down-trend)", "Keep bond duration short"],
          ["Dollar strengthening (UUP up-trend)", "Hedge non-USD assets for USD investors; HK/EM headwind"],
          ["Oil up-trend", "Keep energy exposure"],
          ["Gold down-trend", "Do not add gold until it reclaims its 40w average"],
          ["Defensives leading cyclicals (XLP > XLY)", "Favour quality and defensives"],
          ["China A / HK lagging US over 3 months", "Underweight until relative strength turns"],
          ["VIX > 25", "Halve the risk budget; no new Accumulate in high-volatility names"]),
        P("Market-to-factor links are shown as 'stable' only when both halves of the year agree. Treat unstable links as noise."),
    ]),
    ("6. Before any trade: the five-question checklist", [
        N("Assumption: the one thing that must be true for the trade to work.",
          "Strongest counter-case and the data that would confirm it.",
          "Pre-mortem: six months on, the trade lost 20%. The three most likely reasons, most probable first.",
          "Exit level entered as an alert or stop before buying.",
          "Size within limits, sector not over-weight, decision logged."),
    ]),
    ("7. Singapore income rules", [
        L("Buy for income only when trailing cash yield is inside 6–8%.",
          "Above 8%: the market expects a cut. Verify distribution cover, gearing and refinancing before buying.",
          "In a price down-trend, stage entries in thirds: now, at the 7% yield price, and after the trend repairs.",
          "Stress test: the 'yield if cut 20%' column must still meet your income need.",
          "Prefer names with steady or rising distributions; a falling record (noted in the desk) needs a written reason to hold."),
    ]),
    ("8. Learning loop", [
        P("Each run appends to data/state/history.jsonl. The Monitor lists macro readings that flipped since the last run."),
        P("Each month run `global-research review` against config/decision-log.csv. It marks each decision as working, "
          "not working yet, avoided a loss, missed a gain or exit level hit. Change a rule only after it misfires repeatedly, "
          "and record the change and its reason in this handbook."),
    ]),
    ("9. What the tool does not do", [
        L("It does not value companies: fundamentals were not refreshed in the 11 Oct 2026 run.",
          "It does not include dividends in price returns (except the income desk), costs or taxes.",
          "It uses about one year of weekly data, a small sample. Patterns are hypotheses, not laws.",
          "Rules are mechanical. A documented human override is allowed; an undocumented one is not."),
    ]),
]


def to_markdown() -> str:
    out = ["# Investment Handbook", "",
           "Operating manual for the Decision Desk. Policy numbers are generated from `global_investment_research/decisions.py`.", ""]
    for title, blocks in SECTIONS:
        out += [f"## {title}", ""]
        for kind, v in blocks:
            if kind == "p":
                out += [v, ""]
            elif kind == "ul":
                out += [f"- {x}" for x in v] + [""]
            elif kind == "ol":
                out += [f"{i}. {x}" for i, x in enumerate(v, 1)] + [""]
            else:
                hdr, rows = v
                out += ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
                out += ["| " + " | ".join(r) + " |" for r in rows] + [""]
    return "\n".join(out)


def _inline(text: str) -> str:
    """Escape, then render `code` spans."""
    parts = H.esc(text).split("`")
    return "".join(f"<code>{p}</code>" if i % 2 else p for i, p in enumerate(parts))


def to_html(standalone=True) -> str:
    body = ""
    for title, blocks in SECTIONS:
        inner = ""
        for kind, v in blocks:
            if kind == "p":
                inner += f'<p class="intro">{_inline(v)}</p>'
            elif kind in ("ul", "ol"):
                inner += f'<{kind} class="notes">' + "".join(f"<li>{_inline(x)}</li>" for x in v) + f"</{kind}>"
            else:
                hdr, rows = v
                inner += H.table(hdr, [[_inline(c) for c in r] for r in rows], wrap=set(range(len(hdr))))
        body += f"<section><h2>{H.esc(title)}</h2>{inner}</section>"
    return H.page("Investment Handbook", "Operating manual",
                  "How to turn the weekly monitor into decisions: the routine, the rules, the risk limits and the review loop.",
                  body, "Policy values are generated from the decision engine, so this page and the Decision Desk always agree.",
                  standalone)
