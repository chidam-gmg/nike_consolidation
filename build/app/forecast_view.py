"""Forecast + allocation agent flow (dev, 2026-10-30) — builds the board payload from the fc_ / al_ run tables
written by pipeline/forecast/20_core_forecast_alloc. Same board as the Agent lab, different agents."""
from __future__ import annotations

import pandas as pd

import agent_view

# forecast lane on top, allocation lane below; one human Approver at the end
AGENTS = [
    dict(id="scout", g="SC", name="Scout", role="Sales + stock history", stages=["history"], col=0, row=1),
    dict(id="profiler", g="PR", name="Profiler", role="Demand patterns", stages=["profile"], col=1, row=0),
    dict(id="calendar", g="CA", name="Calendar", role="Events + seasonality", stages=["calendar"], col=2, row=0),
    dict(id="tournament", g="MC", name="Model contest", role="Flat vs seasonal", stages=["tournament"], col=3, row=0),
    dict(id="forecaster", g="FC", name="Forecaster", role="8-week forecast", stages=["forecast"], col=4, row=0),
    dict(id="splitter", g="SP", name="Store splitter", role="Forecast to stores", stages=["store_split"], col=5, row=1),
    dict(id="curator", g="SZ", name="Size curator", role="Stores to sizes", stages=["size_split"], col=6, row=2),
    dict(id="stock", g="ST", name="Stock checker", role="ATP + in-transit", stages=["stock_check"], col=7, row=2),
    dict(id="allocator", g="AL", name="Allocator", role="DC rationing", stages=["allocate"], col=8, row=1),
    dict(id="human", g="HU", name="Approver", role="Human review", stages=[], col=9, row=1, human=True),
]
EDGES = [("scout", "profiler", "history"), ("profiler", "calendar", "profile"), ("calendar", "tournament", "calendar"),
         ("tournament", "forecaster", "tournament"), ("forecaster", "splitter", "forecast"), ("splitter", "curator", "store_split"),
         ("curator", "stock", "size_split"), ("stock", "allocator", "stock_check"), ("allocator", "human", "allocate")]


def _n(v):
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _run_label(summary: dict) -> str:
    try:
        t = pd.Timestamp(summary.get("run_ts")).tz_convert("Asia/Dubai")
        return f"{t.day} {t:%b %Y}, {t:%H:%M} GST"
    except Exception:
        return ""


def payload(stages: pd.DataFrame, events: pd.DataFrame, summary: dict, leaderboard: pd.DataFrame,
            forecast_series: int, next_week_units: float, alloc: dict, signoff: dict | None = None) -> dict:
    st = {}
    for _, r in stages.iterrows():
        st[r["stage"]] = {"start": int(float(r["start"])), "end": int(float(r["end"])),
                          "state": "done" if r["result"] == "SUCCESS" else "failed"}
    k = lambda v: (f"{float(v)/1000:.1f}K" if float(v or 0) >= 10000 else _n(v))   # short wire labels
    lab = {"history": f"{k(forecast_series)} series", "profile": "4 patterns", "calendar": "season + events",
           "tournament": "winners",
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
    fc_end = st.get("forecast", {}).get("end")
    kpis = [
        {"k": "Demand · next 2 weeks", "v": _n(alloc.get("next_2w", next_week_units)), "at": fc_end},
        {"k": "Units to send", "v": _n(alloc.get("units")), "at": end},
        {"k": "Stores served", "v": f"{_n(alloc.get('stores_sent'))} of {_n(summary.get('scope_stores'))}", "at": end},
        {"k": "Wanted · no DC stock", "v": _n(alloc.get("short_units")), "at": end},
        {"k": "Lines to decide", "v": _n(signoff["to_decide"] if signoff else alloc.get("lines")), "at": end, "accent": True},
    ] if alloc.get("short_units") is not None else [
        {"k": "Styles forecast", "v": _n(forecast_series), "at": fc_end},
        {"k": "Next week (P50)", "v": _n(next_week_units), "at": fc_end},
        {"k": "Error, 3+ a week", "v": f"{float(summary['error_fast'])*100:.0f}% (chance {float(summary['floor_fast'])*100:.0f}%)",
         "at": st.get("tournament", {}).get("end")} if summary.get("error_fast") not in (None, "", "nan") else
        {"k": "Forecast error", "v": f"{err_c*100:.0f}% (flat {float(summary.get('error_flat_best', 0))*100:.0f}%)",
         "at": st.get("tournament", {}).get("end")},
        {"k": "Units to allocate", "v": _n(alloc.get("units")), "at": end},
        {"k": "Lines to decide", "v": _n(signoff["to_decide"] if signoff else alloc.get("lines")), "at": end, "accent": True},
    ]
    return {"agents": AGENTS, "stages": st, "edges": edges, "events": ev, "kpis": kpis, "have_timings": bool(st),
            "has": True, "run_id": summary.get("run_id", ""), "who": "forecast job", "run_label": _run_label(summary),
            "scope": (f"CORE · {_n(summary.get('scope_countries'))} countries · {_n(summary.get('scope_stores'))} stores"
                      if summary.get("scope_stores") not in (None, "", "nan") else "CORE · all countries"),
            "colw": 150, "cw": 1520,
            "lanes": [["FORECAST", 2, "top"], ["ALLOCATION", 6, "bottom"]],
            "human_text": (f"{_n(signoff['to_decide'])} to decide · {_n(signoff['with_manager'])} with managers"
                           if signoff else f"plan ready · {_n(alloc.get('lines'))} lines"),
            "human_cls": "done" if signoff and not signoff["to_decide"] and not signoff["with_manager"] else "await"}


def render(*args, theme="light", show_log=True, **kw):
    p = payload(*args, **kw)
    p["show_log"] = show_log
    agent_view.render_flow(p, theme=theme, height=984 if show_log else 626)
