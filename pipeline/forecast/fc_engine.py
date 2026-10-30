"""CORE forecast + allocation engine (dev, 2026-10-30).

1. Weekly style x country history (zero-filled) -> demand pattern per series; events calendar + month / week-of-year
   seasonality per country x division (models forecast the de-seasonalised level, the pattern is put back) (ADI / CV2: smooth, erratic,
   intermittent, lumpy).
2. Model contest — 5 simple models x (flat, seasonal, seasonal on stock-out-corrected demand), 3 cut-offs, 4-week horizon,
   scored on well-stocked weeks only; error = volume-weighted (WAPE) and bias,
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



# ---------------- events calendar + seasonality ----------------
# GCC retail calendar. Islamic dates follow the moon sighting (±1 day) and DSF dates are announced yearly —
# business should confirm these each year (the table fc_events is what the forecast used).
EVENTS = [
    ("Ramadan", "2024-03-11", "2024-04-09"), ("Eid al-Fitr", "2024-04-10", "2024-04-13"),
    ("Eid al-Adha", "2024-06-15", "2024-06-19"), ("Back to school", "2024-08-19", "2024-09-01"),
    ("11.11", "2024-11-10", "2024-11-12"), ("White Friday", "2024-11-25", "2024-12-02"),
    ("UAE National Day", "2024-12-01", "2024-12-03"), ("Dubai Shopping Festival", "2024-12-06", "2025-01-12"),
    ("Ramadan", "2025-03-01", "2025-03-29"), ("Eid al-Fitr", "2025-03-30", "2025-04-02"),
    ("Eid al-Adha", "2025-06-05", "2025-06-09"), ("Back to school", "2025-08-18", "2025-08-31"),
    ("11.11", "2025-11-10", "2025-11-12"), ("White Friday", "2025-11-24", "2025-12-01"),
    ("UAE National Day", "2025-12-01", "2025-12-03"), ("Dubai Shopping Festival", "2025-12-05", "2026-01-11"),
    ("Ramadan", "2026-02-18", "2026-03-19"), ("Eid al-Fitr", "2026-03-20", "2026-03-23"),
    ("Eid al-Adha", "2026-05-26", "2026-05-30"), ("Back to school", "2026-08-17", "2026-08-30"),
    ("11.11", "2026-11-10", "2026-11-12"), ("White Friday", "2026-11-23", "2026-11-30"),
    ("UAE National Day", "2026-12-01", "2026-12-03"), ("Dubai Shopping Festival", "2026-12-04", "2027-01-10"),
    ("Ramadan", "2027-02-08", "2027-03-09"), ("Eid al-Fitr", "2027-03-10", "2027-03-13"),
]


def event_overlap(weeks):
    """{event name: array of the share of each week (Mon–Sun) covered by the event}."""
    out = {}
    for name, a, b in EVENTS:
        a, b = pd.Timestamp(a), pd.Timestamp(b)
        ov = np.array([max(0, (min(b, w + pd.Timedelta(days=6)) - max(a, w)).days + 1) / 7 for w in weeks])
        out[name] = out.get(name, 0) + ov
    return {k: np.clip(v, 0, 1) for k, v in out.items()}


def seasonal_factors(pool, weeks, upto, ev):
    """Per group (country x division): month-of-year x week-of-year seasonality and event uplifts, learned only from
    weeks < upto. Returns (factor matrix groups x len(weeks), season matrix, uplift dict per group)."""
    G, W = pool.shape
    month = np.array([w.month for w in weeks]); woy = np.array([w.isocalendar()[1] for w in weeks])
    any_ev = np.zeros(W, bool)
    for v in ev.values():
        any_ev |= v > 0
    F, S, U = np.ones((G, W)), np.ones((G, W)), []
    k = 13
    for g in range(G):
        p = pool[g, :upto].astype(float)
        if p.sum() <= 0:
            U.append({}); continue
        ma = np.convolve(np.pad(p, (k // 2, k // 2), mode="edge"), np.ones(k) / k, mode="valid")
        r = np.where(ma > 0, p / np.maximum(ma, 1e-9), 1.0)
        clean = ~any_ev[:upto]
        m_idx = {m: np.mean(r[(month[:upto] == m) & clean]) for m in range(1, 13) if ((month[:upto] == m) & clean).any()}
        if m_idx:
            mm = np.mean(list(m_idx.values())); m_idx = {m: v / mm for m, v in m_idx.items()}
        w_raw = {w: np.mean(r[(woy[:upto] == w) & clean]) for w in range(1, 54) if ((woy[:upto] == w) & clean).any()}
        def mf(m): return m_idx.get(m, 1.0)
        def wf(w):
            vals = [(w_raw.get(((w - 1 + d) % 53) + 1), wt) for d, wt in ((-1, .25), (0, .5), (1, .25))]
            vals = [(v, wt) for v, wt in vals if v is not None]
            return sum(v * wt for v, wt in vals) / sum(wt for _, wt in vals) if vals else None
        s = np.array([0.5 * mf(month[i]) + 0.5 * (wf(woy[i]) if wf(woy[i]) is not None else mf(month[i])) for i in range(W)])
        s = np.clip(s, 0.6, 1.8); s = s / s[:upto].mean()
        ups = {}
        for name, ov in ev.items():
            hit = ov[:upto] >= 3 / 7
            if hit.any():
                u = float(np.median(r[hit] / np.maximum(s[:upto][hit], 1e-9)))
                ups[name] = float(np.clip(1 + 0.8 * (u - 1), 0.7, 2.5))
        S[g] = s
        U.append(ups)
    # one past occurrence per event is noisy at group level: blend each group's lift 50/50 with the all-group median
    med = {n: float(np.median([u[n] for u in U if n in u])) for n in ev if any(n in u for u in U)}
    for g in range(G):
        U[g] = {n: round(0.5 * U[g].get(n, med[n]) + 0.5 * med[n], 3) for n in med}
        e = np.ones(W)
        for name, ov in ev.items():
            e *= 1 + (U[g].get(name, 1.0) - 1) * ov
        F[g] = S[g] * e
    return F, S, U


def demand_from_sales(y, stores, weeks_known):
    """Gross sales up for stock-outs. stores = stores holding the style each week (NaN = no stock history).
    availability = stores in stock / most stores holding it in the last 13 weeks. Partial (< 70%): sales / sqrt(availability)
    (square root: the stores that keep stock also catch some of the lost sales; backtest beat a full gross-up). Floored at 0.3.
    Fully out while ranged: no usable signal -> filled from the recent demand level."""
    n, T = y.shape
    roll = np.full((n, T), np.nan)
    for t in range(T):
        win = stores[:, max(0, t - 12):t + 1]
        roll[:, t] = np.where(np.isnan(win).all(axis=1), np.nan, np.nanmax(np.where(np.isnan(win), -1, win), axis=1))
    with np.errstate(invalid="ignore", divide="ignore"):
        avail = np.where(np.isnan(stores) | (roll <= 0), np.nan, stores / np.maximum(roll, 1e-9))
    d = y.astype(float).copy()
    part = ~np.isnan(avail) & (avail > 0) & (avail < 0.7)
    d[part] = y[part] / np.sqrt(np.clip(avail[part], 0.3, 1))
    out = ~np.isnan(avail) & (avail == 0)
    for t in range(T):                                   # fully out of stock: carry the recent demand level
        k = out[:, t]
        if k.any():
            prev = d[k, max(0, t - 8):t]
            good = ~out[k, max(0, t - 8):t]
            d[k, t] = np.where(good.sum(axis=1) > 0, (prev * good).sum(axis=1) / np.maximum(good.sum(axis=1), 1), y[k, t])
    return d, avail

# ---------------- pipeline ----------------
def run(weekly, store_size, stock, stores, key_sizes, dc, dc_map, current_replen, avail=None, log: RunLog | None = None):
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
        if avail is not None and len(avail):
            av = _num(avail.copy(), ["stores_in_stock"]); av["wk"] = pd.to_datetime(av["wk"])
            st_mat = (av.pivot_table(index=["country", "parent"], columns="wk", values="stores_in_stock", aggfunc="sum")
                      .reindex(index=piv.index, columns=weeks))
            known = np.array([w >= av["wk"].min() for w in weeks])
            st_arr = st_mat.to_numpy(float, copy=True)
            st_arr[:, known] = np.nan_to_num(st_arr[:, known], nan=0.0)      # in the stock-history window: missing = no store had stock
        else:
            st_arr, known = np.full(y.shape, np.nan), np.zeros(len(weeks), bool)
        yd, av_ratio = demand_from_sales(y, st_arr, known)
        out["availability"] = av_ratio
        log.say("scout", "tool", "read fct_sap_sales_detail_v · CORE / YEAR Nike articles · retail + e-commerce · Nike stores")
        log.say("scout", "tool", "read atp_detail Wednesday snapshots (from 20 Jan 2025) · stores holding each style each week")
        log.say("scout", "out", f"{len(piv):,} style × country series · {len(weeks)} weeks (from {weeks[0]:%d %b %Y}) up to the week of "
                                 f"{last_week:%d %b %Y} · {int(y.sum()):,} units sold")
        part = np.nansum((av_ratio > 0) & (av_ratio < 0.7)); full = np.nansum(av_ratio == 0)
        log.say("scout", "hold", f"stock-outs since Jan 2025: {int(part):,} series-weeks partly out, {int(full):,} fully out · "
                                 f"demand grossed up by {int(yd.sum() - y.sum()):,} units ({(yd.sum() / max(y.sum(), 1) - 1) * 100:.1f}%)")

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

    # 3 · Calendar — events + month / week-of-year seasonality per country x division (pooled: style-level is too noisy)
    with log.stage("calendar", "calendar"):
        T = y.shape[1]
        all_weeks = list(weeks) + [last_week + pd.Timedelta(days=7 * h) for h in range(1, HORIZON_WEEKS + 1)]
        ev = event_overlap(all_weeks)
        grp = pd.Series(list(zip(piv.index.get_level_values(0), meta.reindex(piv.index)["division"].fillna("OTHER"))))
        groups = sorted(set(grp))
        gidx = grp.map({g: i for i, g in enumerate(groups)}).to_numpy()
        pool = np.zeros((len(groups), len(all_weeks)))
        for gi in range(len(groups)):
            pool[gi, :T] = yd[gidx == gi].sum(axis=0)
        F_full, S_full, U_full = seasonal_factors(pool, all_weeks, T, ev)
        cal = []
        for gi, (country, division) in enumerate(groups):
            for wi, w in enumerate(all_weeks):
                names = [n for n, ov in ev.items() if ov[wi] > 0]
                cal.append({"country": country, "division": division, "week_start": w.date(), "future": wi >= T,
                            "season_factor": round(float(S_full[gi, wi]), 3),
                            "event_factor": round(float(F_full[gi, wi] / max(S_full[gi, wi], 1e-9)), 3),
                            "factor": round(float(F_full[gi, wi]), 3), "events": ", ".join(names)})
        out["seasonality"] = pd.DataFrame(cal)
        out["events"] = pd.DataFrame([{"event": n, "start": a, "end": b} for n, a, b in EVENTS])
        out["event_uplift"] = pd.DataFrame([{"country": c, "division": d, "event": k, "uplift": round(v, 2)}
                                            for (c, d), u in zip(groups, U_full) for k, v in u.items()])
        log.say("calendar", "tool", f"events calendar: {len(EVENTS)} events (Ramadan, Eids, back to school, 11.11, White Friday, "
                                    f"National Day, Dubai Shopping Festival) · seasonality per country × division ({len(groups)} groups)")
        upl = out["event_uplift"]
        if not upl.empty:
            top = upl.groupby("event")["uplift"].median().sort_values(ascending=False)
            log.say("calendar", "out", "event lift (median): " + " · ".join(f"{k} ×{v:.2f}" for k, v in top.items()))
        fut = [n for n, ov in ev.items() if ov[T:].sum() > 0]
        log.say("calendar", "out", "in the next 8 weeks: " + (", ".join(fut) if fut else "no events"))

    # 4 · Tournament — every model scored flat and with seasonality + events, per demand pattern
    with log.stage("tournament", "tournament"):
        rows = []
        for back in BACKTEST_CUTOFFS:
            cut = T - back
            Fc, _, _ = seasonal_factors(pool, all_weeks, cut, ev)            # factors learned only before the cut-off
            Fs = Fc[gidx]
            # score only weeks where the style was well stocked (else actual sales understate demand)
            a_t = av_ratio[:, cut:cut + BACKTEST_H]
            mask = np.where(np.isnan(a_t), 1.0, (a_t >= 0.8).astype(float))
            act = (y[:, cut:cut + BACKTEST_H] * mask).sum(axis=1)
            fac = Fs[:, cut:cut + BACKTEST_H]
            for m in METHODS:
                variants = {"flat": level(y[:, :cut], m)[:, None] * np.ones_like(fac),
                            "seasonal": level(y[:, :cut] / np.maximum(Fs[:, :cut], 1e-9), m)[:, None] * fac,
                            "seasonal + stock-outs": level(yd[:, :cut] / np.maximum(Fs[:, :cut], 1e-9), m)[:, None] * fac}
                for var, fw in variants.items():
                    f = (fw * mask).sum(axis=1)
                    for c in np.unique(cls):
                        k = cls == c
                        a_sum = act[k].sum()
                        rows.append({"cutoff_week": str(weeks[cut - 1].date()), "demand_class": c, "method": m, "variant": var,
                                     "series": int(k.sum()), "actual_units": float(a_sum),
                                     "wape": float(np.abs(f[k] - act[k]).sum() / a_sum) if a_sum else np.nan,
                                     "bias": float((f[k] - act[k]).sum() / a_sum) if a_sum else np.nan})
        bt = pd.DataFrame(rows)
        board = bt.groupby(["demand_class", "method", "variant"]).agg(wape=("wape", "mean"), bias=("bias", "mean"),
                                                                      actual_units=("actual_units", "sum")).reset_index()
        champ = (board.dropna(subset=["wape"]).sort_values(["demand_class", "wape"])
                 .groupby("demand_class").head(1).set_index("demand_class"))
        base = board[(board["method"] == "MA12") & (board["variant"] == "flat")].set_index("demand_class")
        flat_best = board[board["variant"] == "flat"].sort_values("wape").groupby("demand_class").head(1).set_index("demand_class")
        seas_best = board[board["variant"] == "seasonal"].sort_values("wape").groupby("demand_class").head(1).set_index("demand_class")
        out["backtest"] = bt
        out["leaderboard"] = board.assign(champion=board.apply(
            lambda r: r["demand_class"] in champ.index and champ.loc[r["demand_class"], "method"] == r["method"]
            and champ.loc[r["demand_class"], "variant"] == r["variant"], axis=1))
        log.say("tournament", "tool", f"{len(METHODS)} models × (flat · seasonal · seasonal + stock-outs) × {len(BACKTEST_CUTOFFS)} cut-offs × "
                                      f"{BACKTEST_H}-week horizon · scored on well-stocked weeks")
        for c, r in champ.iterrows():
            if c == "inactive":
                continue
            b = base.loc[c, "wape"] if c in base.index else np.nan
            fb = flat_best.loc[c, "wape"] if c in flat_best.index else np.nan
            log.say("tournament", "out", f"{c}: {r['method']} ({r['variant']}) wins · error {r['wape']*100:.0f}% "
                                         f"(best flat {fb*100:.0f}%, 12-week average {b*100:.0f}%) · bias {r['bias']*100:+.0f}%")
        wtd = lambda d: float((d["wape"] * d["actual_units"]).sum() / d["actual_units"].sum())
        act_cls = [c for c in champ.index if c != "inactive"]
        out["error_champion"], out["error_baseline"] = wtd(champ.loc[act_cls]), wtd(base.loc[[c for c in act_cls if c in base.index]])
        out["error_flat_best"] = wtd(flat_best.loc[[c for c in act_cls if c in flat_best.index]])
        out["error_seasonal_best"] = wtd(seas_best.loc[[c for c in act_cls if c in seas_best.index]])
        log.say("tournament", "out", f"overall error {out['error_champion']*100:.0f}% · best seasonal on raw sales "
                                     f"{out['error_seasonal_best']*100:.0f}% · best flat {out['error_flat_best']*100:.0f}% · "
                                     f"plain 12-week average {out['error_baseline']*100:.0f}%")

    # 5 · Forecaster — level from the winner, then seasonality + events put back week by week
    with log.stage("forecast", "forecaster"):
        Fser = F_full[gidx]
        src = {"flat": y, "seasonal": y / np.maximum(Fser[:, :T], 1e-9),
               "seasonal + stock-outs": yd / np.maximum(Fser[:, :T], 1e-9)}
        lv = np.zeros(len(piv)); meth = np.array(["MA12"] * len(piv), dtype=object); var = np.array(["flat"] * len(piv), dtype=object)
        for c in np.unique(cls):
            m, v = (champ.loc[c, "method"], champ.loc[c, "variant"]) if c in champ.index else ("MA12", "flat")
            k = cls == c
            lv[k] = level(src[v][k], m); meth[k] = m; var[k] = v
        fut = np.where((var == "flat")[:, None], 1.0, Fser[:, T:]) * lv[:, None]
        p80 = fut + 0.84 * np.sqrt(np.maximum(fut, 0))
        fc_rows = []
        for i, (country, parent) in enumerate(piv.index):
            if lv[i] <= 0:
                continue
            for h in range(HORIZON_WEEKS):
                fc_rows.append((country, parent, all_weeks[T + h].date(), round(float(fut[i, h]), 3), round(float(p80[i, h]), 3),
                                meth[i], var[i], cls[i]))
        fc = pd.DataFrame(fc_rows, columns=["country", "parent", "week_start", "units_p50", "units_p80", "method", "variant", "demand_class"])
        fc = fc.merge(meta.reset_index(), on=["country", "parent"], how="left")
        out["forecast"], out["history"] = fc, weekly
        wk_tot = fc.groupby("week_start")["units_p50"].sum()
        log.say("forecaster", "out", f"{fc[['country','parent']].drop_duplicates().shape[0]:,} series · {HORIZON_WEEKS} weeks · "
                                     f"weekly total {wk_tot.min():,.0f}–{wk_tot.max():,.0f} units (peak week of {pd.Timestamp(wk_tot.idxmax()):%d %b})")
        cover_w = max(1, round(COVER_DAYS / 7))
        fcl = pd.DataFrame({"cover_units": fut[:, :cover_w].sum(axis=1)}, index=piv.index).reset_index()

    # 6 · Store splitter — country forecast -> store by its share of the style
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
        sp["demand_cover"] = sp["cover_units"] * sp["store_share"]          # next 2 weeks incl. seasonality + events
        log.say("splitter", "tool", f"store share of each style in its country (26 weeks) · {len(active)} active stores")
        log.say("splitter", "out", f"{len(sp):,} store × style lines · {sp['demand_cover'].sum():,.0f} units of {COVER_DAYS}-day demand")

    # 7 · Size curator — country size curve
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

    # 8 · Stock checker — net of ATP + in-transit
    with log.stage("stock_check", "stock"):
        lines = lines.merge(carry[["store", "art", "atp", "intransit"]], on=["store", "art"], how="left")
        lines[["atp", "intransit", "key_size"]] = lines[["atp", "intransit", "key_size"]].fillna(0)
        lines["target"] = np.maximum(lines["demand"], np.where(lines["key_size"] == 1, 1.0, 0.0))
        lines["need"] = np.ceil(np.maximum(0, lines["target"] - lines["atp"] - lines["intransit"]) - 1e-9).astype(int)
        log.say("stock", "tool", "read effective_stock (ATP today) + store_intransit")
        log.say("stock", "out", f"{int((lines['need'] > 0).sum()):,} sizes need stock · {int(lines['need'].sum()):,} units short of {COVER_DAYS}-day cover")

    # 9 · Allocator — DC rationing (primary pool, then shared hub globally)
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
