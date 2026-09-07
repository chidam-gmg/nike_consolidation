"""Nike Store-to-Store Consolidation — review & export UI (Streamlit approximation of the
Claude Design mock). Thin front-end: set parameters → trigger the Databricks pipeline →
review recommended moves → export. No business logic here; the engine (00-06) does it all.

GMG Brand Book V6, INFORM end: Sapphire #20274C, Cobalt #2658F2, Light Silver #E9EFEF,
Burnt Copper #B15940. Economics columns are PROVISIONAL (movement cost / probability-to-sell pending).
"""
import io
import time
import uuid
from datetime import datetime, timezone, timedelta
import pandas as pd
import streamlit as st

import databricks_io as dbio


def human_ts(iso: str) -> str:
    """ISO UTC timestamp -> 'DD Mon YYYY, HH:MM GST' (UAE local, UTC+4)."""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=4)))
        return dt.strftime("%d %b %Y, %H:%M") + " GST"
    except Exception:
        return str(iso)


def commafy(frame, cols):
    """Format numeric columns as thousands-separated strings (Streamlit 'localized' preset isn't
    supported here, so we format in pandas and render as text)."""
    for c in cols:
        if c in frame.columns:
            frame[c] = frame[c].map(lambda v: f"{v:,.0f}" if pd.notna(v) else "")
    return frame


