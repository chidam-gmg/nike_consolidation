"""Daily brief screen — the first view of the dev app. The planner sees what the agents handled and decides only the
exceptions: change a quantity (goes to the line manager with a reason the agents learn from), send, or reject.
Each card can open the store walkthrough, so every number pans out to its detail."""
from __future__ import annotations

import html
from datetime import datetime, timezone, timedelta

import pandas as pd
import streamlit as st

import brief as bf
import databricks_io as dbio
import story_view

TYPE_CLS = {"Supply gap": "gap", "Large send": "big", "Forecast swing": "swing", "Overstock": "over", "Data issue": "data"}
TOKENS = {
    "light": dict(ink="#20274C", dim="#5C6478", line="#E1E5EC", card="#FFFFFF", soft="#F6F8FA", accent="#2658F2",
                  acc_soft="#E6EDFE", warm="#A4502F", warm_soft="#FBECE4", amber="#8A5A00", amber_soft="#FBF1DC",
                  ok="#12704D", ok_soft="#E3F3EA"),
    "dark": dict(ink="#E5E9F4", dim="#8F98B3", line="#252E48", card="#111626", soft="#161C30", accent="#6B92FF",
                 acc_soft="#18244A", warm="#EB9677", warm_soft="#3A2219", amber="#F0BE5C", amber_soft="#33281A",
                 ok="#5FD49A", ok_soft="#123024"),
}


def _css(t: dict) -> str:
    return f"""<style>
    .bf-eyebrow {{ font-size:11.5px; letter-spacing:.12em; text-transform:uppercase; font-weight:700; color:{t['dim']}; }}
    .bf-h1 {{ font:600 30px/1.2 Georgia, serif; color:{t['ink']}; margin:4px 0 6px; }}
    .bf-lede {{ font-size:16.5px; line-height:1.55; color:{t['ink']}; max-width:80ch; margin:0 0 4px; }}
    .bf-figs {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); border:1px solid {t['line']}; border-radius:12px;
               overflow:hidden; background:{t['card']}; margin:8px 0 6px; }}
    .bf-fig {{ padding:13px 18px; border-right:1px solid {t['line']}; min-width:0; }} .bf-fig:last-child {{ border-right:0; }}
    .bf-fig .l {{ font-size:11.5px; letter-spacing:.08em; text-transform:uppercase; font-weight:700; color:{t['dim']}; }}
    .bf-fig .v {{ font-size:28px; font-weight:700; line-height:1.15; margin-top:3px; color:{t['ink']}; font-variant-numeric:tabular-nums; }}
    .bf-fig .s {{ font-size:13px; color:{t['dim']}; }}
    .bf-fig.need {{ background:{t['acc_soft']}; }} .bf-fig.need .l, .bf-fig.need .v {{ color:{t['accent']}; }}
    .bf-type {{ display:inline-block; font-size:11.5px; font-weight:700; border-radius:999px; padding:2px 10px; margin-right:8px; }}
    .bf-gap {{ background:{t['warm_soft']}; color:{t['warm']}; }} .bf-big, .bf-swing {{ background:{t['acc_soft']}; color:{t['accent']}; }}
    .bf-over {{ background:{t['ok_soft']}; color:{t['ok']}; }} .bf-data {{ background:{t['soft']}; color:{t['dim']}; }}
    .bf-kind {{ font-size:11.5px; color:{t['dim']}; letter-spacing:.06em; text-transform:uppercase; font-weight:700; }}
    .bf-impact {{ float:right; font-size:13px; color:{t['dim']}; }} .bf-impact b {{ color:{t['ink']}; }}
    .bf-title {{ font-size:16.5px; font-weight:700; color:{t['ink']}; margin:6px 0 2px; }}
    .bf-title span {{ font-weight:400; color:{t['dim']}; }}
    .bf-why {{ color:{t['ink']}; margin:0 0 6px; font-size:14.5px; }}
    .bf-trail {{ border-left:2px solid {t['line']}; padding-left:12px; font-size:13.5px; color:{t['dim']}; margin:0 0 8px; }}
    .bf-rec {{ background:{t['soft']}; border-radius:8px; padding:8px 12px; font-size:14px; color:{t['ink']}; margin-bottom:6px; }}
    .bf-status {{ border-radius:8px; padding:8px 12px; font-size:13.5px; }}
    .bf-status.ok {{ background:{t['ok_soft']}; color:{t['ok']}; }} .bf-status.wait {{ background:{t['amber_soft']}; color:{t['amber']}; }}
    .bf-status.no {{ background:{t['soft']}; color:{t['dim']}; }}
    .bf-ql {{ font-size:14px; font-weight:600; color:{t['ink']}; }}
    .bf-qr {{ font-size:13px; color:{t['dim']}; }} .bf-qr.chg {{ color:{t['amber']}; font-weight:600; }}
    .bf-panel h4 {{ font:600 16px/1.3 Georgia, serif; color:{t['ink']}; margin:0 0 6px; }}
    .bf-li {{ display:flex; justify-content:space-between; gap:10px; font-size:14px; color:{t['ink']};
              border-bottom:1px solid {t['line']}; padding:5px 0; }}
    .bf-li:last-child {{ border-bottom:0; }} .bf-li b {{ font-variant-numeric:tabular-nums; white-space:nowrap; }}
    .bf-li small {{ display:block; color:{t['dim']}; font-size:12.5px; }}
    .bf-note {{ font-size:13px; color:{t['dim']}; }}
    .bf-agent {{ display:grid; grid-template-columns:28px 1fr; gap:10px; font-size:13.5px; color:{t['dim']}; padding:3px 0; }}
    .bf-av {{ width:28px; height:28px; border-radius:7px; background:{t['acc_soft']}; color:{t['accent']}; font-weight:700;
              font-size:11.5px; display:grid; place-items:center; }}
    .bf-agent b {{ color:{t['ink']}; }}
    @media (max-width: 900px) {{ .bf-figs {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
    </style>"""


