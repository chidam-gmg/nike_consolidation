"""Build the Genie agents + skills for the stock movement engine.

  python3 build_agents.py tables     knowledge tables (steps, tables, glossary) + step views
  python3 build_agents.py runs       backfill agent_step_runs from the job's run history (job API, your login)
  python3 build_agents.py functions  the SQL skills (Unity Catalog functions)
  python3 build_agents.py spaces     create / update the three Genie spaces (ids kept in agents.json)
  python3 build_agents.py all        everything, in that order

Runs locally with the gmg-sso CLI profile (SQL through databricks_io in build/app)."""
import json, os, pathlib, subprocess, sys, tempfile, uuid
from datetime import datetime, timezone, timedelta

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "build" / "app"))
import knowledge as K                      # noqa: E402
import databricks_io as dbio               # noqa: E402

FQ = "sports_analytics_test.nike_consolidation"
WAREHOUSE = "1c9706d9594f1f2f"
JOB_ID = 553755400746008
GST = timezone(timedelta(hours=4))
STATE = HERE / "agents.json"


def q(s):
    return "NULL" if s is None else "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def sql(stmt):
    return dbio.run_sql(stmt)


def load(table, comment, cols, rows, batch=200):
    """cols = [(name, type, comment)]; rows = tuples in the same order."""
    head = ",\n  ".join(f"{n} {t} COMMENT {q(c)}" for n, t, c in cols)
    sql(f"CREATE OR REPLACE TABLE {FQ}.{table} (\n  {head}\n) COMMENT {q(comment)}")
    for i in range(0, len(rows), batch):
        vals = ",\n".join("(" + ", ".join("NULL" if v is None else (str(v) if isinstance(v, (int, float)) else q(v))
                                            for v in r) + ")" for r in rows[i:i + batch])
        sql(f"INSERT INTO {FQ}.{table} VALUES\n{vals}")
    print(f"{table}: {len(rows)} rows")


# ---------------- knowledge tables ----------------
def build_knowledge():
    steps = [(s[0], s[1], s[2], s[3], s[4], s[5], s[6], s[7], s[8], s[9], s[10], s[11], K.notebook_url(s[2])) for s in K.STEPS]
    load("agent_pipeline_steps",
         "The 13 steps of the Nike stock movement job (nike_stock_movement_pipeline, job 553755400746008), in run order: "
         "what each does, its business rules, the settings it uses and the tables it reads and writes.",
         [("step_no", "STRING", "Step number as in the notebook name (00-13; 09 does not exist)"),
          ("task_key", "STRING", "Task name in the Databricks job"), ("notebook", "STRING", "Notebook that runs the step"),
          ("lane", "STRING", "Shared prep, Store consolidation, DC replenishment or Joint guard"),
          ("run_order", "INT", "Order in which the steps finish (1 = first)"),
          ("depends_on", "STRING", "Tasks that must finish before this step starts"),
          ("purpose", "STRING", "What the step does, in one sentence"),
          ("business_rules", "STRING", "The rules the step applies"),
          ("parameters", "STRING", "Run settings the step uses"),
          ("reads", "STRING", "Tables the step reads (comma separated)"), ("writes", "STRING", "Tables the step writes"),
          ("typical_seconds", "INT", "Duration in the last scheduled run (seconds)"),
          ("notebook_url", "STRING", "Link to the notebook in Databricks")], steps)

    writers, readers = {}, {}
    for s in K.STEPS:
        for t in [x.strip() for x in s[10].split(",") if x.strip()]:
            writers.setdefault(t, []).append(f"{s[0]} {s[1]}")
        for t in [x.strip() for x in s[9].split(",") if x.strip()]:
            readers.setdefault(t, []).append(f"{s[0]} {s[1]}")
    trows = []
    for t, layer, grain, desc, refresh in K.TABLES:
        full = t if t.count(".") == 2 else f"{FQ}.{t}"
        trows.append((t, full, layer, grain, desc, refresh, ", ".join(writers.get(t, [])) or None,
                      ", ".join(readers.get(t, [])) or None, K.table_url(t)))
    load("agent_pipeline_tables",
         "Every table the stock movement job reads or writes: what it holds, its grain, which step writes it and which steps "
         "read it. Step outputs hold the LATEST run only; *_history tables keep every run by run_id.",
         [("table_name", "STRING", "Table name (short for the project schema, full for source tables)"),
          ("full_name", "STRING", "catalog.schema.table"), ("layer", "STRING", "Source, Config, Step output, Result or History"),
          ("grain", "STRING", "One row per ..."), ("description", "STRING", "What the table holds"),
          ("refresh", "STRING", "How the table changes: Upstream, Replaced every run, Appended every run, Uploaded from the app"),
          ("written_by_step", "STRING", "Step(s) that write it"), ("read_by_steps", "STRING", "Steps that read it"),
          ("url", "STRING", "Link to the table in Catalog Explorer")], trows)

    load("agent_glossary", "Codes and labels the stock movement job writes, with their meaning and the step that sets them.",
         [("field", "STRING", "Column or concept the code appears in"), ("code", "STRING", "The code or label"),
          ("meaning", "STRING", "What it means"), ("step_no", "STRING", "Step that sets it")], K.GLOSSARY)


