"""Opening pack for new articles: the first allocation of a launch style, store x size, kept within the buy.

Country forecast (nl_forecast_country) -> ranged stores by their share of the style's division x gender sales ->
sizes by the country x division x AH2 x gender size curve (fc_size_curve, the curve the live allocation uses) ->
pack per store x size = the larger of one unit per key size (so the store can show the full run) and the units that
cover 80% of the first COVER_WEEKS of demand (negative binomial, the live allocation's method) -> rationed within the
buy, keeping RESERVE_SHARE at the DC for replenishment. Until the buy file arrives the buy is assumed = the country's
13-week P80, and every row says so.
"""
from __future__ import annotations

import re
from statistics import NormalDist

import numpy as np
import pandas as pd

COVER_WEEKS = 3                   # opening pack covers the first 3 weeks, until the first replenishment lands
PACK_SERVICE = 0.80               # covers 80% of 3-week outcomes per store x size
RESERVE_SHARE = 0.40              # of the buy kept at the DC for replenishment
MIN_STORE_SHARE = 0.01            # a store ranges the style if it sells >= 1% of the country's division x gender units
CORE_CUM = 0.85                   # key sizes when the master has none: the most-sold sizes up to 85% of the curve
PACK_CLUSTER_K = 200              # units a country x cluster needs before its size curve outweighs the GCC curve
PACK_BAND_EDGES = (0.25, 1, 3, 10)   # style x country units a week -> forecast error band (fc_error_bands)
GRADE_ORDER = {"BEACON": 0, "JORDAN": 0, "A+": 1, "A": 2, "B": 3, "C": 4, "UNGRADED": 5}
NOT_LAUNCH = [("NVS", None, "Outlet / factory store"), (None, r"\bNFS\b|OUTLET", "Outlet / factory store"),
              (None, r"ECOM", "E-commerce"), (None, r"^CONVERSE", "Converse store")]


def _nb_params(mu, alpha):
    """Negative binomial with mean mu and variance mu + alpha x mu^2 (same as fc_engine)."""
    mu = np.maximum(np.asarray(mu, float), 0)
    v = 1 + np.maximum(np.asarray(alpha, float), 0) * mu
    v = np.broadcast_to(v, mu.shape)
    pois = v < 1.001
    p = np.where(pois, 1.0, 1 / v)
    r = np.where(pois, np.inf, mu / np.maximum(v - 1, 1e-9))
    return mu, v, pois, p, r


def pack_p0(mu, alpha):
    mu, v, pois, p, r = _nb_params(mu, alpha)
    with np.errstate(invalid="ignore"):
        return np.where(pois, np.exp(-mu), np.exp(np.where(pois, 0, r) * np.log(p)))


def pack_quantile(mu, alpha, q):
    """Smallest whole number of units covering q of outcomes (same as fc_engine.nb_quantile)."""
    mu, v, pois, p, r = _nb_params(mu, alpha)
    small = mu <= 60
    pm = np.where(small, pack_p0(np.where(small, mu, 0), alpha), 0.0)
    cdf = pm.copy()
    res = np.where(small & (cdf >= q), 0, -1)
    k = 0
    while ((res < 0) & small).any() and k < 400:
        pm = np.where(pois, pm * mu / (k + 1), pm * (k + np.where(pois, 0, r)) / (k + 1) * (1 - p))
        k += 1
        cdf = cdf + pm
        res = np.where((res < 0) & small & (cdf >= q), k, res)
    big = np.ceil(mu + NormalDist().inv_cdf(q) * np.sqrt(v * mu))
    return np.where(mu <= 0, 0, np.where(small & (res >= 0), res, big)).astype(int)


