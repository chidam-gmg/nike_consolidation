# Databricks notebook source
# MAGIC %md # New-article (launch) forecast — target season

# COMMAND ----------
from __future__ import annotations

# COMMAND ----------
"""SQL for the new-article (launch) forecast. Shared by the Databricks notebook and local tests."""
FQ = "sports_analytics_test.nike_consolidation"
PM = "prod_mena.gold.mena_sports_product_master_view"
SM = "prod_mena.gold.mena_sports_store_master_view"
SALES = "sap_gmg_datalake.silver.fct_sap_sales_detail_v"
TARGET_SEASON, TARGET_YEAR = "SPRG", "2027"

_ART = f"""art AS (SELECT MATERIAL art, MAX(RT_CONFMAT) parent FROM {PM} WHERE MATL_CAT = '02' AND MATERIAL LIKE 'NK%' GROUP BY MATERIAL),
nike AS (SELECT PLANT store, MAX(COUNTRY) country FROM {SM} WHERE PLANTCAT = 'A' AND `/BIC/ZBU` = 'NIKE' AND COUNTRY <> '' GROUP BY PLANT)"""

# Launch history: units per style x country x week since the style first sold anywhere (weeks 0-12), retail + ecom.
# Styles whose first sale is from 1 Jun 2024 (data starts Mar 2024, so earlier "first sales" may be old styles)
# up to 13 weeks before the last full week (so all 13 weeks are observed).
LAUNCH = f"""WITH {_ART},
s AS (SELECT a.parent, n.country, s.site_code store, s.txn_date d, GREATEST(s.txn_qty, 0) q FROM {SALES} s
      JOIN art a ON a.art = s.variant_article_code JOIN nike n ON n.store = s.site_code
      WHERE s.distr_channel IN ('10', '20') AND s.txn_date >= DATE'2024-03-01'),
f AS (SELECT parent, MIN(d) first_sale FROM s GROUP BY parent
      HAVING MIN(d) >= DATE'2024-06-01' AND MIN(d) <= date_sub(date_trunc('week', current_date()), 7 * 14))
SELECT s.parent, s.country, f.first_sale, CAST(floor(datediff(s.d, f.first_sale) / 7) AS INT) wk,
       SUM(s.q) units, COUNT(DISTINCT s.store) stores
FROM s JOIN f USING (parent) WHERE s.d < date_add(f.first_sale, 91)
GROUP BY s.parent, s.country, f.first_sale, CAST(floor(datediff(s.d, f.first_sale) / 7) AS INT)"""

# Attributes for every Nike style (history and the target season)
ATTRS = f"""SELECT RT_CONFMAT parent, MAX(split(RT_CONFMAT, '-')[0]) model, MAX(get(split(RT_CONFMAT, '-'), 1)) colourway,
       MAX(TRIM(`/BIC/ZRWGH2_T`)) division, MAX(TRIM(CM_CDT3_TXTMD)) ah2, MAX(TRIM(`/BIC/ZCHWGH5`)) gender,
       MAX(TRIM(`/BIC/ZRWGH3_T`)) sport, MAX(TRIM(CM_CDT4_TXTMD)) silhouette, MAX(Global_price) rrp,
       MAX(TRIM(`/BIC/ZZPLC_TXTMD`)) plc, MAX(upper(trim(`/BIC/ZCHSEASON`))) season, MAX(`/BIC/ZCHSEASYR`) season_year,
       MAX(regexp_replace(TXTMD, ',[^,]*$', '')) name, MIN(CREATEDON) created
FROM {PM} WHERE MATL_CAT = '02' AND MATERIAL LIKE 'NK%' GROUP BY RT_CONFMAT"""

COLOUR = f"""SELECT parent, colour, url FROM {FQ}.dim_product_image"""

# Product attributes read from images (Databricks vision endpoint); styles without a row use the attribute fallback
IMAGE_TAGS_SQL = f"""SELECT parent, source, fit, pattern, graphic_size, text_graphic, logo_size, style, base_colour
FROM {FQ}.nl_image_tags"""

# COMMAND ----------
"""New-article (launch) forecast — styles with no sales history.

A new style borrows from the closest styles that have launched before, strongest evidence first:
  A · same model     earlier colourways of the same Nike model (style code before the dash)
  B · look-alikes    the k past launches most like it in the same division x gender: AH2 category, sport,
                     silhouette, life cycle, colour family (decoded from the colourway code) and retail price
  C · cluster        the typical launch in its division x AH2 x gender (fallback)
The forecast is the first 13 weeks after launch, per country, with a weekly launch curve and a high case (P80)
from the back-test spread. Estimates are the AVERAGE of the analogues (the median forecasts ~30% low because a few
launches carry much of the volume); A and B are blended 50/50 — back-test on Spring 2026 (history to Dec 2025):
style-level error 83%, bias +10%, 42% of styles within 2x of actual.
"""