# ---------------- step views (latest run) ----------------
def cview(name, comment, cols, body):
    head = ",\n  ".join(f"{n} COMMENT {q(c)}" for n, c in cols)
    sql(f"CREATE OR REPLACE VIEW {FQ}.{name} (\n  {head}\n) COMMENT {q(comment)} AS\n{body}")
    print("view", name)


LATEST = "Step tables hold the latest run only (see agent_current_run for its run ID and scope). "


def build_views():
    p = lambda k: f"MAX(CASE WHEN param_name = '{k}' THEN param_value END)"
    cview("agent_current_run", "The run the step tables currently hold: run ID, who ran it, scope, data dates and settings. "
          "A planner's scoped run from the app replaces the scheduled run's step tables until the next run.",
          [("run_id", "Engine run ID"), ("run_time_gst", "When the run started (UAE time)"),
           ("run_by", "Email of the planner, or scheduled for the daily 10:30 run"),
           ("country_scope", "Countries in the run (ALL = every country)"), ("category_scope", "Categories in the run (ALL = every category)"),
           ("is_full_scope", "TRUE when the run covered all countries and categories"),
           ("sales_through", "Last day of sales used"), ("stock_as_of", "Date of the stock snapshot"),
           ("current_season", "Season treated as current"), ("ros_window_days", "Days of sales for rate of sale"),
           ("in_transit_lookback_days", "Days of in-transit counted"), ("slow_moving_threshold_days", "Cover above this = slow moving"),
           ("new_to_store_days", "Received within this = new to store"), ("protect_cover_days", "Cover a donor keeps"),
           ("target_cover_days", "Cover a receiver is filled to"), ("movement_cost_per_unit_aed", "Movement cost per unit (provisional)")],
          f"""SELECT {p('RUN_ID')}, from_utc_timestamp({p('RUN_TS')}, 'Asia/Dubai'),
  CASE WHEN {p('TRIGGERED_BY')} IN ('app', 'manual', '') THEN 'scheduled' ELSE {p('TRIGGERED_BY')} END,
  {p('COUNTRY')}, {p('CATEGORY')}, {p('COUNTRY')} = 'ALL' AND {p('CATEGORY')} = 'ALL',
  CAST({p('AS_OF_DATE')} AS DATE), CAST({p('ATP_AS_OF')} AS DATE), {p('CURRENT_SEASON_LABEL')},
  CAST({p('ROS_WINDOW_DAYS')} AS INT), CAST({p('IN_TRANSIT_LOOKBACK_DAYS')} AS INT), CAST({p('SLOW_MOVING_THRESHOLD_DAYS')} AS INT),
  CAST({p('NEW_TO_STORE_DAYS')} AS INT), CAST({p('PROTECT_COVER_DAYS')} AS INT), CAST({p('TARGET_COVER_DAYS')} AS INT),
  CAST({p('MOVEMENT_COST_PER_UNIT')} AS DOUBLE)
FROM {FQ}.run_config""")

    base = [("country", "Country ISO code (AE = UAE, SA = KSA, KW, BH, OM, QA)"), ("store_id", "Store code"),
            ("store_name", "Store name"), ("store_grade", "Store grade (BEACON, JORDAN, A+, A, B, C, NVS)"),
            ("style_code", "Style (generic article) code, e.g. NKIF1911-010"), ("article_code", "Size-level article code"),
            ("size", "Size")]
    bsel = "country, store_id, store_name, store_grade, generic_article, article_code, product_size"
    cview("agent_step02_stock", LATEST + "Steps 01-02: every store x size in scope with sellable stock, in-transit, rate of sale, "
          "cover and the DEAD / SLOW_MOVING / HEALTHY / NEW_TO_STORE label.",
          base + [("atp_units", "Sellable stock (ATP)"), ("in_transit_units", "Units in transit to the store"),
                  ("effective_stock", "ATP + in-transit"), ("units_sold", "Units sold in the rate-of-sale window"),
                  ("rate_of_sale_per_day", "Units sold per day"), ("cover_days", "Days of stock at the current rate (NULL = no sales)"),
                  ("classification", "DEAD, SLOW_MOVING, HEALTHY or NEW_TO_STORE"), ("first_received", "First goods receipt at the store"),
                  ("unit_cost_aed", "Cost per unit (AED)"), ("sales_through", "Last day of sales used")],
          f"SELECT {bsel}, atp_qty, intransit_qty, effective_stock, units_sold, ros, cover_days, classification, first_grn_date, "
          f"unit_cost_aed, as_of_date FROM {FQ}.stock_classified")

    cview("agent_step03_pools", LATEST + "Step 03: stores that can give (Donor) and take (Receiver) per size, with how much.",
          base + [("role", "Donor or Receiver"), ("units", "Donor: surplus it can give. Receiver: units it can take"),
                  ("demand_type", "Receiver only: IN_STOCK, OOS_SELLING or APLUS_CURVE"), ("classification", "Label from step 02"),
                  ("rate_of_sale_per_day", "Units sold per day"), ("cover_days", "Days of cover"),
                  ("effective_stock", "ATP + in-transit")],
          f"""SELECT {bsel}, 'Donor', donor_can_give, CAST(NULL AS STRING), classification, ros, cover_days, effective_stock FROM {FQ}.donor_pool
UNION ALL
SELECT {bsel}, 'Receiver', receiver_can_take, demand_type, classification, ros, cover_days, effective_stock FROM {FQ}.receiver_pool""")

    cview("agent_step03_guards", LATEST + "Step 03: guards that stop a store receiving: APO inbound, season freshness, move-back "
          "within 30 days, and A+ stores missing a display size (which makes them receivers).",
          [("country", "Country ISO code (blank for move-back)"), ("store_id", "Store code"), ("style_code", "Style code"),
           ("article_code", "Size-level article (blank when the guard is for the whole style)"),
           ("guard", "APO, Season-freshness, Move-back <30d or A+ missing size"), ("detail", "Why the guard applies")],
          f"""SELECT country, store_id, generic_article, CAST(NULL AS STRING), 'APO', 'Inbound APO for this style' FROM {FQ}.apo_block
UNION ALL SELECT country, store_id, generic_article, article_code, 'Season-freshness',
  concat('Article season ', season_label, '; store may stock back to ', allow_seasons_back, ' seasons') FROM {FQ}.season_block
UNION ALL SELECT CAST(NULL AS STRING), store_id, generic_article, article_code, 'Move-back <30d',
  concat('Moved out on ', CAST(last_moved_out AS STRING)) FROM {FQ}.moveback_block
UNION ALL SELECT country, store_id, generic_article, article_code, 'A+ missing size', 'A+ store missing this display size'
FROM {FQ}.aplus_missing_sizes""")

    cview("agent_step05_matches", LATEST + "Steps 04, 05 and 08 for store-to-store moves: every matched donor -> receiver move, "
          "the donor size-run decision, and whether the joint receiver guard kept or held it.",
          [("country", "Country ISO code"), ("style_code", "Style code"), ("article_code", "Size-level article"), ("size", "Size"),
           ("donor_store", "Giving store"), ("donor_store_name", "Giving store name"), ("donor_class", "DEAD or SLOW_MOVING"),
           ("receiver_store", "Receiving store"), ("receiver_store_name", "Receiving store name"),
           ("demand_type", "Receiver demand: IN_STOCK, OOS_SELLING, APLUS_CURVE"), ("qty", "Units matched"),
           ("size_run_decision", "Step 05: FULL_RETAINED, DONOR_ALREADY_CUT, RESHAPED_FULL_RUN or NO_SIZE_DEF"),
           ("movement_note", "Plain-language note on why it moves"),
           ("final_status", "Step 08: Recommended, or Held - receiver stays cut-size")],
          f"""SELECT m.country, m.generic_article, m.article_code, m.product_size, m.donor_store, m.donor_store_name, m.donor_class,
  m.receiver_store, m.receiver_store_name, m.demand_type, m.recommended_qty, m.reshape_action, m.movement_note,
  CASE WHEN f.article_code IS NOT NULL THEN 'Recommended' WHEN h.article_code IS NOT NULL THEN 'Held - receiver stays cut-size'
       ELSE 'Dropped' END
FROM {FQ}.match_reshaped_all m
LEFT JOIN (SELECT DISTINCT donor_store, receiver_store, article_code FROM {FQ}.match_reshaped) f
  ON f.donor_store = m.donor_store AND f.receiver_store = m.receiver_store AND f.article_code = m.article_code
LEFT JOIN (SELECT DISTINCT donor_store, receiver_store, article_code FROM {FQ}.held_receiver_curve) h
  ON h.donor_store = m.donor_store AND h.receiver_store = m.receiver_store AND h.article_code = m.article_code""")

    cview("agent_step08_receiver_guard", LATEST + "Step 08: the joint guard per receiving store x style after all store moves and "
          "DC lines: key sizes before and after, the threshold, the decision and the final full / cut status.",
          [("country", "Country ISO code"), ("store_id", "Receiving store"), ("store_grade", "Store grade"), ("style_code", "Style code"),
           ("threshold", "Key sizes needed above this to be full-size"), ("key_sizes_before", "Key sizes in stock before the moves"),
           ("key_sizes_after", "Key sizes in stock after all proposed moves"), ("store_move_units_in", "Units arriving from stores"),
           ("dc_units_in", "Units arriving from the DC"), ("topup_lines", "DC top-up lines to make it full"),
           ("decision", "HOLD_RECV_CUT, ALLOW_RECV_ALREADY_FULL, ALLOW_RECV_BECOMES_FULL, ALLOW_NO_SIZE_DEF or TOPUP_TO_FULL"),
           ("size_status_after", "Full size or Cut size after everything that was kept")],
          f"""SELECT g.country, g.store_id, g.store_grade, g.generic_article, g.threshold, g.key_before, g.key_after, g.consol_in_units,
  g.replen_in_units, g.topup_lines, g.recv_action, s.size_status
FROM {FQ}.receiver_guard_decision g
LEFT JOIN {FQ}.size_status_post_move s ON s.store_id = g.store_id AND s.generic_article = g.generic_article""")

    cview("agent_step11_replen", LATEST + "Steps 10, 11 and 08 for DC replenishment: per Nike store x size the demand, target, need, "
          "units rationed from the primary DC and the UAE hub, and the final quantity after the joint guard.",
          base + [("division", "FOOTWEAR / APPAREL / ACCESSORIES"), ("effective_stock", "ATP + in-transit"),
                  ("pos_units", "Units sold (POS) in the window"), ("demand_per_day", "Demand rate per day"),
                  ("display_floor", "Units needed to display the size"), ("target_units", "Target stock (s)"),
                  ("need_units", "Target - stock"), ("primary_pool", "Primary DC pool for the country"),
                  ("primary_dc_atp", "Primary pool stock"), ("hub_atp", "UAE hub stock"),
                  ("alloc_primary", "Units rationed from the primary pool"), ("alloc_hub", "Units rationed from the hub"),
                  ("rationed_qty", "Total rationed (step 11)"), ("guard_action", "Step 08 action on this line"),
                  ("final_qty", "Units after the joint guard"), ("guard_decision", "Review flag for full-size top-ups"),
                  ("value_at_risk_aed", "Yearly sales at risk if not filled (AED)")],
          f"SELECT {bsel}, division, effective_stock, pos_units, demand_rate_day, display_floor, s_units, need_units, primary_pool, "
          f"primary_atp, hub_atp, alloc_primary, alloc_hub, rationed_qty, guard_action, final_qty, guard_decision, value_at_risk_aed "
          f"FROM {FQ}.replen_final")


