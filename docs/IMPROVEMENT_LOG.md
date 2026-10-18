# Improvement Log

The system must get better every week. Each weekly run answers the self-review questions
below, appends an entry here and ships **one** improvement (code, data or rule) in its pull request.

## Input scorecard

| Input | Status | Why it matters | Source to fix it | Priority |
|---|---|---|---|---|
| Weekly prices, 96 instruments, 5 markets | **Have** (IBKR) | Trend, momentum, risk | IBKR connector | — |
| Option chains, IV and greeks | **Have** (IBKR, 11 underlyings) | Options sleeve | IBKR connector | — |
| SG distributions | **Added 2026-10-11**: 4–5 years per name from IBKR corporate actions (`data/history/sg_distributions_5y.json`) | Sustainable yield and payout record | Refresh quarterly with the same call | — |
| Earnings dates | **Missing** | An option held through earnings carries a hidden volatility event | Bigdata.com corporate calendar (needs credit) | **Critical** for options |
| Macro calendar (CPI, payrolls, central banks) | **Missing** | Hourly alerts should know when a release is due | Bigdata.com economic calendar (needs credit) | High |
| Fundamentals: valuation, margins, FCF, estimates | **Missing** | Trend calls can hide stretched valuations | Financial Datasets (needs credit) or company filings | High |
| Interest rates and curve (2y/10y, policy rates) | **Added 2026-10-11**: 3m, 5y, 10y, 30y Treasury yields and VIX3M from IBKR (weekly since 2009); monitor reads curve shape, term premium and volatility term structure. 2-year and policy rates still missing | Rates drive REITs, growth and FX | FRED/ECB/BoE via GitHub Actions (`macro-daily.yml`) | Medium |
| Credit spreads | **Added 2026-10-11** (HYG vs LQD reading and tilt) | Early warning of risk-off | IBKR | — |
| Breadth beyond the monitored names | **Partial** | 9–15 names per market is a thin breadth sample | Add equal-weight ETFs (RSP) and advance/decline via IBKR | Medium |
| News and sentiment | **Missing** | Explains exceptions in hourly alerts | Bigdata.com search/sentiment (needs credit) | Medium |
| Positioning: short interest, futures positioning (COT), put/call ratios | **Missing** | Crowding risk | IBKR option volumes; CFTC (blocked) | Low |
| Longer price history | **Full universe 2026-10-11**: all 98 instruments, up to 1,000 weekly bars each, plus the 13-week T-bill (IRX) | Backtest through 2008, 2011, 2018, 2020 and 2022 | IBKR `step_count` weekly bars | — |
| Total return (dividends in) | **Missing** for equities | Understates income names | IBKR corporate actions for all stocks | Medium |
| Live and delayed quotes | **Delayed / frozen at weekends** | Option prices must be re-checked live | IBKR market-data subscription | Note only |

## Weekly self-review questions

1. **Accuracy:** did any price, yield or option mid fail a cross-check (snapshot vs weekly close, Black-Scholes vs mid, vendor yield vs computed)?
2. **Calls vs outcomes:** in `global-research review`, which calls worked or failed, and did a rule misfire more than twice?
3. **Blind spots and alert value:** which exception or market move did the system miss? Run `global-research alert-review` on last week's alerts (first `git fetch origin alert-state && git checkout origin/alert-state -- reports/<last week>/alerts`): how many alerts were verified at the close, how many were acted on, how many were duplicates, and which coverage gap ranks first?
4. **Concentration:** is the Accumulate list over 30% in one sector or one driver (oil, AI capex, rates)?
5. **Options:** did any idea span an earnings date, or have quotes too wide to trade?
6. **Coverage:** which instruments had no data or stale data, and why?
7. **Improvement:** what is the single highest-value improvement for next week, taken from the scorecard?

## Entries

### 2026-10-11 (initial build)
- **Accuracy:** 96/96 instruments collected. Snapshot prices are within 10% of weekly closes (London ×100 for pence). Option mids are within a few percent of Black-Scholes. Singapore yields reconcile to the 9 Oct Codex session.
- **Defects fixed:** misaligned weekly stamps between FX and ETFs (Monday vs Tuesday holidays); a 2-year history for SLV; 1-year returns on 52-bar series; long options ranked on tail moves.
- **Blind spots:** no earnings dates, macro calendar or fundamentals; Bigdata.com and Financial Datasets both report zero credit.
- **Next improvement:** fetch 5-year weekly history and backtest the Accumulate/Avoid rules before trusting them further.

