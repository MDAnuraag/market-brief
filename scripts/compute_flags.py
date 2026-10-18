#!/usr/bin/env python3
"""Decide which moves deserve a flag, using each item's own sampled history.

Run after data/latest.json is written and before scripts/update_logs.py:

    python3 scripts/compute_flags.py --write

Without --write it only prints what it would flag.

How it works (the plain-language version is in README.md):
  1. Normal range. For every item, estimate its usual daily swing from the daily moves
     stored in data/history.jsonl. Recent days count more (half-life in watchlist.json),
     single outliers are clipped, and the estimate is blended with a starting guess
     (a prior) that fades as real observations pile up.
  2. Own z-score: this week's move divided by the usual weekly swing (daily swing x sqrt 5).
  3. Peer z-score: the move relative to the median of the item's comparison group, divided
     by the usual size of that gap. Groups are comparison cohorts, not competitors.
  4. Group event: the cohort's median move divided by the usual size of the cohort median.
  5. Tiers: "look_into" needs an unusual move on both own and peer measures (or a group
     event); "keep_an_eye" is the milder version, or a one-day shock.
Nothing here recommends a trade. It only says which moves are unusual compared with
what this page has already seen.
"""
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LATEST = DATA / "latest.json"
WATCHLIST = DATA / "watchlist.json"
HISTORY = DATA / "history.jsonl"


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def read_history(before_date):
    rows = {}
    if HISTORY.exists():
        for raw in HISTORY.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                r = json.loads(raw)
                rows[r["data_date"]] = r  # later editions of the same date replace earlier ones
    return [rows[k] for k in sorted(rows) if k < before_date]


def raw_ewma(obs, halflife, clip):
    """Weighted mean square of (age, value) pairs. Returns (variance, effective sample size)."""
    if not obs:
        return None, 0.0
    ws = [0.5 ** (a / halflife) for a, _ in obs]
    rs = [max(-clip, min(clip, v)) for _, v in obs]
    sw = sum(ws)
    s2 = sum(w * r * r for w, r in zip(ws, rs)) / sw
    neff = sw * sw / sum(w * w for w in ws)
    return s2, neff


def shrunk(obs, prior_var, halflife, k, clip):
    s2, neff = raw_ewma(obs, halflife, clip)
    if s2 is None:
        return prior_var, 0.0
    return (neff * s2 + k * prior_var) / (neff + k), neff