# ---------------- step run history (job API) ----------------
def build_runs(limit=60):
    from itertools import islice
    w = dbio.get_client()
    hist = sql(f"SELECT run_id, run_ts FROM {FQ}.run_stats_history")
    hist["t"] = hist["run_ts"].map(lambda x: datetime.fromisoformat(str(x).replace("Z", "+00:00")))
    step_of = {s[1]: (s[0], s[2]) for s in K.STEPS}
    rows = []
    for r in islice(w.jobs.list_runs(job_id=JOB_ID, expand_tasks=True), limit):
        params = {p.name: p.value for p in (r.job_parameters or [])}
        start = datetime.fromtimestamp(r.start_time / 1000, timezone.utc)
        eng = params.get("RUN_ID") or None
        if not eng:                                        # scheduled runs mint the run ID inside the notebook
            m = hist[(hist["t"] >= start) & (hist["t"] <= start + timedelta(minutes=10))]
            eng = m.iloc[0]["run_id"] if len(m) else None
        trig = params.get("TRIGGERED_BY") or "app"
        res = r.state.result_state.value if r.state and r.state.result_state else (
            r.state.life_cycle_state.value if r.state and r.state.life_cycle_state else None)
        run_sec = int((r.end_time - r.start_time) / 1000) if r.end_time else None
        for t in r.tasks or []:
            sn, nb = step_of.get(t.task_key, ("", t.task_key))
            ts = datetime.fromtimestamp(t.start_time / 1000, timezone.utc) if t.start_time else None
            te = datetime.fromtimestamp(t.end_time / 1000, timezone.utc) if t.end_time else None
            rows.append((str(r.run_id), eng, ts and start.astimezone(GST).strftime("%Y-%m-%d %H:%M:%S"),
                         "scheduled" if trig in ("app", "manual", "") else trig, params.get("COUNTRY") or "ALL",
                         params.get("CATEGORY") or "ALL", res, run_sec, sn, t.task_key, nb,
                         ts and ts.astimezone(GST).strftime("%Y-%m-%d %H:%M:%S"), te and te.astimezone(GST).strftime("%Y-%m-%d %H:%M:%S"),
                         int((t.end_time - t.start_time) / 1000) if t.start_time and t.end_time else None,
                         t.state.result_state.value if t.state and t.state.result_state else None, r.run_page_url))
    load("agent_step_runs",
         "Every step of every recent run of the stock movement job: when it ran, how long it took and whether it succeeded. "
         "Includes failed runs. Snapshot from the job API (refresh with build_agents.py runs).",
         [("job_run_id", "STRING", "Databricks job run ID"), ("engine_run_id", "STRING", "Engine run ID (matches run_stats_history.run_id)"),
          ("run_start_gst", "STRING", "When the run started (UAE time, yyyy-MM-dd HH:mm:ss)"),
          ("run_by", "STRING", "scheduled (daily 10:30) or the planner's email"), ("country_scope", "STRING", "Countries in the run"),
          ("category_scope", "STRING", "Categories in the run"), ("run_result", "STRING", "SUCCESS, FAILED, CANCELED, or RUNNING"),
          ("run_seconds", "INT", "Whole run duration (seconds)"), ("step_no", "STRING", "Step number (00-13)"),
          ("task_key", "STRING", "Task name"), ("notebook", "STRING", "Notebook"),
          ("step_start_gst", "STRING", "Step start (UAE time)"), ("step_end_gst", "STRING", "Step end (UAE time)"),
          ("step_seconds", "INT", "Step duration (seconds)"), ("step_result", "STRING", "SUCCESS, FAILED, SKIPPED or blank if not run"),
          ("run_url", "STRING", "Link to the run in Databricks")], rows)


