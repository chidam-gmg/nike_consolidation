# Part 2: Genie agents and skills, Genie One skills, Claude + MCP, artifacts, operations, appendices.

# ======================= GENIE AGENTS =======================
section("Genie agents and skills")
d = D(1176, 460)
d.box(400, 0, 376, 50, "A question in plain words", ["app Ask box, Genie, Genie One or Claude"])
d.box(380, 76, 416, 56, "Supervisor (Agent Bricks)", ["routes to the right agent: UI step, API not exposed"], kind="dash")
d.arrow([(588, 50), (588, 74)])
ag = [("Pipeline & Steps", ["steps, rules, tables,", "timings, failures, codes"], "pipeline", "accent"),
      ("Store Consolidation Steps", ["steps 01-08: why a store", "moved, held or kept stock"], "consolidation", "accent"),
      ("DC Replenishment Steps", ["steps 10-12: demand,", "rationing, top-ups"], "replenishment", "accent"),
      ("Merchandising", ["results and trends", "across runs (existing)"], "merch", "")]
for i, (t, ls, k, kind) in enumerate(ag):
    x = 10 + i * 292
    d.box(x, 168, 270, 72, t, ls, kind=kind, url=genie(SP[k]))
    d.arrow([(588, 132), (588, 150), (x + 135, 150), (x + 135, 166)])
d.group(10, 266, 1156, 76, "Skills: Unity Catalog SQL functions")
for i, (f, w) in enumerate([("agent_trace_line", 170), ("agent_step_info", 160), ("agent_table_lineage", 190), ("agent_step_timings", 180), ("agent_explain_code", 180), ("agent_review_actions_for", 220)]):
    x = 24 + sum([170, 160, 190, 180, 180, 220][:i]) + i * 12
    d.box(x, 292, w, 36, f, [], url=fn(f), tsize=12)
for i in range(3):
    d.arrow([(145 + i * 292, 240), (145 + i * 292, 264)])
d.group(10, 362, 1156, 92, "Data: sports_analytics_test.nike_consolidation")
for i, (t, s, u) in enumerate([("4 knowledge tables", "steps, tables, glossary, run history", "agent_pipeline_steps"),
                               ("7 step views", "latest run, steps 01-11", "agent_step02_stock"),
                               ("Results + history", "unchanged job tables", "recommendations_history"),
                               ("merch_ views", "curated for the results agent", "merch_runs")]):
    d.box(24 + i * 286, 388, 270, 56, t, [s], url=tbl(u))
d.arrow([(588, 342), (588, 360)])
slide("Genie agent architecture", d.svg(), kicker="Four agents, six skills, one schema; the supervisor bundles them once created in the UI")

slide("The four agents", table(["Agent (Genie space)", "Answers", "Data", "Skills"], [
    [A(genie(SP["pipeline"]), "Pipeline &amp; Steps") + "<br><code>" + SP["pipeline"] + "</code>", "What each step does, rules, settings, which tables it reads and writes, run and step timings, failures, who ran what, any code",
     "agent_pipeline_steps, agent_pipeline_tables, agent_glossary, agent_step_runs, agent_current_run, merch_runs", "step_info, table_lineage, step_timings, explain_code"],
    [A(genie(SP["consolidation"]), "Store Consolidation Steps") + "<br><code>" + SP["consolidation"] + "</code>", "Steps 01-08 for store moves; why a store and article moved, was held or stayed; funnels by step",
     "agent_current_run, agent_step02_stock, agent_step03_pools, agent_step03_guards, agent_step05_matches, agent_step08_receiver_guard, recommendations, held, not_moved", "trace_line, step_info, explain_code"],
    [A(genie(SP["replenishment"]), "DC Replenishment Steps") + "<br><code>" + SP["replenishment"] + "</code>", "Steps 10-12 + 08: demand, need, rationing between 2D22 / 3D31 / hub, top-ups; why a store did or did not get stock",
     "agent_current_run, agent_step11_replen, agent_step08_receiver_guard, fullsize_topup, replen_recommendations, replen_not_replenished, dc_pool_stock", "trace_line, step_info, explain_code"],
    [A(genie(SP["merch"]), "Merchandising") + "<br><code>" + SP["merch"] + "</code>", "Results and trends: moves, held and kept stock, DC lines, review status, history across runs",
     "merch_runs, merch_store_stock, merch_dc_replenishment", "none"],
], widths=["22%", "30%", "34%", "14%"], cls="t sm") + note("Shared ground rules in every agent: name the run an answer comes from (agent_current_run), use agent_trace_line for any 'why' question, match stores by code or name and styles by code. Built by " + A(REPO + "/tree/dev-agent-tab/pipeline/genie/agents", "pipeline/genie/agents") + " (spaces.py, build_agents.py)."),
      kicker="Warehouse Genie_SQL_Warehouse; instructions, sample questions and tested example SQL in each")

