"""Live monitor — status and history of every pipeline behind the app: the daily stock-movement run (consolidation +
DC replenishment), the forecast & allocation run and the new-article forecast. Read-only; refreshes every minute."""
from __future__ import annotations

import html
from datetime import datetime, timezone, timedelta

import pandas as pd
import streamlit as st

from brief_view import TOKENS

GST = timezone(timedelta(hours=4))
DAILY_AT = (10, 30)                 # nike_stock_movement_pipeline schedule (GST)
OVERDUE_AFTER = timedelta(minutes=45)
STATUS = {"ok": "Succeeded", "run": "Running", "fail": "Failed", "late": "Overdue", "idle": "On demand",
          "cancel": "Cancelled"}
TASK_LABELS = {"config": "Validate config", "effective_stock": "Effective stock", "classify": "ROS / cover",
               "pools": "Pools & guards", "match": "Match & size", "reshape": "Size curve",
               "receiver_guard": "Receiver guard", "score": "Score & output", "archive": "Archive",
               "replen_demand": "Replen demand", "replen_ration": "Replen ration", "replen_output": "Replen output",
               "replen_history": "Replen archive"}


def _css(t: dict) -> str:
    return f"""<style>
    .mn-eyebrow {{ font-size:11.5px; letter-spacing:.12em; text-transform:uppercase; font-weight:700; color:{t['dim']}; }}
    .mn-h1 {{ font:600 30px/1.2 Georgia, serif; color:{t['ink']}; margin:4px 0 4px; }}
    .mn-lede {{ font-size:15px; color:{t['dim']}; margin:0 0 10px; }}
    .mn-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:6px 0 14px; }}
    .mn-card {{ border:1px solid {t['line']}; border-radius:12px; background:{t['card']}; padding:14px 18px; min-width:0; }}
    .mn-top {{ display:flex; justify-content:space-between; align-items:center; gap:8px; }}
    .mn-name {{ font-size:16px; font-weight:700; color:{t['ink']}; }}
    .mn-cad {{ font-size:12.5px; color:{t['dim']}; margin-bottom:8px; }}
    .mn-pill {{ font-size:11.5px; font-weight:700; border-radius:999px; padding:2px 10px; white-space:nowrap; }}
    .mn-pill::before {{ content:"●"; margin-right:5px; }}
    .mn-ok {{ background:{t['ok_soft']}; color:{t['ok']}; }} .mn-run {{ background:{t['acc_soft']}; color:{t['accent']}; }}
    .mn-fail, .mn-late {{ background:{t['warm_soft']}; color:{t['warm']}; }}
    .mn-idle, .mn-cancel {{ background:{t['soft']}; color:{t['dim']}; }}
    .mn-run::before {{ animation:mnblink 1.2s infinite; }}
    @keyframes mnblink {{ 50% {{ opacity:.2; }} }}
    .mn-row {{ display:flex; justify-content:space-between; gap:10px; font-size:13.5px; color:{t['dim']};
              border-top:1px solid {t['line']}; padding:5px 0; }}
    .mn-row b {{ color:{t['ink']}; font-weight:600; text-align:right; font-variant-numeric:tabular-nums; }}
    .mn-h2 {{ font:600 17px/1.3 Georgia, serif; color:{t['ink']}; margin:10px 0 4px; }}
    .mn-days {{ display:flex; gap:6px; flex-wrap:wrap; margin:4px 0 2px; }}
    .mn-day {{ width:34px; text-align:center; font-size:11px; color:{t['dim']}; }}
    .mn-day i {{ display:block; height:22px; border-radius:5px; margin-bottom:3px; }}
    .mn-day .y {{ background:{t['ok']}; }} .mn-day .n {{ background:{t['warm']}; }} .mn-day .u {{ background:{t['line']}; }}
    .mn-bar {{ display:grid; grid-template-columns:150px 1fr 64px; gap:10px; align-items:center; font-size:13px;
              color:{t['ink']}; padding:2px 0; }}
    .mn-bar span {{ display:block; height:12px; border-radius:3px; background:{t['accent']}; min-width:2px; }}
    .mn-bar span.f {{ background:{t['warm']}; }} .mn-bar span.r {{ background:{t['acc_soft']}; }}
    .mn-bar em {{ font-style:normal; color:{t['dim']}; text-align:right; font-variant-numeric:tabular-nums; }}
    .mn-note {{ font-size:12.5px; color:{t['dim']}; }}
    @media (max-width: 900px) {{ .mn-grid {{ grid-template-columns:1fr; }} }}
    </style>"""