### 2026-10-11 (backtest added)
- **Shipped:** 5-year weekly history for 36 instruments (IBKR), with the last 52 weeks matching the weekly snapshots exactly; walk-forward backtest and Rule Evidence page; HYG/LQD credit reading.
- **Finding:** the trend rules did not work in Oct 2022 – Oct 2026. Accumulate calls lagged the average instrument by 0.6% over 13 weeks and Avoid calls led it by 1.6%. The rule strategy returned 9.9% a year against 18.0% for always-invested equal weight, with a deeper drawdown (−16.3% vs −9.0%). Up-trend signals helped in only 6 of 36 instruments.
- **Alternatives:** a contrarian rule (price below its 40-week average) helped in 32 of 36 instruments and halved drawdown, but beat equal weight only in the first half. No rule beats equal weight in both halves, so none is adopted.
- **Action taken:** rule confidence is shown as LOW on the Desk, Brief and digests: use exit levels for risk control, keep a diversified core, and size new positions at half the Max size.
- **Open question for the owner:** switch the Accumulate rule to a pullback or contrarian entry? That is not done automatically, because four years of mostly rising markets favour mean reversion and could reverse.
- **Next improvement:** extend the history to ten years so it covers a trending bear market (2008-style or 2022), then re-test.

### 2026-10-11 (19-year re-test)
- **Shipped:** 19-year weekly history for the same 36 instruments (IBKR, `data/history/weekly_long`). Every one of the 261 overlapping weeks per instrument matches the 5-year file exactly. Test window starts January 2007 (before that only 6–8 instruments have data); results run 2008-01-04 to 2026-10-09. One bad print removed in the test only (RIO 2007-06-08 at 0.35 between 36.84 and 39.16); raw file kept as returned.
- **Current rule:** fails. Following the calls returned 5.8% a year against 8.7% for equal weight (Sharpe 0.47 vs 0.63), lost in both halves and two of three thirds; active-return t −0.92. It did cut the worst fall (−36% vs −46.5%), which is why exit levels stay as risk control.
- **Alternatives:** contrarian, the 5-year favourite, fails over 19 years (0 of 3 thirds, worst fall −48%), which confirms why it was not adopted last time. The pullback rule (12.1% a year, Sharpe 0.78) passes the halves test but loses 2014–2020, holds a median of 4 names, sits entirely in cash for 109 weeks and has active t 0.98. The long-trend filter "passes" the first half by 0.003 of Sharpe and has active t −0.17.
- **Rule tightened (not loosened):** a rule is adopted only if it beats equal weight on Sharpe in both halves AND all three thirds AND has active t ≥ 2 (`backtest.ADOPTION_T`). MEDIUM confidence now also requires the rule portfolio to beat equal weight risk-adjusted; good cross-sectional ranking alone (+0.2% vs −0.1% per 13 weeks) is not enough. No rule passes, so the live rule is unchanged and confidence stays LOW.
- **Gap fixed:** the weekly routine never re-ran the backtest, so the confidence note would have disappeared from next week's pack. The routine now runs `backtest` before `monitor`.
- **Next improvement:** the rules use price trend only and none beats equal weight. The highest-value next input is fundamentals or valuation (Financial Datasets credit), tested on the same walk-forward basis, plus a cash yield so time in cash is not scored at 0%.

### 2026-10-11 (revalidation and blind-spot fixes)
Two independent reviews: a code and methodology audit of this repo, and a comparison with the ChatGPT/Codex app (`dickenschui/global-investment`, global-investment.streamlit.app).
- **Proven bugs fixed (each with a test):**
  - Hourly alerts compared London quotes in pence with exit levels in pounds, so UK exits could never fire and 12 UK names raised false "revisit" alerts every hour. Quotes are now put in the units of the weekly levels.
  - BA., RR. and NG. were treated as FX pairs; FX now comes from the universe.
  - Frozen quotes are set aside as stale.
  - The live trend used a 41-week average while every document and the backtest used 40; live and backtest calls now agree on 99.8% of 24,292 weekly calls (98.8% before).
  - The 5% options sleeve was not enforced (16 ideas = 15% of the portfolio); now one idea per underlying, within the cap (4.75% this week).
  - The "significant" flag ignored the sign of t.
  - Data gaps were booked as one-week returns.
  - The Brief and Telegram ignored the risk budget.
  - The UK page mislabelled pounds as pence.
  - The app ignored the risk budget.
  - REIT distribution components on one ex-date are one payment.
- **Data:**
  - Long history now covers all 98 instruments; every overlapping week matches this week's snapshots exactly.
  - Four 2007 London unit errors are dropped automatically. Permanent pence/pound level breaks drop earlier history, and the default start is 2007.
  - The Rolls-Royce October 2020 rights-issue window is excluded as a documented exception.
