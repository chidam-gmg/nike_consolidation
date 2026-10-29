# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · ROS, Cover & Classification — Nike Consolidation POC
# MAGIC
# MAGIC Per store × child article: **ROS** = units sold (SAP retail, `distr_channel` per config, trailing
# MAGIC `ROS_WINDOW_DAYS` ending `AS_OF_DATE`) / `ROS_WINDOW_DAYS`; **cover_days** = `effective_stock / ROS`
# MAGIC (NULL = +inf when ROS=0); **classification** (precedence): `NEW_TO_STORE` (first GRN within
# MAGIC `NEW_TO_STORE_DAYS`, excluded from computation) → `DEAD` (ROS=0) → `SLOW_MOVING`
# MAGIC (cover > `SLOW_MOVING_THRESHOLD_DAYS`) → `HEALTHY`.
# MAGIC
# MAGIC Multi-country: no explicit country filter needed — sales LEFT JOIN onto the (already country-scoped)
# MAGIC `effective_stock`, so out-of-scope rows never appear. GRN = `sports_store_and_warehouse_grn_dates`
# MAGIC (1 row/Location×Material; missing GRN ⇒ NOT new-to-store). Reads frozen `run_config`.

# COMMAND ----------
CATALOG = "sports_analytics_test"; SCHEMA = "nike_consolidation"; FQ = f"{CATALOG}.{SCHEMA}"
cfg = {r["param_name"]: r["param_value"] for r in spark.table(f"{FQ}.run_config").collect()}
AS_OF = cfg["AS_OF_DATE"]; ROS_WIN = int(cfg["ROS_WINDOW_DAYS"])
NEW_DAYS = int(cfg["NEW_TO_STORE_DAYS"]); SLOW_THR = int(cfg["SLOW_MOVING_THRESHOLD_DAYS"])
CHANNELS = "', '".join(cfg["SALES_DISTR_CHANNELS"].split(","))
COUNTRY = cfg["COUNTRY"]; _cc = ", ".join("'" + c.strip() + "'" for c in COUNTRY.split(",") if c.strip())
store_country = "" if COUNTRY == "ALL" else f"AND sm.COUNTRY IN ({_cc})"   # qualify: csg (cfg_store_grade) now also has COUNTRY
CATEGORY = cfg.get("CATEGORY") or "ALL"   # category scope (run_article_scope built in 01)
def in_scope(col):
    return "" if CATEGORY == "ALL" else f"AND {col} IN (SELECT article_code FROM {FQ}.run_article_scope)"
print(f"AS_OF={AS_OF} ROS_WIN={ROS_WIN} NEW_DAYS={NEW_DAYS} SLOW_THR={SLOW_THR} CHANNELS=[{CHANNELS}] COUNTRY={COUNTRY}")

# COMMAND ----------
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.stock_classified AS
WITH sales AS (   -- all articles; channels per config (10 retail + 20 ecom); txn_net_amount is LOCAL
  -- units_sold = GROSS (positive qty only) — demand signal for ROS. A size that SOLD then was RETURNED is not
  -- "dead" (real demand + the unit is back in ATP), so ROS/DEAD use gross (2026-08-10, business call, low ~2.6%
  -- return rate). net_sales / net_sales_per_unit stay NET (revenue net of returns = true value per unit).
  SELECT s.site_code AS store_id, s.variant_article_code AS article_code,
         SUM(GREATEST(s.txn_qty, 0)) AS units_sold,
         SUM(s.txn_net_amount * s.exchange_rate) AS net_sales,                              -- -> AED (net of returns)
         SUM(s.txn_net_amount * s.exchange_rate) / NULLIF(SUM(s.txn_qty), 0) AS net_sales_per_unit
  FROM sap_gmg_datalake.silver.fct_sap_sales_detail_v s
  WHERE s.distr_channel IN ('{CHANNELS}')
    AND s.txn_date >  date_sub(DATE'{AS_OF}', {ROS_WIN})
    AND s.txn_date <= DATE'{AS_OF}'
  GROUP BY s.site_code, s.variant_article_code
),
grn AS (
  SELECT Location AS store_id, Material AS article_code, MIN(First_GRN_Date) AS first_grn_date
  FROM prod_mena.gold.sports_store_and_warehouse_grn_dates GROUP BY Location, Material
),
base AS (
  SELECT e.country, e.store_id, e.store_name, e.bu, e.store_grade,
         e.generic_article, e.article_code, e.product_size,
         e.atp_qty, e.intransit_qty, e.effective_stock, e.unit_cost_aed,
         COALESCE(sa.units_sold, 0) AS units_sold, COALESCE(sa.net_sales, 0) AS net_sales,
         sa.net_sales_per_unit, g.first_grn_date,
         COALESCE(sa.units_sold, 0) / {ROS_WIN}.0 AS ros
  FROM {FQ}.effective_stock e
  LEFT JOIN sales sa ON e.store_id = sa.store_id AND e.article_code = sa.article_code
  LEFT JOIN grn   g  ON e.store_id = g.store_id  AND e.article_code = g.article_code
)
SELECT b.*,
  CASE WHEN b.ros = 0 THEN NULL ELSE b.effective_stock / b.ros END AS cover_days,
  CASE
    WHEN b.first_grn_date > date_sub(DATE'{AS_OF}', {NEW_DAYS}) THEN 'NEW_TO_STORE'
    WHEN b.ros = 0                                              THEN 'DEAD'
    WHEN (b.effective_stock / b.ros) > {SLOW_THR}               THEN 'SLOW_MOVING'
    ELSE 'HEALTHY'
  END AS classification,
  DATE'{AS_OF}' AS as_of_date
