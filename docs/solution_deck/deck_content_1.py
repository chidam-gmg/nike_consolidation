import textwrap
# Part 1: cover, overview, platform, the stock movement job, forecast, apps.

# ======================= COVER =======================
slide("", f"""
<div>
  <div class="eyebrow">GMG Sports · Nike · Solution architecture</div>
  <h1 style="margin-top:18px">Nike stock movement: the job, the apps, the Genie agents, the skills and the Claude connection</h1>
  <div class="sub">Everything built on Databricks for store consolidation, DC replenishment, forecasting and new launches, and how Genie agents, Genie One skills and Claude (through MCP) sit on top of it. Every resource in this deck is a link.</div>
</div>
<div class="meta">
  <div>Workspace<b>8517174019523704</b>AWS eu-west-1</div>
  <div>Schema<b>sports_analytics_test.nike_consolidation</b>118 tables and views</div>
  <div>Live app<b><a href="{APP_LIVE}">nike-consolidation</a></b>daily job 10:30 GST</div>
  <div>Prepared<b>{DATE}</b>owner chidambaram.s@gmg.com</div>
</div>""", cls="cover")

slide("Contents", cols("%%TOC%%", f"""
<h3>How to read this deck</h3>
{bullets([
  "Every blue name is a link: Databricks objects open in the workspace, tables in Catalog Explorer, notebooks in the editor, Genie agents in Genie, artifacts on claude.ai.",
  "Sections follow the data: platform, the daily job, forecasting, the apps, the Genie agents and skills, Genie One skills, the Claude connection, then operations.",
  "Numbers are from the live workspace on " + DATE + " (latest completed run 20261106T063020Z-7be50b) unless a slide says otherwise.",
  "Status pills: <span class='pill ok'>live</span> in use · <span class='pill'>built</span> created, not yet rolled out · <span class='pill warm'>to do</span> designed, not built.",
])}
<div class="note">Audience: the Databricks team, data owners and the CTO office. It is written as a solution-architecture reference: what exists, where it lives, how the pieces call each other and who can do what.</div>
""", ratio="0.9fr 1.1fr"), kicker="Ten sections, from the platform to operations")

# ======================= OVERVIEW =======================
section("Executive overview")
slide("What we built, in one page", kpis([
    ("1,983", "store-to-store moves in today's run"), ("2,873", "DC-to-store lines, AED 810K"), ("13", "job steps, ~8.7 min average"),
    ("60 / 0", "runs in the last month / failures"), ("4", "Genie agents over every step"), ("5,851", "Spring 2027 styles forecast")]) + cols(
  bullets([
    "<b>One Databricks job</b> (<a href='" + JOB + "'>nike_stock_movement_pipeline</a>) rebuilds every recommendation daily at 10:30 GST and on demand from the app: store-to-store consolidation and DC-to-store replenishment, joined by a receiver full-size guard.",
    "<b>Two Databricks Apps</b>: the <a href='" + APP_LIVE + "'>live app</a> planners use today (run, review, export) and a <a href='" + APP_DEV + "'>dev app</a> with the next generation: live monitor, daily exception brief, forecast and allocation, new launches.",
    "<b>Forecasting</b>: weekly demand per style and country with stock-out censoring and negative-binomial P80; store and size allocation; a new-article forecast with image attributes and an opening pack for Spring 2027.",
  ]),
  bullets([
    "<b>Genie agents</b>: three new step-level agents plus the existing merchandising agent answer plain-language questions about every step, run and line, backed by five SQL skills (Unity Catalog functions).",
    "<b>Genie One skills</b>: paste-in skills to run the job with confirmed settings and to e-mail the exhaustive review list as a pre-filled CSV.",
    "<b>Claude connection</b>: Genie spaces and UC functions reach Claude Desktop and claude.ai through Databricks managed MCP endpoints, signed in per user with Databricks OAuth.",
    "<b>Governance by design</b>: the app reads as the viewer, the job runs as its owner, agents answer as the asker; nothing writes to SAP yet.",
  ])), kicker="A recommend-only stock movement engine on Databricks, with agents that explain every number")

# ---- architecture at a glance ----
d = D(1176, 500)
X = [0, 236, 472, 708, 944]; W = 214
for x, t in zip(X, ["Source data", "Compute", "Unity Catalog", "Serving", "People and agents"]):
    d.group(x, 0, W + 18 if x < 944 else 232, 500, t)
