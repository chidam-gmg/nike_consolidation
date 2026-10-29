# Databricks notebook source
# MAGIC %md
# MAGIC # 10 · Replenishment Demand & Need — DC→Store (P1, additive)
# MAGIC
# MAGIC **Twin of the consolidation receiver logic, with the DC as the supply and the warehouse guard INVERTED.**
# MAGIC Demand per **Nike** store × child article = **POS rate-of-sale (retail)** over the window. *(PDA loss-of-sale
# MAGIC de-censoring was REMOVED 2026-09-18 per business; the PDA CTE/columns remain but contribute 0 — easy to
# MAGIC re-enable.)* Computes the order-up-to **target**
# MAGIC `S = MAX(demand-cover over lead time, display floor)` and the **need** vs on-hand + in-transit.
# MAGIC
# MAGIC Reads the frozen `run_config` (00) and the shared tables built by `01–03` (effective_stock,
# MAGIC warehouse_stock, store_intransit, stock_classified, art_size_key). **Does not touch any 00–07 table.**
# MAGIC Nike-only (B3); scope + AS_OF inherited from run_config (via the already-country-scoped shared tables).

# COMMAND ----------
CATALOG = "sports_analytics_test"; SCHEMA = "nike_consolidation"; FQ = f"{CATALOG}.{SCHEMA}"
rc  = {r["param_name"]: r["param_value"] for r in spark.table(f"{FQ}.run_config").collect()}
rp  = {r["param_name"]: r["param_value"] for r in spark.table(f"{FQ}.cfg_replen_params").collect()}
AS_OF = rc["AS_OF_DATE"]
WIN      = int(rp.get("REPLEN_ROS_WINDOW_DAYS", "90"))
PDA_MIN  = int(rp.get("PDA_RECURRENCE_MIN_DAYS", "2"))
REVIEW   = int(rp.get("REVIEW_PERIOD_DAYS", "3"))
TARGET_COVER = int(rp.get("REPLEN_TARGET_COVER_DAYS", "14"))   # flat cover window (business 2026-09-22): simple 14-day
LT_Q     = rp.get("LEAD_TIME_QUANTILE", "P75").upper()
CH       = "', '".join([c.strip() for c in rp.get("REPLEN_SALES_CHANNELS", "10").split(",") if c.strip()])
CATS     = ", ".join("'" + c.strip().upper() + "'" for c in rp.get("CATEGORY_SCOPE", "FOOTWEAR,APPAREL").split(",") if c.strip())
# lead-time column: P75 default; BAH overridden to P90 (fat tail). Configurable via LEAD_TIME_QUANTILE.
LTCOL = "p90_days" if LT_Q == "P90" else "p75_days"
CATEGORY = rc.get("CATEGORY") or "ALL"   # category scope (run_article_scope built in 01)
CAT_SCOPE = "" if CATEGORY == "ALL" else f"AND dm.article_code IN (SELECT article_code FROM {FQ}.run_article_scope)"
print(f"AS_OF={AS_OF} WIN={WIN} PDA_MIN={PDA_MIN} REVIEW={REVIEW} LEAD={LT_Q} CHANNELS=[{CH}] CATS=[{CATS}]")

# COMMAND ----------
# DC SOURCING MODEL — `cfg_replen_dc` (tiered primary+hub map) and `dc_pool_stock` (shared pool inventory) are now
# built ONCE in notebook 01 (the SINGLE DC map shared by the consolidation warehouse guard AND replenishment,
# business 2026-09-22). This notebook just READS them below.