# ---------------- skills (Unity Catalog SQL functions) ----------------
def build_functions():
    sql(f"""CREATE OR REPLACE FUNCTION {FQ}.agent_step_info(step STRING COMMENT 'Step number (e.g. 8 or 08), task name (receiver_guard), or a word from what it does (rationing); all = every step')
RETURNS TABLE (step_no STRING, task_key STRING, lane STRING, purpose STRING, business_rules STRING, parameters STRING,
               reads STRING, writes STRING, depends_on STRING, typical_seconds INT, notebook_url STRING)
COMMENT 'Explains a step of the stock movement job: what it does, its rules, the settings it uses, what it reads and writes.'
RETURN SELECT step_no, task_key, lane, purpose, business_rules, parameters, reads, writes, depends_on, typical_seconds, notebook_url
FROM {FQ}.agent_pipeline_steps
WHERE lower(trim(step)) IN ('all', '', '*')
   OR (regexp_extract(step, '([0-9]+)', 1) <> '' AND step_no = lpad(regexp_extract(step, '([0-9]+)', 1), 2, '0'))
   OR lower(task_key) = lower(trim(step)) OR lower(notebook) LIKE concat('%', lower(trim(step)), '%')
   OR (length(trim(step)) >= 3 AND regexp_extract(step, '([0-9]+)', 1) = ''
       AND lower(replace(concat_ws(' ', task_key, notebook, purpose, business_rules), '_', ' '))
           LIKE concat('%', left(lower(trim(step)), greatest(4, length(trim(step)) - 3)), '%'))""")

    sql(f"""CREATE OR REPLACE FUNCTION {FQ}.agent_table_lineage(table_ref STRING COMMENT 'Table name or part of it, e.g. held or replen_final')
RETURNS TABLE (table_name STRING, layer STRING, grain STRING, description STRING, refresh STRING,
               written_by_step STRING, read_by_steps STRING, url STRING)
COMMENT 'Where a table comes from and where it goes: the step that writes it, the steps that read it, its grain and refresh.'
RETURN SELECT table_name, layer, grain, description, refresh, written_by_step, read_by_steps, url
FROM {FQ}.agent_pipeline_tables WHERE lower(table_name) LIKE concat('%', lower(trim(table_ref)), '%')""")

    sql(f"""CREATE OR REPLACE FUNCTION {FQ}.agent_step_timings(step STRING COMMENT 'Step number, task name, or all', last_n INT COMMENT 'How many most recent runs')
RETURNS TABLE (run_start_gst STRING, run_by STRING, country_scope STRING, run_result STRING, step_no STRING, task_key STRING,
               step_seconds INT, step_result STRING, run_seconds INT, run_url STRING)
COMMENT 'How long each step took in recent runs and whether it succeeded, newest first.'
RETURN WITH r AS (SELECT job_run_id, DENSE_RANK() OVER (ORDER BY run_start_gst DESC) AS rk FROM {FQ}.agent_step_runs GROUP BY job_run_id, run_start_gst)
SELECT s.run_start_gst, s.run_by, s.country_scope, s.run_result, s.step_no, s.task_key, s.step_seconds, s.step_result, s.run_seconds, s.run_url
FROM {FQ}.agent_step_runs s JOIN r ON r.job_run_id = s.job_run_id
WHERE r.rk <= last_n AND (lower(trim(step)) IN ('all', '', '*')
      OR (regexp_extract(step, '([0-9]+)', 1) <> '' AND s.step_no = lpad(regexp_extract(step, '([0-9]+)', 1), 2, '0'))
      OR lower(s.task_key) = lower(trim(step)))
ORDER BY s.run_start_gst DESC, s.step_start_gst""")

    sql(f"""CREATE OR REPLACE FUNCTION {FQ}.agent_explain_code(code_ref STRING COMMENT 'A code or label the job writes, e.g. HOLD_RECV_CUT, OOS_SELLING, NO_DC_STOCK')
RETURNS TABLE (field STRING, code STRING, meaning STRING, step_no STRING)
COMMENT 'Meaning of a code or label written by the stock movement job, and the step that sets it.'
RETURN SELECT field, code, meaning, step_no FROM {FQ}.agent_glossary
WHERE lower(code) LIKE concat('%', lower(trim(code_ref)), '%') OR lower(field) = lower(trim(code_ref))""")

    T = FQ
    sql(f"""CREATE OR REPLACE FUNCTION {FQ}.agent_trace_line(
  store_ref STRING COMMENT 'Store code (e.g. 1122) or words from its name in any order (e.g. Dubai Mall Beacon)',
  article_ref STRING COMMENT 'Size-level article code (e.g. NKIF1911-01002) or style code (e.g. NKIF1911-010)',
  size_ref STRING DEFAULT NULL COMMENT 'Size, when article_ref is a style code (e.g. M or 10.5); NULL = every size')
RETURNS TABLE (store_id STRING, article_code STRING, size STRING, seq INT, step_no STRING, step STRING, outcome STRING, detail STRING)
COMMENT 'Traces one store x article through every step of the latest run: stock, classification, donor/receiver pools, guards, matching, size-run check, joint receiver guard, DC demand and rationing, and the final result. Answers why a line moved, was held, was replenished or not.'
RETURN
WITH st AS (SELECT DISTINCT store_id FROM {T}.agent_step02_stock
            WHERE store_id = trim(store_ref) OR forall(split(lower(trim(store_ref)), ' +'), w -> lower(store_name) LIKE concat('%', w, '%'))
            UNION SELECT DISTINCT store_id FROM {T}.replen_final WHERE store_id = trim(store_ref) OR forall(split(lower(trim(store_ref)), ' +'), w -> lower(store_name) LIKE concat('%', w, '%'))),
sz AS (SELECT CASE WHEN size_ref IS NULL OR upper(trim(size_ref)) IN ('', 'NULL', 'NONE', 'ALL', '*') THEN NULL ELSE upper(trim(size_ref)) END AS s),
ar AS (SELECT DISTINCT article_code, generic_article, product_size FROM (
         SELECT article_code, generic_article, product_size FROM {T}.effective_stock
         UNION ALL SELECT article_code, generic_article, product_size FROM {T}.replen_demand)
       WHERE article_code = upper(trim(article_ref))
          OR (generic_article = upper(trim(article_ref)) AND ((SELECT s FROM sz) IS NULL OR upper(product_size) = (SELECT s FROM sz)))),
k AS (SELECT st.store_id, ar.article_code, ar.generic_article, ar.product_size FROM st CROSS JOIN ar),
run AS (SELECT * FROM {T}.agent_current_run),
mv AS (SELECT article_code, store, concat_ws('; ', collect_list(line)) AS lines FROM (
         SELECT article_code, donor_store AS store, concat('gives ', qty, ' to ', receiver_store, ' ', receiver_store_name, ' [', size_run_decision, '; ', final_status, ']') AS line FROM {T}.agent_step05_matches
         UNION ALL SELECT article_code, receiver_store, concat('receives ', qty, ' from ', donor_store, ' ', donor_store_name, ' [', size_run_decision, '; ', final_status, ']') FROM {T}.agent_step05_matches)
       GROUP BY article_code, store),
hm AS (SELECT article_code, donor_store AS store, concat_ws('; ', collect_list(concat(held_reason, ': ', held_qty, ' units would go to ', receiver_store))) AS lines
       FROM {T}.held_moves GROUP BY article_code, donor_store),
gd AS (SELECT store_id, style_code AS generic_article, concat_ws('; ', collect_set(concat(guard, ' - ', detail))) AS g
       FROM {T}.agent_step03_guards WHERE article_code IS NULL GROUP BY store_id, style_code),
gda AS (SELECT store_id, article_code, concat_ws('; ', collect_set(concat(guard, ' - ', detail))) AS g FROM {T}.agent_step03_guards
        WHERE article_code IS NOT NULL GROUP BY store_id, article_code),
rec AS (SELECT article, store, concat_ws('; ', collect_list(line)) AS lines FROM (
          SELECT article, donor_store AS store, concat('MOVE OUT ', recommended_qty, ' to ', receiver_store, ' - ', reason) AS line FROM {T}.recommendations
          UNION ALL SELECT article, receiver_store, concat('MOVE IN ', recommended_qty, ' from ', donor_store, ' - ', reason) FROM {T}.recommendations)
        GROUP BY article, store)
SELECT k.store_id, k.article_code, k.product_size, 1, '00', 'Run', concat('Run ', run.run_id, ' by ', run.run_by),
       concat('scope ', run.country_scope, ' / ', run.category_scope, ' - sales through ', run.sales_through, ', stock as of ', run.stock_as_of)
FROM k CROSS JOIN run
UNION ALL
SELECT k.store_id, k.article_code, k.product_size, 2, '01-02', 'Stock and classification',
       COALESCE(s.classification, 'No stock of this size'),
       CASE WHEN s.article_code IS NULL THEN 'Nothing in stock or in transit at this store for this size'
            ELSE concat('ATP ', CAST(s.atp_units AS BIGINT), ', in transit ', CAST(s.in_transit_units AS BIGINT), ', effective ', CAST(s.effective_stock AS BIGINT), '; sold ',
                        COALESCE(s.units_sold, 0), ' in window, ', round(COALESCE(s.rate_of_sale_per_day, 0), 2), '/day, cover ',
                        COALESCE(CAST(round(s.cover_days, 1) AS STRING), 'n/a (no sales)'), ' days') END
FROM k LEFT JOIN {T}.agent_step02_stock s ON s.store_id = k.store_id AND s.article_code = k.article_code
UNION ALL
SELECT k.store_id, k.article_code, k.product_size, 3, '03', 'Donor / receiver pools',
       CASE WHEN COUNT(p.role) = 0 THEN 'Neither donor nor receiver' ELSE concat_ws(' + ', collect_list(p.role)) END,
       CASE WHEN COUNT(p.role) = 0 THEN 'Not dead/slow with surplus, and not short of cover'
            ELSE concat_ws('; ', collect_list(concat(p.role, ': ', CAST(p.units AS BIGINT), ' units', CASE WHEN p.demand_type IS NOT NULL THEN concat(' (', p.demand_type, ')') ELSE '' END))) END
FROM k LEFT JOIN {T}.agent_step03_pools p ON p.store_id = k.store_id AND p.article_code = k.article_code
GROUP BY k.store_id, k.article_code, k.product_size
UNION ALL
SELECT k.store_id, k.article_code, k.product_size, 4, '03', 'Guards',
       CASE WHEN g.g IS NULL AND ga.g IS NULL THEN 'No guard applies' ELSE 'Guard applies' END, concat_ws('; ', g.g, ga.g)
FROM k LEFT JOIN gd g ON g.store_id = k.store_id AND g.generic_article = k.generic_article
       LEFT JOIN gda ga ON ga.store_id = k.store_id AND ga.article_code = k.article_code
UNION ALL
SELECT k.store_id, k.article_code, k.product_size, 5, '04-05', 'Store-to-store matching and size-run check',
       CASE WHEN m.lines IS NULL AND h.lines IS NULL THEN 'Not matched' WHEN m.lines IS NULL THEN 'Held' ELSE 'Matched' END,
       concat_ws('; ', m.lines, h.lines)
FROM k LEFT JOIN mv m ON m.article_code = k.article_code AND m.store = k.store_id
       LEFT JOIN hm h ON h.article_code = k.article_code AND h.store = k.store_id
UNION ALL
SELECT k.store_id, k.article_code, k.product_size, 7, '08', 'Joint receiver guard (store x style)',
       COALESCE(r.decision, 'Not a receiver of this style'),
       CASE WHEN r.decision IS NULL THEN NULL ELSE concat('key sizes before ', r.key_sizes_before, ', after ', r.key_sizes_after, ', threshold ',
            r.threshold, '; from stores ', CAST(r.store_move_units_in AS BIGINT), ', from DC ', CAST(r.dc_units_in AS BIGINT), COALESCE(concat('; ', r.size_status_after), ''),
            CASE WHEN t.topup_qty IS NOT NULL THEN concat('; DC top-up ', t.topup_qty, ' of this size') ELSE '' END) END
FROM k LEFT JOIN {T}.agent_step08_receiver_guard r ON r.store_id = k.store_id AND r.style_code = k.generic_article
       LEFT JOIN {T}.fullsize_topup t ON t.store_id = k.store_id AND t.article_code = k.article_code
UNION ALL
SELECT k.store_id, k.article_code, k.product_size, 6, '10-11', 'DC demand and rationing',
       CASE WHEN d.article_code IS NULL THEN 'No DC need' WHEN a.article_code IS NULL THEN concat('Need ', CAST(d.need_units AS BIGINT), ', nothing rationed')
            ELSE concat('Need ', CAST(a.need_units AS BIGINT), ', rationed ', a.rationed_qty) END,
       CASE WHEN d.article_code IS NULL THEN 'Store has enough stock for 14 days of demand and display, or is not a Nike store in scope'
            ELSE concat('demand ', round(d.demand_rate_day, 2), '/day, target ', CAST(d.s_units AS BIGINT), ', display floor ', CAST(d.display_floor AS BIGINT),
                        CASE WHEN a.article_code IS NOT NULL THEN concat('; primary ', a.primary_pool, ' ', a.alloc_primary, ', hub ', a.alloc_hub,
                             '; after guard ', COALESCE(a.final_qty, 0), COALESCE(concat(' (', a.guard_action, ')'), '')) ELSE '' END) END
FROM k LEFT JOIN {T}.replen_demand d ON d.store_id = k.store_id AND d.article_code = k.article_code
       LEFT JOIN {T}.replen_final a ON a.store_id = k.store_id AND a.article_code = k.article_code
UNION ALL
SELECT k.store_id, k.article_code, k.product_size, 8, '06', 'Result: store to store',
       CASE WHEN rc.lines IS NOT NULL THEN 'Recommended move' WHEN hd.held_reason IS NOT NULL THEN concat('Held: ', hd.held_reason)
            WHEN nm.not_moved_reason IS NOT NULL THEN nm.not_moved_reason ELSE 'No line' END,
       COALESCE(rc.lines, CASE WHEN hd.held_reason IS NOT NULL THEN concat(hd.indicative_qty, ' units would go to ', hd.would_be_receiver, ' ', COALESCE(hd.would_be_receiver_name, '')) END)
FROM k LEFT JOIN rec rc ON rc.article = k.article_code AND rc.store = k.store_id
       LEFT JOIN {T}.held hd ON hd.article = k.article_code AND hd.donor_store = k.store_id
       LEFT JOIN {T}.not_moved nm ON nm.article = k.article_code AND nm.store_id = k.store_id
UNION ALL
SELECT k.store_id, k.article_code, k.product_size, 9, '12', 'Result: DC to store',
       CASE WHEN rr.article IS NOT NULL THEN concat('Replenish ', rr.recommended_qty) WHEN nr.article IS NOT NULL THEN concat('Not filled: ', nr.not_replenished_reason)
            ELSE 'No DC line' END,
       CASE WHEN rr.article IS NOT NULL THEN concat(rr.reason, ' from ', rr.source, '; store stock ', rr.store_atp, ' -> ', rr.store_atp_after)
            WHEN nr.article IS NOT NULL THEN concat('needed ', nr.store_need, ', rationed ', nr.rationed_qty) END
FROM k LEFT JOIN {T}.replen_recommendations rr ON rr.article = k.article_code AND rr.receiver_store = k.store_id
       LEFT JOIN {T}.replen_not_replenished nr ON nr.article = k.article_code AND nr.receiver_store = k.store_id""")
    print("functions: agent_step_info, agent_table_lineage, agent_step_timings, agent_explain_code, agent_trace_line")


