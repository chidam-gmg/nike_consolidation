"""Genie space for merchandising over the stock movement engine's results (3 curated views only).
Builds the create-space payload; `--create` posts it (prints space_id), `--update <space_id>` patches it."""
import json, subprocess, sys, uuid, os, tempfile

FQ = "sports_analytics_test.nike_consolidation"
WAREHOUSE = "1c9706d9594f1f2f"
TITLE = "Nike Stock Movement - Merchandising"
DESCRIPTION = ("Ask about the Nike stock movement engine's daily results: store-to-store moves, held and kept stock, "
               "DC-to-store replenishment, lines not filled and review approvals.")
TABLES = [f"{FQ}.merch_runs", f"{FQ}.merch_dc_replenishment", f"{FQ}.merch_store_stock"]

INSTRUCTIONS = """You answer merchandising questions about the Nike stock movement engine. It runs every day at 10:30 UAE time and recommends (1) store-to-store transfers of surplus stock and (2) DC-to-store replenishment. It only recommends; people approve the exceptions.

Which run to use:
- Unless the user names a run, a date or a scoped run, ALWAYS filter is_latest_full_run = TRUE (the latest daily run covering all countries and categories).
- "today" / "latest" / "this run" = is_latest_full_run = TRUE. For history use merch_runs and join on run_id.
- Keep rows in merch_store_stock exist only for the latest full run.

merch_store_stock (one row per store x size):
- status = Move (recommended store-to-store transfer, store -> to_store), Held (would move but a rule held it; to_store is the store it would go to), Keep (stays at the store).
- units means transfer units for Move, movable units for Held, stock on hand for Keep. Never add units across different statuses unless the user explicitly asks for a total of everything.
- reason: why it moves (Move), the rule that held it (Held: Size-curve break, Season-freshness, APO, Receiver stays cut-size, Move-back <30d), or why it stays (Keep).
- value_aed is cost value.

merch_dc_replenishment (one row per store x size need):
- status = Replenish (the DC sends units) or Not filled (store needed stock the DC is not sending; reason says why: No DC stock, DC short or blocked, Held: store would stay cut-size, Blocked: moved out under 30 days ago).
- units = units sent (Replenish) or units needed (Not filled). Do not add them together.
- reason for Replenish: Sold below 14-day cover, Display: 1 per key size, Full-size top-up (sent only to make the store full-size; needs review).
- line_value_aed is retail value of a Replenish line; annual_value_at_risk_aed is the yearly sales at risk of a Not filled line.

Review: review_status is Approved / Rejected / Pending for lines that need a person (full-size top-ups, lines worth >= AED 1,000 or >= 6 units) and Not required for routine lines.

Full / cut size: a store displays a style when it holds more than a threshold of its key sizes (Full size); otherwise Cut size. to_store_size_status_after shows this for the receiving store after the moves.

Matching: stores can be named by code (e.g. 1122) or name; match names with ILIKE '%name%' on store_name / to_store_name. Countries are ISO codes: AE = UAE, SA = KSA, KW = Kuwait, BH = Bahrain, OM = Oman, QA = Qatar. Styles can be named by style_code (e.g. NKIF1911-010) or style_name (ILIKE).

"Why did store X get size Y of style Z": find the row(s) for that store, style and size in both views for the latest full run and report status, units, reason, store_stock_before / store_stock, store_in_transit, daily_demand, cover and review_status.

Format: show units with thousands separators, AED values as K / M (e.g. AED 176K, AED 1.1M), and say which run (run_id, run_date) the answer comes from."""

SAMPLES = [
    "How many units are we moving store to store in the latest run, by category?",
    "Which stores get the most DC replenishment units today?",
    "Why did store 1122 get size M of NKIF1911-010?",
    "Show held stock by held reason for FOOTBALL",
    "Which styles have the most dead stock sitting in stores?",
    "Which DC lines are still pending review?",
    "Top 20 store needs not filled because the DC has no stock, by value at risk",
    "List the full-size top-ups for Dubai Mall",
    "Compare store moves and DC lines for the last 7 daily runs",
    "Which receiving stores are still cut-size after the moves?",
]

EXAMPLES = [
    ("How many units are moving store to store today by category?",
     "SELECT category, COUNT(*) AS lines, SUM(units) AS units, ROUND(SUM(value_aed), 0) AS value_aed\n"
     f"FROM {FQ}.merch_store_stock\nWHERE is_latest_full_run AND status = 'Move'\nGROUP BY category ORDER BY units DESC"),
    ("Why did store 1122 get size M of NKIF1911-010?",
     "SELECT 'DC' AS source, status, units, reason, store_stock_before, store_in_transit, daily_demand, review_status\n"
     f"FROM {FQ}.merch_dc_replenishment\nWHERE is_latest_full_run AND store = '1122' AND style_code = 'NKIF1911-010' AND size = 'M'\n"
     "UNION ALL\nSELECT 'Store move', status, units, reason, store_stock, NULL, NULL, review_status\n"
     f"FROM {FQ}.merch_store_stock\nWHERE is_latest_full_run AND (store = '1122' OR to_store = '1122') AND style_code = 'NKIF1911-010' AND size = 'M'"),
    ("Daily trend of store moves and DC lines",
     f"SELECT run_date, run_id, store_moves, store_move_units, dc_lines, dc_units, dc_value_aed\nFROM {FQ}.merch_runs\n"
     "WHERE is_full_scope ORDER BY run_time_gst DESC LIMIT 7"),
]


def hid():
    return uuid.uuid4().hex


def payload():
    space = {
        "version": 1,
        "config": {"sample_questions": sorted([{"id": hid(), "question": [q]} for q in SAMPLES], key=lambda x: x["id"])},
        "data_sources": {"tables": sorted([{"identifier": t} for t in TABLES], key=lambda x: x["identifier"])},
        "instructions": {
            "text_instructions": [{"id": hid(), "content": [INSTRUCTIONS]}],
            "example_question_sqls": sorted([{"id": hid(), "question": [q], "sql": [s]} for q, s in EXAMPLES], key=lambda x: x["id"]),
        },
    }
    return {"warehouse_id": WAREHOUSE, "title": TITLE, "description": DESCRIPTION, "serialized_space": json.dumps(space)}


def api(method, path, body):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); json.dump(body, f); f.close()
    env = dict(os.environ); env["PATH"] = os.path.expanduser("~/.local/bin") + ":" + env.get("PATH", "")
    r = subprocess.run(["databricks", "api", method, path, "--json", "@" + f.name, "--profile", "gmg-sso"],
                       capture_output=True, text=True, env=env)
    return r.stdout or r.stderr


if __name__ == "__main__":
    if "--create" in sys.argv:
        print(api("post", "/api/2.0/genie/spaces", payload()))
    elif "--update" in sys.argv:
        sid = sys.argv[sys.argv.index("--update") + 1]
        print(api("patch", f"/api/2.0/genie/spaces/{sid}", payload()))
    else:
        print(json.dumps(payload(), indent=1)[:3000])
