# COMMAND ----------
# MAGIC %md ### Run: launch forecast -> ranged stores -> size curve -> opening pack within the buy -> nl_opening_pack, nl_pack_country

# COMMAND ----------
import uuid
from datetime import datetime, timezone
RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
I = {k: spark.sql(q).toPandas() for k, q in (("fc", PACK_FC), ("fc_country", PACK_FC_COUNTRY), ("weekly", PACK_WEEKLY),
                                              ("sizes", PACK_SIZES), ("stores", PACK_STORES), ("store_units", PACK_STORE_UNITS),
                                              ("curve", PACK_CURVE), ("bands", PACK_BANDS))}
try:
    BUY = spark.sql(PACK_BUY).toPandas()
except Exception as e:                       # no buy file loaded yet: buy assumed = 13-week P80
    print("no buy file:", str(e)[:120]); BUY = None
o = opening_pack(buy=BUY, **I)


def save(pdf, table):
    pdf = pdf.copy()
    for c in pdf.columns:
        if pdf[c].dtype == object:
            pdf[c] = pdf[c].astype(str).replace({"nan": None, "None": None})
    pdf["run_id"] = RUN_ID
    spark.createDataFrame(pdf).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.{table}")
    print(table, len(pdf))


save(o["lines"], "nl_opening_pack")
save(o["country"], "nl_pack_country")
c = o["country"]
print(f"{c['parent'].nunique():,} styles · buy {c['buy_qty'].sum():,} · packs {c['pack_units'].sum():,} · "
      f"DC reserve {c['reserve_units'].sum():,} · {o['lines']['store'].nunique()} stores")
