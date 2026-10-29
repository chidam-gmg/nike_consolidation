"""Agent view — a visual "team at work" layer over the deterministic pipeline (business 2026-10-29).

Each agent is one or more REAL job tasks: its status and timings come from the Databricks job run, its messages
from the run's own output tables (run_stats / replen_stats). Nothing here decides or changes a recommendation —
the engine stays rule-based and auditable. Rendered as one self-contained HTML component (CSS/JS replay inside
the iframe, so replaying never reruns the Streamlit script).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import streamlit.components.v1 as components

GST = timezone(timedelta(hours=4))

# col/row = position on the board (3 rows; the two lanes split after Guard and meet at Size Curator)
AGENTS = [
    dict(id="scout", name="Scout", role="Data readiness", code="AG-01", tasks=["config", "effective_stock"], col=0, row=1,
         doing="Checking the ATP feed, store list and in-transit…"),
    dict(id="analyst", name="Analyst", role="Demand & cover", code="AG-02", tasks=["classify"], col=1, row=1,
         doing="Measuring rate-of-sale and cover for every size…"),
    dict(id="guard", name="Guard", role="Rules & blocks", code="AG-03", tasks=["pools"], col=2, row=1,
         doing="Applying APO, season, move-back and DC-first rules…"),
    dict(id="matchmaker", name="Matchmaker", role="Store to store", code="AG-04", tasks=["match"], col=3, row=0,
         doing="Pairing stores with surplus to stores that need stock…"),
    dict(id="replenisher", name="Replenisher", role="DC to store", code="AG-05", tasks=["replen_demand", "replen_ration"],
         col=3, row=2, doing="Working out store needs and rationing DC stock…"),
    dict(id="curator", name="Size Curator", role="Full / cut size", code="AG-06", tasks=["reshape", "receiver_guard"],
         col=4, row=1, doing="Making sure every receiving store ends with a full size run…"),
    dict(id="reviewer", name="Reviewer", role="Final picks", code="AG-07", tasks=["score", "replen_output"], col=5, row=1,
         doing="Scoring and finalising the recommendations…"),
    dict(id="auditor", name="Auditor", role="Books closed", code="AG-08", tasks=["archive", "replen_history"], col=6, row=1,
         doing="Reconciling every stock line and archiving the run…"),
]
EDGES = [("scout", "analyst"), ("analyst", "guard"), ("guard", "matchmaker"), ("guard", "replenisher"),
         ("matchmaker", "curator"), ("replenisher", "curator"), ("curator", "reviewer"), ("reviewer", "auditor")]


def _num(v):
    try:
        f = float(v)
        return None if f != f else f          # NaN -> None
    except (TypeError, ValueError):
        return None


def _n(v, dflt="—"):
    f = _num(v)
    return dflt if f is None else f"{f:,.0f}"


def _aed(v):
    f = _num(v)
    if f is None:
        return "—"
    return f"AED {f / 1e6:,.1f}M" if f >= 1e6 else f"AED {f / 1e3:,.0f}K" if f >= 1e3 else f"AED {f:,.0f}"


def _done_messages(m: dict) -> dict:
    """What each agent reports when it finishes — numbers straight from the run's outputs."""
    s, r = m.get("stats") or {}, m.get("replen") or {}
    scope = f"{s.get('country_scope') or 'ALL'} · {s.get('category_scope') or 'ALL'} categories"
    rec = str(s.get("partition_reconciles", "")).lower() in ("true", "1")
    msgs = {
        "scout": [f"Run scope: {scope}",
                  f"Loaded {_n(s.get('universe_lines'))} store × size stock lines (ATP + in-transit)"],
        "analyst": [f"{_n(s.get('oos_receiver_candidates'))} sizes are selling but out of stock",
                    f"{_n(s.get('aplus_curve_candidates'))} display gaps found at A+ stores"],
        "guard": [f"Blocked receivers — APO {_n(s.get('apo_blocked_candidates'))} · season "
                  f"{_n(s.get('season_blocked_candidates'))} · moved out <30d {_n(s.get('moveback_blocked_candidates'))}",
                  f"Sent {_n(s.get('oos_deferred_to_replen'))} out-of-stock sizes to the DC first"],
        "matchmaker": [f"Paired {_n(s.get('donor_stores'))} donor stores with {_n(s.get('receiver_stores'))} receiving stores"],
        "replenisher": [f"Rationed DC stock across {_n(r.get('stores'))} stores and {_n(r.get('skus'))} SKUs",
                        f"{_n(r.get('no_dc_stock_lines'))} store needs had no DC stock"],
        "curator": [f"Protected {_n(s.get('held_step8'))} donor size runs from breaking",
                    f"Held {_n(s.get('receiver_cut_blocked_moves'))} store moves + {_n(r.get('held_receiver_cut_lines'))} "
                    f"DC lines that would leave a store cut-size",
                    f"Topped up {_n(r.get('fullsize_topup_units'))} sizes from the DC to make stores full-size"],
        "reviewer": [f"{_n(s.get('recommended_moves'))} store moves · {_n(s.get('total_units'))} units",
                     f"{_n(r.get('replen_lines'))} DC lines · {_n(r.get('replen_units'))} units · {_aed(r.get('fill_value_aed'))}"],
        "auditor": [(f"Every stock line accounted for: moved {_n(s.get('moved_donor_lines'))} + held {_n(s.get('held_lines'))}"
                     f" + kept {_n(s.get('not_moved_lines'))} = {_n(s.get('universe_lines'))} " + ("[OK]" if rec else "[MISMATCH]")),
                    "Run archived to history"],
    }
    return msgs


