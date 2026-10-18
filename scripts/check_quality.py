#!/usr/bin/env python3
"""Source tiers and second-source checks.

    python3 scripts/check_quality.py --plan     list the items that need a second source this run
    python3 scripts/check_quality.py --write    compare second sources, mark flags, write "quality"

Every priced item carries `src` (the issuing body) and `tier` (official / reported / aggregator).
An item that was checked also carries `xcheck`: {src, tier, last, chg_1w (optional), as_of (optional)}.
The script never fetches anything. It only compares what the run recorded.
"""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LATEST = ROOT / "data" / "latest.json"
WATCHLIST = ROOT / "data" / "watchlist.json"
HISTORY = ROOT / "data" / "history.jsonl"


def num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def item_class(w, sym, group):
    f = w["flagging"]
    return f["class_by_symbol"].get(sym) or f["class_by_group"].get(group) or "default"


def tol_key(cls, table):
    return cls if cls in table else "default"


def flagged_symbols(d, w, top=3):
    """Item symbols behind the flags. For a flagged cohort, the `top` members with the largest weekly move."""
    out = set()
    wk = {i["symbol"]: abs(i["chg_1w"]) for _, i in all_items(d) if num(i.get("chg_1w"))}
    cohorts = {k.upper().replace("_", " "): v for k, v in w["flagging"]["peer_cohorts"].items()}
    cohorts.update({k.upper(): v for k, v in w["flagging"]["peer_cohorts"].items()})
    for f in d.get("flags", []):
        s = f["symbol"]
        if s in cohorts:
            members = cohorts[s]
            out.update(sorted(members["symbols"], key=lambda m: -wk.get(m, 0))[:top])
        else:
            out.add(s)
    return out


def all_items(d):
    for g in d["groups"]:
        for i in g["items"]:
            yield g["id"], i


def plan(d, w):
    q = w["quality"]
    flagged = flagged_symbols(d, w)
    priced = [(g, i) for g, i in all_items(d) if num(i.get("last"))]
    must = [i["symbol"] for g, i in priced if i["symbol"] in flagged]
    pool = sorted(i["symbol"] for g, i in priced if i["symbol"] not in flagged)
    n = q["audit_sample_per_run"]
    sample = []
    if pool:
        day = dt.date.fromisoformat(d["data_date"]).toordinal()
        start = (day * n) % len(pool)
        sample = [pool[(start + k) % len(pool)] for k in range(min(n, len(pool)))]
    missing = [i["symbol"] for g, i in priced if not i.get("src") or i.get("tier") not in q["tiers"]]
    return must, sample, missing


def cmd_plan(d, w):
    must, sample, missing = plan(d, w)
    print("Second source needed (flagged):", ", ".join(must) or "none")
    print("Second source needed (audit sample):", ", ".join(sample) or "none")
    if missing:
        print("Items missing src or tier:", ", ".join(missing))
    print("Record each check as item.xcheck = {src, tier, last, chg_1w, as_of}. The second source must be independent of the first.")


def cmd_write(d, w):
    q = w["quality"]
    must, sample, missing = plan(d, w)
    counts = {t: 0 for t in q["tiers"]}
    checked = agreed = differed = 0
    status = {}
    for g, i in all_items(d):
        if num(i.get("last")) and i.get("tier") in counts:
            counts[i["tier"]] += 1
        x = i.get("xcheck")
        if not x:
            continue
        if not (x.get("src") and num(x.get("last"))):
            print(f"{i['symbol']}: xcheck needs src and a numeric last")
            sys.exit(1)
        if x.get("tier") not in q["tiers"]:
            print(f"{i['symbol']}: xcheck tier must be one of {list(q['tiers'])}")
            sys.exit(1)
        if x["src"] == i.get("src"):
            print(f"{i['symbol']}: xcheck source is the same as the first source; it must be independent")
            sys.exit(1)
        cls = item_class(w, i["symbol"], g)
        tl = q["tolerance_last_pct"][tol_key(cls, q["tolerance_last_pct"])]
        tw = q["tolerance_week_points"][tol_key(cls, q["tolerance_week_points"])]
        if not num(i.get("last")):
            continue
        if x.get("as_of") and x["as_of"] != (i.get("as_of") or d["data_date"]):
            st, why = "date", f"second source is dated {x['as_of']}"
        else:
            diff = abs(x["last"] / i["last"] - 1) * 100 if i["last"] else None
            if diff is None:
                continue
            why = f"last differs by {diff:.2f}% (limit {tl}%)"
            ok = diff <= tl
            if ok and num(x.get("chg_1w")) and num(i.get("chg_1w")):
                dw = abs(x["chg_1w"] - i["chg_1w"])
                why += f"; week change differs by {dw:.2f} points (limit {tw})"
                ok = dw <= tw
            st = "agree" if ok else "differ"
        x["status"] = st
        x["detail"] = why
        status[i["symbol"]] = st
        checked += 1
        agreed += st == "agree"
        differed += st == "differ"

    for f in d.get("flags", []):
        f.pop("verified", None)
        sym = f["symbol"]
        members = flagged_symbols({"flags": [f], "groups": d["groups"]}, w)
        got = [status[s] for s in members if s in status]
        if not got:
            f["verified"] = {"status": "single", "text": "one source so far"}
        elif "differ" in got:
            f["verified"] = {"status": "differ", "text": "sources disagree; treat the move as unconfirmed"}
        elif all(s == "agree" for s in got):
            f["verified"] = {"status": "agree", "text": f"second source agrees ({len(got)} checked)"}
        else:
            f["verified"] = {"status": "single", "text": "second source dated differently; not a match"}

    flags_confirmed = sum(1 for f in d.get("flags", []) if f["verified"]["status"] == "agree")
    d["quality"] = {
        "tiers": counts, "cross_checked": checked, "agreed": agreed, "differed": differed,
        "flags_confirmed": flags_confirmed, "flags_total": len(d.get("flags", [])),
        "planned_flagged": len(must), "planned_sample": len(sample),
        "untiered": missing,
        "differences": [{"symbol": s, "detail": i["xcheck"]["detail"]} for _, i in all_items(d)
                        for s in [i["symbol"]] if status.get(s) == "differ"],
    }
    LATEST.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"quality: tiers {counts}; {checked} cross-checked ({agreed} agree, {differed} differ); "
          f"{flags_confirmed} of {len(d.get('flags', []))} flags confirmed")
    if missing:
        print("warning, items missing src or tier:", ", ".join(missing))
    miss_plan = [s for s in must if s not in status]
    if miss_plan:
        print("flagged items still without a second source (add one or note it in gaps):", ", ".join(miss_plan))


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("--plan", "--write"):
        sys.exit(__doc__)
    w = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    d = json.loads(LATEST.read_text(encoding="utf-8"))
    (cmd_plan if sys.argv[1] == "--plan" else cmd_write)(d, w)
