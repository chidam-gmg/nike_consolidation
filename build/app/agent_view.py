"""Agent lab — workflow-canvas view over the deterministic pipeline (business 2026-10-29, option A "n8n style").

Each agent is one or more REAL job tasks: status and timings come from the Databricks job run, every number in the
run log and on the wires comes from the run's own outputs (run_stats / replen_stats). Nothing here decides or
changes a recommendation — the engine stays rule-based and auditable. Rendered as one self-contained HTML
component (replay runs inside the iframe, so it never reruns the Streamlit script).
"""
from __future__ import annotations

import json
import time

import streamlit.components.v1 as components

# col/row = canvas grid; the two lanes split after Guard and meet at the Size Curator
AGENTS = [
    dict(id="scout", g="SC", name="Scout", role="Data readiness", stages=["config", "effective_stock"], col=0, row=1),
    dict(id="analyst", g="AN", name="Analyst", role="Demand & cover", stages=["classify"], col=1, row=1),
    dict(id="guard", g="GU", name="Guard", role="Rules & blocks", stages=["pools"], col=2, row=1),
    dict(id="match", g="MA", name="Matchmaker", role="Store to store", stages=["match"], col=3, row=0),
    dict(id="replen", g="RE", name="Replenisher", role="DC to store", stages=["replen_demand", "replen_ration"], col=3, row=2),
    dict(id="curator", g="SZ", name="Size Curator", role="Full / cut size", stages=["reshape", "receiver_guard"], col=4, row=1),
    dict(id="review", g="RV", name="Reviewer", role="Final picks", stages=["score", "replen_output"], col=5, row=1),
    dict(id="audit", g="AU", name="Auditor", role="Books closed", stages=["archive", "replen_history"], col=6, row=1),
    # the human in the loop: approves the exceptions before anything leaves the engine (no job stage)
    dict(id="human", g="HU", name="Approver", role="Human review", stages=[], col=7, row=1, human=True),
]
STAGE_AGENT = {s: a["id"] for a in AGENTS for s in a["stages"]}
STAGE_ORDER = ["config", "effective_stock", "classify", "pools", "match", "replen_demand", "reshape",
               "replen_ration", "receiver_guard", "score", "replen_output", "replen_history", "archive"]


def _num(v):
    try:
        f = float(v)
        return None if f != f else f          # NaN -> None
    except (TypeError, ValueError):
        return None


def _n(v):
    f = _num(v)
    return "—" if f is None else f"{f:,.0f}"


def _aed(v):
    f = _num(v)
    if f is None:
        return "AED —"
    return f"AED {f / 1e6:,.1f}M" if f >= 1e6 else f"AED {f / 1e3:,.0f}K" if f >= 1e3 else f"AED {f:,.0f}"