# COMMAND ----------
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.replen_demand AS
WITH pos AS (                                   -- retail RoS over the replen window (gross units); AED = amount / exchange_rate
  SELECT site_code AS store_id, variant_article_code AS article_code,
         SUM(GREATEST(txn_qty, 0)) AS pos_units,
         SUM(txn_net_amount / NULLIF(exchange_rate, 0)) / NULLIF(SUM(txn_qty), 0) AS net_per_unit_aed
  FROM sap_gmg_datalake.silver.fct_sap_sales_detail_v
  WHERE distr_channel IN ('{CH}') AND txn_date > date_sub(DATE'{AS_OF}', {WIN}) AND txn_date <= DATE'{AS_OF}'
  GROUP BY site_code, variant_article_code
),
pda AS (                                        -- PDA loss-of-sale over the same window (the de-censoring signal)
  SELECT store_code AS store_id, variant_article AS article_code,
         COUNT(DISTINCT CASE WHEN los_flag = 1 THEN txn_date END) AS pda_miss_days,
         SUM(CASE WHEN los_flag = 1 THEN order_qty ELSE 0 END) AS pda_lost_all,
         SUM(CASE WHEN los_flag = 1 THEN order_qty * min_or_cr_sl_price / NULLIF(exchange_rate, 0) ELSE 0 END) AS pda_lost_aed
  FROM runner_app_catalog.gold.fct_runner_app_item_detail_v
  WHERE txn_date > date_sub(DATE'{AS_OF}', {WIN}) AND txn_date <= DATE'{AS_OF}'
  GROUP BY store_code, variant_article
),
demand AS (                                     -- demand = POS sold ONLY. PDA de-censoring REMOVED (business 2026-09-18);
  -- pda_* kept as diagnostics (contribution forced to 0) so the schema and downstream stay stable — easy to re-enable.
  SELECT COALESCE(p.store_id, d.store_id) AS store_id, COALESCE(p.article_code, d.article_code) AS article_code,
         COALESCE(p.pos_units, 0) AS pos_units, p.net_per_unit_aed,
         CAST(0 AS DOUBLE) AS pda_lost_recurring,   -- PDA contribution removed for now (was: recurring PDA loss)
         COALESCE(d.pda_miss_days, 0) AS pda_miss_days,
         COALESCE(d.pda_lost_aed, 0) AS pda_lost_aed, COALESCE(d.pda_lost_all, 0) AS pda_lost_all
  FROM pos p FULL OUTER JOIN pda d ON p.store_id = d.store_id AND p.article_code = d.article_code
),
stores AS (   -- Nike active store list (authority) + country/grade; inherits run_config country scope via effective_stock
  SELECT DISTINCT store_id, country, store_name, store_grade FROM {FQ}.effective_stock
),
art AS (      -- child -> parent, size, division (product master); MATL_CAT='02' = variant (child) article
  SELECT p.MATERIAL AS article_code, MAX(p.RT_CONFMAT) AS generic_article,
         MAX(CASE WHEN TRIM(p.RT_SIZE) = '-' THEN '0.5' ELSE TRIM(p.RT_SIZE) END) AS product_size,
         MAX(TRIM(p.`/BIC/ZRWGH2_T`)) AS division
  FROM prod_mena.gold.mena_sports_product_master_view p WHERE p.MATL_CAT = '02' GROUP BY p.MATERIAL
),
base AS (
  SELECT st.country, dm.store_id, st.store_name, st.store_grade,
         art.generic_article, dm.article_code, art.product_size, UPPER(art.division) AS division,
         COALESCE(sc.effective_stock, 0) AS effective_stock, COALESCE(sc.atp_qty, 0) AS atp_qty,
         COALESCE(it.intransit_qty, 0) AS intransit_qty, sc.unit_cost_aed,
         dm.pos_units, dm.pda_lost_recurring, dm.pda_miss_days, dm.pda_lost_aed,
         (dm.pos_units + dm.pda_lost_recurring) AS demand_units,
         ROUND((dm.pos_units + dm.pda_lost_recurring) / {WIN}.0, 4) AS demand_rate_day,
         ROUND(COALESCE(dm.net_per_unit_aed, dm.pda_lost_aed / NULLIF(dm.pda_lost_all, 0)), 2) AS net_per_unit_aed,
         CASE WHEN dm.pos_units = 0 AND dm.pda_lost_recurring > 0 THEN 1 ELSE 0 END AS ros_censored_flag,
         -- display floor = 1 FACING per key size (breadth), NOT the size-curve threshold (that is a COUNT of
         -- key sizes for "full-size", not units-per-size). So target = MAX(demand cover, 1) => ROS drives depth,
         -- display only guarantees 1/size. (business 2026-09-22: was CAST(ak.threshold AS INT) -> inflated WH qty)
         CASE WHEN COALESCE(ak.is_key_size, 0) = 1 THEN 1 ELSE 0 END AS display_floor,
         cp.primary_pool AS primary_pool,
         COALESCE(pp.pool_atp, 0) AS primary_atp,
         CASE WHEN cp.draws_hub = 1 THEN COALESCE(hb.pool_atp, 0) ELSE 0 END AS hub_atp,
         (COALESCE(pp.pool_atp, 0) + CASE WHEN cp.draws_hub = 1 THEN COALESCE(hb.pool_atp, 0) ELSE 0 END) AS wh_atp,
         CAST({TARGET_COVER} AS INT) AS protection_window   -- flat 14-day cover for ALL countries (business 2026-09-22)
  FROM demand dm
  JOIN stores st ON st.store_id = dm.store_id
  JOIN art    ON art.article_code = dm.article_code
  LEFT JOIN {FQ}.stock_classified sc ON sc.store_id = dm.store_id AND sc.article_code = dm.article_code
  LEFT JOIN {FQ}.store_intransit  it ON it.store_id = dm.store_id AND it.article_code = dm.article_code
  LEFT JOIN (SELECT country, MAX(CASE WHEN tier = 1 AND pool_id <> 'HUB' THEN pool_id END) AS primary_pool,
                    MAX(CASE WHEN pool_id = 'HUB' THEN 1 ELSE 0 END) AS draws_hub
             FROM {FQ}.cfg_replen_dc GROUP BY country) cp ON cp.country = st.country
  LEFT JOIN {FQ}.dc_pool_stock pp ON pp.pool_id = cp.primary_pool AND pp.article_code = dm.article_code   -- exclusive primary
  LEFT JOIN {FQ}.dc_pool_stock hb ON hb.pool_id = 'HUB'           AND hb.article_code = dm.article_code   -- shared hub
  LEFT JOIN {FQ}.art_size_key     ak ON ak.article_code = dm.article_code
  WHERE dm.pos_units > 0 AND UPPER(TRIM(art.division)) IN ({CATS})   -- POS-only demand (PDA removed)
    {CAT_SCOPE}
)
SELECT b.*,
       GREATEST(b.demand_rate_day * b.protection_window, b.display_floor) AS s_units,
       CAST(CEIL(GREATEST(0, GREATEST(b.demand_rate_day * b.protection_window, b.display_floor)
                   - (b.effective_stock + b.intransit_qty))) AS INT) AS need_units,   -- round UP: 0.4 -> 1 (business 2026-09-22)
       DATE'{AS_OF}' AS as_of_date
FROM base b
""")

# COMMAND ----------
display(spark.sql(f"""
  SELECT country, COUNT(*) demand_rows, COUNT(DISTINCT store_id) stores,
         SUM(CASE WHEN wh_atp > 0 AND need_units > 0 THEN 1 ELSE 0 END) replenishable_rows,
         SUM(ros_censored_flag) censored_rows
  FROM {FQ}.replen_demand GROUP BY country ORDER BY demand_rows DESC
"""))