def _payload(job_run: dict | None, metrics: dict, live: bool) -> dict:
    tasks = {t["key"]: t for t in (job_run or {}).get("tasks", [])}
    have_metrics = bool((metrics or {}).get("stats"))
    done_msgs = _done_messages(metrics or {}) if have_metrics else {}
    agents = []
    for a in AGENTS:
        ts = [tasks[k] for k in a["tasks"] if k in tasks]
        starts = [t["start"] for t in ts if t["start"]]
        ends = [t["end"] for t in ts if t["end"]]
        results = [t["result"] for t in ts]
        if any(x in ("FAILED", "TIMEDOUT", "CANCELED", "UPSTREAM_FAILED") for x in results):
            status = "failed"
        elif ts and all(x in ("SUCCESS", "SKIPPED") for x in results):
            status = "done"
        elif any(t["life"] in ("RUNNING", "PENDING", "TERMINATING") for t in ts) or (starts and not ends):
            status = "working"
        elif job_run is None and have_metrics:
            status = "done"
        else:
            status = "waiting"
        agents.append({k: a[k] for k in ("id", "name", "role", "code", "col", "row", "doing")} | {
            "status": status,
            "start": min(starts) if starts else None,
            "end": max(ends) if (ends and status == "done") else None,
            "done_msgs": done_msgs.get(a["id"], []),
        })
    s, r = (metrics or {}).get("stats") or {}, (metrics or {}).get("replen") or {}
    kpis = [
        {"label": "Store moves", "value": _num(s.get("recommended_moves"))},
        {"label": "Units moved", "value": _num(s.get("total_units"))},
        {"label": "DC lines", "value": _num(r.get("replen_lines"))},
        {"label": "DC units", "value": _num(r.get("replen_units"))},
        {"label": "Held for review", "value": _num(s.get("held_lines"))},
    ]
    jr = job_run or {}
    return {
        "live": live, "agents": agents, "edges": EDGES, "kpis": kpis,
        "t0": jr.get("start") or None, "t1": jr.get("end") or None,
        "who": s.get("triggered_by") or jr.get("triggered_by") or "",
        "when": s.get("run_ts") or "",
        "scope": f"{s.get('country_scope') or jr.get('country') or 'ALL'} · {s.get('category_scope') or jr.get('category') or 'ALL'}",
        "run_id": jr.get("engine_run_id") or "", "job_run_id": jr.get("job_run_id") or "",
        "have_timings": bool(jr.get("tasks")),
    }


def render(job_run: dict | None, metrics: dict, live: bool = False, height: int = 760):
    """Draw the agent board. live=True: current statuses of a running job (no replay)."""
    data = json.dumps(_payload(job_run, metrics, live)).replace("</", "<\\/")
    components.html(_HTML.replace("__DATA__", data), height=height, scrolling=True)


