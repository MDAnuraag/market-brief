# Market Brief

A personal market briefing page, updated by scheduled Claude runs.

- **Weekday brief:** about 7 a.m. Central. Overnight moves in Asia and Europe, US futures, resources, regions, climate.
- **Saturday review:** weekly changes across everything, including 401(k) funds and allocation drift.

## How it works
`index.html` reads `data/latest.json`. Each scheduled run follows `CLAUDE.md`, rewrites that file, and pushes. GitHub Pages serves the site.

## Edit what it covers
- `data/watchlist.json`: add or remove instruments, change triggers.
- `data/holdings.json`: add 401(k) balances to turn on the allocation check, and target prices to flag buy zones.

## Limits
Prices come from web search, not a market data feed. They can lag, and some instruments have no free source. Items that could not be retrieved are listed at the bottom of the page. The page reports data and does not recommend trades.
