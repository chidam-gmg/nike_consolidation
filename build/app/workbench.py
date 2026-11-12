"""Stock Movement workbench: role pages on top of the engine (Allocator · Line manager · Executive).

Allocator   Today (First allocation · DC transfer · IBT; select, approve, change qty, reject; send to SAP), Sent to SAP
Manager     Approvals (changes over the tolerance, rejections, added transfers), Rules, Sent to SAP, Impact
Executive   Impact, Sent to SAP
"Engine" in the role picker falls through to the full analyst app (all existing tabs).
Everything is counted the way the business talks about it: SKUs (child articles) and qty.
"""
from __future__ import annotations

import hashlib

import altair as alt
import pandas as pd
import streamlit as st

import databricks_io as dbio
import wb_io as w

ss = st.session_state
ROLES = ["Allocator", "Line manager", "Executive", "Engine"]
PAGES = {"Allocator": ["Today", "Sent to SAP"],
         "Line manager": ["Approvals", "Rules", "Sent to SAP", "Impact"],
         "Executive": ["Impact", "Sent to SAP"]}
REASONS = ["Visual merchandising", "Event at the store", "Store has no space", "Stock figure looks wrong",
           "Keep where it is", "Store request", "Other"]
GREEN, ORANGE, BLUE, RED, SOFTBLUE = "#0E7A4B", "#E07A2F", "#2658F2", "#B42318", "#9DB2F0"
PAGE_SIZE = 20


def _fmt(n) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except Exception:
        return "—"


def _k(n) -> str:
    n = float(n or 0)
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.1f}K" if n >= 1e4 else _fmt(n)


def _pal(theme: str) -> dict:
    if theme == "dark":
        return dict(card="#111626", line="#252E48", text="#E5E9F4", muted="#8790AB", soft="#161C30", sel="#1C2A55", row="#1A2034")
    return dict(card="#FFFFFF", line="#E1E7E8", text="#20274C", muted="#5C6478", soft="#F3F6F7", sel="#EAF0FE", row="#EEF0F3")


def _kid(*parts) -> str:
    return hashlib.md5("|".join(str(x) for x in parts).encode()).hexdigest()[:12]


def _ver() -> int:
    return ss.setdefault("wb_ver", 0)


def _bump():
    ss.wb_ver = _ver() + 1
    st.cache_data.clear()


def _flash(msg: str):
    ss.wb_flash_msg = msg


# ---------- cached reads (ver busts after a write) ----------
@st.cache_data(ttl=600, show_spinner=False)
def _run(ver):
    return w.latest_run()


@st.cache_data(ttl=3600, show_spinner=False)
def _scope(ver):
    return w.store_scope()


@st.cache_data(ttl=3600, show_spinner=False)
def _names(ver):
    return w.store_names()


@st.cache_data(ttl=600, show_spinner=False)
def _totals(rid, ver):
    return w.type_totals(rid)


@st.cache_data(ttl=600, show_spinner=False)
def _queue(rid, mt, ver):
    q = w.review_queue(rid, mt)
    q["kid"] = [_kid(mt, k) for k in q["line_key"]]
    return q


@st.cache_data(ttl=600, show_spinner=False)
def _ready(rid, country, ver):
    return w.ready_to_send(rid, country)


@st.cache_data(ttl=3600, show_spinner=False)
def _fa_sum(countries, ver):
    return w.fa_summary(list(countries))


@st.cache_data(ttl=600, show_spinner=False)
def _fa_rows(country, which, search, ver):
    return w.fa_articles(country, which, search)


@st.cache_data(ttl=3600, show_spinner=False)
def _fa_pack(country, parent):
    return w.fa_pack(country, parent)


@st.cache_data(ttl=600, show_spinner=False)
def _mgr(rid, ver):
    q = w.manager_queue(rid)
    q["kid"] = [_kid("M", a, b) for a, b in zip(q["movement_type"], q["line_key"])]
    return q


@st.cache_data(ttl=600, show_spinner=False)
def _team(rid, ver):
    return w.team_progress(rid)


@st.cache_data(ttl=300, show_spinner=False)
def _sent_runs(ver):
    return w.sent_runs()


@st.cache_data(ttl=300, show_spinner=False)
def _sends(rid, ver):
    return w.sends(rid)


@st.cache_data(ttl=300, show_spinner=False)
def _not_sent(rid, ver):
    return w.not_sent(rid)


@st.cache_data(ttl=3600, show_spinner=False)
def _series(ver):
    return w.daily_series(35)


@st.cache_data(ttl=3600, show_spinner=False)
def _dev_runs(ver):
    try:
        return dbio.run_sql(f"SELECT run_id FROM {dbio.FQ}.run_stats_history WHERE triggered_by LIKE 'dev-%'")["run_id"].tolist()
    except Exception:
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def _coverage(rid, ver):
    return w.coverage(rid)


@st.cache_data(ttl=3600, show_spinner=False)
def _master(ver):
    return w.master_funnel()


@st.cache_data(ttl=3600, show_spinner=False)
def _gap(ver):
    return w.coverage_gap()


@st.cache_data(ttl=3600, show_spinner=False)
def _rules(ver):
    return w.size_rules(), w.run_settings(), w.change_log()


# ---------- styling ----------
def _css(p: dict, active_card: str | None = None):
    st.markdown(f"""<style>
  .st-key-wb_nav {{ border-bottom:1px solid {p['line']}; padding-bottom:0; margin-bottom:6px; }}
  .st-key-wb_nav [data-testid="stButtonGroup"] {{ gap:0 !important; }}
  .st-key-wb_nav [data-testid="stButtonGroup"] button {{ background:transparent !important; border:0 !important;
      border-radius:0 !important; box-shadow:none !important; border-bottom:3px solid transparent !important;
      padding:10px 4px !important; margin-right:24px !important; min-height:auto !important; }}
  .st-key-wb_nav [data-testid="stButtonGroup"] button p {{ font-size:15px !important; font-weight:600 !important; color:{p['muted']} !important; }}
  .st-key-wb_nav [data-testid="stButtonGroup"] button[data-testid="stBaseButton-segmented_controlActive"] {{ border-bottom-color:{BLUE} !important; }}
  .st-key-wb_nav [data-testid="stButtonGroup"] button[data-testid="stBaseButton-segmented_controlActive"] p {{ color:{p['text']} !important; }}
  [class*="st-key-wbcard_"] {{ background:{p['card']}; border:1px solid {p['line']}; border-radius:12px; padding:14px 16px 4px 16px; gap:4px; }}
  {f'.st-key-wbcard_{active_card} {{ border:2px solid {BLUE}; box-shadow:0 1px 4px rgba(38,88,242,.12); }}' if active_card else ''}
  [class*="st-key-wbcard_"] {{ position:relative; cursor:pointer; transition:border-color .15s, box-shadow .15s; }}
  [class*="st-key-wbcard_"]:hover {{ border-color:{BLUE}; box-shadow:0 2px 8px rgba(38,88,242,.15); }}
  [class*="st-key-wbcardbtn_"] {{ position:absolute !important; inset:0; z-index:3; margin:0 !important; width:100% !important; }}
  [class*="st-key-wbcardbtn_"] > div, [class*="st-key-wbcardbtn_"] button {{ width:100% !important; height:100% !important; }}
  [class*="st-key-wbcardbtn_"] button {{ opacity:0; cursor:pointer; }}
  .wb-look img {{ width:100%; height:96px; object-fit:contain; background:#FFFFFF; border-radius:8px; }}
  [class*="st-key-wbrow_"] {{ border-bottom:1px solid {p['row']}; padding:2px 0 2px 0; gap:0; }}
  [class*="st-key-wbrow_"] [data-testid="stNumberInput"] input {{ text-align:right; font-weight:700; }}
  [class*="st-key-wbrow_"] [data-testid="stCheckbox"] {{ padding-top:2px; }}
  .st-key-wb_bulk, .st-key-wb_mbulk, .st-key-wb_fbulk {{ background:{p['sel']}; border-radius:10px; padding:6px 12px; }}
  .wb-h {{ font-size:12px; font-weight:600; letter-spacing:.04em; text-transform:uppercase; color:{p['muted']}; }}
  .wb-chip {{ display:inline-block; font-size:12px; font-weight:600; padding:3px 9px; border-radius:999px; white-space:nowrap; }}
</style>""", unsafe_allow_html=True)


def _bar(parts, height=8) -> str:
    """parts: [(share 0..1, color)] -> one stacked bar."""
    segs = "".join(f"<span style='width:{max(0.0, s) * 100:.2f}%;background:{c};'></span>" for s, c in parts)
    return (f"<span style='display:flex;width:100%;height:{height}px;border-radius:4px;overflow:hidden;"
            f"background:#E4E7EC;'>{segs}</span>")


def _chip(text, kind="todo") -> str:
    col = {"todo": ("#FFF1E7", "#9A3D0B"), "ok": ("#E6F4EC", GREEN), "info": ("#EAF0FE", "#173AA8"),
           "bad": ("#FEF0EF", RED), "muted": ("#F2F4F7", "#4B5565")}[kind]
    return f"<span class='wb-chip' style='background:{col[0]};color:{col[1]};'>{text}</span>"


def _h1(text, sub=""):
    st.markdown(f"<div style='font-size:26px;font-weight:700;line-height:1.2;margin:6px 0 4px 0;'>{text}</div>",
                unsafe_allow_html=True)
    if sub:
        st.caption(sub)