def _weeks_away(start) -> str:
    w = (pd.Timestamp(start) - pd.Timestamp(_gst_now().date())).days // 7
    return "this week" if w <= 0 else f"{w} wk" if w == 1 else f"{w} wks"


def _gst_now():
    return datetime.now(timezone.utc) + timedelta(hours=4)


def _card_status(card, dec_lines: pd.DataFrame, acts: pd.DataFrame):
    """(state, text) from saved decisions: None if still open."""
    if card["kind"] == "act":
        a = acts[acts["card_id"] == card["id"]] if not acts.empty else acts
        if a.empty:
            return None
        r = a.iloc[0]
        txt = {"Requested": ("ok", "Requested · sent to SAP queue"), "Rejected": ("no", "Dismissed"),
               "Awaiting line manager": ("wait", f"Waiting for line manager · reason: {r['reason']}"),
               "Change approved": ("ok", "Change approved by line manager"),
               "Change declined": ("no", "Change declined · recommendation stands")}
        return txt.get(r["status"], ("no", r["status"]))
    d = dec_lines[dec_lines["line_key"].isin(card["lines"])] if not dec_lines.empty else dec_lines
    if d.empty or len(d) < len(card["lines"]):
        return None
    st_ = set(d["status"])
    if "Awaiting line manager" in st_:
        reason = d.loc[d["status"] == "Awaiting line manager", "reason"].dropna()
        return ("wait", "Waiting for line manager" + (f" · reason: {reason.iloc[0]}" if len(reason) else ""))
    if d["decision"].eq("Rejected").all():
        return ("no", "Held" if card["type"] == "Data issue" else "Rejected")
    return ("ok", "Approved · queued for SAP")


def _save_lines(card, rec: pd.DataFrame, qty: dict, decision_all: str | None, reason, note, run_id, me):
    """Turn a card's edited rows into line decisions (Approve / Modify / Reject) and save them."""
    lines = rec.set_index("line_key")
    out = []
    for row_i, row in enumerate(card["rows"] or [{"lines": card["lines"], "rec": None}]):
        keys = row["lines"]
        if decision_all:
            new = {k: int(lines.at[k, "alloc_qty"]) for k in keys}
        elif len(keys) == 1:
            new = {keys[0]: int(qty[row_i])}
        else:
            new = bf.spread(int(qty[row_i]), keys, [lines.at[k, "alloc_qty"] for k in keys])
        for k in keys:
            r = lines.loc[k]
            dec = decision_all or ("Approve" if new[k] == int(r["alloc_qty"]) else "Modify")
            out.append({"line_key": k, "country": r["country"], "store": r["store"], "store_name": r["store_name"],
                        "parent": r["parent"], "art": r["art"], "size": r["size"], "recommended_qty": int(r["alloc_qty"]),
                        "decision": dec, "new_qty": new[k], "reason": reason if dec != "Approve" else None,
                        "comment": note})
    return dbio.save_al_decisions(run_id, pd.DataFrame(out), me)