def launch_stores(stores: pd.DataFrame) -> pd.DataFrame:
    """Store master + why a store does not take launches (outlets, e-commerce, Converse stores)."""
    s = stores.copy()
    s["grade"] = s["grade"].fillna("UNGRADED").astype(str)
    name = s["store_name"].fillna("").astype(str).str.upper()
    s["excluded"] = None
    for grade, pat, why in NOT_LAUNCH:
        hit = (s["grade"] == grade) if grade else name.str.contains(pat, regex=True)
        s.loc[hit & s["excluded"].isna(), "excluded"] = why
    s["grade_rank"] = s["grade"].map(GRADE_ORDER).fillna(6)
    return s


def _size_shares(sty: pd.DataFrame, curve: pd.DataFrame) -> pd.DataFrame:
    """sty: country, parent, cluster, art, size, key_size -> + size_share (country curve leaning on GCC when thin)."""
    cv = curve.copy()
    cv["units_26w"] = pd.to_numeric(cv["units_26w"], errors="coerce").fillna(0)
    ncc = cv.groupby(["country", "cluster"])["units_26w"].sum().rename("ncc").reset_index()
    gcc = cv.groupby(["cluster", "size"])["units_26w"].sum().rename("ug").reset_index()
    out = (sty.merge(cv[["country", "cluster", "size", "units_26w"]], on=["country", "cluster", "size"], how="left")
              .merge(gcc, on=["cluster", "size"], how="left").merge(ncc, on=["country", "cluster"], how="left"))
    out[["units_26w", "ug", "ncc"]] = out[["units_26w", "ug", "ncc"]].fillna(0)
    g = out.groupby(["country", "parent"])
    n = g["size"].transform("count")
    p_g = (out["ug"] / g["ug"].transform("sum").replace(0, np.nan)).fillna(1 / n)
    p_c = (out["units_26w"] / g["units_26w"].transform("sum").replace(0, np.nan)).fillna(p_g)
    w = out["ncc"] / (out["ncc"] + PACK_CLUSTER_K)
    out["size_share"] = w * p_c + (1 - w) * p_g
    out["size_share"] = out["size_share"] / out.groupby(["country", "parent"])["size_share"].transform("sum")
    # key sizes: the master's flag; a style with none flagged takes its most-sold sizes up to CORE_CUM of the curve
    out["key_size"] = pd.to_numeric(out["key_size"], errors="coerce").fillna(0).astype(int)
    out = out.sort_values(["country", "parent", "size_share"], ascending=[True, True, False])
    has_key = out.groupby(["country", "parent"])["key_size"].transform("max") > 0   # after the sort: np.where is positional
    before = out.groupby(["country", "parent"])["size_share"].cumsum() - out["size_share"]
    out["key_size"] = np.where(has_key, out["key_size"], (before < CORE_CUM).astype(int))
    return out.drop(columns=["units_26w", "ug", "ncc"])