slide("Skills: Unity Catalog SQL functions", table(["Skill", "Inputs", "Returns", "Used by"], [
    [A(fn("agent_trace_line"), "<code>agent_trace_line</code>"), "store (code or name words), article (size-level or style code), size (optional)", "One row per step: stock + label, pools, guards, match + size-run check, joint guard, DC demand + ration, final store and DC results", "Consolidation, Replenishment, supervisor"],
    [A(fn("agent_step_info"), "<code>agent_step_info</code>"), "step number, task name, a word ('rationing') or all", "Purpose, rules, settings, reads, writes, typical duration, notebook link", "All step agents"],
    [A(fn("agent_table_lineage"), "<code>agent_table_lineage</code>"), "table name or part", "Layer, grain, refresh, written by, read by, Catalog Explorer link", "Pipeline"],
    [A(fn("agent_step_timings"), "<code>agent_step_timings</code>"), "step or all, last N runs", "Start, who ran it, scope, seconds, result, run link", "Pipeline"],
    [A(fn("agent_explain_code"), "<code>agent_explain_code</code>"), "any code (HOLD_RECV_CUT, NO_DC_STOCK ...)", "Meaning and the step that sets it", "All step agents"],
    [A(fn("agent_review_actions_for"), "<code>agent_review_actions_for</code>"), "'latest' or a run ID", "Every action needing human review, pre-filled for the CSV (decision, comment, line key)", "Review e-mail skill"],
], widths=["21%", "24%", "39%", "16%"], cls="t sm") + note("Unity Catalog functions are governed like tables (EXECUTE grant), callable from Genie, Agent Bricks, the managed MCP endpoint " + C("/api/2.0/mcp/functions/sports_analytics_test/nike_consolidation") + " and plain SQL. Genie passes optional arguments as the text 'NULL'; the skills treat it as empty."),
      kicker="Deterministic SQL the agents call instead of guessing joins")

slide("agent_trace_line, worked example", table(["Step", "Outcome", "Detail"], [
    ["00 Run", "Run 20261105T063020Z-7f589e by scheduled", "scope ALL / ALL, sales through 4 Nov, stock as of 5 Nov"],
    ["01-02 Stock and classification", "DEAD", "ATP 2, in transit 0, effective 2; sold 0 in window, cover n/a"],
    ["03 Donor / receiver pools", "Donor", "Donor: 2 units"],
    ["03 Guards", "Guard applies", "Move-back <30d: moved out on 2 Nov"],
    ["04-05 Matching and size-run check", "Matched", "gives 2 to 1110 Nike Rise TDM [DONOR_ALREADY_CUT; Held - receiver stays cut-size]"],
    ["10-11 DC demand and rationing", "No DC need", "store has enough stock for 14 days of demand and display"],
    ["08 Joint receiver guard", "Not a receiver of this style", "—"],
    ["06 Result: store to store", "Held: Receiver stays cut-size", "2 units would go to 1110 Nike Rise TDM"],
    ["12 Result: DC to store", "No DC line", "—"],
], widths=["28%", "28%", "44%"], cls="t sm") + f"""<pre>SELECT step_no, step, outcome, detail
FROM {FQ}.agent_trace_line('1009', 'NKII7405-10109') ORDER BY seq</pre>""",
      kicker="Store 1009 (Nike MOE), article NKII7405-10109: why it was not moved, in one call")

slide("Knowledge tables and step views", table(["Object", "Type", "Holds", "Refresh"], [
    [T("agent_pipeline_steps"), "table · 13", "Each step: purpose, rules, settings, reads, writes, dependencies, duration, notebook link", "Rebuild when logic changes"],
    [T("agent_pipeline_tables"), "table · 56", "Every source, config, step, result and history table with lineage", "Rebuild when tables change"],
    [T("agent_glossary"), "table · 38", "Every code and label the job writes", "Rebuild when codes change"],
    [T("agent_step_runs"), "table · 782", "Last 60 runs x 13 steps: start, duration, result, who, scope", "Snapshot: build_agents.py runs"],
    [T("agent_current_run"), "view", "Run the step tables hold: ID, who, scope, dates, settings", "Live"],
    [T("agent_step02_stock"), "view", "Steps 01-02 per store x size", "Live (latest run)"],
    [T("agent_step03_pools") + " " + T("agent_step03_guards"), "views", "Step 03 donors, receivers, guards", "Live (latest run)"],
    [T("agent_step05_matches"), "view", "Steps 04, 05, 08 per matched move", "Live (latest run)"],
    [T("agent_step08_receiver_guard"), "view", "Step 08 per receiving store x style", "Live (latest run)"],
    [T("agent_step11_replen"), "view", "Steps 10, 11, 08 per Nike store x size", "Live (latest run)"],
    [T("agent_review_actions"), "view", "Review list of the last completed run", "Live (history)"],
], widths=["34%", "11%", "37%", "18%"], cls="t sm"), kicker="Comments on every column, so Genie reads them correctly; the job's own tables are untouched")

