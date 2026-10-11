"""Excel decision workbook. Policy cells drive every size and price formula, so the
user can change a limit and the whole book recalculates. Requires openpyxl (extra: excel)."""
from __future__ import annotations

from pathlib import Path

from . import decisions as D
from . import options as OPT

BLUE, GREEN = "0000FF", "008000"


def build_workbook(dec: dict, path: str | Path, portfolio_value: float = 1_000_000, opt: dict | None = None) -> Path:
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    F = lambda **k: Font(name="Arial", size=k.pop("size", 10), **k)  # noqa: E731
    head_fill = PatternFill("solid", fgColor="1D5C8A")
    yellow = PatternFill("solid", fgColor="FFFF00")
    thin = Border(bottom=Side(style="thin", color="D9DEE5"))
    wb = Workbook()

    def header(ws, row, cols):
        for i, h in enumerate(cols, 1):
            c = ws.cell(row=row, column=i, value=h)
            c.font, c.fill = F(bold=True, color="FFFFFF"), head_fill
            c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.freeze_panes = ws.cell(row=row + 1, column=2)

    def widths(ws, ws_widths):
        for i, w in enumerate(ws_widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

    # --- Read Me -----------------------------------------------------------
    rm = wb.active
    rm.title = "Read Me"
    lines = [
        ("Global Investment Decision Book", F(bold=True, size=16)),
        (f"Week to {dec['as_of']} · Source: Interactive Brokers weekly closes via global-investment-research", F(color="5B6673")),
        ("", None),
        ("How to use", F(bold=True, size=12)),
        ("1. Enter your portfolio value on the Policy tab (yellow cell). Every Amount recalculates.", F()),
        ("2. Policy tab: blue numbers are editable limits; all sizes and limit prices are formulas that use them.", F()),
        ("3. Decisions tab: work the Accumulate rows first; pass the 5-question checklist before acting.", F()),
        ("4. SG Income tab: limit-order prices for 6%, 7% and 8% trailing yield; change the band on Policy.", F()),
        ("5. Decision Log tab: one row per decision (row 2 is a format example, delete it). Review monthly.", F()),
        ("", None),
        ("Colour legend", F(bold=True, size=12)),
        ("Blue text = input or engine output pasted as value · Black = formula · Green = link to another tab · Yellow fill = enter your own value", F()),
        ("", None),
        ("Limits", F(bold=True, size=12)),
        ("Mechanical price-trend, momentum and yield rules only: no valuation, fundamentals, costs or taxes. Not personalised advice.", F()),
        ("Full rules: docs/HANDBOOK.md in the repository.", F()),
    ]
    for r, (txt, font) in enumerate(lines, 1):
        c = rm.cell(row=r, column=1, value=txt)
        if font:
            c.font = font
    rm.column_dimensions["A"].width = 120

    # --- Policy -------------------------------------------------------------
    po = wb.create_sheet("Policy")
    widths(po, [34, 16, 70])
    header(po, 1, ["Setting", "Value", "Meaning"])
    policy = [
        ("Portfolio value (your base currency)", portfolio_value, "Enter your own figure", "#,##0", True),
        ("Risk budget this week", dec["posture"]["risk_budget"], dec["posture"]["risk_note"], "0%", False),
        ("Risk per position", D.RISK_PER_POSITION, "Loss if the exit level is hit, as share of portfolio", "0.0%", False),
        ("Single stock cap", D.MAX_POSITION, "Maximum weight per company", "0.0%", False),
        ("ETF / fund cap", D.MAX_POSITION_ETF, "Maximum weight per broad fund", "0.0%", False),
        ("Exit buffer below 40-week average", D.STOP_BUFFER, "Exit level = 40w average × (1 − buffer)", "0.0%", False),
        ("Income band low", 0.06, "Singapore income target: lower bound", "0.0%", False),
        ("Income band high", 0.08, "Singapore income target: upper bound", "0.0%", False),
        ("Options sleeve cap", OPT.SLEEVE_CAP, "Total option premium at risk, share of portfolio", "0.0%", False),
        ("Options per-trade risk", OPT.PER_TRADE_RISK, "Premium at risk per option trade", "0.0%", False),
        ("Gap allowance at the exit", D.GAP_ALLOWANCE, "Exits close beyond the level (mean 2.3% since 2008); added to the distance when sizing", "0.0%", False),
    ]
    for r, (k, v, note, fmt, user) in enumerate(policy, 2):
        po.cell(row=r, column=1, value=k).font = F()
        c = po.cell(row=r, column=2, value=v)
        c.font, c.number_format = F(color=BLUE), fmt
        if user:
            c.fill = yellow
        po.cell(row=r, column=3, value=note).font = F(color="5B6673")
    po["B3"].comment = Comment(f"Engine output for week to {dec['as_of']}: VIX-based.", "global-research")
    PV, RB, RPP, CAP_S, CAP_E, BUF, BAND_LO, BAND_HI, OPT_CAP, OPT_PT, GAP = (f"Policy!$B${i}" for i in range(2, 13))
    po.cell(row=14, column=1, value="Portfolio tilts this week").font = F(bold=True)
    for r, t in enumerate(dec["posture"]["tilts"], 15):
        po.cell(row=r, column=1, value=t["action"]).font = F()
        po.cell(row=r, column=3, value="Because: " + t["because"]).font = F(color="5B6673")

    # --- Decisions ------------------------------------------------------------
    ds = wb.create_sheet("Decisions")
    cols = ["Symbol", "Name", "Market", "Sector", "Type", "Decision", "Why", "Last", "Buy zone low", "Buy zone high",
            "40w average", "Exit level", "Revisit above", "Distance to exit", "Max weight", "Amount", "3m return",
            "1y return", "Momentum"]
    header(ds, 1, cols)
    widths(ds, [9, 30, 11, 22, 8, 12, 60, 11, 12, 12, 12, 11, 12, 11, 11, 13, 10, 10, 10])
    fills = {"Accumulate": "E3F1E8", "Hold": "FFFFFF", "Watch": "FFF4D6", "Avoid": "F9E1DF"}
    for r, c in enumerate(dec["calls"], 2):
        kind = "Stock" if c["kind"] == "stock" else "Fund"
        long_ma = (c["stop"] / (1 - D.STOP_BUFFER)) if c.get("stop") else None
        vals = [c["symbol"], c["name"], c["market"], c["sector"], kind, c["stance"], c["rule"], c["last"],
                c["entry"][0] if c.get("entry") else None, c["entry"][1] if c.get("entry") else None, long_ma]
        for i, v in enumerate(vals, 1):
            cell = ds.cell(row=r, column=i, value=v)
            cell.font = F(color=BLUE) if i >= 8 else F(bold=(i == 6))
        ds.cell(row=r, column=6).fill = PatternFill("solid", fgColor=fills[c["stance"]])
        ds.cell(row=r, column=12, value=f'=IF(K{r}="","",K{r}*(1-{BUF}))')
        ds.cell(row=r, column=13, value=c.get("trigger")).font = F(color=BLUE)
        ds.cell(row=r, column=14, value=f'=IF(L{r}="","",H{r}/L{r}-1)')
        ds.cell(row=r, column=15, value=(f'=IF(AND(F{r}="Accumulate",N{r}<>"",N{r}>0),'
                                         f'MIN({RPP}/(N{r}+{GAP}),IF(E{r}="Stock",{CAP_S},{CAP_E})),"")'))
        ds.cell(row=r, column=16, value=f'=IF(O{r}="","",O{r}*{RB}*{PV})')
        for i, key in ((17, "ret_3m"), (18, "ret_1y"), (19, "momentum")):
            ds.cell(row=r, column=i, value=c.get(key)).font = F(color=BLUE)
        for i in range(12, 17):
            ds.cell(row=r, column=i).font = F()
        for i, fmt in ((8, "#,##0.00"), (9, "#,##0.00"), (10, "#,##0.00"), (11, "#,##0.00"), (12, "#,##0.00"),
                       (13, "#,##0.00"), (14, "0.0%"), (15, "0.0%"), (16, "#,##0"), (17, "0.0%;(0.0%);-"),
                       (18, "0.0%;(0.0%);-"), (19, "0.00")):
            ds.cell(row=r, column=i).number_format = fmt
        ds.cell(row=r, column=7).alignment = Alignment(wrap_text=True, vertical="top")
        for i in range(1, len(cols) + 1):
            ds.cell(row=r, column=i).border = thin
    last_row = len(dec["calls"]) + 1
    ds.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{last_row}"
    s = last_row + 2
    ds.cell(row=s, column=1, value="Totals").font = F(bold=True)
    ds.cell(row=s, column=14, value="Accumulate weight").font = F(bold=True)
    ds.cell(row=s, column=15, value=f"=SUM(O2:O{last_row})").number_format = "0.0%"
    ds.cell(row=s, column=16, value=f"=SUM(P2:P{last_row})").number_format = "#,##0"
    ds.cell(row=s + 1, column=1, value=("Prices in each instrument's quote currency (London in GBP, HK in HKD, China A in CNY, "
                                        "SG in SGD). Amounts are in your base currency: convert before ordering.")).font = F(color="5B6673")

    # --- SG Income ---------------------------------------------------------------
    sg = wb.create_sheet("SG Income")
    icols = ["Symbol", "Name", "Price (SGD)", "Trailing cash (SGD)", "Trailing yield", "Fit", "Price for 6%", "Price for 7%",
             "Price for 8%", "Yield if cut 20%", "1y price", "Income decision", "Plan"]
    header(sg, 1, icols)
    widths(sg, [8, 34, 11, 13, 11, 14, 11, 11, 11, 12, 10, 14, 70])
    inc = [c for c in dec["calls"] if c.get("income")]
    inc.sort(key=lambda c: -(c["income"]["yield"] or 0))
    for r, c in enumerate(inc, 2):
        row = c["income"]
        cash = (row["yield"] or 0) * row["price"]  # reconciled to price units
        vals = [c["symbol"], c["name"], row["price"], cash]
        for i, v in enumerate(vals, 1):
            sg.cell(row=r, column=i, value=v).font = F(color=BLUE) if i >= 3 else F()
        sg.cell(row=r, column=4).comment = None
        sg.cell(row=r, column=5, value=f"=IF(C{r}>0,D{r}/C{r},\"\")")
        sg.cell(row=r, column=6, value=f'=IF(E{r}="","no data",IF(E{r}<{BAND_LO},"below band",IF(E{r}>{BAND_HI},"above band","in band")))')
        sg.cell(row=r, column=7, value=f"=D{r}/{BAND_LO}")
        sg.cell(row=r, column=8, value=f"=D{r}/(({BAND_LO}+{BAND_HI})/2)")
        sg.cell(row=r, column=9, value=f"=D{r}/{BAND_HI}")
        sg.cell(row=r, column=10, value=f"=E{r}*0.8")
        sg.cell(row=r, column=11, value=c.get("ret_1y")).font = F(color=BLUE)
        sg.cell(row=r, column=12, value=c["income_call"]["stance"]).font = F(bold=True)
        sg.cell(row=r, column=13, value=c["income_call"]["rule"]).alignment = Alignment(wrap_text=True, vertical="top")
        for i, fmt in ((3, "0.000"), (4, "0.0000"), (5, "0.00%"), (7, "0.000"), (8, "0.000"), (9, "0.000"),
                       (10, "0.00%"), (11, "0.0%;(0.0%);-")):
            sg.cell(row=r, column=i).number_format = fmt
    sg.cell(row=len(inc) + 3, column=1, value=("Trailing cash = distributions with ex-date in the 365 days to the as-of date, from IBKR "
                                               "corporate actions, cross-checked to IBKR dividend yield.")).font = F(color="5B6673")

    # --- FX ------------------------------------------------------------------------
    fx = wb.create_sheet("FX")
    header(fx, 1, ["Pair", "Trend", "Read", "3m change"])
    widths(fx, [12, 10, 40, 12])
    for r, f in enumerate(dec["fx"], 2):
        for i, v in enumerate([f["pair"], f["state"], f["view"], f["ret_3m"]], 1):
            fx.cell(row=r, column=i, value=v).font = F(color=BLUE) if i == 4 else F()
        fx.cell(row=r, column=4).number_format = "0.0%;(0.0%);-"

    # --- Options sleeve ---------------------------------------------------------------
    if opt and opt["ideas"]:
        op = wb.create_sheet("Options")
        ocols = ["Underlying", "Structure", "Legs", "Expiry", "Days", "Spot", "Debit / share", "Cost per contract",
                 "Breakeven", "Move to breakeven", "Max gain / share", "Reward : risk", "Prob. of profit", "IV",
                 "Contracts (per-trade budget)", "Premium at risk", "Stop: close at value"]
        header(op, 1, ocols)
        widths(op, [10, 16, 52, 11, 6, 10, 11, 12, 10, 11, 12, 10, 10, 8, 12, 13, 13])
        for r, i in enumerate(opt["ideas"], 2):
            legs = " / ".join(f"{'Buy' if l['qty'] > 0 else 'Sell'} {l['strike']:g}{l['right']}" for l in i["legs"])
            for col, v in enumerate([i["symbol"], i["structure"], legs, i["expiry"], i["days"], i["underlying"], i["debit"]], 1):
                op.cell(row=r, column=col, value=v).font = F(color=BLUE) if col >= 5 else F()
            op.cell(row=r, column=8, value=f"=G{r}*100")
            op.cell(row=r, column=9, value=i["breakeven"]).font = F(color=BLUE)
            op.cell(row=r, column=10, value=f"=I{r}/F{r}-1")
            op.cell(row=r, column=11, value=i["max_gain"]).font = F(color=BLUE)
            op.cell(row=r, column=12, value=f'=IF(K{r}="","open",K{r}/G{r})')
            op.cell(row=r, column=13, value=i["prob_profit"]).font = F(color=BLUE)
            op.cell(row=r, column=14, value=i["iv"]).font = F(color=BLUE)
            op.cell(row=r, column=15, value=f"=INT({PV}*{OPT_PT}/H{r})")
            op.cell(row=r, column=16, value=f"=O{r}*H{r}")
            op.cell(row=r, column=17, value=f"=G{r}*(1-{OPT.STOP_LOSS})")
            for col, fmt in ((6, "#,##0.00"), (7, "0.00"), (8, "#,##0"), (9, "#,##0.00"), (10, "0.0%;(0.0%);-"),
                             (11, "0.00"), (12, "0.0x"), (13, "0%"), (14, "0%"), (16, "#,##0"), (17, "0.00")):
                op.cell(row=r, column=col).number_format = fmt
        n = len(opt["ideas"]) + 1
        op.cell(row=n + 2, column=1, value="Sleeve check").font = F(bold=True)
        op.cell(row=n + 2, column=15, value="Total if all taken").font = F(bold=True)
        op.cell(row=n + 2, column=16, value=f"=SUM(P2:P{n})").number_format = "#,##0"
        op.cell(row=n + 3, column=15, value="Sleeve cap").font = F(bold=True)
        op.cell(row=n + 3, column=16, value=f"={PV}*{OPT_CAP}").number_format = "#,##0"
        op.cell(row=n + 4, column=15, value="Within cap?").font = F(bold=True)
        op.cell(row=n + 4, column=16, value=f'=IF(P{n+2}<=P{n+3},"Yes","No: pick fewer trades")')
        op.cell(row=n + 6, column=1, value=("Probability of profit is risk-neutral, from the option's implied volatility. Debits are "
                                            "mid prices from IBKR weekend quotes; re-price live before trading. Defined risk only: "
                                            "maximum loss = premium paid.")).font = F(color="5B6673")

    # --- Decision Log --------------------------------------------------------------
    lg = wb.create_sheet("Decision Log")
    lcols = ["Date", "Symbol", "Decision", "Price", "Weight", "Exit level", "Assumption (must be true)",
             "Strongest counter-case", "Pre-mortem: top reason it fails", "Review date", "Outcome (fill at review)"]
    header(lg, 1, lcols)
    widths(lg, [11, 9, 12, 10, 9, 10, 40, 40, 40, 11, 30])
    ex = ["2026-10-12", "XLE", "Accumulate", 65.08, 0.029, 55.59, "Oil stays in up-trend through Q1 2027",
          "OPEC supply increase reverses oil", "Demand slowdown cuts oil 20%", "2026-12-12", ""]
    for i, v in enumerate(ex, 1):
        c = lg.cell(row=2, column=i, value=v)
        c.font, c.fill = F(italic=True, color="5B6673"), yellow
    lg["A2"].comment = Comment("EXAMPLE ROW showing the format. Replace with your own decisions.", "global-research")
    lg.cell(row=2, column=5).number_format = "0.0%"
    dv = DataValidation(type="list", formula1='"Accumulate,Hold,Watch,Avoid"', allow_blank=True)
    lg.add_data_validation(dv)
    dv.add("C2:C500")

    wb.active = 2
    path = Path(path)
    wb.save(path)
    return path
