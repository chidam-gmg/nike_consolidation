"""Build the curated merchandising views the 'Nike Stock Movement' Genie space reads (and nothing else).

Three views (runs, store stock with a Move/Held/Keep status, DC replenishment with a Replenish/Not filled status), plain column names, a comment on every column, and run flags so
'latest' questions resolve without SQL knowledge. Test runs (triggered_by claude-*) are excluded.
Run:  python3 build_merch_views.py  (prints SQL; pass --apply to execute through the SQL helper given as argv[2])
"""
import subprocess, sys

FQ = "sports_analytics_test.nike_consolidation"
SM = "prod_mena.gold.mena_sports_store_master_view"
PM = "prod_mena.gold.mena_sports_product_master_view"
RUN_FLAGS = [("m.run_date", "run_date", "Date the run used for sales (data through this date)"),
             ("m.is_latest_full_run", "is_latest_full_run", "TRUE for the most recent run covering all countries and all categories — use this by default"),
             ("m.is_latest_run", "is_latest_run", "TRUE for the most recent run of any scope")]


def view(name, comment, cols, from_sql):
    head = ",\n  ".join(f"{n} COMMENT '{c}'" for _, n, c in cols)
    sel = ",\n  ".join(e for e, _, _ in cols)
    return f"CREATE OR REPLACE VIEW {FQ}.{name} (\n  {head}\n) COMMENT '{comment}' AS\nSELECT\n  {sel}\n{from_sql}"


V = []
V.append(view("merch_runs",
  "One row per engine run: scope, who ran it, headline results. Use is_latest_full_run = TRUE for the latest daily full run.",
  [("s.run_id", "run_id", "Engine run identifier"),
   ("from_utc_timestamp(s.run_ts, 'Asia/Dubai')", "run_time_gst", "When the run started (UAE time)"),
   ("s.as_of_date", "run_date", "Date the run used for sales (data through this date)"),
   ("CASE WHEN s.triggered_by IN ('app', 'manual') OR s.triggered_by IS NULL THEN 'scheduled' ELSE s.triggered_by END", "run_by", "Who ran it (email) or scheduled for the daily 10:30 run"),
   ("s.country_scope", "country_scope", "Countries in the run (ALL = every country on the store list)"),
   ("COALESCE(s.category_scope, 'ALL')", "category_scope", "Categories in the run (ALL = every category)"),
   ("s.country_scope = 'ALL' AND COALESCE(s.category_scope, 'ALL') = 'ALL'", "is_full_scope", "TRUE when the run covered all countries and categories"),
   ("s.run_id = MAX_BY(s.run_id, s.run_ts) OVER (PARTITION BY (s.country_scope = 'ALL' AND COALESCE(s.category_scope, 'ALL') = 'ALL'))"
    " AND s.country_scope = 'ALL' AND COALESCE(s.category_scope, 'ALL') = 'ALL'", "is_latest_full_run", "TRUE for the most recent full-scope run — use this by default"),
   ("s.run_id = MAX_BY(s.run_id, s.run_ts) OVER ()", "is_latest_run", "TRUE for the most recent run of any scope"),
   ("CAST(s.recommended_moves AS BIGINT)", "store_moves", "Store-to-store transfer lines recommended"),
   ("CAST(s.total_units AS BIGINT)", "store_move_units", "Units in store-to-store transfers"),
   ("ROUND(s.total_expected_uplift, 0)", "store_move_uplift_aed", "Estimated sales uplift of the store moves (AED, provisional)"),
   ("CAST(s.held_lines AS BIGINT)", "held_lines", "Donor lines that would move but a rule held them"),
   ("CAST(r.replen_lines AS BIGINT)", "dc_lines", "DC-to-store replenishment lines recommended"),
   ("CAST(r.replen_units AS BIGINT)", "dc_units", "Units in DC-to-store lines"),
   ("ROUND(r.fill_value_aed, 0)", "dc_value_aed", "Retail value of DC-to-store lines (AED)"),
   ("CAST(r.fullsize_topup_units AS BIGINT)", "dc_fullsize_topup_units", "DC units sent only to make a store full-size (flagged for review)"),
   ("CAST(s.universe_lines AS BIGINT)", "stock_lines_checked", "Store x size stock lines the run looked at"),
   ("s.partition_reconciles", "stock_reconciles", "TRUE when every stock line is accounted for as moved, held or kept")],
  f"FROM {FQ}.run_stats_history s LEFT JOIN {FQ}.replen_stats_history r ON r.run_id = s.run_id\n"
  "WHERE COALESCE(s.triggered_by, '') NOT LIKE 'claude-%'"))