slide("Agents tested on live data", table(["Question", "Agent", "Answer (6 Nov 2026)"], [
    ["Explain in simple words what happens in the rationing step", "Pipeline", "Step 11: own DCs for KSA and Bahrain first, then the UAE hub by grade, demand, value at risk"],
    ["Which step was slowest in yesterday's 10:30 run?", "Pipeline", "Step 08 receiver guard, 95 s"],
    ["Did any run fail in the last month, and what does the joint guard check?", "Pipeline", "No failures; step 08 rules with notebook link"],
    ["Why was NKII7405-10109 at store 1009 not moved?", "Consolidation", "DEAD, donor of 2, matched to 1110, held: receiver stays cut-size"],
    ["How many dead lines, and how many become moves?", "Consolidation", "155,541 dead lines; 1,562 recommended moves"],
    ["Walk me through NKIF1911-010 size S at store 1172", "Consolidation", "No stock; receiver becomes full-size; DC rations 1; replenish 1"],
    ["Why didn't Dubai Mall Beacon get NKHM5973-30005 from the DC?", "Replenishment", "Needed 1; NO_DC_STOCK; store stays cut-size"],
    ["How much need could the DC not fill for lack of stock?", "Replenishment", "29,841 units, 27,377 lines, AED 126.6M at risk"],
], widths=["40%", "15%", "45%"], cls="t sm") + note("10 of 10 test questions answered correctly in 8-28 seconds through the Genie conversation API (" + C("pipeline/genie/agents/ask.py") + "). Two skill fixes were made during testing: the size argument passed as 'NULL', and store names in any word order.", "ok"),
      kicker="Questions worded differently from the agents' own examples")

d = D(1176, 250)
d.box(0, 20, 250, 64, "Option A (ready now)", ["Ask panel per app tab, pointed", "at that tab's Genie agent"], kind="ok")
d.box(0, 110, 250, 64, "Option B (after supervisor)", ["one Ask box → supervisor", "serving endpoint"], kind="dash")
d.box(300, 20, 270, 154, "Agent Bricks supervisor", ["UI: Agents → Multi-Agent Supervisor", "add the 4 Genie spaces with", "routing descriptions", "add agent_trace_line as a tool", "deploy → serving endpoint"], kind="dash")
d.box(620, 20, 270, 154, "Needs", ["Agent Bricks enabled (admin)", "API /multi-agent-supervisors", "not exposed in this workspace", "app scope for serving endpoints", "spaces shared with users"])
d.box(940, 20, 236, 154, "App today", ["dev: ask_agents() in", "databricks_io.py, Genie", "conversation API as viewer", "live: needs dashboards.genie", "scope + redeploy"])
d.arrow([(250, 52), (298, 52)]); d.arrow([(250, 142), (298, 142)]); d.arrow([(570, 97), (618, 97)]); d.arrow([(890, 97), (938, 97)])
slide("Bundling: supervisor and the app's Ask panel", d.svg() + note("Supervisor steps and routing descriptions are in the doc " + A(ART["genie_doc"], "Nike Stock Movement Genie Agents") + ". Until it exists, Option A gives each tab its own expert agent with no new infrastructure."),
      kicker="Agents are ready; bundling is a UI step, the app wiring a small change")

# ======================= GENIE ONE SKILLS =======================
section("Genie One skills")
slide("Skill 1: run-nike-stock-movement", cols(f"""<h3>What it does</h3>{bullets([
  "Runs the engine with default or changed settings, from a conversation.",
  "<b>Always asks first</b>: shows the ten settings with defaults, asks what to change, validates, shows the final table, asks 'Run with these settings?'.",
  "Validates like step 00: whole numbers 1-365, protect cover &lt; target cover, ISO country codes, the 20 business categories, dates not in the future.",
  "Warns: about 8 minutes, one run at a time, a scoped run replaces the app's latest view until the next full run.",
  "Starts with <code>POST /api/2.2/jobs/run-now</code>, follows <code>runs/get</code> every 30 s, summarises from the history tables.",
  "No jobs tool or permission → gives the job link and the settings to run by hand.",
])}""", """<h3>Flow</h3><ol class="steps">
<li><div>User: "run the stock movement"</div></li><li><div>Skill shows defaults, asks what to change</div></li>
<li><div>User changes (e.g. COUNTRY = AE, TARGET 21)</div></li><li><div>Skill validates, shows final table, asks to confirm</div></li>
<li><div>run-now with RUN_ID + TRIGGERED_BY</div></li><li><div>Progress by step, then results vs last scheduled run</div></li></ol>""" +
    note("File: " + A(REPO + "/blob/dev-agent-tab/pipeline/genie/agents/run_stock_movement_SKILL.md", "run_stock_movement_SKILL.md") + " · paste into Genie One as a skill.")),
      kicker="A confirmed, parameterised run, never a surprise run")