import numpy as np
import pandas as pd

WEEKS = 13
K = 10
W_MODEL = 0.5
IMAGE_TAGS = ["fit", "pattern", "graphic_size", "text_graphic", "logo_size", "style", "base_colour"]
MASTER_W = {"ah2": 3.0, "sport": 2.0, "silhouette": 1.5, "plc": 1.0, "colour_family": 1.0, "price": 2.0}
# Image + attribute weights, learned per scope where they have been validated (tees launched Mar-Oct 2025, checked on
# Spring 2026 tees). A style uses them only if it is in scope AND has an image read; otherwise MASTER_W (fallback).
IMAGE_WEIGHTS = {
    ("APPAREL", "MENS", "TEES AND T-SHIRTS"): {
        "weights": {"sport": 5.0, "silhouette": 5.0, "plc": 0.5, "family": 2.0, "price": 3.0, "colour_family": 0.0,
                    "fit": 0.5, "pattern": 1.0, "graphic_size": 0.0, "text_graphic": 1.0, "logo_size": 5.0,
                    "style": 0.0, "base_colour": 1.0},
        "validation": {"styles": 301, "error": 0.79, "within_2x": 0.45, "rank": 0.48,
                       "error_fallback": 0.86, "within_2x_fallback": 0.38, "rank_fallback": 0.42},
    },
}


def family(name) -> str:
    """Product family from the name's first three words, e.g. 'AIR JORDAN 4', 'M NK DF'."""
    return " ".join("".join(ch if ch.isalnum() or ch == " " else " " for ch in str(name).upper()).split()[:3])


def colour_map(attrs: pd.DataFrame, colour: pd.DataFrame) -> dict:
    """First character of the colourway code -> most common colour family on styles that have one (learned)."""
    x = attrs.merge(colour, on="parent").dropna(subset=["colourway", "colour"])
    x["c0"] = x["colourway"].str[0]
    return x.groupby("c0")["colour"].agg(lambda s: s.value_counts().index[0]).to_dict()


def attributes(attrs: pd.DataFrame, colour: pd.DataFrame, cmap: dict, tags: pd.DataFrame | None = None) -> pd.DataFrame:
    a = attrs.merge(colour[["parent", "colour", "url"]], on="parent", how="left")
    a["family"] = a["name"].map(family)
    if tags is not None and len(tags):
        a = a.merge(tags[["parent"] + IMAGE_TAGS + ["source"]].rename(columns={"source": "image_source"}), on="parent", how="left")
    else:
        for c in IMAGE_TAGS + ["image_source"]:
            a[c] = None
    a["rrp"] = pd.to_numeric(a["rrp"], errors="coerce")
    a["colour_family"] = a["colour"].fillna(a["colourway"].fillna("").str[:1].map(cmap))
    for c in ("division", "ah2", "gender", "sport", "silhouette", "plc", "colour_family"):
        a[c] = a[c].fillna("NA")
    return a


def history(launch: pd.DataFrame, attr: pd.DataFrame) -> tuple:
    """Per launched style: 13-week units (region total and per country), store breadth, weekly curve."""
    L = launch.copy()
    for c in ("units", "stores", "wk"):
        L[c] = pd.to_numeric(L[c], errors="coerce").fillna(0)
    L["first_sale"] = pd.to_datetime(L["first_sale"])
    L = L[L["wk"] < WEEKS]
    by_c = L.groupby(["parent", "country"]).agg(units=("units", "sum"), stores=("stores", "max")).reset_index()
    tot = by_c.groupby("parent").agg(units=("units", "sum"), stores=("stores", "sum")).reset_index()
    tot = tot.merge(L.groupby("parent")["first_sale"].first().reset_index(), on="parent").merge(attr, on="parent", how="left")
    share = by_c.assign(share=by_c["units"] / by_c.groupby("parent")["units"].transform("sum").replace(0, np.nan))
    curve = L.groupby(["parent", "wk"])["units"].sum().unstack(fill_value=0).reindex(columns=range(WEEKS), fill_value=0)
    return tot, share[["parent", "country", "share"]].fillna(0), curve