- **Model blind spots fixed:**
  - The backtest now also simulates the Desk as it is run: risk-sized buys on Accumulate, hold to the exit, T-bill cash, 10 bp costs. It is compared with the same exposure spread over everything, at the signal close and a week later, at full and half size.
  - Exits closed a mean 2.3% beyond the level over 662 exits, so sizing now adds a 2% gap allowance.
  - The SG income screen uses the lower of the 12-month and four-year-average yields; AJBU leaves the band (6.21% to 4.97%).
  - Option ideas carry an earnings note and never an invented date. The paid calendars still have no credit.
  - A portfolio check reports correlated clusters, gross exposure, risk to exits and true volatility.
  - The base currency is now GBP from the owner's confirmed mandate (`config/investor.json`), and the FX advice was rewritten for it.
- **Findings (83 rated instruments, 2008–2026):**

  | Version | Return a year | Sharpe | Worst fall |
  |---|---|---|---|
  | Desk, full size | 7.3% | 0.62 | −21.4% |
  | Same exposure, no selection | 7.4% | 0.63 | −22.2% |
  | Desk, trading a week later | 7.2% | 0.59 | −26.5% |
  | Desk, half size | 5.9% | 0.67 | −15.7% |
  | Always-invested equal weight | 9.9% | 0.69 | −43.9% |

  - The exits cut the 2008-style drawdown roughly in half; name selection adds nothing.
  - At full size the worst fall breaches the owner's 20% tolerance; half size fits it.
- **Tested and rejected:** capping correlated clusters at 1% risk lowered the Sharpe ratio in both halves (0.57 vs 0.62), so the check reports clusters but does not cut sizes. No entry variant passes the adoption test, so confidence stays LOW.
- **From the ChatGPT app, adopted:** GBP-primary mandate and drawdown tolerance; stale-quote gating.
- **From the ChatGPT app, still to adopt:**
  - A delivery path that does not depend on Claude connectors: its GitHub Actions email already works, while our routines still have no connectors attached.
  - Hash manifests for snapshot files.
  - Primary-source RSS headline context for urgent alerts.
  - A whole-portfolio stress test calibrated from our 19-year history.
- **Next improvement:** attach the connectors (owner), then add a SHA-256 manifest written at collection time and checked when loading.


### 2026-10-11 (ChatGPT app improvements adopted; new data sources)
- **Adopted from the ChatGPT app, rebuilt and tested here:**
  - Delivery without Claude connectors: `hourly-alerts.yml` (Yahoo pulse, Gmail SMTP, optional Telegram) and `macro-daily.yml` on GitHub Actions.
  - Condition-state alerting: a notice goes out when a condition first appears or worsens, re-arms only when a fresh quote shows it has cleared, and stays pending until a channel confirms delivery. A 90-minute freshness gate and a heartbeat warning cover the monitor's own gaps (not overnight or weekends).
  - SHA-256 manifests: the loader now refuses a snapshot whose files changed after collection. Contract identity checks and a weekly quality review feed questions 1 and 6 of this log.
  - Primary-source headlines (Fed, BLS, BEA, BoE, ECB and others) attached to URGENT alerts as context, with XML entity declarations refused.
  - Whole-portfolio stress test from our own 19-year history in GBP (see below), and an option-chain check plus exact payoff cross-check before an idea is shown.
  - Journal: a required invalidation condition, Open/Closed status, and every revision kept.
- **Fixed rather than copied:** their Treasury URL typo, a shared concurrency group across workflows, checks written as string literals, and use of unadjusted closes. Our heartbeat counts only missed scheduled runs, so overnight and weekend breaks are not reported as outages.
- **New data:**
  - IBKR CBOE indices for 5, 10 and 30-year Treasury yields and VIX3M (weekly since 2009). The monitor now reads curve shape (10y−3m +1.19 pts, steepening), term premium (30y−5y +0.58 pts) and volatility term structure (VIX/VIX3M 0.84, contango).
  - 30 official series configured (FRED rates, spreads, inflation, claims and financial conditions; ECB FX; Bank Rate; ONS CPI and CPIH; Treasury par curve; World Bank GDP). These are fetched on GitHub Actions because they are blocked here. All are `verified: false` until the first run.
- **Stress test this week (Accumulate+Hold at max size, 74.2% gross, GBP):**

  | Episode | Full size | Half size | Verdict |
  |---|---|---|---|
  | GFC | −17.6% | −8.8% | none: 13% of the book had no history |
  | Euro crisis | −4.4% | −2.2% | none: same gap |
  | China/oil | −4.8% | −2.4% | within 20% |
  | Covid crash | −14.0% | −7.0% | within 20% |
  | 2022 rates shock | −1.5% | −0.7% | within 20% |

  The SG income sleeve lost 18–25% of capital in GBP in the China/oil and Covid windows. With a 20% payout cut, its income fell 14–18%. A stronger SGD cushioned 2022.