d = D(1176, 230)
steps = [("Ask", ["run, lines,", "recipients"]), ("Check run", ["finished?", "job not active"]), ("Build CSV", ["agent_review_", "actions_for(run)"]),
         ("Summary", ["by action, reason,", "top stores"]), ("Show + confirm", ["subject, people,", "counts, file"]), ("Send", ["only on a yes;", "else ready to paste"])]
for i, (t, ls) in enumerate(steps):
    x = i * 196
    d.box(x, 20, 180, 66, t, ls, kind="accent" if t in ("Build CSV",) else ("ok" if t == "Send" else ""))
    if i < 5:
        d.arrow([(x + 180, 53), (x + 194, 53)])
d.text(0, 120, "Today: 790 actions in the 6 Nov run (744 DC lines: 713 full-size top-ups; 46 store moves over AED 1,000 or 6 units), all pending.", 12.5, "var(--ink)")
d.text(0, 142, "Same lines as the app's Approvals queue, read from *_history so a run in progress is never half-read; reconciled 790 = 790.", 12.5, "var(--ink)")
d.text(0, 170, "Two variants: Genie One skill (SQL + its e-mail tool) and a Claude Code skill (OneDrive upload + Outlook draft via Microsoft 365).", 12.5)
slide("Skill 2: nike-review-email", d.svg() + table(["Variant", "Where", "Delivery"], [
    ["Genie One skill", A(REPO + "/tree/dev-agent-tab/pipeline/review_email", "pipeline/review_email") + " (paste-in markdown)", "Genie One's e-mail tool if present; otherwise subject + body + CSV handed to the user"],
    ["Claude Code skill", "<code>.claude/skills/nike-review-email/SKILL.md</code> + <code>build_review_pack.py</code>", "CSV to OneDrive folder 'Nike stock movement reviews' (shared once), Outlook draft, send on yes"],
], widths=["18%", "42%", "40%"], cls="t sm"), kicker="The exhaustive list of actions needing a person, as a pre-filled CSV")

slide("Review CSV: data contract", table(["Column", "Meaning", "Column", "Meaning"], [
    ["Action", "Store move or DC line", "To / To name", "Receiving store"],
    ["Why review", "Full-size top-up · High value (≥ AED 1,000) · Large qty (≥ 6)", "Qty", "Units to send"],
    ["Country, Category", "ISO code, business category", "Value (AED)", "Cost (store moves) / retail (DC lines)"],
    ["Style, Style name", "Generic article", "Engine reason", "Why the engine proposes it"],
    ["Article, Size", "Size-level article", "Current status", "Pending / Approved / Rejected (app)"],
    ["From / From name", "Giving store or DC pool", "Decision (Approve / Reject)", "Filled by the reviewer"],
    ["Comment", "Reviewer note", "Line key (do not edit)", "from|to|article: joins back to review_decisions"],
    ["Run ID", "Engine run", "", ""],
], widths=["17%", "33%", "20%", "30%"], cls="t sm") + note("Source: " + A(fn("agent_review_actions_for"), "agent_review_actions_for(run_ref)") + " (default 'latest' completed run). Rules match the app: store moves ≥ AED 1,000 or ≥ 6 units; DC lines that are full-size top-ups or ≥ AED 1,000 or ≥ 6 units. Importing a returned CSV into review_decisions is the next build."),
      kicker="Twenty columns; the line key makes the round trip possible")

