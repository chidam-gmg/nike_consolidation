"""Forecast + allocation agent flow (dev, 2026-10-30) — builds the board payload from the fc_ / al_ run tables
written by pipeline/forecast/20_core_forecast_alloc. Same board as the Agent lab, different agents."""
from __future__ import annotations

import pandas as pd

import agent_view

# forecast lane on top, allocation lane below; one human Approver at the end
AGENTS = [
    dict(id="scout", g="SC", name="Scout", role="Sales history", stages=["history"], col=0, row=1),
    dict(id="profiler", g="PR", name="Profiler", role="Demand patterns", stages=["profile"], col=1, row=0),
    dict(id="tournament", g="MC", name="Model contest", role="Backtest 5 models", stages=["tournament"], col=2, row=0),
    dict(id="forecaster", g="FC", name="Forecaster", role="8-week forecast", stages=["forecast"], col=3, row=0),
    dict(id="splitter", g="SP", name="Store splitter", role="Forecast to stores", stages=["store_split"], col=4, row=1),
    dict(id="curator", g="SZ", name="Size curator", role="Stores to sizes", stages=["size_split"], col=5, row=2),
    dict(id="stock", g="ST", name="Stock checker", role="ATP + in-transit", stages=["stock_check"], col=6, row=2),
    dict(id="allocator", g="AL", name="Allocator", role="DC rationing", stages=["allocate"], col=7, row=1),
    dict(id="human", g="HU", name="Approver", role="Human review", stages=[], col=8, row=1, human=True),
]
EDGES = [("scout", "profiler", "history"), ("profiler", "tournament", "profile"), ("tournament", "forecaster", "tournament"),
         ("forecaster", "splitter", "forecast"), ("splitter", "curator", "store_split"), ("curator", "stock", "size_split"),
         ("stock", "allocator", "stock_check"), ("allocator", "human", "allocate")]


def _n(v):
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


def payload(stages: pd.DataFrame, events: pd.DataFrame, summary: dict, leaderboard: pd.DataFrame,
            forecast_series: int, next_week_units: float, alloc: dict) -> dict:
    st = {}
    for _, r in stages.iterrows():
        st[r["stage"]] = {"start": int(float(r["start"])), "end": int(float(r["end"])),
                          "state": "done" if r["result"] == "SUCCESS" else "failed"}
    k = lambda v: (f"{float(v)/1000:.1f}K" if float(v or 0) >= 10000 else _n(v))   # short wire labels
    lab = {"history": f"{k(forecast_series)} series", "profile": "4 patterns", "tournament": "winners",
           "forecast": f"{k(next_week_units)}/wk", "store_split": f"{k(alloc.get('store_styles'))} lines",
           "size_split": f"{k(alloc.get('size_lines'))} sizes", "stock_check": f"{k(alloc.get('need_lines'))} short",
           "allocate": f"{k(alloc.get('lines'))} lines"}
    edges = [{"f": f, "t": t, "at": st.get(k, {}).get("end"), "label": lab[k]} for f, t, k in EDGES]
    ev = [{"ts": int(float(r["ts"])), "a": r["agent"], "lv": r["level"], "tx": r["text"]} for _, r in events.iterrows()]
    names = {a["id"]: a["name"] for a in AGENTS}
    ev += [{"ts": e["at"] + 50, "a": e["f"], "lv": "send", "tx": f"→ {names[e['t']]}: {e['label']}"} for e in edges if e["at"]]
    ev.sort(key=lambda e: e["ts"])
    err_c, err_b = float(summary.get("error_champion", 0)), float(summary.get("error_baseline", 0))
    end = max([v["end"] for v in st.values()] or [0])
    kpis = [
        {"k": "Styles forecast", "v": _n(forecast_series), "at": st.get("forecast", {}).get("end")},
        {"k": "Next week (P50)", "v": _n(next_week_units), "at": st.get("forecast", {}).get("end")},
        {"k": "Forecast error", "v": f"{err_c*100:.0f}% (avg {err_b*100:.0f}%)", "at": st.get("tournament", {}).get("end")},
        {"k": "Units to allocate", "v": _n(alloc.get("units")), "at": end},
        {"k": "Lines to approve", "v": _n(alloc.get("lines")), "at": end, "accent": True},
    ]
    return {"agents": AGENTS, "stages": st, "edges": edges, "events": ev, "kpis": kpis, "have_timings": bool(st),
            "has": True, "run_id": summary.get("run_id", ""), "who": "forecast job",
            "scope": "CORE · all countries → 21 active stores", "colw": 166, "cw": 1500,
            "lanes": [["FORECAST", 1, "top"], ["ALLOCATION", 5, "bottom"]],
            "human_text": f"plan ready · {_n(alloc.get('lines'))} lines", "human_cls": "await"}


def render(*args, theme="light", **kw):
    agent_view.render_flow(payload(*args, **kw), theme=theme, height=984)