# ---- merch_store_stock: every store x size line with a Status (Move / Held / Keep) — same partition as the app ----
_RD = f"(SELECT run_id, line_type, line_key, decision FROM {FQ}.review_decisions)"
STOCK_COLS = [
  ("run_id", "Engine run identifier"), ("run_date", "Date the run used for sales (data through this date)"),
  ("is_latest_full_run", "TRUE for the most recent run covering all countries and categories — use by default"),
  ("is_latest_run", "TRUE for the most recent run of any scope"),
  ("status", "Move = recommended store-to-store transfer; Held = would move but a rule held it; Keep = stays where it is"),
  ("country", "Country (ISO code, e.g. AE)"), ("category", "Business category (e.g. FOOTBALL, RUNNING MENS)"),
  ("division", "FOOTWEAR / APPAREL / ACCESSORIES"), ("gender", "MENS / WOMENS / UNISEX / KIDS"),
  ("season", "Article season, e.g. HOLI 2026 or CORE"), ("style_code", "Style (generic article) code"),
  ("style_name", "Style name without size"), ("article", "Size-level article code"), ("size", "Size"),
  ("store", "Store holding the stock (the giving store for Move and Held)"), ("store_name", "Store name"),
  ("to_store", "Receiving store for Move, would-be receiver for Held, empty for Keep"), ("to_store_name", "Receiving store name"),
  ("units", "Move: units to transfer. Held: units that could move if released. Keep: units in stock. Do not add units across statuses"),
  ("value_aed", "Cost value of those units (AED)"),
  ("stock_status", "Sales health of the size at the store: Dead (no sales), Slow-moving, Healthy, New to store"),
  ("reason", "Move: why it moves. Held: the rule that held it. Keep: why it stays"),
  ("store_stock", "Units of this size at the store before any move"), ("store_cover_days", "Days of cover at the store before any move"),
  ("to_store_size_status_after", "Move only: Full size / Cut size / No size rule at the receiving store after the moves"),
  ("review_status", "Move only: Approved / Rejected / Pending for lines that need a person, Not required for routine lines")]
def _stock_branch(sel, frm):
    return "SELECT " + ", ".join(f"{e} AS {n}" for (n, _), e in zip(STOCK_COLS, sel)) + " " + frm
_cls = "CASE {c} WHEN 'DEAD' THEN 'Dead (no sales)' WHEN 'SLOW_MOVING' THEN 'Slow-moving' WHEN 'HEALTHY' THEN 'Healthy' WHEN 'NEW_TO_STORE' THEN 'New to store' ELSE {c} END"
_needs_rev = f"(x.inventory_cost_aed >= 1000 OR x.recommended_qty >= 6)"
_mv = _stock_branch(
  ["x.run_id", "m.run_date", "m.is_latest_full_run", "m.is_latest_run", "'Move'", "x.country", "x.business_category", "x.division",
   "x.gender", "x.article_season", "x.parent_article", "x.generic_article_name", "x.article", "x.size",
   "x.donor_store", "x.donor_store_name", "x.receiver_store", "x.receiver_store_name", "CAST(x.recommended_qty AS DOUBLE)",
   "ROUND(x.inventory_cost_aed, 2)", _cls.format(c="x.reason"), "x.movement_note", "ROUND(x.donor_stock, 0)",
   "ROUND(x.donor_cover_days, 1)", "x.receiver_size_status_post_move",
   f"CASE WHEN rd.decision IS NOT NULL THEN INITCAP(rd.decision) WHEN {_needs_rev} THEN 'Pending' ELSE 'Not required' END"],
  f"FROM {FQ}.recommendations_history x JOIN {FQ}.merch_runs m ON m.run_id = x.run_id "
  f"LEFT JOIN {_RD} rd ON rd.run_id = x.run_id AND rd.line_type = 'Store move' "
  f"AND rd.line_key = CONCAT(x.donor_store, '|', x.receiver_store, '|', x.article)")