# ======================= CLAUDE + MCP =======================
section("Claude and MCP integration")
d = D(1176, 430)
d.group(0, 0, 250, 390, "Clients")
d.box(10, 30, 230, 62, "Claude Desktop", ["macOS app, same account", "connectors as claude.ai"], kind="accent")
d.box(10, 104, 230, 62, "claude.ai (web)", ["Settings → Connectors", "custom connector per server"])
d.box(10, 178, 230, 62, "Claude Code (CLI)", ["builder: Databricks CLI", "profile gmg-sso (SSO)"])
d.box(10, 252, 230, 62, "Genie One", ["inside Databricks, runs as the", "user; pasted SKILL.md skills"])
d.group(290, 0, 250, 390, "Auth")
d.box(300, 60, 230, 90, "Databricks OAuth", ["each user signs in once", "token on behalf of the user", "no shared PAT"], kind="ok")
d.box(300, 200, 230, 62, "OAuth U2M (CLI)", ["databricks auth login", "-p gmg-sso"])
d.group(580, 0, 300, 390, "Databricks MCP endpoints")
d.box(590, 30, 280, 74, "Managed Genie MCP", ["/api/2.0/mcp/genie/{space_id}", "tools: query_space_{id},", "poll_response_{id}"], kind="accent")
d.box(590, 116, 280, 74, "Managed UC functions MCP", ["/api/2.0/mcp/functions/", "{catalog}/{schema}", "every function = a tool"])
d.box(590, 202, 280, 74, "Custom MCP (Databricks App)", ["farmfresh-mcp …/mcp", "FastMCP, user token"])
d.box(590, 288, 280, 60, "Workspace APIs", ["Jobs, SQL, Genie, Apps (CLI)"])
d.group(920, 0, 256, 390, "Executes as the user")
d.box(930, 30, 236, 62, "Genie space", ["instructions + example SQL"], url=genie(SP["cxo"]))
d.box(930, 104, 236, 62, "SQL warehouse", ["Genie_SQL_Warehouse"], url=WH)
d.box(930, 178, 236, 62, "Unity Catalog", ["user's own grants, RLS"], kind="accent")
for y in (61, 135):
    d.arrow([(240, y), (298, 100)])
d.arrow([(240, 209), (298, 231)])
d.arrow([(530, 105), (588, 67)]); d.arrow([(530, 105), (588, 153)]); d.arrow([(530, 105), (588, 239)]); d.arrow([(530, 231), (588, 318)])
d.arrow([(870, 67), (928, 61)]); d.arrow([(1048, 92), (1048, 102)]); d.arrow([(1048, 166), (1048, 176)]); d.arrow([(870, 153), (928, 140)])
d.text(0, 418, "Claude never holds a Databricks password or token of its own: every MCP call carries the signed-in person's OAuth token, so Unity Catalog permissions apply unchanged.", 11.5)
slide("How Claude connects to Databricks", d.svg(), kicker="Claude Desktop and claude.ai reach Genie through Databricks managed MCP, signed in per user with OAuth")

slide("Connectors in place", table(["Connector in Claude", "Type", "Endpoint", "Gives Claude"], [
    ["Databricks_gmg_sports", "Managed Genie MCP", C(MCP_GENIE(SP["cxo"])), "GMG_SPORTS_CXO Genie space: Sports sales, budget, traffic, inventory (" + A(genie(SP["cxo"]), "open space") + ")"],
    ["Databricks_gmg_EDG", "Managed Genie MCP", C(H + "/api/2.0/mcp/genie/<EDG space id>"), "GMG_EDG_CXO: supermarket / hypermarket"],
    ["Databricks_gmg_H&amp;B", "Managed Genie MCP", C(H + "/api/2.0/mcp/genie/<HNB space id>"), "GMG_HNB_CXO: pharmacy, health, beauty"],
    ["Databricks_Farmfresh_mcp", "Custom MCP (Databricks App)", C("https://farmfresh-mcp-8517174019523704.aws.databricksapps.com/mcp"), "FarmFresh food sales, margin, budget tools"],
    ["Claude Docs", "claude.ai connector", "—", "Living docs (the Genie agents doc)"],
    ["Microsoft 365", "claude.ai connector", "—", "Outlook drafts, OneDrive upload (review e-mail)"],
], widths=["19%", "17%", "38%", "26%"], cls="t sm") + note("How they were added (same for each Genie connector): claude.ai → Settings → Connectors → <b>Add custom connector</b> → name + the MCP URL → <b>Connect</b> → Databricks OAuth sign-in. Claude Desktop uses the same account-level connectors, so nothing is configured in claude_desktop_config.json. The routing rules in the organisation instructions (Sports → GMG_SPORTS_CXO, EDG, HNB) decide which connector Claude calls."),
      kicker="Four Databricks connectors and two productivity connectors on this account")