def _store(code, names) -> str:
    c = str(code)
    return f"{c} {names[c].title()}" if c in names else c


def _route(frm, to, names) -> str:
    return (f"<div style='font-size:13px;line-height:1.3;'><span style='opacity:.75;'>{_store(frm, names)}</span><br>"
            f"→ <b>{_store(to, names)}</b></div>")


def _item(name, article, size=None) -> str:
    sz = f" · size <b>{size}</b>" if size is not None else ""
    return (f"<div style='line-height:1.25;'><b style='font-size:14px;'>{str(name).title() if name else article}</b><br>"
            f"<span style='font-size:12px;opacity:.75;'>{article}{sz}</span></div>")


def _flash_box():
    msg = ss.pop("wb_flash_msg", None)
    if msg:
        st.success(msg)


# ---------- selection helpers (kept in session, not in widget state, so pages and bulk actions stay safe) ----------
def _sel(prefix) -> set:
    return ss.setdefault(f"{prefix}_sel", set())


def _cb_toggle(prefix, kid):
    s = _sel(prefix)
    s.discard(kid) if kid in s else s.add(kid)


def _cb_select(prefix, kids, on=True):
    s = _sel(prefix)
    (s.update if on else s.difference_update)(kids)
    ss[f"{prefix}_sv"] = ss.get(f"{prefix}_sv", 0) + 1


def _cb_clear(prefix):
    ss[f"{prefix}_sel"] = set()
    ss[f"{prefix}_all"] = False
    ss[f"{prefix}_sv"] = ss.get(f"{prefix}_sv", 0) + 1


def _cb_all(prefix):
    ss[f"{prefix}_all"] = True


def _cb_page(prefix, delta, n_pages):
    ss[f"{prefix}_pg"] = min(max(0, ss.get(f"{prefix}_pg", 0) + delta), max(0, n_pages - 1))


def _cb_reset_page(prefix):
    ss[f"{prefix}_pg"] = 0
    _cb_clear(prefix)


def _checkbox(col, prefix, kid, label):
    col.checkbox(label, value=kid in _sel(prefix), key=f"cb_{prefix}_{kid}_{ss.get(prefix + '_sv', 0)}",
                 on_change=_cb_toggle, args=(prefix, kid), label_visibility="collapsed")


