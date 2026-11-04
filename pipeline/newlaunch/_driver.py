# COMMAND ----------
# MAGIC %md ### Run: launch history -> back-test (last spring) -> forecast the target season -> nl_ tables

# COMMAND ----------
import uuid
from datetime import datetime, timezone
RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
L = spark.sql(LAUNCH).toPandas()
A0 = spark.sql(ATTRS).toPandas()
C = spark.sql(COLOUR).toPandas()
o = run(L, A0, C, TARGET_SEASON, TARGET_YEAR)


def save(pdf, table):
    pdf = pdf.copy()
    for c in pdf.columns:
        if pdf[c].dtype == object:
            pdf[c] = pdf[c].astype(str).replace({"nan": None, "None": None})
    pdf["run_id"] = RUN_ID
    spark.createDataFrame(pdf).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.{table}")
    print(table, len(pdf))


for name, table in (("styles", "nl_forecast"), ("country", "nl_forecast_country"), ("weekly", "nl_forecast_weekly"),
                    ("analogues", "nl_analogues"), ("backtest", "nl_backtest")):
    save(o[name], table)