@st.cache_data(show_spinner=False)
def to_xlsx(frame):
    """Cached Excel bytes (keyed by frame content) — avoids rebuilding on every row-click rerun."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        frame.to_excel(w, index=False, sheet_name="data")
    return buf.getvalue()


# NB: run_id is part of every cache key — the app process is SHARED across users, so a per-run key
# prevents one user's cached slice being served to another viewing a different run.
@st.cache_data(show_spinner=False)
def get_brands(computed_at: str, run_id=None):
    """Cached distinct brands (article-master RF_BNDID) present in the run — for the Brand filter."""
    try:
        return dbio.load_brands(run_id=run_id)
    except Exception:
        return []


@st.cache_data(show_spinner=False)
def get_nm_summary(computed_at: str, run_id=None):
    """Cached not-moved summary (by reason × country), refreshed per run."""
    return dbio.load_not_moved_summary(run_id=run_id)


@st.cache_data(show_spinner="Loading…")
def get_nm_slice(computed_at, country, reason, article, brand=None, run_id=None):
    """Cached bounded slice of not-moved rows for the current filter."""
    return dbio.load_not_moved(country, reason, article=article, brand=brand, run_id=run_id)


@st.cache_data(show_spinner=False)
def get_held_summary(computed_at: str, run_id=None):
    """Cached held summary (by reason × country), refreshed per run."""
    return dbio.load_held_summary(run_id=run_id)


@st.cache_data(show_spinner="Loading…")
def get_held_slice(computed_at, country, reason, article, run_id=None):
    """Cached bounded slice of held rows for the current filter."""
    return dbio.load_held(country, reason, article=article, run_id=run_id)


def full_csv_ui(col, key, loader, fname, sig, gz_loader=None):
    """Two-step full-CSV export: a Prepare button fetches the FULL (unbounded) filtered table once, then a
    Download button serves it — so we never fetch the big table on an ordinary rerun. Re-prepare on filter
    change. A full-width st.status box (not a top-of-page spinner) shows live progress since the fetch can
    take a minute or two for the big not_moved table.
    `gz_loader` (optional): returns (gzip_bytes, row_count) already streamed+compressed — used for the very
    large combined export so we never hold the whole result set / a DataFrame in memory."""
    if col.button("Prepare full CSV", key=key + "_prep", use_container_width=True):
        status = st.status("Preparing full export — this can take a minute for large tables…",
                           expanded=True)
        try:
            t0 = time.time()
            status.write("① Querying Databricks for the full filtered table…")
            if gz_loader is not None:
                # streamed straight to gzip — bounded memory + retry on dropped chunk downloads
                data, nrows = gz_loader()
                out_name, mime = fname + ".gz", "application/gzip"
                status.write(f"② Streamed **{nrows:,} rows** in {int(time.time() - t0)}s (gzip).")
            else:
                df = loader()
                # Large exports (e.g. full not_moved ~565K rows / ~290MB) exceed Streamlit's ~200MB websocket
                # download limit and silently never surface a button → gzip them (≈8-10x smaller).
                big = len(df) > 50_000
                nrows = len(df)
                status.write(f"② Fetched **{nrows:,} rows** in {int(time.time() - t0)}s — "
                             f"{'gzip-' if big else ''}encoding CSV…")
                if big:
                    data, out_name, mime = dbio.to_csv_gz_bytes(df), fname + ".gz", "application/gzip"
                else:
                    data, out_name, mime = dbio.to_csv_bytes(df), fname, "text/csv"
            st.session_state[key] = (sig, data, nrows, out_name, mime)
            mb = len(data) / 1_048_576
            gzd = out_name.endswith(".gz")
            status.update(label=f"✓ Ready — {nrows:,} rows ({mb:.1f} MB{' gzipped' if gzd else ''}) in "
                                f"{int(time.time() - t0)}s. Click **Download all** below."
                                + (" (.gz — unzip to open)" if gzd else ""),
                          state="complete", expanded=False)
        except Exception as e:
            status.update(label=f"Export failed: {e}", state="error")
    saved = st.session_state.get(key)
    if saved and saved[0] == sig:
        col.download_button(f"⬇ Download all ({saved[2]:,} rows)", data=saved[1], file_name=saved[3],
                            mime=saved[4], key=key + "_dl", use_container_width=True)
    elif saved:
        col.caption("Filters changed — Prepare again.")

# ---------- palette ----------
SAPPHIRE, COBALT, SILVER, COPPER = "#20274C", "#2658F2", "#E9EFEF", "#B15940"

st.set_page_config(page_title="Nike Consolidation Engine", layout="wide", page_icon="⇄")

st.markdown(f"""
<style>
  .stApp {{ background:#FFFFFF; }}
  header[data-testid="stHeader"] {{ background:transparent; height:0; }}
  [data-testid="stToolbar"] {{ display:none; }}
  h1,h2,h3,h4 {{ color:{SAPPHIRE}; font-family:Georgia,'Recoleta',serif; }}
  .block-container {{ padding-top:1rem; max-width:1500px; }}
  label, .stMarkdown, .stCaption {{ color:{SAPPHIRE}; }}
  .kpi {{ border-radius:12px; padding:13px 15px; min-height:84px; overflow:hidden; }}
  .kpi .lblrow {{ display:flex; align-items:center; gap:4px; height:15px; overflow:hidden; }}
  .kpi .lbl {{ font-size:11px; font-weight:600; color:#5C6478; white-space:nowrap;
               overflow:hidden; text-overflow:ellipsis; min-width:0; }}
  .kpi .val {{ font-size:20px; font-weight:700; font-variant-numeric:tabular-nums; line-height:1.2;
               white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-top:4px; }}
  .prov {{ background:#FBEADB; color:#9C4B35; border-radius:10px; padding:1px 6px;
           font-size:9px; font-weight:700; flex:0 0 auto; white-space:nowrap; }}
  .topbar {{ display:flex; align-items:center; gap:14px; border-bottom:1px solid #E1E7E8;
             padding-bottom:10px; margin-bottom:6px; }}
  .eyebrow {{ font-size:10px; letter-spacing:.14em; font-weight:700; color:#8891A6; }}
  div[data-testid="stMetricValue"] {{ font-variant-numeric:tabular-nums; }}
</style>
""", unsafe_allow_html=True)

# ---------- header ----------
try:
    from logos import GMG_LOGO, NIKE_LOGO          # embedded base64 (robust to asset sync)
except Exception:
    GMG_LOGO = NIKE_LOGO = ""

st.markdown(
    f"<div class='topbar'>"
    f"<img src='{GMG_LOGO}' style='height:30px;'/>"
    f"<div style='width:1px;height:26px;background:#E1E7E8;'></div>"
    f"<img src='{NIKE_LOGO}' style='height:26px;border-radius:4px;'/>"
    f"<div style='width:1px;height:26px;background:#E1E7E8;'></div>"
    f"<div><div class='eyebrow'>NIKE · STORE-TO-STORE</div>"
    f"<div style='font-family:Georgia,serif;font-size:22px;color:{SAPPHIRE};'>Consolidation engine</div></div>"
    f"<div style='flex:1'></div><div style='font-size:11px;color:#8891A6;'>● Databricks</div></div>",
    unsafe_allow_html=True,
)

# ---------- session ----------
ss = st.session_state
ss.setdefault("run_state", "empty")      # empty | running | results | no-results | error
ss.setdefault("recs", None)
ss.setdefault("stats", {})
ss.setdefault("run_id", None)
ss.setdefault("run_url", None)
ss.setdefault("run_started", None)
ss.setdefault("last_elapsed", None)
ss.setdefault("error_msg", "")

# On a fresh session (e.g. after refresh), reconnect to reality. Streamlit session state resets on
# refresh/reconnect, so (1) if a job is CURRENTLY running, adopt it and show the status card; else
# (2) load the latest computed results so the app doesn't "forget" a completed run.
if ss.run_state == "empty" and not ss.get("_init_loaded"):
    ss["_init_loaded"] = True
    try:
        latest = dbio.latest_run()
        if latest and latest.get("life_cycle") in ("RUNNING", "PENDING", "QUEUED"):
            ss.run_id = latest["run_id"]; ss.run_url = latest.get("run_url")
            ss.run_started = time.time() - (latest.get("elapsed") or 0)
            ss.run_state = "running"
    except Exception:
        pass
if ss.recs is None and ss.run_state == "empty" and not ss.get("_results_loaded"):
    ss["_results_loaded"] = True
    try:
        stats = dbio.load_run_stats()
        if stats and int(float(stats.get("recommended_moves", 0) or 0)) > 0:
            ss.recs = dbio.load_recommendations()
            ss.stats = stats
            ss.run_state = "results"
    except Exception:
        pass  # no results yet, or token not ready — stay on empty prompt

DEFAULTS = dict(ROS_WINDOW_DAYS=14, IN_TRANSIT_LOOKBACK_DAYS=14, SLOW_MOVING_THRESHOLD_DAYS=30,
                NEW_TO_STORE_DAYS=14, PROTECT_COVER_DAYS=7, TARGET_COVER_DAYS=14)

# ---------- run panel ----------
with st.expander("Run parameters", expanded=(ss.run_state in ("empty", "error"))):
    st.caption("Defaults to all countries — scope a run with **Countries to run** below. Moves are intra-country only.")
    st.markdown("**Sales & cover**")
    c = st.columns(3)
    p = {}
    p["ROS_WINDOW_DAYS"] = c[0].number_input("ROS window (days)", 1, 365, DEFAULTS["ROS_WINDOW_DAYS"])
    p["IN_TRANSIT_LOOKBACK_DAYS"] = c[1].number_input("In-transit look-back (days)", 1, 365, DEFAULTS["IN_TRANSIT_LOOKBACK_DAYS"])
    p["SLOW_MOVING_THRESHOLD_DAYS"] = c[2].number_input("Slow-moving threshold (days)", 1, 365, DEFAULTS["SLOW_MOVING_THRESHOLD_DAYS"])
    p["NEW_TO_STORE_DAYS"] = c[0].number_input("New-to-store (days)", 1, 365, DEFAULTS["NEW_TO_STORE_DAYS"])
    p["PROTECT_COVER_DAYS"] = c[1].number_input("Protect cover (days)", 1, 365, DEFAULTS["PROTECT_COVER_DAYS"])
    p["TARGET_COVER_DAYS"] = c[2].number_input("Target cover (days)", 1, 365, DEFAULTS["TARGET_COVER_DAYS"])

    cover_err = p["PROTECT_COVER_DAYS"] >= p["TARGET_COVER_DAYS"]
    if cover_err:
        st.error("Protect cover must be less than target cover — adjust before running.")

    st.markdown("**Configuration**")
    st.caption("Nike brand list and category converter are maintained as engine tables "
               "(`sports_analytics_test.nike_consolidation.cfg_brand_mapping` / `cfg_category_converter`) — "
               "no upload needed. The active store list, size-curve and BU-eligibility can be replaced below.")
    uc = st.columns(3)
    up_size = uc[0].file_uploader("Full / cut-size definition", type="csv", key="up_size")
    up_bu = uc[1].file_uploader("BU-eligibility matrix", type="csv", key="up_bu")
    up_stores = uc[2].file_uploader("Active store list (grades)", type=["csv", "xlsx"], key="up_stores")
    # active store list — the authoritative universe + grade source; download current, upload to replace
    _sl = None
    try:
        _sl = dbio.load_store_grades()
        uc[2].download_button(f"⬇ Download latest store list ({len(_sl)})", data=dbio.to_csv_bytes(_sl),
                              file_name="active_store_list.csv", mime="text/csv", key="dl_stores",
                              use_container_width=True)
    except Exception:
        uc[2].caption("Store list unavailable (sign-in/consent may be needed).")
    uc[2].caption("The store list is the shared, governed store **master** — edit it only to add/close a store. "
                  "To run a subset of countries, use **Countries to run** below (that does NOT edit the list).")

    # ISO2 for the engine COUNTRY filter (it matches store-master ISO2, not the list's display names)
    _CTRY_ISO = {"UAE": "AE", "KSA": "SA", "KWT": "KW", "KUWAIT": "KW", "BAH": "BH", "BAHRAIN": "BH",
                 "OMN": "OM", "OMAN": "OM", "QTR": "QA", "QATAR": "QA", "EGY": "EG", "EGYPT": "EG"}
    _sl_countries = []
    run_countries = []
    if _sl is not None:
        _sl_countries = sorted(_sl["COUNTRY"].dropna().astype(str).str.strip().unique().tolist())
        _prov = dbio.store_list_provenance()
        st.caption(f"📋 Active store list: **{len(_sl)} stores** across {', '.join(_sl_countries)}  ·  "
                   f"last changed by **{_prov['user']}** on {human_ts(_prov['ts'])}")
        rc = st.columns([3, 1])
        run_countries = rc[0].multiselect(
            "Countries to run", _sl_countries, default=_sl_countries, key="run_countries",
            help="Scope THIS run to selected countries. Ephemeral — it does not edit the shared store list.")
        if rc[1].button("↺ Reset store list to baseline", key="reset_stores", use_container_width=True):
            try:
                n = dbio.reset_store_grades_to_baseline()
                st.success(f"Store list reset to governed baseline ({n} stores)."); st.rerun()
            except Exception as e:
                st.error(f"Reset failed: {e}")

    running = ss.run_state == "running"
    if st.button("▶ Run consolidation", type="primary", disabled=cover_err or running,
                 use_container_width=True):
        with st.spinner("Submitting job…"):
            try:
                # apply any uploaded config first (brand list + category converter are engine-maintained tables)
                if up_bu is not None:
                    dbio.replace_config_table("cfg_bu_eligibility", pd.read_csv(up_bu),
                                              bool_cols=["allowed"])
                if up_size is not None:
                    dbio.replace_config_table("cfg_size_curve", pd.read_csv(up_size))
                if up_stores is not None:
                    _sdf = pd.read_excel(up_stores) if up_stores.name.lower().endswith("xlsx") else pd.read_csv(up_stores)
                    dbio.replace_store_grades(_sdf)

                params = dict(p)
                # country scope is a RUN parameter (never a store-list edit). All (or none) selected => ALL.
                if run_countries and _sl_countries and set(run_countries) != set(_sl_countries):
                    iso = ",".join(dict.fromkeys(
                        _CTRY_ISO.get(str(c).strip().upper(), str(c).strip().upper()) for c in run_countries))
                    params["COUNTRY"] = iso or "ALL"
                else:
                    params["COUNTRY"] = "ALL"
                # per-run identity: the app owns RUN_ID so it can read back exactly ITS run (isolation),
                # and stamps who triggered it. TRIGGERED_BY + RUN_ID flow to 00 → run_config → 07 history.
                eng_run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
                params["RUN_ID"] = eng_run_id
                params["TRIGGERED_BY"] = dbio.current_user()
                res = dbio.submit_pipeline(params)
                ss.engine_run_id = eng_run_id
                ss.run_id = res["run_id"]; ss.run_url = res.get("run_url")
                ss.run_started = time.time()
                ss.run_state = "running"
                st.toast(f"Job submitted — run #{ss.run_id}", icon="🚀")
                st.rerun()
            except Exception as e:
                ss.run_state = "error"; ss.error_msg = str(e); st.rerun()
    if running:
        st.caption("A run is in progress — the status card below refreshes automatically.")

# ---------- running: non-blocking, self-refreshing status card ----------
# One status read per script run (NOT a blocking while-loop) so the script always completes and the
# card survives websocket reconnects / tab switches; a trailing sleep+rerun drives the auto-refresh.
if ss.run_state == "running":
    TASK_LABELS = {"config": "Validating config", "effective_stock": "Building effective stock",
                   "classify": "ROS / cover / classification", "pools": "Donor & receiver pools + guards",
                   "match": "Matching & sizing", "reshape": "Size-curve", "score": "Scoring & output",
                   "archive": "Archiving run to history"}
    try:
        s = dbio.run_status(ss.run_id)
    except Exception as e:
        s = None
        st.warning(f"Couldn't read run status (will retry): {e}")

    if s is not None and s["life_cycle"] in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
        if s["result"] == "SUCCESS":
            rid = ss.get("engine_run_id")           # read THIS user's run from the per-run history
            ss.view_run_id = rid
            ss.recs = dbio.load_recommendations(run_id=rid)
            ss.stats = dbio.load_run_stats(run_id=rid)
            ss.run_state = "results" if len(ss.recs) else "no-results"
            ss.last_elapsed = s.get("elapsed")
        else:
            ss.run_state = "error"
            ss.error_msg = s.get("error") or s.get("message") or "Job failed"
        st.rerun()
    else:
        elapsed = (s or {}).get("elapsed")
        if elapsed is None and ss.get("run_started"):
            elapsed = int(time.time() - ss.run_started)
        done = (s or {}).get("done", 0); total = (s or {}).get("total", 8)
        step = TASK_LABELS.get((s or {}).get("current_task"),
                               (s or {}).get("current_task") or "starting up")
        pct = min(max(done / total, 0.02), 0.99)
        queued = (s or {}).get("queued")
        head = "⏳ Queued — waiting for a slot…" if queued else f"🔄 Running · {step}"
        with st.container(border=True):
            st.markdown(f"**{head}**")
            st.progress(pct, text=f"Step {min(done + 1, total)} of {total}  ·  {elapsed or 0}s elapsed")
            cols = st.columns([1, 1, 2])
            cols[0].caption(f"Run **#{ss.run_id}**")
            if ss.get("run_url"):
                cols[1].markdown(f"[Open in Databricks ↗]({ss.run_url})")
            if cols[2].button("↻ Refresh now"):
                st.rerun()
        time.sleep(4)
        st.rerun()

# ---------- states ----------
def kpi(col, label, value, accent=False, prov=False, help=""):
    bg = COBALT if accent else SILVER
    fg = "#FFFFFF" if accent else SAPPHIRE
    lblcolor = 'rgba(255,255,255,.8)' if accent else '#5C6478'
    prov_html = "<span class='prov'>PROV.</span>" if prov else ""
    col.markdown(f"<div class='kpi' style='background:{bg}' title=\"{help}\">"
                 f"<div class='lblrow'><span class='lbl' style='color:{lblcolor}'>{label}</span>{prov_html}</div>"
                 f"<div class='val' style='color:{fg}'>{value}</div></div>", unsafe_allow_html=True)

if ss.run_state == "empty":
    st.info("Set parameters and run to see recommended moves. The engine proposes moves across all "
            "countries — you review, filter, and export.")

elif ss.run_state == "error":
    st.error("The consolidation job did not complete.")
    st.code(ss.error_msg or "Unknown error", language="text")

elif ss.run_state == "no-results":
    st.success("No recommended moves — the job completed but found nothing to move "
               "(stock looks healthy, or all candidates were held by constraints).")

elif ss.run_state == "results":
    stats = ss.stats
    def sv(k, d=0):
        v = stats.get(k, d)
        try: return int(float(v))
        except Exception: return d

    VRID = ss.get("view_run_id")            # the run this user is viewing (None => latest global)
    df = ss.recs.copy()
    if "movement_note" in df:  # ASCII-clean the tag separator for display + export
        df["movement_note"] = df["movement_note"].astype(str).str.replace("·", "-", regex=False)
    BRANDS = get_brands(str(stats.get("computed_at", "")), VRID)   # article-master brands for the Brand filter

    if ss.get("last_elapsed") is not None:
        st.success(f"✓ Run completed in {ss.last_elapsed}s — results below.")
        ss.last_elapsed = None

    # provenance — whose run, when, what scope — so a viewer is never silently shown someone else's run
    if VRID:
        st.caption(f"👁 Viewing **your run** · by **{stats.get('triggered_by', '—')}** · "
                   f"{human_ts(stats.get('run_ts', stats.get('computed_at', '—')))} · "
                   f"scope **{stats.get('country_scope', 'ALL')}**  ·  run `{VRID}`")
    else:
        st.caption("👁 Viewing the **latest completed run** (shared across users). "
                   "Run the engine to get your own isolated view.")

    # run picker — view any recent run (each user can always return to their own or a full-scope run)
    _log = dbio.list_run_log(25)
    fh = st.columns([4, 1])
    if not _log.empty:
        _labels, _map = [], {}
        for _, r in _log.iterrows():
            lbl = (f"{human_ts(r['run_ts'])} · {r.get('triggered_by', '—')} · "
                   f"{r['country_scope']} · {int(float(r['recommended_moves'] or 0))} moves")
            _labels.append(lbl); _map[lbl] = r["run_id"]
        _idx = next((i for i, l in enumerate(_labels) if _map[l] == VRID), 0)
        pick = fh[0].selectbox("View a run", _labels, index=_idx, key="run_picker")
        if _map[pick] != VRID:
            try:
                ss.view_run_id = _map[pick]
                ss.recs = dbio.load_recommendations(run_id=ss.view_run_id)
                ss.stats = dbio.load_run_stats(run_id=ss.view_run_id)
                ss.run_state = "results" if len(ss.recs) else "no-results"
            except Exception as e:
                st.warning(f"Could not load that run: {e}")
            st.rerun()
    else:
        fh[0].caption(f"Latest results computed: **{human_ts(stats.get('computed_at', '—'))}**  ·  "
                      f"{sv('countries')} countries")
    if fh[1].button("↻ Refresh results", use_container_width=True):
        try:
            ss.recs = dbio.load_recommendations(run_id=VRID); ss.stats = dbio.load_run_stats(run_id=VRID)
        except Exception as e:
            st.warning(f"Could not refresh: {e}")
        st.rerun()

    # ---- definitions (brief) ----
    with st.expander("ℹ️  Definitions"):
        st.markdown("""
- **ROS** — units sold/day at a store over the ROS window (0 = dead).
- **Cover (days)** — ATP ÷ ROS (blank = dead/∞).
- **ATP** — sellable stock (SL01). **This is the only stock figure the engine uses** for cover, donor surplus and receiver need.
- **In-transit** — units on the way to the store (latest snapshot). **Counted for the receiving store** — a receiver's need already nets off stock inbound to it, so the engine won't over-ship.
- **Article name** — product description (product master TXTMD).
- **APO** — Y if an APO key is present for that parent at the store (from the ATP table, rolled up from child to parent). The receiver APO reads N by design — APO parents are already excluded from receivers.
- **Reason** — Dead (no sales) or Slow-moving (cover over threshold).
- **Proposed transfer qty** — units to move (donor keeps its reserve; receiver filled to target cover).
- **Cover post-move** — cover after the move: donor (ATP−qty)/ROS, receiver (ATP+qty)/ROS.
- **Est. uplift\\*** — qty × receiver net sales/unit × **probability to sell at receiver**.
- **Net benefit\\*** — uplift − movement cost (assumed **AED 5/unit**). *(\\* provisional until real cost & probability-to-sell are confirmed.)*
- **Season** — the article's Nike season (Jan–Mar Spring, Apr–Jun Summer, Jul–Sep Fall, Oct–Dec Holiday) + year; CORE/YEAR = carried all-year.

**The three tabs together account for every store×article line with ATP** (Moved ⊎ Held ⊎ Not moved):
- **Recommended moves** — proposed donor→receiver transfers.
- **Held** — donor surplus that *would* move but a soft guard blocked it, so business can override. **Held reason:** *Size-curve break* (would break the donor's full-size run), *Season-freshness* (a would-be receiver can't stock this season), *APO* (the would-be receiver already has an inbound APO for this parent). Each row shows a **would-be receiver** + **indicative qty** (min of donor surplus and receiver need) and a blank **Decision** column to mark Approve/Keep.
- **Not moved** — everything retained, with a reason: *no receiving store needs this size*, *new to store*, or *retained – selling within cover* (healthy).

**Why moved** *(tag on every line):*
- *Moved - donor stays full-size* — donor gives from surplus; its size run stays complete after giving.
- *Moved - donor cut — full remaining run consolidated to one receiver to display* — the donor's run was already broken (cut-size), so its **entire remaining giveable run is consolidated into one receiver** so that store can display the full size ladder.
- *Moved - run released & consolidated to a faster receiver (>=25%) to display* — moving would break the donor's (dead/slow) run, but a receiver sells **≥25% faster**, so the run is released and consolidated into that faster receiver to display the full ladder.
- *Moved - no size rule (fallback)* — item has no size definition (e.g. kids/accessories); moved on demand alone, without the size-curve check.

Every store×size sits in exactly one of three tabs: **Recommended moves** (to action), **Held** (would move but a soft guard blocked it — size-curve break / season-freshness / APO — with an indicative qty for a business call), and **Not moved** (no receiving store needs the size / new-to-store / retained & selling within cover).
        """)

    tab_all, tab_moves, tab_held, tab_nm = st.tabs(
        ["🧾  All (one table)", "📦  Recommended moves", "🔒  Held", "🚫  Not moved"])

    # ============ TAB 0: ALL — one combined table, all move columns + Action ============
    with tab_all:
        st.caption("Every store×article line in **one table** — moves, held and not-moved combined — with **all the "
                   "columns from Recommended moves**. Where a value doesn't apply (e.g. no receiver for a *dont move* "
                   "row) it shows **NA**. Final column = **Action** (move / held / dont move).")
        st.markdown("<div style='font-size:11px;font-weight:700;letter-spacing:.06em;color:#8891A6;'>FILTER</div>",
                    unsafe_allow_html=True)
        ac = st.columns([1.1, 1.2, 1.4, 1.2, 1.6, 1.1])
        act_sel = ac[0].selectbox("Action", ["All actions", "move", "held", "dont move"],
                                  key="all_action", label_visibility="collapsed")
        ctry_sel = ac[1].selectbox("Country", ["All countries"] + sorted(str(c) for c in df["country"].dropna().unique()),
                                   key="all_ctry", label_visibility="collapsed")
        cat_sel = ac[2].selectbox("Category", ["All categories"] + sorted(str(c) for c in df["business_category"].dropna().unique()),
                                  key="all_cat", label_visibility="collapsed")
        brand_sel = ac[3].selectbox("Brand", ["All brands"] + BRANDS, key="all_brand", label_visibility="collapsed")
        srch = ac[4].text_input("Search", placeholder="Article / name / size…", key="all_search",
                                label_visibility="collapsed")
        a_arg = None if act_sel == "All actions" else act_sel
        c_arg = None if ctry_sel == "All countries" else ctry_sel
        cat_arg = None if cat_sel == "All categories" else cat_sel
        br_arg = None if brand_sel == "All brands" else brand_sel
        full_csv_ui(ac[5], "combined_all",
                    lambda: dbio.load_combined_export(country=c_arg, category=cat_arg, brand=br_arg, search=srch or None, run_id=VRID),
                    "consolidation_all.csv", ("all", ctry_sel, cat_sel, brand_sel, srch, VRID),
                    gz_loader=lambda: dbio.load_combined_export_gz(country=c_arg, category=cat_arg,
                                                                   brand=br_arg, search=srch or None, run_id=VRID))
        try:
            cdf = dbio.load_combined_export(country=c_arg, action=a_arg, category=cat_arg, brand=br_arg,
                                            search=srch or None, limit=5000, run_id=VRID)
        except Exception as e:
            st.warning(f"Could not load the combined table: {e}"); cdf = pd.DataFrame()
        A_REN = {"country": "Country", "category": "Category", "gender": "Gender", "brand": "Brand",
                 "division": "Division", "demand_type": "Demand", "parent": "Parent", "article": "Article",
                 "article_name": "Article name", "size": "Size", "season": "Season",
                 "donor_store": "Donor", "donor_store_name": "Donor name", "donor_available_sizes": "Donor sizes",
                 "donor_available_sizes_post_move": "Donor sizes post-move",
                 "donor_ros": "Donor ROS", "donor_parent_ros": "Donor parent ROS", "donor_cover_days": "Donor cover (d)",
                 "donor_stock": "Donor ATP", "donor_intransit": "Donor in-transit", "donor_apo": "Donor APO",
                 "reason": "Reason", "receiver_store": "Receiver", "receiver_store_name": "Receiver name",
                 "receiver_available_sizes": "Recv sizes", "receiver_available_sizes_post_move": "Recv sizes post-move",
                 "receiver_ros": "Recv ROS",
                 "receiver_parent_ros": "Recv parent ROS", "receiver_cover_days": "Recv cover (d)",
                 "receiver_stock": "Recv ATP", "receiver_intransit": "Recv in-transit", "receiver_apo": "Recv APO",
                 "qty": "Proposed transfer qty", "donor_cover_post": "Donor cover post-move (d)",
                 "receiver_cover_post": "Recv cover post-move (d)", "inventory_cost_aed": "Inv. cost (AED)",
                 "uplift": "Est. uplift*", "net_benefit": "Net benefit*", "movement_note": "Why moved",
                 "action": "Action"}
        A_ORDER = ["Country", "Category", "Gender", "Brand", "Division", "Demand", "Parent", "Article", "Article name",
                   "Size", "Season", "Donor", "Donor name", "Donor sizes", "Donor sizes post-move", "Donor ROS", "Donor parent ROS",
                   "Donor cover (d)", "Donor ATP", "Donor in-transit", "Donor APO", "Reason", "Receiver",
                   "Receiver name", "Recv sizes", "Recv sizes post-move", "Recv ROS", "Recv parent ROS", "Recv cover (d)", "Recv ATP",
                   "Recv in-transit", "Recv APO", "Proposed transfer qty", "Donor cover post-move (d)",
                   "Recv cover post-move (d)", "Inv. cost (AED)", "Est. uplift*", "Net benefit*", "Why moved", "Action"]
        if len(cdf):
            for _c in ("donor_ros", "donor_parent_ros", "donor_cover_days", "donor_stock", "donor_intransit",
                       "receiver_ros", "receiver_parent_ros", "receiver_cover_days", "receiver_stock",
                       "receiver_intransit", "qty", "donor_cover_post", "receiver_cover_post",
                       "inventory_cost_aed", "uplift", "net_benefit"):
                if _c in cdf:
                    cdf[_c] = pd.to_numeric(cdf[_c], errors="coerce")
            cdf["reason"] = cdf["reason"].map({"DEAD": "Dead", "SLOW_MOVING": "Slow-moving"}).fillna(cdf["reason"])
            cdf = commafy(cdf, ["donor_stock", "donor_intransit", "receiver_stock", "receiver_intransit",
                                "qty", "inventory_cost_aed", "uplift", "net_benefit"])
            for _c in ("donor_ros", "donor_parent_ros", "donor_cover_days", "receiver_ros", "receiver_parent_ros",
                       "receiver_cover_days", "donor_cover_post", "receiver_cover_post"):
                if _c in cdf:
                    cdf[_c] = cdf[_c].map(lambda v: f"{v:.3f}" if pd.notna(v) else "")
            cdf = cdf.rename(columns=A_REN)
            cdf = cdf[[c for c in A_ORDER if c in cdf.columns]]
            cdf = cdf.where(cdf.notna(), "NA").replace({"": "NA", "None": "NA", "nan": "NA"})   # NA for inapplicable
        st.caption(f"Showing up to 5,000 rows of **{act_sel.lower()}** · download = full combined file "
                   "(move + held + dont move).")
        st.dataframe(cdf, hide_index=True, use_container_width=True, height=460)

    # ============ TAB 1: recommended moves ============
    with tab_moves:
        mtiles = st.container()   # tiles at the top of this tab, filled after the filter below
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # ---- moves breakdown by 'Why moved' (all moves, mirrors the not-moved summary) ----
        wm = (df.groupby("movement_note", as_index=False)
                .agg(Items=("article", "count"), Units=("recommended_qty", "sum"), inv=("inventory_cost_aed", "sum"))
                .sort_values("Items", ascending=False)
                .rename(columns={"movement_note": "Why moved", "inv": "Inventory cost (AED)"}))
        st.caption("Moves breakdown — **why moved** (all recommended moves):")
        st.dataframe(commafy(wm, ["Items", "Units", "Inventory cost (AED)"]),
                     hide_index=True, use_container_width=True)
        st.caption(
            f"🔒 **{sv('held_lines'):,} donor lines held by constraint** (AED {sv('held_inventory_aed'):,} of stock) — "
            "would move but a soft guard blocked them (APO / season-freshness / size-curve). "
            "See the **Held** tab to review and export them.")
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

        st.markdown("<div style='font-size:11px;font-weight:700;letter-spacing:.06em;color:#8891A6;'>FILTER</div>",
                    unsafe_allow_html=True)
        f = st.columns([1.0, 1.3, 1.1, 1.1, 1.1, 1.1, 1.3, 1.4, 1.1])
        countries = ["All countries"] + sorted(df["country"].dropna().unique().tolist())
        fc = f[0].selectbox("Country", countries, label_visibility="collapsed")
        fbrand = f[8].selectbox("Brand", ["All brands"] + BRANDS, label_visibility="collapsed", key="mv_brand")
        cats = ["All categories"] + sorted(df["business_category"].dropna().unique().tolist())
        fcat = f[1].selectbox("Category", cats, label_visibility="collapsed")
        reasons = f[2].multiselect("Reason", ["DEAD", "SLOW_MOVING"], default=[],
                                   format_func=lambda x: "Dead" if x == "DEAD" else "Slow-moving",
                                   label_visibility="collapsed", placeholder="Reason")
        donors = ["All donor stores"] + sorted(df["donor_store"].dropna().unique().tolist())
        fd = f[3].selectbox("Donor", donors, label_visibility="collapsed")
        recvs = ["All receiver stores"] + sorted(df["receiver_store"].dropna().unique().tolist())
        fr = f[4].selectbox("Receiver", recvs, label_visibility="collapsed")
        DMD_LABEL = {"OOS_SELLING": "Out-of-stock demand", "IN_STOCK": "In-stock top-up", "APLUS_CURVE": "A+ curve completion"}
        dmd_opts = sorted(df["demand_type"].dropna().unique().tolist()) if "demand_type" in df else []
        dmd_sel = f[5].multiselect("Demand", dmd_opts, default=[], format_func=lambda x: DMD_LABEL.get(x, x),
                                   label_visibility="collapsed", placeholder="Demand type")
        why_opts = sorted(df["movement_note"].dropna().unique().tolist())
        why_sel = f[6].multiselect("Why moved", why_opts, default=[],
                                   label_visibility="collapsed", placeholder="Why moved")
        search = f[7].text_input("Search", placeholder="Search article, size…", label_visibility="collapsed")

        view = df.copy()
        if fc != "All countries": view = view[view["country"] == fc]
        if fcat != "All categories": view = view[view["business_category"] == fcat]
        if fbrand != "All brands" and "brand" in view: view = view[view["brand"] == fbrand]
        if reasons: view = view[view["reason"].isin(reasons)]
        if fd != "All donor stores": view = view[view["donor_store"] == fd]
        if fr != "All receiver stores": view = view[view["receiver_store"] == fr]
        if dmd_sel and "demand_type" in view: view = view[view["demand_type"].isin(dmd_sel)]
        if why_sel: view = view[view["movement_note"].isin(why_sel)]
        if search:
            q = search.lower()
            hay = (view["parent_article"].astype(str) + " " + view["article"].astype(str) + " "
                   + view["size"].astype(str)).str.lower()
            view = view[hay.str.contains(q, na=False)]

        # KPI tiles (top of this tab) — reactive to the filter
        with mtiles:
            k = st.columns(7)
            kpi(k[0], "Recommended moves", f"{len(view):,}", accent=True, help="Transfer lines in the current filter.")
            kpi(k[1], "Units to move", f"{int(view['recommended_qty'].sum()):,}", help="Total units in the current filter.")
            kpi(k[2], "Inventory value to move", f"AED {int(view['inventory_cost_aed'].sum()):,}",
                help="Recommended (not yet actioned): cost value of the stock proposed to move = qty × unit cost (AED).")
            kpi(k[3], "Donor stores", f"{view['donor_store'].nunique():,}", help="Distinct donors in view.")
            kpi(k[4], "Receiver stores", f"{view['receiver_store'].nunique():,}", help="Distinct receivers in view.")
            kpi(k[5], "Est. revenue uplift", f"AED {int(view['expected_sales_uplift'].sum()):,}", prov=True,
                help="PROVISIONAL. Expected revenue for the moves in view.")
            kpi(k[6], "Est. net benefit", f"AED {int(view['net_benefit'].sum()):,}", prov=True,
                help="PROVISIONAL. Sum of net benefit (est. uplift − movement cost) for the moves in view.")

        # Donor cover display: ∞ when dead (ROS=0 with stock), 0 when emptied post-move, else the number.
        def _dc_today(r):
            return "∞" if (r["donor_ros"] == 0 or pd.isna(r["donor_cover_days"])) else f"{r['donor_cover_days']:.0f}"
        def _dc_post(r):
            if pd.notna(r["donor_cover_post_move"]):
                return f"{r['donor_cover_post_move']:.0f}"
            rem = (r["donor_stock"] or 0) - (r["recommended_qty"] or 0)   # ROS=0 case
            return "0" if rem <= 0 else "∞"
        view = view.assign(_dc_today=view.apply(_dc_today, axis=1), _dc_post=view.apply(_dc_post, axis=1))

        view = view.assign(_demand=view["demand_type"].map(DMD_LABEL).fillna(view["demand_type"])
                           if "demand_type" in view else "")
        show = view.rename(columns={
            "country": "Country", "business_category": "Category", "gender": "Gender", "division": "Division",
            "parent_article": "Parent", "_demand": "Demand",
            "article": "Article", "article_name": "Article name", "size": "Size", "article_season": "Season",
            "donor_store": "Donor", "donor_store_name": "Donor name", "donor_available_sizes": "Donor sizes",
            "donor_available_sizes_post_move": "Donor sizes post-move",
            "donor_ros": "Donor ROS", "donor_parent_ros": "Donor parent ROS", "_dc_today": "Donor cover (d)", "donor_stock": "Donor ATP",
            "donor_intransit": "Donor in-transit", "donor_apo": "Donor APO",
            "reason": "Reason",
            "receiver_store": "Receiver", "receiver_store_name": "Receiver name", "receiver_available_sizes": "Recv sizes",
            "receiver_available_sizes_post_move": "Recv sizes post-move",
            "receiver_ros": "Recv ROS", "receiver_parent_ros": "Recv parent ROS", "receiver_cover_days": "Recv cover (d)", "receiver_stock": "Recv ATP",
            "receiver_intransit": "Recv in-transit", "receiver_apo": "Recv APO",
            "recommended_qty": "Proposed transfer qty",
            "_dc_post": "Donor cover post-move (d)",
            "receiver_cover_post_move": "Recv cover post-move (d)",
            "inventory_cost_aed": "Inv. cost (AED)",
            "expected_sales_uplift": "Est. uplift*", "net_benefit": "Net benefit*", "movement_note": "Why moved",
        })
        show["Reason"] = show["Reason"].map({"DEAD": "Dead", "SLOW_MOVING": "Slow-moving"}).fillna(show["Reason"])
        show = commafy(show, ["Donor ATP", "Donor in-transit", "Recv ATP", "Recv in-transit", "Recv cover (d)",
                              "Recv cover post-move (d)", "Proposed transfer qty", "Inv. cost (AED)",
                              "Est. uplift*", "Net benefit*"])
        cols = ["Country", "Category", "Gender", "Division", "Demand", "Parent", "Article", "Article name", "Size", "Season",
                "Donor", "Donor name", "Donor sizes", "Donor sizes post-move", "Donor ROS", "Donor parent ROS", "Donor cover (d)", "Donor ATP", "Donor in-transit", "Donor APO",
                "Reason", "Receiver", "Receiver name", "Recv sizes", "Recv sizes post-move", "Recv ROS", "Recv parent ROS", "Recv cover (d)", "Recv ATP", "Recv in-transit", "Recv APO",
                "Proposed transfer qty", "Donor cover post-move (d)", "Recv cover post-move (d)",
                "Inv. cost (AED)", "Est. uplift*", "Net benefit*", "Why moved"]
        ccfg = {
            "Category": st.column_config.TextColumn(help="Business category from the category converter (e.g. NSW MENS)."),
            "Gender": st.column_config.TextColumn(help="MENS / WOMENS / KIDS / UNISEX."),
            "Division": st.column_config.TextColumn(help="Product division (product master /BIC/ZRWGH2_T) — e.g. FOOTWEAR / APPAREL."),
            "Demand": st.column_config.TextColumn(help="Why the receiver needs it: Out-of-stock demand (sells it, ATP=0, not in the DC), In-stock top-up (has stock, below target), or A+ curve completion."),
            "Parent": st.column_config.TextColumn(help="Parent (generic) article — the style/colour."),
            "Donor parent ROS": st.column_config.NumberColumn(format="%.3f", help="Donor parent-level ROS = sum of the parent's article ROS at the donor (same for every size of the parent)."),
            "Recv parent ROS": st.column_config.NumberColumn(format="%.3f", help="Receiver parent-level ROS = sum of the parent's article ROS at the receiver."),
            "Article": st.column_config.TextColumn(help="Child article — the specific size SKU that moves."),
            "Article name": st.column_config.TextColumn(width="medium", help="Article description (product master TXTMD)."),
            "Season": st.column_config.TextColumn(help="Article's Nike season (RF_SEASON + year). CORE/YEAR = season-agnostic. Receivers with a freshness rule only accept current-or-newer seasons."),
            "Donor sizes": st.column_config.TextColumn(help="All sizes of this parent currently in stock at the donor store."),
            "Donor sizes post-move": st.column_config.TextColumn(help="Sizes of this parent the donor still displays after giving the recommended units (a size drops off once its last unit moves)."),
            "Recv sizes post-move": st.column_config.TextColumn(help="Sizes of this parent the receiver displays after the moves — includes newly received sizes it did not stock before."),
            "Donor ROS": st.column_config.NumberColumn(format="%.3f", help="Donor units sold/day over the ROS window. 0 = dead."),
            "Donor cover (d)": st.column_config.TextColumn(help="Donor cover today = ATP ÷ ROS. ∞ = dead (no sales)."),
            "Donor ATP": st.column_config.TextColumn(help="Donor sellable stock = ATP (SL01) only. This is what the engine uses."),
            "Donor in-transit": st.column_config.TextColumn(help="Units in transit toward the donor (latest snapshot). Display only — a donor gives ATP only, so its in-transit is NOT used."),
            "Donor APO": st.column_config.TextColumn(help="Y = an APO key is present for this parent at the donor store (atp_detail, parent level)."),
            "Reason": st.column_config.TextColumn(help="Dead = no sales; Slow-moving = cover above threshold."),
            "Recv sizes": st.column_config.TextColumn(help="All sizes of this parent currently in stock at the receiver store."),
            "Recv ROS": st.column_config.NumberColumn(format="%.3f", help="Receiver units sold/day over the ROS window."),
            "Recv cover (d)": st.column_config.TextColumn(help="Receiver cover today = ATP ÷ ROS."),
            "Recv ATP": st.column_config.TextColumn(help="Receiver sellable stock = ATP (SL01) only. This is what the engine uses."),
            "Recv in-transit": st.column_config.TextColumn(help="Units in transit toward the receiver (latest snapshot). Counted for the receiver — its need nets off inbound stock so the engine won't over-ship."),
            "Recv APO": st.column_config.TextColumn(help="Y = APO key present for this parent at the receiver. Always N here — the APO guard already excludes APO parents from receivers."),
            "Proposed transfer qty": st.column_config.TextColumn(help="Whole units to move donor → receiver."),
            "Donor cover post-move (d)": st.column_config.TextColumn(help="Donor cover after giving qty. 0 = emptied; ∞ = still dead."),
            "Recv cover post-move (d)": st.column_config.TextColumn(help="Receiver cover after receiving qty."),
            "Inv. cost (AED)": st.column_config.TextColumn(help="Cost value of the moved units = qty × unit cost (AED)."),
            "Est. uplift*": st.column_config.TextColumn(label="Est. uplift (AED)*", help="PROVISIONAL. qty × receiver net sales/unit × probability to sell at receiver."),
            "Net benefit*": st.column_config.TextColumn(label="Net benefit (AED)*", help="PROVISIONAL. Est. uplift − movement cost (assumed AED 5/unit)."),
            "Why moved": st.column_config.TextColumn(width="medium", help="Plain-English reason for the move."),
        }
        top = st.columns([3, 1, 1])
        top[0].caption(f"{len(view):,} of {len(df):,} lines")
        top[1].download_button("Export (Excel)", data=to_xlsx(show[cols]), file_name="consolidation_moves.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True)
        top[2].button("Push to SAP", disabled=True, help="Coming soon — approve-and-execute path not yet enabled",
                      use_container_width=True)
        st.caption("ℹ️ Click any row to open its full explanation — donor & receiver detail, why it moved, and economics.")

        event = st.dataframe(show[cols], use_container_width=True, hide_index=True, height=460,
                             on_select="rerun", selection_mode="single-row", column_config=ccfg)
        st.caption("Hover a column header for its definition. * = provisional economics (movement cost assumed AED 5/unit; probability-to-sell pending).")

        sel = event.selection.rows if event and event.selection else []
        if sel:
            row = view.iloc[sel[0]]

            @st.dialog(f"{row['parent_article']} · {row['article']}")
            def detail():
                st.caption(f"{row.get('article_name','')} · {row.get('business_category','')} · {row.get('gender','')} · Size {row['size']} · {row['country']}")
                st.markdown(f"**{row['donor_store']}** ({row['donor_store_name']}) "
                            f"→ **{row['recommended_qty']:.0f} units** → "
                            f"**{row['receiver_store']}** ({row['receiver_store_name']})")
                st.info(f"**Why moved:** {row.get('movement_note', '')}")
                d, r = st.columns(2)
                _rem = (row["donor_stock"] or 0) - (row["recommended_qty"] or 0)
                _dpost = (f"{row['donor_cover_post_move']:.0f}" if pd.notna(row["donor_cover_post_move"])
                          else ("0 (emptied)" if _rem <= 0 else "∞ (dead)"))
                with d:
                    st.markdown("**Donor**")
                    st.write({"BU": row["donor_bu"], "Class": row["reason"],
                              "Sizes in stock": row.get("donor_available_sizes", ""),
                              "Sizes post-move": row.get("donor_available_sizes_post_move", ""),
                              "ATP (sellable)": f"{row['donor_stock']:.0f}",
                              "In-transit (not used)": f"{row.get('donor_intransit', 0):.0f}",
                              "APO key": row.get("donor_apo", ""),
                              "Key-size run (before→after)": f"{row.get('donor_run_before','?')}→{row.get('donor_run_after','?')}",
                              "ROS (u/day)": f"{row['donor_ros']:.3f}",
                              "Cover today (d)": "∞ (dead)" if (row["donor_ros"] == 0 or pd.isna(row["donor_cover_days"])) else f"{row['donor_cover_days']:.0f}",
                              "Cover post-move (d)": _dpost})
                with r:
                    st.markdown("**Receiver**")
                    _rin = row.get("receiver_units_in", row["recommended_qty"])
                    _rpost = row.get("receiver_stock_post_move", (row["receiver_stock"] or 0) + (_rin or 0))
                    st.write({"BU": row["receiver_bu"],
                              "Sizes in stock": row.get("receiver_available_sizes", ""),
                              "Sizes post-move": row.get("receiver_available_sizes_post_move", ""),
                              "ATP (sellable)": f"{row['receiver_stock']:.0f}",
                              "Units in (all donors)": f"{_rin:.0f}",
                              "ATP after all moves": f"{_rpost:.0f}",
                              "In-transit (not used)": f"{row.get('receiver_intransit', 0):.0f}",
                              "APO key": row.get("receiver_apo", ""),
                              "Key-size run (before→after)": f"{row.get('receiver_run_before','?')}→{row.get('receiver_run_after','?')}",
                              "ROS (u/day)": f"{row['receiver_ros']:.3f}",
                              "Cover today (d)": "—" if pd.isna(row["receiver_cover_days"]) else f"{row['receiver_cover_days']:.0f}",
                              "Cover post-move (d)": "—" if pd.isna(row["receiver_cover_post_move"]) else f"{row['receiver_cover_post_move']:.0f}"})
                st.markdown("**Revenue uplift** :orange-badge[PROVISIONAL]")
                st.write({"Expected uplift": f"AED {row['expected_sales_uplift']:,.0f}",
                          "Movement cost (AED 5/unit)": f"AED {row['movement_cost']:,.0f}",
                          "Net benefit": f"AED {row['net_benefit']:,.0f}"})
                st.caption("Uplift = qty × receiver net sales/unit × probability to sell at receiver. "
                           "Net benefit = uplift − movement cost.")
            detail()

    # ============ TAB 2: held (would move, blocked by a constraint) ============
    with tab_held:
        computed_at = str(stats.get("computed_at", ""))
        hsumm = get_held_summary(computed_at, VRID)
        h_items = int(hsumm["lines"].sum()) if not hsumm.empty else 0
        h_units = int(hsumm["units"].sum()) if not hsumm.empty else 0
        h_invc = int(hsumm["inventory_cost"].sum()) if not hsumm.empty else 0
        hk = st.columns(7)
        kpi(hk[0], "Held items", f"{h_items:,}", accent=True, help="Store×article lines that could move to a blocked receiver (each has at least 1 movable unit).")
        kpi(hk[1], "Indicative units to move", f"{h_units:,}", help="Whole units that would actually move if the guard were overridden — a real, de-duplicated allocation (not the donor's full stock).")
        kpi(hk[2], "Indicative value", f"AED {h_invc:,}", help="Cost value of the indicative movable units (movable qty × unit cost) — the actionable override opportunity.")
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        st.caption("Held by reason — would move to a blocked receiver, override to release (indicative = actually movable):")
        h_by_reason = (hsumm.groupby("held_reason", as_index=False)[["lines", "units", "inventory_cost"]].sum()
                       .sort_values("lines", ascending=False)
                       .rename(columns={"held_reason": "Held reason", "lines": "Items",
                                        "units": "Indicative units", "inventory_cost": "Indicative value (AED)"}))
        st.dataframe(commafy(h_by_reason, ["Items", "Indicative units", "Indicative value (AED)"]),
                     hide_index=True, use_container_width=True)
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        st.markdown("<div style='font-size:11px;font-weight:700;letter-spacing:.06em;color:#8891A6;'>"
                    "DETAIL — filter to review (bounded to 5,000, biggest indicative value first)</div>", unsafe_allow_html=True)
        hf = st.columns([1.0, 1.5, 1.2, 1.1, 1.0, 1.0])
        h_countries = ["All countries"] + sorted(hsumm["country"].dropna().unique().tolist())
        h_c = hf[0].selectbox("Country", h_countries, key="h_c", label_visibility="collapsed")
        h_reasons = ["All reasons"] + sorted(hsumm["held_reason"].dropna().unique().tolist())
        h_r = hf[1].selectbox("Reason", h_reasons, key="h_r", label_visibility="collapsed")
        h_a = hf[2].text_input("Article", key="h_a", placeholder="Article / name…", label_visibility="collapsed")
        h_brand = hf[3].selectbox("Brand", ["All brands"] + BRANDS, key="h_brand", label_visibility="collapsed")
        h_df, h_total = get_held_slice(computed_at, h_c, h_r, h_a or None, run_id=VRID)
        if h_brand != "All brands" and "brand" in h_df:
            h_df = h_df[h_df["brand"] == h_brand]; h_total = len(h_df)
        h_show = h_df.rename(columns={
            "country": "Country", "business_category": "Category", "gender": "Gender", "division": "Division",
            "parent_article": "Parent", "article": "Article", "article_name": "Article name", "size": "Size",
            "article_season": "Season",
            "donor_store": "Donor", "donor_store_name": "Donor name", "bu": "BU", "class": "Class", "apo_key": "APO",
            "held_reason": "Held reason", "would_be_receiver": "Would-be receiver",
            "would_be_receiver_name": "Would-be receiver name", "would_be_receiver_ros": "Recv ROS",
            "indicative_qty": "Indicative qty", "indicative_value_aed": "Indicative value (AED)",
            "available_sizes": "Available sizes",
            "effective_stock": "ATP", "intransit_qty": "In-transit",
            "inventory_cost_aed": "Donor inv. (AED)", "ros": "ROS", "parent_ros": "Parent ROS",
            "cover_days": "Cover (d)", "decision": "Decision"})
        h_cols = ["Country", "Category", "Gender", "Division", "Parent", "Article", "Article name", "Size", "Season",
                  "Donor", "Donor name", "BU", "Class", "Held reason", "APO",
                  "Would-be receiver", "Would-be receiver name", "Recv ROS", "Indicative qty", "Indicative value (AED)",
                  "Available sizes", "ATP", "In-transit", "Donor inv. (AED)", "ROS", "Parent ROS", "Cover (d)", "Decision"]
        hf[4].download_button("Export (Excel)", data=to_xlsx(h_show[h_cols]), file_name="held.xlsx",
                              mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                              use_container_width=True)
        _hb = None if h_brand == "All brands" else h_brand
        full_csv_ui(hf[5], "held_csv", lambda: dbio.load_held_full(h_c, h_r, h_a or None, brand=_hb, run_id=VRID),
                    "held_all.csv", (computed_at, h_c, h_r, h_a or "", h_brand, VRID))
        hcap = f"Showing {len(h_df):,} of {h_total:,} held items"
        if h_total > len(h_df):
            hcap += " — first 5,000 by indicative value; refine the filter to narrow (export reflects what's shown)."
        st.caption(hcap)
        h_show = commafy(h_show, ["Indicative qty", "Indicative value (AED)", "ATP", "In-transit", "Donor inv. (AED)", "Cover (d)"])
        st.dataframe(h_show[h_cols], hide_index=True, use_container_width=True, height=430, column_config={
            "Article name": st.column_config.TextColumn(width="medium", help="Article description (product master TXTMD)."),
            "Division": st.column_config.TextColumn(help="Product division (product master /BIC/ZRWGH2_T) — e.g. FOOTWEAR / APPAREL."),
            "Season": st.column_config.TextColumn(help="Article's Nike season (RF_SEASON + year). CORE/YEAR = season-agnostic."),
            "Parent ROS": st.column_config.NumberColumn(format="%.3f", help="Parent-level ROS at the donor = sum of the parent's article ROS (repeats across the parent's sizes)."),
            "Held reason": st.column_config.TextColumn(help="Why it was blocked: Size-curve break / Season-freshness / APO."),
            "APO": st.column_config.TextColumn(help="Y = APO key present for this parent at the donor store."),
            "Would-be receiver": st.column_config.TextColumn(help="The store this donor line would actually supply if the guard were overridden (the biggest single move; a de-duplicated allocation, not just any blocked store)."),
            "Recv ROS": st.column_config.NumberColumn(format="%.3f", help="Would-be receiver units sold/day."),
            "Indicative qty": st.column_config.TextColumn(help="Whole units that would actually move from this donor line (summed across the receivers it supplies) — de-duplicated, always ≥ 1."),
            "Indicative value (AED)": st.column_config.TextColumn(help="Indicative movable qty × unit cost — the actionable override value for this line."),
            "ATP": st.column_config.TextColumn(help="Donor sellable stock (SL01) — the full holding; only the indicative qty is actually movable."),
            "In-transit": st.column_config.TextColumn(help="Units in transit to the donor (display only, not used in calc)."),
            "Donor inv. (AED)": st.column_config.TextColumn(help="Cost value of the donor's FULL holding of this line (context) — not all of it moves; see Indicative value."),
            "ROS": st.column_config.NumberColumn(format="%.3f"),
            "Decision": st.column_config.TextColumn(help="Business override: enter Approve to move / Keep to hold. (Not yet fed back into the engine.)")})

    # ============ TAB 3: not moved ============
    with tab_nm:
        computed_at = str(stats.get("computed_at", ""))
        summ = get_nm_summary(computed_at, VRID)
        total_items = int(summ["lines"].sum()) if not summ.empty else 0
        total_units = int(summ["units"].sum()) if not summ.empty else 0
        total_invc = int(summ["inventory_cost"].sum()) if not summ.empty else 0
        nk = st.columns(7)
        kpi(nk[0], "Items not moved", f"{total_items:,}", accent=True, help="Store×article lines not moved.")
        kpi(nk[1], "Units not moved", f"{total_units:,}", help="Total units sitting un-moved.")
        kpi(nk[2], "Inventory value", f"AED {total_invc:,}", help="Cost value of un-moved stock (stock × unit cost).")
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        st.caption("Full picture by reason:")
        by_reason = (summ.groupby("not_moved_reason", as_index=False)[["lines", "units", "inventory_cost"]].sum()
                     .sort_values("lines", ascending=False)
                     .rename(columns={"not_moved_reason": "Reason not moved", "lines": "Items",
                                      "units": "Units", "inventory_cost": "Inventory cost (AED)"}))
        st.dataframe(commafy(by_reason, ["Items", "Units", "Inventory cost (AED)"]),
                     hide_index=True, use_container_width=True)
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        st.markdown("<div style='font-size:11px;font-weight:700;letter-spacing:.06em;color:#8891A6;'>"
                    "DETAIL — filter to view lines (bounded to 5,000)</div>", unsafe_allow_html=True)
        nf = st.columns([1.0, 1.5, 1.2, 1.1, 1.0, 1.0])
        nm_countries = ["All countries"] + sorted(summ["country"].dropna().unique().tolist())
        nm_c = nf[0].selectbox("Country", nm_countries, key="nm_c", label_visibility="collapsed")
        nm_reasons = ["All reasons"] + sorted(summ["not_moved_reason"].dropna().unique().tolist())
        nm_r = nf[1].selectbox("Reason", nm_reasons, key="nm_r", label_visibility="collapsed")
        nm_a = nf[2].text_input("Article", key="nm_a", placeholder="Article / name…", label_visibility="collapsed")
        nm_brand = nf[3].selectbox("Brand", ["All brands"] + BRANDS, key="nm_brand", label_visibility="collapsed")
        nm_df, nm_total = get_nm_slice(computed_at, nm_c, nm_r, nm_a or None,
                                       brand=(None if nm_brand == "All brands" else nm_brand), run_id=VRID)
        nm_show = nm_df.rename(columns={
            "country": "Country", "business_category": "Category", "gender": "Gender", "division": "Division",
            "parent_article": "Parent", "article": "Article", "article_name": "Article name", "size": "Size",
            "article_season": "Season",
            "store_id": "Store", "store_name": "Store name", "bu": "BU", "class": "Class", "apo_key": "APO",
            "available_sizes": "Available sizes", "effective_stock": "ATP", "intransit_qty": "In-transit",
            "inventory_cost_aed": "Inv. cost (AED)", "ros": "ROS", "parent_ros": "Parent ROS", "cover_days": "Cover (d)",
            "not_moved_reason": "Reason not moved"})
        nm_cols = ["Country", "Category", "Gender", "Division", "Parent", "Article", "Article name", "Size", "Season", "Store", "Store name",
                   "BU", "Class", "APO", "Available sizes", "ATP", "In-transit", "Inv. cost (AED)", "ROS", "Parent ROS", "Cover (d)", "Reason not moved"]
        nf[4].download_button("Export (Excel)", data=to_xlsx(nm_show[nm_cols]), file_name="not_moved.xlsx",
                              mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                              use_container_width=True)
        _nmb = None if nm_brand == "All brands" else nm_brand
        full_csv_ui(nf[5], "nm_csv", lambda: dbio.load_not_moved_full(nm_c, nm_r, nm_a or None, brand=_nmb, run_id=VRID),
                    "not_moved_all.csv", (computed_at, nm_c, nm_r, nm_a or "", nm_brand, VRID))
        cap = f"Showing {len(nm_df):,} of {nm_total:,} matching items"
        if nm_total > len(nm_df):
            cap += " — first 5,000; refine the filter to narrow (export reflects what's shown)."
        st.caption(cap)
        nm_show = commafy(nm_show, ["ATP", "In-transit", "Inv. cost (AED)", "Cover (d)"])
        st.dataframe(nm_show[nm_cols], hide_index=True, use_container_width=True, height=430, column_config={
            "Article name": st.column_config.TextColumn(width="medium", help="Article description (product master TXTMD)."),
            "Division": st.column_config.TextColumn(help="Product division (product master /BIC/ZRWGH2_T) — e.g. FOOTWEAR / APPAREL."),
            "Season": st.column_config.TextColumn(help="Article's Nike season (RF_SEASON + year). CORE/YEAR = season-agnostic."),
            "Parent ROS": st.column_config.NumberColumn(format="%.3f", help="Parent-level ROS at the store = sum of the parent's article ROS (repeats across the parent's sizes)."),
            "APO": st.column_config.TextColumn(help="Y = APO key present for this parent at the store."),
            "ATP": st.column_config.TextColumn(help="Sellable stock (SL01) — the only stock the engine uses."),
            "In-transit": st.column_config.TextColumn(help="Units in transit to the store (display only, not used in calc)."),
            "Available sizes": st.column_config.TextColumn(help="All sizes of this parent in stock at the store."),
            "ROS": st.column_config.NumberColumn(format="%.3f")})