def _price(t, H):
    if not t.rrp or not t.rrp > 0:
        return np.zeros(len(H))
    r = H["rrp"].values
    return np.nan_to_num(np.exp(-np.abs(np.log(np.where(r > 0, r, np.nan) / t.rrp)) / 0.25))


def _parts(t, H, w: dict) -> tuple:
    """Weighted similarity of target t to every row of H, plus each attribute's match (1/0, price 0-1)."""
    parts = {}
    for f in ("ah2", "sport", "silhouette", "plc", "colour_family", "family") + tuple(IMAGE_TAGS):
        if w.get(f, 0) and f in H:
            v = getattr(t, f, None)
            parts[f] = ((H[f].values == v) & pd.notna(v)).astype(float)
    if w.get("price", 0):
        parts["price"] = _price(t, H)
    total = sum(w[f] * m for f, m in parts.items()) if parts else np.zeros(len(H))
    return np.asarray(total, float), parts


def _score(t, H):
    """Similarity of target row t to history H with the fallback (master attribute) weights."""
    return _parts(t, H, MASTER_W)[0]


def forecast(targets: pd.DataFrame, hist: pd.DataFrame, w_model: float = W_MODEL, k: int = K) -> pd.DataFrame:
    """One row per target style: tier, method, A / B / C estimates (13-week region units), blend, analogues with the
    score and the attributes each one matched."""
    H = hist.reset_index(drop=True)
    by_dg = {key: g for key, g in H.groupby(["division", "gender"])}
    by_model = {m: g for m, g in H.groupby("model")}
    clus = H.groupby(["division", "ah2", "gender"])["units"].mean()
    rows = []
    for t in targets.itertuples():
        sib = by_model.get(t.model)
        sib = sib[sib["parent"] != t.parent] if sib is not None else None
        a = float(sib["units"].mean()) if sib is not None and len(sib) else np.nan
        g = by_dg.get((t.division, t.gender))
        b, ana, detail, method = np.nan, [], [], "attributes"
        if g is not None and len(g):
            img = IMAGE_WEIGHTS.get((t.division, t.gender, t.ah2))
            if img and pd.notna(getattr(t, "fit", None)):
                cand = g[(g["ah2"] == t.ah2) & g["fit"].notna()]
                if len(cand) >= 5 * k:
                    g, w, method = cand, img["weights"], "image + attributes"
                else:
                    w = MASTER_W
            else:
                w = MASTER_W
            sc, parts = _parts(t, g, w)
            order = np.lexsort((-g["first_sale"].values.astype("int64"), -sc))[:k]
            top = g.iloc[order]
            b, ana = float(top["units"].mean()), top["parent"].tolist()
            for j in order:
                detail.append({"analogue": g["parent"].iloc[j], "score": float(sc[j]),
                               "matched": ", ".join(f for f, m in parts.items() if (m[j] >= 0.5))})
        c = float(clus.get((t.division, t.ah2, t.gender), np.nan))
        if not np.isnan(a) and not np.isnan(b):
            est, tier = w_model * a + (1 - w_model) * b, "A · same model"
        elif not np.isnan(a):
            est, tier = a, "A · same model"
        elif not np.isnan(b):
            est, tier = b, "B · look-alikes"
        else:
            est, tier = c, "C · cluster"
        rows.append({"parent": t.parent, "tier": tier, "method": method, "est_a": a, "est_b": b, "est_c": c,
                     "units_13w": max(0.0, float(est)) if not np.isnan(est) else 0.0,
                     "siblings": 0 if sib is None else len(sib), "analogues": ana, "detail": detail})
    return pd.DataFrame(rows)


def score(pred: pd.DataFrame, actual: pd.DataFrame, col: str = "units_13w") -> dict:
    m = pred.merge(actual[["parent", "units"]], on="parent")
    p, y = m[col].fillna(0).values, m["units"].values
    return {"styles": len(m), "wape": float(np.abs(p - y).sum() / max(y.sum(), 1)), "bias": float(p.sum() / max(y.sum(), 1) - 1),
            "within_2x": float(np.mean((np.maximum(p, 1) / np.maximum(y, 1) <= 2) & (np.maximum(y, 1) / np.maximum(p, 1) <= 2)))}


def country_split(fc: pd.DataFrame, share: pd.DataFrame, sibl: dict, analog: dict) -> pd.DataFrame:
    """Country shares: the average share of the style's siblings and look-alikes."""
    out = []
    sh = share.pivot_table(index="parent", columns="country", values="share", fill_value=0)
    for r in fc.itertuples():
        src = [p for p in (sibl.get(r.parent, []) + list(r.analogues)) if p in sh.index]
        w = sh.loc[src].mean() if src else sh.mean()
        w = w / w.sum() if w.sum() > 0 else w
        out += [{"parent": r.parent, "country": c, "units_13w": r.units_13w * float(v)} for c, v in w.items() if v > 0]
    return pd.DataFrame(out)


