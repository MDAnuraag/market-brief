# Market Brief

A personal market briefing page, updated by scheduled Claude runs.

- **Weekday brief:** about 7 a.m. Central. Overnight moves in Asia and Europe, US futures, resources, regions, climate.
- **Saturday review:** weekly changes across everything, including the fund group and the earnings calendar.
- **Headlines:** the biggest announcements of the period, each with links to several outlets from different regions, plus the official source when there is one.
- **Reading:** recent pieces from the newsletters in `data/watchlist.json`, tagged where they touch something on the watchlist.

## How it works
`index.html` (the departure-board layout; the earlier layout is kept as `classic.html`) reads `data/latest.json`. Each scheduled run follows `CLAUDE.md`, rewrites that file, runs `scripts/update_logs.py`, and pushes. GitHub Pages serves the site.

## Files
- `data/watchlist.json`: instruments, triggers, group keywords, reading feeds, headline outlets. Edit this to change what the briefs cover.
- `data/latest.json`: the current brief.
- `data/history.jsonl`: one line per run with closing levels. This is the base for volatility and correlation work once enough runs pile up.
- `data/reading_log.jsonl`: articles already shown, so weekday briefs do not repeat them.
- `scripts/compute_flags.py`: decides which moves get flagged, from each item's own history and its comparison group.
- `data/series/*.csv`: running records of operational indicators.
- `scripts/indicators.py`: due list, ingest and summaries for those series.
- `scripts/check_quality.py`: second-source comparison and the data-checks summary.
- `scripts/update_logs.py`: checks `latest.json` and appends to the logs.

## How flags decide
A flat rule such as "8% in a week" treats a steady fund and a volatile chip stock the same. The page instead asks how unusual a move is for that item, using what it has already recorded.

1. **Normal range.** Each run stores every item's moves in `data/history.jsonl`. From the daily moves it estimates a typical daily swing (a standard deviation). Recent days count more (half-life 20 sessions), and any single extreme day is clipped so one crash does not distort the estimate for months.
2. **Starting guess that fades.** Early on there is little history, so the estimate is blended with a starting guess for that type of instrument, such as 2.8% a day for chip stocks and 0.9% for broad funds. The blend is weighted by how many real observations exist: with 15 sessions of history it is roughly half and half, and with 60 the starting guess barely matters. The page states which stage it is in.
3. **Market mood.** If every item of a type has recently been swinging more than its starting guess, a multiplier widens the normal range for that type, so a turbulent month does not flag everything.
4. **Weekly scale.** The weekly normal range is the daily one times the square root of 5. This assumes days are roughly independent, which is only approximately true.
5. **Own z-score.** This week's move divided by the weekly normal range. A value of 3 means three typical swings.
6. **Peer z-score.** The move minus the median of the item's comparison group, divided by how large that gap normally is (also learned from history). Groups are for comparison only. They are not competitors, and an item is never judged against a group it does not resemble.
7. **Group event.** The group's median move divided by how large a group-wide move normally is. When a whole group moves together, one group flag replaces a pile of single flags.
8. **Tiers.** Look into: own z at least 3 and peer z at least 2, or a group event at 3. Keep an eye on: own z at least 2.5 and peer z at least 1.5, or a one-day move above 3.5 times the daily range, or the VIX at or above 25. Items with no comparison group need 0.5 more on the own z-score. At most 8 flags show.
9. **News.** A flag links to a headline or reading piece only when that piece's overlap tags name the same symbol.

Limits: moves in markets are fatter-tailed than this math assumes, so extreme moves happen more often than a bell curve says. Weekly-published series (some freight and price indices) have no daily history, so they rely on the starting guess for now. The thresholds are in `data/watchlist.json` under `flagging` and can be changed there.

## Source tiers and second-source checks

Every price carries the body that issued it (`src`) and a tier: **official** (the issuer's own page), **reported** (a news report quoting the release), or **aggregator** (a compiled quote site). Flagged items, plus a rotating sample of eight other items per run, are checked against one independent second source. `scripts/check_quality.py` compares them: the last price may differ by at most 0.6% (wider for commodities, gas, freight and rates) and the weekly change by at most 1 point (also wider where those markets are noisier). Each flag then shows "second source agrees", "sources disagree" or "one source so far", and the footer keeps a "data checks" summary. The script never fetches anything and never changes a number; it only compares what the run recorded.

## Indicators

Slow operational numbers (TSMC monthly revenue, Korean chip exports, Japanese and global chip equipment billings, global chip sales) are kept in `data/series/*.csv`, one row per period. A restated number is added as a new row, so revisions stay visible. For each series the page shows the latest value, year-on-year change (reported by the source, or computed here when it is not), a sparkline, whether the reported figures reconcile (TSMC's monthly figures must add up to its year-to-date total), and when the next release is due. Once a series has 12 year-on-year readings, today's reading is also compared with its own median and typical spread (median absolute deviation, which a single odd month cannot distort); before that the card shows how much history it has instead of pretending to a range. When an indicator touches a flagged name, it appears under the flag as context. Indicators are not signals and are never combined into a score.

## Spreads and seasonal ranges

Three relationships are computed from prices already on the page: Brent minus WTI, copper to aluminum, and copper to gold. Each is shown against its own recent sessions, so the page learns what is normal for it the same way it does for prices. Indicator series with enough history also show their latest value against the five-year average and range for the same period (the usual way analysts judge whether inventories are tight). That needs about three earlier years on file. Weekly series (rig count, US and EU gas storage) are supported alongside monthly and quarterly ones. A one-off backfill can be done by appending rows to the series CSV.

## Route line (region filter)
Above the Regions board, the eight stops (Oceania, East Asia, Southeast Asia, South Asia, Middle East and Africa, Europe, Americas, Global) sit on a dotted line in follow-the-sun order. Each stop shows the median one-day move of that region's gauges and how many flagged instruments are tagged to it. Picking a stop filters every board whose rows all carry a region (Regions, Positive impact), shows only that region's climate entry, and marks that region's coverage links under Headlines (the rest are dimmed, not hidden). Reading, the other boards and paging keep working as before. The tag comes from `region` on each item in `data/watchlist.json`; `Broad emerging` shows under Global.

## Add a newsletter
Add an entry under `reading.feeds` in `data/watchlist.json` with its feed address (a Substack feed is `https://<name>.substack.com/feed`) a `group` (Chips, Energy, Metals and minerals, Food and farm, Shipping and logistics, Climate and policy, or China and Asia) and set `enabled` to `true`. The Reading section lists everything the feeds returned, with topic chips to narrow it; nothing is filtered out. Feeds that mix free and paywalled items are included, and the page shows only what the feed itself exposes.

## Limits
Prices come from web search, not a market data feed. They can lag, and some instruments have no free source. Items that could not be retrieved are listed at the bottom of the page. Paywalled newsletters show only what their feed exposes. The page reports data and does not recommend trades.