def _script(s: dict, r: dict, has: bool) -> dict:
    """Per stage: [(fraction of the stage, level, text)]. Levels: run / tool / out / hold. Without results yet
    (live run, before it archives) outputs are generic completion lines, with no numbers."""
    cat = s.get("category_scope") or "ALL"
    rec = str(s.get("partition_reconciles", "")).lower() in ("true", "1")
    out = lambda txt: [(1, "out", txt)] if has else [(1, "out", "stage complete")]
    hold = lambda txt: [(0.7, "hold", txt)] if has else []
    return {
        "config": [(0, "run", "start · freezing run config"),
                   (0.4, "tool", f"read cfg_scalar_params · scope {s.get('country_scope') or 'ALL'} · {cat}")]
                  + out(f"run_config written · sales through {s.get('as_of_date') or '—'}"),
        "effective_stock": [(0, "run", "start · building today's stock picture"),
                            (0.25, "tool", "read sap_gmg_datalake.bronze.atp_detail · latest ATP snapshot · SL01"),
                            (0.5, "tool", "read prod_mena.silver.combined_inventory · in-transit")]
                           + ([(0.75, "tool", f"scope articles · category {cat}")] if cat != "ALL" else [])
                           + out(f"effective_stock: {_n(s.get('universe_lines'))} store × size lines"),
        "classify": [(0, "run", "start · rate-of-sale and cover per size"),
                     (0.4, "tool", "read fct_sap_sales_detail_v · sales window")]
                    + out(f"{_n(s.get('oos_receiver_candidates'))} sizes selling but out of stock · "
                          f"{_n(s.get('aplus_curve_candidates'))} A+ display gaps"),
        "pools": [(0, "run", "start · applying receiver rules"),
                  (0.3, "tool", "read APO flags · read recommendations_history (30 days)")]
                 + hold(f"blocked receivers · APO {_n(s.get('apo_blocked_candidates'))} · season "
                        f"{_n(s.get('season_blocked_candidates'))} · moved out <30d {_n(s.get('moveback_blocked_candidates'))}")
                 + out(f"{_n(s.get('oos_deferred_to_replen'))} out-of-stock sizes routed to the DC first"),
        "match": [(0, "run", "start · pairing surplus with need")]
                 + out(f"paired {_n(s.get('donor_stores'))} donor stores ↔ {_n(s.get('receiver_stores'))} receiving stores"),
        "replen_demand": [(0, "run", "start · store needs vs ATP + in-transit"),
                          (0.5, "tool", "read POS demand · 14-day cover target")]
                         + out("needs computed for every selling size"),
        "replen_ration": [(0, "run", "rationing · primary pool first, then shared hub")]
                         + hold(f"{_n(r.get('no_dc_stock_lines'))} store needs had no DC stock")
                         + out(f"DC stock rationed across {_n(r.get('stores'))} stores"),
        "reshape": [(0, "run", "start · checking donor size runs")]
                   + out(f"protected {_n(s.get('held_step8'))} donor size runs from breaking"),
        "receiver_guard": [(0, "run", "start · joint full-size check, store + DC"),
                           (0.3, "tool", "position = ATP + in-transit + store moves + DC lines − donations")]
                          + hold(f"held {_n(s.get('receiver_cut_blocked_moves'))} store moves + "
                                 f"{_n(r.get('held_receiver_cut_lines'))} DC lines · receiver would stay cut-size")
                          + out(f"DC top-up · {_n(r.get('fullsize_topup_units'))} sizes for "
                                f"{_n(r.get('fullsize_topup_pairs'))} store × style pairs → full size (for review)"),
        "score": [(0, "run", "start · scoring store moves")]
                 + out(f"{_n(s.get('recommended_moves'))} store moves · {_n(s.get('total_units'))} units · "
                       f"est. uplift {_aed(s.get('total_expected_uplift'))}"),
        "replen_output": [(0, "run", "start · finalising DC lines")]
                         + out(f"{_n(r.get('replen_lines'))} DC lines · {_n(r.get('replen_units'))} units · "
                               f"{_aed(r.get('fill_value_aed'))}"),
        "replen_history": [(0, "run", "archiving DC lines")] + out("DC run archived"),
        "archive": [(0, "run", "reconciling every stock line")]
                   + ([(0.6, "out", f"{_n(s.get('moved_donor_lines'))} moved + {_n(s.get('held_lines'))} held + "
                                    f"{_n(s.get('not_moved_lines'))} kept = {_n(s.get('universe_lines'))} · "
                                    + ("OK" if rec else "MISMATCH"))] if has else [])
                   + out("run archived to history"),
    }


