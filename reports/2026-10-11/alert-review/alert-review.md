# Alert review · levels 2026-10-09, week to 2026-10-16

13 market conditions, 15 notices (2 duplicates), 5 pushed to Telegram/email. At the official close 6 were verified and 3 refuted. Decisions logged within 7 days: 0 (decision log has 0 entries). System notices: 2.

## By rule

| Rule | Conditions | Notices | Duplicates | Pushed | Verified | Refuted | Unscored | Acted | Verified rate |
|---|---|---|---|---|---|---|---|---|---|
| Large daily move | 3 | 3 | 0 | 3 | 0 | 0 | 3 | 0 | n/a |
| Monitor heartbeat gap | 1 | 2 | 1 | 1 | 0 | 0 | 0 | 0 | n/a |
| Revisit trigger crossed | 9 | 11 | 2 | 1 | 6 | 3 | 0 | 0 | 67% |
| Volatility spike | 1 | 1 | 0 | 1 | 0 | 0 | 1 | 0 | n/a |

## Conditions

| Symbol | Rule | Severity | First notice | Notices | Pushed | Quote | Level or prior | Close | Outcome | Usefulness |
|---|---|---|---|---|---|---|---|---|---|---|
| TLT | Large daily move | WARN | 2026-10-15 15:40 | 1 | yes (inferred) |  |  | 80.05 | unscored | unscored |
| USD.SGD | Large daily move | WARN | 2026-10-14 15:58 | 1 | yes (inferred) |  |  | 1.279775 | unscored | unscored |
| XLV | Large daily move | WARN | 2026-10-15 15:40 | 1 | yes (inferred) |  |  | 176.71 | unscored | unscored |
| MONITOR | Monitor heartbeat gap | WARN | 2026-10-12 07:14 | 2 | yes (inferred) |  |  |  | system | system |
| VIX | Volatility spike | WARN | 2026-10-16 18:46 | 1 | yes (inferred) |  |  | 19.99 | unscored | unscored |
| 000333 | Revisit trigger crossed | INFO | 2026-10-16 05:46 | 1 | no |  |  | 84.6 | verified | verified, not acted |
| 823 | Revisit trigger crossed | INFO | 2026-10-16 05:46 | 1 | no |  |  | 38.2 | verified | verified, not acted |
| 857 | Revisit trigger crossed | INFO | 2026-10-13 07:37 | 1 | no |  |  | 9.995 | verified | verified, not acted |
| DBA | Revisit trigger crossed | INFO | 2026-10-12 17:44 | 1 | yes (inferred) |  |  | 28.23 | refuted | noise |
| ISF | Revisit trigger crossed | INFO | 2026-10-13 07:37 | 2 | no |  |  | 10.594 | verified | verified, not acted |
| LSEG | Revisit trigger crossed | INFO | 2026-10-12 12:07 | 2 | no |  |  | 87.94 | verified | verified, not acted |
| SSE | Revisit trigger crossed | INFO | 2026-10-13 07:37 | 1 | no |  |  | 25.78 | verified | verified, not acted |
| U11 | Revisit trigger crossed | INFO | 2026-10-16 05:46 | 1 | no |  |  | 41.61 | refuted | noise |
| UNG | Revisit trigger crossed | INFO | 2026-10-13 19:57 | 1 | no |  |  | 10.66 | refuted | noise |

## Coverage gaps, highest priority first

1. **Scheduled checks did not run** (tier 1): 25 of 315 scheduled checks ran. Fix: Start hourly-alerts.yml from an external scheduler (workflow_dispatch); GitHub's own schedule drops most runs on this repository.
2. **Review alerts without headline context** (tier 2): 4 of 4 WARN/URGENT conditions had no matching headline. Fix: Add official feeds or market words for the affected lines (news.MARKET_WORDS).
3. **News feed bls_releases failing** (tier 2): 11 failures in 11 fetches (HTTPError: unavailable or not a valid feed). Fix: Fix or replace the feed URL in config/news-sources.json; the reason names the HTTP status.
4. **No official source: mas_media_releases** (tier 3): not configured. Fix: No documented MAS RSS URL was found in the reference app; the reference only links https://www.mas.gov.sg/ and /monetary-policy as pages. Add once the URL is confirmed on mas.gov.sg.
5. **No official source: HKMA_HIBOR** (tier 3): not configured. Fix: Not configured: the HKMA Open API is key-less, but no documentation for the HIBOR endpoint or its field names was available in the reference repo (it only uses the HKMA press-release RSS) and the API documentation site could not be reached from the build container. Confirm the endpoint and fields at apidocs.hkma.gov.hk, add a parser with a fixture, then configure.
6. **No official source: MAS_SORA** (tier 3): not configured. Fix: Not configured: MAS statistics APIs have moved behind an API gateway that issues keys; no documented key-less endpoint was found in the reference repo or reachable official docs.
7. **No official source: SINGSTAT_CPI** (tier 3): not configured. Fix: Not configured: the SingStat Table Builder API is described as public, but the table id and JSON layout for headline CPI could not be confirmed from the reference repo or reachable official docs.

## Manual reconciliation of the four one-day moves

These runs were written before alerts stored their reference prices, so the scorecard leaves them unscored. Prior
closes and the Friday close are IBKR daily bars; the alert quote is rebuilt from the rounded % in the alert text, so
the kept share is approximate.

| Symbol | Prior close (IBKR) | Alert move | Friday close | Kept of the move | Outcome | Pushed |
|---|---|---|---|---|---|---|
| TLT | 78.89 | +1.5% | 80.05 | 98% | verified | yes |
| XLV | 173.96 | +2.8% | 176.71 | 56% | verified | yes |
| VIX | 16.46 | 21.5 (+31%) | 19.99 | 70% | verified | yes |
| USD.SGD | 1.27995 | +0.6% | 1.279775 | −2% | refuted (fully reversed) | yes |

With these, 9 of 13 market conditions were verified at the close and 4 refuted. Of the 5 market alerts pushed, 3
were verified; of the 8 not pushed (INFO), 6 were verified. None was acted on: the decision log is empty and the
weekly refresh that would act on confirmed revisit levels did not run on 17 October.
