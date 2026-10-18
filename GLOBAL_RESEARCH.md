# Global Investment Research

Python package `global_investment_research` (CLI: `global-research`) for monitoring and researching
HK, China, SG, UK and US equities alongside sectors, gold, commodities, rates and FX.

It sits next to the original Taiwan three-dimension analysis skill (`SKILL.md`, `scripts/`), which is unchanged.

## What it does

| Area | Module | Output |
|---|---|---|
| Daily/weekly monitor: market proxies, breadth, US sector rotation, cross-asset trends | `monitor.py` | `monitor.html`, `monitor.json` |
| Macro backdrop: rule-based readings (volatility, dollar, rates, gold, oil, cyclicals vs defensives, copper vs gold, Asia vs US), each with its evidence | `monitor.regime_readings` | in `monitor.html` |
| Pattern study: market-vs-macro-factor correlations, labelled *stable* only if both halves of the sample agree | `analytics.split_correlation` | in `monitor.html` |
| Learning across runs: each run appends to `history.jsonl`; the next report lists readings that flipped | `monitor.append_history`, `regime_changes` | "What changed" section |
| Macro event linkage: market reaction in the bar after each event you log in `config/macro-events.csv` | `monitor.event_reactions` | in `monitor.html` |
| US tech watchlist, UK equities + FX, Singapore 6–8% income screen | `reports.py`, `income.py` | `us-tech.html`, `uk-fx.html`, `sg-income.html` |
| Minimum-variance allocation, Ledoit-Wolf covariance, fitted on training data only, evaluated after allocation | `portfolio.py` | JSON |
| Rule evidence: walk-forward test of the Accumulate/Avoid rules and alternatives on 19 years of IBKR weekly closes (`data/history/weekly_long`, 36 instruments); a new rule is adopted only if it beats equal weight in both halves, all three thirds and with active t ≥ 2 | `backtest.py`, `evidence.py` | `backtest.json`, `evidence.html`, confidence note on Desk/Brief |
| Investment Desk app: one page with the week's calls, live read-only IBKR checks against exit levels and buy zones, position sizing, SG income, options sleeve, rule evidence, "challenge this trade" and a shared decision journal | `app/index.html`, `app_data.py`, `docs/APP_UPDATE.md` | https://claude.ai/artifact/G7LaSQYJscDLYKNAFNu4Fn |
| Curve and volatility term structure: 3m/5y/10y/30y Treasury yields and VIX3M from IBKR (weekly since 2009); curve shape, term premium, VIX/VIX3M | `curve.py` | in `monitor.html` macro readings |
| Official macro data: FRED, ECB, Bank of England, ONS, US Treasury, World Bank, fetched daily on GitHub Actions (blocked inside Claude); readings join the monitor when the snapshot is under 10 days old | `macro_sources.py`, `.github/workflows/macro-daily.yml` | `data/macro/latest.json` |
| Stress test: this week's Accumulate+Hold book at full and half size through the GFC, euro crisis, China/oil, Covid and 2022 in GBP against the 20% mandate limit; SG income sleeve under measured ES3 and SGD shocks | `stress.py` | Desk, app |
| Hourly alerts without Claude: Yahoo pulse, condition state (notify on new or escalated, re-arm only when quoted, retry undelivered), 90-minute freshness gate, heartbeat gap and stale-levels warning, repeats within the desk week not re-sent, one consolidated message per run with every line labelled (delayed vendor quote, unverified headline), official headlines on URGENT and WARN alerts, Telegram and Gmail | `alerts.py`, `alert_state.py`, `pulse_yahoo.py`, `news.py`, `mailer.py`, `.github/workflows/hourly-alerts.yml` | state on the `alert-state` branch |
| Weekly alert scorecard: each alert verified or refuted at the official close, duplicates, pushes, decisions logged, and coverage gaps ranked | `alert_review.py` (`global-research alert-review`) | `reports/<date>/alert-review/` |
| Data integrity: SHA-256 manifests (loader refuses changed files), contract identity (venue, currency, type, contract id), weekly quality review | `integrity.py`, `identity.py`, `quality.py` | `quality.md` for the Improvement Log |
| Option chain check (crossed or one-sided quotes, bad IV, expired, duplicates) and exact expiry payoff cross-check before an idea is shown | `payoff.py` | Options page, app |
| Company fundamentals: margins, FCF, cash conversion, ROE on average equity, accounting-equation check, P/E withheld on currency mismatch | `company.py` | library |

## Data sources

Universe: `config/universe.json` (96 instruments). Loaders all return the same shape, so analytics do not depend on the source.

1. **IBKR snapshot** (`--snapshot DIR`): JSON files collected through the Interactive Brokers connector, following
   `docs/DATA_COLLECTION.md`. This is the source that works inside the Claude cloud environment.
2. **CSV** (`--csv DIR`): one `<symbol>.csv` per instrument with `date,close`.
3. **Yahoo** (`--yahoo`): needs `pip install -e .[yahoo]` and network access to Yahoo Finance.

Futures are tracked through exchange-traded proxies (GLD, SLV, CPER, USO, UNG, DBA) to avoid contract-roll gaps.

## Commands

