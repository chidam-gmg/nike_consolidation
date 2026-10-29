"""Nike Stock Movement Engine — review & export UI (store→store consolidation + DC→store
replenishment). Streamlit front-end: set parameters → trigger the Databricks pipeline →
review recommended moves → export. No business logic here; the engine notebooks do it all.

GMG Brand Book V6, INFORM end: Sapphire #20274C, Cobalt #2658F2, Light Silver #E9EFEF,
Burnt Copper #B15940. Economics columns are PROVISIONAL (movement cost / probability-to-sell pending).
"""
import io
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
import pandas as pd
import streamlit as st

import databricks_io as dbio
import agent_view

# DEV preview (separate Databricks app, APP_MODE=dev): read-only — no runs, no config writes, so it can sit on
# live data without ever touching the shared config tables or triggering the production job.
DEV = os.environ.get("APP_MODE", "").strip().lower() == "dev"


def human_ts(iso: str) -> str:
    """ISO UTC timestamp -> 'DD Mon YYYY, HH:MM GST' (UAE local, UTC+4)."""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=4)))
        return dt.strftime("%d %b %Y, %H:%M") + " GST"
    except Exception:
        return str(iso)


def _who(x) -> str:
    """Plain-language run owner: the daily job runs with the job default ('app'); app users are recorded by email."""
    x = str(x or "").strip()
    return "scheduled 10:30 run" if x in ("", "app", "manual", "None", "nan") else x


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


@st.cache_data(show_spinner=False, ttl=60)
def get_agent_runs():
    """Recent job runs with task timings (short TTL: the board should follow new runs)."""
    return dbio.agent_job_runs(30)


@st.cache_data(show_spinner=False)
def get_review_items(run_id, ver: int = 0):
    """Review queue for a run; `ver` bumps after every save so the cache refreshes."""
    return dbio.load_review_items(run_id)


@st.cache_data(show_spinner=False)
def get_agent_metrics(run_id=None, computed_at: str = ""):
    return dbio.agent_run_metrics(run_id)


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