FROM base b
""")

# COMMAND ----------
# SALES DEMAND (point 1, 2026-07-28) — store×article sales INDEPENDENT of ATP. `stock_classified` anchors
# sales on effective_stock (ATP>0), so a size a store SELLS but is stocked out (ATP=0) is dropped and never
# becomes a receiver. This table keeps ALL Nike-BU store sales (ch per config, trailing ROS window) so 03 can
# build the out-of-stock "latent demand" receivers. article -> parent/size from product master (authoritative,
# available even when the article has zero ATP anywhere). Nike-BU PLANTCAT='A' stores only.
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.sales_demand AS
WITH sales AS (   -- units_sold = GROSS (positive qty only) for ROS/demand; revenue stays NET (see stock_classified note)
  SELECT s.site_code AS store_id, s.variant_article_code AS article_code,
         SUM(GREATEST(s.txn_qty, 0)) AS units_sold,
         SUM(s.txn_net_amount * s.exchange_rate) AS net_sales,
         SUM(s.txn_net_amount * s.exchange_rate) / NULLIF(SUM(s.txn_qty), 0) AS net_sales_per_unit
  FROM sap_gmg_datalake.silver.fct_sap_sales_detail_v s
  WHERE s.distr_channel IN ('{CHANNELS}')
    AND s.txn_date >  date_sub(DATE'{AS_OF}', {ROS_WIN})
    AND s.txn_date <= DATE'{AS_OF}'
  GROUP BY s.site_code, s.variant_article_code
),
stores AS (   -- active store list is the authority: restrict + grade from cfg_store_grade
  SELECT sm.PLANT AS store_id, sm.COUNTRY AS country, sm.`/BIC/ZNAME1` AS store_name,
         sm.`/BIC/ZBU` AS bu, csg.grade AS store_grade
  FROM prod_mena.gold.mena_sports_store_master_view sm
  JOIN {FQ}.cfg_store_grade csg ON csg.store_code = sm.PLANT
  WHERE sm.PLANTCAT = 'A' AND sm.`/BIC/ZBU` = 'NIKE' {store_country}
),
art AS (
  SELECT p.MATERIAL AS article_code, MAX(p.RT_CONFMAT) AS generic_article,
         MAX(CASE WHEN TRIM(p.RT_SIZE) = '-' THEN '0.5' ELSE TRIM(p.RT_SIZE) END) AS product_size
  FROM prod_mena.gold.mena_sports_product_master_view p WHERE p.MATL_CAT = '02' GROUP BY p.MATERIAL
)
SELECT st.country, sa.store_id, st.store_name, st.bu, st.store_grade,
       art.generic_article, sa.article_code, art.product_size,
       sa.units_sold, sa.net_sales, sa.net_sales_per_unit,
       sa.units_sold / {ROS_WIN}.0 AS ros,
       DATE'{AS_OF}' AS as_of_date
FROM sales sa
JOIN stores st ON sa.store_id = st.store_id
LEFT JOIN art  ON art.article_code = sa.article_code
WHERE 1 = 1 {in_scope('sa.article_code')}
""")

# COMMAND ----------
display(spark.sql(f"""
  SELECT classification, COUNT(*) rows, COUNT(DISTINCT store_id) stores,
         ROUND(SUM(effective_stock),0) units, ROUND(AVG(cover_days),1) avg_cover
  FROM {FQ}.stock_classified GROUP BY classification ORDER BY rows DESC
"""))