- **Chain check:** 154 contracts were checked and 1 issue found (FXI 2027-01-15 31P: an ask with no bid). No idea used it.
- **First live runs on GitHub Actions (same day):**
  - **Macro:** 27 of 30 official series fetched and parsed: FRED, ECB, Bank of England, ONS, the Treasury par curve and World Bank China and US. These are now `verified: true`.
  - **World Bank:** Singapore, Hong Kong and UK GDP timed out on the first run. With one retry, the second run fetched all 30 of 30 series, and every source is now verified.
  - **Cross-check:** the Treasury par curve agrees with the IBKR indices (10y 5.24% vs 5.244%, 30y 5.60% vs 5.600%). The 3-month rates differ by basis: the IBKR T-bill discount rate is 4.06%, FRED constant maturity 4.23%. The curve reading now names its basis.
  - **Yahoo:** 69 of 69 quotes returned. On a Sunday all were correctly set aside as stale, so nothing fired. State is kept on the `alert-state` branch. Email was skipped until the mail secrets are added.
  - **New official readings:**
    - US high-yield spread tight at 3.15 pp but widening (+0.45 over 3 months).
    - US CPI 3.4% y/y (Aug).
    - UK Bank Rate 3.75% with CPIH 3.3%, so the real rate is +0.45 pp.
    - Jobless claims falling 9.7% over 13 weeks.
    - Chicago Fed financial conditions looser than average.
  - The monitor never reads an official observation dated after the snapshot week.
- **Limits:**
  - News feeds have not run live yet: they are fetched only when an URGENT alert fires.
  - HKMA, MAS and SingStat are not configured; the endpoints were not confirmed or need keys.
  - Stress returns are price-only weekly closes with weights held fixed.
- **Next improvement:** merge, add the mail secrets, run both workflows by hand, and set `verified: true` source by source from the run log.

### 2026-10-18 (alert value, duplicates, provenance and coverage)

- **Measured** (`reports/2026-10-11/alert-review/`, levels of 9 October, week to 16 October; closes from IBKR):
  - 25 of 315 scheduled checks ran (8%). Yahoo returned every quote it was asked for (69 of 69 when markets were
    open); the long "No live quote" lists were closed markets, not data gaps.
  - 13 market conditions produced 15 notices: 2 duplicates (LSEG and ISF crossed their revisit levels, fell back and
    were re-sent later in the week). The heartbeat gap was sent twice on 12 October.
  - At the Friday close 9 of 13 were verified and 4 refuted. Revisit triggers: 6 of 9 verified (LSEG, ISF, SSE, 857,
    000333, 823); DBA, UNG and U11 fell back. One-day moves: TLT, XLV and the VIX spike held; USD/SGD fully reversed.
  - Only 5 market alerts were pushed (3 verified). The 8 INFO revisit alerts were logged but never pushed, by design,
    and 6 of them were verified. Nothing was acted on: the decision log is empty, and the weekly refresh that would
    act on confirmed revisit levels did not run on 17 October (the routine still has no repository attached).
  - No alert carried a headline: context was attached to URGENT alerts only, and none fired. The BLS feed failed 11 of
    11 fetches; the reason did not record the HTTP status.
- **Shipped:**
  - `global-research alert-review`: verifies each alert at the official close, counts duplicates, pushes and
    decisions logged, and ranks coverage gaps.
  - Repeats within the desk week (one-day moves: the same day) are logged, not re-sent; a higher severity still is.
  - One message per run: each symbol once, grouped by what to do, monitor notices apart, closed markets counted not
    listed. Prices are labelled as delayed vendor quotes and headlines as unverified keyword matches.
  - Headline context now goes on WARN alerts too. News failures record the HTTP status.
  - Run records hold `pushed`, `repeats`, `no_quote`, `not_live` and each alert's quote and reference, so next week's
    one-day moves are scored automatically.
  - A "Weekly levels stale" monitor notice fires once levels are more than 9 days old.
- **Coverage gaps, in priority order:**
  1. Checks that did not run (92% of slots). Fix: external scheduler calling workflow_dispatch (owner action).
  2. Weekly refresh not running, so from 19 October every level is a week old. Fix: attach the repository to the
     routine (owner action).
  3. No headline context on review alerts. Fixed in code; measure again next week.
  4. BLS feed. Next week's runs will name the HTTP status; then fix or replace it.
  5. No official source for MAS, SORA, HIBOR or SingStat CPI.
- **Limits:** "acted" can only be measured if decisions are written to `config/decision-log.csv`; the app's journal
  stays in the browser. The one-day moves above were reconciled by hand from the rounded % in the alert text.
