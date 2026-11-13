#!/usr/bin/env python3
"""Build the brief in plain Python (no LLM), then run the repo's own checks.

  python3 scripts/build_brief.py            dry run: builds in a temporary copy of the repo and saves the
                                            result under data/shadow/brief/. Nothing live changes.
  python3 scripts/build_brief.py --live     rewrites data/latest.json and the logs in this checkout.

Prices come from scripts/fetch_prices.py. Reading, headlines and climate come from RSS feeds. The text
sections are plain: feed excerpts and link lists, no generated prose except a templated summary.
"""
import argparse
import copy
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_prices as fp  # noqa: E402

CT = ZoneInfo("America/Chicago")
ROOT = Path(__file__).resolve().parent.parent

# Feeds the watchlist lists but we do not read: robots.txt disallows them.
SKIP_FEEDS = {"Fertilizer Daily"}

# General news feeds for the Headlines section. Not all of these may load; the run reports which did.
HEADLINE_FEEDS = [
    ("Federal Reserve", "Americas", "https://www.federalreserve.gov/feeds/press_all.xml", "primary"),
    ("European Central Bank", "Europe", "https://www.ecb.europa.eu/rss/press.html", "primary"),
    ("BBC News", "Europe", "https://feeds.bbci.co.uk/news/business/rss.xml", "press"),
    ("DW", "Europe", "https://rss.dw.com/rdf/rss-en-bus", "press"),
    ("France 24", "Europe", "https://www.france24.com/en/business/rss", "press"),
    ("The Guardian", "Europe", "https://www.theguardian.com/business/rss", "press"),
    ("Al Jazeera", "Middle East and Africa", "https://www.aljazeera.com/xml/rss/all.xml", "press"),
    ("Arab News", "Middle East and Africa", "https://www.arabnews.com/rss.xml", "press"),
    ("The Hindu", "South Asia", "https://www.thehindu.com/business/feeder/default.rss", "press"),
    ("Economic Times", "South Asia", "https://economictimes.indiatimes.com/rssfeedstopstories.cms", "press"),
    ("The Straits Times", "Southeast Asia", "https://www.straitstimes.com/news/business/rss.xml", "press"),
    ("CNA", "Southeast Asia", "https://www.channelnewsasia.com/rssfeeds/8395954", "press"),
    ("Japan Times", "East Asia", "https://www.japantimes.co.jp/feed/", "press"),
    ("South China Morning Post", "East Asia", "https://www.scmp.com/rss/91/feed", "press"),
    ("Korea Herald", "East Asia", "https://www.koreaherald.com/rss/kh_Business", "press"),
    ("ABC News Australia", "Oceania", "https://www.abc.net.au/news/feed/51120/rss.xml", "press"),
    ("RNZ", "Oceania", "https://www.rnz.co.nz/rss/business.xml", "press"),
    ("NPR", "Americas", "https://feeds.npr.org/1006/rss.xml", "press"),
    ("CBC News", "Americas", "https://www.cbc.ca/webfeed/rss/rss-business", "press"),
    ("CNBC", "Americas", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "press"),
]

REGION_WORDS = {
    "Americas": ["United States", "U.S.", "Brazil", "Canada", "Mexico", "Chile", "Argentina", "Colombia", "Peru",
                 "Amazon", "California", "Texas", "Panama", "Latin America", "Caribbean"],
    "Europe": ["Europe", "European", "EU", "UK", "Britain", "Germany", "France", "Spain", "Italy", "Poland",
               "Norway", "Netherlands", "Ukraine", "Rhine", "Brussels"],
    "Middle East and Africa": ["Saudi", "UAE", "Iran", "Israel", "Qatar", "Turkey", "Egypt", "Africa", "Nigeria",
                               "Kenya", "Ethiopia", "Middle East", "Gulf", "Morocco"],
    "South Asia": ["India", "Pakistan", "Bangladesh", "Sri Lanka", "Nepal", "Himalaya", "monsoon"],
    "East Asia": ["China", "Chinese", "Japan", "Korea", "Taiwan", "Hong Kong", "Mongolia", "Beijing", "Tokyo"],
    "Southeast Asia": ["Indonesia", "Vietnam", "Thailand", "Philippines", "Malaysia", "Singapore", "ASEAN",
                       "Myanmar", "Mekong", "Cambodia"],
    "Oceania": ["Australia", "New Zealand", "Pacific", "Fiji", "Papua", "Queensland", "Great Barrier Reef"],
}

