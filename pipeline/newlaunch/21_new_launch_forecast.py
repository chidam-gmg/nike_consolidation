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


def colour_map(attrs: pd.DataFrame, colour: pd.DataFrame) -> dict:
    """First character of the colourway code -> most common colour family on styles that have one (learned)."""
    x = attrs.merge(colour, on="parent").dropna(subset=["colourway", "colour"])
    x["c0"] = x["colourway"].str[0]
    return x.groupby("c0")["colour"].agg(lambda s: s.value_counts().index[0]).to_dict()


def attributes(attrs: pd.DataFrame, colour: pd.DataFrame, cmap: dict) -> pd.DataFrame:
    a = attrs.merge(colour[["parent", "colour", "url"]], on="parent", how="left")
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


def _score(t, H):
    """Similarity of target row t to history H (same division x gender already)."""
    s = (3.0 * (H["ah2"].values == t.ah2) + 2.0 * (H["sport"].values == t.sport)
         + 1.5 * (H["silhouette"].values == t.silhouette) + 1.0 * (H["plc"].values == t.plc)
         + 1.0 * (H["colour_family"].values == t.colour_family))
    if t.rrp and t.rrp > 0:
        r = H["rrp"].values
        s = s + 2.0 * np.exp(-np.abs(np.log(np.where(r > 0, r, np.nan) / t.rrp)) / 0.25)
    return np.nan_to_num(s)


def forecast(targets: pd.DataFrame, hist: pd.DataFrame, w_model: float = W_MODEL, k: int = K) -> pd.DataFrame:
    """One row per target style: tier, A / B / C estimates (13-week region units), blend, analogue list."""
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
        b, ana = np.nan, []
        if g is not None and len(g):
            sc = _score(t, g)
            top = g.assign(sc=sc).sort_values(["sc", "first_sale"], ascending=[False, False]).head(k)
            b, ana = float(top["units"].mean()), top["parent"].tolist()
        c = float(clus.get((t.division, t.ah2, t.gender), np.nan))
        if not np.isnan(a) and not np.isnan(b):
            est, tier = w_model * a + (1 - w_model) * b, "A · same model"
        elif not np.isnan(a):
            est, tier = a, "A · same model"
        elif not np.isnan(b):
            est, tier = b, "B · look-alikes"
        else:
            est, tier = c, "C · cluster"
        rows.append({"parent": t.parent, "tier": tier, "est_a": a, "est_b": b, "est_c": c,
                     "units_13w": max(0.0, float(est)) if not np.isnan(est) else 0.0,
                     "siblings": 0 if sib is None else len(sib), "analogues": ana})
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


def run(launch: pd.DataFrame, attrs0: pd.DataFrame, colour: pd.DataFrame, season: str, year: str) -> dict:
    """Back-test on last year's same season, then forecast the target season from all launch history."""
    for c in ("units", "wk", "stores"):
        launch[c] = pd.to_numeric(launch[c], errors="coerce").fillna(0)
    cmap = colour_map(attrs0, colour)
    A = attributes(attrs0, colour, cmap)
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
                           "plc", "rrp", "colour_family", "url"]], on="parent", how="left")
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
    for r in styles.itertuples():
        src = [(p, "same model") for p in sib.get(r.parent, [])[:5]] + [(p, "look-alike") for p in list(r.analogues)[:5]]
        for rank, (p, kind) in enumerate(src):
            h = tot.loc[tot["parent"] == p]
            if len(h):
                ana.append({"parent": r.parent, "rank": rank, "kind": kind, "analogue": p, "analogue_name": names.get(p),
                            "analogue_units_13w": float(h["units"].iloc[0]), "analogue_first_sale": h["first_sale"].iloc[0].date()})
    styles["siblings"] = styles["siblings"].astype(int)
    styles["analogues"] = styles["analogues"].apply(lambda x: ",".join(list(x)[:5]))
    styles["launch_week"] = styles["launch_week"].dt.date
    return {"styles": styles, "country": country, "weekly": pd.DataFrame(wk), "analogues": pd.DataFrame(ana),
            "backtest": backtest, "mult": mult}

# COMMAND ----------
# MAGIC %md ### Run: launch history -> back-test (last spring) -> forecast the target season -> nl_ tables

# COMMAND ----------
import uuid
from datetime import datetime, timezone
RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
L = spark.sql(LAUNCH).toPandas()
A0 = spark.sql(ATTRS).toPandas()
C = spark.sql(COLOUR).toPandas()
o = run(L, A0, C, TARGET_SEASON, TARGET_YEAR)


def save(pdf, table):
    pdf = pdf.copy()
    for c in pdf.columns:
        if pdf[c].dtype == object:
            pdf[c] = pdf[c].astype(str).replace({"nan": None, "None": None})
    pdf["run_id"] = RUN_ID
    spark.createDataFrame(pdf).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.{table}")
    print(table, len(pdf))


for name, table in (("styles", "nl_forecast"), ("country", "nl_forecast_country"), ("weekly", "nl_forecast_weekly"),
                    ("analogues", "nl_analogues"), ("backtest", "nl_backtest")):
    save(o[name], table)