```bash
python -m venv .venv && .venv/bin/pip install -e .[test]
.venv/bin/global-research monitor --snapshot data/snapshots/2026-10-11 --out reports/2026-10-11 --state-dir data/state
.venv/bin/global-research portfolio --snapshot data/snapshots/2026-10-11 --symbols SPY TLT GLD --max-weight 0.6
.venv/bin/global-research backtest --history data/history/weekly_long --start 2007-01-01 --out reports/2026-10-11  # before monitor
.venv/bin/global-research manifest data/snapshots/2026-10-11            # after every file is collected (--verify to check)
.venv/bin/global-research quality --snapshot data/snapshots/2026-10-11 --previous data/snapshots/<last week> --out reports/2026-10-11/quality
.venv/bin/global-research macro --config config/macro-sources.json --out data/macro   # needs open internet (GitHub Actions)
.venv/bin/global-research pulse --yahoo --reports reports/2026-10-11 --out pulse.json
.venv/bin/global-research alerts --pulse pulse.json --reports reports/2026-10-11 --state-dir data/state [--news] [--telegram] [--email]
.venv/bin/global-research demo --out /tmp/demo     # synthetic data, offline
.venv/bin/python -m pytest -q
```

## GitHub Actions setup

| Workflow | When | Needs |
|---|---|---|
| `tests.yml` | every push and pull request | nothing |
| `macro-daily.yml` | 06:30 UTC weekdays with backups at 09:10 and 13:40, or by hand | nothing (public sources); commits `data/macro/` |
| `hourly-alerts.yml` | every 20 minutes 01:07–21:47 UTC weekdays (GitHub drops some scheduled runs; extra runs never repeat a notice), or by hand | repository secrets `MAIL_USERNAME` (your Gmail address), `MAIL_APP_PASSWORD` (a Gmail app password, not your login password), `MAIL_TO`; optional `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |

Add secrets under the repository's Settings → Secrets and variables → Actions. To check them, run "Hourly alerts" by hand
(Actions → Hourly alerts → Run workflow) with "Send a test message" ticked: it sends one test email and Telegram message
and reports each channel in the log. For Telegram, create a bot with @BotFather, send it any message, and use the bot token
and your chat id as `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Scheduled runs fire only from the default
branch, so they start once this work is merged. Every source in `config/macro-sources.json` and `config/news-sources.json`
is marked `verified: false` until a run parses it cleanly.

## Method and limits

- Returns are price returns in each instrument's quote currency; dividends are excluded except in the income screen.
- Trend = price vs 10- and 40-week moving averages (the weekly equivalent of 50/200-day).
- Momentum score = average of 3-month and 1-year return ÷ annualised volatility.
- Correlation is not causation, and one year of weekly data (~52 points) is a small sample. The stability test guards against
  over-reading chance patterns; it is not proof of a durable relationship.
- Fundamentals were not refreshed in the 2026-10-11 run: the Financial Datasets connector had no credit and filings sites are
  blocked from the cloud environment. Theses carried over from the 9 Oct Codex session are shown as "to verify".
- Research output only; not investment advice.

## Decision Desk and Handbook

`global-research monitor` also writes `decision-desk.html` and `decisions.json`. Every instrument gets one of four calls
(Accumulate, Hold, Watch, Avoid). Each call states the rule behind it, a buy zone, an exit level or revisit trigger, and a
volatility-based maximum size. The desk also gives the week's risk budget and macro tilts, a Singapore income desk with staged
limit prices, an FX hedging view and a five-question pre-trade checklist.

The operating rules are in [docs/HANDBOOK.md](docs/HANDBOOK.md), regenerated by `global-research handbook` from the same
policy constants the engine uses. Log decisions in `config/decision-log.csv`, then score them with
`global-research review --snapshot <dir>`.

## Delivery and hourly alerts

- `global-research monitor` also writes `email.html` (inline-styled digest) and `telegram.txt`.
- `global-research notify --reports <dir>` sends the Telegram digest when `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_CHAT_ID` are set (and `api.telegram.org` is reachable); otherwise it skips with a message.
- `global-research watchlist --reports <dir>` lists the symbols the hourly pulse must quote.
- `global-research alerts --pulse pulse.json --reports <dir> [--telegram]` reports new exceptions only:
  exit breaches, revisit triggers, moves of 2.5σ (WARN) and 4σ (URGENT) against each instrument's own
  volatility, VIX ≥ 25 or a +20% spike, FX moves ≥ 1.5%, USD/HKD outside 7.75–7.85, and option ideas moving
  against. Each condition is notified once, again only if its severity rises. A condition that clears and fires again
  is logged under `repeats` and not re-sent: within the same desk week for level rules, the same UTC day for one-day
  moves. A condition still active into a new day (moves) or new weekly levels (level rules) is announced again. Each run writes `alerts/alerts-<time>.json` with what was sent, what was actually pushed (INFO-only runs are
  logged, not pushed), repeats, `no_quote` (the vendor returned nothing: a coverage gap), `not_live` (market closed)
  and news failures with their HTTP status.
- Alert messages group each symbol once under "Act now" (URGENT), "Review today" (WARN) or "For the weekly close"
  (INFO), with monitor notices apart. Prices are labelled as delayed vendor quotes, unconfirmed until the official
  close; headlines as unverified keyword matches on official feeds.
- `global-research alert-review --reports reports/<last week> --snapshot data/snapshots/<this week> --out
  reports/<this week>/alert-review` scores last week's alerts. An alert is verified when the official week-end close
  is still past its level (or kept at least half of a one-day move), and acted on only when `config/decision-log.csv`
  records a decision on the name within 7 days. It also counts duplicates and pushes, and ranks coverage gaps:
  checks that did not run, stale levels, missing quotes, failing feeds, review alerts without headline context, and
  markets with no official source.
- `docs/IMPROVEMENT_LOG.md` holds the input scorecard and the weekly self-review questions.
