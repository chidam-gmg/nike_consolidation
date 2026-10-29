# Databricks notebook source
# MAGIC %md
# MAGIC # 07 · History Archive — Nike Consolidation POC
# MAGIC
# MAGIC The `00→06` output tables are all `CREATE OR REPLACE` (each run overwrites the last). This step
# MAGIC **appends** every run's outputs to append-only `*_history` Delta tables so business can browse and
# MAGIC compare **every historical run**. Each archived row carries the run key: `run_id`, `run_ts`,
# MAGIC `as_of_date`, `country_scope`.
# MAGIC
# MAGIC - **`run_stats_history`** — one row per run: the headline metrics + partition reconciliation (the
# MAGIC   canonical "list of every run"). Always archived (tiny).
# MAGIC - **`recommendations_history`** / **`held_history`** — the actionable detail per run.
# MAGIC - **`not_moved_history`** — the full retained residual (~600K rows/run). OFF by default (its line
# MAGIC   count is preserved in `run_stats_history`); set `ARCHIVE_NOT_MOVED=1` in `cfg_scalar_params` to keep it.
# MAGIC
# MAGIC **Idempotent:** re-running the same `RUN_ID` first deletes that run's rows, then re-appends — so a
# MAGIC pipeline retry never double-counts. Schema drift is absorbed via `mergeSchema`.

# COMMAND ----------
from pyspark.sql import functions as F
CATALOG = "sports_analytics_test"; SCHEMA = "nike_consolidation"; FQ = f"{CATALOG}.{SCHEMA}"
cfg = {r["param_name"]: r["param_value"] for r in spark.table(f"{FQ}.run_config").collect()}
RUN_ID   = cfg["RUN_ID"]
RUN_TS   = cfg["RUN_TS"]
AS_OF    = cfg["AS_OF_DATE"]
COUNTRY  = cfg["COUNTRY"]
TRIGGERED_BY = cfg.get("TRIGGERED_BY", "manual")
# not_moved is now ALWAYS archived so the app can read each user's own run in isolation; not_moved_history
# is pruned to the most recent NOT_MOVED_KEEP_RUNS runs (it is the storage hog at ~570K rows/run).
NOT_MOVED_KEEP_RUNS = int(str(cfg.get("NOT_MOVED_KEEP_RUNS", "25")).strip() or "25")
print(f"Archiving RUN_ID={RUN_ID}  RUN_TS={RUN_TS}  AS_OF={AS_OF}  COUNTRY={COUNTRY}  by={TRIGGERED_BY}")

# COMMAND ----------
def archive(src: str):
    """Append {FQ}.{src} to {FQ}.{src}_history, stamped with the run key; replace-by-run_id (idempotent)."""
    hist = f"{FQ}.{src}_history"
    df = (spark.table(f"{FQ}.{src}")
            .withColumn("run_id",       F.lit(RUN_ID))
            .withColumn("run_ts",       F.to_timestamp(F.lit(RUN_TS)))
            .withColumn("country_scope", F.lit(COUNTRY))
            .withColumn("category_scope", F.lit(cfg.get("CATEGORY") or "ALL"))
            .withColumn("triggered_by",  F.lit(TRIGGERED_BY))
            .withColumn("archived_at",  F.current_timestamp()))
    # run_stats has no as_of_date column; recommendations does. Add one only if absent so history is uniform.
    if "as_of_date" not in df.columns:
        df = df.withColumn("as_of_date", F.to_date(F.lit(AS_OF)))
    if spark.catalog.tableExists(hist):
        spark.sql(f"DELETE FROM {hist} WHERE run_id = '{RUN_ID}'")   # idempotent retry
    (df.write.mode("append").option("mergeSchema", "true").saveAsTable(hist))
    n = spark.table(hist).where(F.col("run_id") == RUN_ID).count()
    print(f"  {hist}: +{n} rows for this run")

for src in ["run_stats", "recommendations", "held", "not_moved"]:
    archive(src)

# COMMAND ----------
# Retention: keep only the most recent NOT_MOVED_KEEP_RUNS runs in not_moved_history (the ~570K-rows/run
# hog). recommendations_history / held_history / run_stats_history are small and kept in full.
nm_hist = f"{FQ}.not_moved_history"
if spark.catalog.tableExists(nm_hist):
    keep = [r["run_id"] for r in spark.sql(
        f"SELECT run_id FROM {nm_hist} GROUP BY run_id ORDER BY MAX(run_ts) DESC LIMIT {NOT_MOVED_KEEP_RUNS}"
    ).collect()]
    if keep:
        keep_list = ", ".join("'" + k.replace("'", "''") + "'" for k in keep)
        spark.sql(f"DELETE FROM {nm_hist} WHERE run_id NOT IN ({keep_list})")
        print(f"  {nm_hist}: retained {len(keep)} most-recent runs")

# COMMAND ----------
# The run log — every run ever, newest first.
display(spark.sql(f"""
  SELECT run_id, run_ts, as_of_date, country_scope,
         recommended_moves, total_units, held_lines, not_moved_lines, universe_lines,
         partition_reconciles, ROUND(total_net_benefit,0) AS net_benefit
  FROM {FQ}.run_stats_history
  ORDER BY run_ts DESC
"""))
