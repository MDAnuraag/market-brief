#!/usr/bin/env python3
"""Operational indicator series: store, check, and summarize.

Series are defined in data/watchlist.json under "indicators" and stored append-only in
data/series/<id>.csv (one row per period per release; a revised value is a new row).

    python3 scripts/indicators.py due [--today YYYY-MM-DD]   what is due for a fetch
    python3 scripts/indicators.py add                         ingest "indicator_observations" from latest.json
    python3 scripts/indicators.py compute [--write]           summarize every series into latest.json["indicators"]

Nothing here guesses a number. A new value that looks like a units mistake or a wild jump
is rejected, and the run must re-check the source.
"""
import csv
import datetime as dt
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SERIES_DIR = DATA / "series"
LATEST = DATA / "latest.json"
HISTORY = DATA / "history.jsonl"
WATCHLIST = DATA / "watchlist.json"

HEADER = ["period", "value", "yoy_reported", "mom_reported", "ytd_reported",
          "status", "released", "source", "source_url", "note"]
NUM = ("value", "yoy_reported", "mom_reported", "ytd_reported")
STATUSES = ("final", "preliminary", "revised")
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


# ---- periods ------------------------------------------------------------------
WEEK_ANCHOR = dt.date(2000, 1, 7).toordinal()  # a Friday; weekly periods are indexed from here


def parse_period(p, freq, weekday=4):
    if freq == "weekly":
        try:
            d = dt.date.fromisoformat(p)
        except ValueError:
            raise ValueError(f"weekly period must be a week-ending date like 2026-10-16: {p}")
        if d.weekday() != weekday:
            raise ValueError(f"{p} is not a {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][weekday]}; use the week-ending date")
        return (d.toordinal() - WEEK_ANCHOR - (weekday - 4)) // 7
    if freq == "monthly":
        m = re.fullmatch(r"(\d{4})-(\d{2})", p)
        if not m or not 1 <= int(m.group(2)) <= 12:
            raise ValueError(f"monthly period must look like 2026-09: {p}")
        return int(m.group(1)) * 12 + int(m.group(2)) - 1
    m = re.fullmatch(r"(\d{4})-Q([1-4])", p)
    if not m:
        raise ValueError(f"quarterly period must look like 2026-Q2: {p}")
    return int(m.group(1)) * 4 + int(m.group(2)) - 1


def fmt_period(o, freq, weekday=4):
    if freq == "weekly":
        return dt.date.fromordinal(o * 7 + WEEK_ANCHOR + (weekday - 4)).isoformat()
    if freq == "monthly":
        return f"{o // 12}-{o % 12 + 1:02d}"
    return f"{o // 4}-Q{o % 4 + 1}"