def _payload(job_run: dict | None, metrics: dict, live: bool, run_id: str | None = None, theme: str = "light",
             review: dict | None = None) -> dict:
    s, r = (metrics or {}).get("stats") or {}, (metrics or {}).get("replen") or {}
    has = bool(s)
    tasks = {t["key"]: t for t in (job_run or {}).get("tasks", [])}
    have_timings = bool(tasks)
    # without job timings (old run, or no job view permission) lay the finished stages out on a synthetic clock
    if not have_timings and has:
        tasks = {k: {"key": k, "start": i * 1000 + 1, "end": i * 1000 + 900, "result": "SUCCESS", "life": "TERMINATED"}
                 for i, k in enumerate(STAGE_ORDER)}

    def stage(k):
        t = tasks.get(k)
        if not t:
            return {"start": None, "end": None, "state": "waiting"}
        res, life = t.get("result"), t.get("life")
        state = ("failed" if res in ("FAILED", "TIMEDOUT", "CANCELED", "UPSTREAM_FAILED")
                 else "done" if res in ("SUCCESS", "SKIPPED")
                 else "running" if (t.get("start") and not t.get("end")) or life in ("RUNNING", "PENDING", "TERMINATING")
                 else "waiting")
        return {"start": t.get("start") or None, "end": (t.get("end") or None) if state in ("done", "failed") else None,
                "state": state}

    stages = {k: stage(k) for k in STAGE_AGENT}
    names = {a["id"]: a["name"] for a in AGENTS}
    events = []
    for k, lines in _script(s, r, has).items():
        st = stages[k]
        if not st["start"]:
            continue
        for i, (f, lv, tx) in enumerate(lines):
            if st["end"]:
                ts = st["start"] + f * (st["end"] - st["start"]) + (0 if f == 1 else i)
            elif f == 0 or lv == "tool":
                ts = st["start"] + i * 400            # running: start + tool calls only, no results yet
            else:
                continue
            if st["state"] == "failed" and f == 1:
                lv, tx = "hold", "stage failed — open the run in Databricks"
            events.append({"ts": ts, "a": STAGE_AGENT[k], "lv": lv, "tx": tx})

    def edge(f, t, after, label):
        return {"f": f, "t": t, "at": stages[after]["end"] if stages[after]["state"] == "done" else None,
                "label": label if has else "handed over"}
    edges = [
        edge("scout", "analyst", "effective_stock", f"{_n(s.get('universe_lines'))} lines"),
        edge("analyst", "guard", "classify", f"{_n(s.get('oos_receiver_candidates'))} OOS sizes"),
        edge("guard", "match", "pools", "receiver pool"),
        edge("guard", "replen", "pools", f"{_n(s.get('oos_deferred_to_replen'))} DC-first"),
        edge("match", "curator", "match", f"{_n(s.get('donor_stores'))} → {_n(s.get('receiver_stores'))} stores"),
        edge("replen", "curator", "replen_ration", f"{_n(r.get('replen_lines'))} DC lines"),
        edge("curator", "review", "receiver_guard", f"{_n(r.get('fullsize_topup_units'))} top-ups"),
        edge("review", "audit", "replen_output",
             f"{_n((_num(s.get('recommended_moves')) or 0) + (_num(r.get('replen_lines')) or 0))} lines"),
    ]
    rv = review or {}
    audit_end = max([x for x in (stages["archive"]["end"], stages["replen_history"]["end"]) if x] or [0]) or None
    if has and rv.get("total") is not None:
        edges.append({"f": "audit", "t": "human", "at": audit_end, "label": f"{_n(rv.get('total'))} to review"})
    for e in edges:
        if e["at"]:
            events.append({"ts": e["at"] + 50, "a": e["f"], "lv": "send", "tx": f"→ {names[e['t']]}: {e['label']}"})
    if has and audit_end and rv.get("total") is not None:
        routine = (_num(s.get("recommended_moves")) or 0) + (_num(r.get("replen_lines")) or 0) - (rv.get("total") or 0)
        events.append({"ts": audit_end + 120, "a": "human", "lv": "wait",
                       "tx": f"{_n(rv.get('total'))} lines need a decision ({_n(rv.get('topup'))} full-size top-ups, "
                             f"{_n(rv.get('other'))} high value / large qty) · {_n(routine)} routine lines auto-approved"})
        if (rv.get("approved") or 0) + (rv.get("rejected") or 0):
            events.append({"ts": audit_end + 240, "a": "human", "lv": "out",
                           "tx": f"approved {_n(rv.get('approved'))} · rejected {_n(rv.get('rejected'))} · "
                                 f"pending {_n(rv.get('pending'))}"})
    events.sort(key=lambda e: e["ts"])

    kpis = [
        {"k": "Store moves", "v": _n(s.get("recommended_moves")), "at": stages["score"]["end"]},
        {"k": "Units moved", "v": _n(s.get("total_units")), "at": stages["score"]["end"]},
        {"k": "DC lines", "v": _n(r.get("replen_lines")), "at": stages["replen_output"]["end"]},
        {"k": "DC value", "v": _aed(r.get("fill_value_aed")), "at": stages["replen_output"]["end"]},
        {"k": "Held by rules", "v": _n(s.get("held_lines")), "at": stages["score"]["end"]},
    ]
    jr = job_run or {}
    return {
        "live": live, "theme": theme, "review": rv, "now": int(time.time() * 1000), "have_timings": have_timings, "has": has,
        "agents": [dict(a) for a in AGENTS], "stages": stages, "edges": edges, "events": events, "kpis": kpis,
        "run_id": jr.get("engine_run_id") or run_id or "",
        # the scheduled 10:30 job runs with the job's default TRIGGERED_BY ('app'); app users are recorded by email
        "who": {"app": "scheduled", "": "scheduled"}.get(str(s.get("triggered_by") or jr.get("triggered_by") or ""),
                                                          str(s.get("triggered_by") or jr.get("triggered_by"))),
        "scope": f"{s.get('country_scope') or jr.get('country') or 'ALL'} · {s.get('category_scope') or jr.get('category') or 'ALL'}",
    }


def render(job_run: dict | None, metrics: dict, live: bool = False, run_id: str | None = None,
           theme: str = "light", review: dict | None = None, height: int = 1040):
    """Draw the workflow canvas + run log. live=True: current state of a running job (no replay).
    theme follows the app's Dark mode toggle so the board blends into the page either way."""
    data = json.dumps(_payload(job_run, metrics, live, run_id, theme, review)).replace("</", "<\\/")
    components.html(_HTML.replace("__DATA__", data), height=height, scrolling=True)


_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&family=Source+Code+Pro:wght@400;500;600&display=swap">
<style>
:root { /* light = GMG brand (matches the app page); dark = console. Set by the app's Dark mode toggle. */
  color-scheme: light;
  --bg:#FFFFFF; --panel:#F6F8F9; --panel-2:#FFFFFF; --line:#DCE3E6; --fg:#20274C; --muted:#5C6478; --dim:#68708A;
  --accent:#2658F2; --run:#9A5A00; --ok:#12704D; --hold:#9A4A33; --dot:#E2E8EB; --console:#FBFCFC;
  --wire-done:#9DB0E8; --acc-soft:rgba(38,88,242,.10); --run-soft:rgba(196,122,16,.14); --ok-soft:rgba(30,158,106,.12);
  --ui:"Source Sans 3", "Source Sans Pro", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono:"Source Code Pro", ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace; }
