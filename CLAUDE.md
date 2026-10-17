# Market Brief: run instructions

This repo publishes a market briefing page (`index.html`) that reads `data/latest.json`. A scheduled run updates that file and pushes it. These are the rules for a run.

## Inputs
- `data/watchlist.json`: instruments, triggers, allocation targets, climate checklist.
- `data/holdings.json`: optional balances and target prices set by the owner. Null means "not provided".

## Editions
- **weekday** (about 7 a.m. Central, Monday to Friday): cover every group whose `cadence` is `daily`. Show what moved overnight in Asia and Europe and where US futures point.
- **saturday** (Saturday morning Central): cover every group, including `funds_401k`, with 1-week and 1-month changes after Friday's close, plus the allocation check and a fuller climate section.

## Data rules
1. Get prices with WebSearch and WebFetch. Prefer exchange, central-bank, or agency pages over aggregators, and use a mix of regional sources so no country's press or agency is the default.
2. Never invent a number. If a price, change, or date cannot be found, set the field to `null` and add a line to `gaps` naming the symbol and what failed.
3. Put the date of the data in `data_date` (the last trading day, in YYYY-MM-DD). If an individual item is older than that, set its `as_of`.
4. Changes are percentages as plain numbers (3.2 means +3.2%). For yields, `last` is the yield in percent and the change is in percentage points, noted in `note`.
5. List the main sources in `sources` as short strings (domain or publication name).
6. Report data, not recommendations. Do not say to buy or sell.

## Flags
Add an entry to `flags` ({symbol, reason}) when an item crosses a trigger in `watchlist.json`:
- stock or fund moved more than its weekly threshold, or an index moved more than its daily threshold
- VIX above its limit
- earnings within the configured number of days (note the date in `next_earnings`)
- price below the `target_prices` entry in `holdings.json`
Keep reasons to one plain sentence with the number in it. If nothing triggers, leave `flags` empty.

## Per-item content
For each item set `symbol`, `name`, `last`, `chg_1d`, `chg_1w`, `chg_1m`, and optionally `unit`, `note`, `esg`, `next_earnings`, `region`, `as_of`. Write a `note` only for the biggest movers and for items with a real catalyst. Keep notes under 25 words. For ESG-themed items, `esg` can carry an MSCI rating or a recent controversy flag when you can verify one; otherwise leave it out. Do not produce a single "ethical score".

## Summary and climate
- `summary`: 3 to 5 sentences on what mattered, spread across regions and resource markets, not only US indices.
- `climate`: 3 to 6 entries ({region, headline, detail, source}) from the `climate_watch` checklist, ordered alphabetically by region. Cover several regions each run.

## Allocation check (Saturday only)
If every value in `holdings.json` `funds_401k` is non-null, compute the US versus international split (FXAIX, FSMDX, FSSNX are US; FTIHX is international) and compare with `allocation_targets`. Set `allocation` to {summary, note}. Alert if drift is at least `drift_alert_points`. If balances are missing, set `allocation` to {summary: "Balances not provided, so drift was not calculated.", note: "Add current balances to data/holdings.json."}.

## Output shape
`data/latest.json` follows this shape, with groups copied from `watchlist.json` in order (skip non-daily groups on weekday runs):

```json
{
  "generated_at": "2026-10-19T12:05:00Z",
  "data_date": "2026-10-16",
  "edition": "weekday",
  "summary": "...",
  "flags": [{ "symbol": "NVDA", "reason": "Up 9.1% this week, above the 8% trigger." }],
  "groups": [{ "id": "macros", "title": "Macros", "group_by": null, "items": [] }],
  "climate": [{ "region": "Oceania", "headline": "...", "detail": "...", "source": "..." }],
  "allocation": null,
  "sources": ["..."],
  "gaps": ["JKM LNG: no free price found"]
}
```

Carry `group_by` over from the watchlist. Write the real timestamp in UTC.

## Finish
1. Validate: `python3 -c "import json;json.load(open('data/latest.json'))"`.
2. Commit only `data/latest.json` with a message like `Brief 2026-10-19 (weekday)`, then push to `main`.
3. If the push fails, retry once. If it still fails, stop and report the error. Do not force-push.
