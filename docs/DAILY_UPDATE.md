# Daily after-hours publish of the Global Investment Desk

Every weekday after the US close (16:20 New York time), the Desk at
https://claude.ai/artifact/G7LaSQYJscDLYKNAFNu4Fn is republished with that session's official closes.

**What changes daily and what does not.** Only the closes change. Stances, buy zones, exits and revisit
triggers stay those of the latest weekly refresh (`reports/<date>/`): the rules were tested on weekly closes and the
Handbook acts on Friday's close, so a daily close below an exit is a warning for Friday, not a new decision.
Read-only against markets: never place orders, create alerts or watchlists, or touch account tools.

1. **Start from main.** `git fetch origin main` and reset the working branch to it if it carries nothing unmerged.
   Use the latest `reports/<date>/` and `data/snapshots/<date>/` (the newest dates).

2. **Collect the closes** with the Interactive Brokers connector, in a subagent so the replies stay out of the main
   context. For every instrument in `data/snapshots/<date>/*.json`, call `get_price_history` with its
   `contract_id`, `exchange` and `security_type`, `step: "ONE_DAY"`, `step_count: 2`, `outside_rth: false`.
   Copy the numbers exactly; never estimate. IBKR allows about 30 requests a minute: pace the calls and retry a
   rate-limited one once. Write `data/daily/<session date>.json` (the New York session date):

   ```json
   {"session_date": "2026-10-16", "source": "IBKR get_price_history ONE_DAY, last two bars as returned",
    "retrieved_at_utc": "2026-10-16T20:25:00Z",
    "closes": {"LSEG": {"close": 87.94, "prior_close": 86.82, "bar_time": "2026-10-16T07:00:00Z"}},
    "missing": {"XYZ": "no bars returned"}}
   ```

   `close` and `bar_time` come from the last bar, `prior_close` from the one before. FX bars are stamped 21:15 UTC
   on the previous day; keep the time as returned. Any instrument without a usable bar goes under `missing` with
   the reason.

3. **Build the data file:**

   ```bash
   .venv/bin/global-research app-data --reports reports/<date> --snapshot data/snapshots/<date> \
       --state-dir data/state --daily data/daily/<session date>.json --out app/data.json
   ```

   It prints how many names were priced, the exceptions, and any lines missing, stale (no session that day) or
   to check (further from the weekly close than 25%, or five weekly standard deviations if wider). Investigate every name listed as to check before publishing.

4. **Publish** with the Artifact tool, as `docs/APP_UPDATE.md` step 2 describes: read the artifact, then publish
   `app/index.html` to its URL with `files: {"data.json": "app/data.json"}`, omitting `capabilities`.

5. **Record.** Commit `data/daily/<session date>.json` and `app/data.json` to the working branch and push; they
   ride into the next pull request. Reply in five lines or fewer: session date, names priced, exceptions (exit
   breaches first), gaps, and the artifact link. Send nothing else: hourly alerts and the weekly email already
   cover delivery.

If the connector is unavailable or more than a tenth of the closes are missing, do not publish; say what failed.
