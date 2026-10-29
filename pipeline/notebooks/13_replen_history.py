# Databricks notebook source
# MAGIC %md
# MAGIC # 13 · Replenishment History Archive (P1, additive)
# MAGIC
# MAGIC Mirrors `07_history_archive`: appends this run's replen outputs to append-only `*_history` Delta tables,
# MAGIC stamped with the run key (`run_id`, `run_ts`, `as_of_date`, `country_scope`, `triggered_by`).
# MAGIC **Idempotent** — re-running the same `RUN_ID` deletes that run's rows first, then re-appends.

# COMMAND ----------
from pyspark.sql import functions as F
CATALOG = "sports_analytics_test"; SCHEMA = "nike_consolidation"; FQ = f"{CATALOG}.{SCHEMA}"
cfg = {r["param_name"]: r["param_value"] for r in spark.table(f"{FQ}.run_config").collect()}
RUN_ID = cfg["RUN_ID"]; RUN_TS = cfg["RUN_TS"]; AS_OF = cfg["AS_OF_DATE"]
COUNTRY = cfg["COUNTRY"]; TRIGGERED_BY = cfg.get("TRIGGERED_BY", "manual")
print(f"Archiving replen RUN_ID={RUN_ID} AS_OF={AS_OF} COUNTRY={COUNTRY} by={TRIGGERED_BY}")

# COMMAND ----------
def archive(src: str):
    hist = f"{FQ}.{src}_history"
    df = (spark.table(f"{FQ}.{src}")
            .withColumn("run_id", F.lit(RUN_ID))
            .withColumn("run_ts", F.to_timestamp(F.lit(RUN_TS)))
            .withColumn("country_scope", F.lit(COUNTRY))
            .withColumn("category_scope", F.lit(cfg.get("CATEGORY") or "ALL"))
            .withColumn("triggered_by", F.lit(TRIGGERED_BY))
            .withColumn("archived_at", F.current_timestamp()))
    if "as_of_date" not in df.columns:
        df = df.withColumn("as_of_date", F.to_date(F.lit(AS_OF)))
    if spark.catalog.tableExists(hist):
        spark.sql(f"DELETE FROM {hist} WHERE run_id = '{RUN_ID}'")
    df.write.mode("append").option("mergeSchema", "true").saveAsTable(hist)
    print(f"  {hist}: +{spark.table(hist).where(F.col('run_id')==RUN_ID).count()} rows")

for src in ["replen_stats", "replen_recommendations", "replen_not_replenished"]:
    archive(src)

# COMMAND ----------
display(spark.sql(f"""
  SELECT run_id, run_ts, as_of_date, country_scope, replen_lines, replen_units, stores, skus,
         fill_value_aed, no_dc_stock_lines, shortfall_or_blocked_lines
  FROM {FQ}.replen_stats_history ORDER BY run_ts DESC
"""))