slide("Connect a Genie space to Claude Desktop, step by step", cols("""<ol class="steps">
<li><div><b>Check</b> the space opens in Genie. Managed MCP is already on in this workspace (the Sports connector uses it).</div></li>
<li><div><b>Grant</b> each user: Genie space Can run, warehouse Can use, SELECT on its tables (and EXECUTE on functions for the functions endpoint).</div></li>
<li><div><b>Build the URL</b>: <code>https://dbc-74c28e5e-272e.cloud.databricks.com/api/2.0/mcp/genie/&lt;space_id&gt;</code></div></li>
<li><div><b>Add the connector</b>: claude.ai or Claude Desktop → Settings → Connectors → Add custom connector → paste the URL, name it (e.g. Nike_stock_movement_steps).</div></li>
<li><div><b>Connect</b>: Databricks OAuth sign-in once per user; tools <code>query_space_&lt;id&gt;</code> and <code>poll_response_&lt;id&gt;</code> appear.</div></li>
<li><div><b>Test</b> in a new chat: "Why was NKII7405-10109 at store 1009 not moved?" and compare with the agent in Genie.</div></li>
</ol>""", table(["To connect next (built, not yet added)", "MCP URL"], [
    ["Pipeline &amp; Steps", C(MCP_GENIE(SP["pipeline"]))],
    ["Store Consolidation Steps", C(MCP_GENIE(SP["consolidation"]))],
    ["DC Replenishment Steps", C(MCP_GENIE(SP["replenishment"]))],
    ["Merchandising", C(MCP_GENIE(SP["merch"]))],
    ["Skills (all agent_ functions)", C(MCP_FN)],
], cls="t xs", widths=["32%", "68%"]) + note("The functions endpoint exposes every function in the schema as a tool, so Claude can call agent_trace_line or agent_review_actions_for directly.", "amber"), ratio="1fr 1.05fr"),
      kicker="Six steps; the new Nike agents are one connector each")

slide("Claude Code: how the resources were built", cols(table(["Piece", "Detail"], [
    ["Identity", "Databricks CLI v0.270 at ~/.local/bin, profile <code>gmg-sso</code>, OAuth U2M SSO (" + C("databricks auth login -p gmg-sso") + ")"],
    ["SQL", "Statement Execution API through build/app/databricks_io.py (same code the apps use)"],
    ["Jobs", "jobs submit for notebooks 20 / 21 / 22; run-now by the app"],
    ["Genie", "Genie spaces API: POST/PATCH /api/2.0/genie/spaces (serialized_space v2: tables, instructions, example SQL, sql_functions)"],
    ["Apps", "workspace import-dir + apps deploy"],
    ["Docs + artifacts", "Claude Docs connector, claude.ai artifacts"],
    ["Microsoft 365", "Outlook draft / send, OneDrive upload (review e-mail skill)"],
    ["Guardrails", "Production job, notebooks and tables never edited by the agent builders; new jobs, OneDrive writes and sends need explicit approval"],
], cls="t sm", widths=["22%", "78%"]), table(["Builder", "Command"], [
    ["Agents: all", C("python3 pipeline/genie/agents/build_agents.py all")],
    ["Knowledge + views", C("build_agents.py tables")],
    ["Step run history", C("build_agents.py runs")],
    ["Skills", C("build_agents.py functions")],
    ["Genie spaces", C("build_agents.py spaces")],
    ["Ask an agent", C("ask.py consolidation \"...\"")],
    ["Review pack", C("pipeline/review_email/build_review_pack.py")],
    ["New launches", C("pipeline/newlaunch/build_notebook.py")],
], cls="t xs", widths=["30%", "70%"])), kicker="Everything is reproducible from the repo with one login")

# ======================= ARTIFACTS, CODE, OPS =======================
section("Artifacts, code and operations")
slide("Published artifacts and docs", table(["Artifact", "Type", "Use"], [
    [A(ART["genie_doc"], "Nike Stock Movement Genie Agents"), "Claude Doc", "What was created for Genie: agents, skills, tests, bundling, open items"],
    [A(ART["sysmap"], "Stock Movement System Map"), "Page", "Live app connections with links; the Run sequence timed from a real run"],
    [A(ART["handover"], "Nike Stock Movement Handover"), "Page", "Jobs: where written, where scheduled, how to run"],
    [A(ART["brief"], "Planner Morning Brief"), "Page", "Mock-up of the exception-management brief for the CTO"],
    [A(ART["agentlab"], "Agent Lab Concepts"), "Page", "Early agentic UI concepts (Agent lab, since retired)"],
    [A(ART["lifecycle"], "Merchandising Lifecycle"), "Page", "Business process vs data, for the merchandising teams"],
    [A(REPO + "/blob/dev-agent-tab/TECH_DEBT.md", "TECH_DEBT.md"), "Repo doc", "TD-1 to TD-10: open design items and decisions"],
    ["This deck", "PDF", "docs/solution_deck/nike_stock_movement_solution_architecture.pdf"],
], widths=["32%", "12%", "56%"]), kicker="Share from each artifact's Share menu; they are private by default")