# ---------------- Genie spaces ----------------
def api(method, path, body=None):
    env = dict(os.environ); env["PATH"] = os.path.expanduser("~/.local/bin") + ":" + env.get("PATH", "")
    cmd = ["databricks", "api", method, path, "--profile", "gmg-sso"]
    if body is not None:
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); json.dump(body, f); f.close()
        cmd += ["--json", "@" + f.name]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    try:
        return json.loads(r.stdout)
    except Exception:
        raise RuntimeError((r.stdout or r.stderr)[:800])


def hid():
    return uuid.uuid4().hex


def space_payload(spec):
    space = {
        "version": 2,
        "config": {"sample_questions": sorted([{"id": hid(), "question": [x]} for x in spec["samples"]], key=lambda x: x["id"])},
        "data_sources": {"tables": sorted([{"identifier": t if t.count(".") == 2 else f"{FQ}.{t}"} for t in spec["tables"]],
                                          key=lambda x: x["identifier"])},
        "instructions": {
            "text_instructions": [{"id": hid(), "content": [spec["instructions"]]}],
            "example_question_sqls": sorted([{"id": hid(), "question": [a], "sql": [b]} for a, b in spec["examples"]],
                                            key=lambda x: x["id"]),
            "sql_functions": sorted([{"id": hid(), "identifier": f"{FQ}.{f}"} for f in spec["functions"]], key=lambda x: x["id"]),
        },
    }
    return {"warehouse_id": WAREHOUSE, "title": spec["title"], "description": spec["description"], "serialized_space": json.dumps(space)}


def build_spaces():
    import spaces as SP
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    for key, spec in SP.SPACES.items():
        body = space_payload(spec)
        if state.get(key):
            out = api("patch", f"/api/2.0/genie/spaces/{state[key]}", body)
        else:
            out = api("post", "/api/2.0/genie/spaces", body)
            state[key] = out["space_id"]
        print(key, state[key], out.get("title"))
    STATE.write_text(json.dumps(state, indent=1))


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("tables", "all"):
        build_knowledge(); build_views()
    if what in ("runs", "all"):
        build_runs()
    if what in ("functions", "all"):
        build_functions()
    if what in ("spaces", "all"):
        build_spaces()