d.box(10, 30, W - 4, 62, "SAP data lake", ["atp_detail: sellable stock", "fct_sap_sales_detail_v: sales"], url=tbl("sap_gmg_datalake.bronze.atp_detail"))
d.box(10, 102, W - 4, 62, "prod_mena (MENA lake)", ["product + store masters", "in-transit, goods receipts"], url=tbl("prod_mena.gold.mena_sports_product_master_view"))
d.box(10, 174, W - 4, 48, "Akeneo", ["product_images"], url=tbl("akeneo_gmg_datalake.gold.product_images"))
d.box(10, 232, W - 4, 62, "Business config", ["store list, size curves,", "BU rules: uploaded in app"], url=tbl("cfg_store_grade"))
d.box(10, 304, W - 4, 62, "Order book (stale)", ["PO, ETA, launch dates", "last ETA Feb 2026"], kind="dash", url=tbl("sports_analytics_test.gold.order_book_request_v"))
d.box(246, 30, W - 4, 78, "Stock movement job", ["13 notebook steps, serverless", "daily 10:30 GST + Run button", "job 553755400746008"], kind="dark", url=JOB)
d.box(246, 118, W - 4, 62, "Forecast & allocation", ["notebook 20, on demand", "NB P80, store + size split"], url=folder(2208784873396674))
d.box(246, 190, W - 4, 62, "New-article forecast", ["notebooks 21 + 22", "look-alikes + opening pack"], url=folder(2208784873396674))
d.box(246, 262, W - 4, 62, "Agent + review builders", ["build_agents.py, review pack", "run locally (Claude Code)"], url=REPO)
d.box(482, 30, W - 4, 78, "nike_consolidation schema", ["sports_analytics_test", "118 tables and views", "Delta, Unity Catalog"], kind="accent", url=SCHEMA)
d.box(482, 118, W - 4, 48, "Step tables", ["latest run, replaced each run"])
d.box(482, 176, W - 4, 48, "Results + *_history", ["every run kept by run_id"])
d.box(482, 234, W - 4, 48, "fc_ / al_ / nl_", ["forecast, allocation, launches"])
d.box(482, 292, W - 4, 48, "agent_ knowledge + views", ["for Genie and the skills"])
d.box(482, 350, W - 4, 48, "cfg_ + decisions", ["config, approvals, sign-off"])
d.box(718, 30, W - 4, 48, "SQL warehouse", ["Genie_SQL_Warehouse, serverless"], url=WH)
d.box(718, 88, W - 4, 48, "Live app", ["run, review, export"], kind="ok", url=APP_LIVE)
d.box(718, 146, W - 4, 48, "Dev app", ["monitor, brief, forecasts"], url=APP_DEV)
d.box(718, 204, W - 4, 48, "Genie agents (4)", ["steps, runs, results"], kind="accent", url=genie(SP["pipeline"]))
d.box(718, 262, W - 4, 48, "Skills: UC functions (6)", ["trace a line, explain a step"], url=fn("agent_trace_line"))
d.box(718, 320, W - 4, 48, "Managed MCP endpoints", ["/api/2.0/mcp/genie, /functions"])
d.box(954, 30, 212, 48, "Planners, managers", ["browser, Databricks SSO"])
d.box(954, 88, 212, 48, "Claude Desktop / claude.ai", ["custom MCP connectors"], kind="accent")
d.box(954, 146, 212, 48, "Genie One", ["pasted skills: run, review"])
d.box(954, 204, 212, 48, "Outlook + OneDrive", ["review e-mail + CSV"])
d.box(954, 262, 212, 48, "SAP", ["order push: not built"], kind="dash")
for x in (224, 460, 696, 932):
    d.arrow([(x, 250), (x + 22, 250)])
d.text(10, 430, "Data flows left to right; people and agents read on the right. Only the job writes recommendations; the apps, Genie and Claude read as the signed-in user.", 12)
d.text(10, 450, "Writes back: app config uploads (cfg_), approvals (review_decisions, al_decisions), Genie conversations. No write to SAP.", 12)
slide("Architecture at a glance", d.svg(), kicker="Five layers: source data, compute, Unity Catalog, serving, and the people and agents that use it")

# ---- inventory ----
slide("Resource inventory: apps, jobs, compute, code", table(["Resource", "What it is", "Status"], [
    [A(APP_LIVE, "nike-consolidation") + " · " + A(APP_SET, "settings"), "Live Databricks App planners use: run the engine, review moves and DC lines, approvals, export", "<span class='pill ok'>live</span>"],
    [A(APP_DEV, "nike-consolidation-dev") + " · " + A(APP_DEV_SET, "settings"), "Dev app, read-only: Live monitor, Daily brief, Forecast & allocation, New launches, Store consolidation, Replenishment", "<span class='pill'>built</span>"],
    [A(JOB, "nike_stock_movement_pipeline") + " (553755400746008)", "The engine: 13 notebook steps, serverless, daily 10:30 GST, max 1 concurrent run, queueing on", "<span class='pill ok'>live</span>"],
    [A(LAST_RUN, "Latest completed run (5 Nov)"), "452 s, all 13 steps succeeded", "<span class='pill ok'>live</span>"],
    [A(job(1086461525973252), "nike-app-autostart") + " · " + A(job(596330768678080), "nike-app-autostop"), "Start the apps 07:00, stop 19:00 GST, Mon-Fri (app compute cost)", "<span class='pill ok'>live</span>"],
    [A(job(353261038085683), "replenishment_pipeline_P1") + " · " + A(job(756802809605811), "nike_consolidation_pipeline"), "Earlier standalone jobs; replenishment is now inside the merged engine job", "<span class='pill grey'>legacy</span>"],
    [A(folder(2208784873396674), "nike-consolidation-pipeline") + " (workspace folder)", "The 13 step notebooks + forecast/20, newlaunch/21, newlaunch/22", "<span class='pill ok'>live</span>"],
    [A(folder(2208784873396693), "nike-consolidation-app") + " (workspace folder)", "Deployed source of the live app (app.py, databricks_io.py, app.yaml)", "<span class='pill ok'>live</span>"],
    [A(WH, "Genie_SQL_Warehouse") + " (1c9706d9594f1f2f)", "Small serverless Pro warehouse: every app query, Genie and the skills", "<span class='pill ok'>live</span>"],
    [A(SCHEMA, "sports_analytics_test.nike_consolidation"), "All engine, forecast, launch, agent and decision tables (118)", "<span class='pill ok'>live</span>"],
    [A(REPO, "github.com/chidam-gmg/nike_consolidation"), "Private repo: pipeline, apps, Genie builders, docs (branch dev-agent-tab)", "<span class='pill ok'>live</span>"],
], widths=["34%", "54%", "12%"]), kicker="Where everything lives (1 of 2)")

slide("Resource inventory: Genie agents, skills, artifacts", cols(table(["Genie agent / skill", "Status"], [
    [A(genie(SP["pipeline"]), "Nike Stock Movement - Pipeline &amp; Steps"), "<span class='pill'>built</span>"],
    [A(genie(SP["consolidation"]), "Nike Stock Movement - Store Consolidation Steps"), "<span class='pill'>built</span>"],
    [A(genie(SP["replenishment"]), "Nike Stock Movement - DC Replenishment Steps"), "<span class='pill'>built</span>"],
    [A(genie(SP["merch"]), "Nike Stock Movement - Merchandising"), "<span class='pill ok'>live</span>"],
    [A(genie(SP["cxo"]), "GMG_SPORTS_CXO") + " (Sports sales, budget, stock)", "<span class='pill ok'>live</span>"],
    [A(fn("agent_trace_line"), "<code>agent_trace_line</code>") + " + 4 more skills", "<span class='pill'>built</span>"],
    [A(fn("agent_review_actions_for"), "<code>agent_review_actions_for</code>"), "<span class='pill'>built</span>"],
    ["Genie One skill: run-nike-stock-movement", "<span class='pill'>built</span>"],
    ["Genie One skill: nike-review-email", "<span class='pill'>built</span>"],
], cls="t sm", widths=["78%", "22%"]), table(["Published artifact (claude.ai)", "Purpose"], [
    [A(ART["genie_doc"], "Nike Stock Movement Genie Agents"), "Doc: agents, skills, tests, bundling"],
    [A(ART["sysmap"], "Stock Movement System Map"), "Live app connections + Run sequence"],
    [A(ART["handover"], "Nike Stock Movement Handover"), "Jobs: where written, how scheduled"],
    [A(ART["brief"], "Planner Morning Brief"), "Exception-management mock-up"],
    [A(ART["agentlab"], "Agent Lab Concepts"), "Early agentic UI concepts"],
    [A(ART["lifecycle"], "Merchandising Lifecycle"), "Business vs data process map"],
], cls="t sm", widths=["52%", "48%"]), ratio="1fr 1fr"), kicker="Where everything lives (2 of 2)")

