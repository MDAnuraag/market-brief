#!/usr/bin/env python3
"""Fetch prices for the watchlist from public pages and APIs, in plain Python (no LLM).

Shadow mode: writes data/shadow/prices.json and data/shadow/compare.md and never touches
data/latest.json. Items with no scripted source yet are listed as 'skipped'.

  python3 scripts/fetch_prices.py [--data-date YYYY-MM-DD] [--all] [--limit N]
"""
import argparse
import calendar
import datetime as dt
import json
import os
import re
import sys
import time
from io import StringIO
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import requests

UA = "market-brief/1.0 (+https://github.com/MDAnuraag/market-brief)"
SA = "https://stockanalysis.com"
CT = ZoneInfo("America/Chicago")

# ---- which source each symbol uses -------------------------------------------------
SUFFIX = {".KS": "krx", ".T": "tyo", ".AS": "ams", ".TW": "tpe", ".HK": "hkg", ".SR": "tadawul",
          ".NS": "nse", ".AX": "asx", ".OL": "osl", ".CO": "cph", ".PA": "epa", ".DE": "fra", ".L": "lon"}
MUTUAL = {"SWTSX", "SWISX", "FXAIX", "FSMDX", "FSSNX", "FTIHX"}
ETF_GROUPS = {"regions", "esg_funds"}
ETF_SYMS = {"VOO", "VTI", "VXUS", "URA", "KRBN", "BDRY", "IYT",
            "ICLN", "TAN", "FAN", "PBW", "PHO", "CGW", "LIT", "REMX", "COPX", "PICK", "IGF", "PAVE"}
INVESTING = {  # symbol: (category, slug, change kind)
    "^GSPC": ("indices", "us-spx-500", "pct"), "^IXIC": ("indices", "nasdaq-composite", "pct"),
    "^DJI": ("indices", "us-30", "pct"), "^RUT": ("indices", "smallcap-2000", "pct"),
    "^VIX": ("indices", "volatility-s-p-500", "pct"),
    "^TNX": ("rates-bonds", "u.s.-10-year-bond-yield", "pp"),
    "BZ=F": ("commodities", "brent-oil", "pct"), "CL=F": ("commodities", "crude-oil", "pct"),
    "NG=F": ("commodities", "natural-gas", "pct"), "TTF=F": ("commodities", "dutch-ttf-gas-c1-futures", "pct"),
    "HG=F": ("commodities", "copper", "pct"), "GC=F": ("commodities", "gold", "pct"),
    "SI=F": ("commodities", "silver", "pct"),
}
NO_SOURCE = {"U3O8", "EUA", "JKM", "NICKEL", "IRONORE", "LITHIUM", "COBALT", "NDPR", "RICE", "PALM",
             "UREA", "BDI", "SCFI", "DCI", "TANKER"}  # indices and spot prices with no free page found yet
ECB_USD = {"USDBRL": "BRL", "USDMXN": "MXN", "USDTRY": "TRY", "USDZAR": "ZAR", "USDINR": "INR",
           "USDJPY": "JPY", "USDKRW": "KRW", "USDCNY": "CNY", "USDIDR": "IDR", "USDPHP": "PHP",
           "USDTHB": "THB"}
ECB_INVERT = {"EURUSD": "EUR", "AUDUSD": "AUD"}  # shown as USD per unit, so 1 / (units per USD)


def plan(sym, group):
    """Return ('sa', url path) / ('inv', ...) / ('fx', ...) or None when no scripted source yet."""
    if sym in INVESTING:
        return ("inv",) + INVESTING[sym]
    if sym in ECB_USD or sym in ECB_INVERT:
        return ("fx", sym)
    for suf, ex in SUFFIX.items():
        if sym.endswith(suf):
            return ("sa", f"/quote/{ex}/{sym[:-len(suf)]}/history/")
    if group in {"policy_rates", "currencies"} or sym in NO_SOURCE or sym.startswith("^") or "=" in sym:
        return None
    if re.fullmatch(r"[A-Z]{1,5}", sym) is None:
        return None
    if sym in MUTUAL:
        return ("sa", f"/quote/mutf/{sym.lower()}/history/")
    if group in ETF_GROUPS or sym in ETF_SYMS:
        return ("sa", f"/etf/{sym.lower()}/history/")
    return ("sa", f"/stocks/{sym.lower()}/history/")