slide("Repository map", cols(table(["Folder", "Contents"], [
    [C("pipeline/notebooks/"), "The 13 engine notebooks (00-13)"],
    [C("pipeline/job/"), "Job definition (nike_stock_movement_pipeline.json)"],
    [C("pipeline/forecast/"), "fc_engine, fc_sql, _driver → notebook 20"],
    [C("pipeline/newlaunch/"), "nl_engine, nl_pack, nl_sql, drivers → notebooks 21, 22; image_attributes/"],
    [C("pipeline/genie/"), "merch views + space; agents/ (knowledge, spaces, builders, ask, run skill)"],
    [C("pipeline/review_email/"), "review view/function SQL, pack builder, skill"],
    [C("build/app/"), "Streamlit app: app.py, databricks_io.py, views (brief, monitor, forecast, launches, story)"],
    [C("build/deploy/"), "app.dev.yaml (dev app config)"],
    [C("docs/solution_deck/"), "This deck: builder, styles, content"],
], cls="t sm", widths=["30%", "70%"]), f"""<h3>{A(REPO, "chidam-gmg/nike_consolidation")}</h3>{bullets([
  "Private, SSH (ed25519) as chidam-gmg; working branch <code>dev-agent-tab</code>.",
  "Live app code snapshot on main (29 Oct 2026); dev work since on the branch.",
  "No secrets in the repo: OAuth for people, service principals for apps, CLI SSO for builders.",
  "Work since the last commit (b0b4d7d) is local: monitor, opening pack, Genie agents, review e-mail, this deck.",
])}""", ratio="1.2fr 1fr"), kicker="One repo for pipeline, apps, agents and docs")

slide("Runbook", table(["Task", "How", "When"], [
    ["Daily run", "Automatic 10:30 GST; check the dev app's Live monitor or " + A(JOB, "the job page"), "Daily"],
    ["Extra run with settings", "Live app ▶ Run, or the Genie One skill run-nike-stock-movement", "On demand"],
    ["Review actions", "App Approvals, or the review e-mail skill (CSV)", "After each run"],
    ["Refresh forecast", "jobs submit forecast/20_core_forecast_alloc", "Weekly / on demand"],
    ["Refresh launches + packs", "newlaunch/21 then 22 (22 alone when the buy file lands)", "On demand"],
    ["Refresh agent run history", "build_agents.py runs", "Weekly"],
    ["Rebuild agents after logic change", "edit knowledge.py / spaces.py → build_agents.py tables / spaces", "On change"],
    ["Redeploy an app", "workspace import-dir + apps deploy (slide: App deployment)", "On change"],
    ["'App not running' in browser", "Session expired: reopen the tab and sign in", "As needed"],
    ["CLI token expired", "databricks auth login -p gmg-sso", "As needed"],
    ["Store list wrong after upload", "App: Reset store list to baseline", "As needed"],
], widths=["26%", "56%", "18%"], cls="t sm"), kicker="Day-to-day operation and recovery")

slide("Open items and roadmap", table(["Item", "Why it matters", "Next step", "Owner"], [
    ["Share Genie agents", "Only the owner can use them", "Can run on 3 spaces + grants for pilot planners", "Owner"],
    ["Supervisor (Agent Bricks)", "One entry point for all agents", "Create in UI; deploy endpoint", "Owner + admin"],
    ["App Ask panel", "Agents inside the app", "Option A now; B after supervisor", "Build"],
    ["Connect new agents to Claude", "Ask in Claude Desktop", "4 Genie connectors + functions MCP", "Owner"],
    ["Step run history freshness", "agent_step_runs is a snapshot", "Add as last job task (needs approval)", "Owner"],
    ["Named jobs for forecast / launches", "Visible on Live monitor", "Create jobs (approval) + grant app SP view", "Owner"],
    ["Review CSV import", "Close the e-mail loop", "Load Decision by line key into review_decisions", "Build"],
    ["SAP order push", "Actions reach SAP", "SAP API (BAPI / OData / IDoc) via Integration Suite + MCP tool with approvals", "SAP team"],
    ["Store universe UAE only", "Other GCC stores not in the engine", "Add stores to cfg_store_grade", "Business"],
    ["Buy file + launch dates", "Opening packs are provisional", "Load nl_buy; refresh order book", "Buying"],
], widths=["22%", "24%", "40%", "14%"], cls="t sm"), kicker="What is built but not rolled out, and what is designed but not built")

