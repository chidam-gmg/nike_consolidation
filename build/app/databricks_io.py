"""Data access for the Nike Consolidation Streamlit app.

Reads results via the SQL warehouse and triggers the 00->06 pipeline via the Jobs API,
using the Databricks SDK. In a deployed Databricks App the SDK auto-authenticates with the
app's service principal; locally it falls back to the `gmg-sso` CLI profile.
"""
from __future__ import annotations

import os
import time
import pandas as pd
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState
from databricks.sdk.service import jobs

CATALOG = "sports_analytics_test"
SCHEMA = "nike_consolidation"
FQ = f"{CATALOG}.{SCHEMA}"

WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "1c9706d9594f1f2f")
NB_DIR = os.environ.get(
    "NIKE_PIPELINE_DIR",
    "/Users/chidambaram.s@gmg.com/nike-consolidation-pipeline",
)
PROFILE = os.environ.get("DATABRICKS_CONFIG_PROFILE", "gmg-sso")

# Ordered pipeline tasks (task_key, notebook)
PIPELINE = [
    ("config", "00_config_validation"),
    ("effective_stock", "01_effective_stock"),
    ("classify", "02_ros_cover_classify"),
    ("pools", "03_pools_guards"),
    ("match", "04_match_size"),
    ("reshape", "05_size_curve"),
    ("score", "06_score_output"),
]


_IN_APP = bool(os.environ.get("DATABRICKS_APP_NAME"))


def get_client() -> WorkspaceClient:
    # Deployed app: default auth (service principal). Local: named profile.
    if os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_CLIENT_ID"):
        return WorkspaceClient()
    return WorkspaceClient(profile=PROFILE)


def run_sql(sql: str) -> pd.DataFrame:
    """In a deployed app, run SQL as the signed-in viewer via on-behalf-of-user auth
    (forwarded token) so queries use the viewer's UC + warehouse access — no service-principal
    grants needed. Locally, use the SDK with the CLI profile."""
    if _IN_APP:
        return _run_sql_obo(sql)
    return _run_sql_sdk(sql)


def _run_sql_obo(sql: str) -> pd.DataFrame:
    import requests
    import streamlit as st
    from databricks.sdk.core import Config

    token = st.context.headers.get("x-forwarded-access-token")
    if not token:
        raise RuntimeError("No user token forwarded by the platform. Sign out and reopen the app, "
                           "then accept the consent screen requesting SQL access.")
    host = Config().host.rstrip("/")
    hdr = {"Authorization": f"Bearer {token}"}
    r = requests.post(f"{host}/api/2.0/sql/statements", headers=hdr, timeout=60, json={
        "statement": sql, "warehouse_id": WAREHOUSE_ID,
        "wait_timeout": "30s", "disposition": "INLINE", "format": "JSON_ARRAY"})
    if r.status_code != 200:
        raise RuntimeError(f"SQL API error {r.status_code}: {r.text[:400]}")
    out = r.json()
    while out["status"]["state"] in ("PENDING", "RUNNING"):
        time.sleep(2)
        out = requests.get(f"{host}/api/2.0/sql/statements/{out['statement_id']}",
                           headers=hdr, timeout=60).json()
    if out["status"]["state"] != "SUCCEEDED":
        raise RuntimeError(f"Query failed: {out['status'].get('error', out['status'])}")
    sid = out["statement_id"]
    cols = [c["name"] for c in out["manifest"]["schema"]["columns"]] if out.get("manifest", {}).get("schema", {}).get("columns") else []
    # page ALL result chunks — an INLINE result can span multiple chunks (e.g. recommendations ~10.7K rows =
    # 2 chunks). Reading only the first chunk silently drops the tail (was truncating ~1.5K rows / late countries).
    res = out.get("result", {}) or {}
    rows = list(res.get("data_array", []) or [])
    nxt = res.get("next_chunk_index")
    while nxt is not None:
        ch = requests.get(f"{host}/api/2.0/sql/statements/{sid}/result/chunks/{nxt}",
                          headers=hdr, timeout=120).json()
        rows += list(ch.get("data_array", []) or [])
        nxt = ch.get("next_chunk_index")
    return pd.DataFrame(rows, columns=cols)


def _run_sql_sdk(sql: str) -> pd.DataFrame:
    w = get_client()
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=sql, wait_timeout="30s")
    sid = resp.statement_id
    state = resp.status.state
    while state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(2)
        resp = w.statement_execution.get_statement(sid)
        state = resp.status.state
    if state != StatementState.SUCCEEDED:
        msg = resp.status.error.message if resp.status and resp.status.error else "unknown error"
        raise RuntimeError(f"SQL failed: {msg}")
    cols = [c.name for c in resp.manifest.schema.columns] if resp.manifest.schema.columns else []
    rows = list(resp.result.data_array or []) if resp.result else []
    nxt = resp.result.next_chunk_index if resp.result else None
    while nxt is not None:   # page all chunks (see _run_sql_obo note)
        ch = w.statement_execution.get_statement_result_chunk_n(sid, nxt)
        rows += list(ch.data_array or [])
        nxt = ch.next_chunk_index
    return pd.DataFrame(rows, columns=cols)