def _ts(x):
    """Any of: ISO string, pandas Timestamp, epoch ms, or engine run_id (20261105T063020Z-xxxx) -> aware UTC datetime."""
    if x is None or (isinstance(x, float) and pd.isna(x)) or str(x) in ("", "None", "nan", "NaT"):
        return None
    if isinstance(x, (int, float)):
        return datetime.fromtimestamp(x / 1000, timezone.utc) if x else None
    s = str(x)
    if len(s) >= 16 and s[8] == "T" and s[15] == "Z":
        try:
            return datetime.strptime(s[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        d = pd.Timestamp(s)
        return (d.tz_localize("UTC") if d.tzinfo is None else d.tz_convert("UTC")).to_pydatetime()
    except Exception:
        return None


def _gst(d, fmt="%d %b, %H:%M") -> str:
    return d.astimezone(GST).strftime(fmt) + " GST" if d else "—"


def _ago(d, now) -> str:
    if not d:
        return ""
    m = int((now - d).total_seconds() // 60)
    if m < 1:
        return "just now"
    if m < 60:
        return f"{m} min ago"
    return f"{m // 60} h ago" if m < 48 * 60 else f"{m // 1440} days ago"


def _dur(sec) -> str:
    if sec is None or pd.isna(sec):
        return "—"
    if sec < 10:
        return f"{sec:.1f} s"
    sec = int(sec)
    return f"{sec // 60} min {sec % 60:02d} s" if sec >= 60 else f"{sec} s"


def _n(x) -> str:
    try:
        return f"{int(float(x)):,}"
    except (TypeError, ValueError):
        return "—"


def _job_status(r: dict) -> str:
    if r.get("life") in ("RUNNING", "PENDING", "QUEUED", "BLOCKED", "WAITING_FOR_RETRY", "TERMINATING"):
        return "run"
    return {"SUCCESS": "ok", "CANCELED": "cancel"}.get(r.get("result"), "fail")


def _who(x) -> str:
    x = str(x or "").strip()
    return "Scheduled" if x in ("", "app", "manual", "None", "nan") else x


def _card(name, cadence, status, rows) -> str:
    body = "".join(f"<div class='mn-row'><span>{html.escape(k)}</span><b>{html.escape(str(v))}</b></div>" for k, v in rows)
    return (f"<div class='mn-card'><div class='mn-top'><div class='mn-name'>{html.escape(name)}</div>"
            f"<span class='mn-pill mn-{status}'>{STATUS[status]}</span></div>"
            f"<div class='mn-cad'>{html.escape(cadence)}</div>{body}</div>")


def _history(cons: pd.DataFrame, jobs: list) -> pd.DataFrame:
    """One row per stock-movement attempt: completed runs from history, plus failed/running attempts from the job API."""
    by_rid = {j["engine_run_id"]: j for j in jobs if j.get("engine_run_id")}
    unkeyed = [j for j in jobs if not j.get("engine_run_id")]     # scheduled runs: the notebook mints the RUN_ID itself
    rows, used = [], set()
    for _, r in cons.iterrows():
        rid = str(r["run_id"])
        j = by_rid.get(rid)
        if j is None and _ts(rid):                   # match on start time: the RUN_ID is stamped within minutes of start
            j = next((u for u in unkeyed if id(u) not in used and u["start"] and
                      timedelta(0) <= _ts(rid) - _ts(u["start"]) <= timedelta(minutes=10)), None)
        if j is not None:
            used.add(id(j))
        start = _ts(j["start"]) if j else _ts(rid)
        end = _ts(j["end"]) if j and j.get("end") else _ts(r.get("archived_at"))
        rows.append({"start": start, "status": _job_status(j) if j else "ok",
                     "secs": (end - start).total_seconds() if start and end else None,
                     "trigger": _who(r.get("triggered_by")), "scope": str(r.get("country_scope") or "ALL")
                     + ("" if str(r.get("category_scope") or "ALL") == "ALL" else f" · {r['category_scope']}"),
                     "moves": r.get("recommended_moves"), "units": r.get("total_units"), "held": r.get("held_lines"),
                     "replen": r.get("replen_lines"), "replen_units": r.get("replen_units"),
                     "data_as_of": r.get("as_of_date"), "run_id": rid, "url": j.get("url") if j else None})
    oldest = min((x["start"] for x in rows if x["start"]), default=None)
    for j in jobs:                                   # attempts that never reached history: failed / cancelled / in flight
        s = _job_status(j)
        start = _ts(j["start"])
        if id(j) in used or s == "ok" or (oldest and start and start < oldest):
            continue
        end = _ts(j.get("end"))
        rows.append({"start": start, "status": s, "secs": ((end or datetime.now(timezone.utc)) - start).total_seconds()
                     if start else None, "trigger": _who(j.get("triggered_by")),
                     "scope": j.get("country") or "ALL", "run_id": j.get("engine_run_id") or str(j["job_run_id"]),
                     "url": j.get("url")})
    h = pd.DataFrame(rows)
    return h.sort_values("start", ascending=False, na_position="last").reset_index(drop=True) if len(h) else h


def _daily_strip(h: pd.DataFrame, now: datetime, days: int = 14) -> str:
    """One tile per day: did the scheduled run complete that day?"""
    done = set()
    if len(h):
        sch = h[(h["trigger"] == "Scheduled") & (h["status"] == "ok")]
        done = {d.astimezone(GST).date() for d in sch["start"] if d}
    today = now.astimezone(GST)
    due_today = today >= today.replace(hour=DAILY_AT[0], minute=DAILY_AT[1], second=0) + OVERDUE_AFTER
    tiles = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).date()
        cls = "y" if d in done else ("u" if (i == 0 and not due_today) else "n")
        tip = "completed" if cls == "y" else ("not due yet" if cls == "u" else "no scheduled run")
        tiles.append(f"<div class='mn-day' title='{d:%d %b}: {tip}'><i class='{cls}'></i>{d:%d}<br>{d:%a}</div>")
    return "<div class='mn-days'>" + "".join(tiles) + "</div>"


def _bars(steps: list, total_label: str) -> str:
    """steps = [(label, seconds, state)] -> proportional horizontal bars."""
    if not steps:
        return ""
    mx = max(s for _, s, _ in steps) or 1
    out = []
    for lbl, s, state in steps:
        cls = {"fail": "f", "run": "r"}.get(state, "")
        out.append(f"<div class='mn-bar'><div>{html.escape(lbl)}</div>"
                   f"<div><span class='{cls}' style='width:{max(s / mx * 100, 0.5):.1f}%'></span></div><em>{_dur(s)}</em></div>")
    return "".join(out) + f"<div class='mn-note'>{html.escape(total_label)}</div>"


def _task_steps(j: dict) -> list:
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    steps = []
    for t in sorted(j.get("tasks", []), key=lambda t: t["start"] or 9e15):
        if not t["start"]:
            continue
        state = "run" if t["life"] in ("RUNNING", "PENDING", "QUEUED") else ("ok" if t["result"] in ("SUCCESS", None) else "fail")
        steps.append((TASK_LABELS.get(t["key"], t["key"]), ((t["end"] or now_ms) - t["start"]) / 1000, state))
    return steps


def _pack_row(pk, now) -> list:
    """Opening-pack run (22_new_launch_opening_pack), shown on the new-article card."""
    if pk is None or not len(pk):
        return []
    r = pk.iloc[0]
    t = _ts(r["run_id"])
    return [("Opening pack", f"{_n(r['pack_units'])} to stores · {_n(r['reserve_units'])} at DC · {_ago(t, now)}")]


def render(theme: str, data: dict, jobs: list, jobs_err: str | None):
    t = TOKENS[theme]
    now = datetime.now(timezone.utc)
    st.markdown(_css(t), unsafe_allow_html=True)
    st.markdown(f"<div class='mn-eyebrow'>Operations · refreshed {_gst(now, '%H:%M:%S')}</div>"
                "<div class='mn-h1'>Live monitor</div>"
                "<div class='mn-lede'>Every pipeline behind this app: when it last ran, whether it worked, and what it "
                "produced. Updates every minute.</div>", unsafe_allow_html=True)

    cons, fc, nl = data.get("consol", pd.DataFrame()), data.get("fc", pd.DataFrame()), data.get("nl", pd.DataFrame())
    h = _history(cons, jobs)

    # ---- status cards ----
    today = now.astimezone(GST)
    due = today.replace(hour=DAILY_AT[0], minute=DAILY_AT[1], second=0, microsecond=0)
    nxt = due if today < due else due + timedelta(days=1)
    last = h.iloc[0] if len(h) else None
    if last is None:
        s_status = "late"
    elif last["status"] in ("run", "fail", "cancel"):
        s_status = last["status"]
    else:
        sch_today = len(h) and any(d and d.astimezone(GST).date() == today.date()
                                   for d in h.loc[(h["trigger"] == "Scheduled") & (h["status"] == "ok"), "start"])
        s_status = "late" if (today > due + OVERDUE_AFTER and not sch_today) else "ok"
    ok = h[h["status"] == "ok"].iloc[0] if len(h) and (h["status"] == "ok").any() else None
    cards = [_card("Stock movement", "Store consolidation + DC replenishment · daily 10:30 GST", s_status, [
        ("Last run", f"{_gst(last['start'])} · {_ago(last['start'], now)}" if last is not None else "—"),
        ("Took", _dur(last["secs"]) if last is not None else "—"),
        ("Output", f"{_n(ok['moves'])} store moves · {_n(ok['replen'])} DC lines" if ok is not None else "—"),
        ("Next scheduled", _gst(nxt))])]

    if len(fc):
        f = fc.iloc[0]
        fstart = _ts(f.get("run_ts"))
        st_ = data.get("fc_stages", pd.DataFrame())
        fsec = ((pd.to_numeric(st_["end"]).max() - pd.to_numeric(st_["start"]).min()) / 1000) if len(st_) else None
        cards.append(_card("Forecast & allocation", "Weekly demand forecast + store/size allocation · on demand", "idle", [
            ("Last run", f"{_gst(fstart)} · {_ago(fstart, now)}"),
            ("Model time", _dur(fsec)),
            ("Accuracy", f"{float(f['error_champion']):.0%} error · P80 covers {float(f['p80_coverage']):.0%}"),
            ("Output", f"{_n(f.get('plan_lines_all'))} lines · {_n(f.get('plan_units_all'))} units")]))
    else:
        cards.append(_card("Forecast & allocation", "on demand", "idle", [("Last run", "no run found")]))

    if len(nl):
        nl = nl.assign(_t=nl["run_id"].map(_ts)).sort_values("_t", ascending=False)
        n0 = nl.iloc[0]
        bt = data.get("nl_bt", pd.DataFrame())
        bt = bt[bt["run_id"] == n0["run_id"]] if len(bt) else bt
        cards.append(_card("New-article forecast", "Spring 2027 launches, look-alikes + image attributes · on demand", "idle", [
            ("Last run", f"{_gst(n0['_t'])} · {_ago(n0['_t'], now)}"),
            ("Styles", f"{_n(n0['styles'])} · {_n(n0['with_image'])} with images"),
            ("Back-test", f"{float(bt.iloc[0]['wape']):.0%} error · {float(bt.iloc[0]['within_2x']):.0%} within 2×"
             if len(bt) else "—"),
            ("Output", f"{_n(n0['units'])} units over 13 weeks")] + _pack_row(data.get("nl_pack"), now)))
    st.markdown("<div class='mn-grid'>" + "".join(cards) + "</div>", unsafe_allow_html=True)

    # ---- in flight ----
    live = [j for j in jobs if _job_status(j) == "run"]
    for j in live:
        with st.container(border=True):
            st.markdown(f"<div class='mn-h2'>Running now · started {_gst(_ts(j['start']))} by "
                        f"{html.escape(_who(j.get('triggered_by')))}</div>", unsafe_allow_html=True)
            done = sum(1 for tk in j["tasks"] if tk["result"] in ("SUCCESS", "SKIPPED"))
            st.progress(max(done / max(len(j["tasks"]), 1), 0.02), text=f"{done} of {len(j['tasks'])} steps done")
            st.markdown(_bars(_task_steps(j), "Blue = finished · pale = in progress"), unsafe_allow_html=True)
            if j.get("url"):
                st.markdown(f"[Open in Databricks ↗]({j['url']})")

    # ---- daily reliability + run history ----
    st.markdown("<div class='mn-h2'>Scheduled run · last 14 days</div>", unsafe_allow_html=True)
    st.markdown(_daily_strip(h, now) + "<div class='mn-note'>Green = the 10:30 run completed · "
                "orange = no completed scheduled run that day</div>", unsafe_allow_html=True)

    st.markdown("<div class='mn-h2'>Stock movement · run history</div>", unsafe_allow_html=True)
    if len(h):
        v = pd.DataFrame({
            "Started": h["start"].map(lambda d: _gst(d, "%a %d %b, %H:%M")),
            "Status": h["status"].map(STATUS),
            "Triggered by": h["trigger"],
            "Scope": h["scope"],
            "Took": h["secs"].map(_dur),
            "Store moves": pd.to_numeric(h.get("moves"), errors="coerce"),
            "Units": pd.to_numeric(h.get("units"), errors="coerce"),
            "Held": pd.to_numeric(h.get("held"), errors="coerce"),
            "DC lines": pd.to_numeric(h.get("replen"), errors="coerce"),
            "Data as of": h.get("data_as_of"),
            "Run": h["run_id"],
            "Job": h["url"]})
        st.dataframe(v, hide_index=True, use_container_width=True, height=min(38 + 35 * len(v), 460),
                     column_config={c: st.column_config.NumberColumn(format="localized") for c in
                                    ("Store moves", "Units", "Held", "DC lines")} |
                     {"Job": st.column_config.LinkColumn(display_text="Open ↗", width="small"),
                      "Run": st.column_config.TextColumn(width="small")})
    else:
        st.info("No stock-movement runs recorded yet.")
    if jobs_err:
        st.caption("Showing completed runs only — failed or in-progress attempts appear here once the app is given "
                   "view access to the daily job.")

    # ---- where the time goes ----
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("<div class='mn-h2'>Latest stock-movement run · time by step</div>", unsafe_allow_html=True)
        lj = next((j for j in jobs if _job_status(j) != "run"), None)
        if lj:
            steps = _task_steps(lj)
            st.markdown(_bars(steps, f"{len(steps)} steps · steps 04–07 and 10–13 run in parallel, "
                                     "so the total is shorter than the sum"), unsafe_allow_html=True)
        else:
            st.caption("Step timings need view access to the daily job.")
    with c2:
        st.markdown("<div class='mn-h2'>Latest forecast run · time by stage</div>", unsafe_allow_html=True)
        stg = data.get("fc_stages", pd.DataFrame())
        if len(stg):
            steps = [(str(r["stage"]).replace("_", " ").capitalize(),
                      (float(r["end"]) - float(r["start"])) / 1000, "ok" if r["result"] == "SUCCESS" else "fail")
                     for _, r in stg.iterrows()]
            st.markdown(_bars(steps, "Model stages only — data loading and saving are not included"),
                        unsafe_allow_html=True)
        else:
            st.caption("No forecast run recorded.")
