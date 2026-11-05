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
FEATS = MASTER + ["price"] + IMG + ["look"]
E = np.load(f"{D}/pool_emb.npy"); EID = pd.read_parquet(f"{D}/pool_emb_ids.parquet")["parent"].tolist()
EPOS = {p: i for i, p in enumerate(EID)}
P = P[P["parent"].isin(EPOS)].reset_index(drop=True)


def feat_matrix(t, H):
    """Feature x candidate matrix for target row t against history frame H."""
    rows = [(H[f].values == t[f]).astype(float) for f in MASTER]
    r = H["rrp"].values
    rows.append(np.exp(-np.abs(np.log(np.where(r > 0, r, np.nan) / t["rrp"])) / 0.25) if t["rrp"] and t["rrp"] > 0
                else np.zeros(len(H)))
    rows += [((H[f].values == t[f]) & pd.notna(t[f])).astype(float) for f in IMG]
    rows.append(E[[EPOS[p] for p in H["parent"]]] @ E[EPOS[t["parent"]]])      # visual similarity (CLIP cosine)
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


def learn(cases, active, grid=(0, 0.5, 1, 2, 3, 5), passes=3, look_grid=(0, 5, 10, 20, 40)):
    w = np.array([1.0 if f in active else 0.0 for f in FEATS])
    best = evaluate(cases, w)["wape"]
    for _ in range(passes):
        for i, f in enumerate(FEATS):
            if f not in active:
                continue
            for g in (look_grid if f == "look" else grid):
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


W = json.load(open(f"{D}/weights2.json")); wt = np.array(W["w_tags"])
il = FEATS.index("look")
def coh(w):
    out = []
    for _, t in val_t.iterrows():
        F = feat_matrix(t, Hv); idx = np.argsort(-(w @ F))[:K]
        out.append(float(np.mean(E[[EPOS[p] for p in Hv["parent"].iloc[idx]]] @ E[EPOS[t["parent"]]])))
    return float(np.mean(out))
for lw in (0, 5, 10, 20, 40, 80):
    w = wt.copy(); w[il] = lw; v = evaluate(val, w)
    print(f"visual weight {lw:3d}: validation error {v['wape']:.0%} bias {v['bias']:+.0%} within2x {v['within_2x']:.0%} rank {v['rank']:.2f} · look-alike visual similarity {coh(w):.3f}")