# ---- fetching and parsing ----------------------------------------------------------
class Fetcher:
    def __init__(self, gap=2.5):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.gap, self.last = gap, {}
        self.n = 0

    def get(self, url):
        host = urlparse(url).netloc
        for attempt in range(3):
            wait = self.gap - (time.time() - self.last.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.s.get(url, timeout=30)
            except requests.RequestException as e:
                self.last[host] = time.time()
                if attempt == 2:
                    raise RuntimeError(f"{type(e).__name__}")
                time.sleep(5)
                continue
            self.last[host] = time.time()
            self.n += 1
            if r.status_code == 429 and attempt < 2:
                print(f"  429 from {host}; waiting 60s", flush=True)
                time.sleep(60)
                continue
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            return r
        raise RuntimeError("gave up")


def to_date(s):
    s = str(s).strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%m/%d/%Y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def num(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return None if x != x else float(x)
    t = re.sub(r"[,%\s$]", "", str(x))
    try:
        return float(t)
    except ValueError:
        return None


def parse_rows(html):
    """Newest-first [(date, close)] from the first table with a Date column and a Close/Price/NAV column."""
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        return []
    for df in tables:
        cols = {str(c).strip().lower(): c for c in df.columns}
        if "date" not in cols:
            continue
        pc = next((cols[k] for k in ("close", "price", "nav") if k in cols), None)
        if pc is None:
            continue
        rows = []
        for _, r in df.iterrows():
            d, v = to_date(r[cols["date"]]), num(r[pc])
            if d and v is not None:
                rows.append((d, v))
        if rows:
            return sorted(set(rows), reverse=True)
    return []


def month_before(d):
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def metrics(rows, data_date, kind="pct"):
    rows = [r for r in rows if r[0] <= data_date]
    if not rows:
        return None
    (d0, v0) = rows[0]
    oldest = rows[-1][0]

    def base(target):
        if oldest > target:
            return None  # table does not reach the base date: leave null, never substitute another date
        return next(v for d, v in rows if d <= target)

    def chg(b):
        if b is None or b == 0:
            return None
        return round(v0 - b, 3) if kind == "pp" else round((v0 / b - 1) * 100, 2)

    prev = rows[1][1] if len(rows) > 1 else None
    return {"last": v0, "chg_1d": chg(prev), "chg_1w": chg(base(d0 - dt.timedelta(days=7))),
            "chg_1m": chg(base(month_before(d0))), "as_of": d0.isoformat(), "rows": len(rows)}


def fx_series(fetch, symbols, start, end):
    url = f"https://api.frankfurter.dev/v1/{start}..{end}?base=USD&symbols={','.join(sorted(symbols))}"
    data = fetch.get(url).json()["rates"]
    return {cur: sorted(((dt.date.fromisoformat(d), v[cur]) for d, v in data.items() if cur in v), reverse=True)
            for cur in symbols}


# ---- main --------------------------------------------------------------------------
def load_items(everything, edition):
    wl = json.load(open("data/watchlist.json"))
    out = []
    for g in wl["groups"]:
        if not everything and edition == "weekday" and g.get("cadence") != "daily":
            continue
        for it in g["items"]:
            out.append((g["id"], it))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-date")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", default="data/shadow")
    a = ap.parse_args()

    now = dt.datetime.now(CT)
    edition = "saturday" if now.weekday() == 5 else "weekday"
    fetch = Fetcher()
    fresh = f"?fresh={now.date().isoformat()}"

    # data date: the last US trading day before today (VOO's newest row), unless given
    if a.data_date:
        data_date = dt.date.fromisoformat(a.data_date)
    else:
        rows = parse_rows(fetch.get(f"{SA}/etf/voo/history/{fresh}").text)
        before = [d for d, _ in rows if d < now.date()]
        if not before:
            sys.exit("could not work out the data date from VOO")
        data_date = before[0]
    print(f"edition={edition} data_date={data_date}", flush=True)

    items = load_items(a.all, edition)
    if a.limit:
        items = items[: a.limit]

    results, fx_syms = {}, {}
    for gid, it in items:
        sym = it["symbol"]
        p = plan(sym, gid)
        url = ""
        if p is None:
            results[sym] = {"status": "skipped", "reason": "no scripted source yet"}
        elif p[0] == "fx":
            fx_syms[sym] = ECB_USD.get(sym) or ECB_INVERT[sym]
        else:
            try:
                url = f"{SA}{p[1]}{fresh}" if p[0] == "sa" else f"https://www.investing.com/{p[1]}/{p[2]}-historical-data"
                kind = "pct" if p[0] == "sa" else p[3]
                m = metrics(parse_rows(fetch.get(url).text), data_date, kind)
                if m is None:
                    results[sym] = {"status": "error", "reason": "no rows on or before the data date", "url": url}
                else:
                    results[sym] = {"status": "ok", "url": url, **m}
            except Exception as e:  # one bad page never stops the run
                results[sym] = {"status": "error", "reason": str(e)[:120], "url": url}
        print(f"{sym:14} {results.get(sym, {}).get('status', 'fx')}", flush=True)

    if fx_syms:
        try:
            start = (data_date - dt.timedelta(days=75)).isoformat()
            ser = fx_series(fetch, set(fx_syms.values()), start, data_date.isoformat())
            for sym, cur in fx_syms.items():
                rows = ser.get(cur, [])
                if sym in ECB_INVERT:
                    rows = [(d, 1 / v) for d, v in rows]
                m = metrics(rows, data_date)
                results[sym] = ({"status": "ok", "url": "https://api.frankfurter.dev/v1/", **m}
                                if m else {"status": "error", "reason": "no ECB rows"})
        except Exception as e:
            for sym in fx_syms:
                results[sym] = {"status": "error", "reason": f"Frankfurter: {str(e)[:100]}"}

    os.makedirs(a.out, exist_ok=True)
    json.dump({"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "data_date": data_date.isoformat(), "edition": edition, "requests": fetch.n,
               "results": results}, open(f"{a.out}/prices.json", "w"), indent=1)
    write_compare(results, data_date, a.out)


def write_compare(results, data_date, out):
    try:
        prev = json.load(open("data/latest.json"))
    except Exception:
        prev = {"groups": [], "data_date": None}
    old = {i["symbol"]: i for g in prev["groups"] for i in g["items"]}
    n = {"ok": 0, "error": 0, "skipped": 0}
    diff, filled, errors, lines = [], [], [], []
    same_day = prev.get("data_date") == data_date.isoformat()
    for sym, r in results.items():
        n[r["status"]] += 1
        o = old.get(sym, {})
        if r["status"] == "error":
            errors.append(f"- `{sym}`: {r.get('reason')}")
        elif r["status"] == "ok":
            if o.get("last") is None:
                filled.append(f"- `{sym}`: {r['last']} (was empty)")
            elif same_day and o.get("last"):
                d = abs(r["last"] / o["last"] - 1) * 100
                cd = [(k, o.get(k), r[k]) for k in ("chg_1d", "chg_1w", "chg_1m")
                      if o.get(k) is not None and r[k] is not None and abs(o[k] - r[k]) > 0.3]
                if d > 0.3 or cd:
                    diff.append(f"- `{sym}`: last {o['last']} -> {r['last']}" + "".join(f"; {k} {x} -> {y}" for k, x, y in cd))
    md = [f"## Shadow prices for {data_date}", "",
          f"{n['ok']} fetched, {n['error']} failed, {n['skipped']} with no scripted source yet.",
          f"Compared with the brief on main only when its data date is also {data_date} (it is: {same_day}).", ""]
    for title, rows in (("Failed", errors), ("Filled where main had no value", filled), ("Differs from main", diff)):
        md += [f"### {title} ({len(rows)})", *(rows or ["none"]), ""]
    md += ["### No scripted source yet", ", ".join(f"`{s}`" for s, r in results.items() if r["status"] == "skipped") or "none"]
    text = "\n".join(md)
    open(f"{out}/compare.md", "w").write(text + "\n")
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        open(path, "a").write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