def _pager(prefix, n):
    pages = max(1, -(-n // PAGE_SIZE))
    pg = min(ss.get(f"{prefix}_pg", 0), pages - 1)
    return pg, pages


def _pager_row(prefix, n, pg, pages):
    c = st.columns([6, 1, 1], vertical_alignment="center")
    c[0].caption(f"Showing {pg * PAGE_SIZE + 1 if n else 0:,}–{min(n, (pg + 1) * PAGE_SIZE):,} of {n:,}")
    c[1].button("‹ Prev", key=f"{prefix}_prev", on_click=_cb_page, args=(prefix, -1, pages), disabled=pg == 0, width="stretch")
    c[2].button("Next ›", key=f"{prefix}_next", on_click=_cb_page, args=(prefix, 1, pages), disabled=pg >= pages - 1,
                width="stretch")


# ---------- nav: page tabs · country · role on one bar ----------
def _cb_country():
    for px in ("wb_q_DC_TRANSFER", "wb_q_IBT", "wb_fa", "wb_mgr"):
        _cb_reset_page(px)


def role_picker(_col=None) -> str:
    p = _pal("dark" if ss.get("dark_mode") else "light")
    _css(p, ss.get("wb_type", "DC_TRANSFER") if ss.get("wb_role", "Allocator") == "Allocator" else None)
    with st.container(key="wb_nav"):
        c = st.columns([5.6, 1.5, 1.7], vertical_alignment="center")
        role = c[2].selectbox("View as", ROLES, key="wb_role", label_visibility="collapsed",
                              format_func=lambda r: f"View as · {r}")
        if role != "Engine":
            pages = PAGES[role]
            with c[0]:
                st.segmented_control("Page", pages, default=pages[0], key=f"wb_page_{role}", label_visibility="collapsed")
            try:
                codes = w.country_codes(_scope(_ver())["countries"])
            except Exception:
                codes = []
            opts = ["All"] + [x for x in ["AE", "SA", "KW", "QA", "BH", "OM"] if x in codes]
            c[1].selectbox("Country", opts, key="wb_cty", label_visibility="collapsed", on_change=_cb_country,
                           format_func=lambda x: "All countries" if x == "All" else w.CC_NAME.get(x, x))
    return role or "Allocator"


def render(role: str, theme: str, dev: bool):
    p = _pal(theme)
    pages = PAGES[role]
    page = ss.get(f"wb_page_{role}") or pages[0]
    country = ss.get("wb_cty", "All")
    country = None if country in (None, "All") else country
    try:
        run = _run(_ver())
    except Exception as e:
        st.warning(f"Could not load the latest run: {e}")
        return
    if not run:
        st.info("No completed engine run yet.")
        return
    rid = str(run["run_id"])
    user = dbio.current_user()
    if page == "Today":
        _today(p, run, rid, user, country)
    elif page == "Approvals":
        _approvals(p, rid, user, country)
    elif page == "Rules":
        _rules_page(p, rid)
    elif page == "Sent to SAP":
        _saplog(p, rid)
    elif page == "Impact":
        _impact(p, rid, country)


# ---------- ALLOCATOR · TODAY ----------
def _open_mask(q):
    return q["status"].isna() | (q["mgr_decision"].fillna("") == "SENT_BACK")


def _today(p, run, rid, user, country):
    ver = _ver()
    with st.spinner("Loading today's run…"):
        scope = _scope(ver)
        codes = w.country_codes(scope["countries"])
        tt = _totals(rid, ver)
        qs = {mt: _queue(rid, mt, ver) for mt in ["DC_TRANSFER", "IBT"]}
        fa = _fa_sum(tuple([country] if country else codes), ver)
        ready = _ready(rid, country, ver)
    tot = tt[tt["country"].isna()] if country is None else tt[tt["country"] == country]
    tot = tot.set_index("movement_type").reindex(["DC_TRANSFER", "IBT"]).fillna(0)

    m = {}
    for mt, q in qs.items():
        q = q if country is None else q[q["country"] == country]
        o = q[_open_mask(q)]
        wait = q[q["status"].isin(["CHANGED", "REJECTED"]) & q["mgr_decision"].isna()]
        m[mt] = dict(need_skus=o["article"].nunique(), need_qty=int(o["proposed_qty"].sum()),
                     wait_qty=int(wait["proposed_qty"].sum()), arts=set(o["article"]))
    need_skus = len(m["DC_TRANSFER"]["arts"] | m["IBT"]["arts"])
    need_qty = m["DC_TRANSFER"]["need_qty"] + m["IBT"]["need_qty"]
    by = scope["by_country"]
    n_stores = int(by["in_run"].sum()) if country is None else \
        int(by.loc[by["country"].map(lambda c: w.country_codes([c])[0]) == country, "in_run"].sum())
    where = w.CC_NAME.get(country, country) if country else f"{len(codes)} {'country' if len(codes) == 1 else 'countries'}"

    top = st.columns([3.2, 1.2, 1.8], vertical_alignment="bottom")
    with top[0]:
        _h1(f"{need_skus:,} SKUs · {need_qty:,} qty need you",
            f"{where} · {n_stores} stores · run `{rid}` · stock as of {run.get('as_of_date', '—')} · "
            f"SAP cut-off **{w.CUTOFF}** (proposed)")
    with top[1].popover("+ Add a transfer", width="stretch"):
        _add_transfer_form(rid, user, scope)
    if top[2].button(f"Send {ready['qty']:,} qty · {ready['skus']:,} SKUs to SAP", type="primary", width="stretch",
                     disabled=ready["qty"] == 0):
        _confirm_send(rid, ready, user, country)
    _flash_box()

    sel = ss.setdefault("wb_type", "DC_TRANSFER")
    cards = {
        "FIRST_ALLOCATION": dict(n="1", label="First allocation", route="DC → Store", skus=fa["all_skus"], qty=fa["pack"],
                                 ready=1 - (fa["need_skus"] / fa["all_skus"] if fa["all_skus"] else 0),
                                 ready_txt=f"Ready {_fmt(fa['all_skus'] - fa['need_skus'])} SKUs",
                                 need_txt=f"{_fmt(fa['need_skus'])} SKUs",
                                 note="New articles (Spring 2027) · provisional until the buy file loads"),
        "DC_TRANSFER": dict(n="2", label="DC transfer", route="DC → Store", skus=tot.loc["DC_TRANSFER", "skus"],
                            qty=tot.loc["DC_TRANSFER", "qty"]),
        "IBT": dict(n="3", label="IBT", route="Store → Store", skus=tot.loc["IBT", "skus"], qty=tot.loc["IBT", "qty"]),
    }
    for mt in ["DC_TRANSFER", "IBT"]:
        q_all = float(cards[mt]["qty"] or 0)
        rq = q_all - m[mt]["need_qty"] - m[mt]["wait_qty"]
        cards[mt].update(ready=rq / q_all if q_all else 0, ready_txt=f"Ready {_k(rq)} qty",
                         need_txt=f"{_fmt(m[mt]['need_skus'])} SKUs · {_fmt(m[mt]['need_qty'])} qty",
                         note="Replenishment from the DC · all divisions" if mt == "DC_TRANSFER"
                         else "Store to store, within each country")
    cols = st.columns(3)
    for c, mt in zip(cols, ["FIRST_ALLOCATION", "DC_TRANSFER", "IBT"]):
        d, on = cards[mt], sel == mt
        with c.container(key=f"wbcard_{mt}"):
            st.markdown(f"""
<div style="display:flex;justify-content:space-between;align-items:center;">
  <div style="display:flex;align-items:center;gap:8px;"><span style="width:22px;height:22px;border-radius:50%;
       background:{BLUE if on else p['soft']};color:{'#FFF' if on else p['text']};font-size:12px;font-weight:700;
       display:inline-flex;align-items:center;justify-content:center;">{d['n']}</span><b style="font-size:16px;">{d['label']}</b></div>
  <span style="font-size:12px;font-weight:600;color:{p['muted']};background:{p['soft']};padding:3px 8px;border-radius:999px;">{d['route']}</span>
</div>
<div style="display:flex;gap:28px;margin:10px 0 8px 0;">
  <div><div style="font-size:26px;font-weight:700;line-height:1.1;">{_fmt(d['skus'])}</div><div style="font-size:12px;color:{p['muted']};">SKUs</div></div>
  <div><div style="font-size:26px;font-weight:700;line-height:1.1;">{_k(d['qty'])}</div><div style="font-size:12px;color:{p['muted']};">qty</div></div>
</div>
{_bar([(d['ready'], GREEN), (1 - d['ready'], ORANGE)])}
<div style="display:flex;justify-content:space-between;font-size:12px;color:{p['muted']};margin-top:6px;">
  <span><span style="color:{GREEN};">●</span> {d['ready_txt']}</span>
  <span><span style="color:{ORANGE};">●</span> Needs you <b style="color:{p['text']};">{d['need_txt']}</b></span></div>
<div style="font-size:12px;color:{p['muted']};margin-top:4px;">{d['note']}</div>
<div style="font-size:13px;font-weight:600;color:{BLUE};margin:6px 0 8px 0;">{'Showing below ▾' if on else f"Review {d['label']} ›"}</div>""",
                        unsafe_allow_html=True)
            if st.button(f"Open {d['label']}", key=f"wbcardbtn_{mt}", disabled=on):
                ss.wb_type = mt
                st.rerun()

    if sel == "FIRST_ALLOCATION":
        fc = country or ("AE" if "AE" in codes else codes[0])
        if not country:
            st.caption(f"Opening packs are approved per country · showing {w.CC_NAME.get(fc, fc)} (pick a country above).")
        _fa_queue(p, fc, user)
    else:
        _transfer_queue(p, rid, sel, user, country, qs[sel])


@st.dialog("Send to SAP")
def _confirm_send(rid, ready, user, country=None):
    st.markdown(f"**{ready['qty']:,} qty · {ready['skus']:,} SKUs** from run `{rid}`"
                + (f" · {w.CC_NAME.get(country, country)} only" if country else " · all countries"))
    for _, r in ready["by_type"].iterrows():
        st.write(f"- {w.TYPE_LABEL.get(r['mt'], r['mt'])}: {int(r['qty']):,} qty · {int(r['skus']):,} SKUs")
    st.caption("Routine transfers plus the ones you approved. Changes over ±1 qty, rejections and added transfers wait "
               "for your manager and go in your next send. The SAP confirmation feed is not connected yet: the send is "
               "logged with its run ID and exported from Sent to SAP.")
    if st.button("Send now", type="primary", width="stretch"):
        with st.spinner("Sending…"):
            res = w.send_to_sap(rid, user, country)
        _flash(f"Sent {res['qty']:,} qty to SAP · send `{res['send_id']}` · open Sent to SAP to export.")
        _bump()
        st.rerun()


def _add_transfer_form(rid, user, scope):
    stores = scope["stores"]
    opts = [f"{r.store_code} {str(r.store_name).title()}" for r in stores[stores["in_run"]].itertuples()]
    src = st.selectbox("From", ["DC"] + opts, key="wb_add_from")
    dst = st.selectbox("To", opts, key="wb_add_to")
    art = st.text_input("Article (SKU)", placeholder="NKIU0249-01004", key="wb_add_art")
    size = st.text_input("Size", placeholder="M", key="wb_add_size")
    qty = st.number_input("Qty", min_value=1, max_value=200, value=2, key="wb_add_qty")
    why = st.selectbox("Reason", REASONS, key="wb_add_reason")
    if st.button("Add · goes to your manager", type="primary", width="stretch", disabled=not art):
        mt = "DC_TRANSFER" if src == "DC" else "IBT"
        w.add_transfer(rid, mt, "UAE hub (1D00/1D01)" if mt == "DC_TRANSFER" else src.split(" ")[0], dst.split(" ")[0],
                       art.strip().upper(), size.strip().upper(), int(qty), why, user)
        _flash(f"Added {art} → {dst}. It is with your manager.")
        _bump()
        st.rerun()


# --- allocator decisions (callbacks: run before the rerun, so widget state can be reset safely) ---
def _cb_decide(rid, rows: pd.DataFrame, status, reason_key, user, prefix):
    reason = ss.get(reason_key) if reason_key else None
    if status in ("CHANGED", "REJECTED") and not reason:
        _flash("Pick a reason first.")
        return
    n = w.save_decisions(rid, rows, status, reason, user)
    word = {"APPROVED": "approved", "CHANGED": "changed", "REJECTED": "rejected"}[status]
    nk = rows["article"].nunique()
    _flash(f"{nk:,} SKU{'' if nk == 1 else 's'} · {int(rows['proposed_qty'].sum()):,} qty {word}."
           + (" Changes over ±1 qty and rejections go to your manager." if status != "APPROVED" else ""))
    _cb_clear(prefix)
    _bump()


def _cb_undo(rid, rows, prefix):
    w.undo_decisions(rid, rows)
    _flash("Undone.")
    _bump()


def _transfer_queue(p, rid, mt, user, country, q):
    ver = _ver()
    names = _names(ver)
    prefix = f"wb_q_{mt}"
    if country:
        q = q[q["country"] == country]
    if q.empty:
        st.success("Nothing needs you here.")
        return
    reasons = q["review_reason"].fillna("").str.split(" · ").explode()
    counts = reasons[reasons != ""].value_counts()
    f = st.columns([2.2, 4.2, 2], vertical_alignment="center")
    show = f[0].segmented_control("Show", ["Needs you", "Decided", "All"], default="Needs you", key=f"{prefix}_show",
                                  label_visibility="collapsed", on_change=_cb_reset_page, args=(prefix,)) or "Needs you"
    chips = f[1].pills("Why", [f"{r} ({n:,})" for r, n in counts.items()], selection_mode="multi", key=f"{prefix}_chip",
                       label_visibility="collapsed", on_change=_cb_reset_page, args=(prefix,))
    stores = sorted(set(q["to_site"].astype(str)) | set(q["from_site"].astype(str)))
    store = f[2].selectbox("Store", ["All stores"] + [_store(s, names) for s in stores], key=f"{prefix}_store",
                           label_visibility="collapsed", on_change=_cb_reset_page, args=(prefix,))
    om = _open_mask(q)
    v = q[om] if show == "Needs you" else q[~om] if show == "Decided" else q
    if chips:
        keep = [c.rsplit(" (", 1)[0] for c in chips]
        v = v[v["review_reason"].fillna("").apply(lambda s: any(k in s.split(" · ") for k in keep))]
    if store != "All stores":
        code = store.split(" ")[0]
        v = v[(v["to_site"].astype(str) == code) | (v["from_site"].astype(str) == code)]
    v = v.reset_index(drop=True)
    pg, pages = _pager(prefix, len(v))
    page = v.iloc[pg * PAGE_SIZE:(pg + 1) * PAGE_SIZE]

    # bulk bar
    selset, allm = _sel(prefix), ss.get(f"{prefix}_all", False)
    chosen = v if allm else v[v["kid"].isin(selset)]
    with st.container(key="wb_bulk"):
        b = st.columns([1.3, 2.4, 1.2, 1.2, 0.9, 2.4], vertical_alignment="center")
        page_kids = page["kid"].tolist()
        all_on = bool(page_kids) and all(k in selset for k in page_kids)
        b[0].button("Unselect page" if all_on else "Select page", key=f"{prefix}_selpg", width="stretch",
                    on_click=_cb_select, args=(prefix, page_kids, not all_on))
        b[1].markdown(f"**{len(chosen):,} selected** · {chosen['article'].nunique():,} SKUs · "
                      f"{int(chosen['proposed_qty'].sum()):,} qty")
        pend = chosen[_open_mask(chosen)]
        b[2].button("Approve", type="primary", key=f"{prefix}_ok", disabled=pend.empty, width="stretch",
                    on_click=_cb_decide, args=(rid, pend, "APPROVED", None, user, prefix))
        with b[3].popover("Reject", disabled=pend.empty, width="stretch"):
            st.selectbox("Reason", REASONS, key=f"{prefix}_rr", index=None, placeholder="Pick a reason")
            st.button(f"Reject {len(pend):,} → manager", key=f"{prefix}_rejgo", type="primary",
                      on_click=_cb_decide, args=(rid, pend, "REJECTED", f"{prefix}_rr", user, prefix))
        b[4].button("Clear", key=f"{prefix}_clr", on_click=_cb_clear, args=(prefix,), width="stretch",
                    disabled=chosen.empty)
        if len(v) > len(page) and not allm:
            b[5].button(f"Select all {len(v):,} matching", key=f"{prefix}_allm", on_click=_cb_all, args=(prefix,),
                        width="stretch")
        elif allm:
            b[5].caption(f"All {len(v):,} matching transfers selected")

    hdr = st.columns([0.35, 3.3, 3.0, 1.45, 1.0, 1.8, 2.6])
    for c, t in zip(hdr[1:], ["Article", "From → to", "Qty", "Value AED", "Why it needs you", ""]):
        c.markdown(f"<span class='wb-h'>{t}</span>", unsafe_allow_html=True)
    for _, r in page.iterrows():
        _row_transfer(p, r, rid, user, names, prefix, ver)
    _pager_row(prefix, len(v), pg, pages)


def _row_transfer(p, r, rid, user, names, prefix, ver):
    kid = r["kid"]
    st_, mg = r["status"], r["mgr_decision"]
    pending = pd.isna(st_) or mg == "SENT_BACK"
    with st.container(key=f"wbrow_{kid}"):
        c = st.columns([0.35, 3.3, 3.0, 1.45, 1.0, 1.8, 2.6], vertical_alignment="center")
        _checkbox(c[0], prefix, kid, f"Select {r['article']}")
        c[1].markdown(_item(r["name"], r["article"], r["size"]), unsafe_allow_html=True)
        c[2].markdown(_route(r["from_site"], r["to_site"], names), unsafe_allow_html=True)
        base = int(r["decided_qty"]) if pd.notna(r["decided_qty"]) else int(r["proposed_qty"])
        qty = c[3].number_input("Qty", min_value=0, max_value=999, step=1, value=base, key=f"q_{kid}_{ver}",
                                label_visibility="collapsed", disabled=not pending)
        c[4].markdown(f"<span style='font-size:13px;'>{_fmt(r['value_aed']) if pd.notna(r['value_aed']) else '—'}</span>",
                      unsafe_allow_html=True)
        if pending:
            chip = _chip(r["review_reason"] or "Review")
            if mg == "SENT_BACK":
                chip = _chip("Sent back", "bad") + f"<div style='font-size:12px;opacity:.8;'>{r['mgr_note'] or ''}</div>"
        else:
            lab = {"APPROVED": ("Ready to send" if int(r["decided_qty"] or 0) == int(r["proposed_qty"]) else "Changed · ready", "ok"),
                   "CHANGED": ("Changed → manager", "info"), "REJECTED": ("Rejected → manager", "bad")}.get(st_, (st_, "muted"))
            if mg == "APPROVED":
                lab = ("Manager approved", "ok")
            elif mg == "KEPT":
                lab = ("Manager kept proposal", "ok")
            chip = _chip(*lab)
        c[5].markdown(chip, unsafe_allow_html=True)
        row = pd.DataFrame([r]).assign(new_qty=int(qty))
        with c[6]:
            a = st.columns(2)
            if not pending:
                a[1].button("Undo", key=f"u_{kid}", on_click=_cb_undo, args=(rid, row, prefix), width="stretch",
                            disabled=pd.notna(mg))
            elif int(qty) != int(r["proposed_qty"]):
                with a[0].popover("Save", width="stretch"):
                    st.caption(f"{int(r['proposed_qty'])} → {int(qty)} qty · ±{w.TOLERANCE_QTY} goes straight to SAP, "
                               f"more goes to your manager")
                    st.selectbox("Reason", REASONS, key=f"rr_{kid}", index=None, placeholder="Pick a reason")
                    st.button("Save change", key=f"sv_{kid}", type="primary",
                              on_click=_cb_decide, args=(rid, row, "CHANGED", f"rr_{kid}", user, prefix))
                a[1].caption(f"was {int(r['proposed_qty'])}")
            else:
                a[0].button("Approve", key=f"ok_{kid}", type="primary", width="stretch",
                            on_click=_cb_decide, args=(rid, row, "APPROVED", None, user, prefix))
                with a[1].popover("Reject", width="stretch"):
                    st.selectbox("Reason", REASONS, key=f"rr_{kid}", index=None, placeholder="Pick a reason")
                    st.button("Reject → manager", key=f"rj_{kid}", type="primary",
                              on_click=_cb_decide, args=(rid, row, "REJECTED", f"rr_{kid}", user, prefix))
                    st.caption(r["why"] or "")


# --- first allocation ---
def _cb_packs(country, rows, user, prefix):
    n = w.approve_packs(country, rows, user)
    _flash(f"{n:,} opening packs approved · {int(rows['pack_units'].sum()):,} qty. Released when the stock lands at the DC.")
    _cb_clear(prefix)
    _bump()


def _cb_focus(parent):
    ss.wb_fa_focus = parent


@st.cache_data(ttl=3600, show_spinner=False)
def _fa_detail(parent):
    return w.fa_detail(parent)


@st.cache_data(ttl=3600, show_spinner=False)
def _dc_name(country):
    return w.dc_name(country)


def _fa_queue(p, country, user):
    ver = _ver()
    prefix = "wb_fa"
    fa = _fa_sum((country,), ver)
    f = st.columns([2.2, 3], vertical_alignment="center")
    which = f[0].segmented_control("Show", ["Needs you", "Ready", "All"], default="Needs you", key="wb_fa_show",
                                   label_visibility="collapsed", on_change=_cb_reset_page, args=(prefix,)) or "Needs you"
    search = f[1].text_input("Find", placeholder="Find article or name", key="wb_fa_find", label_visibility="collapsed")
    rows = _fa_rows(country, {"Needs you": "need", "Ready": "ready", "All": "all"}[which], search.strip(), ver)
    rows = rows.reset_index(drop=True)
    dcn = _dc_name(country)
    st.caption(f"{w.CC_NAME.get(country, country)} · Spring 2027 plan · {fa['all_skus']:,} SKUs · {fa['pack']:,} qty in opening "
               f"packs · {fa['reserve']:,} to keep at the DC ({dcn}) · {fa['held']:,} packs held (buy below one size run). "
               f"Buy is assumed from the forecast until the buy file loads; packs release when stock lands at the DC.")
    if rows.empty:
        st.success("No opening packs in this view.")
        return
    rows["kid"] = [_kid("FA", country, x) for x in rows["parent"]]
    pg, pages = _pager(prefix, len(rows))
    page = rows.iloc[pg * PAGE_SIZE:(pg + 1) * PAGE_SIZE]
    selset = _sel(prefix)
    chosen = rows[rows["kid"].isin(selset)]
    with st.container(key="wb_fbulk"):
        b = st.columns([1.3, 3, 1.6, 0.9, 2.4], vertical_alignment="center")
        page_kids = page["kid"].tolist()
        all_on = bool(page_kids) and all(k in selset for k in page_kids)
        b[0].button("Unselect page" if all_on else "Select page", key=f"{prefix}_selpg", width="stretch",
                    on_click=_cb_select, args=(prefix, page_kids, not all_on))
        b[1].markdown(f"**{len(chosen):,} packs selected** · {int(chosen['pack_units'].sum()):,} qty")
        b[2].button("Approve packs", type="primary", key=f"{prefix}_ok", disabled=chosen.empty, width="stretch",
                    on_click=_cb_packs, args=(country, chosen, user, prefix))
        b[3].button("Clear", key=f"{prefix}_clr", on_click=_cb_clear, args=(prefix,), width="stretch", disabled=chosen.empty)
    widths = [0.35, 3.3, 1.1, 1.0, 2.9, 1.0, 1.9, 2.2]
    hdr = st.columns(widths)
    for c, t in zip(hdr[1:], ["New article", "Planned buy", "In DC now", "Opening pack · kept at DC", "Stores", "Status", ""]):
        c.markdown(f"<span class='wb-h'>{t}</span>", unsafe_allow_html=True)
    focus = ss.get("wb_fa_focus")
    for _, r in page.iterrows():
        kid = r["kid"]
        with st.container(key=f"wbrow_{kid}"):
            c = st.columns(widths, vertical_alignment="center")
            _checkbox(c[0], prefix, kid, f"Select {r['parent']}")
            c[1].markdown(f"<div style='line-height:1.25;'><b style='font-size:14px;'>{str(r['name']).title()}</b><br>"
                          f"<span style='font-size:12px;opacity:.75;'>{r['parent']} · <b>{r['season'] or 'season n/a'}</b> · "
                          f"{_fmt(r['skus'])} sizes</span></div>", unsafe_allow_html=True)
            c[2].markdown(f"<b>{_fmt(r['buy_qty'])}</b><br><span style='font-size:11px;opacity:.7;'>assumed</span>",
                          unsafe_allow_html=True)
            dcq = int(r["dc_now"] or 0)
            c[3].markdown(f"<b style='color:{GREEN if dcq else RED};'>{dcq:,}</b><br><span style='font-size:11px;opacity:.7;'>"
                          f"{'received' if dcq else 'not received'}</span>", unsafe_allow_html=True)
            buy = float(r["buy_qty"] or 0)
            c[4].markdown(_bar([(float(r["pack_units"] or 0) / buy if buy else 0, BLUE),
                                (float(r["reserve_units"] or 0) / buy if buy else 0, SOFTBLUE)], height=10)
                          + f"<div style='display:flex;justify-content:space-between;font-size:12px;opacity:.85;margin-top:3px;'>"
                            f"<span><b style='color:{BLUE};'>{_fmt(r['pack_units'])}</b> to stores</span>"
                            f"<span>{_fmt(r['reserve_units'])} kept at DC</span></div>", unsafe_allow_html=True)
            c[5].markdown(f"<span style='font-size:13px;'>{_fmt(r['stores_ranged'])} of {_fmt(r['stores_eligible'])}</span>",
                          unsafe_allow_html=True)
            ok = r["decision"] == "APPROVED" or not r["review"]
            c[6].markdown(_chip("Approved" if r["decision"] == "APPROVED" else (r["review"] or "Ready"),
                                "ok" if ok else "todo"), unsafe_allow_html=True)
            a = c[7].columns(2)
            if r["decision"] != "APPROVED":
                a[0].button("Approve", key=f"fa_ok_{kid}", type="primary", width="stretch",
                            on_click=_cb_packs, args=(country, pd.DataFrame([r]), user, prefix))
            shown = (focus or page.iloc[0]["parent"]) == r["parent"]
            a[1].button("Shown below" if shown else "Details", key=f"fa_pk_{kid}", width="stretch", disabled=shown,
                        on_click=_cb_focus, args=(r["parent"],), help="Image, look-alikes, DC stock and the pack by store and size")
    _pager_row(prefix, len(rows), pg, pages)
    fr = rows[rows["parent"] == focus] if focus else page.head(1)
    if len(fr):
        _fa_details(p, country, fr.iloc[0], dcn)


def _img(url, caption, h=180) -> str:
    if isinstance(url, str) and url:
        return (f"<img src='{url}' style='width:100%;height:{h}px;object-fit:contain;background:#FFFFFF;border-radius:10px;"
                f"border:1px solid #E4E7EC;'><div style='font-size:12px;opacity:.75;margin-top:4px;'>{caption}</div>")
    return (f"<div style='height:{h}px;display:grid;place-items:center;background:#F3F6F7;border-radius:10px;font-size:12px;"
            f"color:#5C6478;text-align:center;padding:8px;'>{caption}</div>")


def _fa_details(p, country, r, dcn):
    d = _fa_detail(str(r["parent"]))
    f, ana = d["f"], d["analogues"]
    with st.container(border=True):
        c = st.columns([1.05, 1.5, 2.6], gap="medium")
        with c[0]:
            st.markdown(_img(f.get("img"), "No image available for this article"), unsafe_allow_html=True)
            sib = int(float(f.get("siblings") or 0))
            t = lambda k: str(f.get(k) or "—").title() if f.get(k) else "—"
            facts = [("Article", "New · never sold in GMG"),
                     ("Season", r["season"] or "—"),
                     ("Division", t("division")), ("Category (ah2)", t("ah2")), ("Gender", t("gender")),
                     ("Sport", t("sport")), ("Silhouette", t("silhouette")),
                     ("RRP", f"AED {_fmt(f.get('rrp'))}" if f.get("rrp") else "—"),
                     ("Model", f"Sold before · {sib} colourways" if sib else "New model"),
                     ("Launch week (assumed)", str(f.get("launch_week") or "—")),
                     ("Forecast from", f"{f.get('tier') or '—'} · {f.get('confidence') or ''}")]
            st.markdown("".join(f"<div style='display:flex;justify-content:space-between;gap:8px;font-size:12px;margin:3px 0;'>"
                                f"<span style='opacity:.7;'>{k}</span><b style='text-align:right;'>{v}</b></div>" for k, v in facts),
                        unsafe_allow_html=True)
            st.caption("Launch week is assumed: last season's typical first sale for the division, one year on. "
                       "Replace with the Nike launch calendar.")
        with c[1]:
            buy, pack, keep = float(r["buy_qty"] or 0), float(r["pack_units"] or 0), float(r["reserve_units"] or 0)
            dcq = float(r["dc_now"] or 0)
            st.markdown(f"<div class='wb-h' style='margin-bottom:6px;'>Stock · {w.CC_NAME.get(country, country)} · DC {dcn}</div>",
                        unsafe_allow_html=True)
            steps = [("Planned buy", buy, "#20274C", f"Assumed: 13-week forecast at P80 ({_fmt(r['units_13w'])} expected). "
                                                    "Replaced by the real buy when the buy file loads."),
                     ("In the DC now", dcq, GREEN if dcq else RED,
                      "Received at the DC" if dcq else "Not received yet · nothing to send until it lands"),
                     ("Opening pack → stores", pack, BLUE, f"{_fmt(r['stores_ranged'])} stores · covers the first 3 weeks "
                                                          f"({_fmt(r['demand_3w'])} expected sales)"),
                     ("Kept at the DC", keep, SOFTBLUE, "Feeds DC transfers once the article sells")]
            mx = max(1.0, buy, pack + keep)
            st.markdown("".join(
                f"<div style='margin:8px 0;'><div style='display:flex;justify-content:space-between;font-size:13px;'>"
                f"<span>{lbl}</span><b>{int(v):,}</b></div>{_bar([(v / mx, col)], height=10)}"
                f"<div style='font-size:11px;opacity:.7;margin-top:2px;'>{note}</div></div>" for lbl, v, col, note in steps),
                unsafe_allow_html=True)
        with c[2]:
            st.markdown("<div class='wb-h' style='margin-bottom:6px;'>Look-alikes the forecast learned from</div>",
                        unsafe_allow_html=True)
            if ana.empty:
                st.caption("No look-alikes recorded for this article.")
            else:
                lc = st.columns(len(ana))
                for col, (_, x) in zip(lc, ana.iterrows()):
                    col.markdown(f"<div class='wb-look'>{_img(x['url'] if isinstance(x['url'], str) else None, 'No image', h=96)}"
                                 f"<div style='font-size:12px;font-weight:600;line-height:1.2;margin-top:4px;'>{str(x['analogue_name']).title()}</div>"
                                 f"<div style='font-size:11px;opacity:.7;'>{x['analogue']} · {str(x['ah2'] or '').title()}</div>"
                                 f"<div style='font-size:12px;margin-top:2px;'><b>{_fmt(x['analogue_units_13w'])}</b> sold in 13 wks</div>"
                                 f"<div style='font-size:11px;opacity:.7;'>first sale {str(x['analogue_first_sale'])[:10]}</div></div>",
                                 unsafe_allow_html=True)
                st.caption("Same category (ah2) always; then matched on sport, silhouette, colour family, price band and "
                           "product life cycle where available.")
        _pack_chart(p, country, r)


@st.cache_data(ttl=3600, show_spinner=False)
def _fa_grid(country, parent):
    return w.fa_pack_grid(country, parent)


def _pack_chart(p, country, r):
    g = _fa_grid(country, str(r["parent"]))
    if g.empty:
        st.caption("No stores ranged: the buy is below one full size run, so it is held at the DC.")
        return
    g = g.assign(store_label=g["store"].astype(str) + " " + g["store_name"].str.replace("NIKE-", "").str.replace("NIKE ", "")
                 .str.title(), kind=g["key_size"].map(lambda k: "Key size" if k == 1 else "Other size"))
    stores = g.groupby("store_label")["qty"].sum().sort_values(ascending=False).index.tolist()
    sizes = (g.assign(_o=pd.to_numeric(g["size"].str.extract(r"([\d.]+)")[0], errors="coerce"))
             .sort_values(["_o", "size"])["size"].drop_duplicates().tolist())
    pick = alt.selection_point(fields=["store_label"], empty=True, name="store")
    left = alt.Chart(g).mark_bar(color=BLUE, cornerRadiusEnd=3).encode(
        x=alt.X("sum(qty):Q", title="Qty"),
        y=alt.Y("store_label:N", sort=stores, title=None, axis=alt.Axis(labelLimit=260, labelOverlap=False)),
        opacity=alt.condition(pick, alt.value(1.0), alt.value(0.35)),
        tooltip=[alt.Tooltip("store_label:N", title="Store"), alt.Tooltip("grade:N", title="Grade"),
                 alt.Tooltip("sum(qty):Q", title="Qty")]
    ).add_params(pick).properties(width=430, height=max(200, 24 * len(stores)),
                                  title="Opening pack by store · click a store")
    right = alt.Chart(g).transform_filter(pick).mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X("size:N", sort=sizes, title="Size", axis=alt.Axis(labelAngle=0)),
        y=alt.Y("sum(qty):Q", title="Qty"),
        color=alt.Color("kind:N", scale=alt.Scale(domain=["Key size", "Other size"], range=[BLUE, SOFTBLUE]),
                        legend=alt.Legend(orient="bottom", title=None)),
        tooltip=[alt.Tooltip("size:N"), alt.Tooltip("sum(qty):Q", title="Qty"), alt.Tooltip("kind:N")]
    ).properties(width=430, height=300, title="Size curve · all stores, or the store you click")
    st.altair_chart(alt.hconcat(left, right, spacing=48).resolve_scale(color="independent"))
    st.caption("Click a store bar to see its size curve; click it again (or the empty space) to go back to all stores.")


# ---------- LINE MANAGER · APPROVALS ----------
def _cb_mgr(rid, rows, decision, note_key, user, prefix):
    note = ss.get(note_key) if note_key else None
    if decision == "SENT_BACK" and not note:
        _flash("Add a note for the allocator first.")
        return
    n = w.save_manager(rid, rows, decision, note, user)
    _flash(f"{n} decisions saved · the allocator sees them on Today.")
    _cb_clear(prefix)
    _bump()


def _approvals(p, rid, user, country):
    ver = _ver()
    prefix = "wb_mgr"
    q = _mgr(rid, ver)
    names = _names(ver)
    if country and len(q):
        stores = _scope(ver)["stores"]
        codes = set(stores.loc[stores["country"].map(lambda c: w.country_codes([c])[0]) == country, "store_code"].astype(str))
        q = q[q["to_site"].astype(str).isin(codes)]
    todo = q[q["mgr_decision"].isna()] if len(q) else q
    _h1(f"{todo['article'].nunique() if len(todo) else 0:,} SKUs · {int(todo['proposed_qty'].sum()) if len(todo) else 0:,} qty need you",
        f"Run `{rid}` · decide by **{w.MANAGER_BY}** so approved changes make the {w.CUTOFF} cut-off (proposed) · "
        f"changes over ±{w.TOLERANCE_QTY} qty, rejections and added transfers come to you")
    _flash_box()
    team = _team(rid, ver)
    tot = _totals(rid, ver)
    review_total = int(tot.loc[tot["country"].isna(), "review_transfers"].sum())
    if len(team):
        cols = st.columns(min(4, len(team)))
        for c, (_, t) in zip(cols, team.iterrows()):
            done = int(t["decided"])
            c.markdown(
                f"<div style='background:{p['card']};border:1px solid {p['line']};border-radius:12px;padding:12px 14px;'>"
                f"<div style='display:flex;justify-content:space-between;font-size:13px;'><b>{str(t['decided_by']).split('@')[0]}</b>"
                f"<span style='color:{p['muted']};'>{done:,} of {review_total:,} decided</span></div>"
                f"<div style='margin:8px 0;'>{_bar([(done / review_total if review_total else 0, GREEN)])}</div>"
                f"<div style='font-size:12px;color:{p['muted']};'>{int(t['to_manager'])} sent to you</div></div>",
                unsafe_allow_html=True)
    if q.empty:
        st.info("Nothing has come to you for this run yet.")
        return
    show = st.segmented_control("Show", ["To decide", "All"], default="To decide", key="wb_mgr_show",
                                label_visibility="collapsed", on_change=_cb_reset_page, args=(prefix,)) or "To decide"
    v = (todo if show == "To decide" else q).reset_index(drop=True)
    pg, pages = _pager(prefix, len(v))
    page = v.iloc[pg * PAGE_SIZE:(pg + 1) * PAGE_SIZE]
    selset = _sel(prefix)
    chosen = v[v["kid"].isin(selset)]
    with st.container(key="wb_mbulk"):
        b = st.columns([1.3, 2.2, 1.2, 1.4, 1.3, 0.9], vertical_alignment="center")
        page_kids = page["kid"].tolist()
        all_on = bool(page_kids) and all(k in selset for k in page_kids)
        b[0].button("Unselect page" if all_on else "Select page", key=f"{prefix}_selpg", width="stretch",
                    on_click=_cb_select, args=(prefix, page_kids, not all_on))
        b[1].markdown(f"**{len(chosen)} selected** · {int(chosen['proposed_qty'].sum()) if len(chosen) else 0:,} qty")
        b[2].button("Approve", type="primary", key=f"{prefix}_ok", disabled=chosen.empty, width="stretch",
                    on_click=_cb_mgr, args=(rid, chosen, "APPROVED", None, user, prefix))
        b[3].button("Keep proposal", key=f"{prefix}_keep", disabled=chosen.empty, width="stretch",
                    on_click=_cb_mgr, args=(rid, chosen, "KEPT", None, user, prefix))
        with b[4].popover("Send back", disabled=chosen.empty, width="stretch"):
            st.text_input("Note to the allocator", key=f"{prefix}_note")
            st.button("Send back with note", key=f"{prefix}_back", type="primary",
                      on_click=_cb_mgr, args=(rid, chosen, "SENT_BACK", f"{prefix}_note", user, prefix))
        b[5].button("Clear", key=f"{prefix}_clr", on_click=_cb_clear, args=(prefix,), width="stretch", disabled=chosen.empty)
    hdr = st.columns([0.35, 1.2, 3.0, 2.8, 1.6, 2.4, 3.0])
    for c, t in zip(hdr[1:], ["Type", "Article", "From → to", "Qty", "Allocator's reason", ""]):
        c.markdown(f"<span class='wb-h'>{t}</span>", unsafe_allow_html=True)
    for _, r in page.iterrows():
        kid = r["kid"]
        with st.container(key=f"wbrow_{kid}"):
            c = st.columns([0.35, 1.2, 3.0, 2.8, 1.6, 2.4, 3.0], vertical_alignment="center")
            _checkbox(c[0], prefix, kid, f"Select {r['article']}")
            c[1].markdown(_chip(w.TYPE_LABEL.get(r["movement_type"], r["movement_type"]), "muted"), unsafe_allow_html=True)
            c[2].markdown(_item(None, r["article"], r["size"]), unsafe_allow_html=True)
            c[3].markdown(_route(r["from_site"], r["to_site"], names), unsafe_allow_html=True)
            call = {"CHANGED": f"{int(r['proposed_qty'])} → <b style='color:{ORANGE};font-size:16px;'>{int(r['qty'])}</b>",
                    "REJECTED": f"{int(r['proposed_qty'])} → <b style='color:{RED};'>reject</b>",
                    "ADDED": f"<b style='color:{BLUE};font-size:16px;'>+{int(r['qty'])}</b> added"}.get(r["status"], "")
            c[4].markdown(f"<span style='font-size:13px;'>{call}</span>", unsafe_allow_html=True)
            c[5].markdown(f"<div style='font-size:13px;line-height:1.3;'>{r['reason'] or ''}<br><span style='opacity:.7;font-size:12px;'>"
                          f"{str(r['decided_by']).split('@')[0]}</span></div>", unsafe_allow_html=True)
            row = pd.DataFrame([r])
            if pd.isna(r["mgr_decision"]):
                a = c[6].columns(3)
                a[0].button("Approve", key=f"m_ok_{kid}", type="primary", width="stretch",
                            on_click=_cb_mgr, args=(rid, row, "APPROVED", None, user, prefix))
                a[1].button("Keep", key=f"m_kp_{kid}", width="stretch", help="Send the engine's proposed qty",
                            on_click=_cb_mgr, args=(rid, row, "KEPT", None, user, prefix))
                with a[2].popover("Back", width="stretch"):
                    st.text_input("Note to the allocator", key=f"m_nt_{kid}")
                    st.button("Send back", key=f"m_bk_{kid}", type="primary",
                              on_click=_cb_mgr, args=(rid, row, "SENT_BACK", f"m_nt_{kid}", user, prefix))
            else:
                lab = {"APPROVED": ("Approved", "ok"), "KEPT": ("Kept proposal", "info"),
                       "SENT_BACK": ("Sent back", "bad")}.get(r["mgr_decision"], (r["mgr_decision"], "muted"))
                c[6].markdown(_chip(*lab), unsafe_allow_html=True)
    _pager_row(prefix, len(v), pg, pages)


# ---------- SENT TO SAP ----------
def _saplog(p, rid):
    ver = _ver()
    runs = _sent_runs(ver)
    names = _names(ver)
    opts = [rid] + [r for r in runs["run_id"].tolist() if r != rid] if len(runs) else [rid]
    top = st.columns([3, 2], vertical_alignment="bottom")
    with top[0]:
        _h1("Sent to SAP", "Every transfer keeps its run ID, decision and approver. SAP confirmation feed not connected "
                           "yet: status shows Sent until SAP returns order numbers.")
    pick = top[1].selectbox("Run", opts, key="wb_sap_run", format_func=lambda r: f"Run {r}" + (" · latest" if r == rid else ""))
    s = _sends(pick, ver)
    ns = _not_sent(pick, ver)
    if s.empty:
        st.info("Nothing sent to SAP for this run yet. The allocator sends from Today.")
    else:
        bt = s.groupby("movement_type").agg(skus=("article", "nunique"), qty=("qty", "sum")).reindex(w.TYPES).fillna(0)
        mx = max(1.0, float(bt["qty"].max()))
        rows = "".join(
            f"<div style='display:grid;grid-template-columns:130px 1fr 190px;gap:8px;align-items:center;font-size:13px;margin:6px 0;'>"
            f"<b>{w.TYPE_LABEL[mt]}</b>{_bar([(r['qty'] / mx, BLUE)], height=12)}"
            f"<span style='text-align:right;color:{p['muted']};'><b style='color:{p['text']};'>{int(r['qty']):,}</b> qty · "
            f"{int(r['skus']):,} SKUs{' (on DC receipt)' if mt == 'FIRST_ALLOCATION' else ''}</span></div>"
            for mt, r in bt.iterrows())
        c = st.columns([1.4, 1])
        c[0].markdown(f"<div style='background:{p['card']};border:1px solid {p['line']};border-radius:12px;padding:14px 18px;'>"
                      f"<div style='font-size:26px;font-weight:700;'>{int(s['qty'].sum()):,} <span style='font-size:13px;"
                      f"font-weight:400;color:{p['muted']};'>qty · {s['article'].nunique():,} SKUs</span></div>{rows}</div>",
                      unsafe_allow_html=True)
        sends = s.groupby("send_id").agg(at=("sent_at", "min"), by=("sent_by", "first"), skus=("article", "nunique"),
                                         qty=("qty", "sum")).reset_index().sort_values("at")
        steps = "".join(f"<div style='font-size:13px;margin:4px 0;'><b>{str(r['at'])[11:16]}</b> · {str(r['by']).split('@')[0]} sent "
                        f"{int(r['qty']):,} qty · {int(r['skus']):,} SKUs <code>{r['send_id']}</code></div>"
                        for _, r in sends.iterrows())
        c[1].markdown(f"<div style='background:{p['card']};border:1px solid {p['line']};border-radius:12px;padding:14px 18px;'>"
                      f"<div style='font-size:12px;font-weight:700;letter-spacing:.06em;color:{p['muted']};'>TRACE · RUN {pick}</div>"
                      f"{steps}<div style='font-size:13px;margin:4px 0;color:{p['muted']};'>{len(ns)} not sent (below)</div></div>",
                      unsafe_allow_html=True)
        f = st.columns([1.5, 3, 1.6], vertical_alignment="center")
        typ = f[0].selectbox("Type", ["All types"] + [w.TYPE_LABEL[t] for t in w.TYPES], key="wb_sap_type",
                             label_visibility="collapsed")
        find = f[1].text_input("Find", placeholder="Find article or store", key="wb_sap_find", label_visibility="collapsed")
        v = s if typ == "All types" else s[s["movement_type"].map(w.TYPE_LABEL) == typ]
        if find:
            t = find.upper()
            v = v[v["article"].astype(str).str.upper().str.contains(t) | v["from_site"].astype(str).str.contains(t)
                  | v["to_site"].astype(str).str.contains(t)]
        exp = v.assign(movement_type=v["movement_type"].map(w.TYPE_LABEL))
        f[2].download_button(f"Export CSV · {exp['qty'].sum():,.0f} qty", dbio.to_csv_bytes(exp),
                             file_name=f"sap_transfers_{pick}.csv", mime="text/csv", type="primary", width="stretch")
        routes = (v.groupby(["movement_type", "from_site", "to_site"])
                  .agg(skus=("article", "nunique"), qty=("qty", "sum")).reset_index().sort_values("qty", ascending=False))
        st.markdown(f"##### By route · one SAP order per route · top {min(25, len(routes))} of {len(routes):,}")
        rmx = max(1.0, float(routes["qty"].max())) if len(routes) else 1.0
        html = "".join(
            f"<div style='display:grid;grid-template-columns:110px 1.4fr 1.4fr 1fr 150px 160px;gap:10px;align-items:center;"
            f"padding:8px 4px;border-bottom:1px solid {p['row']};font-size:13px;'>"
            f"{_chip(w.TYPE_LABEL[r['movement_type']], 'muted')}<span style='opacity:.8;'>{_store(r['from_site'], names)}</span>"
            f"<b>→ {_store(r['to_site'], names)}</b>{_bar([(r['qty'] / rmx, BLUE)], height=10)}"
            f"<span style='text-align:right;'><b>{int(r['qty']):,}</b> qty · {int(r['skus']):,} SKUs</span>"
            f"<span style='text-align:right;'>{_chip('Sent · awaiting SAP', 'info')}</span></div>"
            for _, r in routes.head(25).iterrows())
        st.markdown(f"<div style='background:{p['card']};border:1px solid {p['line']};border-radius:12px;padding:4px 14px;'>{html}</div>",
                    unsafe_allow_html=True)
    if len(ns):
        st.markdown("##### Not sent")
        html = ""
        for _, r in ns.head(50).iterrows():
            back = r["mgr_decision"] == "SENT_BACK"
            lab = "Sent back" if back else {"REJECTED": "Rejected", "CHANGED": "Waiting for manager",
                                            "ADDED": "Waiting for manager"}.get(r["status"], r["status"])
            kind = "bad" if back or r["status"] == "REJECTED" else "todo"
            html += (f"<div style='display:grid;grid-template-columns:110px 1.6fr 2fr 80px 1.6fr 1.8fr;gap:10px;align-items:center;"
                     f"padding:8px 4px;border-bottom:1px solid {p['row']};font-size:13px;'>"
                     f"{_chip(w.TYPE_LABEL.get(r['movement_type'], r['movement_type']), 'muted')}<span><b>{r['article']}</b> · {r['size']}</span>"
                     f"<span>{_store(r['from_site'], names)} → {_store(r['to_site'], names)}</span><span>{_fmt(r['proposed_qty'])} qty</span>"
                     f"{_chip(lab, kind)}<span style='opacity:.8;'>{r['reason'] or ''} {('· ' + r['mgr_note']) if r['mgr_note'] else ''}</span></div>")
        st.markdown(f"<div style='background:{p['card']};border:1px solid {p['line']};border-radius:12px;padding:4px 14px;'>{html}</div>",
                    unsafe_allow_html=True)


# ---------- EXECUTIVE · IMPACT ----------
def _impact(p, rid, country):
    ver = _ver()
    s = _series(ver)
    tt = _totals(rid, ver)
    tot = (tt[tt["country"].isna()] if country is None else tt[tt["country"] == country]).set_index("movement_type") \
        .reindex(["DC_TRANSFER", "IBT"]).fillna(0)
    scope = _scope(ver)
    codes = w.country_codes(scope["countries"])
    fa = _fa_sum(tuple([country] if country else codes), ver)
    where = w.CC_NAME.get(country, country) if country else f"{len(codes)} countries · {scope['in_run']} stores"
    _h1(f"Today: {_fmt(tot['skus'].sum())} SKUs · {_fmt(tot['qty'].sum())} qty to move",
        f"{where} · DC transfer and IBT from run `{rid}`; first allocation is the provisional Spring 2027 plan")
    share = float(tot["review_qty"].sum()) / float(tot["qty"].sum() or 1)
    k = st.columns(4)
    tiles = [("1 · First allocation", fa["all_skus"], fa["pack"], "Spring 2027 opening packs · provisional"),
             ("2 · DC transfer", tot.loc["DC_TRANSFER", "skus"], tot.loc["DC_TRANSFER", "qty"], "DC → store, today"),
             ("3 · IBT", tot.loc["IBT", "skus"], tot.loc["IBT", "qty"], "Store → store, today")]
    for c, (lbl, skus, qty, sub) in zip(k, tiles):
        c.markdown(f"<div style='background:{p['card']};border:1px solid {p['line']};border-radius:12px;padding:14px 16px;'>"
                   f"<div style='font-size:12px;font-weight:600;color:{p['muted']};'>{lbl}</div>"
                   f"<div style='display:flex;gap:24px;margin-top:6px;'>"
                   f"<div><div style='font-size:26px;font-weight:700;line-height:1.1;'>{_fmt(skus)}</div><div style='font-size:12px;color:{p['muted']};'>SKUs</div></div>"
                   f"<div><div style='font-size:26px;font-weight:700;line-height:1.1;'>{_k(qty)}</div><div style='font-size:12px;color:{p['muted']};'>qty</div></div></div>"
                   f"<div style='font-size:12px;color:{p['muted']};margin-top:6px;'>{sub}</div></div>", unsafe_allow_html=True)
    k[3].markdown(f"<div style='background:{p['card']};border:1px solid {p['line']};border-radius:12px;padding:14px 16px;'>"
                  f"<div style='font-size:12px;font-weight:600;color:{p['muted']};'>Qty decided by a person</div>"
                  f"<div style='font-size:26px;font-weight:700;line-height:1.1;margin-top:6px;'>{share:.0%}</div>"
                  f"<div style='margin:8px 0 6px 0;'>{_bar([(1 - share, GREEN), (share, ORANGE)])}</div>"
                  f"<div style='font-size:12px;color:{p['muted']};'>{_fmt(tot['review_qty'].sum())} of {_fmt(tot['qty'].sum())} qty · rest by guardrails</div></div>",
                  unsafe_allow_html=True)
    if len(s):
        m = st.segmented_control("Measure", ["Qty per day", "SKUs per day", "IBT expected uplift"], default="Qty per day",
                                 key="wb_imp_m", label_visibility="collapsed") or "Qty per day"
        if m == "IBT expected uplift":
            d = s[["day", "ibt_uplift"]].rename(columns={"ibt_uplift": "AED"}).melt("day")
        elif m == "SKUs per day":
            d = s[["day", "dc_skus", "ibt_skus"]].rename(columns={"dc_skus": "DC transfer", "ibt_skus": "IBT"}).melt("day")
        else:
            d = s[["day", "dc_qty", "ibt_qty"]].rename(columns={"dc_qty": "DC transfer", "ibt_qty": "IBT"}).melt("day")
        st.altair_chart(alt.Chart(d).mark_line(point=True).encode(
            x=alt.X("day:T", title=None), y=alt.Y("value:Q", title=None),
            color=alt.Color("variable:N", scale=alt.Scale(range=[BLUE, ORANGE]), legend=alt.Legend(orient="top", title=None)),
            tooltip=["day:T", "variable", alt.Tooltip("value:Q", format=",.0f")]).properties(height=280), width="stretch")
        note = "Each point is one daily run, traceable by its run ID."
        if s["run_id"].isin(_dev_runs(ver)).any():
            note = "The latest point is the dev run on all 71 stores in 6 countries; earlier points are 21 UAE stores. " + note
        st.caption(note + " Expected uplift is provisional (AED 5 a qty movement cost placeholder).")
    _coverage_section(p, rid, ver)
    st.markdown("##### Outcomes · measured from go-live")
    o = st.columns(4)
    for c, (lbl, sub) in zip(o, [("IBT sell-through, 4 weeks", "vs the qty had it stayed"),
                                 ("DC transfers that kept a size in stock", "store × size"),
                                 ("Changes that beat the proposal", "allocator reason vs outcome"),
                                 ("Opening pack sell-through, 3 weeks", "first allocation vs forecast")]):
        c.markdown(f"<div style='border:1px dashed {p['line']};border-radius:12px;padding:14px 16px;'>"
                   f"<div style='font-size:12px;color:{p['muted']};'>{lbl}</div><b>[after go-live]</b>"
                   f"<div style='font-size:12px;color:{p['muted']};'>{sub}</div></div>", unsafe_allow_html=True)


def _coverage_section(p, rid, ver):
    st.markdown("##### SKUs evaluated")
    with st.spinner("Counting SKUs…"):
        cov = _coverage(rid, ver)
        mf = _master(ver)
        gap = _gap(ver)
    if cov.empty:
        st.caption("No coverage data for this run.")
        return
    num = ["with_stock", "in_stores", "at_dc", "evaluated", "dc_evaluated", "with_ibt", "with_dc", "with_transfer",
           "dc_only_not_evaluated"]
    t = cov[num].sum()
    steps = [("Nike-family SKUs in the article master", mf.get("master_articles", 0), BLUE),
             ("With stock anywhere in GMG today", mf.get("stocked_anywhere", 0), BLUE),
             ("With stock in the run's stores or DCs", t["with_stock"], BLUE),
             ("Evaluated by the engine", t["evaluated"], GREEN),
             ("Got a transfer today", t["with_transfer"], ORANGE)]
    top = max(1, steps[0][1])
    rows = "".join(
        f"<div style='display:grid;grid-template-columns:300px 1fr 110px;gap:10px;align-items:center;font-size:13px;margin:6px 0;'>"
        f"<span>{lbl}</span>{_bar([(max(v / top, 0.004), col)], height=12)}"
        f"<b style='text-align:right;font-variant-numeric:tabular-nums;'>{int(v):,}</b></div>"
        for lbl, v, col in steps)
    st.markdown(f"<div style='background:{p['card']};border:1px solid {p['line']};border-radius:12px;padding:12px 18px;'>"
                f"{rows}</div>", unsafe_allow_html=True)

    # why evaluated < with stock
    gap_n = int(t["with_stock"] - t["evaluated"])
    st.markdown(f"##### Why {gap_n:,} SKUs with stock are not evaluated")
    st.caption("The engine moves stock to stores that hold or sell the article. These SKUs sit only at the DC: no stock and "
               "no sales in any Nike store in the run. The DC is shared with SSS.")
    if len(gap):
        order = ["2026: no sales anywhere in 180 days", "2026: sold in Nike stores, now out of stock",
                 "2026: sells only in SSS and other banners", "2025 or older", "Spring 2027: in the opening packs"]
        what = {"2026: no sales anywhere in 180 days": ("Gap · never allocated: first-allocation candidates", RED),
                "2026: sold in Nike stores, now out of stock": ("Gap · fell out of the 90-day demand window: replenish", RED),
                "2026: sells only in SSS and other banners": ("By design · SSS range", GREEN),
                "2025 or older": ("Aged DC stock · clearance decision", ORANGE),
                "Spring 2027: in the opening packs": ("Covered by first allocation", GREEN)}
        g = gap.set_index("reason").reindex([x for x in order if x in set(gap["reason"])]).reset_index()
        gm = max(1.0, float(g["skus"].max()))
        html = "".join(
            f"<div style='display:grid;grid-template-columns:330px 1fr 190px 330px;gap:10px;align-items:center;font-size:13px;margin:7px 0;'>"
            f"<span>{r['reason']}</span>{_bar([(r['skus'] / gm, what[r['reason']][1])], height=12)}"
            f"<span style='text-align:right;'><b>{int(r['skus']):,}</b> SKUs · {int(r['qty']):,} qty</span>"
            f"<span style='color:{what[r['reason']][1]};font-weight:600;'>{what[r['reason']][0]}</span></div>"
            for _, r in g.iterrows())
        st.markdown(f"<div style='background:{p['card']};border:1px solid {p['line']};border-radius:12px;padding:10px 18px;'>"
                    f"{html}</div>", unsafe_allow_html=True)

    dim = st.segmented_control("Group by", ["Category", "Brand", "Gender", "Division"], default="Category",
                               key="wb_cov_dim", label_visibility="collapsed") or "Category"
    col = dim.lower()
    gg = cov.groupby(col)[num].sum().sort_values("with_stock", ascending=False).head(15).reset_index()
    gg["Got a transfer"] = gg["with_transfer"]
    gg["Evaluated · nothing to move"] = gg["evaluated"] - gg["with_transfer"]
    gg["Not evaluated · DC only"] = gg["with_stock"] - gg["evaluated"]
    d = gg.melt(id_vars=[col], value_vars=["Got a transfer", "Evaluated · nothing to move", "Not evaluated · DC only"],
                var_name="status", value_name="skus")
    st.altair_chart(alt.Chart(d).mark_bar().encode(
        y=alt.Y(f"{col}:N", sort=gg[col].tolist(), title=None),
        x=alt.X("skus:Q", title="SKUs with stock", stack="zero"),
        color=alt.Color("status:N", scale=alt.Scale(domain=["Got a transfer", "Evaluated · nothing to move",
                                                           "Not evaluated · DC only"], range=[ORANGE, GREEN, "#C9CED8"]),
                        legend=alt.Legend(orient="top", title=None)),
        order=alt.Order("status:N", sort="ascending"),
        tooltip=[col, "status", alt.Tooltip("skus:Q", format=",")]).properties(height=max(220, 26 * len(gg)),
                                                                              title=f"SKUs by {col} (top 15)"),
        width="stretch")


# ---------- LINE MANAGER · RULES ----------
def _rules_page(p, rid):
    ver = _ver()
    _h1("Rules the engine follows", "Read-only in this preview. Changes apply from the next run and are logged below.")
    scope = _scope(ver)
    t = st.tabs(["Opening pack", "Size rules", "DC transfer and IBT", "What needs a person", "Stores", "Change log"])
    with t[0]:
        st.dataframe(pd.DataFrame(w.PACK_SETTINGS, columns=["Setting", "Value", "Unit", "What it does"]),
                     hide_index=True, width="stretch")
        fa = _fa_sum(tuple(w.country_codes(scope["countries"])), ver)
        st.caption(f"Spring 2027, {len(scope['countries'])} countries: {fa['all_skus']:,} SKUs · {fa['pack']:,} qty to "
                   f"stores · {fa['reserve']:,} kept at the DC · {fa['held']:,} packs held · {fa.get('buy_source') or ''}")
    sizes, settings, log = _rules(ver)
    with t[1]:
        flagged = int((sizes["check"] != "").sum())
        st.caption(f"{len(sizes)} size groups · {flagged} flagged. A store shows a style full-size when it holds more key "
                   f"sizes than the threshold.")
        st.dataframe(sizes.rename(columns={"division": "Division", "category": "Category", "gender": "Gender",
                                           "key_sizes": "Key sizes", "full_size_when_more_than": "Full-size when more than",
                                           "sizes": "Sizes", "check": "Check"}),
                     hide_index=True, width="stretch", height=420)
    with t[2]:
        st.dataframe(settings.rename(columns={"param_name": "Setting", "value": "Value", "notes": "What it does"}),
                     hide_index=True, width="stretch", height=520)
    with t[3]:
        st.dataframe(pd.DataFrame([
            ("First allocation: low-confidence look-alikes or buy below one size run", "always", "Allocator"),
            ("Transfer worth AED 1,000 or more", f"AED {int(dbio.REVIEW_MIN_AED):,}", "Allocator"),
            ("Transfer of 6 qty or more", f"{int(dbio.REVIEW_MIN_QTY)} qty", "Allocator"),
            ("DC transfer: full-size top-up", "always", "Allocator"),
            ("Allocator changes qty by more than", f"±{w.TOLERANCE_QTY} qty (proposed)", "Line manager"),
            ("Allocator rejects or adds a transfer", "always", "Line manager"),
            ("Store list change", "always (proposed)", "Second approver"),
        ], columns=["Rule", "Threshold", "Goes to"]), hide_index=True, width="stretch")
    with t[4]:
        by = scope["by_country"].rename(columns={"country": "Country", "baseline": "Stores in baseline", "in_run": "In the run"})
        st.markdown(f"**{scope['in_run']} of {scope['baseline']} stores are in the run.** The engine only uses stores on this "
                    f"list (cfg_store_grade).")
        st.dataframe(by, hide_index=True, width="stretch")
        miss = scope["stores"][~scope["stores"]["in_run"]]
        if len(miss):
            st.markdown(f"##### Not in the run · {len(miss)}")
            st.dataframe(miss.drop(columns="in_run").rename(columns={"country": "Country", "store_code": "Store",
                                                                      "store_name": "Name", "grade": "Grade"}),
                         hide_index=True, width="stretch", height=300)
    with t[5]:
        if log.empty:
            st.caption("No change history available.")
        else:
            st.dataframe(log.rename(columns={"timestamp": "When", "operation": "Change", "userName": "By",
                                             "rows_written": "Rows", "table": "Table"}),
                         hide_index=True, width="stretch", height=420)