# ======================= PLATFORM =======================
section("Platform foundations")
slide("Workspace, identity and compute", cols(table(["Item", "Value"], [
    ["Workspace", A(H, "dbc-74c28e5e-272e.cloud.databricks.com") + "<br>ID <code>8517174019523704</code>"],
    ["Cloud / region", "AWS eu-west-1 · metastore <code>metastore-eu-west-1</code>"],
    ["Account", "<code>112156b7-5693-4274-8ebc-5efc0a998f4f</code>"],
    ["Catalog / schema", A(SCHEMA, "sports_analytics_test.nike_consolidation")],
    ["SQL warehouse", A(WH, "Genie_SQL_Warehouse") + " · Small · serverless · Pro"],
    ["Job compute", "Serverless notebooks (no cluster to manage)"],
    ["App compute", "Databricks Apps (Streamlit 1.66.1), auto-start 07:00 / stop 19:00 GST"],
    ["Builder access", "Databricks CLI profile <code>gmg-sso</code>, OAuth U2M browser SSO, no PAT"],
], cls="t sm", widths=["28%", "72%"]), f"""
<h3>Architecture choices</h3>
{bullets([
  "<b>Serverless everywhere</b>: the job, the warehouse and the apps start on demand; nothing idles outside 07:00-19:00 except the daily job.",
  "<b>One schema</b> for engine, forecast, launches, agents and decisions keeps lineage and grants simple; history tables carry the run_id.",
  "<b>Notebooks as steps</b>: each step is one notebook with a frozen run_config, so any step can be rerun or debugged in isolation.",
  "<b>Delta replace + append</b>: step outputs are replaced each run (cheap, always current); *_history tables append per run (audit, run picker, Genie history).",
  "<b>No secrets in code</b>: user OAuth for people, app service principals for app-to-job, CLI SSO for builders.",
])}
<div class="note">Unity Catalog system tables (<code>system.lakeflow</code>, <code>system.access</code>) are not granted to the project user, so run history for the agents comes from the Jobs API instead.</div>
""", ratio="1fr 1.05fr"), kicker="One AWS workspace, one schema, serverless compute, SSO identities")

# ---- data landscape ----
d = D(1176, 500)
d.group(0, 0, 360, 500, "Upstream (read only)")
srcs = [("sap_gmg_datalake.bronze.atp_detail", "Stock: ATP per store x size (SL01)"),
        ("sap_gmg_datalake.silver.fct_sap_sales_detail_v", "Sales lines, channels 10 + 20"),
        ("prod_mena.silver.combined_inventory", "In-transit to stores"),
        ("prod_mena.gold.mena_sports_product_master_view", "Article master (style, size, season)"),
        ("prod_mena.gold.mena_sports_store_master_view", "Store master (country, grade)"),
        ("prod_mena.gold.sports_store_and_warehouse_grn_dates", "Goods receipts: new-to-store"),
        ("akeneo_gmg_datalake.gold.product_images", "Images + colour (forecast, launches)")]
for i, (t, s) in enumerate(srcs):
    d.box(10, 28 + i * 67, 340, 60, t.split(".")[-1], [s, t.rsplit(".", 1)[0]], url=tbl(t), tsize=12.5, lsize=11)
d.group(400, 0, 776, 500, "sports_analytics_test.nike_consolidation (project)")
layers = [("Config (cfg_)", "18 tables: store list, size curves, BU rules, kids bands, DC map, guardrails, line managers", "cfg_store_grade"),
          ("Step outputs (latest run)", "effective_stock, stock_classified, donor_pool, receiver_pool, match_*, replen_* ...", "stock_classified"),
          ("Results", "recommendations, held, not_moved, run_stats, replen_recommendations, replen_not_replenished", "recommendations"),
          ("History (append per run)", "*_history for moves, held, not-moved, stats, DC lines, unfilled needs", "run_stats_history"),
          ("Forecast + allocation", "fc_forecast, fc_backtest, fc_error_bands, fc_size_curve, al_allocation, al_size_run", "fc_forecast"),
          ("New launches", "nl_forecast, nl_analogues, nl_backtest, nl_image_tags, nl_opening_pack, nl_pack_country", "nl_forecast"),
          ("Decisions + brief", "review_decisions, al_decisions, ex_actions, cfg_guardrails, cfg_line_manager", "review_decisions"),
          ("Agents (agent_, merch_)", "knowledge tables, step views, review actions, curated merch views", "agent_pipeline_steps")]