:root[data-theme="dark"] { color-scheme: dark;
  --bg:#0A0D16; --panel:#111626; --panel-2:#161C30; --line:#252E48; --fg:#E5E9F4; --muted:#8790AB; --dim:#7A84A3;
  --accent:#5B86FF; --run:#F2A93B; --ok:#34C08F; --hold:#E07A5F; --dot:#1C2338; --console:#080B13;
  --wire-done:#3D4B78; --acc-soft:rgba(91,134,255,.12); --run-soft:rgba(242,169,59,.14); --ok-soft:rgba(52,192,143,.15); }
* { box-sizing:border-box; }
html, body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.45 var(--ui); }
.wrap { display:grid; gap:12px; padding:4px 0 8px; }
.head { display:flex; flex-wrap:wrap; gap:8px 14px; align-items:center; }
.head .t { font-weight:600; font-size:15px; }
.chip { font:11px var(--mono); color:var(--muted); border:1px solid var(--line); border-radius:8px; padding:4px 8px; background:var(--panel); }
.chip b { color:var(--fg); font-weight:500; }
.live { font:600 11px var(--mono); letter-spacing:.1em; color:var(--run); display:inline-flex; align-items:center; gap:6px; }
.live::before { content:""; width:7px; height:7px; border-radius:50%; background:var(--run); animation:pulse 1s ease-in-out infinite; }
[hidden] { display:none !important; }
.grow { flex:1; }
.clock { font:12px var(--mono); color:var(--muted); } .clock b { color:var(--fg); font-weight:500; }
.btn { font:600 13px var(--ui); color:var(--fg); background:var(--panel-2); border:1px solid var(--line); border-radius:8px; padding:8px 16px; min-height:36px; cursor:pointer; }
.btn:hover { border-color:var(--accent); } .btn.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
.btn:focus-visible, .filter:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.kpis { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); border:1px solid var(--line); border-radius:10px; background:var(--panel); overflow:hidden; }
.kpi { padding:8px 16px; border-right:1px solid var(--line); min-width:0; } .kpi:last-child { border-right:0; }
.kpi .l { font-size:10.5px; letter-spacing:.1em; text-transform:uppercase; color:var(--muted); }
.kpi .v { font:500 21px var(--mono); font-variant-numeric:tabular-nums; margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.stage { border:1px solid var(--line); border-radius:12px; overflow:hidden; position:relative;
  background-color:var(--bg); background-image:radial-gradient(var(--dot) 1.2px, transparent 1.2px); background-size:18px 18px; }
.canvas { position:absolute; left:0; top:0; width:1440px; height:480px; transform-origin:0 0; }
.canvas svg { position:absolute; left:0; top:0; width:1440px; height:480px; overflow:visible; }
.edge { fill:none; stroke:var(--line); stroke-width:2; } .edge.active { stroke:var(--run); } .edge.done { stroke:var(--wire-done); }
.pkt { fill:var(--run); }
.cnt rect { fill:var(--panel-2); stroke:var(--line); } .cnt text { fill:var(--muted); font:10px var(--mono); }
.node { position:absolute; width:150px; display:grid; justify-items:center; gap:8px; }
.box { position:relative; width:84px; height:84px; border-radius:14px; background:var(--panel-2); border:1.5px solid var(--line);
  display:grid; place-items:center; transition:border-color .3s, box-shadow .3s; }
.glyph { font:600 20px var(--mono); color:var(--muted); }
.port { position:absolute; top:37px; width:10px; height:10px; border-radius:50%; background:var(--panel); border:1.5px solid var(--dim); }
.port.in { left:-6px; } .port.out { right:-6px; }
.badge { position:absolute; top:-8px; right:-8px; width:20px; height:20px; border-radius:50%; background:var(--panel); border:1.5px solid var(--line); display:grid; place-items:center; }
.badge svg { width:11px; height:11px; }
.node.s-done .badge { color:var(--ok); } .node.s-running .badge { color:var(--run); } .node.s-failed .badge { color:var(--hold); }
.nm { font-size:12.5px; font-weight:600; text-align:center; }
.rl { font:10px var(--mono); color:var(--muted); text-align:center; margin-top:-4px; }
.node.s-running .box { border-color:var(--run); box-shadow:0 0 0 4px var(--run-soft); } .node.s-running .glyph { color:var(--run); }
.node.s-running .badge { border-color:var(--run); } .node.s-running .badge svg { animation:spin 1s linear infinite; }
.node.s-done .box { border-color:var(--ok); } .node.s-done .glyph { color:var(--fg); }
.node.s-done .badge { border-color:var(--ok); background:var(--ok-soft); }
.node.s-failed .box { border-color:var(--hold); } .node.s-failed .glyph { color:var(--hold); } .node.s-failed .badge { border-color:var(--hold); }
.node.s-idle .box { border-color:var(--accent); }
.node.s-waiting .box { opacity:.55; }
.lane { position:absolute; font:10px var(--mono); color:var(--dim); letter-spacing:.12em; }
.stt { font:600 9.5px var(--mono); letter-spacing:.06em; text-transform:uppercase; text-align:center; line-height:1.3;
  padding:4px 8px; border-radius:8px; border:1px solid var(--line); color:var(--dim); background:var(--panel); max-width:150px; }
.node.s-running .stt { color:var(--run); border-color:var(--run); background:var(--run-soft); }
.node.s-done .stt { color:var(--ok); border-color:transparent; background:transparent; }
.node.s-failed .stt { color:var(--hold); border-color:var(--hold); }
.node.s-idle .stt, .node.s-ready .stt { color:var(--accent); border-color:var(--accent); background:var(--acc-soft); }
.node.s-ready .box { border-color:var(--accent); opacity:1; }
.node.human .box { border-style:dashed; border-radius:42px; }
.node.s-await .box { border-color:var(--accent); border-style:solid; box-shadow:0 0 0 4px var(--acc-soft); animation:await 1.6s ease-in-out infinite; }
.node.s-await .glyph { color:var(--accent); }
.node.s-await .stt { color:var(--accent); border-color:var(--accent); background:var(--acc-soft); }
@keyframes await { 50% { box-shadow:0 0 0 9px transparent; } }
.lv.wait { color:var(--accent); }
.edge.pending { stroke:var(--run); stroke-dasharray:3 6; opacity:.55; animation:dash 1.2s linear infinite; }
@keyframes dash { to { stroke-dashoffset:-18; } }
.now { display:flex; flex-wrap:wrap; gap:8px 16px; align-items:center; padding:8px 16px; border:1px solid var(--line); border-radius:10px;
  background:var(--panel); font:12px var(--mono); }
.now .k { color:var(--muted); font-size:10.5px; letter-spacing:.1em; text-transform:uppercase; margin-right:6px; }
.now .v { color:var(--fg); } .now .v.run { color:var(--run); } .now .v.ok { color:var(--ok); } .now .v.fail { color:var(--hold); }
.now .bar { flex:1 1 160px; height:4px; background:var(--line); border-radius:2px; overflow:hidden; min-width:120px; }
.now .bar i { display:block; height:100%; background:var(--accent); width:0; transition:width .3s; }
@keyframes spin { to { transform:rotate(360deg); } }
@keyframes pulse { 50% { opacity:.3; } }
.log { border:1px solid var(--line); border-radius:12px; background:var(--console); overflow:hidden; }
.log-head { display:flex; flex-wrap:wrap; gap:8px 12px; align-items:center; padding:8px 12px; border-bottom:1px solid var(--line); background:var(--panel); }
.log-head .t { font:11px var(--mono); letter-spacing:.12em; color:var(--muted); }
.filters { display:flex; gap:4px; flex-wrap:wrap; }
.filter { font:500 11px var(--mono); color:var(--muted); background:transparent; border:1px solid var(--line); border-radius:8px; padding:4px 8px; min-height:28px; cursor:pointer; }
.filter[aria-pressed="true"] { color:var(--fg); border-color:var(--accent); background:var(--acc-soft); }
.log-body { height:290px; overflow:auto; font:12px var(--mono); }
.ln { display:grid; grid-template-columns:72px 128px 56px minmax(0,1fr); gap:12px; padding:4px 12px; border-bottom:1px solid var(--panel); }
.ln:hover { background:var(--acc-soft); }
.ts { color:var(--dim); font-variant-numeric:tabular-nums; }
.nots .ln { grid-template-columns:128px 56px minmax(0,1fr); } .nots .ts { display:none; } .ag { color:var(--accent); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.lv { font-size:10.5px; padding-top:1px; } .lv.run { color:var(--run); } .lv.tool { color:var(--accent); } .lv.out { color:var(--ok); }
.lv.send { color:var(--muted); } .lv.hold { color:var(--hold); }
.tx { color:var(--fg); overflow-wrap:anywhere; }
.empty { padding:12px; color:var(--muted); }
.ln.new { animation:flash .6s ease; } @keyframes flash { from { background:var(--run-soft); } to { background:transparent; } }
.cursor { display:inline-block; width:7px; height:13px; margin:6px 12px; background:var(--run); animation:pulse 1s steps(2) infinite; }
.foot { font-size:11.5px; color:var(--muted); }
@media (max-width:720px) { .kpis { grid-template-columns:repeat(2,minmax(0,1fr)); } .ln { grid-template-columns:62px minmax(0,1fr); } .ag, .lv { display:none; } }
@media (prefers-reduced-motion:reduce) { *, *::before, *::after { animation:none !important; transition:none !important; } }
</style></head><body><div class="wrap">
<div class="head">
  <span class="chip">RUN <b id="c-run"></b></span><span class="chip">SCOPE <b id="c-scope"></b></span><span class="chip">BY <b id="c-who"></b></span>
  <span class="live" id="live" hidden>LIVE</span>
  <span class="grow"></span>
  <span class="clock" id="clock"></span>
  <button class="btn" id="speed" type="button">Speed 1×</button>
  <button class="btn primary" id="play" type="button">Replay run</button>
</div>
<div class="kpis" id="kpis"></div>
<div class="now" id="now"></div>
<div class="stage"><div class="canvas" id="canvas"><svg id="edges" viewBox="0 0 1440 480" aria-hidden="true"></svg></div></div>
<div class="log"><div class="log-head"><span class="t">RUN LOG</span><div class="filters" id="filters"></div></div><div class="log-body" id="logbody"></div></div>
</div>
<script>
const D = __DATA__;
document.documentElement.dataset.theme = D.theme === "dark" ? "dark" : "light";
const S = D.stages, A = {}; D.agents.forEach(a => A[a.id] = a);
const SV = Object.values(S).filter(s => s.start);
const T0 = SV.length ? Math.min(...SV.map(s => s.start)) : 0;
const T1 = D.live ? D.now : (SV.length ? Math.max(...SV.map(s => s.end || s.start)) : 0);
const pad = n => String(n).padStart(2, "0");
const gst = ms => { if (!D.have_timings || !ms) return "--:--:--"; const d = new Date(ms + 4 * 3600e3);
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`; };
const LV = { run: "RUN", tool: "TOOL", out: "OUT", send: "SEND", hold: "HOLD", wait: "WAIT" };
document.getElementById("c-run").textContent = D.run_id || (D.live ? "in progress" : "latest");
document.getElementById("c-scope").textContent = D.scope;
document.getElementById("c-who").textContent = D.who || "scheduled";
if (D.live) document.getElementById("live").hidden = false;
if (D.live || !D.have_timings) { document.getElementById("play").hidden = true; document.getElementById("speed").hidden = true; }

function stageState(k, now) {
  const s = S[k]; if (!s.start || now < s.start) return "waiting";
  if (s.state === "failed" && (!s.end || now >= s.end)) return "failed";
  if (s.end && now >= s.end) return "done";
  return "running";
}
function agentState(a, now) {
  const st = a.stages.map(k => stageState(k, now));
  if (st.includes("failed")) return "failed";
  if (st.includes("running")) return "running";
  if (st.every(x => x === "done")) return "done";
  if (st.includes("done")) return "idle";
  return "waiting";
}

/* canvas */
const canvas = document.getElementById("canvas"), svg = document.getElementById("edges"), NS = "http://www.w3.org/2000/svg";
const NX = c => 44 + c * 180, NY = r => 40 + r * 130;
const ICON = { done: '<svg viewBox="0 0 12 12"><path d="M2.5 6.2l2.3 2.3 4.7-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  running: '<svg viewBox="0 0 12 12"><path d="M6 1.5a4.5 4.5 0 1 1-4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  failed: '<svg viewBox="0 0 12 12"><path d="M3.5 3.5l5 5M8.5 3.5l-5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>' };
const nodes = {};
D.agents.forEach(a => {
  const el = document.createElement("div"); el.className = "node s-waiting"; el.style.left = (NX(a.col) - 33) + "px"; el.style.top = NY(a.row) + "px";
  el.innerHTML = '<div class="box"><span class="port in"></span><span class="glyph"></span><span class="port out"></span><span class="badge"></span></div><div class="nm"></div><div class="rl"></div><div class="stt"></div>';
  el.querySelector(".glyph").textContent = a.g; el.querySelector(".nm").textContent = a.name; el.querySelector(".rl").textContent = a.role;
  canvas.appendChild(el); nodes[a.id] = el;
});
[["STORE ↔ STORE", NY(0) - 22], ["DC → STORE", NY(2) + 152]].forEach(([t, y]) => { const l = document.createElement("div"); l.className = "lane";
  l.textContent = t; l.style.left = (NX(3) - 6) + "px"; l.style.top = y + "px"; canvas.appendChild(l); });
const edges = D.edges.map(e => {
  const a = A[e.f], b = A[e.t];
  const x1 = NX(a.col) + 88, y1 = NY(a.row) + 42, x2 = NX(b.col) - 4, y2 = NY(b.row) + 42, dx = Math.max(40, (x2 - x1) / 2);
  const p = document.createElementNS(NS, "path"); p.setAttribute("d", `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`);
  p.setAttribute("class", "edge"); svg.appendChild(p);
  const pk = document.createElementNS(NS, "circle"); pk.setAttribute("r", "3.5"); pk.setAttribute("class", "pkt"); pk.style.display = "none"; svg.appendChild(pk);
  const g = document.createElementNS(NS, "g"); g.setAttribute("class", "cnt"); g.style.display = "none";
  const len = p.getTotalLength(), mid = p.getPointAtLength(len / 2), mx = mid.x, my = y1 === y2 ? y1 - 14 : mid.y, w = e.label.length * 6.1 + 14;
  const r = document.createElementNS(NS, "rect"); r.setAttribute("x", mx - w / 2); r.setAttribute("y", my - 9); r.setAttribute("width", w); r.setAttribute("height", 18); r.setAttribute("rx", 9);
  const tx = document.createElementNS(NS, "text"); tx.setAttribute("x", mx); tx.setAttribute("y", my + 3.5); tx.setAttribute("text-anchor", "middle"); tx.textContent = e.label;
  g.append(r, tx); svg.appendChild(g);
  return { e, p, pk, g, len };
});
const UP = {}; D.edges.forEach(e => (UP[e.t] = UP[e.t] || []).push(e.f));
const clk = ms => { const t = Math.max(0, Math.round(ms / 1000)); return t >= 60 ? `${Math.floor(t / 60)}m ${pad(t % 60)}s` : `${t}s`; };
function runningStage(a, now) { return a.stages.find(k => stageState(k, now) === "running"); }
const RV = D.review || {};
function humanStatus(now) {     // approver: waits for the Auditor, then for people to decide the review items
  const auditDone = agentState(A.audit, now) === "done";
  if (!auditDone) return ["waiting", "waiting for Auditor"];
  if (RV.total === undefined || RV.total === null) return ["waiting", "review not loaded"];
  if (!RV.total) return ["done", "nothing to review"];
  if (RV.pending > 0) return ["await", `awaiting approval · ${RV.pending.toLocaleString("en-US")} of ${RV.total.toLocaleString("en-US")}`];
  return ["done", `approved ${RV.approved.toLocaleString("en-US")} · rejected ${RV.rejected.toLocaleString("en-US")}`];
}
function nodeStatus(a, now) {   // state class + plain-language status line for one agent at time `now`
  if (a.human) return humanStatus(now);
  const s = agentState(a, now), ups = UP[a.id] || [];
  const pendingUp = ups.filter(u => agentState(A[u], now) !== "done").map(u => A[u].name);
  if (s === "running") { const k = runningStage(a, now); return ["running", `running · ${k} · ${clk(now - S[k].start)}`]; }
  if (s === "done") { const st = a.stages.map(k => S[k]).filter(x => x.start); const dur = st.reduce((n, x) => n + ((x.end || now) - x.start), 0);
    return ["done", D.have_timings ? `done · ${clk(dur)}` : "done"]; }
  if (s === "failed") return ["failed", `failed · ${a.stages.find(k => stageState(k, now) === "failed")}`];
  if (pendingUp.length) return [s, `waiting for ${pendingUp.join(" + ")}`];
  return [s === "idle" ? "idle" : "ready", ups.length ? "handover received · starting" : "queued"];
}
function renderCanvas(now, animate) {
  D.agents.forEach(a => { const [cls, txt] = nodeStatus(a, now), el = nodes[a.id];
    el.className = "node s-" + cls + (a.human ? " human" : ""); el.querySelector(".badge").innerHTML = ICON[cls] || ""; el.querySelector(".stt").textContent = txt; });
  edges.forEach(x => {
    const sent = x.e.at && now >= x.e.at, st = A[x.e.t].human ? (humanStatus(now)[0] === "await" ? "running" : humanStatus(now)[0]) : agentState(A[x.e.t], now),
          active = sent && (st === "running" || st === "idle");
    const pending = !sent && agentState(A[x.e.f], now) === "running";   // sender working → handover coming
    x.p.setAttribute("class", "edge" + (active ? " active" : sent ? " done" : pending ? " pending" : "")); x.g.style.display = sent ? "" : "none";
    if (active && animate) { const pt = x.p.getPointAtLength(((performance.now() / 1400) % 1) * x.len);
      x.pk.setAttribute("cx", pt.x); x.pk.setAttribute("cy", pt.y); x.pk.style.display = ""; } else x.pk.style.display = "none";
  });
}

/* now bar: who is running, who is waiting for a handover, overall progress */
const nowEl = document.getElementById("now");
function renderNow(now) {
  const keys = Object.keys(S), total = keys.length, done = keys.filter(k => stageState(k, now) === "done").length;
  const AG = D.agents.filter(a => !a.human);
  const running = AG.filter(a => agentState(a, now) === "running");
  const failed = AG.filter(a => agentState(a, now) === "failed");
  const waiting = AG.filter(a => ["waiting", "idle"].includes(agentState(a, now)));
  const started = SV.length && now >= T0;
  const span = (k, v, cls) => `<span><span class="k">${k}</span><span class="v ${cls || ""}">${v}</span></span>`;
  const esc = t => String(t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  let html;
  if (failed.length) html = span("Status", "stopped · " + esc(failed.map(a => a.name).join(", ")) + " failed", "fail");
  else if (!started) html = span("Status", D.has || SV.length ? "ready to replay" : "waiting for the run to start");
  else if (done === total && !running.length) html = (RV.pending > 0
      ? span("Status", `engine finished · ${RV.pending.toLocaleString("en-US")} lines awaiting approval`, "run")
      : span("Status", RV.total ? "run complete · review done" : "run complete", "ok"))
    + span("Stages", `${done} / ${total}`) + (D.have_timings ? span("Elapsed", clk(T1 - T0)) : "");
  else html = span("Now running", running.length ? esc(running.map(a => `${a.name} (${runningStage(a, now)})`).join(" · ")) : "handing over…", "run")
    + span("Waiting", waiting.length ? esc(waiting.map(a => a.name).join(", ")) : "—")
    + span("Stages", `${done} / ${total}`) + (D.have_timings ? span("Elapsed", clk(now - T0)) : "");
  nowEl.innerHTML = html + `<span class="bar"><i style="width:${Math.round(done / Math.max(1, total) * 100)}%"></i></span>`;
}

/* fit the canvas to the panel width (scale down, never up) so every node, incl. the Approver, is visible */
const stageEl = document.querySelector(".stage");
function fit() { const w = stageEl.clientWidth, sc = Math.min(1, (w - 2) / 1440);
  canvas.style.transform = `scale(${sc})`; canvas.style.left = Math.max(0, (w - 1440 * sc) / 2) + "px";
  stageEl.style.height = Math.ceil(480 * sc) + "px"; }
fit(); window.addEventListener("resize", fit);
if (!D.have_timings) document.getElementById("logbody").classList.add("nots");

/* KPIs */
const kbox = document.getElementById("kpis");
const kels = D.kpis.map(k => { const d = document.createElement("div"); d.className = "kpi"; const l = document.createElement("div"); l.className = "l"; l.textContent = k.k;
  const v = document.createElement("div"); v.className = "v"; d.append(l, v); kbox.appendChild(d); return { k, v }; });
function renderKpis(now) { kels.forEach(({ k, v }) => v.textContent = (D.has && (!k.at || now >= k.at)) ? k.v : "—"); }

/* run log */
const logbody = document.getElementById("logbody"), filters = document.getElementById("filters");
let filter = "all", shown = -1;
[["all", "All"], ...D.agents.map(a => [a.id, a.name])].forEach(([id, label]) => {
  const b = document.createElement("button"); b.type = "button"; b.className = "filter"; b.textContent = label; b.setAttribute("aria-pressed", id === "all");
  b.onclick = () => { filter = id; filters.querySelectorAll(".filter").forEach(x => x.setAttribute("aria-pressed", x === b)); shown = -1; renderLog(cur, false); };
  filters.appendChild(b);
});
function renderLog(now, anim) {
  const vis = D.events.filter(e => e.ts <= now && (filter === "all" || e.a === filter));
  if (vis.length === shown) return;
  const prev = shown; logbody.innerHTML = "";
  vis.forEach((e, i) => {
    const ln = document.createElement("div"); ln.className = "ln" + (anim && prev >= 0 && i >= prev ? " new" : "");
    const c = (cls, t) => { const s = document.createElement("span"); s.className = cls; s.textContent = t; return s; };
    ln.append(c("ts", gst(e.ts)), c("ag", A[e.a].g + " " + A[e.a].name), c("lv " + e.lv, LV[e.lv]), c("tx", e.tx)); logbody.appendChild(ln);
  });
  if (D.live) { const k = document.createElement("span"); k.className = "cursor"; logbody.appendChild(k); }
  if (!vis.length && !D.live) { const m = document.createElement("div"); m.className = "empty"; m.textContent = "No stage activity recorded for this run."; logbody.appendChild(m); }
  shown = vis.length; logbody.scrollTop = logbody.scrollHeight;
}

/* engine */
let cur = T1 + 1, playing = false, t0p = 0, speed = 1; const SPAN = 42000;
function render(now, anim) {
  document.getElementById("clock").innerHTML = D.have_timings ? `engine time <b>${gst(Math.min(now, T1))}</b> GST` : "stage timings unavailable";
  renderCanvas(now, anim || D.live); renderNow(now); renderKpis(now); renderLog(now, anim);
}
function tick() {
  if (!playing) return; const f = ((performance.now() - t0p) * speed) / SPAN; cur = T0 - 1 + f * (T1 - T0 + 2);
  if (f >= 1) { playing = false; cur = T1 + 1; document.getElementById("play").textContent = "Replay run"; }
  render(cur, true); if (playing) requestAnimationFrame(tick);
}
document.getElementById("play").onclick = () => {
  const b = document.getElementById("play");
  if (playing) { playing = false; cur = T1 + 1; b.textContent = "Replay run"; shown = -1; render(cur, false); return; }
  playing = true; t0p = performance.now(); shown = 0; logbody.innerHTML = ""; b.textContent = "Skip to result"; requestAnimationFrame(tick);
};
document.getElementById("speed").onclick = e => { const el = playing ? (performance.now() - t0p) * speed : 0;
  speed = speed === 1 ? 3 : 1; e.target.textContent = "Speed " + speed + "×"; if (playing) t0p = performance.now() - el / speed; };
render(cur, false);
if (D.live) {   // live: advance the engine clock between app refreshes so timers tick
  const p0 = performance.now();
  const loop = () => { const now = D.now + (performance.now() - p0); renderCanvas(now, true); renderNow(now); requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
}
</script></body></html>
"""
