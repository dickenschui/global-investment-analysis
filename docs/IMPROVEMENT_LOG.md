# Improvement Log

The system must get better every week. Each weekly run answers the self-review questions
below, appends an entry here and ships **one** improvement (code, data or rule) in its pull request.

## Input scorecard

| Input | Status | Why it matters | Source to fix it | Priority |
|---|---|---|---|---|
| Weekly prices, 96 instruments, 5 markets | **Have** (IBKR) | Trend, momentum, risk | IBKR connector | — |
| Option chains, IV and greeks | **Have** (IBKR, 11 underlyings) | Options sleeve | IBKR connector | — |
| SG distributions, 1 year | **Partial** | Income stability needs 3–5 years of history | IBKR corporate actions with a longer period | High |
| Earnings dates | **Missing** | An option held through earnings carries a hidden volatility event | Bigdata.com corporate calendar (needs credit) | **Critical** for options |
| Macro calendar (CPI, payrolls, central banks) | **Missing** | Hourly alerts should know when a release is due | Bigdata.com economic calendar (needs credit) | High |
| Fundamentals: valuation, margins, FCF, estimates | **Missing** | Trend calls can hide stretched valuations | Financial Datasets (needs credit) or company filings | High |
| Interest rates and curve (2y/10y, policy rates) | **Proxy only** (TLT/IEF prices) | Rates drive REITs, growth and FX | Financial Datasets interest rates, or FRED (blocked) | Medium |
| Credit spreads | **Missing** | Early warning of risk-off | Add HYG and LQD to the universe (IBKR) | Medium, cheap |
| Breadth beyond the monitored names | **Partial** | 9–15 names per market is a thin breadth sample | Add equal-weight ETFs (RSP) and advance/decline via IBKR | Medium |
| News and sentiment | **Missing** | Explains exceptions in hourly alerts | Bigdata.com search/sentiment (needs credit) | Medium |
| Positioning: short interest, futures positioning (COT), put/call ratios | **Missing** | Crowding risk | IBKR option volumes; CFTC (blocked) | Low |
| Longer price history (5–10 years) | **Missing** | Rules are untested beyond one year | IBKR FIVE_YEARS weekly bars | **High**: needed to backtest the rules |
| Total return (dividends in) | **Missing** for equities | Understates income names | IBKR corporate actions for all stocks | Medium |
| Live and delayed quotes | **Delayed / frozen at weekends** | Option prices must be re-checked live | IBKR market-data subscription | Note only |

## Weekly self-review questions

1. **Accuracy:** did any price, yield or option mid fail a cross-check (snapshot vs weekly close, Black-Scholes vs mid, vendor yield vs computed)?
2. **Calls vs outcomes:** in `global-research review`, which calls worked or failed, and did a rule misfire more than twice?
3. **Blind spots:** which exception or market move this week did the system miss, and what input would have caught it?
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
