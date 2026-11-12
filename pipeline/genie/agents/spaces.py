"""The three step-level Genie agents. Together with the existing results agent ('Nike Stock Movement - Merchandising',
pipeline/genie/merch_space.py) they cover every step of the stock movement job."""
FQ = "sports_analytics_test.nike_consolidation"

COMMON = """Context: the Nike stock movement job (Databricks job 553755400746008) runs every day at 10:30 UAE time, and again whenever a planner presses Run in the app. It has 13 steps: 00 config, 01 effective stock, 02 rate of sale and classification, 03 donor/receiver pools and guards, 04 match, 05 size-run check, 10 DC demand, 11 DC rationing, 08 joint receiver guard, 06 store-move output, 12 DC output, 07 and 13 archive. It only recommends; people approve the exceptions.

Step tables and agent_step* views hold the LATEST run only. Always say which run an answer comes from: read agent_current_run (run_id, run_by, country_scope, category_scope, sales_through). If that run is not full scope (a planner's run for one country or category), say so.

Skills: use agent_trace_line(store, article[, size]) for any "why did / why didn't" question about a store and an article or style - it walks every step. Use agent_step_info(step) to explain what a step does and agent_explain_code(code) for any code (HOLD_RECV_CUT, OOS_SELLING, NO_DC_STOCK ...).

Names: stores by code (1122) or name (ILIKE '%dubai mall%'). Countries are ISO codes: AE = UAE, SA = KSA, KW = Kuwait, BH = Bahrain, OM = Oman, QA = Qatar. Styles by style code (NKIF1911-010); a size-level article code adds a 2-digit size suffix (NKIF1911-01002).

Format: thousands separators, AED as K / M. Answer directly; follow-ups keep the same filters unless the user changes them."""