_hd = _stock_branch(
  ["x.run_id", "m.run_date", "m.is_latest_full_run", "m.is_latest_run", "'Held'", "x.country", "x.business_category", "x.division",
   "x.gender", "x.article_season", "x.parent_article", "x.generic_article_name", "x.article", "x.size",
   "x.donor_store", "x.donor_store_name", "x.would_be_receiver", "x.would_be_receiver_name", "CAST(x.indicative_qty AS DOUBLE)",
   "ROUND(x.indicative_value_aed, 2)", _cls.format(c="x.class"), "x.held_reason", "ROUND(x.effective_stock, 0)",
   "ROUND(x.cover_days, 1)", "CAST(NULL AS STRING)", "CAST(NULL AS STRING)"],
  f"FROM {FQ}.held_history x JOIN {FQ}.merch_runs m ON m.run_id = x.run_id")
_kp = _stock_branch(
  ["x.run_id", "m.run_date", "m.is_latest_full_run", "m.is_latest_run", "'Keep'", "x.country", "x.business_category", "x.division",
   "x.gender", "x.article_season", "x.parent_article", "x.generic_article_name", "x.article", "x.size",
   "x.store_id", "x.store_name", "CAST(NULL AS STRING)", "CAST(NULL AS STRING)", "CAST(x.effective_stock AS DOUBLE)",
   "ROUND(x.inventory_cost_aed, 2)", _cls.format(c="x.class"), "x.not_moved_reason", "ROUND(x.effective_stock, 0)",
   "ROUND(x.cover_days, 1)", "CAST(NULL AS STRING)", "CAST(NULL AS STRING)"],
  # Keep rows only for the latest full run (the not-moved archive is ~200K rows per run)
  f"FROM {FQ}.not_moved_history x JOIN {FQ}.merch_runs m ON m.run_id = x.run_id AND m.is_latest_full_run")
V.append(f"CREATE OR REPLACE VIEW {FQ}.merch_store_stock (\n  "
         + ",\n  ".join(f"{n} COMMENT '{c}'" for n, c in STOCK_COLS)
         + "\n) COMMENT 'Every store x size stock line with what the engine decided: Move (store-to-store transfer), Held (would move but a rule held it) or Keep (stays). Keep rows exist for the latest full run only. Filter is_latest_full_run = TRUE unless a run is named.' AS\n"
         + _mv + "\nUNION ALL\n" + _hd + "\nUNION ALL\n" + _kp)

# ---- merch_dc_replenishment: every store x size DC need with Status (Replenish / Not filled) ----
DC_COLS = [
  ("run_id", "Engine run identifier"), ("run_date", "Date the run used for sales (data through this date)"),
  ("is_latest_full_run", "TRUE for the most recent run covering all countries and categories — use by default"),
  ("is_latest_run", "TRUE for the most recent run of any scope"),
  ("status", "Replenish = DC sends stock to the store; Not filled = the store needs stock but the DC is not sending it"),
  ("country", "Country (ISO code)"), ("category", "Business category"), ("division", "Division"),
  ("style_code", "Style code"), ("style_name", "Style name"), ("article", "Size-level article code"), ("size", "Size"),
  ("store", "Store"), ("store_name", "Store name"), ("store_grade", "Store grade"),
  ("source_dc", "Replenish: DC supplying the stock"), ("dc_stock", "DC stock available for this size"),
  ("units", "Replenish: units the DC sends. Not filled: units the store needed. Do not add units across statuses"),
  ("reason", "Replenish: why the store gets it. Not filled: why the DC is not sending it"),
  ("store_stock_before", "Replenish: store stock today"), ("store_in_transit", "Replenish: units already on the way"),
  ("store_stock_after", "Replenish: store stock after the line arrives"), ("daily_demand", "Replenish: units sold per day"),
  ("line_value_aed", "Replenish: retail value of the units sent (AED)"),
  ("annual_value_at_risk_aed", "Not filled: yearly sales value at risk (AED)"),
  ("review_status", "Replenish only: Approved / Rejected / Pending for lines that need a person, Not required for routine lines")]
def _dc_branch(sel, frm):
    return "SELECT " + ", ".join(f"{e} AS {n}" for (n, _), e in zip(DC_COLS, sel)) + " " + frm
