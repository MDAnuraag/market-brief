#!/usr/bin/env python3
"""Check data/latest.json, then update the append-only logs.

Run from anywhere:  python3 scripts/update_logs.py
Exit code 1 means latest.json is invalid. Fix it before pushing.

Logs written:
  data/history.jsonl      one line per run: closing levels for every item that had a price
  data/reading_log.jsonl  one line per article the first time it appears in the Reading section
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LATEST = DATA / "latest.json"
WATCHLIST = DATA / "watchlist.json"
HISTORY = DATA / "history.jsonl"
READING_LOG = DATA / "reading_log.jsonl"

NUMERIC = ("last", "chg_1d", "chg_1w", "chg_1m")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
errors = []


def err(msg):
    errors.append(msg)


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        sys.exit(f"ERROR: cannot read {path.name}: {e}")


def read_lines(path):
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            out.append(json.loads(raw))
    return out


def write_lines(path, rows):
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows), encoding="utf-8")


d = load(LATEST)
w = load(WATCHLIST)
symbols = {i["symbol"] for g in w["groups"] for i in g["items"]}

# ---- validation -----------------------------------------------------------
for key, typ in (("generated_at", str), ("data_date", str), ("edition", str), ("summary", str),
                 ("flags", list), ("groups", list), ("climate", list), ("sources", list), ("gaps", list)):
    if not isinstance(d.get(key), typ):
        err(f"{key}: missing or wrong type")

if d.get("edition") not in ("weekday", "saturday"):
    err("edition must be 'weekday' or 'saturday'")
if isinstance(d.get("data_date"), str) and not DATE.match(d["data_date"]):
    err("data_date must look like 2026-10-16")

for f in d.get("flags", []):
    if not (isinstance(f, dict) and f.get("symbol") and f.get("reason")):
        err(f"flag needs symbol and reason: {f}")

for g in d.get("groups", []):
    if not g.get("id") or not isinstance(g.get("items"), list):
        err(f"group needs id and items: {g.get('id')}")
        continue
    for i in g["items"]:
        s = i.get("symbol")
        if not s:
            err(f"{g['id']}: item without symbol")
            continue
        if s not in symbols:
            err(f"{g['id']}/{s}: not in watchlist.json")
        for k in NUMERIC:
            if k in i and i[k] is not None and not is_num(i[k]):
                err(f"{s}.{k}: must be a number or null")

for r in d.get("reading", []):
    t = r.get("title", "(untitled)")
    for k in ("source", "title", "url", "published", "summary"):
        if not r.get(k):
            err(f"reading '{t}': missing {k}")
    if r.get("url") and not str(r["url"]).startswith(("https://", "http://")):
        err(f"reading '{t}': url must start with http:// or https://")
    if r.get("access", "full") not in ("full", "partial", "teaser"):
        err(f"reading '{t}': access must be full, partial, or teaser")
    if len(str(r.get("summary", "")).split()) > 45:
        err(f"reading '{t}': summary is over 45 words")
    ov = r.get("overlap", [])
    if len(ov) > 3:
        err(f"reading '{t}': more than 3 overlaps")
    for o in ov:
        if o.get("symbol") not in symbols:
            err(f"reading '{t}': overlap symbol {o.get('symbol')} is not in watchlist.json")
        if o.get("strength") not in ("direct", "loose"):
            err(f"reading '{t}': overlap strength must be direct or loose")
        if not o.get("reason"):
            err(f"reading '{t}': overlap {o.get('symbol')} needs a reason")

if errors:
    print("latest.json has problems:")
    for e in errors:
        print(" -", e)
    sys.exit(1)

# ---- price history --------------------------------------------------------
last = {}
for g in d["groups"]:
    for i in g["items"]:
        if is_num(i.get("last")):
            last[i["symbol"]] = i["last"]

line = {
    "data_date": d["data_date"],
    "edition": d["edition"],
    "generated_at": d["generated_at"],
    "last": last,
    "flagged": [f["symbol"] for f in d["flags"]],
}
key = (line["data_date"], line["edition"])
rows = [r for r in read_lines(HISTORY) if (r.get("data_date"), r.get("edition")) != key]
rows.append(line)
write_lines(HISTORY, rows)

# ---- reading log ----------------------------------------------------------
rlog = read_lines(READING_LOG)
known = {r["url"] for r in rlog}
added = 0
for r in d.get("reading", []):
    if r["url"] in known:
        continue
    rlog.append({
        "first_seen": d["data_date"],
        "source": r["source"],
        "title": r["title"],
        "url": r["url"],
        "published": r["published"],
        "overlap": [o["symbol"] for o in r.get("overlap", [])],
    })
    known.add(r["url"])
    added += 1
if added:
    write_lines(READING_LOG, rlog)

print(f"ok: {sum(len(g['items']) for g in d['groups'])} items, {len(last)} priced, "
      f"{len(d.get('reading', []))} reading items ({added} new), history has {len(rows)} runs")