_HTML = r"""
<!doctype html><html><head><meta charset="utf-8">
<style>
  :root { --ink:#20274C; --cobalt:#2658F2; --grid:#EEF2F4; --grid2:#E3E9EC; --line:#C9D3DA; --muted:#7A8499;
          --ok:#1E9E6A; --fail:#B15940; --paper:#FBFCFC; }
  * { box-sizing:border-box; }
  body { margin:0; color:var(--ink); background:#fff;
         font-family:ui-monospace,"SFMono-Regular","JetBrains Mono",Menlo,Consolas,monospace; font-size:12px; }
  .hdr { display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; padding:2px 0 8px; border-bottom:1px solid var(--line); }
  .hdr .t { font-size:13px; font-weight:700; letter-spacing:.12em; }
  .hdr .m { color:var(--muted); font-size:11px; letter-spacing:.04em; }
  .hdr .grow { flex:1; }
  .live { color:var(--cobalt); font-weight:700; letter-spacing:.1em; }
  .live::before { content:''; display:inline-block; width:7px; height:7px; border-radius:50%; background:var(--cobalt);
                  margin-right:6px; animation:blink 1s steps(2) infinite; }
  @keyframes blink { 50% { opacity:.15; } }
  button { font:inherit; font-size:11px; letter-spacing:.08em; border:1px solid var(--ink); background:#fff; color:var(--ink);
           padding:5px 10px; cursor:pointer; text-transform:uppercase; }
  button:hover:not(:disabled) { background:var(--ink); color:#fff; }
  button:disabled { opacity:.35; cursor:default; }
  .readouts { display:grid; grid-template-columns:repeat(5,minmax(120px,1fr)); border:1px solid var(--line); margin:10px 0; }
  .ro { padding:8px 12px; border-right:1px solid var(--line); background:var(--paper); }
  .ro:last-child { border-right:0; }
  .ro .l { font-size:10px; letter-spacing:.12em; color:var(--muted); text-transform:uppercase; }
  .ro .v { font-size:22px; font-weight:600; font-variant-numeric:tabular-nums; margin-top:3px; }
  .wrap { display:flex; gap:10px; align-items:stretch; }
  .boardbox { flex:1 1 auto; overflow-x:auto; border:1px solid var(--line);
              background-color:var(--paper);
              background-image:linear-gradient(var(--grid2) 1px,transparent 1px),linear-gradient(90deg,var(--grid2) 1px,transparent 1px),
                               linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
              background-size:100px 100px,100px 100px,20px 20px,20px 20px; }
  .board { position:relative; width:1190px; height:420px; }
  svg { position:absolute; left:0; top:0; width:1190px; height:420px; pointer-events:none; overflow:visible; }
  .edge { stroke:#AEB9C3; stroke-width:1.2; fill:none; }
  .edge.on { stroke:var(--cobalt); stroke-width:1.6; stroke-dasharray:4 4; animation:flow .8s linear infinite; }
  .edge.done { stroke:var(--ink); }
  @keyframes flow { to { stroke-dashoffset:-8; } }
  .mod { position:absolute; width:150px; height:118px; background:#fff; border:1px solid var(--line); transition:border-color .3s, box-shadow .3s; }
  .mod .bar { display:flex; justify-content:space-between; align-items:center; padding:5px 8px; border-bottom:1px solid var(--line);
              font-size:10px; letter-spacing:.1em; color:var(--muted); }
  .led { width:8px; height:8px; border-radius:50%; background:#C4CCD3; }
  .mod .nm { padding:8px 8px 0; font-size:13px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; }
  .mod .rl { padding:2px 8px 0; font-size:10px; color:var(--muted); letter-spacing:.04em; text-transform:uppercase; }
  .mod .stt { position:absolute; left:8px; bottom:22px; font-size:10px; letter-spacing:.12em; }
  .mod .tm { position:absolute; left:8px; bottom:7px; font-size:10px; color:var(--muted); font-variant-numeric:tabular-nums; }
  .mod.waiting { opacity:.6; }
  .mod.waiting .stt { color:var(--muted); }
  .mod.working { border-color:var(--cobalt); box-shadow:0 0 0 3px rgba(38,88,242,.10); }
  .mod.working .led { background:var(--cobalt); animation:blink .8s steps(2) infinite; }
  .mod.working .stt { color:var(--cobalt); }
  .mod.done { border-color:var(--ink); }
  .mod.done .led { background:var(--ok); }
  .mod.done .stt { color:var(--ok); }
  .mod.failed { border-color:var(--fail); }
  .mod.failed .led { background:var(--fail); }
  .mod.failed .stt { color:var(--fail); }
  .console { flex:0 0 400px; border:1px solid var(--ink); background:#14182E; color:#C8D0E6; display:flex; flex-direction:column; height:420px; }
  .console .h { padding:6px 10px; font-size:10px; letter-spacing:.14em; color:#7F8AB0; border-bottom:1px solid #2A3156; display:flex; justify-content:space-between; }
  .lines { overflow-y:auto; padding:8px 10px; flex:1; font-size:11.5px; line-height:1.55; }
  .ln { white-space:pre-wrap; animation:fadein .25s ease; }
  .ln .ts { color:#6C779E; } .ln .ag { color:#8FB0FF; font-weight:700; } .ln.ok .tx { color:#E7ECF7; } .ln.work .tx { color:#9AA4C2; }
  .ln.err .tx { color:#F2A07B; }
  .cursor { display:inline-block; width:7px; height:13px; background:#8FB0FF; vertical-align:-2px; animation:blink 1s steps(2) infinite; }
  @keyframes fadein { from { opacity:0; } to { opacity:1; } }
  .ask { margin-top:10px; display:flex; gap:0; border:1px solid var(--line); }
  .ask span { padding:7px 10px; color:var(--muted); border-right:1px solid var(--line); background:var(--paper); }
  .ask input { flex:1; border:0; padding:7px 10px; font:inherit; background:#fff; color:var(--muted); }
  .ask button { border:0; border-left:1px solid var(--line); }
  .foot { margin-top:8px; font-size:10.5px; color:var(--muted); letter-spacing:.02em; }
  @media (max-width:1100px) { .wrap { flex-direction:column; } .console { flex-basis:auto; width:100%; height:280px; } }
</style></head><body>
<div class="hdr">
  <div class="t">STOCK MOVEMENT · AGENT LAB</div>
  <div class="m" id="meta"></div><div class="live" id="live" style="display:none">LIVE</div>
  <div class="grow"></div>
  <button id="replay">Replay run</button><button id="final">Show result</button>
</div>
<div class="readouts" id="kpis"></div>
<div class="wrap">
  <div class="boardbox"><div class="board" id="board"><svg id="svg" width="1190" height="420" viewBox="0 0 1190 420"></svg></div></div>
  <div class="console"><div class="h"><span>RUN LOG</span><span id="clock">--:--:-- GST</span></div><div class="lines" id="items"></div></div>
</div>
<div class="ask"><span>QUERY&gt;</span><input disabled placeholder="ask the team, e.g. why did store 1122 get size M?  [coming soon]"><button disabled>Run</button></div>
<div class="foot">Each agent is a stage of the deterministic engine. Decisions are rule-based and auditable; this view replays the engine's real timings and outputs.</div>
<script>
const D = __DATA__;
const COLW = 170, ROWH = 140, X0 = 14, Y0 = 0, CW = 150, CH = 118;
const board = document.getElementById('board'), svg = document.getElementById('svg');
const byId = {}; D.agents.forEach(a => byId[a.id] = a);
const pos = a => ({ x: X0 + a.col * COLW, y: Y0 + 12 + a.row * ROWH });
const gst = ms => ms ? new Date(ms + 4 * 3600e3).toISOString().substr(11, 8) : '--:--:--';
const fmt = v => (v === null || v === undefined) ? '—' : Math.round(v).toLocaleString('en-US');

document.getElementById('meta').textContent =
  'SCOPE ' + D.scope + (D.who ? '  ·  BY ' + D.who : '') + (D.run_id ? '  ·  RUN ' + D.run_id : '');
if (D.live) { document.getElementById('live').style.display = ''; document.getElementById('replay').style.display = 'none';
              document.getElementById('final').style.display = 'none'; }
if (!D.have_timings) document.getElementById('replay').disabled = true;

const cards = {};
D.agents.forEach(a => {
  const p = pos(a), el = document.createElement('div');
  el.className = 'mod waiting'; el.style.left = p.x + 'px'; el.style.top = p.y + 'px';
  const bar = document.createElement('div'); bar.className = 'bar';
  const code = document.createElement('span'); code.textContent = a.code; const led = document.createElement('span'); led.className = 'led';
  bar.append(code, led);
  const nm = document.createElement('div'); nm.className = 'nm'; nm.textContent = a.name;
  const rl = document.createElement('div'); rl.className = 'rl'; rl.textContent = a.role;
  const st = document.createElement('div'); st.className = 'stt';
  const tm = document.createElement('div'); tm.className = 'tm';
  el.append(bar, nm, rl, st, tm); board.appendChild(el); cards[a.id] = { el, st, tm };
});
const edges = D.edges.map(([f, t]) => {
  const a = pos(byId[f]), b = pos(byId[t]);
  const x1 = a.x + CW, y1 = a.y + CH / 2, x2 = b.x, y2 = b.y + CH / 2, mx = (x1 + x2) / 2;
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', `M${x1},${y1} L${mx},${y1} L${mx},${y2} L${x2},${y2}`);
  path.setAttribute('class', 'edge'); svg.appendChild(path);
  return { f, t, path };
});
function setState(states) {
  D.agents.forEach(a => {
    const s = states[a.id], c = cards[a.id];
    c.el.className = 'mod ' + s;
    c.st.textContent = s === 'working' ? 'STATUS: RUNNING' : s === 'done' ? 'STATUS: COMPLETE' : s === 'failed' ? 'STATUS: FAILED' : 'STATUS: STANDBY';
    c.tm.textContent = s === 'done' && a.start && a.end ? `${gst(a.start)} > ${gst(a.end)}` :
                       s === 'working' && a.start ? `T+ since ${gst(a.start)}` : '';
  });
  edges.forEach(e => {
    const sf = states[e.f], st = states[e.t];
    e.path.setAttribute('class', 'edge' + (sf === 'done' && st === 'working' ? ' on' : sf === 'done' && st === 'done' ? ' done' : ''));
  });
}
const items = document.getElementById('items'), clock = document.getElementById('clock');
function log(a, text, ms, kind) {
  const ln = document.createElement('div'); ln.className = 'ln ' + kind;
  const ts = document.createElement('span'); ts.className = 'ts'; ts.textContent = '[' + gst(ms) + '] ';
  const ag = document.createElement('span'); ag.className = 'ag'; ag.textContent = a.code + ' ' + a.name.toUpperCase();
  const tx = document.createElement('span'); tx.className = 'tx'; tx.textContent = ' > ' + text;
  ln.append(ts, ag, tx); items.appendChild(ln); items.scrollTop = items.scrollHeight;
  if (ms) clock.textContent = gst(ms) + ' GST';
}
function cursor() { const c = document.createElement('span'); c.className = 'cursor'; items.appendChild(c); }
function kpis(show) {
  const box = document.getElementById('kpis'); box.innerHTML = '';
  D.kpis.forEach(k => {
    const el = document.createElement('div'); el.className = 'ro';
    const l = document.createElement('div'); l.className = 'l'; l.textContent = k.label;
    const v = document.createElement('div'); v.className = 'v'; v.textContent = show ? fmt(k.value) : '—';
    el.append(l, v); box.appendChild(el); k._el = v;
  });
}
function countUp(ms) {
  D.kpis.forEach(k => {
    if (k.value === null || k.value === undefined) return;
    const t0 = performance.now();
    const step = now => { const f = Math.min(1, (now - t0) / ms); k._el.textContent = fmt(k.value * (1 - Math.pow(1 - f, 3)));
                          if (f < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  });
}
let timers = [];
function finalState() {
  timers.forEach(clearTimeout); timers = [];
  const states = {}; D.agents.forEach(a => states[a.id] = a.status); setState(states);
  items.innerHTML = '';
  const ev = [];   // one time-ordered log: stage starts + stage results
  D.agents.forEach((a, i) => {
    if (a.status !== 'waiting') ev.push([a.start || 0, i, 0, a, a.doing, 'work']);
    if (a.status === 'done') a.done_msgs.forEach((m, j) => ev.push([a.end || 0, i, 1 + j, a, m, 'ok']));
    if (a.status === 'failed') ev.push([a.end || a.start || 0, i, 9, a, 'stage failed; see the run in Databricks', 'err']);
  });
  ev.sort((x, y) => x[0] - y[0] || x[1] - y[1] || x[2] - y[2]).forEach(e => log(e[3], e[4], e[0], e[5]));
  if (D.live) cursor();
  kpis(!D.live || D.agents.every(a => a.status === 'done'));
}
function replay() {
  timers.forEach(clearTimeout); timers = []; items.innerHTML = ''; kpis(false);
  const st = {}; D.agents.forEach(a => st[a.id] = 'waiting'); setState(st);
  const valid = D.agents.filter(a => a.start && a.end);
  if (!valid.length) { finalState(); return; }
  const t0 = Math.min(...valid.map(a => a.start)), t1 = Math.max(...valid.map(a => a.end));
  const SPAN = 32000, sc = ms => 400 + (ms - t0) / Math.max(1, t1 - t0) * SPAN;
  valid.forEach(a => {
    timers.push(setTimeout(() => { st[a.id] = 'working'; setState(st); log(a, a.doing, a.start, 'work'); }, sc(a.start)));
    timers.push(setTimeout(() => { st[a.id] = a.status === 'failed' ? 'failed' : 'done'; setState(st);
                                   a.done_msgs.forEach(m => log(a, m, a.end, 'ok')); }, sc(a.end)));
  });
  timers.push(setTimeout(() => countUp(1500), sc(byId.reviewer.end || t1)));
}
document.getElementById('replay').onclick = replay;
document.getElementById('final').onclick = finalState;
finalState();
</script></body></html>
"""
