"""Daily brief — exception management over the forecast + allocation run.

Every recommended line is either handled by the agents (inside the planner's guardrails) or lands on a decision card.
Cards group lines so a planner sees dozens of decisions, not thousands of rows:
  decide · Large send      store x style with a line above the auto-approve limit
  decide · Forecast swing  style x country whose next 2 weeks moved >= swing % against the last 4 weeks sold
  decide · Data issue      store with no grade (cannot be ranked) — hold or send anyway
  act    · Supply gap      style x country likely to sell with no DC stock: move from stores above pack, buy the rest
  act    · Overstock       store x style well above its pack that other stores in the country are short of
Pure pandas; the app reads the tables and the guardrails, this builds the brief.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULTS = {"auto_max_line_units": 5, "swing_pct": 50, "swing_min_weekly": 10, "gap_min_chance": 80, "gap_top_n": 10,
            "overstock_min_units": 20, "overstock_top_n": 20}
COUNTRY = {"AE": "UAE", "SA": "KSA", "KW": "Kuwait", "QA": "Qatar", "BH": "Bahrain", "OM": "Oman", "EG": "Egypt"}
REASONS = ["Store space or display limit", "Size mix not right for this store", "Local event or promotion",
           "Stock better used in another store", "Quantity too high", "Quantity too low", "Other"]


def style_label(name) -> str:
    return " ".join(w if (len(w) <= 2 or any(ch.isdigit() for ch in w)) else w.title() for w in str(name or "").split())


def _num(df, cols):
    for c in cols:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


def build(al: pd.DataFrame, sr: pd.DataFrame, sw: pd.DataFrame, guard: dict | None = None) -> dict:
    g = {**DEFAULTS, **(guard or {})}
    al = _num(al.copy(), ["alloc_qty", "need", "demand", "p_sell", "target", "atp", "intransit", "cover_units"])
    sr = _num(sr.copy(), ["atp", "intransit", "target", "need", "alloc_qty"])
    sw = _num(sw.copy(), ["fw", "hw"])
    al["line_key"] = al["store"].astype(str) + "|" + al["art"].astype(str)
    al["unit"] = np.where(al["cluster"].fillna("").str.upper().str.startswith("FOOTWEAR"), "pairs", "units")
    rec = al[al["alloc_qty"] > 0].copy()
    cards, held = [], set()

    # decide · Data issue — stores without a grade
    for (store, sname, country), d in rec[rec["grade"].fillna("") == "UNGRADED"].groupby(["store", "store_name", "country"]):
        kind = ("an outlet" if "OUTLET" in str(sname).upper() else
                "an e-commerce site" if "ECOM" in str(sname).upper() else "a store")
        cards.append({"id": f"data|{store}", "type": "Data issue", "kind": "decide", "country": country, "store": store,
                      "parent": None, "title": str(sname).title(), "sub": COUNTRY.get(country, country),
                      "why": f"This is {kind} with no grade in the store master or the store list, so the agents cannot "
                             f"rank it against other stores. {int(d['alloc_qty'].sum())} units are planned for it.",
                      "trail": ["Auditor held every line for this store until it has a grade."],
                      "rec": "Hold until the store has a grade." + (f" Check whether CORE replenishment should go to {kind}."
                                                                    if kind != "a store" else ""),
                      "impact": f"{int(d['alloc_qty'].sum())} units held", "score": float(d["alloc_qty"].sum()),
                      "rows": [], "lines": d["line_key"].tolist(), "send_label": "Send anyway", "reject_label": "Hold"})
        held |= set(d["line_key"])

    # decide · Large send — any line above the auto-approve limit; the card carries every size of that store x style
    big = rec[(rec["alloc_qty"] > g["auto_max_line_units"]) & ~rec["line_key"].isin(held)]
    for (store, parent), _ in big.groupby(["store", "parent"]):
        d = rec[(rec["store"] == store) & (rec["parent"] == parent) & ~rec["line_key"].isin(held)].sort_values("size")
        r0, units, have = d.iloc[0], int(d["alloc_qty"].sum()), int((d["atp"].clip(lower=0) + d["intransit"]).sum())
        cards.append({"id": f"big|{store}|{parent}", "type": "Large send", "kind": "decide", "country": r0["country"],
                      "store": store, "parent": parent, "title": style_label(r0["style_name"] or parent),
                      "sub": f"{str(r0['store_name']).title()} · grade {r0['grade']}",
                      "why": f"{len(d)} size{'s' if len(d) > 1 else ''}, {units} {r0['unit']} — at least one line is above "
                             f"your {int(g['auto_max_line_units'])}-unit limit. Expected demand {d['demand'].sum():.0f} "
                             f"{r0['unit']} over two weeks; the store holds {have} of these sizes.",
                      "trail": [f"Allocator sourced it from the {', '.join(sorted(set(d['source'].dropna())))}."],
                      "rec": f"Send {units} {r0['unit']}.", "impact": f"{units} {r0['unit']}", "score": float(units),
                      "rows": [{"label": f"Size {r.size}", "rec": int(r.alloc_qty), "lines": [r.line_key]} for r in d.itertuples()],
                      "lines": d["line_key"].tolist(), "send_label": "Send", "reject_label": "Reject"})
        held |= set(d["line_key"])

    # decide · Forecast swing — the whole style in the country, edited as one total (spread back over its lines)
    if not sw.empty:
        sw = sw[(np.maximum(sw["fw"], sw["hw"]) >= g["swing_min_weekly"]) & (sw["hw"] > 0)].copy()
        sw["chg"] = sw["fw"] / sw["hw"] - 1
        for r in sw[sw["chg"].abs() >= g["swing_pct"] / 100].itertuples():
            d = rec[(rec["country"] == r.country) & (rec["parent"] == r.parent) & ~rec["line_key"].isin(held)]
            if d.empty:
                continue
            units, unit = int(d["alloc_qty"].sum()), d["unit"].iloc[0]
            cards.append({"id": f"swing|{r.country}|{r.parent}", "type": "Forecast swing", "kind": "decide",
                          "country": r.country, "store": d.groupby("store")["alloc_qty"].sum().idxmax(), "parent": r.parent,
                          "title": style_label(d["style_name"].iloc[0] or r.parent), "sub": COUNTRY.get(r.country, r.country),
                          "why": f"Next two weeks are forecast at {r.fw:.0f} a week against {r.hw:.0f} a week sold over the "
                                 f"last 4 weeks ({r.chg:+.0%}).",
                          "trail": [f"Forecaster used the {r.variant} model for this {r.demand_class} style; the season "
                                    "curve and events explain the change."],
                          "rec": f"Accept the forecast: {units} {unit} to {d['store'].nunique()} stores. On recent sales it "
                                 f"would be about {units * r.hw / max(r.fw, 1e-9):.0f}.",
                          "impact": f"{r.chg:+.0%} vs last 4 weeks", "score": abs(r.chg) * units,
                          "rows": [{"label": f"Send to {d['store'].nunique()} stores", "rec": units, "lines": d["line_key"].tolist()}],
                          "lines": d["line_key"].tolist(), "send_label": "Send", "reject_label": "Reject"})
            held |= set(d["line_key"])

    # act · Supply gap — likely to sell, no DC stock: match against stock above pack in other stores, buy the rest
    over = sr.assign(over=(sr["atp"].clip(lower=0) + sr["intransit"].clip(lower=0) - sr["target"]).clip(lower=0))
    short = al[al["status"].isin(["No DC stock", "Part-allocated"]) & (al["p_sell"] >= g["gap_min_chance"] / 100)].copy()
    short["short"] = (short["need"] - short["alloc_qty"]).clip(lower=0)
    gaps = short.groupby(["country", "parent"])["short"].sum().sort_values(ascending=False).head(int(g["gap_top_n"]))
    for (country, parent), qty in gaps.items():
        d = short[(short["country"] == country) & (short["parent"] == parent)]
        moves = 0
        for art, need_art in d.groupby("art")["short"].sum().items():
            donors = over[(over["country"] == country) & (over["art"] == art) & ~over["store"].isin(d["store"])]
            moves += min(int(need_art), int(donors["over"].sum()))
        donor_n = over[(over["country"] == country) & over["art"].isin(d["art"]) & ~over["store"].isin(d["store"])
                       & (over["over"] > 0)]["store"].nunique()
        unit, top = d["unit"].iloc[0], d.groupby("store_name")["short"].sum().idxmax()
        rows = ([{"label": "Move store to store", "rec": moves}] if moves else []) + \
               ([{"label": "Buy request", "rec": int(qty) - moves}] if int(qty) - moves > 0 else [])
        cards.append({"id": f"gap|{country}|{parent}", "type": "Supply gap", "kind": "act", "country": country,
                      "store": d.groupby("store")["short"].sum().idxmax(), "parent": parent,
                      "title": style_label(d["style_name"].iloc[0] or parent), "sub": COUNTRY.get(country, country),
                      "why": f"{d['store'].nunique()} stores are short {int(qty)} {unit} with a high chance of selling in the "
                             f"next two weeks, and no DC holds them. Most short: {str(top).title()}.",
                      "trail": ["Allocator found no DC stock for these sizes."] +
                               ([f"Consolidator found {moves} {unit} above pack in {donor_n} other store{'s' if donor_n != 1 else ''} in "
                                 f"{COUNTRY.get(country, country)}."] if moves else
                                ["Consolidator found no spare stock of these sizes in other stores."]),
                      "rec": (f"Move {moves} store to store" + (f" and raise a buy request for {int(qty) - moves}."
                                                                   if int(qty) - moves > 0 else ".")) if moves else
                             f"Raise a buy request for {int(qty)}. No other store has spare stock of these sizes.",
                      "impact": f"{int(qty)} {unit} short", "score": float(qty) * 1.5, "rows": rows, "lines": [],
                      "send_label": "Send", "reject_label": "Dismiss"})

    # act · Overstock — well above pack, and other stores in the country are short of the same sizes
    shortall = al[(al["need"] - al["alloc_qty"]) > 0].assign(gap=lambda x: x["need"] - x["alloc_qty"])
    osum = over.groupby(["country", "store", "store_name", "parent", "style_name"])["over"].sum().reset_index()
    osum = osum[osum["over"] >= g["overstock_min_units"]]
    cand = []
    for r in osum.itertuples():
        o = over[(over["store"] == r.store) & (over["parent"] == r.parent) & (over["over"] > 0)]
        need = shortall[(shortall["country"] == r.country) & shortall["art"].isin(o["art"]) & (shortall["store"] != r.store)]
        matched = int(sum(min(int(ov), int(need.loc[need["art"] == a, "gap"].sum())) for a, ov in zip(o["art"], o["over"])))
        if matched > 0:
            cand.append((matched, r, need["store"].nunique()))
    for matched, r, n_to in sorted(cand, key=lambda x: -x[0])[: int(g["overstock_top_n"])]:
        unit = "pairs" if str(sr.loc[sr["parent"] == r.parent, "cluster"].iloc[0]).upper().startswith("FOOTWEAR") else "units"
        cards.append({"id": f"over|{r.store}|{r.parent}", "type": "Overstock", "kind": "act", "country": r.country,
                      "store": r.store, "parent": r.parent, "title": style_label(r.style_name or r.parent),
                      "sub": str(r.store_name).title(),
                      "why": f"The store holds {int(r.over)} {unit} above its two-week pack, and {n_to} other stores in "
                             f"{COUNTRY.get(r.country, r.country)} are short of the same sizes.",
                      "trail": [f"Consolidator matched {matched} {unit} to those stores."],
                      "rec": f"Transfer {matched} {unit} to {n_to} stores.", "impact": f"{int(r.over)} {unit} above pack",
                      "score": float(matched), "rows": [{"label": f"Transfer to {n_to} stores", "rec": matched}],
                      "lines": [], "send_label": "Send transfer", "reject_label": "Hold"})

    auto = rec[~rec["line_key"].isin(held)]
    by_type = pd.Series([c["type"] for c in cards]).value_counts().to_dict() if cards else {}
    return {"cards": sorted(cards, key=lambda c: -c["score"]), "auto": auto, "held": held, "rec": rec, "by_type": by_type,
            "stats": {"lines": int(len(rec)), "units": int(rec["alloc_qty"].sum()), "stores": int(rec["store"].nunique()),
                      "auto_lines": int(len(auto)), "auto_units": int(auto["alloc_qty"].sum()),
                      "auto_display": int((auto["reason"].fillna("").str.startswith("Display")).sum()),
                      "short_units": int(((al["need"] - al["alloc_qty"]).clip(lower=0)).sum()),
                      "decide": sum(1 for c in cards if c["kind"] == "decide"), "act": sum(1 for c in cards if c["kind"] == "act")},
            "guard": g}


def spread(total: int, lines: list, weights: list) -> dict:
    """Split an edited total back over its lines in proportion to the recommendation (largest remainder)."""
    w = np.array(weights, float)
    if total <= 0 or w.sum() <= 0:
        return {k: 0 for k in lines}
    raw = total * w / w.sum()
    base = np.floor(raw).astype(int)
    for i in np.argsort(-(raw - base))[: total - base.sum()]:
        base[i] += 1
    return dict(zip(lines, base.tolist()))
