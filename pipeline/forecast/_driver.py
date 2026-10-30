# COMMAND ----------
# MAGIC %md ### Run: read inputs, forecast, allocate, write the fc_ / al_ tables the Agent lab reads

# COMMAND ----------
import uuid
from datetime import datetime, timezone
RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
names = ["WEEKLY", "STORE_SIZE", "STOCK", "STORES", "KEY_SIZES", "DC", "DC_MAP", "CURRENT_REPLEN", "AVAIL"]
inp = {n: spark.sql(globals()[n]).toPandas() for n in names}
o = run(*[inp[n] for n in names])
log = o["log"]

def save(pdf, table):
    pdf = pdf.copy()
    for c in pdf.columns:
        if pdf[c].dtype == object:
            pdf[c] = pdf[c].astype(str).replace({"nan": None, "None": None})
    pdf["run_id"] = RUN_ID
    spark.createDataFrame(pdf).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.{table}")
    print(table, len(pdf))

save(o["forecast"], "fc_forecast")
hist = o["history"][["country", "parent", "wk", "units"]].copy(); hist["wk"] = hist["wk"].dt.date
save(hist, "fc_history")
save(o["backtest"], "fc_backtest")
save(o["leaderboard"], "fc_leaderboard")
save(o["allocation"], "al_allocation")
save(o["seasonality"], "fc_seasonality")
save(o["events"], "fc_events")
save(o["event_uplift"], "fc_event_uplift")
save(o["error_bands"], "fc_error_bands")
save(o["calibration"], "fc_calibration")
save(pd.DataFrame(log.stages), "fc_run_stages")
save(pd.DataFrame(log.events), "fc_run_events")
vs = o["vs_current"]
save(pd.DataFrame([{"run_ts": datetime.now(timezone.utc).isoformat(), "error_champion": o["error_champion"],
                    "error_baseline": o["error_baseline"], "error_flat_best": o["error_flat_best"],
                    "error_seasonal_best": o["error_seasonal_best"], "error_fast": o["error_fast"], "floor_fast": o["floor_fast"],
                    "p80_coverage": o["p80_coverage"], "p80_coverage_old": o["p80_coverage_old"],
                    "p_sell_gap_slow": o["p_sell_gap_slow"], **vs}]), "fc_run_summary")