def launch_curve(hist: pd.DataFrame, curve: pd.DataFrame) -> pd.DataFrame:
    """Share of 13-week units by week since launch, per division x life cycle, weighted by volume: total units by
    week across the group's launches. (A per-style median or average is swamped by styles that sold one or two
    units, all "in week 1".) Groups with fewer than 50 launches use the division's shape."""
    c = curve.join(hist.set_index("parent")[["division", "plc"]])
    g = c.groupby(["division", "plc"])
    m = g[list(range(WEEKS))].sum()
    small = g.size() < 50
    div = c.groupby("division")[list(range(WEEKS))].sum()
    for key in m.index[small.reindex(m.index).values]:
        m.loc[key] = div.loc[key[0]]
    return m.div(m.sum(axis=1), axis=0)


def p80_multiplier(pred: pd.DataFrame, actual: pd.DataFrame) -> dict:
    """High case per tier: the 80th percentile of actual / forecast in the back-test."""
    m = pred.merge(actual[["parent", "units"]], on="parent")
    m["r"] = (m["units"] + 1) / (m["units_13w"] + 1)
    return m.groupby("tier")["r"].quantile(0.8).clip(lower=1.0).to_dict()


def run(launch: pd.DataFrame, attrs0: pd.DataFrame, colour: pd.DataFrame, season: str, year: str,
        tags: pd.DataFrame | None = None) -> dict:
    """Back-test on last year's same season, then forecast the target season from all launch history.
    tags: product attributes read from images (nl_image_tags); styles without them use the attribute fallback."""
    for c in ("units", "wk", "stores"):
        launch[c] = pd.to_numeric(launch[c], errors="coerce").fillna(0)
    cmap = colour_map(attrs0, colour)
    A = attributes(attrs0, colour, cmap, tags)
    tot, share, curve = history(launch, A)
    prev_year = str(int(year) - 1)

    # back-test: last year's same season, from what was known one month before it launched
    prev = tot[(tot["season"] == season) & (tot["season_year"] == prev_year)]
    cut = prev["first_sale"].quantile(0.1) - pd.Timedelta(days=30)
    H = tot[tot["first_sale"] + pd.Timedelta(days=91) <= cut]
    T = prev[prev["first_sale"] >= cut]
    bt = forecast(A[A["parent"].isin(T["parent"])], H)
    mult = p80_multiplier(bt, T)
    rows = [{"level": "style", "groups": len(T), **score(bt, T)}]
    sib_bt = {t.parent: H.loc[(H["model"] == t.model) & (H["parent"] != t.parent), "parent"].tolist()
              for t in A[A["parent"].isin(T["parent"])].itertuples()}
    fcc = country_split(bt, share[share["parent"].isin(H["parent"])], sib_bt, {})
    act = launch[launch["parent"].isin(T["parent"]) & (launch["wk"] < WEEKS)].groupby(["parent", "country"])["units"].sum().reset_index()
    meta = A.set_index("parent")[["division", "gender", "ah2"]]
    for by in (["country"], ["division", "gender"], ["division", "gender", "country"]):
        p = fcc.join(meta, on="parent").groupby(by)["units_13w"].sum()
        y = act.join(meta, on="parent").groupby(by)["units"].sum()
        j = pd.concat([p, y], axis=1, keys=["p", "y"]).fillna(0)
        rows.append({"level": " x ".join(by), "groups": len(j), "styles": len(T),
                     "wape": float(np.abs(j.p - j.y).sum() / j.y.sum()), "bias": float(j.p.sum() / j.y.sum() - 1), "within_2x": np.nan})
    for meth in sorted(bt["method"].unique()):
        sub = bt[bt["method"] == meth]
        rows.append({"level": f"style · {meth}", "groups": len(sub), **score(sub, T)})
    m = bt.merge(T[["parent", "units"]], on="parent")
    backtest = pd.DataFrame(rows).assign(season=f"{season} {prev_year}", cutoff=str(cut.date()),
                                         p80_cover=float(np.mean(m["units"] <= m["units_13w"] * m["tier"].map(mult))))

    # target season: every style tagged season x year that has not sold yet, from all launch history
    tgt = A[(A["season"] == season) & (A["season_year"] == year) & ~A["parent"].isin(tot["parent"])]
    fc = forecast(tgt, tot)
    fc["units_p80"] = fc["units_13w"] * fc["tier"].map(mult).fillna(max(mult.values()) if mult else 1.5)
    sib = {t.parent: tot.loc[(tot["model"] == t.model) & (tot["parent"] != t.parent), "parent"].tolist() for t in tgt.itertuples()}
    country = country_split(fc, share, sib, {})
    # launch week: last year's typical first sale for the division, one year on
    lw = prev.groupby("division")["first_sale"].median() + pd.DateOffset(years=1)
    lw = lw.apply(lambda d: (d - pd.Timedelta(days=d.weekday())).normalize())
    styles = fc.merge(tgt[["parent", "name", "model", "colourway", "division", "ah2", "gender", "sport", "silhouette",
                           "plc", "rrp", "colour_family", "family", "url", "image_source"] + IMAGE_TAGS], on="parent", how="left")
    styles["launch_week"] = styles["division"].map(lw).fillna(lw.median() if len(lw) else pd.NaT)
    lc = launch_curve(tot, curve)
    wk = []
    for r in styles.itertuples():
        shp = lc.loc[(r.division, r.plc)] if (r.division, r.plc) in lc.index else lc.mean()
        for w in range(WEEKS):
            wk.append({"parent": r.parent, "wk": w, "week_start": (r.launch_week + pd.Timedelta(weeks=w)).date(),
                       "units_p50": r.units_13w * float(shp[w]), "units_p80": r.units_p80 * float(shp[w])})
    names = attrs0.set_index("parent")["name"].to_dict()
    ana = []
    units = tot.set_index("parent")["units"].to_dict(); first = tot.set_index("parent")["first_sale"].to_dict()
    for r in styles.itertuples():
        src = [(p, "same model", None, "same model") for p in sib.get(r.parent, [])[:5]] + \
              [(d["analogue"], "look-alike", d["score"], d["matched"]) for d in r.detail]
        for rank, (p, kind, sc, matched) in enumerate(src):
            if p in units:
                ana.append({"parent": r.parent, "rank": rank, "kind": kind, "analogue": p, "analogue_name": names.get(p),
                            "analogue_units_13w": float(units[p]), "analogue_first_sale": first[p].date(),
                            "score": sc, "matched": matched})
    styles["siblings"] = styles["siblings"].astype(int)
    styles = styles.drop(columns="detail")
    styles["analogues"] = styles["analogues"].apply(lambda x: ",".join(list(x)[:5]))
    styles["launch_week"] = styles["launch_week"].dt.date
    wrows = [{"scope": "fallback · all styles", "attribute": f, "kind": "image" if f in IMAGE_TAGS else "master", "weight": v}
             for f, v in MASTER_W.items()]
    for key, cfg in IMAGE_WEIGHTS.items():
        for f, v in cfg["weights"].items():
            wrows.append({"scope": " · ".join(key), "attribute": f, "kind": "image" if f in IMAGE_TAGS else "master", "weight": v,
                          **{f"val_{k}": x for k, x in cfg["validation"].items()}})
    return {"styles": styles, "country": country, "weekly": pd.DataFrame(wk), "analogues": pd.DataFrame(ana),
            "backtest": backtest, "mult": mult, "weights": pd.DataFrame(wrows)}

# COMMAND ----------
# MAGIC %md ### Run: launch history -> back-test (last spring) -> forecast the target season -> nl_ tables

# COMMAND ----------
import uuid
from datetime import datetime, timezone
RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
L = spark.sql(LAUNCH).toPandas()
A0 = spark.sql(ATTRS).toPandas()
C = spark.sql(COLOUR).toPandas()
try:
    TG = spark.sql(IMAGE_TAGS_SQL).toPandas()
except Exception as e:                       # no image tags yet: every style uses the attribute fallback
    print("no image tags:", e); TG = None
o = run(L, A0, C, TARGET_SEASON, TARGET_YEAR, TG)


def save(pdf, table):
    pdf = pdf.copy()
    for c in pdf.columns:
        if pdf[c].dtype == object:
            pdf[c] = pdf[c].astype(str).replace({"nan": None, "None": None})
    pdf["run_id"] = RUN_ID
    spark.createDataFrame(pdf).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.{table}")
    print(table, len(pdf))


for name, table in (("styles", "nl_forecast"), ("country", "nl_forecast_country"), ("weekly", "nl_forecast_weekly"),
                    ("analogues", "nl_analogues"), ("backtest", "nl_backtest"),
                    ("weights", "nl_weights")):
    save(o[name], table)