st.set_page_config(page_title="Nike Stock Movement Engine", layout="wide", page_icon="⇄")

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
  /* chat (Ask the agents) */
  [data-testid="stChatMessage"] {{ border-radius:12px; padding:14px 16px; gap:14px; }}
  [data-testid="stChatMessage"]:has(.chat-u) {{ background:#F3F6F7; border:1px solid #E1E7E8; }}
  [data-testid="stChatMessage"]:has(.chat-a) {{ background:transparent; border:1px solid transparent; }}
  [data-testid^="stChatMessageAvatar"] {{ border-radius:8px !important; width:32px; height:32px; }}
  [data-testid="stChatMessage"]:has(.chat-u) [data-testid^="stChatMessageAvatar"] {{ background:{SAPPHIRE} !important; color:#FFFFFF !important; }}
  [data-testid="stChatMessage"]:has(.chat-a) [data-testid^="stChatMessageAvatar"] {{ background:{COBALT} !important; color:#FFFFFF !important; }}
  [data-testid^="stChatMessageAvatar"] * {{ color:#FFFFFF !important; fill:#FFFFFF !important; }}
  [data-testid="stChatInput"] > div {{ border-radius:12px !important; }}
  .chat-u, .chat-a {{ display:none; }}
  [data-testid="stElementContainer"]:has(.chat-u), [data-testid="stElementContainer"]:has(.chat-a) {{ display:none; }}
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
    f"<div><div class='eyebrow'>NIKE · CONSOLIDATION + REPLENISHMENT</div>"
    f"<div style='font-family:Georgia,serif;font-size:22px;color:{SAPPHIRE};'>Stock movement engine</div></div>"
    f"<div style='flex:1'></div><div style='font-size:11px;color:#8891A6;'>● Databricks</div></div>",
    unsafe_allow_html=True,
)

# Dark mode — one switch for the whole page (app chrome + Agent lab), so nothing renders half light / half dark.
_tg = st.columns([6, 1], vertical_alignment="center")
DARK = _tg[1].toggle("Dark mode", key="dark_mode", help="Switch the whole app between GMG light and dark.")
if DARK:
    st.markdown("""
<style>
  :root { color-scheme: dark; }
  .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] { background:#0A0D16 !important; }
  .stApp { color:#E5E9F4; }
  .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp p, .stApp li, .stApp label, .stApp summary,
  [data-testid="stMarkdownContainer"], [data-testid="stWidgetLabel"] * { color:#E5E9F4 !important; }
  [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] * { color:#8790AB !important; }
  [data-testid="stCaptionContainer"] strong { color:#C9D0E4 !important; }
  .topbar { border-bottom-color:#252E48 !important; }
  .topbar img { background:#FFFFFF; padding:3px 6px; border-radius:6px; }
  .topbar div[style*="Georgia"] { color:#E5E9F4 !important; }
  .topbar > div[style*="background:#E1E7E8"] { background:#252E48 !important; }
  [data-testid="stExpander"] details { background:#111626 !important; border-color:#252E48 !important; }
  [data-testid="stExpander"] summary { background:#111626 !important; color:#E5E9F4 !important; }
  [data-testid="stExpander"] summary:hover { background:#161C30 !important; }
  [data-testid="stVerticalBlockBorderWrapper"] { border-color:#252E48 !important; }
  .stTabs [data-baseweb="tab-border"] { background:#252E48 !important; }
  .stTabs [data-baseweb="tab"] p { color:#8790AB !important; }
  .stTabs [aria-selected="true"] p { color:#E5E9F4 !important; }
  [data-baseweb="select"] > div, [data-baseweb="base-input"], [data-baseweb="input"] > div, .stApp input, .stApp textarea,
  [data-testid="stNumberInputContainer"] { background:#161C30 !important; color:#E5E9F4 !important; border-color:#252E48 !important; }
  [data-baseweb="select"] div { background-color:#161C30 !important; color:#E5E9F4 !important; }
  [data-baseweb="select"], [data-baseweb="select"] > div { background-color:#161C30 !important; border-color:#2E3858 !important; }
  [data-testid="stMultiSelect"] div:not([data-baseweb="tag"]):not([data-baseweb="tag"] *),
  [data-testid="stSelectbox"] div { background-color:#161C30 !important; color:#C9D0E4 !important; border-color:#2E3858 !important; }
  [data-baseweb="select"] input { background:transparent !important; border:0 !important; box-shadow:none !important; }
  [data-baseweb="select"] input::placeholder, [data-baseweb="select"] [class*="placeholder"] { color:#7A84A3 !important;
    -webkit-text-fill-color:#7A84A3 !important; opacity:1 !important; }
  [data-testid="stChatInput"] textarea::placeholder { -webkit-text-fill-color:#7A84A3 !important; opacity:1 !important; }
  .stApp input::placeholder, .stApp textarea::placeholder, [data-testid="stMultiSelect"] input::placeholder {
    color:#8C96B4 !important; -webkit-text-fill-color:#8C96B4 !important; opacity:1 !important; }
  [data-baseweb="select"] svg { fill:#8790AB !important; }
  [data-baseweb="tag"], [data-baseweb="tag"] * { background-color:#252E48 !important; }
  [data-baseweb="popover"] ul, [data-baseweb="menu"] { background:#161C30 !important; }
  [data-baseweb="popover"] li { color:#E5E9F4 !important; }
  .stButton > button:not([kind="primary"]), .stDownloadButton > button, [data-testid="stNumberInputStepDown"],
  [data-testid="stNumberInputStepUp"] { background:#161C30 !important; color:#E5E9F4 !important; border-color:#252E48 !important; }
  [data-testid="stFileUploaderDropzone"] { background:#111626 !important; }
  [data-testid="stAlertContainer"] { background:#161C30 !important; }
  [data-testid="stAlertContainer"] * { color:#E5E9F4 !important; }
  [data-testid="stDataFrame"], [data-testid="stTable"] { filter:invert(.92) hue-rotate(180deg); }
  .kpi:not([style*="2658F2"]) { background:#111626 !important; }
  .kpi:not([style*="2658F2"]) .val { color:#E5E9F4 !important; }
  .stApp code { background:#161C30 !important; color:#8FB0FF !important; }
  .stApp h5, .stApp h6, [data-testid="stHeading"], [data-testid="stHeading"] * { color:#E5E9F4 !important; }
  [data-testid="stCode"] pre, [data-testid="stCode"] code { background:#0E1220 !important; }
  [data-testid="stChatMessage"]:has(.chat-u) { background:#161C30 !important; border-color:#252E48 !important; }
  [data-testid="stChatMessage"]:has(.chat-u) [data-testid^="stChatMessageAvatar"] { background:#2A3458 !important; }
  [data-testid="stChatMessage"]:has(.chat-a) [data-testid^="stChatMessageAvatar"] { background:#3D6BFF !important; }
  [data-testid="stChatInput"] > div, [data-testid="stChatInput"] textarea { background:#161C30 !important; color:#E5E9F4 !important;
    border-color:#2E3858 !important; }
  [data-testid="stChatInput"] textarea::placeholder { color:#7A84A3 !important; }
  [data-testid="stChatInputSubmitButton"] svg { fill:#8FB0FF !important; color:#8FB0FF !important; }
  [data-testid="stBottom"], [data-testid="stBottom"] > div, [data-testid="stBottomBlockContainer"] { background:#0A0D16 !important; }
</style>""", unsafe_allow_html=True)
THEME = "dark" if DARK else "light"

if DEV:
    st.warning("DEV PREVIEW — read-only copy on live data. Running the engine and config uploads are disabled here.")

# ATP freshness stamp — snapshot the run used + when that snapshot was loaded upstream (etl_timetsamp)
try:
    _atp = dbio.load_atp_info()
    if _atp.get("atp_snapshot_date"):
        _upd = human_ts(_atp["atp_updated_at"]) if _atp.get("atp_updated_at") else "—"
        _dmd = f" · demand window through **{_atp['as_of_date']}**" if _atp.get("as_of_date") else ""
        _tg[0].caption(f"Inventory (ATP) as of **{_atp['atp_snapshot_date']}**{_dmd} · upstream loaded **{_upd}**")
except Exception:
    pass

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
    up_size = uc[0].file_uploader(
        "Full / cut-size definition", type="csv", key="up_size", disabled=DEV,
        help="CSV, one row per key size, headers: division, ah2_description, gender, size, threshold — ALL genders "
             "incl. KIDS in one file. Adult: one ladder per division / ah2 / gender (adult apparel ah2 = APPAREL). "
             "KIDS: ah2 = sub-category (e.g. HOODIES, SANDALS); the ladder is sub-category + age band (from the "
             "kids size → age band mapping below). A store×style is FULL size when its in-stock key sizes > threshold. "
             "The upload REPLACES the whole definition, so download the current one, edit it, and re-upload the full file.")
    up_bu = uc[1].file_uploader("BU-eligibility matrix", type="csv", key="up_bu", disabled=DEV)
    up_stores = uc[2].file_uploader("Active store list (grades)", type=["csv", "xlsx"], key="up_stores", disabled=DEV)

    # full/cut-size definition: download the current one; validate any upload BEFORE it can replace the live table
    _sc_live, size_df, size_err = None, None, False
    try:
        _sc_live = dbio.load_size_curve()
        uc[0].download_button(f"⬇ Download current definition ({len(_sc_live)})",
                              data=dbio.to_csv_bytes(_sc_live), file_name="size_curve_definition.csv",
                              mime="text/csv", key="dl_size", use_container_width=True)
    except Exception:
        uc[0].caption("Current definition unavailable (sign-in/consent may be needed).")
    # kids size -> age band (id1) mapping: resolves which kids ladder a size belongs to (download / upload)
    up_bands = uc[0].file_uploader(
        "Kids size → age band (id1)", type="csv", key="up_bands", disabled=DEV,
        help="CSV, headers: size, id1 — one row per kids size and age band (e.g. 2T,l · 1C,CRIB · 1C,T). A size may "
             "sit in several bands; each style is placed in the band that best fits its size range. "
             "The upload REPLACES the whole mapping.")
    _kb_live, bands_df, bands_err = None, None, False
    try:
        _kb_live = dbio.load_kids_size_bands()
        uc[0].download_button(f"⬇ Download kids age-band mapping ({len(_kb_live)})",
                              data=dbio.to_csv_bytes(_kb_live), file_name="kids_size_band.csv",
                              mime="text/csv", key="dl_bands", use_container_width=True)
    except Exception:
        uc[0].caption("Kids age-band mapping unavailable (sign-in/consent may be needed).")
    if up_bands is not None:
        try:
            up_bands.seek(0)
            bands_df, _kb_errs, _kb_warns = dbio.validate_kids_size_bands(
                pd.read_csv(up_bands, dtype=str, keep_default_na=False))
        except Exception as e:
            bands_df, _kb_errs, _kb_warns = None, [f"Could not read the file: {e}"], []
        if _kb_errs:
            bands_err = True
            uc[0].error("Kids age-band mapping NOT accepted — fix and re-upload. The live mapping is unchanged.\n\n- "
                        + "\n- ".join(_kb_errs))
        else:
            uc[0].success(f"Valid: {len(bands_df)} size → band rows, {bands_df['id1'].nunique()} bands — "
                          "applied when you click Run.")
        for _w in _kb_warns:
            uc[0].warning(_w)
    if up_size is not None:
        try:
            up_size.seek(0)
            # dtype=str: keep sizes verbatim (otherwise an all-numeric column turns 10 into 10.0 and stops matching)
            size_df, _sc_errs, _sc_warns = dbio.validate_size_curve(
                pd.read_csv(up_size, dtype=str, keep_default_na=False), _sc_live,
                bands_df if bands_df is not None else _kb_live)   # kids ladders resolved via the mapping in force
        except Exception as e:
            size_df, _sc_errs, _sc_warns = None, [f"Could not read the file: {e}"], []
        if _sc_errs:
            size_err = True
            uc[0].error("Size definition NOT accepted — fix and re-upload. The live definition is unchanged.\n\n- "
                        + "\n- ".join(_sc_errs))
        else:
            _nk = int((size_df["gender"] == "KIDS").sum())
            _ng = size_df[size_df["gender"] != "KIDS"].groupby(["division", "ah2_description", "gender"]).ngroups
            uc[0].success(f"Valid: {len(size_df)} rows ({_ng} adult groups, {_nk} kids rows) — "
                          "applied when you click Run.")
        for _w in _sc_warns:
            uc[0].warning(_w)
    elif bands_df is not None and _sc_live is not None:
        # mapping changed on its own: re-check the LIVE definition's kids ladders against the new mapping
        _, _x_errs, _x_warns = dbio.validate_size_curve(_sc_live, None, bands_df)
        if _x_errs:
            bands_err = True
            uc[0].error("This age-band mapping does not fit the live size definition:\n\n- " + "\n- ".join(_x_errs))
        for _w in _x_warns:
            uc[0].warning(_w)
    size_err = size_err or bands_err
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
    run_categories = []
    if _sl is not None:
        _sl_countries = sorted(_sl["COUNTRY"].dropna().astype(str).str.strip().unique().tolist())
        _prov = dbio.store_list_provenance()
        st.caption(f"Active store list: **{len(_sl)} stores** across {', '.join(_sl_countries)}  ·  "
                   f"last changed by **{_prov['user']}** on {human_ts(_prov['ts'])}")
        rc = st.columns([2, 2, 1])
        run_countries = rc[0].multiselect(
            "Countries to run", _sl_countries, default=_sl_countries, key="run_countries",
            help="Scope THIS run to selected countries. The list comes from the active store list — a country "
                 "only appears if the store list has stores in it. Ephemeral — it does not edit the store list.")
        try:
            _run_cat_opts = dbio.load_run_categories()
        except Exception:
            _run_cat_opts = []
        run_categories = rc[1].multiselect(
            "Categories to run", _run_cat_opts, key="run_categories", placeholder="All categories",
            help="Scope THIS run to selected business categories (e.g. FOOTBALL). Empty = all categories. "
                 "Moves, replenishment, held and not-moved are all limited to these categories.")
        if rc[2].button("↺ Reset store list to baseline", key="reset_stores", use_container_width=True, disabled=DEV):
            try:
                n = dbio.reset_store_grades_to_baseline()
                st.success(f"Store list reset to governed baseline ({n} stores)."); st.rerun()
            except Exception as e:
                st.error(f"Reset failed: {e}")

    running = ss.run_state == "running"
    if size_err:
        st.error("Run is disabled until the size-definition / kids age-band file is fixed (or removed from the uploader).")
    if st.button("▶ Run stock movement", type="primary", disabled=DEV or cover_err or running or size_err,
                 use_container_width=True):
        with st.spinner("Submitting job…"):
            try:
                # apply any uploaded config first (brand list + category converter are engine-maintained tables)
                if up_bu is not None:
                    dbio.replace_config_table("cfg_bu_eligibility", pd.read_csv(up_bu),
                                              bool_cols=["allowed"])
                if bands_df is not None:  # mapping first: the size definition's kids ladders resolve through it
                    dbio.replace_config_table("cfg_kids_size_band", bands_df)
                if size_df is not None:   # already validated + normalised above
                    dbio.replace_config_table("cfg_size_curve", size_df)
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
                # category scope is a RUN parameter too ('|'-separated; empty => ALL)
                params["CATEGORY"] = "|".join(run_categories) if run_categories else "ALL"
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
                st.toast(f"Job submitted — run #{ss.run_id}")
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
                   "match": "Matching & sizing", "reshape": "Size-curve",
                   "receiver_guard": "Receiver full-size guard (store + DC)", "score": "Scoring & output",
                   "archive": "Archiving run to history",
                   "replen_demand": "Replenishment demand", "replen_ration": "Replenishment rationing",
                   "replen_output": "Replenishment output", "replen_history": "Archiving replenishment"}
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
        head = "Queued — waiting for a slot…" if queued else f"Running · {step}"
        with st.container(border=True):
            st.markdown(f"**{head}**")
            st.progress(pct, text=f"Step {min(done + 1, total)} of {total}  ·  {elapsed or 0}s elapsed")
            cols = st.columns([1, 1, 2])
            cols[0].caption(f"Run **#{ss.run_id}**")
            if ss.get("run_url"):
                cols[1].markdown(f"[Open in Databricks ↗]({ss.run_url})")
            if cols[2].button("↻ Refresh now"):
                st.rerun()
        try:   # agent lab, live: same board, current task states (no replay while running)
            agent_view.render(dbio.agent_job_run(ss.run_id), {}, live=True, theme=THEME)
        except Exception:
            pass
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
    st.error("The stock movement job did not complete.")
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
        st.caption(f"Viewing **your run** · by **{_who(stats.get('triggered_by'))}** · "
                   f"{human_ts(stats.get('run_ts', stats.get('computed_at', '—')))} · "
                   f"scope **{stats.get('country_scope', 'ALL')}**"
                   + (f" · category **{stats.get('category_scope')}**"
                      if str(stats.get('category_scope') or 'ALL') not in ('ALL', 'None', 'nan') else "")
                   + f"  ·  run `{VRID}`")
    else:
        st.caption("Viewing the **latest completed run** (shared across users). "
                   "Run the engine to get your own isolated view.")

    # run picker — view any recent run (each user can always return to their own or a full-scope run)
    _log = dbio.list_run_log(25)
    fh = st.columns([4, 1], vertical_alignment="bottom")
    if not _log.empty:
        _labels, _map = [], {}
        for _, r in _log.iterrows():
            lbl = (f"{human_ts(r['run_ts'])} · {_who(r.get('triggered_by'))} · "
                   f"{r['country_scope']}"
                   + (f" · {r['category_scope']}" if str(r.get('category_scope') or 'ALL') != 'ALL' else "")
                   + f" · {int(float(r['recommended_moves'] or 0))} moves")
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

    tab_agent, tab_all, tab_replen = st.tabs(
        ["Agent lab", "Store consolidation", "Replenishment (DC → store)"])

    # ============ TAB: AGENT LAB (visual layer over the engine's real run — changes nothing) ============
    with tab_agent:
        try:
            _arid = VRID
            if not _arid:
                _lg = dbio.list_run_log(1)
                _arid = str(_lg.iloc[0]["run_id"]) if not _lg.empty else None
            try:
                _runs = get_agent_runs()
            except Exception:   # no job view permission → results-only board (no stage timings)
                _runs = []
            _jr = dbio.agent_match_run(_runs, _arid) if (_arid and _runs) else None
            ss.setdefault("review_ver", 0)
            try:
                _rv = get_review_items(_arid, ss.review_ver) if _arid else None
            except Exception:
                _rv = None
            _rvs = None
            if _rv is not None:
                _dec = _rv["decision"].fillna("PENDING")
                _top = _rv["review_reason"].fillna("").str.contains("Full-size top-up")
                _rvs = {"total": int(len(_rv)), "pending": int((_dec == "PENDING").sum()),
                        "approved": int((_dec == "APPROVED").sum()), "rejected": int((_dec == "REJECTED").sum()),
                        "topup": int(_top.sum()), "other": int((~_top).sum())}
            agent_view.render(_jr, get_agent_metrics(_arid, str(stats.get("computed_at", ""))), run_id=_arid,
                              theme=THEME, review=_rvs, height=984)
        except Exception as e:
            _rv = None
            st.info(f"Agent lab unavailable for this run: {e}")

        # ---------- Human review: the approver node's queue — decide right here ----------
        st.markdown("##### Human review")
        st.caption(f"Approve or reject the lines that need a person: DC full-size top-ups, and any line worth "
                   f"≥ AED {dbio.REVIEW_MIN_AED:,.0f} or moving ≥ {dbio.REVIEW_MIN_QTY} units. Every other line is "
                   "routine and auto-approved. Decisions are saved for this run with your name.")
        if _rv is None:
            st.info("No review queue for this run.")
        elif _rv.empty:
            st.success("Nothing needs review in this run — every line is routine.")
        else:
            _LBL = dbio.REVIEW_STATUS; _CODE = {v: k for k, v in _LBL.items()}
            _rf = st.columns([1.2, 2, 1.4])
            _f_type = _rf[0].multiselect("Type", sorted(_rv["line_type"].unique()), key="rv_type",
                                         placeholder="All types", label_visibility="collapsed")
            _reasons = sorted({x for r in _rv["review_reason"].fillna("") for x in r.split(" · ") if x})
            _f_reason = _rf[1].multiselect("Reason", _reasons, key="rv_reason", placeholder="All reasons",
                                           label_visibility="collapsed")
            _f_status = _rf[2].multiselect("Status", list(_LBL.values()), default=["Pending"], key="rv_status",
                                           placeholder="All statuses", label_visibility="collapsed")
            _view = _rv.copy()
            _view["Decision"] = _view["decision"].fillna("PENDING").map(_LBL)
            if _f_type:
                _view = _view[_view["line_type"].isin(_f_type)]
            if _f_reason:
                _view = _view[_view["review_reason"].fillna("").apply(lambda r: any(x in r for x in _f_reason))]
            if _f_status:
                _view = _view[_view["Decision"].isin(_f_status)]
            _show = _view.rename(columns={
                "line_type": "Type", "review_reason": "Why review", "parent_article": "Parent",
                "generic_article_name": "Style", "article": "Article", "size": "Size", "from_site": "From",
                "to_site": "To", "qty": "Qty", "value_aed": "Value (AED)", "comment": "Comment", "decided_by": "Decided by"})
            for _c in ("Comment", "Decided by"):
                _show[_c] = _show[_c].fillna("").astype(str).replace({"None": "", "nan": ""})
            _cols = ["Decision", "Why review", "Type", "Style", "Article", "Size", "From", "To", "Qty", "Value (AED)",
                     "Comment", "Decided by"]
            _edited = st.data_editor(
                _show[_cols], key=f"rv_editor_{_arid}_{ss.review_ver}", hide_index=True, use_container_width=True,
                height=360, disabled=[c for c in _cols if c not in ("Decision", "Comment")],
                column_config={
                    "Decision": st.column_config.SelectboxColumn(options=list(_LBL.values()), required=True, width="medium"),
                    "Comment": st.column_config.TextColumn(width="medium", help="Optional note saved with the decision"),
                    "Value (AED)": st.column_config.NumberColumn(format="%,.0f", width="small"),
                    "Type": st.column_config.TextColumn(width="small"), "Size": st.column_config.TextColumn(width="small"),
                    "To": st.column_config.TextColumn(width="small"), "Qty": st.column_config.NumberColumn(width="small"),
                    "Decided by": st.column_config.TextColumn(width="small"),
                })
            _bc = st.columns([1.3, 1.3, 1.3, 3])
            _pend = _view[_view["decision"].fillna("PENDING") == "PENDING"]

            def _save(rows, msg):
                try:
                    n = dbio.save_review_decisions(_arid, rows, dbio.current_user())
                    ss.review_ver += 1
                    st.toast(f"{msg}: {n:,} line{'s' if n != 1 else ''}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not save decisions: {e}. You need write access to "
                             f"{dbio.REVIEW_FQ}; ask the app owner.")

            if _bc[0].button("Save decisions", type="primary", use_container_width=True, key="rv_save"):
                _chg = _view.assign(decision=_edited["Decision"].map(_CODE).values, comment=_edited["Comment"].values)
                _orig = _view.assign(decision=_view["decision"].fillna("PENDING"))
                _mask = (_chg["decision"].values != _orig["decision"].values) | (
                    _chg["comment"].fillna("").values != _orig["comment"].fillna("").values)
                if _mask.any():
                    _save(_chg[_mask], "Saved")
                else:
                    st.toast("No changes to save")
            if _bc[1].button(f"Approve {len(_pend):,} pending shown", use_container_width=True, key="rv_appr",
                             disabled=_pend.empty):
                _save(_pend.assign(decision="APPROVED"), "Approved")
            if _bc[2].button(f"Reject {len(_pend):,} pending shown", use_container_width=True, key="rv_rej",
                             disabled=_pend.empty):
                _save(_pend.assign(decision="REJECTED"), "Rejected")
            _bc[3].caption(f"{len(_view):,} of {len(_rv):,} review lines shown · filter, then decide in the "
                           "Decision column and Save, or use the bulk buttons.")
        # ---------- Ask the agents: a chat about this run's results, answered as the signed-in user ----------
        _ask_on = bool(dbio.ASK_SPACE_ID)
        ss.setdefault("ask_history", [])        # chronological [(question, answer dict)] for this session
        ss.setdefault("ask_conv", None)
        _ah = st.columns([6, 1])
        _ah[0].markdown("##### Ask the agents")
        if _ah[1].button("New chat", key="ask_new", use_container_width=True, disabled=not (_ask_on and ss.ask_history)):
            ss.ask_history, ss.ask_conv = [], None
            st.rerun()

        def _fmt_answer_table(cols, rows):
            df = pd.DataFrame(rows, columns=cols)
            for c in df.columns:
                num = pd.to_numeric(df[c], errors="coerce")
                if num.notna().all() and len(num):
                    whole = (num.round(0) == num).all()
                    money = any(k in c.lower() for k in ("aed", "value", "price"))
                    df[c] = num.map(lambda v: f"{v:,.0f}" if (whole or money) else f"{v:,.2f}")
            return df

        def _render_answer(a):
            if a.get("error"):
                st.warning(f"Couldn't answer: {a['error']}")
                return
            if a.get("text"):
                st.markdown(a["text"])
            if a.get("rows"):
                st.dataframe(_fmt_answer_table(a["columns"], a["rows"]), hide_index=True, use_container_width=True,
                             height=min(38 + 35 * len(a["rows"]), 320))
            if a.get("sql"):
                with st.expander("How this was answered"):
                    st.code(a["sql"], language="sql")

        _chat = st.container(height=520 if ss.ask_history else 140, border=True)
        with _chat:
            if not ss.ask_history:
                st.caption("Ask about this run's store moves, held stock, DC lines or approvals — e.g. "
                           "*Why did store 1122 get size M of NKIF1911-010?* or *Top 10 DC lines by value*. "
                           "Follow-up questions continue the conversation." if _ask_on else
                           "Coming soon: ask questions about this run's moves, holds and DC lines.")
            for _qq, _aa in ss.ask_history:
                with st.chat_message("user", avatar=":material/person:"):
                    st.markdown('<span class="chat-u"></span>', unsafe_allow_html=True)
                    st.markdown(_qq)
                with st.chat_message("assistant", avatar=":material/hub:"):
                    st.markdown('<span class="chat-a"></span>', unsafe_allow_html=True)
                    _render_answer(_aa)
        _prompt = st.chat_input("Ask the agents a question about this run…", key="ask_input", disabled=not _ask_on)
        if _prompt and _prompt.strip():
            _q = _prompt.strip()
            with _chat:
                with st.chat_message("user", avatar=":material/person:"):
                    st.markdown('<span class="chat-u"></span>', unsafe_allow_html=True)
                    st.markdown(_q)
                with st.chat_message("assistant", avatar=":material/hub:"):
                    st.markdown('<span class="chat-a"></span>', unsafe_allow_html=True)
                    with st.spinner("The agents are looking into it…"):
                        try:
                            _latest = dbio.list_run_log(1)
                            _ctx = _arid if (_arid and not _latest.empty and str(_latest.iloc[0]["run_id"]) != _arid) else None
                            _ans = dbio.ask_agents(_q, ss.ask_conv, run_id=_ctx)
                            ss.ask_conv = _ans["conversation_id"]
                        except Exception as e:
                            _ans = {"error": str(e)}
                    _render_answer(_ans)
            ss.ask_history.append((_q, _ans))
            st.rerun()
        if ss.ask_history:
            st.caption("Answers come from this engine's results only. Follow-ups continue the same conversation; "
                       "New chat starts over.")

    # ============ TAB: DC → STORE REPLENISHMENT (P1) ============
    with tab_replen:
        st.caption("**DC → store replenishment** — recommend-only. Demand = **POS rate-of-sale** (retail). "
                   "Target per size = **max(14-day demand cover, 1 facing)**, rounded up; when the in-country "
                   "DC is short, stock is **rationed** across stores (flagship→A→B→C, breadth before depth). "
                   "Built by the `replenishment_pipeline_P1` job.")
        try:
            rdf = dbio.load_replenishment()
        except Exception as e:
            st.warning(f"Could not load replenishment recommendations: {e}")
            rdf = pd.DataFrame()
        if len(rdf):
            rfc = st.columns([1.2, 1.6, 1.2, 3])
            # multi-select filters (empty = all)
            r_ctry = rfc[0].multiselect("Country", sorted(str(c) for c in rdf["country"].dropna().unique()),
                                        key="rp_ctry_m", placeholder="All countries", label_visibility="collapsed")
            r_reason = rfc[1].multiselect("Reason", sorted(str(c) for c in rdf["reason"].dropna().unique()),
                                          key="rp_reason_m", placeholder="All reasons", label_visibility="collapsed")
            r_prop = rfc[2].multiselect("Propensity", ["HIGH", "MED", "LOW", "THIN"],
                                        key="rp_prop_m", placeholder="All propensities", label_visibility="collapsed")
            rv = rdf.copy()
            if r_ctry:
                rv = rv[rv["country"].isin(r_ctry)]
            if r_reason:
                rv = rv[rv["reason"].isin(r_reason)]
            if r_prop:
                rv = rv[rv["recurrence_propensity"].isin(r_prop)]
            rk = st.columns(4)
            kpi(rk[0], "Lines", f"{len(rv):,}")
            kpi(rk[1], "Units to ship", f"{int(rv['recommended_qty'].sum()):,}")
            kpi(rk[2], "Stores", f"{rv['receiver_store'].nunique():,}")
            kpi(rk[3], "SKUs", f"{rv['article'].nunique():,}")
            rshow = rv.rename(columns={
                "country": "Country", "category": "Category", "division": "Division",
                "parent_article": "Parent", "generic_article_name": "Generic article name", "article": "Article", "size": "Size",
                "receiver_store": "Store", "source": "Source", "source_available": "DC ATP",
                "store_need": "Need", "recommended_qty": "Replen qty", "reason": "Reason",
                "demand_rate_day": "Demand/day", "protection_window": "Cover window (d)",
                "presentation_floor": "Display floor",
                "store_atp": "Store ATP (before)", "store_intransit": "Store in-transit", "store_atp_after": "Store ATP (after)",
                "unit_price_aed": "Unit price (AED)", "recurrence_propensity": "Recurrence", "decision": "Decision",
            })
            rcols = ["Country", "Category", "Division", "Parent", "Generic article name", "Article", "Size", "Store", "Reason", "Recurrence",
                     "Demand/day", "Cover window (d)", "Display floor", "Store ATP (before)", "Store in-transit", "Need",
                     "Source", "DC ATP", "Replen qty", "Store ATP (after)", "Unit price (AED)", "Decision"]
            rshow = commafy(rshow, ["DC ATP", "Store ATP (before)", "Store in-transit", "Store ATP (after)", "Need", "Replen qty"])
            rshow["Demand/day"] = rshow["Demand/day"].map(lambda v: f"{v:.3f}" if pd.notna(v) else "")
            rccfg = {
                "Reason": st.column_config.TextColumn(help="REPLEN_DEPLETION = sold then ran below cover · REPLEN_DISPLAY = display-floor driven · REPLEN_FULL_SIZE = 1 unit of a missing key size so the store becomes full-size (current season / CORE). Lines that would leave the store cut-size are held, not shown."),
                "Recurrence": st.column_config.TextColumn(help="Recurrence propensity from chain breadth (HIGH = SKU demanded at 50+ stores → likely repeat demand)."),
                "Demand/day": st.column_config.TextColumn(help="Daily demand = POS units sold / window."),
                "Cover window (d)": st.column_config.NumberColumn(help="Target days of cover — flat 14 days for all stores."),
                "Display floor": st.column_config.NumberColumn(help="Min display quantity for this size (from size_definition); 0 if not a display size."),
                "Category": st.column_config.TextColumn(help="Business category (category converter)."),
                "Division": st.column_config.TextColumn(help="Product division (product master /BIC/ZRWGH2_T) — FOOTWEAR / APPAREL."),
                "Store ATP (before)": st.column_config.TextColumn(help="Store sellable stock now (SL01), before this allocation."),
                "Store ATP (after)": st.column_config.TextColumn(help="Store stock after the replen ships = Store ATP (before) + Replen qty."),
                "Need": st.column_config.TextColumn(help="max(demand cover, display floor) − (store stock + in-transit)."),
                "Store in-transit": st.column_config.TextColumn(help="Units already on the way to the store (counted before replenishing)."),
                "DC ATP": st.column_config.TextColumn(help="In-country DC sellable stock for this SKU (the shared pool)."),
                "Replen qty": st.column_config.TextColumn(help="Rationed units to ship from DC → store (whole units)."),
                "Decision": st.column_config.TextColumn(help="'Review - moving to make full size' = the DC tops up missing key sizes so this store displays a full run; review the bundle together. Otherwise reviewer marks Approve / Adjust / Keep."),
            }
            rtop = st.columns([3, 1])
            rtop[0].caption(f"{len(rv):,} of {len(rdf):,} replenishment lines")
            rtop[1].download_button("Export (Excel)", data=to_xlsx(rshow[rcols]),
                                    file_name="replenishment_recommendations.xlsx",
                                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                    use_container_width=True)
            st.dataframe(rshow[rcols], hide_index=True, use_container_width=True, height=460, column_config=rccfg)
            st.caption("Recommend-only · P1 scope: footwear + apparel, in-country DC, short-lead. "
                       "Cross-border depth & the sparse one-off tail are out of scope (allocation / consolidation).")
        else:
            st.info("No replenishment recommendations available — run the `replenishment_pipeline_P1` job "
                    "(reads `sports_analytics_test.nike_consolidation.replen_recommendations`).")

    # ============ TAB 0: ALL — one combined table, all move columns + Action ============
    with tab_all:
        st.caption("Every store×article line in **one table** — moves, held and not-moved combined — with **all the "
                   "columns from Recommended moves**. Where a value doesn't apply (e.g. no receiver for a *dont move* "
                   "row) it shows **NA**. Final column = **Action** (move / held / dont move).")
        st.markdown("<div style='font-size:11px;font-weight:700;letter-spacing:.06em;color:#8891A6;'>FILTER</div>",
                    unsafe_allow_html=True)
        ac = st.columns([1.1, 1.2, 1.4, 1.2, 1.6, 1.1])
        # multi-select filters (empty = all)
        act_sel = tuple(ac[0].multiselect("Action", ["move", "held", "dont move"], key="all_action_m",
                                          placeholder="All actions", label_visibility="collapsed"))
        ctry_sel = tuple(ac[1].multiselect("Country", sorted(str(c) for c in df["country"].dropna().unique()),
                                           key="all_ctry_m", placeholder="All countries", label_visibility="collapsed"))
        cat_sel = tuple(ac[2].multiselect("Category", sorted(str(c) for c in df["business_category"].dropna().unique()),
                                          key="all_cat_m", placeholder="All categories", label_visibility="collapsed"))
        brand_sel = tuple(ac[3].multiselect("Brand", BRANDS, key="all_brand_m",
                                            placeholder="All brands", label_visibility="collapsed"))
        srch = ac[4].text_input("Search", placeholder="Article / name / size…", key="all_search",
                                label_visibility="collapsed")
        a_arg, c_arg, cat_arg, br_arg = (list(act_sel) or None, list(ctry_sel) or None,
                                         list(cat_sel) or None, list(brand_sel) or None)
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
                 "generic_article_name": "Generic article name", "article_name": "Article name", "size": "Size", "season": "Season",
                 "donor_store": "Donor", "donor_store_name": "Donor name", "donor_available_sizes": "Donor sizes",
                 "donor_available_sizes_post_move": "Donor sizes post-move", "donor_size_status_post_move": "Donor full/cut",
                 "donor_ros": "Donor ROS", "donor_parent_ros": "Donor parent ROS", "donor_cover_days": "Donor cover (d)",
                 "donor_stock": "Donor ATP", "donor_intransit": "Donor in-transit", "donor_apo": "Donor APO",
                 "reason": "Reason", "receiver_store": "Receiver", "receiver_store_name": "Receiver name",
                 "receiver_available_sizes": "Recv sizes", "receiver_available_sizes_post_move": "Recv sizes post-move",
                 "receiver_size_status_post_move": "Recv full/cut",
                 "receiver_ros": "Recv ROS",
                 "receiver_parent_ros": "Recv parent ROS", "receiver_cover_days": "Recv cover (d)",
                 "receiver_stock": "Recv ATP", "receiver_intransit": "Recv in-transit", "receiver_apo": "Recv APO",
                 "qty": "Proposed transfer qty", "donor_cover_post": "Donor cover post-move (d)",
                 "receiver_cover_post": "Recv cover post-move (d)", "inventory_cost_aed": "Inv. cost (AED)",
                 "uplift": "Est. uplift*", "net_benefit": "Net benefit*", "movement_note": "Why moved",
                 "action": "Action"}
        A_ORDER = ["Country", "Category", "Gender", "Brand", "Division", "Demand", "Parent", "Article", "Generic article name", "Article name",
                   "Size", "Season", "Donor", "Donor name", "Donor sizes", "Donor sizes post-move", "Donor full/cut", "Donor ROS", "Donor parent ROS",
                   "Donor cover (d)", "Donor ATP", "Donor in-transit", "Donor APO", "Reason", "Receiver",
                   "Receiver name", "Recv sizes", "Recv sizes post-move", "Recv full/cut", "Recv ROS", "Recv parent ROS", "Recv cover (d)", "Recv ATP",
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
        st.caption(f"Showing up to 5,000 rows of **{', '.join(act_sel) if act_sel else 'all actions'}** · download = full combined file "
                   "(move + held + dont move).")
        st.dataframe(cdf, hide_index=True, use_container_width=True, height=460)

    # ---- definitions (brief) — reference material, kept at the bottom so the Agent lab sits higher ----
    with st.expander("Definitions"):
        st.markdown("""
- **ROS** — units sold/day at a store over the ROS window (0 = dead).
- **Cover (days)** — ATP ÷ ROS (blank = dead/∞).
- **ATP** — sellable stock (SL01). **This is the only stock figure the engine uses** for cover, donor surplus and receiver need.
- **In-transit** — units on the way to the store (latest snapshot). **Counted for the receiving store** — a receiver's need already nets off stock inbound to it, so the engine won't over-ship.
- **Generic article name** — parent style name without the size (product master parent row; fallback = article name minus the size).
- **Article name** — product description (product master TXTMD).
- **APO** — Y if an APO key is present for that parent at the store (from the ATP table, rolled up from child to parent). The receiver APO reads N by design — APO parents are already excluded from receivers.
- **Reason** — Dead (no sales) or Slow-moving (cover over threshold).
- **Proposed transfer qty** — units to move (donor keeps its reserve; receiver filled to target cover).
- **Cover post-move** — cover after the move: donor (ATP−qty)/ROS, receiver (ATP+qty)/ROS.
- **Est. uplift\\*** — qty × receiver net sales/unit × **probability to sell at receiver**.
- **Net benefit\\*** — uplift − movement cost (assumed **AED 5/unit**). *(\\* provisional until real cost & probability-to-sell are confirmed.)*
- **Season** — the article's Nike season (Jan–Mar Spring, Apr–Jun Summer, Jul–Sep Fall, Oct–Dec Holiday) + year; CORE/YEAR = carried all-year.

**The Action column accounts for every store×article line with ATP** (Moved ⊎ Held ⊎ Not moved):
- **move** — proposed donor→receiver transfers.
- **Held** — donor surplus that *would* move but a soft guard blocked it, so business can override. **Held reason:** *Size-curve break* (would break the donor's full-size run), *Season-freshness* (a would-be receiver can't stock this season), *APO* (the would-be receiver already has an inbound APO for this parent). *Receiver stays cut-size* (after ATP + in-transit + all store and DC moves the receiver still can't display a full run). *Move-back <30d* (the article left that receiver in the last 30 days). Each row shows a **would-be receiver** + **indicative qty** (min of donor surplus and receiver need) and a blank **Decision** column to mark Approve/Keep.
- **Not moved** — everything retained, with a reason: *no receiving store needs this size*, *new to store*, or *retained – selling within cover* (healthy).

**Why moved** *(tag on every line):*
- *Moved - donor stays full-size* — donor gives from surplus; its size run stays complete after giving.
- *Moved - donor cut — full remaining run consolidated to one receiver to display* — the donor's run was already broken (cut-size), so its **entire remaining giveable run is consolidated into one receiver** so that store can display the full size ladder.
- *Moved - run released & consolidated to a faster receiver (>=25%) to display* — moving would break the donor's (dead/slow) run, but a receiver sells **≥25% faster**, so the run is released and consolidated into that faster receiver to display the full ladder.
- *Moved - no size rule (fallback)* — item has no size definition (e.g. kids/accessories); moved on demand alone, without the size-curve check.

Every store×size sits in exactly one **Action**: **move** (to action), **held** (would move but a soft guard blocked it — size-curve break / season-freshness / APO — with an indicative qty for a business call), or **dont move** (no receiving store needs the size / new-to-store / retained & selling within cover).
        """)