STOP = set("""that with from this will have after over amid says said news could would their about into more than what when
year week month today also been were they them then these those which while where there other some such only just
very much many most each both here your you our are was has had for and the but not new""".split())

FRED = {"^GSPC": "SP500", "^IXIC": "NASDAQCOM", "^DJI": "DJIA", "^VIX": "VIXCLS", "^TNX": "DGS10"}
FRED_NAME = {"^GSPC": "S&P Dow Jones Indices", "^IXIC": "Nasdaq", "^DJI": "S&P Dow Jones Indices",
             "^VIX": "Cboe", "^TNX": "US Department of the Treasury"}
STATIC_NOTE = re.compile(r"^(Quoted in [A-Za-z ]+|Priced in [A-Z]{3}|[A-Z]{3}|GBX \(pence\)|USD GDR)\.?$")


# ---------------------------------------------------------------------------- helpers
def clean(text):
    t = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    t = re.sub(r"\s*The post .{0,200} appeared first on .{0,100}\.?\s*$", "", t, flags=re.S)
    return re.sub(r"\s+", " ", t).strip()


def excerpt(text, words):
    w = clean(text).split()
    return " ".join(w[:words]) + ("..." if len(w) > words else "")


def entry_date(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    return dt.date(*t[:3]) if t else None


def read_feed(fetch, url):
    r = fetch.get(url)
    f = feedparser.parse(r.content)
    if not f.entries and f.bozo:
        raise RuntimeError("not a readable feed")
    return f.entries


def signed(p, nd=1):
    return f"{p:+.{nd}f}%"


def fmt_day(d):
    return f"{d.day} {d.strftime('%b')}"


def read_lines(path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ---------------------------------------------------------------------------- prices
def fred_rows(fetch, sid):
    text = fetch.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}").text
    rows = []
    for line in text.splitlines()[1:]:
        d, _, v = line.partition(",")
        v = fp.num(v)
        if v is not None:
            rows.append((dt.date.fromisoformat(d), v))
    return sorted(rows, reverse=True)


def hist_base(history, sym, target):
    """Last recorded level on or just before target (within 4 days) from this page's own run history."""
    for h in sorted(history, key=lambda x: x["data_date"], reverse=True):
        d = dt.date.fromisoformat(h["data_date"])
        if d <= target:
            return h["last"].get(sym) if (target - d).days <= 4 else None
    return None


def build_groups(wl, prev, results, data_date, prev_date, edition, history, fred, notes_out):
    prev_items = {i["symbol"]: i for g in prev.get("groups", []) for i in g["items"]}
    groups, carried, failed = [], [], []
    for g in wl["groups"]:
        if edition == "weekday" and g.get("cadence") != "daily":
            continue
        out = {"id": g["id"], "title": g["title"], "group_by": g.get("group_by"), "items": []}
        for wi in g["items"]:
            sym = wi["symbol"]
            it = copy.deepcopy(prev_items.get(sym)) or {"symbol": sym}
            for k in ("name", "unit", "region"):
                if k in wi:
                    it[k] = wi[k]
            r = results.get(sym, {})
            if r.get("status") == "ok":
                digits = 5 if (sym in fp.ECB_INVERT or sym in fp.ECB_USD) and r["last"] < 1 else 4
                last = round(r["last"], digits) if (sym in fp.ECB_INVERT or sym in fp.ECB_USD) else r["last"]
                it.update(last=last, chg_1d=r["chg_1d"], chg_1w=r["chg_1w"], chg_1m=r["chg_1m"])
                d0 = dt.date.fromisoformat(r["as_of"])
                if r["as_of"] == data_date.isoformat():
                    it.pop("as_of", None)
                else:
                    it["as_of"] = r["as_of"]
                it.pop("xcheck", None)
                if it.get("note") and not STATIC_NOTE.match(it["note"]):
                    it.pop("note")
                kind = "pp" if sym == "^TNX" else "pct"
                if it["chg_1m"] is None:  # the page table is too short: try FRED, then our own history
                    base, how = None, None
                    target = fp.month_before(d0)
                    if sym in fred:
                        rows = [x for x in fred[sym] if x[0] <= target]
                        if rows and fred[sym][-1][0] <= target:
                            base, how = rows[0][1], f"FRED (St. Louis Fed), {fmt_day(rows[0][0])} close"
                    if base is None:
                        base = hist_base(history, sym, target)
                        how = "this page's own history" if base is not None else None
                    if base:
                        it["chg_1m"] = round(r["last"] - base, 3) if kind == "pp" else round((r["last"] / base - 1) * 100, 2)
                        it["note"] = f"1m base from {how}."
                if "src" not in it:
                    it["src"] = ("European Central Bank (via Frankfurter API mirror)" if sym in fp.ECB_USD or sym in fp.ECB_INVERT
                                 else "Investing.com" if sym in fp.INVESTING else "stockanalysis.com")
                    it["tier"] = "aggregator"
            else:
                reason = r.get("reason", "no scripted source yet")
                if g["id"] != "policy_rates" and it.get("last") is not None:
                    old = it.get("as_of") or prev_date
                    if old < data_date.isoformat():
                        it.update(chg_1d=None, chg_1w=None, chg_1m=None, as_of=old)
                        it["note"] = f"Carried from the {fmt_day(dt.date.fromisoformat(old))} brief: {reason}."
                it.setdefault("last", None)
                for k in ("chg_1d", "chg_1w", "chg_1m"):
                    it.setdefault(k, None)
                (failed if r.get("status") == "error" else carried).append(sym)
            out["items"].append(it)
        groups.append(out)
    notes_out["carried"], notes_out["failed"] = carried, failed
    return groups


def add_xchecks(groups, fred, data_date):
    n = 0
    for g in groups:
        for it in g["items"]:
            sym = it["symbol"]
            rows = fred.get(sym)
            if not rows or it.get("last") is None or it.get("as_of"):
                continue
            hit = [v for d, v in rows if d == data_date]
            if hit:
                it["xcheck"] = {"src": f"FRED (St. Louis Fed) - {FRED_NAME[sym]}", "tier": "aggregator",
                                "last": hit[0], "as_of": data_date.isoformat()}
                n += 1
    return n


# ---------------------------------------------------------------------------- text sections
def make_matcher(wl):
    pats = []
    for g in wl["groups"]:
        for it in g["items"]:
            name = it["name"].strip()
            if len(name) >= 5 and not name.startswith("^"):
                pats.append((it["symbol"], re.compile(r"\b" + re.escape(name) + r"\b", re.I), name))
    return pats


def overlaps(matcher, title, text):
    found = []
    for sym, rx, name in matcher:
        if rx.search(title):
            found.append((0, {"symbol": sym, "strength": "direct", "reason": f"{name} is named in the headline."}))
        elif rx.search(text):
            found.append((1, {"symbol": sym, "strength": "loose", "reason": f"{name} is named in the feed excerpt."}))
    found.sort(key=lambda x: x[0])
    out, seen = [], set()
    for _, o in found:
        if o["symbol"] not in seen:
            out.append(o)
            seen.add(o["symbol"])
    return out[:3]


def build_reading(wl, fetch, today, edition, matcher, rlog_urls, gaps):
    cfg = wl["reading"]
    feeds = [f for f in cfg["feeds"] if f.get("enabled", True) and f["name"] not in SKIP_FEEDS]
    seen_urls, uniq = set(), []
    for f in feeds:  # the watchlist lists Utility Dive twice
        if f["url"] not in seen_urls:
            seen_urls.add(f["url"])
            uniq.append(f)
    by_feed, quiet, entries_cache = {}, [], {}
    for f in uniq:
        try:
            ents = read_feed(fetch, f["url"])
        except Exception as e:  # noqa: BLE001
            gaps.append(f"{f['name']}: feed not read ({str(e)[:60]})")
            continue
        entries_cache[f["url"]] = ents
        keep = []
        for e in ents:
            d = entry_date(e)
            link = e.get("link", "")
            if not d or not link or d > today or (today - d).days > cfg["lookback_days"]:
                continue
            if edition == "weekday" and link in rlog_urls:
                continue
            keep.append((d, e))
        keep.sort(key=lambda x: x[0], reverse=True)
        if not keep:
            quiet.append(f["name"])
        by_feed[f["name"]] = (f, keep)
    cap = cfg["max_items_per_feed"]
    while sum(min(cap, len(k)) for _, k in by_feed.values()) > cfg["max_items_total"] and cap > 1:
        cap -= 1
    out = []
    for name, (f, keep) in by_feed.items():
        for d, e in keep[:cap]:
            body = (e.get("content") or [{}])[0].get("value", "") or e.get("summary", "")
            txt = clean(body)
            access = "full" if len(txt) > 1500 else "partial" if len(txt) > 300 else "teaser"
            summ = excerpt(e.get("summary", "") or body, cfg["summary_max_words"]) or "The feed gives the headline only."
            out.append({"source": name, "title": clean(e.get("title", "")) or "(untitled)", "url": e["link"],
                        "published": d.isoformat(), "source_url": f["site"], "topic": f["group"],
                        "access": access, "summary": summ,
                        "overlap": overlaps(matcher, clean(e.get("title", "")), txt[:2000])})
    order = {g: i for i, g in enumerate(cfg["group_order"])}
    out.sort(key=lambda r: (order.get(r["topic"], 99), r["source"], r["published"]), reverse=False)
    return out, quiet, [f for f in uniq if f["name"] in by_feed], entries_cache


def toks(title):
    return {w.lower().strip("'-") for w in re.findall(r"[A-Za-z][A-Za-z'\-]{3,}|\d[\d.,]*%?", title)} - STOP


def build_headlines(fetch, today, edition, max_stories, matcher, gaps):
    days = 7 if edition == "saturday" else (4 if today.weekday() == 0 else 2)
    items, loaded = [], []
    for outlet, region, url, kind in HEADLINE_FEEDS:
        try:
            ents = read_feed(fetch, url)
        except Exception as e:  # noqa: BLE001
            gaps.append(f"Headlines: {outlet} not read ({str(e)[:50]})")
            continue
        loaded.append((outlet, url))
        for e in ents[:25]:
            d, link, title = entry_date(e), e.get("link", ""), clean(e.get("title", ""))
            if d and link and title and d <= today and (today - d).days <= days:
                items.append({"outlet": outlet, "region": region, "kind": kind, "title": title, "url": link,
                              "date": d, "text": clean(e.get("summary", "")), "toks": toks(title)})
    items.sort(key=lambda x: x["date"], reverse=True)
    clusters = []
    for it in items:
        for c in clusters:
            if any(m["outlet"] == it["outlet"] for m in c):
                continue
            shared = len(c[0]["toks"] & it["toks"])
            if shared >= 3 and shared / max(1, min(len(c[0]["toks"]), len(it["toks"]))) >= 0.45:
                c.append(it)
                break
        else:
            clusters.append([it])
    multi = [c for c in clusters if len(c) >= 2]
    multi.sort(key=lambda c: (len({m["region"] for m in c}), len(c), max(m["date"] for m in c)), reverse=True)
    primary = [c for c in clusters if len(c) == 1 and c[0]["kind"] == "primary"]
    chosen = (multi + primary)[:max_stories]
    out = []
    for c in chosen:
        c.sort(key=lambda m: (m["kind"] != "primary", m["date"]))
        cov = []
        regions = set()
        for m in sorted(c, key=lambda m: (m["region"] in regions, m["kind"] != "primary")):
            if len(cov) < 4:
                cov.append({"outlet": m["outlet"], "region": m["region"], "title": m["title"], "url": m["url"], "kind": m["kind"]})
                regions.add(m["region"])
        rep = c[0]
        summ = excerpt(rep["text"], 35) or f"Reported by {', '.join(m['outlet'] for m in c[:4])}."
        out.append({"story": rep["title"][:120], "date": max(m["date"] for m in c).isoformat(), "summary": summ,
                    "coverage": cov, "overlap": overlaps(matcher, rep["title"], rep["text"])})
    return out, loaded


def build_climate(entries_cache, feeds, today, lookback):
    cands = []
    for f in feeds:
        if f["group"] != "Climate and policy":
            continue
        for e in entries_cache.get(f["url"], []):
            d, link = entry_date(e), e.get("link", "")
            if not d or not link or d > today or (today - d).days > lookback:
                continue
            title, body = clean(e.get("title", "")), clean(e.get("summary", ""))
            text = f"{title}. {body}"
            scores = {r: sum(len(re.findall(r"\b" + re.escape(w) + r"\b", text)) for w in words)
                      for r, words in REGION_WORDS.items()}
            region = max(scores, key=scores.get)
            if scores[region] > 0:
                cands.append((d, region, f, e, title, body))
    cands.sort(key=lambda x: x[0], reverse=True)
    best = {}
    for d, region, f, e, title, body in cands:
        best.setdefault(region, {"region": region, "headline": title, "detail": excerpt(body, 40) or "Headline only.",
                                 "source": f"{f['name']}, {fmt_day(d)}", "source_url": e["link"]})
    return [best[r] for r in sorted(best)][:7]


def build_summary(groups, data_date):
    it = {i["symbol"]: i for g in groups for i in g["items"]}
    fresh = lambda s: s in it and it[s].get("last") is not None and not it[s].get("as_of")  # noqa: E731
    word = lambda p: "rose" if p > 0.05 else "fell" if p < -0.05 else "was flat"  # noqa: E731
    out = []
    parts = []
    for s, nm in (("^GSPC", "the S&P 500"), ("^IXIC", "the Nasdaq"), ("^DJI", "the Dow")):
        if fresh(s) and it[s].get("chg_1d") is not None:
            parts.append((s, nm))
    if parts:
        s0, n0 = parts[0]
        txt = f"Prices are the {fmt_day(data_date)} closes. {n0[0].upper() + n0[1:]} {word(it[s0]['chg_1d'])} {abs(it[s0]['chg_1d']):.1f}% to {it[s0]['last']:,.2f}"
        rest = [f"{nm} {signed(it[s]['chg_1d'])}" for s, nm in parts[1:]]
        if rest:
            txt += ", with " + " and ".join(rest)
        if fresh("^VIX") and it["^VIX"].get("chg_1d") is not None:
            txt += f", and the VIX {word(it['^VIX']['chg_1d'])} {abs(it['^VIX']['chg_1d']):.1f}% to {it['^VIX']['last']:.2f}"
        out.append(txt + ".")
    for gid, label in (("semis", "Among chip and equipment names"), ("regions", "Among country and regional funds")):
        g = next((x for x in groups if x["id"] == gid), None)
        rows = [i for i in (g or {"items": []})["items"] if i.get("chg_1d") is not None and not i.get("as_of")]
        if len(rows) >= 4:
            rows.sort(key=lambda i: i["chg_1d"], reverse=True)
            top, bot = rows[0], rows[-1]
            out.append(f"{label}, {top['name']} led ({signed(top['chg_1d'])}) and {bot['name']} lagged ({signed(bot['chg_1d'])}).")
    res = [(s, n) for s, n in (("BZ=F", "Brent"), ("NG=F", "US natural gas"), ("TTF=F", "European gas"),
                               ("GC=F", "gold"), ("HG=F", "copper")) if fresh(s) and it[s].get("chg_1d") is not None]
    if res:
        out.append("In resources, " + ", ".join(f"{n} {signed(it[s]['chg_1d'])}" for s, n in res) + ".")
    macro = []
    if fresh("^TNX") and it["^TNX"].get("chg_1d") is not None:
        macro.append(f"the 10-year Treasury yield ended at {it['^TNX']['last']:.2f}% ({it['^TNX']['chg_1d']:+.2f} points)")
    if fresh("USDJPY") and it["USDJPY"].get("chg_1d") is not None:
        macro.append(f"the dollar {word(it['USDJPY']['chg_1d'])} {abs(it['USDJPY']['chg_1d']):.1f}% against the yen")
    if macro:
        out.append("In rates and currencies, " + " and ".join(macro) + ".")
    return " ".join(out) or f"Prices are the {fmt_day(data_date)} closes."


# ---------------------------------------------------------------------------- pipeline
def run(work, name, *args, check=True):
    cmd = [sys.executable, str(work / "scripts" / name), *args]
    p = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
    print(f"$ {name} {' '.join(args)} -> exit {p.returncode}")
    print((p.stdout + p.stderr).strip()[-1500:])
    if check and p.returncode:
        raise SystemExit(f"{name} failed")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--data-date")
    ap.add_argument("--edition", choices=["weekday", "saturday"])
    a = ap.parse_args()

    now = dt.datetime.now(CT)
    today = now.date()
    edition = a.edition or ("saturday" if now.weekday() == 5 else "weekday")
    fetch = fp.Fetcher()
    fresh_q = f"?fresh={today.isoformat()}"

    if a.data_date:
        data_date = dt.date.fromisoformat(a.data_date)
    else:
        rows = fp.parse_rows(fetch.get(f"{fp.SA}/etf/voo/history/{fresh_q}").text)
        before = [d for d, _ in rows if d < today]
        if not before:
            sys.exit("could not work out the data date")
        data_date = before[0]

    work = ROOT if a.live else Path(tempfile.mkdtemp(prefix="brief-")) / "repo"
    if not a.live:
        shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "__pycache__", "shadow"))
    wl = json.loads((work / "data/watchlist.json").read_text(encoding="utf-8"))
    prev = json.loads((work / "data/latest.json").read_text(encoding="utf-8"))
    history = read_lines(work / "data/history.jsonl")
    rlog_urls = {r["url"] for r in read_lines(work / "data/reading_log.jsonl")}
    gaps, notes = [], {}
    print(f"edition={edition} data_date={data_date} live={a.live}", flush=True)

    items = [(g["id"], i) for g in wl["groups"] for i in g["items"]
             if edition == "saturday" or g.get("cadence") == "daily"]
    results = fp.collect(items, fetch, data_date, fresh_q)
    fred = {}
    for sym, sid in FRED.items():
        try:
            fred[sym] = fred_rows(fetch, sid)
        except Exception as e:  # noqa: BLE001
            gaps.append(f"FRED {sid}: not read ({str(e)[:50]})")
    groups = build_groups(wl, prev, results, data_date, prev.get("data_date", data_date.isoformat()),
                          edition, history, fred, notes)
    n_x = add_xchecks(groups, fred, data_date)

    matcher = make_matcher(wl)
    reading, quiet, loaded_feeds, cache = build_reading(wl, fetch, today, edition, matcher, rlog_urls, gaps)
    cap = wl["headlines"]["max_stories_saturday" if edition == "saturday" else "max_stories_weekday"]
    headlines, loaded_news = build_headlines(fetch, today, edition, cap, matcher, gaps)
    climate = build_climate(cache, loaded_feeds, today, wl["reading"]["lookback_days"])

    if notes["failed"]:
        gaps.append("Prices not fetched, carried from the previous brief: " + ", ".join(notes["failed"]))
    if notes["carried"]:
        gaps.append("No scripted source yet, carried from the previous brief: " + ", ".join(notes["carried"]))
    gaps.append("Indicators, earnings dates and per-item commentary are not refreshed by the scripted run.")
    gaps.append("Second sources: FRED for the S&P 500, Nasdaq, Dow, VIX and 10-year yield only; all other prices are from one aggregator.")
    gaps.append("Reading summaries are feed excerpts, headlines are matched by shared words, and climate entries are matched by region keywords.")

    sources = [{"name": "stockanalysis.com", "url": "https://stockanalysis.com"},
               {"name": "Investing.com", "url": "https://www.investing.com"},
               {"name": "European Central Bank (via Frankfurter API)", "url": "https://api.frankfurter.dev"},
               {"name": "FRED, Federal Reserve Bank of St. Louis", "url": "https://fred.stlouisfed.org"}]
    sources += [{"name": f["name"], "url": f["site"]} for f in loaded_feeds]
    sources += [{"name": o, "url": u} for o, u in loaded_news]

    d = dict(prev)
    d.update(generated_at=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             data_date=data_date.isoformat(), edition=edition, summary=build_summary(groups, data_date),
             groups=groups, headlines=headlines, reading=reading, reading_quiet=quiet, climate=climate,
             sources=sources, gaps=gaps)
    d.pop("indicator_observations", None)

    def save(obj):
        (work / "data/latest.json").write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    def reload():
        return json.loads((work / "data/latest.json").read_text(encoding="utf-8"))

    save(d)
    run(work, "indicators.py", "compute", "--write")
    run(work, "compute_flags.py", "--write")
    run(work, "check_quality.py", "--plan", check=False)
    run(work, "check_quality.py", "--write")
    d = reload()
    if d.get("flags"):
        top = d["flags"][:2]
        names = {i["symbol"]: i.get("name", i["symbol"]) for g in d["groups"] for i in g["items"]}
        d["summary"] += " Flagged for a closer look: " + "; ".join(
            f"{names.get(f['symbol'], f['symbol'])}: {f['reason'].rstrip('.')}" for f in top) + "."
    else:
        d["summary"] += " No move stood out against its own history or its comparison group."
    save(d)
    chk = run(work, "update_logs.py", check=False)
    ok = chk.returncode == 0

    report = [f"## Scripted brief ({'live' if a.live else 'dry run'}) for {data_date}, {edition}", "",
              f"- Validation by update_logs.py: {'passed' if ok else 'FAILED'}",
              f"- Items: {sum(len(g['items']) for g in d['groups'])}, priced: "
              f"{sum(1 for g in d['groups'] for i in g['items'] if i.get('last') is not None)}, "
              f"fetched this run: {sum(1 for r in results.values() if r['status'] == 'ok')}, second-sourced: {n_x}",
              f"- Flags: {len(d['flags'])}; headlines: {len(d['headlines'])}; reading: {len(d['reading'])}; "
              f"climate: {len(d['climate'])}; quiet feeds: {len(quiet)}",
              f"- Reading feeds read: {len(loaded_feeds)}; headline feeds read: {len(loaded_news)} of {len(HEADLINE_FEEDS)}",
              "", "### Summary", d["summary"], "", "### Gaps", *[f"- {g}" for g in d["gaps"]]]
    text = "\n".join(report)
    print(text)
    if not a.live:
        out = ROOT / "data/shadow/brief"
        out.mkdir(parents=True, exist_ok=True)
        shutil.copy(work / "data/latest.json", out / "latest.json")
        (out / "report.md").write_text(text + "\n", encoding="utf-8")
    if not a.live:
        shutil.rmtree(work.parent, ignore_errors=True)
    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp:
        open(sp, "a").write(text + "\n")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