_SMJ = f"LEFT JOIN (SELECT PLANT, MAX(`/BIC/ZNAME1`) AS nm FROM {SM} GROUP BY PLANT) sm ON sm.PLANT = x.receiver_store"
_dc_rev = "(x.decision IS NOT NULL OR x.recommended_qty >= 6 OR x.recommended_qty * x.unit_price_aed >= 1000)"
_rp = _dc_branch(
  ["x.run_id", "m.run_date", "m.is_latest_full_run", "m.is_latest_run", "'Replenish'", "x.country", "x.category", "x.division",
   "x.parent_article", "x.generic_article_name", "x.article", "x.size", "x.receiver_store", "TRIM(sm.nm)", "CAST(NULL AS STRING)",
   "x.source", "ROUND(x.source_available, 0)", "CAST(x.recommended_qty AS DOUBLE)",
   "CASE x.reason WHEN 'REPLEN_DEPLETION' THEN 'Sold below 14-day cover' WHEN 'REPLEN_DISPLAY' THEN 'Display: 1 per key size' "
   "WHEN 'REPLEN_FULL_SIZE' THEN 'Full-size top-up' WHEN 'REPLEN_CENSORED' THEN 'Lost sales signal' ELSE x.reason END",
   "ROUND(x.store_atp, 0)", "ROUND(x.store_intransit, 0)", "ROUND(x.store_atp_after, 0)", "ROUND(x.demand_rate_day, 3)",
   "ROUND(x.recommended_qty * x.unit_price_aed, 2)", "CAST(NULL AS DOUBLE)",
   f"CASE WHEN rd.decision IS NOT NULL THEN INITCAP(rd.decision) WHEN {_dc_rev} THEN 'Pending' ELSE 'Not required' END"],
  f"FROM {FQ}.replen_recommendations_history x JOIN {FQ}.merch_runs m ON m.run_id = x.run_id {_SMJ} "
  f"LEFT JOIN {_RD} rd ON rd.run_id = x.run_id AND rd.line_type = 'DC line' "
  f"AND rd.line_key = CONCAT(x.source, '|', x.receiver_store, '|', x.article)")
_nf = _dc_branch(
  ["x.run_id", "m.run_date", "m.is_latest_full_run", "m.is_latest_run", "'Not filled'", "x.country", "CAST(NULL AS STRING)",
   "CAST(NULL AS STRING)", "x.parent_article", "TRIM(pm.TXTMD)", "x.article", "x.size", "x.receiver_store", "TRIM(sm.nm)",
   "x.store_grade", "CAST(NULL AS STRING)", "ROUND(x.wh_atp, 0)", "CAST(x.store_need AS DOUBLE)",
   "CASE x.not_replenished_reason WHEN 'NO_DC_STOCK' THEN 'No DC stock' WHEN 'DC_SHORTFALL_OR_BLOCKED' THEN 'DC short or blocked' "
   "WHEN 'HELD_RECEIVER_STAYS_CUT' THEN 'Held: store would stay cut-size' WHEN 'MOVE_BACK_30D' THEN 'Blocked: moved out under 30 days ago' "
   "ELSE x.not_replenished_reason END",
   "CAST(NULL AS DOUBLE)", "CAST(NULL AS DOUBLE)", "CAST(NULL AS DOUBLE)", "CAST(NULL AS DOUBLE)", "CAST(NULL AS DOUBLE)",
   "ROUND(x.value_at_risk_aed, 0)", "CAST(NULL AS STRING)"],
  f"FROM {FQ}.replen_not_replenished_history x JOIN {FQ}.merch_runs m ON m.run_id = x.run_id {_SMJ} "
  f"LEFT JOIN (SELECT MATERIAL, MAX(TXTMD) AS TXTMD FROM {PM} WHERE MATL_CAT = '01' GROUP BY MATERIAL) pm ON pm.MATERIAL = x.parent_article")
V.append(f"CREATE OR REPLACE VIEW {FQ}.merch_dc_replenishment (\n  "
         + ",\n  ".join(f"{n} COMMENT '{c}'" for n, c in DC_COLS)
         + "\n) COMMENT 'Every store x size DC-to-store need: Replenish (the DC sends stock) or Not filled (with the reason). Filter is_latest_full_run = TRUE unless a run is named.' AS\n"
         + _rp + "\nUNION ALL\n" + _nf)

if __name__ == "__main__":
    sql = [v.replace("\n", " ") for v in V]
    if "--apply" in sys.argv:
        helper = sys.argv[sys.argv.index("--apply") + 1]
        for name, q in zip(["runs", "store_stock", "dc_replenishment"], sql):
            r = subprocess.run(["python3", helper], input=q, capture_output=True, text=True)
            ok = "error" not in (r.stdout + r.stderr).lower() or "KeyError: 'columns'" in r.stderr
            print(f"merch_{name}: {'ok' if ok else 'FAILED'}", "" if ok else (r.stdout + r.stderr)[-600:])
    else:
        print(";\n\n".join(V))