def opening_pack(fc: pd.DataFrame, fc_country: pd.DataFrame, weekly: pd.DataFrame, sizes: pd.DataFrame,
                 stores: pd.DataFrame, store_units: pd.DataFrame, curve: pd.DataFrame, bands: pd.DataFrame,
                 buy: pd.DataFrame | None = None) -> dict:
    """Returns lines (store x size pack) and country (buy, packs, DC reserve, stores) for every launch style."""
    num = lambda d, cols: d.assign(**{c: pd.to_numeric(d[c], errors="coerce").fillna(0) for c in cols})
    fc, fc_country, weekly = num(fc, ["units_13w", "units_p80"]), num(fc_country, ["units_13w"]), num(weekly, ["units_p50"])
    sty = fc[["parent", "name", "division", "ah2", "gender", "units_13w", "units_p80", "confidence"]].rename(
        columns={"units_13w": "style_13w", "units_p80": "style_p80"})
    early = weekly[pd.to_numeric(weekly["wk"]) < COVER_WEEKS].groupby("parent")["units_p50"].sum().rename("style_3w")
    c = fc_country[["parent", "country", "units_13w"]].merge(sty, on="parent").merge(early.reset_index(), on="parent", how="left")
    c["share_c"] = c["units_13w"] / c["style_13w"].replace(0, np.nan)
    c["units_p80"] = c["style_p80"] * c["share_c"]
    c["demand_3w"] = c["style_3w"].fillna(0) * c["share_c"]
    c["buy_qty"], c["buy_source"] = np.ceil(c["units_p80"] - 1e-9).astype(int), "Assumed: 13-week P80 (no buy file yet)"
    if buy is not None and len(buy):
        b = num(buy, ["buy_qty"]).groupby(["parent", "country"])["buy_qty"].sum().rename("buy_file").reset_index()
        c = c.merge(b, on=["parent", "country"], how="left")
        c["buy_source"] = np.where(c["buy_file"].notna(), "Buy file", c["buy_source"])
        c["buy_qty"] = c["buy_file"].fillna(c["buy_qty"]).astype(int)
        c = c.drop(columns="buy_file")
    c["pack_cap"] = np.floor(c["buy_qty"] * (1 - RESERVE_SHARE) + 1e-9).astype(int)
    edges = np.asarray(PACK_BAND_EDGES, float)
    alpha = num(bands, ["nb_alpha", "band_order"]).sort_values("band_order")["nb_alpha"].to_numpy()
    c["nb_alpha"] = alpha[np.searchsorted(edges, (c["units_13w"] / 13).to_numpy(), side="right")]

    # stores: share of the style's division x gender units in its country (26 weeks), launch stores only
    st = launch_stores(stores)
    su = num(store_units, ["units"]).merge(st[["store", "excluded"]], on="store", how="left")
    su = su[su["excluded"].isna()]
    su["share"] = su["units"] / su.groupby(["country", "division", "gender"])["units"].transform("sum")
    su = su[su["share"] >= MIN_STORE_SHARE]
    su["store_share"] = su["share"] / su.groupby(["country", "division", "gender"])["share"].transform("sum")
    sp = c.merge(su[["country", "division", "gender", "store", "store_share"]], on=["country", "division", "gender"])
    sp = sp.merge(st[["store", "store_name", "grade", "grade_rank"]], on="store", how="left")

    # sizes: the style's sizes on the country x cluster size curve
    sz = sizes[["parent", "art", "size", "key_size"]].drop_duplicates(["parent", "art"])
    base = c[["country", "parent", "division", "ah2", "gender"]].copy()
    base["cluster"] = base["division"].astype(str) + " · " + base["ah2"].astype(str) + " · " + base["gender"].astype(str)
    ss = _size_shares(base[["country", "parent", "cluster"]].merge(sz, on="parent"), curve)
    keys = ss.groupby(["country", "parent"])["key_size"].sum().rename("key_sizes").reset_index()

    # how many stores the buy can dress with a full key-size run; the rest are left for later (best grades first)
    sp = sp.merge(keys, on=["country", "parent"], how="left").fillna({"key_sizes": 0})
    sp["max_stores"] = np.where(sp["key_sizes"] > 0, sp["pack_cap"] // sp["key_sizes"].clip(lower=1), 0)
    sp = sp.sort_values(["parent", "country", "grade_rank", "store_share"], ascending=[True, True, True, False])
    sp["store_rank"] = sp.groupby(["parent", "country"]).cumcount()
    sp["ranged"] = sp["store_rank"] < sp["max_stores"]

    L = sp[sp["ranged"]].merge(ss[["country", "parent", "art", "size", "key_size", "size_share"]], on=["country", "parent"])
    L["demand_3w"] = L["demand_3w"] * L["store_share"] * L["size_share"]
    L = L.rename(columns={"demand_3w": "demand"})
    L["p_sell"] = 1 - pack_p0(L["demand"].to_numpy(), L["nb_alpha"].to_numpy())
    L["cover_qty"] = pack_quantile(L["demand"].to_numpy(), L["nb_alpha"].to_numpy(), PACK_SERVICE)
    L["target"] = np.maximum(L["cover_qty"], L["key_size"]).astype(int)

    # ration within the cap: every unit is a step; first units of key sizes, then breadth before depth, best grades first
    L = L.reset_index(drop=True)
    rep = np.repeat(L.index.to_numpy(), L["target"].to_numpy())
    U = pd.DataFrame({"i": rep})
    U["k"] = U.groupby("i").cumcount() + 1
    U = U.join(L[["parent", "country", "key_size", "grade_rank", "demand", "pack_cap"]], on="i")
    U["first_key"] = np.where((U["k"] == 1) & (U["key_size"] == 1), 0, 1)
    U = U.sort_values(["parent", "country", "first_key", "k", "grade_rank", "demand"],
                      ascending=[True, True, True, True, True, False])
    U = U[U.groupby(["parent", "country"]).cumcount() < U["pack_cap"]]
    L["pack_qty"] = U.groupby("i").size().reindex(L.index, fill_value=0).astype(int)
    L["reason"] = np.select(
        [L["pack_qty"] < L["target"], (L["cover_qty"] <= L["key_size"]) & (L["key_size"] == 1), L["cover_qty"] > 0],
        ["Cut to fit the buy", "Display: 1 per key size", f"{COVER_WEEKS}-week demand ({PACK_SERVICE:.0%} cover)"],
        "Not needed (low chance)")
    lines = L[["country", "store", "store_name", "grade", "parent", "name", "art", "size", "key_size", "store_share",
               "size_share", "demand", "p_sell", "cover_qty", "target", "pack_qty", "reason"]]

    agg = L.groupby(["parent", "country"]).agg(pack_units=("pack_qty", "sum"), pack_need=("target", "sum"),
                                               stores_ranged=("store", "nunique")).reset_index()
    elig = sp.groupby(["parent", "country"])["store"].nunique().rename("stores_eligible").reset_index()
    cs = (c.merge(keys, on=["country", "parent"], how="left").merge(elig, on=["parent", "country"], how="left")
           .merge(agg, on=["parent", "country"], how="left"))
    cs[["pack_units", "pack_need", "stores_ranged", "stores_eligible", "key_sizes"]] = \
        cs[["pack_units", "pack_need", "stores_ranged", "stores_eligible", "key_sizes"]].fillna(0).astype(int)
    cs["reserve_units"] = cs["buy_qty"] - cs["pack_units"]
    cs["status"] = np.select(
        [cs["stores_eligible"] == 0, cs["stores_ranged"] == 0, cs["stores_ranged"] < cs["stores_eligible"]],
        ["No launch store sells this group", "Buy below one full size run — held at DC",
         "Buy covers the best-graded stores only"], "All eligible stores ranged")
    # what a person must look at before the pack goes out; everything else is approved as planned
    file_buy = cs["buy_source"] == "Buy file"
    cs["review"] = np.select(
        [cs["confidence"].astype(str).str.startswith("low"), file_buy & (cs["pack_units"] < cs["pack_need"]),
         file_buy & (cs["buy_qty"] > 2 * cs["units_13w"]), (cs["stores_ranged"] == 0) & (cs["units_13w"] >= 1)],
        ["Low-confidence look-alikes", "Buy short of the opening packs", "Buy over 2× the forecast",
         "Not ranged — buy below one full size run"], "")
    country = cs[["parent", "name", "country", "confidence", "units_13w", "units_p80", "demand_3w", "buy_qty",
                  "buy_source", "pack_cap", "pack_need", "pack_units", "reserve_units", "key_sizes", "stores_eligible",
                  "stores_ranged", "status", "review"]]
    return {"lines": lines, "country": country,
            "params": {"cover_weeks": COVER_WEEKS, "service": PACK_SERVICE, "reserve_share": RESERVE_SHARE,
                       "min_store_share": MIN_STORE_SHARE}}
