#!/usr/bin/env python3
"""Probe which data sources answer from this machine. Read-only: writes nothing under data/.

Run it from the 'Probe sources' GitHub Actions workflow. For each source it records the
HTTP status, the size, and the newest date found in the page, so we can see what a
scripted run can really read before building the fetcher.
"""
import datetime as dt
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

import requests

UA = "market-brief-probe/1.0 (+https://github.com/MDAnuraag/market-brief)"
MON = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
DATE_RE = re.compile(
    rf"(20\d\d-\d\d-\d\d)|((?:{MON})[a-z]* \d{{1,2}}, 20\d\d)|(\d{{1,2}} (?:{MON})[a-z]* 20\d\d)"
)
TODAY = dt.datetime.now(dt.timezone.utc).date()
SA = "https://stockanalysis.com"
FRESH = f"?fresh={TODAY.isoformat()}"


def to_date(s):
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def newest_date(text):
    best = None
    for m in DATE_RE.finditer(text):
        d = to_date(m.group(0))
        if d and d <= TODAY + dt.timedelta(days=1) and (best is None or d > best):
            best = d
    return best


def last_friday():
    d = TODAY
    while d.weekday() != 4 or d >= TODAY:
        d -= dt.timedelta(days=1)
    return d


def build_sources():
    fri = last_friday()
    s = [
        ("stockanalysis", "US ETF (VOO)", f"{SA}/etf/voo/history/{FRESH}"),
        ("stockanalysis", "US stock (NVDA)", f"{SA}/stocks/nvda/history/{FRESH}"),
        ("stockanalysis", "US mutual fund (FXAIX)", f"{SA}/quote/mutf/fxaix/history/{FRESH}"),
        ("stockanalysis", "Korea (000660)", f"{SA}/quote/krx/000660/history/{FRESH}"),
        ("stockanalysis", "Japan (8035)", f"{SA}/quote/tyo/8035/history/{FRESH}"),
        ("stockanalysis", "Netherlands (ASM)", f"{SA}/quote/ams/ASM/history/{FRESH}"),
        ("stockanalysis", "Taiwan (2454)", f"{SA}/quote/tpe/2454/history/{FRESH}"),
        ("stockanalysis", "Hong Kong (0981)", f"{SA}/quote/hkg/0981/history/{FRESH}"),
        ("stockanalysis", "India (RELIANCE)", f"{SA}/quote/nse/RELIANCE/history/{FRESH}"),
        ("stockanalysis", "Saudi (2222)", f"{SA}/quote/tadawul/2222/history/{FRESH}"),
        ("stockanalysis", "UK (BP)", f"{SA}/quote/lon/BP/history/{FRESH}"),
        ("stockanalysis", "Norway (YAR)", f"{SA}/quote/osl/YAR/history/{FRESH}"),
        ("stockanalysis", "Australia (PDN)", f"{SA}/quote/asx/PDN/history/{FRESH}"),
        ("stockanalysis", "France (AI)", f"{SA}/quote/epa/AI/history/{FRESH}"),
        ("stockanalysis", "Germany (HLAG)", f"{SA}/quote/fra/HLAG/history/{FRESH}"),
        ("stockanalysis", "Canada (NXE)", f"{SA}/quote/tsx/NXE/history/{FRESH}"),
        ("investing.com", "Brent history", "https://www.investing.com/commodities/brent-oil-historical-data"),
        ("investing.com", "S&P 500 history", "https://www.investing.com/indices/us-spx-500-historical-data"),
        ("investing.com", "US 10y yield history", "https://www.investing.com/rates-bonds/u.s.-10-year-bond-yield-historical-data"),
        ("FRED", "10y yield (DGS10)", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"),
        ("FRED", "S&P 500 (SP500)", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500"),
        ("FRED", "Nasdaq (NASDAQCOM)", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=NASDAQCOM"),
        ("FRED", "VIX (VIXCLS)", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"),
        ("FRED", "Brent spot (DCOILBRENTEU)", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU"),
        ("FRED", "Henry Hub spot (DHHNGSP)", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DHHNGSP"),
        ("Stooq", "S&P 500 (^spx)", "https://stooq.com/q/d/l/?s=%5Espx&i=d"),
        ("Stooq", "Brent (cb.f)", "https://stooq.com/q/d/l/?s=cb.f&i=d"),
        ("ECB", "Reference rates 90d (XML)", "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"),
        ("Frankfurter", "USD base, latest", "https://api.frankfurter.dev/v1/latest?base=USD"),
        ("Trading Economics", "Iron ore", "https://tradingeconomics.com/commodity/iron-ore"),
        ("Trading Economics", "US interest rate", "https://tradingeconomics.com/united-states/interest-rate"),
        ("EIA", "Gas storage history", "https://www.eia.gov/dnav/ng/hist/nw2_epg0_swo_r48_bcfw.htm"),
        ("NRC", f"Reactor status {fri}", f"https://www.nrc.gov/reading-rm/doc-collections/event-status/reactor-status/{fri.year}/{fri:%Y%m%d}ps.html"),
        ("Cameco", "Uranium price", "https://www.cameco.com/invest/markets/uranium-price"),
    ]
    try:
        wl = json.load(open("data/watchlist.json"))
        for f in wl["reading"]["feeds"]:
            if f.get("enabled", True):
                s.append(("feed", f["name"], f["url"]))
    except Exception as e:  # keep probing even if the watchlist is unreadable
        print("watchlist not read:", e, file=sys.stderr)
    return s


def main():
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    last_hit = {}
    rows = []
    for group, name, url in build_sources():
        host = urlparse(url).netloc
        wait = 3.0 - (time.time() - last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        status, size, newest, note = "-", 0, None, ""
        try:
            r = sess.get(url, timeout=25)
            last_hit[host] = time.time()
            status, size = r.status_code, len(r.content)
            newest = newest_date(r.text[:400000])
            if group == "feed":
                note = f"{len(re.findall(r'<(?:item|entry)[ >]', r.text))} items"
        except Exception as e:
            last_hit[host] = time.time()
            note = type(e).__name__
        age = (TODAY - newest).days if newest else None
        ok = status == 200 and newest is not None and age is not None and age <= 5
        rows.append((group, name, status, size, newest.isoformat() if newest else "-", "OK" if ok else "CHECK", note, url))
        print(f"{'OK   ' if ok else 'CHECK'} {status} {group:18} {name:28} newest={newest} {note}", flush=True)

    ok_n = sum(1 for r in rows if r[5] == "OK")
    md = [f"## Source probe {TODAY} ({ok_n} of {len(rows)} readable and current)", "",
          "| Source | Item | HTTP | Newest date | Result | Note |", "|---|---|---|---|---|---|"]
    for g, n, st, sz, nd, res, note, url in rows:
        md.append(f"| {g} | {n} | {st} | {nd} | {res} | {note} |")
    out = "\n".join(md)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(out + "\n")
    with open("probe_result.json", "w") as f:
        json.dump([dict(zip(("group", "item", "http", "bytes", "newest", "result", "note", "url"), r)) for r in rows], f, indent=1)


if __name__ == "__main__":
    main()