for i, (t, s, link) in enumerate(layers):
    x = 410 + (i % 2) * 380; y = 28 + (i // 2) * 116
    d.box(x, y + 6, 366, 104, t, textwrap.wrap(s, 58), url=tbl(link), kind="accent" if i in (2, 3) else "", tsize=13.5, lsize=11.2)
d.arrow([(360, 250), (398, 250)])
slide("Unity Catalog data landscape", d.svg(), kicker="Seven upstream tables feed one project schema organised in layers")

# ---- identity & security ----
d = D(1176, 440)
lanes = [
  ("Planner in the app", [("Planner", "Databricks SSO"), ("Databricks App", "forwards user token, scope sql"), ("SQL warehouse", "runs as the planner"), ("Unity Catalog", "planner's own grants")], "ok"),
  ("Run button", [("App service principal", "app-1wmqjr nike-consolidation"), ("Job run-now", "Can manage run on the job"), ("Job runs as owner", "chidambaram.s@gmg.com"), ("Reads SAP, writes schema", "planner needs no SAP access")], ""),
  ("Claude / Genie One", [("Claude or Genie One", "per-user Databricks OAuth"), ("Managed MCP / Genie", "/api/2.0/mcp/genie/{space}"), ("SQL warehouse", "runs as the asker"), ("Unity Catalog", "asker's own grants")], "accent"),
  ("Builder (Claude Code)", [("Claude Code on laptop", "CLI profile gmg-sso"), ("OAuth U2M SSO", "no PAT, token refresh"), ("Statement + Jobs APIs", "create tables, functions"), ("Genie spaces API", "create / update agents")], ""),
]
for r, (name, steps, kind) in enumerate(lanes):
    y = 10 + r * 106
    d.text(0, y + 44, name, 13, "var(--ink)", 700)
    for c, (t, s) in enumerate(steps):
        x = 190 + c * 250
        d.box(x, y + 12, 220, 56, t, [s], kind=kind if c in (0, 3) and kind else "")
        if c < 3:
            d.arrow([(x + 220, y + 40), (x + 248, y + 40)])
slide("Identity and security model", d.svg() + note(
    "Principle: <b>people read as themselves; only the job writes recommendations, and it runs as one accountable owner.</b> The live app's service principal can start the job and nothing else. "
    "The dev app's service principal (<code>388f7a00-26eb-4ec8-91c2-f42e318ee769</code>) has no job access, so its Live monitor falls back to history tables."),
    kicker="Four paths into the data, each with a clear identity")

slide("Access matrix: who needs what", table(["Persona", "Needs", "Granted today"], [
    ["Planner (live app)", "App: Can use · warehouse: Can use · SELECT on schema results + history · MODIFY on cfg_ tables to upload config, review_decisions to approve", "Pilot planners (r.puttaswamy, ashwini.p, ajaya.g and others)"],
    ["Line manager (dev app sign-off)", "Same as planner + MODIFY on al_decisions, ex_actions; mapped in cfg_line_manager", "Mapping table exists; directory not connected"],
    ["Business POC (read only)", "App: Can use · group_inv_optimization · warehouse Can use · SELECT", "Requested for gagan.s@gmg.com"],
    ["Genie agent user", "Genie space: Can run · warehouse: Can use · SELECT on agent_ views and step tables · EXECUTE on agent_ functions", "Owner only: spaces not shared yet"],
    ["Genie One / Claude user", "As Genie agent user + the Claude connector (OAuth sign-in once)", "Owner"],
    ["Run the job", "Job: Can manage run (or through the live app, whose service principal has it)", "App SP + owner"],
    ["Builder / owner", "Schema owner, job owner, app owner", "chidambaram.s@gmg.com"],
    ["Live app SP", "Job: Can manage run", "app-1wmqjr nike-consolidation (4a734335-...)"],
    ["Dev app SP", "None on the job (read-only app); Genie scope for the Ask panel", "app-1wmqjr nike-consolidation-dev (388f7a00-...)"],
], widths=["20%", "52%", "28%"], cls="t sm"), kicker="Least privilege: grant by persona, not by person")

# ======================= THE JOB =======================
section("The stock movement job")
slide("The engine: nike_stock_movement_pipeline", cols(table(["Setting", "Value"], [
    ["Job", A(JOB, "nike_stock_movement_pipeline") + " · 553755400746008"],
    ["Schedule", "Daily 10:30, Asia/Dubai (cron <code>0 30 10 * * ?</code>)"],
    ["Triggered also by", "Live app ▶ Run stock movement (run-now with the planner's settings)"],
    ["Run as", "chidambaram.s@gmg.com (owner)"],
    ["Concurrency", "Max 1 run; queueing on: a second click waits"],
    ["Compute", "Serverless notebook tasks"],
    ["Duration", "Average 8.7 min (60 runs); last scheduled 7.5 min"],
    ["Reliability", "60 runs since 5 Oct 2026, 0 failed"],
    ["Notebooks", A(folder(2208784873396674), "nike-consolidation-pipeline")],
], cls="t sm", widths=["30%", "70%"]), table(["Job parameter", "Default", "Meaning"], [
    ["COUNTRY", "ALL", "ALL or comma list of ISO codes"],
    ["CATEGORY", "ALL", "ALL or | list of business categories"],
    ["ROS_WINDOW_DAYS", "14", "Days of sales for rate of sale"],
    ["IN_TRANSIT_LOOKBACK_DAYS", "14", "In-transit days counted"],
    ["SLOW_MOVING_THRESHOLD_DAYS", "30", "Cover above = slow moving"],
    ["NEW_TO_STORE_DAYS", "14", "Received within = left alone"],
    ["PROTECT_COVER_DAYS", "7", "Cover a donor keeps (< target)"],
    ["TARGET_COVER_DAYS", "14", "Cover a receiver is filled to"],
    ["AS_OF_DATE / ATP_AS_OF", "blank", "Sales through yesterday / stock today"],
    ["RUN_ID / TRIGGERED_BY", "app sets", "Per-run identity for history and isolation"],
], cls="t xs", widths=["40%", "14%", "46%"]), ratio="0.95fr 1.05fr"), kicker="One job, two branches, one joint guard; parameters are frozen in run_config by step 00")

# ---- DAG gantt ----
STEPS = [("00", "config", "00_config_validation", 0, 31, "shared"), ("01", "effective_stock", "01_effective_stock", 32, 93, "shared"),
         ("02", "classify", "02_ros_cover_classify", 125, 36, "shared"), ("03", "pools", "03_pools_guards", 162, 63, "shared"),
         ("04", "match", "04_match_size", 225, 14, "store"), ("05", "reshape", "05_size_curve", 239, 25, "store"),
         ("10", "replen_demand", "10_replen_demand", 225, 24, "dc"), ("11", "replen_ration", "11_replen_ration", 250, 15, "dc"),
         ("08", "receiver_guard", "08_receiver_guard", 265, 95, "join"), ("06", "score", "06_score_output", 361, 63, "store"),
         ("12", "replen_output", "12_replen_output", 361, 30, "dc"), ("13", "replen_history", "13_replen_history", 391, 22, "dc"),
         ("07", "archive", "07_history_archive", 424, 28, "store")]
COL = {"shared": "#66708A", "store": "#2658F2", "dc": "#B15940", "join": "#6B4FC8"}
d = D(1176, 470); X0, X1, TOT = 270, 1100, 452
sx = lambda s: X0 + s / TOT * (X1 - X0)
for m in range(0, 8):
    x = sx(m * 60); d.raw(f'<line x1="{x}" y1="22" x2="{x}" y2="440" stroke="var(--line)"/>'); d.text(x, 16, f"{m} min", 11, anchor="middle")
for i, (no, key, nbn, s, du, lane) in enumerate(STEPS):
    y = 34 + i * 31
    d.raw(f'<a href="{e(nb(NBID[nbn]))}"><text x="0" y="{y + 12}" font-size="12.5" fill="var(--acc)"><tspan font-family="JetBrains Mono" fill="var(--dim)">{no}</tspan>  {e(nbn[3:].replace("_", " "))}</text></a>')
    d.raw(f'<rect x="{sx(s)}" y="{y}" width="{max(du / TOT * (X1 - X0), 3)}" height="16" rx="4" fill="{COL[lane]}"/>')
    d.text(1172, y + 12, f"{du} s", 11.5, anchor="end")
d.text(0, 462, "Bars: real timings of the scheduled run of Thu 5 Nov 2026 (452 s). Steps 04-05 and 10-11 run side by side after 03; 08 needs both branches; names link to the notebooks.", 11.5)
slide("Job DAG and timings", '<div class="legend"><span><i style="background:#66708A"></i>Shared prep</span><span><i style="background:#2658F2"></i>Store consolidation</span><span><i style="background:#B15940"></i>DC replenishment</span><span><i style="background:#6B4FC8"></i>Joint receiver guard</span></div>' + d.svg(),
      kicker="Thirteen tasks; the receiver guard (step 08) and the stock build (step 01) take a third of the time")


def step_rows(keys):
    out = []
    K = {s[0]: s for s in STEP_INFO}
    for k in keys:
        no, key, nbn, purpose, rules, reads, writes = K[k]
        out.append([f"<b>{no}</b> " + A(nb(NBID[nbn]), e(nbn[3:].replace('_', ' '))), purpose + "<br><span class='small'>" + rules + "</span>",
                    " ".join(T(x) if "." not in x or x.count(".") == 2 else C(x) for x in reads), " ".join(T(x) for x in writes)])
    return out


STEP_INFO = [
 ("00", "config", "00_config_validation", "Checks and freezes the run settings.", "Invalid values stop the run; COUNTRY / CATEGORY scope; RUN_ID and TRIGGERED_BY stamped.", ["cfg_scalar_params"], ["run_config"]),
 ("01", "effective_stock", "01_effective_stock", "Builds the stock picture.", "Effective stock = ATP + in-transit; only stores on cfg_store_grade; DC pools 2D22, 3D31, HUB.",
  ["sap_gmg_datalake.bronze.atp_detail", "prod_mena.silver.combined_inventory", "cfg_store_grade"], ["effective_stock", "store_intransit", "dc_pool_stock", "warehouse_stock"]),
 ("02", "classify", "02_ros_cover_classify", "Rate of sale, cover, label.", "DEAD (no sales), SLOW_MOVING (cover > 30 d), NEW_TO_STORE (< 14 d), HEALTHY.",
  ["sap_gmg_datalake.silver.fct_sap_sales_detail_v", "prod_mena.gold.sports_store_and_warehouse_grn_dates"], ["stock_classified", "sales_demand"]),
 ("03", "pools", "03_pools_guards", "Who can give, who can take; guards.", "Donor surplus above 7 d cover; receivers below 14 d, out-of-stock sellers, A+ gaps; APO, season, 30-day move-back guards.",
  ["stock_classified", "cfg_size_curve", "cfg_store_season"], ["donor_pool", "receiver_pool", "apo_block", "season_block", "moveback_block"]),
 ("04", "match", "04_match_size", "Pairs surplus with need per size.", "Same country, allowed BU pairs, receivers by ROS then cover, largest donors first.",
  ["donor_pool", "receiver_pool", "cfg_bu_eligibility"], ["match_sized"]),
 ("05", "reshape", "05_size_curve", "Protects the donor's size run.", "Full-size = key sizes > threshold; a run may go whole to a receiver selling 25% faster, else held.",
  ["match_sized", "cfg_size_curve"], ["match_reshaped_all", "held_size_curve"]),
 ("08", "receiver_guard", "08_receiver_guard", "Joint check of every receiving store x style.", "After all store moves and DC lines: full -> allow; DC can complete -> top-up (review); else hold everything into it.",
  ["match_reshaped_all", "replen_allocated", "dc_pool_stock"], ["receiver_guard_decision", "fullsize_topup", "replen_final", "match_reshaped", "held_receiver_curve"]),
 ("10", "replen_demand", "10_replen_demand", "Store need from the DC per size.", "Target = max(14 d demand, 1 per display size); need = target - ATP - in-transit.",
  ["stock_classified", "dc_pool_stock", "cfg_replen_params"], ["replen_demand"]),
 ("11", "replen_ration", "11_replen_ration", "Shares out short DC stock.", "KSA 2D22, Bahrain 3D31 first; then UAE hub; grade, demand, value at risk; breadth first.",
  ["replen_demand", "dc_pool_stock"], ["replen_allocated"]),
 ("06", "score", "06_score_output", "Scores and writes store moves.", "Uplift = qty x net sales/unit; net benefit = uplift - AED 5/unit (provisional); partition reconciles.",
  ["match_reshaped", "stock_classified"], ["recommendations", "held", "held_moves", "not_moved", "run_stats"]),
 ("12", "replen_output", "12_replen_output", "Writes DC lines and unfilled needs.", "REPLEN_DEPLETION / DISPLAY / FULL_SIZE; not filled: NO_DC_STOCK, SHORTFALL, HELD, MOVE_BACK.",
  ["replen_final", "fullsize_topup"], ["replen_recommendations", "replen_not_replenished", "replen_stats"]),
 ("07", "archive", "07_history_archive", "Archives store results by run ID.", "Append; rerunning a run ID replaces its rows.", ["recommendations", "held", "run_stats"],
  ["recommendations_history", "held_history", "not_moved_history", "run_stats_history"]),
 ("13", "replen_history", "13_replen_history", "Archives DC results by run ID.", "Append; rerunning a run ID replaces its rows.", ["replen_recommendations", "replen_stats"],
  ["replen_recommendations_history", "replen_not_replenished_history", "replen_stats_history"]),
]
HD = ["Step", "What it does and its rules", "Reads", "Writes"]
WD = ["16%", "40%", "21%", "23%"]
slide("Steps 00-03: shared preparation", table(HD, step_rows(["00", "01", "02", "03"]), cls="t sm", widths=WD), kicker="Settings, stock, rate of sale and the donor / receiver pools are built once for both branches")
slide("Steps 04, 05, 08: store consolidation and the joint guard", table(HD, step_rows(["04", "05", "08"]), cls="t sm", widths=WD), kicker="Store-to-store moves are matched, size-protected, then checked together with DC lines")
slide("Steps 10, 11, 12: DC replenishment", table(HD, step_rows(["10", "11", "12"]), cls="t sm", widths=WD), kicker="Demand per Nike store and size, rationed across the KSA, Bahrain and UAE hub pools")
slide("Steps 06, 07, 13: outputs and history", table(HD, step_rows(["06", "07", "13"]), cls="t sm", widths=WD) + note(
    "Every stock line ends in exactly one of <b>moved</b>, <b>held</b> or <b>not moved</b>; <code>run_stats.partition_reconciles</code> proves it each run. "
    "History is append-only and keyed by <code>run_id</code>, which is what the app's run picker, the Genie merchandising agent and the review e-mail read."),
    kicker="Results are replaced each run and archived by run ID")

# ---- lineage flow ----
d = D(1176, 300)
nodes = [("Sources", ["SAP stock, sales", "masters, receipts"], 0), ("01-02", ["stock picture", "rate of sale, label"], 170),
         ("03", ["donors, receivers", "guards"], 340), ("04-05 | 10-11", ["store matches", "DC demand + ration"], 510),
         ("08 joint guard", ["full-size check", "top-ups, holds"], 680), ("06 | 12", ["moves, held, kept", "DC lines, unfilled"], 850),
         ("07 | 13", ["*_history", "by run_id"], 1020)]
for t, ls, x in nodes:
    d.box(x, 40, 150, 70, t, ls, kind="accent" if t.startswith("08") else ("dark" if t == "Sources" else ""))
    if x < 1020:
        d.arrow([(x + 150, 75), (x + 168, 75)])
cons = [("Live app", 850, APP_LIVE), ("Dev app", 935, APP_DEV), ("Genie agents", 1020, genie(SP["consolidation"])), ("Review e-mail", 1100, None)]
d.group(680, 160, 496, 120, "Read by")
for i, (t, _, u) in enumerate(cons):
    d.box(690 + i * 121, 188, 113, 48, t, [], url=u)
d.arrow([(925, 112), (925, 186)]); d.arrow([(1095, 112), (1095, 186)])
d.text(0, 160, "Step tables (01-08) are what the step-level Genie agents and agent_trace_line read; results and history are what the apps,", 12)
d.text(0, 178, "the merchandising agent and the review e-mail read. A planner's scoped run replaces the step tables until the next run.", 12)
slide("End-to-end lineage", d.svg(), kicker="From SAP stock and sales to a reviewed recommendation")

slide("Run history and today's outcome", cols(kpis([("60", "runs since 5 Oct"), ("31 / 29", "scheduled / planner runs"), ("10", "people who ran it"), ("0", "failed runs")]) + table(
    ["Step", "Avg s", "Max s"], [["08 receiver guard", "120", "175"], ["01 effective stock", "93", "115"], ["06 score", "73", "124"],
                                 ["03 pools", "71", "82"], ["00 config", "40", "286"], ["02 classify", "40", "46"], ["12 replen output", "33", "70"]], cls="t sm"),
    kpis([("1,983", "store moves · 3,046 units"), ("1,852", "held lines"), ("2,873", "DC lines · 2,991 units"), ("AED 810K", "DC retail value")]) + table(
    ["Today (6 Nov, 10:30 run)", "Value"], [["Run ID", "<code>20261106T063020Z-7be50b</code>"], ["Stock lines checked", "194,206 (UAE, 21 stores)"],
                                            ["Not moved", "190,589 lines"], ["Actions needing review", "790 (744 DC lines, 46 store moves)"],
                                            ["Source", T("run_stats_history") + " · " + T("replen_stats_history") + " · " + T("agent_step_runs")]], cls="t sm")),
    kicker="Stable daily operation; the active store list is UAE only today")

# ======================= FORECAST & LAUNCHES =======================
section("Forecast, allocation and new launches")
slide("Forecast and allocation (notebook 20)", cols(f"""
<h3>Method</h3>{bullets([
  "Weekly demand per country x style, 8-week horizon; history censored for stock-outs (sqrt gross-up below 70% availability) and events 2024-27.",
  "Tournament per series: MA4, MA12, SES, Croston SBA, TSB; a seasonal variant only wins if it beats flat by more than 1 point of error.",
  "Uncertainty: negative binomial with alpha learned per volume band; P80 = 80% service level; slow-style calibration fades out above 0.25-0.5 a week.",
  "Store split by the store's share of division x gender sales; size split by country x division x AH2 x gender curve (GCC curve when thin).",
  "Allocation: units covering 80% of 14-day outcomes, at least 1 per key size, rationed from the primary DC then the UAE hub by grade.",
])}""", f"""<h3>Latest run (2 Nov 2026)</h3>{kpis([("54%", "error (champion) vs 59% baseline"), ("91%", "P80 coverage"), ("85", "stores, 7 countries"), ("4,444", "units planned, 3,565 lines")])}
<h4 style="margin-top:6px">Tables</h4><p class="small">{" ".join(T(x) for x in ["fc_forecast","fc_history","fc_backtest","fc_leaderboard","fc_error_bands","fc_calibration","fc_size_curve","fc_events","fc_run_summary","fc_run_stages","al_allocation","al_size_run","dim_product_image"])}</p>
<p class="small">Runs on demand as a one-off submit of <code>forecast/20_core_forecast_alloc</code> (serverless). Feeds the dev app's Daily brief and Forecast & allocation tabs.</p>"""),
      kicker="Demand-led allocation for styles with history, all Nike stores aggregated by country")

slide("New-article forecast and opening pack (notebooks 21, 22)", cols(f"""
<h3>Forecast for styles with no sales (Spring 2027)</h3>{bullets([
  "5,851 styles, 403K units in the first 13 weeks; launch week assumed from last spring until buyer dates arrive.",
  "Look-alikes: 10 closest past launches (category, sport, silhouette, life cycle, colour, price) blended 50/50 with earlier colourways of the same model.",
  "Image attributes (vision model) + visual similarity (CLIP) for men's tees with learned weights; low-confidence flag when look-alikes are under 0.45 alike.",
  "Back-test on Spring 2026: country &plusmn;11%, division x gender &plusmn;15%, style 82% error (45% within 2x), P80 covers 80%.",
])}<p class="small">{" ".join(T(x) for x in ["nl_forecast","nl_forecast_country","nl_forecast_weekly","nl_analogues","nl_backtest","nl_weights","nl_image_tags","nl_image_embeddings"])}</p>""", f"""
<h3>Opening pack per store and size</h3>{bullets([
  "Launch stores: full-price Nike (no outlets, e-com, Converse) selling at least 1% of the country's division x gender.",
  "Pack = max(1 per key size, 80% cover of the first 3 weeks); packs use at most 60% of the buy; 40% stays at the DC.",
  "Buy assumed = 13-week P80 until the buy file lands in <code>nl_buy</code>; every view says provisional.",
])}{kpis([("337K", "units to 54 stores"), ("377K", "kept at the DC")])}
<p class="small">{T("nl_opening_pack")} {T("nl_pack_country")} · walkthrough in the dev app's New launches tab.</p>"""),
      kicker="Look-alike forecasting with images, and the first allocation of every new style")

slide("Supporting jobs and one-off runs", table(["Job / run", "What", "Schedule", "Status"], [
    [A(JOB, "nike_stock_movement_pipeline"), "The engine (consolidation + replenishment, 13 tasks)", "Daily 10:30 GST + app", "<span class='pill ok'>live</span>"],
    ["core_forecast_alloc_dev (one-off submit)", "forecast/20_core_forecast_alloc", "On demand", "<span class='pill ok'>live</span>"],
    ["new_launch_forecast_dev (one-off submit)", "newlaunch/21_new_launch_forecast", "On demand", "<span class='pill ok'>live</span>"],
    ["new_launch_opening_pack_dev (one-off submit)", "newlaunch/22_new_launch_opening_pack", "On demand", "<span class='pill ok'>live</span>"],
    [A(job(1086461525973252), "nike-app-autostart"), "Starts the apps", "07:00 GST Mon-Fri", "<span class='pill ok'>live</span>"],
    [A(job(596330768678080), "nike-app-autostop"), "Stops the apps", "19:00 GST Mon-Fri", "<span class='pill ok'>live</span>"],
    [A(job(353261038085683), "replenishment_pipeline_P1"), "Standalone replenishment (now merged into the engine)", "None", "<span class='pill grey'>legacy</span>"],
    [A(job(756802809605811), "nike_consolidation_pipeline"), "Earlier consolidation job", "None", "<span class='pill grey'>legacy</span>"],
], widths=["30%", "38%", "18%", "14%"]) + note("One-off submits are invisible to app service principals. Registering forecast and launches as named jobs (proposed; blocked pending approval) would put them on the Live monitor with live status.", "amber"),
      kicker="One scheduled engine, three on-demand notebooks, two cost-control jobs")

# ======================= APPS =======================
section("The apps")
slide("Live app: nike-consolidation", cols(table(["Area", "What planners do", "Reads / writes"], [
    ["Run parameters", "Set cover days, windows, country, categories; upload store list, size definition, BU matrix, kids bands", "Writes " + T("cfg_size_curve") + " " + T("cfg_bu_eligibility") + " " + T("cfg_store_grade") + " " + T("cfg_kids_size_band")],
    ["▶ Run stock movement", "Start the job with their settings; status card every 4 s", "Jobs API run-now (app SP)"],
    ["Run picker", "Open any recent run: who, when, scope, moves", "Reads " + T("run_stats_history")],
    ["Store consolidation", "Every store x article line: move / held / dont move, filters, full CSV", "Reads " + T("recommendations") + " " + T("held") + " " + T("not_moved")],
    ["Replenishment (DC → store)", "DC lines, reasons, Excel export", "Reads " + T("replen_recommendations")],
    ["Approvals", "Approve / reject lines that need a person", "Writes " + T("review_decisions")],
], cls="t sm", widths=["20%", "42%", "38%"]), f"""
{table(["Setting", "Value"], [["URL", A(APP_LIVE, "open the app")], ["Settings", A(APP_SET, "deployments, logs, permissions")],
   ["Source", A(folder(2208784873396693), "nike-consolidation-app")], ["Auth", "User OAuth, scope <code>sql</code>"],
   ["Service principal", "app-1wmqjr nike-consolidation"], ["Env", "NIKE_JOB_ID, DATABRICKS_WAREHOUSE_ID, NIKE_PIPELINE_DIR"]], cls="t sm", widths=["34%", "66%"])}
{note("Store list = store universe: a partial upload silently shrinks every run. 'Reset store list to baseline' restores it from cfg_store_grade_baseline.", "warn")}""", ratio="1.35fr 1fr"),
      kicker="Two tabs planners use today; per-user run isolation through run_id")

slide("When a planner presses ▶ Run stock movement", f"""<ol class="steps">
<li><div><b>Inputs checked in the app.</b> Protect cover must be below target cover; the size definition must validate; the button is disabled while a run is in progress.</div></li>
<li><div><b>Uploaded files saved first</b>, as the planner, into the cfg_ tables (size definition, BU matrix, store list, kids bands).</div></li>
<li><div><b>The app mints a run ID</b> (UTC time + 6 hex) and calls <code>jobs/run-now</code> on {A(JOB, "job 553755400746008")} with COUNTRY, CATEGORY, six cover settings, RUN_ID and TRIGGERED_BY = the planner's e-mail. The app's service principal has Can manage run; the job runs as its owner.</div></li>
<li><div><b>A status card follows the run</b> every 4 seconds: "Running · step N of 13", with a link to the run. Reopening the app picks the run back up.</div></li>
<li><div><b>The 13 steps run</b> (about 8 minutes); queued if another run is going (max 1 concurrent).</div></li>
<li><div><b>Results load by run ID</b> from the *_history tables, so the planner sees exactly their run; the run picker lists every run with who ran it and its scope.</div></li>
<li><div><b>On failure</b> the card names the failed task; earlier steps may have refreshed the latest tables, but nothing is archived, so the run never appears in the picker.</div></li>
</ol>""" + note("Full walkthrough with every link: " + A(ART["sysmap"], "Stock Movement System Map") + " (claude.ai artifact)."),
      kicker="Seven steps from click to reviewed results")

slide("Dev app: the next generation", table(["Tab", "What it shows", "Built from"], [
    ["Live monitor (first tab)", "Status of every pipeline, 14-day schedule strip, run history with links, time by step; refreshes every minute", T("run_stats_history") + " " + T("replen_stats_history") + " " + T("fc_run_summary") + " + Jobs API"],
    ["Daily brief", "Exception cards only: supply gaps, large sends, forecast swings, overstock, data issues; approve / change / reject", T("al_allocation") + " " + T("cfg_guardrails") + " " + T("ex_actions")],
    ["Forecast & allocation", "Agents board, store walkthrough (country demand → store → size pack), allocation plan with sign-off, analyst detail", T("fc_forecast") + " " + T("al_size_run") + " " + T("al_decisions")],
    ["New launches", "Spring 2027 forecast, look-alikes with images, CTO walkthrough incl. stores and opening pack", T("nl_forecast") + " " + T("nl_opening_pack")],
    ["Store consolidation", "Same as live, with an Approvals queue at the top (790 lines today)", T("recommendations_history") + " " + T("review_decisions")],
    ["Replenishment", "DC → store lines", T("replen_recommendations")],
], widths=["19%", "47%", "34%"], cls="t sm") + note(f"URL {A(APP_DEV, 'nike-consolidation-dev')} · APP_MODE=dev (read-only: no runs, no config writes) · scopes <code>sql</code> + <code>dashboards.genie</code> · Agent lab tab retired 5 Nov 2026."),
      kicker="Exception management first: the planner validates only what the agents flag")

d = D(1176, 300)
d.box(0, 20, 190, 70, "Forecast run", ["notebook 20", "al_allocation"], kind="dark")
d.box(230, 20, 220, 70, "Exception cards", ["supply gap, large send, swing,", "overstock, data issue"], kind="accent")
d.box(490, 20, 200, 70, "Planner decides", ["approve · change qty · reject", "reason required on change"])
d.box(730, 20, 200, 70, "Line manager", ["cfg_line_manager", "approves the change"])
d.box(970, 20, 200, 70, "Final plan", ["view al_final", "SAP push: to do"], kind="dash")
d.arrow([(190, 55), (228, 55)]); d.arrow([(450, 55), (488, 55)]); d.arrow([(690, 55), (728, 55)]); d.text(709, 12, "changed / rejected", 10.5, anchor="middle"); d.arrow([(930, 55), (968, 55)])
d.arrow([(590, 90), (590, 150), (1070, 150), (1070, 92)], "approved as planned", 760, 144)
d.box(230, 190, 220, 62, "Guardrails", ["cfg_guardrails (versioned)", "what counts as an exception"], url=tbl("cfg_guardrails"))
d.box(490, 190, 200, 62, "Decisions", ["al_decisions, ex_actions", "reason kept for learning"], url=tbl("al_decisions"))
d.box(730, 190, 200, 62, "Learning", ["reasons feed model review", "(28-day window)"])
d.arrow([(340, 190), (340, 92)]); d.arrow([(690, 221), (728, 221)])
slide("Daily brief and sign-off flow", d.svg() + note("Shadow mode today: routine lines are released only through 'Release N lines'; directory, Teams notifications and the SAP push are open items (TD-9)."),
      kicker="Agents handle the routine; people decide the exceptions, and their reasons are kept")

slide("App deployment and configuration", cols(f"""<h3>Deploy (dev app)</h3><pre>rsync build/app/ → scratch/devstage
cp build/deploy/app.dev.yaml devstage/app.yaml
databricks workspace import-dir devstage \\
  /Workspace/Users/chidambaram.s@gmg.com/nike-consolidation-app-dev \\
  --overwrite -p gmg-sso
databricks apps deploy nike-consolidation-dev \\
  --source-code-path /Workspace/.../nike-consolidation-app-dev -p gmg-sso</pre>
<p class="small">Live app: same, folder <code>nike-consolidation-app</code>, app.yaml without APP_MODE.</p>""", table(["Setting", "Live", "Dev"], [
    ["APP_MODE", "—", "dev (read-only)"], ["NIKE_JOB_ID", "553755400746008", "553755400746008"], ["DATABRICKS_WAREHOUSE_ID", "1c9706d9594f1f2f", "1c9706d9594f1f2f"],
    ["ASK_SPACE_ID", "—", "01f1d371857d11438cd5701cb35f9a2c"], ["User scopes", "sql", "sql, dashboards.genie"],
    ["Service principal", "4a734335-ac1e-4760-9a3a-9335f2f7e9ba", "388f7a00-26eb-4ec8-91c2-f42e318ee769"],
    ["Job permission", "Can manage run", "none"], ["Streamlit", "1.66.1", "1.66.1"]], cls="t sm", widths=["34%", "33%", "33%"]) +
    note("Reads run on-behalf-of the viewer (x-forwarded-access-token → SQL Statement API), so the apps need no data grants of their own. A stale browser session shows 'app not running': reopen the tab to sign in again.", "amber"),
    ratio="1fr 1.1fr"), kicker="Source in the workspace, one CLI deploy, viewer-scoped reads")
