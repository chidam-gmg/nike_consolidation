"""CORE forecast + allocation engine (dev, 2026-10-30).

1. Weekly style x country history (zero-filled) -> demand pattern per series (ADI / CV2: smooth, erratic,
   intermittent, lumpy).
2. Model contest — 5 simple models, 3 backtest cut-offs, 4-week horizon; error = volume-weighted (WAPE) and bias,
   scored per demand pattern; the champion per pattern is used for the forecast (no model is fixed up front).
3. Forecast — style x country, 8 weeks ahead, P50 = champion level, P80 = Poisson-style upper band.
4. Allocation — 2 weeks of forecast -> store (store share of the style in its country) -> size (country size curve)
   -> target = max(demand, 1 per key size the store carries) -> need net of ATP + in-transit (rounded up) ->
   DC rationing (exclusive primary pool, then the shared UAE hub globally per size; priority grade then demand).
Every stage appends timestamped lines to an event log that the Agent lab view replays.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HORIZON_WEEKS = 8
BACKTEST_CUTOFFS = (12, 8, 4)     # weeks before the last full week
BACKTEST_H = 4
COVER_DAYS = 14
GRADE_RANK = {"BEACON": 0, "JORDAN": 0, "A+": 1, "A": 2, "B": 3, "C": 4, "NVS": 5}
METHODS = ("MA4", "MA12", "SES", "CROSTON_SBA", "TSB")


class RunLog:
    """Stage timings + plain-language event lines for the agent view."""

    def __init__(self):
        self.stages, self.events = [], []

    def now(self):
        return int(time.time() * 1000)

    def stage(self, key, agent):
        log = self

        class _S:
            def __enter__(s):
                s.t0 = log.now(); log.say(agent, "run", f"start · {key.replace('_', ' ')}", s.t0); return s

            def __exit__(s, *exc):
                log.stages.append({"stage": key, "agent": agent, "start": s.t0, "end": log.now(),
                                   "result": "FAILED" if exc[0] else "SUCCESS"})
                return False
        return _S()

    def say(self, agent, level, text, ts=None):
        self.events.append({"ts": ts or self.now(), "agent": agent, "level": level, "text": text})


def _num(df, cols):
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


# ---------------- forecasting models (level per series from history up to t) ----------------
def _ma(y, k):
    return y[:, -k:].mean(axis=1)


def _ses(y, a=0.2):
    lvl = y[:, 0].astype(float)
    for t in range(1, y.shape[1]):
        lvl = a * y[:, t] + (1 - a) * lvl
    return lvl


def _croston_sba(y, a=0.1):
    n, T = y.shape
    z = np.zeros(n); p = np.ones(n); q = np.ones(n); init = np.zeros(n, bool)
    for t in range(T):
        nz = y[:, t] > 0
        first = nz & ~init
        z[first] = y[first, t]; p[first] = q[first]; init |= first
        upd = nz & ~first
        z[upd] = a * y[upd, t] + (1 - a) * z[upd]
        p[upd] = a * q[upd] + (1 - a) * p[upd]
        q = np.where(nz, 1, q + 1)
    return np.where(init, (1 - a / 2) * z / np.maximum(p, 1e-9), 0.0)


def _tsb(y, a=0.1, b=0.1):
    n, T = y.shape
    nzmask = y > 0
    prob = nzmask.mean(axis=1).astype(float)
    cnt = nzmask.sum(axis=1)
    z = np.where(cnt > 0, np.where(nzmask, y, 0).sum(axis=1) / np.maximum(cnt, 1), 0.0)
    for t in range(T):
        nz = nzmask[:, t]
        prob = b * nz + (1 - b) * prob
        z = np.where(nz, a * y[:, t] + (1 - a) * z, z)
    return prob * z


def level(y, method):
    return {"MA4": lambda: _ma(y, 4), "MA12": lambda: _ma(y, 12), "SES": lambda: _ses(y),
            "CROSTON_SBA": lambda: _croston_sba(y), "TSB": lambda: _tsb(y)}[method]()


def classify(y):
    """Syntetos-Boylan demand pattern over the last 52 weeks."""
    w = y[:, -52:]
    nz = w > 0
    cnt = nz.sum(axis=1)
    adi = np.where(cnt > 0, 52.0 / np.maximum(cnt, 1), np.inf)
    mean_nz = np.where(cnt > 0, np.nansum(np.where(nz, w, 0), axis=1) / np.maximum(cnt, 1), 0)
    sd_nz = np.sqrt(np.where(cnt > 0, np.nansum(np.where(nz, (w - mean_nz[:, None]) ** 2, 0), axis=1) / np.maximum(cnt, 1), 0))
    cv2 = np.where(mean_nz > 0, (sd_nz / np.maximum(mean_nz, 1e-9)) ** 2, 0)
    return np.select([cnt == 0, (adi < 1.32) & (cv2 < 0.49), adi < 1.32, cv2 < 0.49],
                     ["inactive", "smooth", "erratic", "intermittent"], "lumpy")


# ---------------- pipeline ----------------
def run(weekly, store_size, stock, stores, key_sizes, dc, dc_map, current_replen, log: RunLog | None = None):
    log = log or RunLog()
    out = {}

    # 1 · Scout — weekly history matrix
    with log.stage("history", "scout"):
        weekly = _num(weekly.copy(), ["units"])
        weekly["wk"] = pd.to_datetime(weekly["wk"])
        weeks = pd.date_range(weekly["wk"].min(), weekly["wk"].max(), freq="7D")
        piv = weekly.pivot_table(index=["country", "parent"], columns="wk", values="units", aggfunc="sum").reindex(columns=weeks).fillna(0)
        meta = weekly.groupby(["country", "parent"])[["division", "gender", "category"]].max()
        y = piv.to_numpy(float)
        last_week = weeks[-1]
        log.say("scout", "tool", "read fct_sap_sales_detail_v · CORE / YEAR Nike articles · retail + e-commerce · Nike stores")
        log.say("scout", "out", f"{len(piv):,} style × country series · {len(weeks)} weeks up to the week of {last_week:%d %b %Y} · "
                                 f"{int(y.sum()):,} units")

    # 2 · Profiler — demand pattern per series
    with log.stage("profile", "profiler"):
        cls = classify(y)
        vol = y[:, -52:].sum(axis=1)
        prof = pd.DataFrame({"demand_class": cls, "units_52w": vol}, index=piv.index)
        mix = prof.groupby("demand_class")["units_52w"].agg(["size", "sum"])
        mix["pct"] = mix["sum"] / mix["sum"].sum() * 100
        log.say("profiler", "out", " · ".join(f"{k} {int(r['size']):,} series ({r['pct']:.0f}% units)"
                                              for k, r in mix.sort_values("sum", ascending=False).iterrows()))
        log.say("profiler", "hold", f"long tail: {int(mix.loc[mix.index.isin(['intermittent', 'lumpy']), 'size'].sum()):,} series carry "
                                    f"{mix.loc[mix.index.isin(['intermittent', 'lumpy']), 'pct'].sum():.0f}% of units")

    # 3 · Tournament — backtest every model per demand pattern
    with log.stage("tournament", "tournament"):
        rows = []
        T = y.shape[1]
        for back in BACKTEST_CUTOFFS:
            cut = T - back
            hist, act = y[:, :cut], y[:, cut:cut + BACKTEST_H].sum(axis=1)
            for m in METHODS:
                f = level(hist, m) * BACKTEST_H
                for c in np.unique(cls):
                    k = cls == c
                    a_sum = act[k].sum()
                    rows.append({"cutoff_week": str(weeks[cut - 1].date()), "demand_class": c, "method": m,
                                 "series": int(k.sum()), "actual_units": float(a_sum),
                                 "wape": float(np.abs(f[k] - act[k]).sum() / a_sum) if a_sum else np.nan,
                                 "bias": float((f[k] - act[k]).sum() / a_sum) if a_sum else np.nan})
        bt = pd.DataFrame(rows)
        board = bt.groupby(["demand_class", "method"]).agg(wape=("wape", "mean"), bias=("bias", "mean"),
                                                           actual_units=("actual_units", "sum")).reset_index()
        champ = (board.dropna(subset=["wape"]).sort_values(["demand_class", "wape"])
                 .groupby("demand_class").head(1).set_index("demand_class"))
        base = board[board["method"] == "MA12"].set_index("demand_class")
        out["backtest"], out["leaderboard"] = bt, board.assign(champion=board.apply(
            lambda r: r["demand_class"] in champ.index and champ.loc[r["demand_class"], "method"] == r["method"], axis=1))
        log.say("tournament", "tool", f"{len(METHODS)} models × {len(BACKTEST_CUTOFFS)} cut-offs × {BACKTEST_H}-week horizon")
        for c, r in champ.iterrows():
            if c == "inactive":
                continue
            b = base.loc[c, "wape"] if c in base.index else np.nan
            log.say("tournament", "out", f"{c}: {r['method']} wins · error {r['wape']*100:.0f}% (12-week average {b*100:.0f}%) · bias {r['bias']*100:+.0f}%")
        w_all = (champ["wape"] * champ["actual_units"]).sum() / champ["actual_units"].sum()
        w_base = (base["wape"] * base["actual_units"]).sum() / base["actual_units"].sum()
        out["error_champion"], out["error_baseline"] = float(w_all), float(w_base)
        log.say("tournament", "out", f"overall error {w_all*100:.0f}% vs {w_base*100:.0f}% for a plain 12-week average")

    # 4 · Forecaster — champion per pattern, 8 weeks
    with log.stage("forecast", "forecaster"):
        lv = np.zeros(len(piv))
        meth = np.array(["MA12"] * len(piv), dtype=object)
        for c in np.unique(cls):
            m = champ.loc[c, "method"] if c in champ.index else "MA12"
            k = cls == c
            lv[k] = level(y[k], m); meth[k] = m
        p80 = lv + 0.84 * np.sqrt(np.maximum(lv, 0))
        fc_rows = []
        for i, (country, parent) in enumerate(piv.index):
            if lv[i] <= 0:
                continue
            for h in range(1, HORIZON_WEEKS + 1):
                fc_rows.append((country, parent, (last_week + pd.Timedelta(days=7 * h)).date(), round(float(lv[i]), 3),
                                round(float(p80[i]), 3), meth[i], cls[i]))
        fc = pd.DataFrame(fc_rows, columns=["country", "parent", "week_start", "units_p50", "units_p80", "method", "demand_class"])
        fc = fc.merge(meta.reset_index(), on=["country", "parent"], how="left")
        out["forecast"] = fc
        out["history"] = weekly
        nxt = fc[fc["week_start"] == fc["week_start"].min()]
        log.say("forecaster", "out", f"{fc[['country','parent']].drop_duplicates().shape[0]:,} series forecast {HORIZON_WEEKS} weeks ahead · "
                                     f"next week {nxt['units_p50'].sum():,.0f} units (P80 {nxt['units_p80'].sum():,.0f})")
        fcl = pd.DataFrame({"weekly_p50": lv}, index=piv.index).reset_index()

    # 5 · Store splitter — country forecast -> store by its share of the style
    with log.stage("store_split", "splitter"):
        ss = _num(store_size.copy(), ["units"])
        st_par = ss.groupby(["country", "store", "parent"])["units"].sum().reset_index()
        st_par["store_share"] = st_par["units"] / st_par.groupby(["country", "parent"])["units"].transform("sum")
        stores = stores.copy()
        active = set(stores["store"])
        carry = _num(stock.copy(), ["atp", "intransit", "key_size"])
        carried = carry[carry["atp"] + carry["intransit"] > 0][["store", "parent"]].drop_duplicates()
        sp = st_par[st_par["store"].isin(active)].merge(fcl, on=["country", "parent"])
        sp = sp.merge(carried.assign(carries=1), on=["store", "parent"], how="left")
        sp = sp[sp["carries"].fillna(0) == 1]                       # only styles the store already ranges
        sp["demand_cover"] = sp["weekly_p50"] * COVER_DAYS / 7 * sp["store_share"]
        log.say("splitter", "tool", f"store share of each style in its country (26 weeks) · {len(active)} active stores")
        log.say("splitter", "out", f"{len(sp):,} store × style lines · {sp['demand_cover'].sum():,.0f} units of {COVER_DAYS}-day demand")

    # 6 · Size curator — country size curve
    with log.stage("size_split", "curator"):
        curve = ss.groupby(["country", "parent", "art", "size"])["units"].sum().reset_index()
        curve["size_share"] = curve["units"] / curve.groupby(["country", "parent"])["units"].transform("sum")
        ks = _num(key_sizes.copy(), ["key_size"])
        lines = sp.merge(curve[["country", "parent", "art", "size", "size_share"]], on=["country", "parent"])
        # every key size the store should display, even if the curve has no sales for it yet
        key_rows = sp[["country", "store", "parent"]].merge(ks[ks["key_size"] == 1][["parent", "art", "size"]], on="parent")
        lines = lines.merge(key_rows.assign(_k=1), on=["country", "store", "parent", "art", "size"], how="outer")
        lines["size_share"] = lines["size_share"].fillna(0)
        lines["demand_cover"] = lines.groupby(["store", "parent"])["demand_cover"].transform("max").fillna(0)
        lines["demand"] = lines["demand_cover"] * lines["size_share"]
        lines = lines.merge(ks[["art", "key_size"]], on="art", how="left")
        log.say("curator", "tool", "size curve per style and country (26 weeks of size sales) · 1 facing per key size")
        log.say("curator", "out", f"{len(lines):,} store × size lines")

    # 7 · Stock checker — net of ATP + in-transit
    with log.stage("stock_check", "stock"):
        lines = lines.merge(carry[["store", "art", "atp", "intransit"]], on=["store", "art"], how="left")
        lines[["atp", "intransit", "key_size"]] = lines[["atp", "intransit", "key_size"]].fillna(0)
        lines["target"] = np.maximum(lines["demand"], np.where(lines["key_size"] == 1, 1.0, 0.0))
        lines["need"] = np.ceil(np.maximum(0, lines["target"] - lines["atp"] - lines["intransit"]) - 1e-9).astype(int)
        log.say("stock", "tool", "read effective_stock (ATP today) + store_intransit")
        log.say("stock", "out", f"{int((lines['need'] > 0).sum()):,} sizes need stock · {int(lines['need'].sum()):,} units short of {COVER_DAYS}-day cover")

    # 8 · Allocator — DC rationing (primary pool, then shared hub globally)
    with log.stage("allocate", "allocator"):
        lines = lines.merge(stores[["store", "grade", "store_name"]], on="store", how="left")
        lines["grade_rank"] = lines["grade"].map(GRADE_RANK).fillna(6)
        dcs = _num(dc.copy(), ["pool_atp"]).set_index(["pool_id", "art"])["pool_atp"].to_dict()
        dmap = dc_map.set_index("country").to_dict("index")
        need = lines[lines["need"] > 0].sort_values(["grade_rank", "demand"], ascending=[True, False]).copy()
        alloc, src = [], []
        for _, r in need.iterrows():
            q, s = 0, []
            m = dmap.get(r["country"], {})
            for pool in ([m.get("primary_pool")] if m.get("primary_pool") else []) + (["HUB"] if str(m.get("draws_hub", "0")) in ("1", "1.0") else []):
                avail = dcs.get((pool, r["art"]), 0)
                take = int(min(r["need"] - q, avail))
                if take > 0:
                    dcs[(pool, r["art"])] = avail - take; q += take; s.append("UAE hub" if pool == "HUB" else pool)
                if q >= r["need"]:
                    break
            alloc.append(q); src.append(" + ".join(s))
        need["alloc_qty"], need["source"] = alloc, src
        need["reason"] = np.where(need["demand"] >= need["target"] - 1e-9, "Forecast demand", "Display: 1 per key size")
        need["status"] = np.where(need["alloc_qty"] >= need["need"], "Allocated",
                                  np.where(need["alloc_qty"] > 0, "Part-allocated", "No DC stock"))
        out["allocation"] = need[["country", "store", "store_name", "grade", "parent", "art", "size", "key_size", "demand", "target",
                                  "atp", "intransit", "need", "alloc_qty", "source", "reason", "status"]]
        a = out["allocation"]
        log.say("allocator", "tool", "DC pools: primary first (KSA / BAH), then the shared UAE hub · priority grade, then demand")
        log.say("allocator", "out", f"{int((a['alloc_qty'] > 0).sum()):,} lines · {int(a['alloc_qty'].sum()):,} units allocated · "
                                    f"{int((a['status'] == 'No DC stock').sum()):,} sizes with no DC stock")
        cur = _num(current_replen.copy(), ["qty"])
        out["vs_current"] = {"current_lines": int(len(cur)), "current_units": int(cur["qty"].sum()),
                             "plan_lines": int((a["alloc_qty"] > 0).sum()), "plan_units": int(a["alloc_qty"].sum())}
        log.say("allocator", "out", f"today's engine sends {out['vs_current']['current_units']:,} CORE units on {out['vs_current']['current_lines']:,} lines; "
                                    f"forecast plan {out['vs_current']['plan_units']:,} units on {out['vs_current']['plan_lines']:,} lines")

    out["log"] = log
    return out