def _card(card, b, t, run_id, me, dec_lines, acts, theme, loaders, ver):
    cls = TYPE_CLS.get(card["type"], "data")
    trail = "".join(f"<div>{html.escape(x)}</div>" for x in card["trail"])
    st.markdown(f"""<div class="bf-impact"><b>{html.escape(card['impact'])}</b></div>
        <span class="bf-type bf-{cls}">{card['type']}</span><span class="bf-kind">{'decide' if card['kind'] == 'decide' else 'act'}</span>
        <div class="bf-title">{html.escape(card['title'])} <span>· {html.escape(card['sub'])}</span></div>
        <p class="bf-why">{html.escape(card['why'])}</p><div class="bf-trail">{trail}</div>
        <div class="bf-rec">Recommended: <b>{html.escape(card['rec'])}</b></div>""", unsafe_allow_html=True)
    status = _card_status(card, dec_lines, acts)
    if status:
        st.markdown(f'<div class="bf-status {status[0]}">{html.escape(status[1])}</div>', unsafe_allow_html=True)
        return
    k = f"bf_{run_id}_{ver}_{card['id']}"
    qty, changed = [], False
    for i, row in enumerate(card["rows"]):
        c = st.columns([1.5, 1, 2.2], vertical_alignment="center")
        c[0].markdown(f'<span class="bf-ql">{html.escape(row["label"])}</span>', unsafe_allow_html=True)
        v = c[1].number_input(row["label"], min_value=0, step=1, value=int(row["rec"]), key=f"{k}_q{i}",
                              label_visibility="collapsed")
        qty.append(int(v))
        chg = int(v) != int(row["rec"])
        changed = changed or chg
        c[2].markdown(f'<span class="bf-qr{" chg" if chg else ""}">'
                      f'{"changed from" if chg else "recommended"} {int(row["rec"])}</span>', unsafe_allow_html=True)
    reason = note = None
    if changed:
        r = st.columns([1.4, 1.6])
        reason = r[0].selectbox("Why the change?", [""] + bf.REASONS, key=f"{k}_r", format_func=lambda x: x or "Why the change?",
                                label_visibility="collapsed")
        note = r[1].text_input("Note", key=f"{k}_n", placeholder="Add a note (optional)", label_visibility="collapsed")
        st.markdown('<span class="bf-note">A changed quantity goes to your line manager before anything is sent. '
                    'Your reason is saved so the agents learn from it.</span>', unsafe_allow_html=True)
    a = st.columns([1.3, 1, 1, 1.3, 2], vertical_alignment="center")
    send = a[0].button("Send for review" if changed else card["send_label"], type="primary", key=f"{k}_s",
                       use_container_width=True)
    reject = a[1].button(card["reject_label"], key=f"{k}_x", use_container_width=True)
    with a[2].popover("Ask why", use_container_width=True):
        st.markdown(f"**Why the agents raised this**\n\n{card['why']}\n\n" + "\n\n".join(f"- {x}" for x in card["trail"]))
        st.caption("For follow-up questions, use Ask the agents in the Agent lab.")
    detail = a[3].toggle("Show detail", key=f"{k}_d", disabled=not (card.get("store") and card.get("parent")))
    if send or reject:
        if send and changed and not reason:
            st.warning("Pick a reason for the change — it goes to your line manager and the agents learn from it.")
            return
        try:
            if card["kind"] == "act":
                rows = [{"label": r["label"], "recommended": int(r["rec"]), "qty": q} for r, q in zip(card["rows"], qty)]
                dbio.save_action(run_id, card, "Reject" if reject else ("Modify" if changed else "Send"), rows,
                                 reason or ("Dismissed" if reject else None), note, me)
            elif card["type"] == "Data issue":
                _save_lines(card, b["rec"], [], "Reject" if reject else "Approve",
                            "Store has no grade" if reject else None, note, run_id, me)
            else:
                _save_lines(card, b["rec"], qty, "Reject" if reject else None,
                            (reason or "Rejected from the daily brief") if (reject or changed) else None, note, run_id, me)
            st.session_state["bf_ver"] = st.session_state.get("bf_ver", 0) + 1
            st.rerun()
        except Exception as e:
            st.error(f"Could not save: {e}")
    if detail:
        sr = loaders["size_run"](card["store"], card["parent"])
        if sr.empty:
            st.caption("No size detail for this store and style.")
        else:
            ser = loaders["series"](sr["country"].iloc[0], card["parent"])
            cal = loaders["calendar"](sr["country"].iloc[0], card["parent"])
            ev = {str(pd.Timestamp(x).date()): y for x, y in zip(cal["week_start"], cal["events"].fillna(""))} if not cal.empty else {}
            story_view.render(sr, ser, ev, theme=theme)