# ======================= APPENDICES =======================
section("Appendix")
slide("Appendix A: identifiers", cols(table(["Object", "ID"], [
    ["Workspace", "8517174019523704"], ["Account", "112156b7-5693-4274-8ebc-5efc0a998f4f"], ["Warehouse", "1c9706d9594f1f2f"],
    ["Engine job", "553755400746008"], ["Autostart / autostop", "1086461525973252 / 596330768678080"],
    ["Replenishment P1 (legacy)", "353261038085683"], ["Consolidation (legacy)", "756802809605811"],
    ["Live app SP", "4a734335-ac1e-4760-9a3a-9335f2f7e9ba"], ["Dev app SP", "388f7a00-26eb-4ec8-91c2-f42e318ee769"],
    ["Pipeline folder", "2208784873396674"], ["App folder", "2208784873396693"],
], cls="t xs", widths=["45%", "55%"]), table(["Genie space / notebook", "ID"], [
    ["Pipeline & Steps", SP["pipeline"]], ["Store Consolidation Steps", SP["consolidation"]], ["DC Replenishment Steps", SP["replenishment"]],
    ["Merchandising", SP["merch"]], ["GMG_SPORTS_CXO", SP["cxo"]]] + [[k, str(v)] for k, v in NBID.items()], cls="t xs", widths=["55%", "45%"])),
      kicker="For support tickets and API calls")

tabs = json.load(open(HERE / "deck_tables.json"))
half = (len(tabs) + 3) // 4
cols4 = [tabs[i * half:(i + 1) * half] for i in range(4)]
slide("Appendix B: every table and view in the schema", '<div class="grid4">' + "".join(
    '<div class="small" style="display:grid;gap:1px">' + "".join(f'<a href="{e(tbl(t))}" style="font-family:var(--f-mono);font-size:10.4px">{e(t)}</a>' for t in c) + "</div>" for c in cols4) + "</div>",
      kicker=f"{len(tabs)} objects in sports_analytics_test.nike_consolidation; each name opens it in Catalog Explorer")

slide("Appendix C: codes the job writes", table(["Field", "Code", "Meaning", "Step"], [
    ["classification", "DEAD / SLOW_MOVING / HEALTHY / NEW_TO_STORE", "No sales / cover > 30 d / within cover / received < 14 d", "02"],
    ["demand_type", "IN_STOCK / OOS_SELLING / APLUS_CURVE", "Below target cover / sold but out of stock / A+ missing display size", "03"],
    ["reshape_action", "FULL_RETAINED / DONOR_ALREADY_CUT / RESHAPED_FULL_RUN / NO_SIZE_DEF", "Donor stays full / was cut / run released to faster store / no size rule", "05"],
    ["recv_action", "ALLOW_RECV_* / TOPUP_TO_FULL / HOLD_RECV_CUT", "Receiver full / DC top-up (review) / hold everything into it", "08"],
    ["held_reason", "Size-curve break, Season-freshness, APO, Receiver stays cut-size, Move-back <30d", "Rule that held a would-be move", "03-08"],
    ["not_moved_reason", "no receiving store needs this size / retained / new to store", "Why stock stays", "06"],
    ["replen reason", "REPLEN_DEPLETION / REPLEN_DISPLAY / REPLEN_FULL_SIZE", "Sold below cover / 1 per display size / top-up", "12"],
    ["not_replenished_reason", "NO_DC_STOCK / DC_SHORTFALL_OR_BLOCKED / HELD_RECEIVER_STAYS_CUT / MOVE_BACK_30D", "Why a store need was not filled", "12"],
    ["DC pool", "2D22 / 3D31 / HUB (1D00 + 1D01)", "KSA / Bahrain / UAE hub serving UAE, KW, OM, QA", "11"],
    ["grade", "BEACON, JORDAN, A+, A, B, C, NVS", "Priority order; NVS = Nike Value Store (outlet)", "11"],
], widths=["17%", "36%", "39%", "8%"], cls="t xs") + note("Full list with meanings: " + T("agent_glossary") + " · ask any step agent 'what does &lt;code&gt; mean?'"),
      kicker="The vocabulary every agent, view and CSV uses")

slide("Thank you", f"""<div style="display:grid;gap:14px;max-width:960px">
<div class="divider-big" style="font:600 38px/1.15 var(--f-disp)">A recommend-only engine people can question in plain language.</div>
<p style="font-size:17px;color:var(--dim)">Start from the {A(APP_LIVE, "live app")}, the {A(APP_DEV, "dev app's Live monitor")}, or ask the {A(genie(SP["consolidation"]), "Store Consolidation Steps agent")} why any store moved stock. The design reference for every piece is this deck; the build scripts are in {A(REPO, "the repo")}.</p>
{kpis([("13", "job steps"), ("2", "apps"), ("4", "Genie agents"), ("6", "SQL skills"), ("2", "Genie One skills"), ("118", "schema objects")])}
</div>""", kicker="Contact: chidambaram.s@gmg.com · GMG Sports analytics")
