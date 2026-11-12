---
name: run-nike-stock-movement
description: Runs the Nike stock movement job (store-to-store consolidation + DC-to-store replenishment) on Databricks, with the default settings or settings the user changes. Use whenever the user asks to run, rerun, refresh or trigger the stock movement engine, the consolidation job or the replenishment job. Always asks whether to change any default setting before running.
---

# Run the Nike stock movement job

- Job: `nike_stock_movement_pipeline`, job ID **553755400746008**
- Workspace: https://dbc-74c28e5e-272e.cloud.databricks.com (workspace ID 8517174019523704)
- Job page: https://dbc-74c28e5e-272e.cloud.databricks.com/jobs/553755400746008?o=8517174019523704
- It already runs every day at 10:30 Dubai time with the defaults. Use this skill for an extra run, usually with changed settings.

## Rule 1: always ask before running

Never start the job on the first message. Always:

1. Show the settings table below with the default values.
2. Ask: **"Do you want to change any of these settings before I run the job?"**
3. Wait for the answer.
4. Apply the user's changes and check them against the rules in "Check the settings". If a value breaks a rule, say which and why, and ask again.
5. Show the final settings as a table, marking each changed value with "(changed)", and ask: **"Run with these settings?"**
6. Start the job only after a clear yes.

Even if the user says "just run it", show the table once and ask once whether anything should change.

## Settings the user can change

| Setting (job parameter) | Default | What it does |
| --- | --- | --- |
| COUNTRY | ALL | Countries to run. ALL, or a comma-separated list of ISO codes: AE (UAE), SA (KSA), KW (Kuwait), BH (Bahrain), OM (Oman), QA (Qatar) |
| CATEGORY | ALL | Categories to run. ALL, or a list separated by \| from: ACCESSORIES, ACG, BASKETBALL, CONVERSE, FOOTBALL, FOOTBALL KIDS, HYPER LOCAL, NSW MENS, NSW WOMENS, OTHERS, RUNNING MENS, RUNNING WOMENS, SERVICES, SKIMS, SWIM KIDS, SWIM MENS, SWIM WOMENS, TRAINING MENS, TRAINING WOMENS, YOUNG ATHLETES |
| ROS_WINDOW_DAYS | 14 | Days of sales used for the rate of sale |
| IN_TRANSIT_LOOKBACK_DAYS | 14 | Days of stock in transit counted for the receiving store |
| SLOW_MOVING_THRESHOLD_DAYS | 30 | Days of cover above which stock counts as slow moving (a donor) |
| NEW_TO_STORE_DAYS | 14 | Stock first received within this many days is left alone |
| PROTECT_COVER_DAYS | 7 | Days of cover a giving store always keeps |
| TARGET_COVER_DAYS | 14 | Days of cover a receiving store is filled up to |
| AS_OF_DATE | blank = yesterday | Last day of sales used (YYYY-MM-DD). Change only to re-run a past day |
| ATP_AS_OF | blank = today | Date of the stock snapshot used (YYYY-MM-DD). Change only to re-run a past day |

Set by you, never asked:

| Setting | Value |
| --- | --- |
| RUN_ID | Current UTC time as `yyyyMMdd'T'HHmmss'Z'` + `-` + 6 random lowercase hex characters, e.g. `20261106T081500Z-3fa9c2` |
| TRIGGERED_BY | The user's email address |

## Check the settings

- The six day settings are whole numbers from 1 to 365.
- PROTECT_COVER_DAYS must be **less than** TARGET_COVER_DAYS; otherwise stock would circulate between stores.
- COUNTRY uses only the codes listed. Upper-case it.
- CATEGORY uses only the categories listed, joined with `|` (e.g. `FOOTBALL|RUNNING MENS`). Upper-case it.
- AS_OF_DATE is no later than yesterday and ATP_AS_OF no later than today, both in YYYY-MM-DD. Leave them blank unless the user asks for a past day.
- Note: the active store list currently holds UAE stores only, so other countries return no moves until stores are added.

## Tell the user before starting

- A run takes about 7 to 8 minutes, and only one runs at a time. If another is running, this one waits in the queue.
- The run replaces the latest results everyone sees in the app until the next run. If it is not a full run (COUNTRY and CATEGORY both ALL), say so plainly: the app will show only that country or category until the next full run at 10:30.

## Start the run

Use the Databricks Jobs tool or API available to you, as the user:

```
POST /api/2.2/jobs/run-now
{
  "job_id": 553755400746008,
  "job_parameters": {
    "COUNTRY": "ALL", "CATEGORY": "ALL",
    "ROS_WINDOW_DAYS": "14", "IN_TRANSIT_LOOKBACK_DAYS": "14", "SLOW_MOVING_THRESHOLD_DAYS": "30",
    "NEW_TO_STORE_DAYS": "14", "PROTECT_COVER_DAYS": "7", "TARGET_COVER_DAYS": "14",
    "AS_OF_DATE": "", "ATP_AS_OF": "",
    "RUN_ID": "<generated>", "TRIGGERED_BY": "<user email>"
  }
}
```

- Send every parameter, all as strings, with the final values.
- Reply with the run link: `https://dbc-74c28e5e-272e.cloud.databricks.com/?o=8517174019523704#job/553755400746008/run/<run_id from the response>`
- If you cannot start jobs (no jobs tool, or a permission error), say so. Give the user the job page link and the final settings, so they can use "Run now with different parameters" themselves. The user needs "Can manage run" on the job.

## Follow the run

Check `GET /api/2.2/jobs/runs/get?run_id=<run_id>` about every 30 seconds and report progress by step: config, effective stock, classify, pools, match, size curve, DC demand, DC rationing, receiver guard, score, DC output, DC archive, archive (13 steps).

When it ends:

- **Succeeded:** summarise this run's results with SQL on warehouse 1c9706d9594f1f2f:

```sql
SELECT s.run_id, s.country_scope, s.category_scope, s.as_of_date,
       s.recommended_moves, s.total_units AS move_units, s.held_lines,
       r.replen_lines AS dc_lines, r.replen_units AS dc_units, ROUND(r.fill_value_aed) AS dc_value_aed
FROM sports_analytics_test.nike_consolidation.run_stats_history s
LEFT JOIN sports_analytics_test.nike_consolidation.replen_stats_history r ON r.run_id = s.run_id
WHERE s.run_id = '<RUN_ID you set>'
```

  Report store moves, units moved, held lines, DC lines, DC units and DC value (AED as K / M), and compare them with the latest scheduled run (`triggered_by = 'app'` in run_stats_history).
- **Failed:** name the failed step and its error message from runs/get. Say that the run was not saved to history, so it will not appear in the app's run picker. Steps that finished may already have refreshed the latest tables, so suggest a rerun.
- **Cancelled or timed out:** say so and offer to run again.