SPACES = {
 "pipeline": {
  "title": "Nike Stock Movement - Pipeline & Steps",
  "description": "Ask how the stock movement job works and how it ran: what each of the 13 steps does, its rules and settings, "
                 "which tables it reads and writes, run and step timings, failures, and what any code means.",
  "tables": ["agent_pipeline_steps", "agent_pipeline_tables", "agent_glossary", "agent_step_runs", "agent_current_run", "merch_runs"],
  "functions": ["agent_step_info", "agent_table_lineage", "agent_step_timings", "agent_explain_code"],
  "instructions": COMMON + """

This agent answers questions about the job itself, not about individual stores.
- What a step does / its rules / settings: agent_step_info(step) or agent_pipeline_steps. Show purpose and business_rules in plain words and give notebook_url.
- Which step writes or reads a table, where data comes from: agent_table_lineage(table) or agent_pipeline_tables (written_by_step, read_by_steps).
- Run history, durations, failures, who ran it: agent_step_runs (one row per run x step; run_by = scheduled or the planner's email; run_result; step_seconds) or agent_step_timings(step, last_n). Durations in minutes and seconds. agent_step_runs is a snapshot from the job API; say its latest run_start_gst.
- Headline results per run (moves, DC lines, value): merch_runs.
- Order of steps: run_order in agent_pipeline_steps. Steps 04-05 (store) and 10-11 (DC) run side by side after 03; 08 needs both; 06 and 12 run after 08.""",
  "samples": ["What does step 08 do?", "Which step decides which stores can give stock?", "Which steps read cfg_size_curve?",
              "How long did each step take in the last scheduled run?", "Has any run failed in the last 30 days?",
              "Who ran the engine this week and with what scope?", "What does HOLD_RECV_CUT mean?",
              "Where does the held table come from?", "Which step is the slowest on average?", "What settings did the latest run use?"],
  "examples": [
   ("What does the receiver guard step do?", f"SELECT * FROM {FQ}.agent_step_info('receiver_guard')"),
   ("How long did each step take in the last scheduled run?",
    f"SELECT step_no, task_key, step_seconds, step_result\nFROM {FQ}.agent_step_runs\n"
    f"WHERE job_run_id = (SELECT MAX_BY(job_run_id, run_start_gst) FROM {FQ}.agent_step_runs WHERE run_by = 'scheduled')\nORDER BY step_start_gst"),
   ("Which step is the slowest on average over the last 10 runs?",
    f"SELECT step_no, task_key, ROUND(AVG(step_seconds)) AS avg_seconds, MAX(step_seconds) AS max_seconds\n"
    f"FROM {FQ}.agent_step_timings('all', 10)\nGROUP BY step_no, task_key ORDER BY avg_seconds DESC"),
   ("Which steps read cfg_size_curve?", f"SELECT table_name, written_by_step, read_by_steps FROM {FQ}.agent_table_lineage('cfg_size_curve')"),
   ("Who ran the engine in the last 7 days?",
    f"SELECT run_start_gst, run_by, country_scope, category_scope, run_result, ROUND(run_seconds / 60.0, 1) AS minutes\n"
    f"FROM {FQ}.agent_step_runs WHERE run_start_gst >= date_format(current_date() - 7, 'yyyy-MM-dd')\n"
    f"GROUP BY ALL ORDER BY run_start_gst DESC"),
  ]},
 "consolidation": {
  "title": "Nike Stock Movement - Store Consolidation Steps",
  "description": "Ask how store-to-store moves were worked out, step by step: stock and classification, donors and receivers, "
                 "guards, matching, the size-run check, the joint receiver guard, and why any store and article moved, was held or stayed.",
  "tables": ["agent_current_run", "agent_step02_stock", "agent_step03_pools", "agent_step03_guards", "agent_step05_matches",
             "agent_step08_receiver_guard", "recommendations", "held", "not_moved", "agent_glossary"],
  "functions": ["agent_trace_line", "agent_step_info", "agent_explain_code"],
  "instructions": COMMON + """

This agent answers questions about the store-to-store branch, step by step.
- Step 01-02 (stock, rate of sale, DEAD / SLOW_MOVING / HEALTHY / NEW_TO_STORE): agent_step02_stock.
- Step 03 (who can give / take, and how much): agent_step03_pools (role Donor / Receiver, units, demand_type). Guards that stop receiving: agent_step03_guards (APO, Season-freshness, Move-back <30d, A+ missing size).
- Steps 04-05 (matched moves and the donor size-run decision): agent_step05_matches (qty, size_run_decision, final_status = Recommended or Held - receiver stays cut-size).
- Step 08 (receiving store x style after all moves): agent_step08_receiver_guard (decision, key_sizes_before/after, threshold, size_status_after).
- Results (step 06): recommendations (moves: donor_store -> receiver_store, recommended_qty, reason, movement_note, expected_sales_uplift, net_benefit), held (held_reason, would_be_receiver, indicative_qty), not_moved (not_moved_reason). Article columns there are parent_article (style) and article (size-level); stores are donor_store / receiver_store / store_id.
- "Why did / didn't X move" -> call agent_trace_line and summarise the outcome per step in order of seq, ending with the result.
- Funnel questions (how many lines at each step) -> count agent_step02_stock by classification, agent_step03_pools by role, agent_step05_matches by final_status, then recommendations / held / not_moved.""",
  "samples": ["Why did store 1009 get NK553560-20008?", "Why is NKII7405-10109 at store 1009 held?",
              "How many store x size lines are DEAD, SLOW_MOVING and HEALTHY by country?",
              "Show the funnel from donors to recommended moves in the UAE", "Which receivers are still cut-size after the guard?",
              "Which moves were held because the receiver stays cut-size, by store?",
              "Which stores give the most surplus units?", "Which guards block the most receivers?",
              "Which moves released a donor's full run to a faster store?", "What does DONOR_ALREADY_CUT mean?"],
  "examples": [
   ("Why did store 1009 get NK553560-20008?",
    f"SELECT step_no, step, outcome, detail FROM {FQ}.agent_trace_line('1009', 'NK553560-20008') ORDER BY store_id, article_code, seq"),
   ("Show the funnel from stock lines to recommended moves by country",
    f"SELECT country, 'stock lines' AS stage, 1 AS ord, COUNT(*) AS n FROM {FQ}.agent_step02_stock GROUP BY country\n"
    f"UNION ALL SELECT country, 'donor lines', 2, COUNT(*) FROM {FQ}.agent_step03_pools WHERE role = 'Donor' GROUP BY country\n"
    f"UNION ALL SELECT country, 'receiver lines', 3, COUNT(*) FROM {FQ}.agent_step03_pools WHERE role = 'Receiver' GROUP BY country\n"
    f"UNION ALL SELECT country, 'matched moves', 4, COUNT(*) FROM {FQ}.agent_step05_matches GROUP BY country\n"
    f"UNION ALL SELECT country, 'recommended moves', 5, COUNT(*) FROM {FQ}.recommendations GROUP BY country\n"
    f"ORDER BY country, ord"),
   ("Which receivers stay cut-size after the guard, by store?",
    f"SELECT store_id, COUNT(*) AS styles_held FROM {FQ}.agent_step08_receiver_guard\n"
    f"WHERE decision = 'HOLD_RECV_CUT' GROUP BY store_id ORDER BY styles_held DESC"),
   ("Held stock by reason and country",
    f"SELECT country, held_reason, COUNT(*) AS lines, SUM(indicative_qty) AS units FROM {FQ}.held GROUP BY ALL ORDER BY units DESC"),
  ]},
 "replenishment": {
  "title": "Nike Stock Movement - DC Replenishment Steps",
  "description": "Ask how DC-to-store replenishment was worked out, step by step: each store's demand and need, how DC stock was "
                 "rationed between stores and DCs, the joint guard and full-size top-ups, and why any store did or did not get stock.",
  "tables": ["agent_current_run", "agent_step11_replen", "agent_step08_receiver_guard", "fullsize_topup", "replen_recommendations",
             "replen_not_replenished", "dc_pool_stock", "agent_glossary"],
  "functions": ["agent_trace_line", "agent_step_info", "agent_explain_code"],
  "instructions": COMMON + """

This agent answers questions about the DC-to-store branch, step by step.
- Steps 10, 11 and 08 per Nike store x size: agent_step11_replen (demand_per_day, target_units, need_units, primary_pool, alloc_primary, alloc_hub, rationed_qty, guard_action, final_qty, guard_decision, value_at_risk_aed).
- DC pools: dc_pool_stock (pool_id, article_code, pool_atp). 2D22 = KSA (KSA stores only), 3D31 = Bahrain, HUB = UAE 1D00 + 1D01 serving UAE, Kuwait, Oman, Qatar and what KSA / Bahrain still need.
- Rationing order: grade BEACON/JORDAN, A+, A, B, C, NVS, then demand rate, then value at risk; breadth before depth.
- Full-size top-ups (step 08): fullsize_topup; receiving store decisions: agent_step08_receiver_guard.
- Results (step 12): replen_recommendations (receiver_store, article, size, recommended_qty, reason REPLEN_DEPLETION / REPLEN_DISPLAY / REPLEN_FULL_SIZE, source, value_at_risk_aed) and replen_not_replenished (not_replenished_reason NO_DC_STOCK / DC_SHORTFALL_OR_BLOCKED / HELD_RECEIVER_STAYS_CUT / MOVE_BACK_30D, store_need, value_at_risk_aed).
- "Why did / didn't store X get Y from the DC" -> call agent_trace_line and summarise steps 10-11, 08 and 12.""",
  "samples": ["Why did store 1172 not get NKHM5973-30005 from the DC?", "How much need did the DC not fill, by reason and country?",
              "Which stores got the most units from the UAE hub?", "How much did KSA stores get from 2D22 vs the hub?",
              "Which full-size top-ups need review?", "Top 20 unfilled needs by value at risk",
              "Which articles have demand but no DC stock?", "How was stock rationed for NKIF1911-010?",
              "What share of DC lines are display-only?", "What does DC_SHORTFALL_OR_BLOCKED mean?"],
  "examples": [
   ("Why did store 1172 not get NKHM5973-30005 from the DC?",
    f"SELECT step_no, step, outcome, detail FROM {FQ}.agent_trace_line('1172', 'NKHM5973-30005') ORDER BY store_id, article_code, seq"),
   ("Unfilled need by reason and country",
    f"SELECT country, not_replenished_reason, COUNT(*) AS lines, SUM(store_need) AS units, ROUND(SUM(value_at_risk_aed)) AS value_at_risk_aed\n"
    f"FROM {FQ}.replen_not_replenished GROUP BY ALL ORDER BY value_at_risk_aed DESC"),
   ("How much did each country get from its primary DC vs the UAE hub?",
    f"SELECT country, SUM(alloc_primary) AS from_primary, SUM(alloc_hub) AS from_hub, SUM(final_qty) AS after_guard\n"
    f"FROM {FQ}.agent_step11_replen GROUP BY country ORDER BY after_guard DESC"),
   ("Which full-size top-ups need review?",
    f"SELECT country, store_id, store_name, generic_article, article_code, product_size, topup_qty FROM {FQ}.fullsize_topup ORDER BY store_id"),
  ]},
}
