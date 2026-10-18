# Market Brief

A personal market briefing page, updated by scheduled Claude runs.

- **Weekday brief:** about 7 a.m. Central. Overnight moves in Asia and Europe, US futures, resources, regions, climate.
- **Saturday review:** weekly changes across everything, including the fund group and the earnings calendar.
- **Headlines:** the biggest announcements of the period, each with links to several outlets from different regions, plus the official source when there is one.
- **Reading:** recent pieces from the newsletters in `data/watchlist.json`, tagged where they touch something on the watchlist.

## How it works
`index.html` reads `data/latest.json`. Each scheduled run follows `CLAUDE.md`, rewrites that file, runs `scripts/update_logs.py`, and pushes. GitHub Pages serves the site.

## Files
- `data/watchlist.json`: instruments, triggers, group keywords, reading feeds, headline outlets. Edit this to change what the briefs cover.
- `data/latest.json`: the current brief.
- `data/history.jsonl`: one line per run with closing levels. This is the base for volatility and correlation work once enough runs pile up.
- `data/reading_log.jsonl`: articles already shown, so weekday briefs do not repeat them.
- `scripts/update_logs.py`: checks `latest.json` and appends to the logs.

## Add a newsletter
Add an entry under `reading.feeds` in `data/watchlist.json` with its feed address (a Substack feed is `https://<name>.substack.com/feed`) and set `enabled` to `true`.

## Limits
Prices come from web search, not a market data feed. They can lag, and some instruments have no free source. Items that could not be retrieved are listed at the bottom of the page. Paywalled newsletters show only what their feed exposes. The page reports data and does not recommend trades.
