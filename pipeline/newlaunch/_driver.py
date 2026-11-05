# COMMAND ----------
# MAGIC %md ### Run: launch history -> back-test (last spring) -> forecast the target season -> nl_ tables

# COMMAND ----------
import uuid
from datetime import datetime, timezone
RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
L = spark.sql(LAUNCH).toPandas()
A0 = spark.sql(ATTRS).toPandas()
C = spark.sql(COLOUR).toPandas()
try:
    TG = spark.sql(IMAGE_TAGS_SQL).toPandas()
except Exception as e:                       # no image tags yet: every style uses the attribute fallback
    print("no image tags:", e); TG = None
try:
    EM = spark.sql(EMBEDDINGS_SQL).toPandas()
except Exception as e:
    print("no image embeddings:", e); EM = None
o = run(L, A0, C, TARGET_SEASON, TARGET_YEAR, TG, EM)


def save(pdf, table):
    pdf = pdf.copy()
    for c in pdf.columns:
        if pdf[c].dtype == object:
            pdf[c] = pdf[c].astype(str).replace({"nan": None, "None": None})
    pdf["run_id"] = RUN_ID
    spark.createDataFrame(pdf).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.{table}")
    print(table, len(pdf))


for name, table in (("styles", "nl_forecast"), ("country", "nl_forecast_country"), ("weekly", "nl_forecast_weekly"),
                    ("analogues", "nl_analogues"), ("backtest", "nl_backtest"),
                    ("weights", "nl_weights")):
    save(o[name], table)