def end_month_index(o, freq):
    """Calendar month index (year*12 + month-1) of the month the period ends in."""
    if freq == "monthly":
        return o
    return (o // 4) * 12 + (o % 4) * 3 + 2


def release_date(o, s):
    if s["freq"] == "weekly":
        return dt.date.fromordinal(o * 7 + WEEK_ANCHOR + (s.get("weekday", 4) - 4)) + dt.timedelta(days=s["release_offset_days"])
    idx = end_month_index(o, s["freq"]) + s["lag_months"]
    y, m = idx // 12, idx % 12 + 1
    return dt.date(y, m, s["release_day"])


def period_words(o, s):
    if s["freq"] == "weekly":
        return {"<Month>": "", "<Year>": "", "<Quarter>": "", "<Week>": fmt_period(o, "weekly", s.get("weekday", 4))}
    if s["freq"] == "monthly":
        return {"<Month>": MONTHS[o % 12], "<Year>": str(o // 12), "<Quarter>": ""}
    return {"<Month>": "", "<Year>": str(o // 4), "<Quarter>": f"Q{o % 4 + 1}"}


# ---- io -----------------------------------------------------------------------
def load_cfg():
    w = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    return w, w["indicators"]


def to_num(v):
    if v is None or v == "":
        return None
    return float(v)


def read_rows(sid):
    path = SERIES_DIR / f"{sid}.csv"
    rows = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            for i, r in enumerate(csv.DictReader(f)):
                for k in NUM:
                    r[k] = to_num(r.get(k))
                r["_i"] = i
                rows.append(r)
    return rows


def write_rows(sid, rows):
    SERIES_DIR.mkdir(exist_ok=True)
    with (SERIES_DIR / f"{sid}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for r in rows:
            w.writerow(["" if r.get(k) is None else r.get(k) for k in HEADER])


def pp(p, s):
    return parse_period(p, s["freq"], s.get("weekday", 4))


def fp(o, s):
    return fmt_period(o, s["freq"], s.get("weekday", 4))


def current_by_period(rows, s):
    cur = {}
    for r in sorted(rows, key=lambda r: (r["released"], r["_i"])):
        cur[pp(r["period"], s)] = r
    return cur


def today_from_args():
    if "--today" in sys.argv:
        return dt.date.fromisoformat(sys.argv[sys.argv.index("--today") + 1])
    return dt.datetime.now(dt.timezone.utc).date()


# ---- due ----------------------------------------------------------------------
def due_list(cfg, today):
    out = []
    for s in cfg["series"]:
        rows = read_rows(s["id"])
        cur = current_by_period(rows, s)
        if not cur and not s.get("first_period"):
            continue
        o = max(cur) + 1 if cur else pp(s["first_period"], s)
        while release_date(o, s) <= today:
            rd = release_date(o, s)
            if s["freq"] == "weekly" and today > rd + dt.timedelta(days=21):
                o = pp(fp(o, s), s) + 1  # a weekly series that has lapsed for weeks: ask only for the newest releases
                continue
            late = today > rd + dt.timedelta(days=cfg["grace_days"])
            hint = s["find"]
            for k, v in period_words(o, s).items():
                hint = hint.replace(k, v)
            out.append({"id": s["id"], "period": fp(o, s), "expected": rd.isoformat(),
                        "late": late, "search": " ".join(hint.split())})
            o += 1
    return out


def cmd_due():
    _, cfg = load_cfg()
    items = due_list(cfg, today_from_args())
    if not items:
        print("none due")
    for i in items:
        print(f"{i['id']} | period {i['period']} | expected {i['expected']}{' (late)' if i['late'] else ''} | search: {i['search']}")


# ---- add ----------------------------------------------------------------------
def cmd_add():
    w, cfg = load_cfg()
    d = json.loads(LATEST.read_text(encoding="utf-8"))
    obs = d.get("indicator_observations") or []
    defs = {s["id"]: s for s in cfg["series"]}
    errors, added, skipped = [], 0, 0
    by_series = {}
    for n, o in enumerate(obs):
        sid = o.get("id")
        s = defs.get(sid)
        if not s:
            errors.append(f"observation {n}: unknown series id {sid}")
            continue
        try:
            po = pp(str(o.get("period")), s)
        except ValueError as e:
            errors.append(f"{sid}: {e}")
            continue
        if not isinstance(o.get("value"), (int, float)) or isinstance(o.get("value"), bool):
            errors.append(f"{sid} {o['period']}: value must be a number")
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(o.get("released", ""))):
            errors.append(f"{sid} {o['period']}: released must look like 2026-10-08")
            continue
        if not str(o.get("source_url", "")).startswith(("https://", "http://")):
            errors.append(f"{sid} {o['period']}: source_url must be http(s)")
            continue
        if not o.get("source"):
            errors.append(f"{sid} {o['period']}: source is required")
            continue
        if o.get("status", "final") not in STATUSES:
            errors.append(f"{sid} {o['period']}: status must be one of {STATUSES}")
            continue
        for k in ("yoy_reported", "mom_reported", "ytd_reported"):
            if o.get(k) is not None and not isinstance(o[k], (int, float)):
                errors.append(f"{sid} {o['period']}: {k} must be a number or omitted")
        by_series.setdefault(sid, []).append((po, o))
    if errors:
        print("indicator_observations has problems:")
        for e in errors:
            print(" -", e)
        sys.exit(1)

    for sid, items in by_series.items():
        s = defs[sid]
        rows = read_rows(sid)
        cur = current_by_period(rows, s)
        for po, o in sorted(items, key=lambda x: x[0]):
            value = float(o["value"])
            same = [r for r in rows if pp(r["period"], s) == po]
            if any(abs(r["value"] - value) < 1e-9 and r["released"] == o["released"] for r in same):
                skipped += 1
                continue
            status = o.get("status", "final")
            if same:
                prev = max(same, key=lambda r: (r["released"], r["_i"]))
                if abs(prev["value"] - value) < 1e-9:
                    skipped += 1  # same value restated on a later release: nothing new to record
                    continue
                change = abs(value / prev["value"] - 1) * 100
                if change > 10:
                    errors.append(f"{sid} {o['period']}: revision of {change:.0f}% (from {prev['value']} to {value}); re-check the units and the source")
                    continue
                status = "revised"
            else:
                before = cur.get(po - 1)
                after = cur.get(po + 1)
                ref = before or after
                if ref and not (1 / 3 <= value / ref["value"] <= 3):
                    errors.append(f"{sid} {o['period']}: {value} is more than 3x away from the neighbouring period ({ref['value']}); re-check units")
                    continue
            rows.append({"period": o["period"], "value": value, "yoy_reported": o.get("yoy_reported"),
                         "mom_reported": o.get("mom_reported"), "ytd_reported": o.get("ytd_reported"),
                         "status": status, "released": o["released"], "source": o["source"],
                         "source_url": o["source_url"], "note": o.get("note", ""), "_i": len(rows)})
            added += 1
        rows.sort(key=lambda r: (pp(r["period"], s), r["released"], r["_i"]))
        if not errors:
            write_rows(sid, rows)
    if errors:
        print("indicator_observations rejected (nothing written for the affected series):")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    d.pop("indicator_observations", None)
    LATEST.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"indicators: {added} new rows, {skipped} already on file")


# ---- compute ------------------------------------------------------------------
def pct(a, b):
    return (a / b - 1) * 100 if b else None


def build_series(s, cfg, today, symbols):
    rows = read_rows(s["id"])
    cur = current_by_period(rows, s)
    if not cur:
        return {"id": s["id"], "name": s["name"], "group": s["group"], "unit": s["unit"], "freq": s["freq"],
                "source": s["source"], "tier": s["tier"], "site": s.get("site"), "status": "no data",
                "touches": [], "history_n": 0}
    step_back = {"weekly": 52, "monthly": 12}.get(s["freq"], 4)
    last_o = max(cur)
    first_o = min(cur)
    last = cur[last_o]

    def yoy_of(o):
        r = cur.get(o)
        if not r:
            return None, None
        if r["yoy_reported"] is not None:
            return r["yoy_reported"], "reported"
        base = cur.get(o - step_back)
        if base:
            return pct(r["value"], base["value"]), "computed"
        return None, None

    def mom_of(o):
        r = cur.get(o)
        if not r:
            return None, None
        if r["mom_reported"] is not None:
            return r["mom_reported"], "reported"
        base = cur.get(o - 1)
        if base:
            return pct(r["value"], base["value"]), "computed"
        return None, None

    yoy, yoy_src = yoy_of(last_o)
    yoy_prev, _ = yoy_of(last_o - 1)
    mom, mom_src = mom_of(last_o)
    yoys = {o: yoy_of(o)[0] for o in cur if yoy_of(o)[0] is not None}
    need = cfg["min_periods_for_range"]
    z, status = None, f"building history ({len(yoys)} of {need} year-on-year readings)"
    if not yoys:
        status = f"building history ({len(cur)} reading{'s' if len(cur) != 1 else ''} on file)"
    hist = [v for o, v in yoys.items() if o != last_o]
    if yoy is not None and len(yoys) >= need and len(hist) >= 3:
        med = statistics.median(hist)
        mad = 1.4826 * statistics.median(abs(v - med) for v in hist)
        mad = max(mad, 0.5)
        z = (yoy - med) / mad
        status = "range test active"

    # revisions: periods with more than one row
    revision = None
    by_p = {}
    for r in rows:
        by_p.setdefault(pp(r["period"], s), []).append(r)
    for o in sorted(by_p, reverse=True):
        rs = sorted(by_p[o], key=lambda r: (r["released"], r["_i"]))
        if len(rs) > 1:
            revision = {"period": fp(o, s), "from": rs[0]["value"], "to": rs[-1]["value"],
                        "pct": round(pct(rs[-1]["value"], rs[0]["value"]), 2)}
            break

    # reconciliation checks
    checks = []
    if s.get("ytd_check"):
        ok = bad = 0
        for o, r in cur.items():
            if r["ytd_reported"] is None:
                continue
            if o % 12 == 0:
                prev_ytd = 0.0
            else:
                p = cur.get(o - 1)
                if not p or p["ytd_reported"] is None:
                    continue
                prev_ytd = p["ytd_reported"]
            tol = max(0.011, abs(r["value"]) * 0.0002)
            if abs(r["ytd_reported"] - prev_ytd - r["value"]) <= tol:
                ok += 1
            else:
                bad += 1
        if ok + bad:
            checks.append({"name": "year-to-date reconciles", "ok": ok, "bad": bad})
    mok = mbad = 0
    implied = []
    for r in rows:
        if r["mom_reported"] is None:
            continue
        po = pp(r["period"], s)
        prior = [x for x in rows if pp(x["period"], s) == po - 1 and x["released"] <= r["released"]]
        if not prior:
            continue
        prior = max(prior, key=lambda x: (x["released"], x["_i"]))
        calc = pct(r["value"], prior["value"])
        tol = 0.6 if float(r["mom_reported"]).is_integer() else 0.15
        if abs(calc - r["mom_reported"]) <= tol:
            mok += 1
        else:
            mbad += 1
            implied.append({"period": r["period"], "stated": r["mom_reported"], "on_file": round(calc, 1),
                            "implied_prior": round(r["value"] / (1 + r["mom_reported"] / 100), 2),
                            "prior_on_file": prior["value"]})
    if mok + mbad:
        chk = {"name": "stated change matches values on file", "ok": mok, "bad": mbad}
        if implied:
            chk["detail"] = implied
        checks.append(chk)

    # seasonal range: the same period in each of the last five years
    same = [cur[last_o - step_back * k]["value"] for k in range(1, 6) if (last_o - step_back * k) in cur]
    range5 = None
    if len(same) >= 3:
        avg = sum(same) / len(same)
        range5 = {"n": len(same), "avg": round(avg, 2), "min": min(same), "max": max(same),
                  "vs_avg_pct": round(pct(last["value"], avg), 1),
                  "position": "above" if last["value"] > max(same) else "below" if last["value"] < min(same) else "within"}

    # gaps inside the range on file
    gaps = [fp(o, s) for o in range(first_o, last_o) if o not in cur]
    if len(gaps) > 8:
        gaps = gaps[:4] + [f"... {len(gaps) - 8} more ..."] + gaps[-4:]

    nxt = release_date(last_o + 1, s)
    late = today > nxt + dt.timedelta(days=cfg["grace_days"])
    spark = [{"p": fp(o, s), "v": cur[o]["value"]} for o in sorted(cur)][-26:]
    return {
        "id": s["id"], "name": s["name"], "group": s["group"], "unit": s["unit"], "freq": s["freq"],
        "smoothing": s.get("smoothing", "none"), "source": s["source"], "tier": s["tier"],
        "site": s.get("site"), "period": fp(last_o, s), "value": last["value"],
        "row_status": last["status"], "released": last["released"], "source_name": last["source"],
        "source_url": last["source_url"], "yoy": None if yoy is None else round(yoy, 1), "yoy_source": yoy_src,
        "yoy_prev": None if yoy_prev is None else round(yoy_prev, 1),
        "yoy_change": None if (yoy is None or yoy_prev is None) else round(yoy - yoy_prev, 1),
        "mom": None if mom is None else round(mom, 1), "mom_source": mom_src,
        "z": None if z is None else round(z, 2), "status": status, "history_n": len(cur),
        "revision": revision, "checks": checks, "gaps": gaps, "range5": range5,
        "next_expected": nxt.isoformat(), "late": late,
        "touches": [x for x in s.get("touches", []) if x in symbols], "note": s.get("note", ""),
        "spark": spark,
    }


def compute_spreads(w, d):
    cfg = w.get("spreads", {}).get("list", [])
    hist = []
    if HISTORY.exists():
        for line in HISTORY.read_text(encoding="utf-8").splitlines():
            if line.strip():
                hist.append(json.loads(line))
    hist.sort(key=lambda r: (r["data_date"], r.get("edition", "")))
    last_now = {i["symbol"]: i["last"] for g in d["groups"] for i in g["items"] if isinstance(i.get("last"), (int, float))}
    out = []
    for sp in cfg:
        def val(m):
            a, b = m.get(sp["a"]), m.get(sp["b"])
            if a is None or b is None or not b:
                return None
            return a - b if sp["kind"] == "diff" else a * sp["factor"] / b
        v = val(last_now)
        if v is None:
            continue
        series = [(r["data_date"], val(r.get("last", {}))) for r in hist]
        series = [(dte, x) for dte, x in series if x is not None and dte != d["data_date"]]
        series.append((d["data_date"], v))
        prev = series[-2][1] if len(series) > 1 else None
        out.append({"id": sp["id"], "name": sp["name"], "unit": sp["unit"], "kind": sp["kind"],
                    "value": round(v, 2), "prev": None if prev is None else round(prev, 2),
                    "change": None if prev is None else round(v - prev, 2),
                    "spark": [{"p": dte, "v": round(x, 3)} for dte, x in series[-20:]],
                    "touches": [t for t in sp.get("touches", []) if t in last_now or True],
                    "inputs": f"{sp['a']} and {sp['b']}", "note": sp.get("note", ""), "as_of": d["data_date"]})
    return out


def cmd_compute():
    w, cfg = load_cfg()
    today = today_from_args()
    symbols = {i["symbol"] for g in w["groups"] for i in g["items"]}
    out = [build_series(s, cfg, today, symbols) for s in cfg["series"]]
    for r in out:
        extra = ""
        if r.get("checks"):
            extra = " | " + "; ".join(f"{c['name']}: {c['ok']} ok, {c['bad']} off" for c in r["checks"])
        print(f"{r['id']}: {r.get('period')} {r.get('value')} yoy {r.get('yoy')} ({r.get('status')}){extra}"
              f"{' | LATE' if r.get('late') else ''}{' | gaps ' + ','.join(r['gaps']) if r.get('gaps') else ''}")
    if True:
        d = json.loads(LATEST.read_text(encoding="utf-8"))
        for sp in compute_spreads(w, d):
            print(f"spread {sp['id']}: {sp['value']} {sp['unit']} (previous {sp['prev']})")
    if "--write" in sys.argv:
        d = json.loads(LATEST.read_text(encoding="utf-8"))
        d["indicators"] = out
        d["spreads"] = compute_spreads(w, d)
        LATEST.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote indicators to data/latest.json")


if __name__ == "__main__":
    cmds = {"due": cmd_due, "add": cmd_add, "compute": cmd_compute}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]]()