def main():
    write = "--write" in sys.argv
    d = json.loads(LATEST.read_text(encoding="utf-8"))
    w = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    cfg = w["flagging"]
    hl = cfg["halflife_sessions"]
    kw = cfg["prior_weight_sessions"]
    wins = cfg["winsor_sigmas"]
    min_cohort = cfg["min_cohort"]
    look, keep = cfg["look_into"], cfg["keep_an_eye"]
    sqrt5 = math.sqrt(5)

    hist = read_history(d["data_date"])
    nrows = len(hist)

    # ---- items in this run ------------------------------------------------
    group_of, items, names = {}, {}, {}
    for g in d["groups"]:
        for i in g["items"]:
            group_of[i["symbol"]] = g["id"]
            items[i["symbol"]] = i
            names[i["symbol"]] = i.get("name", i["symbol"])

    def klass(sym):
        c = cfg["class_by_symbol"].get(sym) or cfg["class_by_group"].get(group_of[sym])
        return c

    # ---- cohorts ----------------------------------------------------------
    cohort_name, cohort_members = {}, {}
    sym_cohort = {}
    for cid, c in cfg["peer_cohorts"].items():
        cohort_name[cid] = c["name"]
        cohort_members[cid] = list(c["symbols"])
        for s in c["symbols"]:
            sym_cohort.setdefault(s, cid)
    for g in d["groups"]:
        if g["id"] in cfg["no_peer_groups"]:
            continue
        gid = g["id"]
        left = [i["symbol"] for i in g["items"] if i["symbol"] not in sym_cohort]
        if left:
            cohort_name[gid] = g.get("title", gid)
            cohort_members[gid] = left
            for s in left:
                sym_cohort[s] = gid

    # ---- history series ---------------------------------------------------
    def d1_series(sym):
        out = []
        for idx, r in enumerate(hist):
            v = (r.get("chg") or {}).get(sym)
            if v and is_num(v[0]):
                out.append((nrows - 1 - idx, v[0]))
        return out

    # regime multiplier per class: how volatile the class has been versus its starting guess
    prior_d = cfg["priors_daily_vol_pct"]
    by_class = {}
    for sym in items:
        c = klass(sym)
        if c and c != "skip":
            by_class.setdefault(c, []).append(sym)
    regime = {}
    for c, syms in by_class.items():
        ratios, neffs = [], []
        for s in syms:
            s2, ne = raw_ewma(d1_series(s), hl, wins * prior_d[c])
            if s2 is not None and ne >= 3:
                ratios.append(s2 / prior_d[c] ** 2)
                neffs.append(ne)
        if len(ratios) >= cfg["regime_min_members"]:
            m = math.sqrt(statistics.median(ratios))
            m = max(0.6, min(2.5, m))
            lam = min(1.0, statistics.median(neffs) / 10.0)
            regime[c] = 1 + lam * (m - 1)
        else:
            regime[c] = 1.0

    sigma_d, neff_item = {}, {}
    for sym in items:
        c = klass(sym)
        if not c or c == "skip":
            continue
        base = prior_d[c] * regime[c]
        var, ne = shrunk(d1_series(sym), base ** 2, hl, kw, wins * base)
        sigma_d[sym] = math.sqrt(var)
        neff_item[sym] = ne

    def chg(sym, k):
        v = items[sym].get(k)
        return v if is_num(v) else None

    # ---- cohort statistics ------------------------------------------------
    cohort_info = {}
    for cid, members in cohort_members.items():
        present = [s for s in members if s in sigma_d and chg(s, "chg_1w") is not None]
        if len(present) < min_cohort:
            continue
        xs = {s: chg(s, "chg_1w") for s in present}
        med = statistics.median(xs.values())
        # history of the cohort median (daily) -> how big a cohort-wide move usually is
        med_obs = []
        for idx, r in enumerate(hist):
            vals = []
            for s in present:
                v = (r.get("chg") or {}).get(s)
                if v and is_num(v[0]):
                    vals.append(v[0])
            if len(vals) >= min_cohort:
                med_obs.append((nrows - 1 - idx, statistics.median(vals)))
        prior_med = cfg["cohort_median_share"] * statistics.median(sigma_d[s] for s in present)
        var_m, _ = shrunk(med_obs, prior_med ** 2, hl, kw, wins * prior_med)
        cohort_info[cid] = {"present": present, "xs": xs, "median": med,
                            "sigma_med_w": math.sqrt(var_m) * sqrt5}

    def peer_stats(sym):
        cid = sym_cohort.get(sym)
        info = cohort_info.get(cid)
        if not info or sym not in info["xs"]:
            return None
        others = [v for s, v in info["xs"].items() if s != sym]
        if len(others) < min_cohort - 1:
            return None
        resid = info["xs"][sym] - statistics.median(others)
        # history of this item's gap to the median of the others (daily)
        eps = []
        for idx, r in enumerate(hist):
            ch = r.get("chg") or {}
            mine = ch.get(sym)
            vals = [ch[s][0] for s in info["present"] if s != sym and s in ch and ch[s] and is_num(ch[s][0])]
            if mine and is_num(mine[0]) and len(vals) >= min_cohort - 1:
                eps.append((nrows - 1 - idx, mine[0] - statistics.median(vals)))
        prior_e = cfg["residual_share"] * sigma_d[sym]
        var_e, _ = shrunk(eps, prior_e ** 2, hl, kw, wins * prior_e)
        return cid, resid, math.sqrt(var_e) * sqrt5, statistics.median(others)

    if "--debug" in sys.argv:
        for cid,i in cohort_info.items(): print("cohort",cid,len(i["present"]),"median %.1f"%i["median"],"sigma_med_w %.1f"%i["sigma_med_w"],"z %.2f"%(i["median"]/i["sigma_med_w"]))
    # ---- decide flags -----------------------------------------------------
    flags, events = [], {}
    for cid, info in cohort_info.items():
        z = info["median"] / info["sigma_med_w"]
        if abs(z) >= look["min_z_group"]:
            events[cid] = z
            word = "fell" if info["median"] < 0 else "rose"
            flags.append({
                "symbol": cid.upper().replace("_", " "), "tier": "look_into",
                "reason": (f"{cohort_name[cid]}: median move {info['median']:+.1f}% this week across "
                           f"{len(info['present'])} names, {abs(z):.1f} times the group's usual weekly swing."),
                "z_group": round(z, 2), "_score": abs(z), "_cohort": cid,
            })

    suppressed = 0
    for sym in sigma_d:
        xw, x1 = chg(sym, "chg_1w"), chg(sym, "chg_1d")
        c = klass(sym)
        unit = "pp" if c == "rate" else "%"
        sig_w = sigma_d[sym] * sqrt5
        z_own = xw / sig_w if xw is not None else None
        z_1d = x1 / sigma_d[sym] if x1 is not None else None
        ps = peer_stats(sym)
        z_peer = ps[1] / ps[2] if ps else None

        tier = None
        if z_own is not None:
            extra = 0.0 if ps else cfg["no_peer_extra_z"]
            peer_ok = lambda lim: (abs(z_peer) >= lim) if ps else True  # noqa: E731
            if abs(z_own) >= look["min_z_own"] + extra and peer_ok(look["min_z_peer"]):
                tier = "look_into"
            elif abs(z_own) >= keep["min_z_own"] + extra and peer_ok(keep["min_z_peer"]):
                tier = "keep_an_eye"
        if tier is None and z_1d is not None and abs(z_1d) >= keep["min_z_own_1d"]:
            tier = "keep_an_eye"
        if tier is None:
            continue

        if ps and ps[0] in events and abs(z_peer) < cfg["suppress_in_event_unless_z_peer"]:
            suppressed += 1
            continue

        if z_own is not None and abs(z_own) >= keep["min_z_own"]:
            direction = "Down" if xw < 0 else "Up"
            bits = [f"{direction} {abs(xw):.1f}{unit} this week, {abs(z_own):.1f} times its usual weekly swing (about ±{sig_w:.1f}{unit})"]
            score = abs(z_own)
        else:
            direction = "Down" if x1 < 0 else "Up"
            bits = [f"{direction} {abs(x1):.1f}{unit} in one day, {abs(z_1d):.1f} times its usual daily swing (about ±{sigma_d[sym]:.1f}{unit})"]
            score = abs(z_1d)
        text = bits[0]
        if ps:
            gap = ps[1]
            text += f"; {abs(gap):.1f} points {'below' if gap < 0 else 'above'} the {cohort_name[ps[0]]} median"
        ne = items[sym].get("next_earnings")
        if ne:
            text += f"; reports earnings {ne}"
        text += "."
        f = {"symbol": sym, "tier": tier, "reason": text, "_score": score}
        if z_own is not None:
            f["z_own"] = round(z_own, 2)
        if z_peer is not None:
            f["z_peer"] = round(z_peer, 2)
        flags.append(f)

    vix = items.get("^VIX")
    if vix and is_num(vix.get("last")) and vix["last"] >= cfg["vix_above"]:
        flags.append({"symbol": "^VIX", "tier": "keep_an_eye", "_score": 2.6,
                      "reason": f"VIX closed at {vix['last']:.1f}, at or above the {cfg['vix_above']} line that usually marks stressed markets."})

    # ---- rank, cap, attach news ------------------------------------------
    tier_order = {"look_into": 0, "keep_an_eye": 1}
    flags.sort(key=lambda f: (tier_order[f["tier"]], "_cohort" not in f, -f["_score"]))  # group events lead
    look_list = [f for f in flags if f["tier"] == "look_into"][: cfg["max_look_into"]]
    keep_list = [f for f in flags if f["tier"] == "keep_an_eye"]
    room = cfg["max_flags"] - len(look_list)
    final = look_list + keep_list[: max(room, 0)]
    dropped = len(flags) - len(final)

    pieces = []
    for h in d.get("headlines", []):
        cov = h.get("coverage") or []
        pick = next((c for c in cov if c.get("kind") == "primary"), cov[0] if cov else None)
        if pick:
            pieces.append(("headline", h.get("story", ""), pick.get("url"), h.get("overlap", [])))
    for r in d.get("reading", []):
        pieces.append(("reading", r.get("title", ""), r.get("url"), r.get("overlap", [])))

    for f in final:
        syms = set(cohort_info[f["_cohort"]]["present"]) if "_cohort" in f else {f["symbol"]}
        news = []
        for kind, title, url, ov in sorted(pieces, key=lambda p: p[0] != "headline"):
            hit = [o for o in ov if o.get("symbol") in syms]
            if hit and url:
                news.append({"kind": kind, "title": title, "url": url,
                             "strength": "direct" if any(o.get("strength") == "direct" for o in hit) else "loose"})
        news.sort(key=lambda n: n["strength"] != "direct")
        if news:
            f["news"] = news[:2]
    for f in final:
        f.pop("_score", None)
        f.pop("_cohort", None)

    ne_vals = sorted(neff_item.values())
    median_neff = ne_vals[len(ne_vals) // 2] if ne_vals else 0
    if nrows == 0:
        mode, note = "starting estimates", "No earlier sessions on record, so normal ranges are starting estimates for each type of instrument."
    elif median_neff < 8:
        mode, note = "learning", f"{nrows} earlier session{'s' if nrows != 1 else ''} on record; normal ranges still lean on starting estimates."
    elif median_neff < 25:
        mode, note = "blending", f"{nrows} earlier sessions on record; normal ranges blend each item's own history with starting estimates."
    else:
        mode, note = "mostly learned", f"{nrows} earlier sessions on record; normal ranges come mostly from each item's own history."
    basis = {"sessions": nrows, "mode": mode, "note": note,
             "below_the_bar": dropped + suppressed}

    # ---- report -----------------------------------------------------------
    print(f"history sessions: {nrows}  mode: {mode}  median effective sample: {median_neff:.1f}")
    print(f"regime multipliers: " + ", ".join(f"{k} {v:.2f}" for k, v in sorted(regime.items())))
    print(f"flags: {len(final)} ({sum(f['tier']=='look_into' for f in final)} look into, "
          f"{sum(f['tier']=='keep_an_eye' for f in final)} keep an eye on); "
          f"{dropped} over the cap, {suppressed} folded into group events")
    for f in final:
        print(f"  [{f['tier']}] {f['symbol']}: {f['reason']}" + ("  +news" if f.get("news") else ""))

    if write:
        d["flags"] = final
        d["flag_basis"] = basis
        LATEST.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote flags and flag_basis to data/latest.json")


if __name__ == "__main__":
    main()