def _http_json_retry(url: str, hdr: dict | None = None, tries: int = 4, timeout: int = 300):
    """GET `url` and parse JSON, retrying transient network failures with exponential backoff.
    Presigned cloud-storage chunk downloads occasionally drop mid-stream ('Connection broken:
    IncompleteRead') — without a retry a single blip aborts a whole multi-hundred-MB export."""
    import requests, json as _json, http.client
    try:
        from urllib3.exceptions import ProtocolError
    except Exception:                                            # pragma: no cover
        ProtocolError = None
    retriable = (requests.exceptions.ChunkedEncodingError,
                 requests.exceptions.ConnectionError,
                 requests.exceptions.Timeout,
                 http.client.IncompleteRead, _json.JSONDecodeError)
    if ProtocolError is not None:
        retriable = retriable + (ProtocolError,)
    last = None
    for attempt in range(tries):
        try:
            r = requests.get(url, headers=hdr, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except retriable as e:
            last = e
            if attempt < tries - 1:
                time.sleep(min(2 ** attempt, 8))                 # 1s, 2s, 4s, 8s
    raise RuntimeError(f"Chunk download failed after {tries} attempts: {last}")


def _run_external(sql: str):
    """Submit `sql` with disposition=EXTERNAL_LINKS, poll to completion, return
    (columns, statement_id, host, hdr, first_page_links). Auth = forwarded user token in-app else CLI profile."""
    import requests
    if _IN_APP:
        import streamlit as st
        from databricks.sdk.core import Config
        token = st.context.headers.get("x-forwarded-access-token")
        if not token:
            raise RuntimeError("No user token forwarded by the platform. Sign out and reopen the app, "
                               "then accept the consent screen requesting SQL access.")
        host = Config().host.rstrip("/"); hdr = {"Authorization": f"Bearer {token}"}
    else:
        w = get_client(); host = w.config.host.rstrip("/"); hdr = w.config.authenticate()
    out = requests.post(f"{host}/api/2.0/sql/statements", headers=hdr, timeout=60, json={
        "statement": sql, "warehouse_id": WAREHOUSE_ID, "wait_timeout": "30s",
        "disposition": "EXTERNAL_LINKS", "format": "JSON_ARRAY"}).json()
    sid = out["statement_id"]
    while out["status"]["state"] in ("PENDING", "RUNNING"):
        time.sleep(2)
        out = requests.get(f"{host}/api/2.0/sql/statements/{sid}", headers=hdr, timeout=60).json()
    if out["status"]["state"] != "SUCCEEDED":
        raise RuntimeError(f"Query failed: {out['status'].get('error', out['status'])}")
    cols = [c["name"] for c in out["manifest"]["schema"]["columns"]]
    ext = (out.get("result", {}) or {}).get("external_links", []) or []
    return cols, sid, host, hdr, ext


def _iter_external_pages(sid, host, hdr, first_links):
    """Yield each EXTERNAL_LINKS chunk's list-of-rows in order (every GET retry-protected).
    Only one chunk is held at a time — the caller decides whether to accumulate or stream out."""
    ext = first_links
    while ext:
        nxt = None
        for lk in ext:
            # presigned cloud-storage URL — do NOT send the workspace bearer token on this GET
            yield _http_json_retry(lk["external_link"]) or []
            nxt = lk.get("next_chunk_index")
        if nxt is None:
            break
        md = _http_json_retry(f"{host}/api/2.0/sql/statements/{sid}/result/chunks/{nxt}", hdr) or {}
        ext = md.get("external_links", []) or []


def run_sql_full(sql: str) -> pd.DataFrame:
    """Full-table export for results that exceed the 25MB INLINE cap (e.g. the ~600K-row not_moved table).
    Uses `disposition=EXTERNAL_LINKS` (presigned per-chunk URLs, retry-protected). Materialises a DataFrame;
    for very large exports prefer `run_sql_to_csv_gz`, which streams and keeps memory bounded."""
    cols, sid, host, hdr, ext = _run_external(sql)
    rows = []
    for page in _iter_external_pages(sid, host, hdr, ext):
        rows += page
    return pd.DataFrame(rows, columns=cols)


def run_sql_to_csv_gz(sql: str):
    """Stream an EXTERNAL_LINKS result straight into a gzip-compressed CSV buffer WITHOUT ever holding
    the full row set (or a DataFrame) in memory — peak memory is one chunk + the (compressed) buffer,
    regardless of total size. Returns (gzip_bytes, row_count). CSV format matches `to_csv_gz_bytes`
    (UTF-8 BOM, all fields quoted)."""
    import csv as _csv, gzip, io
    cols, sid, host, hdr, ext = _run_external(sql)
    buf = io.BytesIO()
    gz = gzip.GzipFile(fileobj=buf, mode="wb")
    wrap = io.TextIOWrapper(gz, encoding="utf-8-sig", newline="")
    w = _csv.writer(wrap, quoting=_csv.QUOTE_ALL)
    w.writerow(cols)
    n = 0
    for page in _iter_external_pages(sid, host, hdr, ext):
        for row in page:
            w.writerow(["" if v is None else v for v in row])
            n += 1
    wrap.flush(); wrap.detach(); gz.close()
    return buf.getvalue(), n


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """Human-readable CSV: UTF-8 with BOM (Excel-friendly) + all fields quoted so sizes like 9-11 / 30-30
    are NOT date-coerced by Excel."""
    import csv as _csv
    return df.to_csv(index=False, quoting=_csv.QUOTE_ALL).encode("utf-8-sig")


def to_csv_gz_bytes(df: pd.DataFrame) -> bytes:
    """Gzip-compressed CSV, STREAMED (no giant intermediate string). Used for very large exports
    (e.g. full not_moved ~565K rows / ~290MB) which otherwise exceed Streamlit's ~200MB websocket
    download limit and never surface a download. Same UTF-8-BOM + QUOTE_ALL as to_csv_bytes."""
    import csv as _csv, gzip, io
    buf = io.BytesIO()
    gz = gzip.GzipFile(fileobj=buf, mode="wb")
    wrap = io.TextIOWrapper(gz, encoding="utf-8-sig", newline="")
    df.to_csv(wrap, index=False, quoting=_csv.QUOTE_ALL)
    wrap.flush(); wrap.detach(); gz.close()
    return buf.getvalue()


SCALAR_PARAMS = ["ROS_WINDOW_DAYS", "IN_TRANSIT_LOOKBACK_DAYS", "SLOW_MOVING_THRESHOLD_DAYS",
                 "NEW_TO_STORE_DAYS", "PROTECT_COVER_DAYS", "TARGET_COVER_DAYS"]
JOB_ID = os.environ.get("NIKE_JOB_ID")


def submit_pipeline(params: dict) -> dict:
    """Trigger the pipeline via run_now on the persistent job (NIKE_JOB_ID) so it runs as the job owner
    (prod access). All tunable params + COUNTRY are passed as **job parameters** — the config notebook
    reads them straight from its widgets, so no OBO SQL write to cfg_scalar_params is needed (that write
    ran as the viewer and was a silent failure point). Returns {run_id, run_url}.
    Fallback (no NIKE_JOB_ID): one-time multi-task submit (dev)."""
    w = get_client()
    if JOB_ID:
        job_params = {"COUNTRY": str(params.get("COUNTRY", "ALL"))}
        for k in SCALAR_PARAMS:
            if k in params:
                job_params[k] = str(int(params[k]))
        # per-run identity so the app reads back exactly ITS run (isolation) + attributes it
        if params.get("RUN_ID"):        job_params["RUN_ID"] = str(params["RUN_ID"])
        if params.get("TRIGGERED_BY"):  job_params["TRIGGERED_BY"] = str(params["TRIGGERED_BY"])
        run = w.jobs.run_now(job_id=int(JOB_ID), job_parameters=job_params)
        url = None
        try:
            url = w.jobs.get_run(run.run_id).run_page_url
        except Exception:
            pass
        return {"run_id": run.run_id, "run_url": url}
    # dev fallback
    base = {k: str(v) for k, v in params.items()}
    tasks = []
    for i, (key, nb) in enumerate(PIPELINE):
        tasks.append(jobs.SubmitTask(
            task_key=key,
            notebook_task=jobs.NotebookTask(
                notebook_path=f"{NB_DIR}/{nb}",
                base_parameters=base if key == "config" else None),
            depends_on=[jobs.TaskDependency(task_key=PIPELINE[i - 1][0])] if i else None))
    run = w.jobs.submit(run_name="nike_consolidation_app", tasks=tasks)
    return {"run_id": run.run_id, "run_url": None}


def run_status(run_id: int) -> dict:
    w = get_client()
    r = w.jobs.get_run(run_id)
    life = r.state.life_cycle_state.value if r.state and r.state.life_cycle_state else None
    result = r.state.result_state.value if r.state and r.state.result_state else None
    msg = r.state.state_message if r.state else None
    tasks = r.tasks or []
    total = len(tasks) or len(PIPELINE)
    done, current, err = 0, None, None
    for t in tasks:
        ls = t.state.life_cycle_state.value if t.state and t.state.life_cycle_state else None
        rs = t.state.result_state.value if t.state and t.state.result_state else None
        if rs in ("SUCCESS", "SKIPPED"):
            done += 1
        elif ls in ("RUNNING", "PENDING") and current is None:
            current = t.task_key
        if rs == "FAILED" and err is None:
            err = f"Task '{t.task_key}' failed"
    # elapsed derived from the server-side start_time (survives websocket reconnects / session resets)
    elapsed = None
    if r.start_time:
        end_ms = r.end_time or int(time.time() * 1000)
        elapsed = max(0, int((end_ms - r.start_time) / 1000))
    return {"life_cycle": life, "result": result, "message": msg, "error": err,
            "current_task": current, "done": done, "total": total,
            "elapsed": elapsed, "run_url": r.run_page_url,
            "queued": life in ("PENDING", "QUEUED") and not current}


def latest_run() -> dict:
    """Most recent run of the persistent job — used to surface status even if the app was reopened
    (session_state lost). Returns {} when there is no job / no runs."""
    if not JOB_ID:
        return {}
    w = get_client()
    runs = list(w.jobs.list_runs(job_id=int(JOB_ID), limit=1))
    if not runs:
        return {}
    return run_status(runs[0].run_id) | {"run_id": runs[0].run_id}


def current_user() -> str:
    """Best-effort identity of the logged-in user (for run attribution). In-app: the platform-forwarded
    email header; locally: the CLI profile user."""
    if _IN_APP:
        try:
            import streamlit as st
            h = st.context.headers
            return (h.get("X-Forwarded-Email") or h.get("x-forwarded-email")
                    or h.get("X-Forwarded-Preferred-Username") or "app-user")
        except Exception:
            return "app-user"
    try:
        return get_client().current_user.me().user_name
    except Exception:
        return "cli-user"


def list_run_log(limit=25) -> pd.DataFrame:
    """Recent runs (from run_stats_history) for the run picker — run_id, when, who, scope, headline metrics.
    Empty until the first run after per-run attribution shipped (triggered_by column)."""
    try:
        return run_sql(f"""
            SELECT run_id, run_ts, triggered_by, country_scope,
                   recommended_moves, held_lines, not_moved_lines
            FROM {FQ}.run_stats_history ORDER BY run_ts DESC LIMIT {int(limit)}""")
    except Exception:
        return pd.DataFrame()


# ---- reads used by the UI ----
def _src(base: str, run_id=None) -> str:
    """Table reference for a reader. With run_id → that run's rows from the per-run *_history archive
    (per-user run isolation); without → the global 'latest' table. Returns a SQL snippet usable anywhere
    a table name is (a subquery when run-scoped, so aliases/joins/UNIONs work unchanged)."""
    if run_id:
        rid = str(run_id).replace("'", "''")
        return f"(SELECT * FROM {FQ}.{base}_history WHERE run_id = '{rid}')"
    return f"{FQ}.{base}"


def load_recommendations(run_id=None) -> pd.DataFrame:
    df = run_sql(f"""
        SELECT country, parent_article, r.article, article_name, size, business_category, b.brand AS brand, gender, division,
               article_season, demand_type, donor_apo, receiver_apo,
               donor_store, donor_store_name, donor_bu, donor_available_sizes, donor_available_sizes_post_move,
               receiver_store, receiver_store_name, receiver_bu, receiver_available_sizes, receiver_available_sizes_post_move,
               recommended_qty, reason, donor_stock, donor_intransit, receiver_stock, receiver_intransit,
               receiver_units_in, receiver_stock_post_move,
               donor_cover_days, receiver_cover_days, donor_ros, receiver_ros, donor_parent_ros, receiver_parent_ros,
               donor_cover_post_move, receiver_cover_post_move,
               donor_run_before, donor_run_after, receiver_run_before, receiver_run_after,
               unit_cost_aed, inventory_cost_aed,
               reshape_action, movement_note, expected_sales_uplift, movement_cost, net_benefit
        FROM {_src('recommendations', run_id)} r LEFT JOIN {_BRAND_SUB} b ON r.article = b.art
        ORDER BY country, parent_article, receiver_store, donor_store, r.article
    """)
    numeric = ["recommended_qty", "donor_stock", "donor_intransit", "receiver_stock", "receiver_intransit",
               "receiver_units_in", "receiver_stock_post_move",
               "donor_cover_days", "receiver_cover_days", "donor_ros", "receiver_ros", "donor_parent_ros", "receiver_parent_ros",
               "donor_cover_post_move", "receiver_cover_post_move",
               "donor_run_before", "donor_run_after", "receiver_run_before", "receiver_run_after", "unit_cost_aed",
               "inventory_cost_aed", "expected_sales_uplift", "movement_cost", "net_benefit"]
    for c in numeric:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def load_run_stats(run_id=None) -> dict:
    df = run_sql(f"SELECT * FROM {_src('run_stats', run_id)}")
    return df.iloc[0].to_dict() if not df.empty else {}


def load_not_moved_summary(run_id=None) -> pd.DataFrame:
    """Small summary of not-moved stock by reason × country (the 'complete picture' headline)."""
    df = run_sql(f"""
        SELECT not_moved_reason, country, COUNT(*) AS lines, ROUND(SUM(effective_stock), 0) AS units,
               ROUND(SUM(inventory_cost_aed), 0) AS inventory_cost
        FROM {_src('not_moved', run_id)} GROUP BY not_moved_reason, country ORDER BY lines DESC
    """)
    for c in ["lines", "units", "inventory_cost"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _lit(s):
    return "'" + str(s).replace("'", "''") + "'"


def _like(col, term):
    return f"UPPER({col}) LIKE '%" + str(term).upper().replace("'", "''").replace("%", "") + "%'"


def _nm_where(country, reason, article):
    where = []
    if country and country != "All countries": where.append(f"country = {_lit(country)}")
    if reason and reason != "All reasons": where.append(f"not_moved_reason = {_lit(reason)}")
    if article: where.append(f"({_like('parent_article', article)} OR {_like('article', article)} OR {_like('article_name', article)})")
    return ("WHERE " + " AND ".join(where)) if where else ""


def load_not_moved(country=None, reason=None, category=None, article=None, brand=None, limit=5000, run_id=None):
    """Bounded, filtered slice of not_moved (never load all in the browser). Returns (df, total_count)."""
    w = _nm_where(country, reason, article)
    if brand and brand != "All brands":
        cond = f"b.brand = {_lit(brand)}"
        w = (w + " AND " + cond) if w else ("WHERE " + cond)
    j = f"{_src('not_moved', run_id)} n LEFT JOIN {_BRAND_SUB} b ON n.article = b.art"
    total = int(run_sql(f"SELECT COUNT(*) n FROM {j} {w}").iloc[0]["n"])
    df = run_sql(f"""
        SELECT country, business_category, gender, division, parent_article, n.article, article_name, size, article_season,
               store_id, store_name, b.brand AS brand, bu, class, apo_key, available_sizes, effective_stock, intransit_qty, inventory_cost_aed,
               ros, parent_ros, cover_days, not_moved_reason
        FROM {j} {w}
        ORDER BY country, not_moved_reason, store_id, parent_article, n.article
        LIMIT {int(limit)}
    """)
    for c in ["effective_stock", "intransit_qty", "inventory_cost_aed", "ros", "parent_ros", "cover_days"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df, total


def load_not_moved_full(country=None, reason=None, article=None, brand=None, run_id=None) -> pd.DataFrame:
    """Full (unbounded) not_moved for CSV export — all columns, chunk-paged."""
    w = _nm_where(country, reason, article)
    if brand:
        cond = f"b.brand = {_lit(brand)}"
        w = (w + " AND " + cond) if w else ("WHERE " + cond)
        return run_sql_full(f"SELECT n.* FROM {_src('not_moved', run_id)} n LEFT JOIN {_BRAND_SUB} b ON n.article = b.art {w} "
                            "ORDER BY n.country, n.not_moved_reason, n.store_id, n.parent_article, n.article")
    return run_sql_full(f"SELECT country, business_category, gender, division, parent_article, article, article_name, size, "
                        f"article_season, store_id, store_name, bu, class, apo_key, available_sizes, effective_stock, "
                        f"intransit_qty, inventory_cost_aed, ros, parent_ros, cover_days, not_moved_reason "
                        f"FROM {_src('not_moved', run_id)} {w} ORDER BY country, not_moved_reason, store_id, parent_article, article")


def load_held_summary(run_id=None) -> pd.DataFrame:
    """Summary of held-by-constraint stock by reason × country. `units`/`inventory_cost` are the INDICATIVE
    (actually movable) figures from the real allocation — not the donor's full sitting inventory."""
    df = run_sql(f"""
        SELECT held_reason, country, COUNT(*) AS lines, ROUND(SUM(indicative_qty), 0) AS units,
               ROUND(SUM(indicative_value_aed), 0) AS inventory_cost
        FROM {_src('held', run_id)} GROUP BY held_reason, country ORDER BY lines DESC
    """)
    for c in ["lines", "units", "inventory_cost"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _held_where(country, reason, article):
    where = []
    if country and country != "All countries": where.append(f"country = {_lit(country)}")
    if reason and reason != "All reasons": where.append(f"held_reason = {_lit(reason)}")
    if article: where.append(f"({_like('parent_article', article)} OR {_like('article', article)} OR {_like('article_name', article)})")
    return ("WHERE " + " AND ".join(where)) if where else ""


def load_held(country=None, reason=None, category=None, article=None, limit=5000, run_id=None):
    """Bounded, filtered slice of held (ordered by inventory value so the biggest items surface).
    Returns (df, total_count)."""
    w = _held_where(country, reason, article)
    total = int(run_sql(f"SELECT COUNT(*) n FROM {_src('held', run_id)} {w}").iloc[0]["n"])
    df = run_sql(f"""
        SELECT country, business_category, gender, division, parent_article, h.article, article_name, size, article_season,
               donor_store, donor_store_name, b.brand AS brand, bu, class, apo_key, held_reason,
               would_be_receiver, would_be_receiver_name, would_be_receiver_ros, indicative_qty, indicative_value_aed,
               available_sizes, effective_stock, intransit_qty, inventory_cost_aed, ros, parent_ros, cover_days, decision
        FROM {_src('held', run_id)} h LEFT JOIN {_BRAND_SUB} b ON h.article = b.art {w}
        ORDER BY indicative_value_aed DESC, country, donor_store, parent_article, h.article
        LIMIT {int(limit)}
    """)
    for c in ["would_be_receiver_ros", "indicative_qty", "indicative_value_aed", "effective_stock", "intransit_qty",
              "inventory_cost_aed", "ros", "parent_ros", "cover_days"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df, total


def load_held_full(country=None, reason=None, article=None, brand=None, run_id=None) -> pd.DataFrame:
    """Full (unbounded) held for CSV export — all columns, chunk-paged."""
    w = _held_where(country, reason, article)
    cols = ("h.country, h.business_category, h.gender, h.division, h.parent_article, h.article, h.article_name, "
            "h.size, h.article_season, h.donor_store, h.donor_store_name, h.bu, h.class, h.apo_key, h.held_reason, "
            "h.would_be_receiver, h.would_be_receiver_name, h.would_be_receiver_ros, h.indicative_qty, "
            "h.indicative_value_aed, h.available_sizes, h.effective_stock, h.intransit_qty, h.inventory_cost_aed, "
            "h.ros, h.parent_ros, h.cover_days, h.decision")
    j = f"{_src('held', run_id)} h LEFT JOIN {_BRAND_SUB} b ON h.article = b.art"
    if brand:
        cond = f"b.brand = {_lit(brand)}"
        w = (w + " AND " + cond) if w else ("WHERE " + cond)
    return run_sql_full(f"SELECT {cols} FROM {j} {w} "
                        "ORDER BY h.inventory_cost_aed DESC, h.country, h.donor_store, h.parent_article, h.article")


# ---- combined single-file export (move + held + no-move), same detail as the tabs ----
# brand from the article master RF_BNDID (a distinct lookup, joined per article); placed after gender.
_BRAND_SUB = ("(SELECT MATERIAL AS art, MAX(`RF_BNDID`) AS brand "
              "FROM prod_mena.gold.mena_sports_product_master_view GROUP BY MATERIAL)")
_COMBINED_SQL = """
SELECT r.country, r.business_category AS category, r.gender, b.brand, r.division, r.demand_type,
  r.parent_article AS parent, r.article, r.article_name, r.size, r.article_season AS season,
  r.donor_store, r.donor_store_name, r.donor_available_sizes, r.donor_available_sizes_post_move,
  CAST(r.donor_ros AS DOUBLE) donor_ros, CAST(r.donor_parent_ros AS DOUBLE) donor_parent_ros,
  CAST(r.donor_cover_days AS DOUBLE) donor_cover_days, CAST(r.donor_stock AS INT) donor_stock,
  CAST(r.donor_intransit AS INT) donor_intransit, r.donor_apo,
  r.reason, r.receiver_store, r.receiver_store_name, r.receiver_available_sizes, r.receiver_available_sizes_post_move,
  CAST(r.receiver_ros AS DOUBLE) receiver_ros, CAST(r.receiver_parent_ros AS DOUBLE) receiver_parent_ros,
  CAST(r.receiver_cover_days AS DOUBLE) receiver_cover_days, CAST(r.receiver_stock AS INT) receiver_stock,
  CAST(r.receiver_intransit AS INT) receiver_intransit, r.receiver_apo,
  CAST(r.recommended_qty AS INT) qty,
  CAST(r.donor_cover_post_move AS DOUBLE) donor_cover_post, CAST(r.receiver_cover_post_move AS DOUBLE) receiver_cover_post,
  CAST(r.inventory_cost_aed AS DOUBLE) inventory_cost_aed,
  CAST(r.expected_sales_uplift AS DOUBLE) uplift, CAST(r.net_benefit AS DOUBLE) net_benefit, r.movement_note, 'move' action
FROM {REC} r LEFT JOIN {BR} b ON r.article=b.art
UNION ALL
SELECT h.country, h.business_category, h.gender, b.brand, h.division, CAST(NULL AS STRING),
  h.parent_article, h.article, h.article_name, h.size, h.article_season,
  h.donor_store, h.donor_store_name, h.available_sizes, CAST(NULL AS STRING),
  CAST(h.ros AS DOUBLE), CAST(h.parent_ros AS DOUBLE), CAST(h.cover_days AS DOUBLE), CAST(h.effective_stock AS INT),
  CAST(h.intransit_qty AS INT), h.apo_key,
  h.held_reason, h.would_be_receiver, h.would_be_receiver_name, CAST(NULL AS STRING), CAST(NULL AS STRING),
  CAST(h.would_be_receiver_ros AS DOUBLE), CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE), CAST(NULL AS INT),
  CAST(NULL AS INT), CAST(NULL AS STRING),
  CAST(h.indicative_qty AS INT), CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE),
  CAST(h.inventory_cost_aed AS DOUBLE), CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE), CAST(NULL AS STRING), 'held'
FROM {HELD} h LEFT JOIN {BR} b ON h.article=b.art
UNION ALL
SELECT n.country, n.business_category, n.gender, b.brand, n.division, CAST(NULL AS STRING),
  n.parent_article, n.article, n.article_name, n.size, n.article_season,
  n.store_id, n.store_name, n.available_sizes, CAST(NULL AS STRING),
  CAST(n.ros AS DOUBLE), CAST(n.parent_ros AS DOUBLE), CAST(n.cover_days AS DOUBLE), CAST(n.effective_stock AS INT),
  CAST(n.intransit_qty AS INT), n.apo_key,
  n.not_moved_reason, CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING),
  CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE), CAST(NULL AS INT),
  CAST(NULL AS INT), CAST(NULL AS STRING),
  CAST(NULL AS INT), CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE),
  CAST(n.inventory_cost_aed AS DOUBLE), CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE), CAST(NULL AS STRING), 'dont move'
FROM {NM} n LEFT JOIN {BR} b ON n.article=b.art
"""


def load_combined_export(country=None, action=None, category=None, brand=None, search=None, limit=None, run_id=None) -> pd.DataFrame:
    """recommendations + held + not_moved unioned into ONE table with the SAME detail as the three tabs
    (from/to store, ROS, cover, ATP, in-transit, qty, reason, inv cost), a `brand` col (article-master
    RF_BNDID) after gender, and a FINAL `action` col ('move'|'held'|'no move'). Filters mirror the tabs;
    pass `limit` for the on-screen table, none for the full one-file export (gzip in the UI)."""
    outer = _combined_export_sql(country, action, category, brand, search, limit, run_id)
    if limit:
        return run_sql(outer)
    # full one-file export (no limit): ~580K rows blows the 25MB INLINE cap → stream via EXTERNAL_LINKS
    return run_sql_full(outer)


def _combined_export_sql(country=None, action=None, category=None, brand=None, search=None, limit=None, run_id=None) -> str:
    """Build the combined (move+held+dont move) export SQL, shared by the DataFrame and streamed-gz paths."""
    def esc(s): return str(s).replace("'", "''")
    body = _COMBINED_SQL.format(REC=_src('recommendations', run_id), HELD=_src('held', run_id),
                                NM=_src('not_moved', run_id), BR=_BRAND_SUB).strip()
    outer = f"SELECT * FROM ({body}) x"
    conds = []
    if country and country != "ALL":  conds.append(f"country = '{esc(country)}'")
    if action and action != "all":    conds.append(f"action = '{esc(action)}'")
    if category and category != "ALL": conds.append(f"category = '{esc(category)}'")
    if brand and brand != "ALL":       conds.append(f"brand = '{esc(brand)}'")
    if search:
        s = esc(search).lower()
        conds.append("(lower(article) LIKE '%{0}%' OR lower(article_name) LIKE '%{0}%' "
                     "OR lower(size) LIKE '%{0}%')".format(s))
    if conds:
        outer += " WHERE " + " AND ".join(conds)
    outer += " ORDER BY CASE action WHEN 'move' THEN 0 WHEN 'held' THEN 1 ELSE 2 END, brand, country"
    if limit:
        outer += f" LIMIT {int(limit)}"
    return outer


def load_combined_export_gz(country=None, action=None, category=None, brand=None, search=None, run_id=None):
    """Streamed gzip-CSV of the FULL combined export (no row limit) — bounded memory + retry, for the
    All-tab 'Prepare full CSV' (~580K rows). Returns (gzip_bytes, row_count)."""
    return run_sql_to_csv_gz(_combined_export_sql(country, action, category, brand, search, limit=None, run_id=run_id))


def load_brands(run_id=None) -> list:
    """Distinct brands (article-master RF_BNDID) present in the current run's output — for the Brand filter."""
    df = run_sql(
        f"SELECT DISTINCT b.brand FROM "
        f"(SELECT article FROM {_src('recommendations', run_id)} UNION SELECT article FROM {_src('held', run_id)} "
        f" UNION SELECT article FROM {_src('not_moved', run_id)}) a "
        f"LEFT JOIN {_BRAND_SUB} b ON a.article = b.art WHERE b.brand IS NOT NULL ORDER BY b.brand")
    return df["brand"].tolist()


# ---- config uploads ----
def _sql_lit(val, is_bool: bool) -> str:
    if pd.isna(val):
        return "NULL"
    if is_bool:
        return "true" if str(val).strip().lower() in ("true", "1", "yes", "y") else "false"
    return "'" + str(val).replace("'", "''") + "'"


def replace_config_table(table: str, df: pd.DataFrame, bool_cols=None) -> int:
    """CREATE OR REPLACE a small cfg mapping table from an uploaded CSV DataFrame.
    String columns by default; names in bool_cols become BOOLEAN."""
    bool_cols = set(bool_cols or [])
    if df.empty:
        raise ValueError(f"Uploaded file for {table} is empty.")
    cols = list(df.columns)
    coldefs = ", ".join(f"`{c}` " + ("BOOLEAN" if c in bool_cols else "STRING") for c in cols)
    values = []
    for _, row in df.iterrows():
        vals = ", ".join(_sql_lit(row[c], c in bool_cols) for c in cols)
        values.append(f"({vals})")
    col_list = ", ".join(f"`{c}`" for c in cols)
    run_sql(f"CREATE OR REPLACE TABLE {FQ}.{table} ({coldefs})")
    # batch inserts to keep statements small
    B = 500
    for i in range(0, len(values), B):
        run_sql(f"INSERT INTO {FQ}.{table} ({col_list}) VALUES " + ", ".join(values[i:i + B]))
    return len(values)


# ---- active store list (cfg_store_grade) — the authoritative store universe + grade source ----
_GRADE_RANK = {"BEACON": 0, "JORDAN": 0, "A+": 1, "A": 2, "B": 3, "C": 4, "NVS": 5}


_STORE_LIST_COLS = ["STORE CODE", "SCOPE (BRAND)", "COUNTRY", "STORE NAME",
                    "ROLE", "GRADE (confirm)", "ACTIVE? (Y/N)"]


def load_store_grades() -> pd.DataFrame:
    """Current active store list in the SAME format the business uploads (download → edit → re-upload,
    a clean round-trip). ACTIVE is pre-filled Y (these are the currently-active stores); mark N to drop one."""
    raw = run_sql(f"SELECT store_code, country, grade, store_name FROM {FQ}.cfg_store_grade "
                  f"ORDER BY grade_rank, store_code")
    return pd.DataFrame({
        "STORE CODE": raw["store_code"],
        "SCOPE (BRAND)": "NIKE",          # single-BU today; per-store concept when multi-BU lands
        "COUNTRY": raw["country"],
        "STORE NAME": raw["store_name"],
        "ROLE": "MONO",
        "GRADE (confirm)": raw["grade"],
        "ACTIVE? (Y/N)": "Y",
    })[_STORE_LIST_COLS]


def replace_store_grades(df: pd.DataFrame) -> int:
    """Replace cfg_store_grade from an uploaded store list. Robust to the business template headers
    (STORE CODE / SCOPE (BRAND) / COUNTRY / STORE NAME / ROLE / GRADE (confirm) / ACTIVE? (Y/N), any case,
    trailing '(confirm)'/'(Y/N)' etc.). **Honours the ACTIVE column** — rows marked N/No/0/False/Inactive are
    dropped, so "remove a store" (either delete the row OR set ACTIVE=N) actually excludes it from the run."""
    norm = {c.strip().lower().replace(" ", "_"): c for c in df.columns}
    def col(*names, required=True):
        for n in names:
            for k, orig in norm.items():
                if k == n or k.startswith(n):      # prefix match: 'grade' ~ 'grade_(confirm)'
                    return orig
        if required:
            raise ValueError(f"Store list must have a column matching {names}; got {list(df.columns)}")
        return None
    code_c, grade_c = col("store_code"), col("grade")
    country_c, name_c = col("country"), col("store_name")
    active_c = col("active", required=False)
    out = pd.DataFrame({
        "store_code": df[code_c].astype(str).str.strip(),
        "country":    df[country_c].astype(str).str.strip(),
        "grade":      df[grade_c].astype(str).str.strip().str.upper(),
        "store_name": df[name_c].astype(str).str.strip(),
    })
    if active_c is not None:                        # honour ACTIVE? (Y/N): drop explicit inactives
        act = df[active_c].astype(str).str.strip().str.upper()
        out = out[~act.isin(["N", "NO", "0", "FALSE", "INACTIVE"])]
    out = out[(out["store_code"] != "") & (out["store_code"].str.lower() != "nan")]
    out = out.drop_duplicates(subset=["store_code"])
    out["grade_rank"] = out["grade"].map(_GRADE_RANK).fillna(6).astype(int)
    out = out[["store_code", "country", "grade", "grade_rank", "store_name"]]
    coldefs = "store_code STRING, country STRING, grade STRING, grade_rank INT, store_name STRING"
    run_sql(f"CREATE OR REPLACE TABLE {FQ}.cfg_store_grade ({coldefs})")
    vals = []
    for _, r in out.iterrows():
        def q(s): return "'" + str(s).replace("'", "''") + "'"
        vals.append(f"({q(r.store_code)}, {q(r.country)}, {q(r.grade)}, {int(r.grade_rank)}, {q(r.store_name)})")
    B = 500
    for i in range(0, len(vals), B):
        run_sql(f"INSERT INTO {FQ}.cfg_store_grade (store_code, country, grade, grade_rank, store_name) "
                f"VALUES " + ", ".join(vals[i:i + B]))
    return len(out)


def store_list_provenance() -> dict:
    """Who last changed cfg_store_grade and when (Delta history) — drives the provenance banner so an
    edit by one user is always visible/attributed to the next."""
    try:
        h = run_sql(f"SELECT userName, timestamp FROM (DESCRIBE HISTORY {FQ}.cfg_store_grade) "
                    f"ORDER BY version DESC LIMIT 1")
        if len(h):
            return {"user": str(h.iloc[0]["userName"]), "ts": str(h.iloc[0]["timestamp"])}
    except Exception:
        pass
    return {"user": "—", "ts": "—"}


def reset_store_grades_to_baseline() -> int:
    """Restore cfg_store_grade from the immutable governed baseline (cfg_store_grade_baseline)."""
    run_sql(f"CREATE OR REPLACE TABLE {FQ}.cfg_store_grade AS SELECT * FROM {FQ}.cfg_store_grade_baseline")
    return int(run_sql(f"SELECT COUNT(*) n FROM {FQ}.cfg_store_grade").iloc[0]["n"])