def render(b: dict, run_id: str, theme: str, me: str, dev: bool, dec_lines: pd.DataFrame, acts: pd.DataFrame,
           inputs: dict, loaders: dict):
    t = TOKENS.get(theme, TOKENS["light"])
    st.markdown(_css(t), unsafe_allow_html=True)
    s, cards, ver = b["stats"], b["cards"], st.session_state.get("bf_ver", 0)
    status = {c["id"]: _card_status(c, dec_lines, acts) for c in cards}
    open_cards = [c for c in cards if status[c["id"]] is None]
    n_dec = sum(1 for c in open_cards if c["kind"] == "decide")
    n_act = sum(1 for c in open_cards if c["kind"] == "act")
    sm = inputs["summary"].iloc[0].to_dict() if not inputs["summary"].empty else {}
    gap = next((c for c in cards if c["type"] == "Supply gap"), None)
    try:
        ran = pd.Timestamp(sm.get("run_ts")).tz_convert("Asia/Dubai")
        ran_s = f"Plan ready at {ran:%H:%M} GST, {ran.day} {ran:%b}"
    except Exception:
        ran_s = "Plan ready"
    head = (f"{n_dec} decision{'s' if n_dec != 1 else ''} and {n_act} action{'s' if n_act != 1 else ''} need you today."
            if open_cards else "Nothing needs you today.")
    lede = (f"The agents planned <b>{s['units']:,} units to {s['stores']} stores</b> for the next two weeks. "
            f"<b>{s['auto_lines']:,} of {s['lines']:,} lines</b> are inside your guardrails and handled by the agents. ")
    if gap:
        lede += (f"The biggest risk is <b>{html.escape(gap['title'])}</b> in {html.escape(gap['sub'])}: "
                 f"{html.escape(gap['impact'])} with no DC stock.")
    st.markdown(f"""<div class="bf-eyebrow">Daily brief · {_gst_now():%A} {_gst_now().day} {_gst_now():%B} · Nike CORE ·
        {int(float(sm.get('scope_countries') or 0))} countries · {int(float(sm.get('scope_stores') or 0))} stores · {ran_s}</div>
        <div class="bf-h1">{head} Everything else is handled.</div><p class="bf-lede">{lede}</p>
        <div class="bf-figs">
          <div class="bf-fig"><div class="l">Planned</div><div class="v">{s['units']:,}</div><div class="s">units · {s['stores']} stores</div></div>
          <div class="bf-fig"><div class="l">Handled by agents</div><div class="v">{s['auto_lines']:,}</div>
            <div class="s">lines · {s['auto_units']:,} units inside guardrails</div></div>
          <div class="bf-fig need"><div class="l">Needs you</div><div class="v">{len(open_cards)}</div>
            <div class="s">{n_dec} to decide · {n_act} to act on</div></div>
          <div class="bf-fig"><div class="l">Supply gap</div><div class="v">{s['short_units'] / 1000:.1f}K</div>
            <div class="s">units wanted, no DC stock</div></div></div>""", unsafe_allow_html=True)

    left, right = st.columns([2.25, 1], gap="large")
    with left:
        types = [ty for ty in ["Large send", "Forecast swing", "Data issue", "Supply gap", "Overstock"] if ty in b["by_type"]]
        open_by = pd.Series([c["type"] for c in open_cards]).value_counts().to_dict() if open_cards else {}
        f = st.columns([4.2, 1], vertical_alignment="center")
        pick = f[0].pills("Show", ["All"] + types, default="All", key="bf_type", label_visibility="collapsed",
                          format_func=lambda x: f"{x} · {len(open_cards) if x == 'All' else open_by.get(x, 0)}")
        show_done = f[1].toggle("Show done", key="bf_done")
        pool = [c for c in (cards if show_done else open_cards) if pick in (None, "All") or c["type"] == pick]
        lim = st.session_state.get("bf_lim", 10)
        if not pool:
            st.success("Nothing open here. Decided cards are under Show done.")
        for card in pool[:lim]:
            with st.container(border=True):
                _card(card, b, t, run_id, me, dec_lines, acts, theme, loaders, ver)
        if len(pool) > lim:
            if st.button(f"Show {min(10, len(pool) - lim)} more of {len(pool) - lim}", key="bf_more"):
                st.session_state["bf_lim"] = lim + 10
                st.rerun()

    with right:
        auto = b["auto"]
        undecided = auto[~auto["line_key"].isin(dec_lines["line_key"])] if not dec_lines.empty else auto
        with st.container(border=True):
            st.markdown(f"""<div class="bf-panel"><h4>Handled by the agents</h4>
              <div class="bf-li"><span>Key-size display top-ups<small>1 unit each, so every key size is on show</small></span><b>{s['auto_display']:,}</b></div>
              <div class="bf-li"><span>Routine top-ups<small>up to {int(b['guard']['auto_max_line_units'])} units a line</small></span><b>{s['auto_lines'] - s['auto_display']:,}</b></div>
              <div class="bf-li"><span>Released to SAP queue<small>shadow mode: nothing goes until you release it</small></span><b>{len(auto) - len(undecided):,}</b></div>
              </div>""", unsafe_allow_html=True)
            if len(undecided) and st.button(f"Release {len(undecided):,} lines", type="primary", use_container_width=True,
                                             key="bf_release"):
                with st.spinner("Releasing lines inside your guardrails…"):
                    rows = undecided.assign(recommended_qty=undecided["alloc_qty"], decision="Approve", new_qty=None,
                                            reason=None, comment="Released within guardrails from the daily brief")
                    dbio.save_al_decisions(run_id, rows, f"agents · released by {me}")
                st.session_state["bf_ver"] = ver + 1
                st.rerun()

        # sign-off waiting for this person as line manager (line changes + actions)
        try:
            lq = dbio.load_manager_queue(run_id, me, include_unassigned=dev)
            aq = dbio.load_action_queue(me, include_unassigned=dev)
        except Exception:
            lq, aq = pd.DataFrame(), pd.DataFrame()
        if len(lq) or len(aq):
            with st.container(border=True):
                st.markdown(f'<div class="bf-panel"><h4>Waiting for your sign-off</h4><span class="bf-note">'
                            f'{len(lq) + len(aq)} change(s) from planners{" · dev: unmapped changes shown" if dev else ""}</span></div>',
                            unsafe_allow_html=True)
                for r in aq.head(6).itertuples():
                    st.markdown(f"**{r.card_type}** · {r.title} — {r.reason or ''}  \n<span class='bf-note'>by {r.decided_by}</span>",
                                unsafe_allow_html=True)
                    c = st.columns(2)
                    if c[0].button("Approve", key=f"bfa_ok_{r.card_id}", use_container_width=True):
                        dbio.save_action_manager(r.run_id, r.card_id, True, "", me); st.rerun()
                    if c[1].button("Decline", key=f"bfa_no_{r.card_id}", use_container_width=True):
                        dbio.save_action_manager(r.run_id, r.card_id, False, "", me); st.rerun()
                for r in lq.head(6).itertuples():
                    st.markdown(f"**{str(r.store_name).title()}** · {r.art} size {r.size}: "
                                f"{r.recommended_qty} → {r.final_qty} — {r.reason or ''}  \n<span class='bf-note'>by {r.decided_by}</span>",
                                unsafe_allow_html=True)
                    c = st.columns(2)
                    for col, lab, dec in ((c[0], "Approve", "Approve change"), (c[1], "Decline", "Decline change")):
                        if col.button(lab, key=f"bfl_{lab}_{r.line_key}", use_container_width=True):
                            dbio.save_manager_decisions(run_id, pd.DataFrame([{"line_key": r.line_key, "manager_decision": dec,
                                                                               "manager_comment": ""}]), me)
                            st.session_state["bf_ver"] = ver + 1
                            st.rerun()

        with st.container(border=True):
            st.markdown(f"""<div class="bf-panel"><h4>What the agents did</h4>
              <div class="bf-agent"><div class="bf-av">FC</div><div><b>Forecaster</b> forecast {int(float(sm.get('scope_stores') or 0))}
                stores' CORE styles 8 weeks ahead, with season curves and events.</div></div>
              <div class="bf-agent"><div class="bf-av">SP</div><div><b>Splitter</b> split demand to stores and sizes by product cluster.</div></div>
              <div class="bf-agent"><div class="bf-av">AL</div><div><b>Allocator</b> planned {s['units']:,} units from the DCs.</div></div>
              <div class="bf-agent"><div class="bf-av">AU</div><div><b>Auditor</b> checked every line against your guardrails;
                {s['lines'] - s['auto_lines']:,} lines came to you on {s['decide']} cards.</div></div></div>""", unsafe_allow_html=True)

        ev = inputs["events"]
        if not ev.empty:
            items = "".join(f'<div class="bf-li"><span>{html.escape(str(r.event))}<small>{pd.Timestamp(r.start):%d %b} – '
                            f'{pd.Timestamp(r.end):%d %b}</small></span><b>{_weeks_away(r.start)}</b></div>'
                            for r in ev.itertuples())
            with st.container(border=True):
                st.markdown(f'<div class="bf-panel"><h4>Coming up</h4>{items}</div>', unsafe_allow_html=True)

        try:
            ln = dbio.load_learning()
        except Exception:
            ln = pd.DataFrame()
        with st.container(border=True):
            body = ("".join(f'<div class="bf-li"><span>{html.escape(str(r.reason))}<small>{html.escape(str(r.store_name).title())}</small></span>'
                            f'<b>{int(r.n)}×</b></div>' for r in ln.itertuples())
                    if not ln.empty else '<span class="bf-note">No changes recorded yet. Each reason you give will show here.</span>')
            st.markdown(f'<div class="bf-panel"><h4>Learning from your changes</h4><span class="bf-note">Reasons behind changes in '
                        f'the last 4 weeks. When one keeps coming back, the agents propose a rule for your manager to '
                        f'approve.</span>{body}</div>', unsafe_allow_html=True)

        g = b["guard"]
        with st.container(border=True):
            st.markdown(f"""<div class="bf-panel"><h4>Your guardrails</h4><span class="bf-note">Agents approve a line on their own
              when it is <b>{int(g['auto_max_line_units'])} units or fewer</b>, the store has a grade, and the style's forecast is
              within <b>±{int(g['swing_pct'])}%</b> of recent sales.</span></div>""", unsafe_allow_html=True)
            with st.expander("Change guardrails"):
                with st.form("bf_guard"):
                    new = {"auto_max_line_units": st.number_input("Auto-approve lines up to (units)", 1, 50, int(g["auto_max_line_units"])),
                           "swing_pct": st.number_input("Flag forecast swings of (%)", 10, 300, int(g["swing_pct"])),
                           "gap_top_n": st.number_input("Supply gaps to show", 0, 50, int(g["gap_top_n"])),
                           "overstock_top_n": st.number_input("Overstock transfers to show", 0, 50, int(g["overstock_top_n"]))}
                    if st.form_submit_button("Save guardrails", type="primary"):
                        changed = {k: v for k, v in new.items() if float(v) != float(g[k])}
                        if changed:
                            dbio.save_guardrails(changed, me)
                            st.session_state["bf_ver"] = ver + 1
                            st.cache_data.clear()
                            st.rerun()
