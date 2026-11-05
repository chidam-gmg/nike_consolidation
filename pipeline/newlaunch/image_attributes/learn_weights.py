"""Learn look-alike weights for master + image attributes on men's tees, validate out of sample, forecast deck tees.

Train: tees launched Mar-Oct 2025, each forecast only from launches fully observed (first sale + 13 wks) a month
before its own first sale. Validate: Spring 2026 tees (first sale >= cut), history = launches observed before cut.
Look-alike score = sum(weight x feature); forecast = average 13-week units of the 10 best.
"""
import re, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, "/Users/chidambaram.s/Documents/projects/nike_consolidation/pipeline/newlaunch")
import nl_engine as NE

D = sys.argv[1]
K = 10
pool = pd.read_parquet(f"{D}/pool.parquet")
pool["units"] = pd.to_numeric(pool["units"]); pool["first_sale"] = pd.to_datetime(pool["first_sale"])
tags = pd.read_parquet(f"{D}/pool_tags.parquet").drop_duplicates("parent")
A0 = pd.read_parquet(f"{D}/../nl/ATTRS.parquet"); C0 = pd.read_parquet(f"{D}/../nl/COLOUR.parquet")
A = NE.attributes(A0, C0, NE.colour_map(A0, C0))
fam = lambda n: " ".join(re.sub(r"[^A-Z0-9 ]", " ", str(n).upper()).split()[:3])
A["family"] = A["name"].map(fam)
IMG = ["fit", "pattern", "graphic_size", "text_graphic", "logo_size", "style", "base_colour"]
MASTER = ["sport", "silhouette", "plc", "colour_family", "family"]
P = (pool[["parent", "units", "first_sale"]].merge(A[["parent", "rrp"] + MASTER + ["season", "season_year"]], on="parent")
     .merge(tags, on="parent", how="inner").reset_index(drop=True))
P["rrp"] = pd.to_numeric(P["rrp"], errors="coerce")
FEATS = MASTER + ["price"] + IMG


def feat_matrix(t, H):
    """Feature x candidate matrix for target row t against history frame H."""
    rows = [(H[f].values == t[f]).astype(float) for f in MASTER]
    r = H["rrp"].values
    rows.append(np.exp(-np.abs(np.log(np.where(r > 0, r, np.nan) / t["rrp"])) / 0.25) if t["rrp"] and t["rrp"] > 0
                else np.zeros(len(H)))
    rows += [((H[f].values == t[f]) & pd.notna(t[f])).astype(float) for f in IMG]
    return np.nan_to_num(np.vstack(rows))


def build(targets, hist_fn):
    out = []
    for _, t in targets.iterrows():
        H = hist_fn(t)
        if len(H) >= K:
            out.append((t["units"], feat_matrix(t, H), H["units"].values, H["first_sale"].values.astype("int64")))
    return out


def evaluate(cases, w):
    pred, act = [], []
    for y, F, u, fs in cases:
        s = w @ F + fs * 1e-21                       # tiny recency tie-break
        pred.append(u[np.argpartition(-s, K)[:K]].mean()); act.append(y)
    p, a = np.array(pred), np.array(act)
    return {"wape": float(np.abs(p - a).sum() / a.sum()), "bias": float(p.sum() / a.sum() - 1),
            "within_2x": float(np.mean((np.maximum(p, 1) / np.maximum(a, 1) <= 2) & (np.maximum(a, 1) / np.maximum(p, 1) <= 2))),
            "rank": float(pd.Series(p).rank().corr(pd.Series(a).rank()))}


def learn(cases, active, grid=(0, 0.5, 1, 2, 3, 5), passes=3):
    w = np.array([1.0 if f in active else 0.0 for f in FEATS])
    best = evaluate(cases, w)["wape"]
    for _ in range(passes):
        for i, f in enumerate(FEATS):
            if f not in active:
                continue
            for g in grid:
                w2 = w.copy(); w2[i] = g
                e = evaluate(cases, w2)["wape"]
                if e < best - 1e-4:
                    best, w = e, w2
    return w


train_t = P[(P["first_sale"] >= "2025-03-01") & (P["first_sale"] <= "2025-10-31")]
train = build(train_t, lambda t: P[P["first_sale"] + pd.Timedelta(days=121) <= t["first_sale"]])
cut = pd.Timestamp("2025-11-12")
val_t = P[(P["season"] == "SPRG") & (P["season_year"] == "2026") & (P["first_sale"] >= cut)]
Hv = P[P["first_sale"] + pd.Timedelta(days=91) <= cut]
val = build(val_t, lambda t: Hv)
print(f"pool with tags {len(P):,} · train cases {len(train)} · validation cases {len(val)}")

current = np.array([{"sport": 2, "silhouette": 1.5, "plc": 1, "colour_family": 1, "price": 2}.get(f, 0.0) for f in FEATS])
w_master = learn(train, set(MASTER + ["price"]))
w_all = learn(train, set(FEATS))
res = {}
for name, w in (("current weights (master)", current), ("learned · master only", w_master), ("learned · master + image", w_all)):
    res[name] = {"train": evaluate(train, w), "validation": evaluate(val, w)}
    v = res[name]["validation"]; tr = res[name]["train"]
    print(f"{name:28s} validation: error {v['wape']:.0%} bias {v['bias']:+.0%} within2x {v['within_2x']:.0%} rank {v['rank']:.2f}"
          f"   | train error {tr['wape']:.0%}")
imp = pd.Series(w_all, index=FEATS)
print("\nlearned weights (master + image):", {k: v for k, v in imp.items() if v > 0})
print("importance %:", (imp / imp.sum() * 100).round(0).sort_values(ascending=False).to_dict())
json.dump({"feats": FEATS, "w_all": w_all.tolist(), "w_master": w_master.tolist(), "current": current.tolist(), "res": res},
          open(f"{D}/weights.json", "w"))

# forecast the deck's Spring 2027 tees from all tagged history
deck = pd.read_parquet(f"{D}/deck_tags.parquet").merge(A[["parent", "rrp", "name"] + MASTER], on="parent")
deck["rrp"] = pd.to_numeric(deck["rrp"], errors="coerce")
info = pd.DataFrame(json.load(open(f"{D}/deck_tees.json")))
out, ana = [], []
for _, t in deck.iterrows():
    F = feat_matrix(t, P)
    for name, w in (("current", current), ("learned_all", w_all)):
        idx = np.argsort(-(w @ F + P["first_sale"].values.astype("int64") * 1e-21))[:K]
        out.append({"parent": t["parent"], "weights": name, "units_13w": float(P["units"].values[idx].mean())})
        if name == "learned_all":
            ana += [{"parent": t["parent"], "rank": r, "analogue": P["parent"].iloc[j], "units": float(P["units"].iloc[j])}
                    for r, j in enumerate(idx)]
fc = pd.DataFrame(out).pivot(index="parent", columns="weights", values="units_13w").reset_index().merge(info, on="parent")
fc.to_parquet(f"{D}/deck_forecast.parquet"); pd.DataFrame(ana).to_parquet(f"{D}/deck_analogues.parquet")
print(f"\ndeck tees forecast: {len(fc)} · total now {fc['units_13w_now'].sum():,.0f} · current-weights-on-tees {fc['current'].sum():,.0f}"
      f" · learned master+image {fc['learned_all'].sum():,.0f}")
print(fc[fc.parent.isin(["NKIR2969-010", "NKIR2969-030", "NKIR2969-610"])][["parent", "name", "units_13w_now", "current", "learned_all"]].round(0).to_string(index=False))
