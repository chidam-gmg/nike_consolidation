# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Effective Stock — Nike Consolidation POC
# MAGIC
# MAGIC `effective_stock[store, article] = ATP(SL01) ONLY`. In-transit (`combined_inventory` LATEST daily
# MAGIC snapshot) is carried as a **display-only** column — NOT added to effective_stock and NOT used in any
# MAGIC calculation (business decision 2026-07-24). **Universe = Nike-BU stores**
# MAGIC (`/BIC/ZBU='NIKE'`), **all articles** (no brand filter) — consolidation is *within Nike stores*
# MAGIC (validated against the Nike-BU DSR). Multi-country: `COUNTRY='ALL'` or a single ISO2 code.
# MAGIC Carries `country`, `store_name` (`/BIC/ZNAME1`), bu, grade, parent, size (`-`→0.5). Reads `run_config`.

# COMMAND ----------
CATALOG = "sports_analytics_test"; SCHEMA = "nike_consolidation"; FQ = f"{CATALOG}.{SCHEMA}"
cfg = {r["param_name"]: r["param_value"] for r in spark.table(f"{FQ}.run_config").collect()}
COUNTRY = cfg["COUNTRY"]; AS_OF = cfg["AS_OF_DATE"]; LOOKBACK = int(cfg["IN_TRANSIT_LOOKBACK_DAYS"])
ATP_AS_OF = cfg["ATP_AS_OF"]   # inventory anchor = TODAY (D); demand stays AS_OF = D-1 (business 2026-09-29)
ALL = (COUNTRY == "ALL")
_cc = ", ".join("'" + c.strip() + "'" for c in COUNTRY.split(",") if c.strip())   # COUNTRY may be a list
store_country = "" if ALL else f"AND sm.COUNTRY IN ({_cc})"   # qualify: csg (cfg_store_grade) now also has COUNTRY
CATEGORY = cfg.get("CATEGORY") or "ALL"   # '|'-separated business categories, or ALL
print(f"COUNTRY={COUNTRY} (ALL={ALL})  CATEGORY={CATEGORY}  AS_OF={AS_OF}  IN_TRANSIT_LOOKBACK_DAYS={LOOKBACK}")

# COMMAND ----------
# CATEGORY SCOPE (business 2026-10-28): a run can be limited to business categories (category converter).
# `run_article_scope` = the in-scope article list; 01/02/10 semi-join it so every downstream table (pools,
# moves, replen, held/not-moved, partition) is category-scoped. ALL => no filter (table still written, unused).
_cats = ", ".join("'" + c.replace("'", "''") + "'" for c in CATEGORY.split("|") if c)
_cat_where = "" if CATEGORY == "ALL" else f"AND UPPER(TRIM(cc.business_category)) IN ({_cats})"
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.run_article_scope AS
SELECT DISTINCT p.MATERIAL AS article_code, cc.business_category
FROM prod_mena.gold.mena_sports_product_master_view p
JOIN {FQ}.cfg_category_converter cc
  ON UPPER(cc.converter_key) = UPPER(CONCAT(COALESCE(p.RF_BNDID,''), COALESCE(p.`/BIC/ZRWGH3_T`,''),
       COALESCE(p.`/BIC/ZCHWGH5`,''), COALESCE(p.`/BIC/ZRWGH2_T`,'')))
WHERE p.MATL_CAT = '02' {_cat_where}
""")
if CATEGORY != "ALL":
    _n = spark.table(f"{FQ}.run_article_scope").count()
    if _n == 0:
        raise ValueError(f"CATEGORY={CATEGORY} matches no articles in cfg_category_converter — check the spelling.")
    print(f"Category scope: {_n} articles")
def in_scope(col):   # SQL predicate fragment restricting an article column to the run's category scope
    return "" if CATEGORY == "ALL" else f"AND {col} IN (SELECT article_code FROM {FQ}.run_article_scope)"

# COMMAND ----------
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.effective_stock AS
WITH stores AS (   -- ACTIVE store list (cfg_store_grade) is the AUTHORITY: universe is RESTRICTED to it (no
  -- consolidation for stores off the list) and store_grade comes from it (overrides master /BIC/ZCH_CL_GR).
  SELECT sm.PLANT AS store_id, sm.COUNTRY AS country, sm.`/BIC/ZNAME1` AS store_name,
         sm.`/BIC/ZBU` AS bu, csg.grade AS store_grade
  FROM prod_mena.gold.mena_sports_store_master_view sm
  JOIN {FQ}.cfg_store_grade csg ON csg.store_code = sm.PLANT
  WHERE sm.PLANTCAT = 'A' AND sm.`/BIC/ZBU` = 'NIKE' {store_country}
),
atp_snap AS (SELECT MAX(c_file_date) d FROM sap_gmg_datalake.bronze.atp_detail WHERE c_file_date <= DATE'{ATP_AS_OF}'),
art_dim AS (   -- parent + size per article (all brands), latest snapshot
  SELECT article_code, MAX(generic_article) AS generic_article,
         MAX(CASE WHEN product_size = '-' THEN '0.5' ELSE product_size END) AS product_size
  FROM sap_gmg_datalake.bronze.atp_detail
  WHERE c_file_date = (SELECT d FROM atp_snap)
  GROUP BY article_code
),
atp AS (
  SELECT a.site_code AS store_id, a.article_code,
         SUM(a.atp_qty) AS atp_qty, MAX(a.unit_cost_aed) AS unit_cost_aed
  FROM sap_gmg_datalake.bronze.atp_detail a
  WHERE a.storage_location = 'SL01' AND a.c_file_date = (SELECT d FROM atp_snap)
    AND a.site_code IN (SELECT store_id FROM stores)
  GROUP BY a.site_code, a.article_code
),
it_snap AS (   -- combined_inventory is a DAILY SNAPSHOT of the in-transit LEVEL (1 row/plant/material/day),
               -- not a per-shipment feed. Take the latest snapshot within the lookback window.
  SELECT MAX(to_date(date)) d FROM prod_mena.silver.combined_inventory
  WHERE stock_type = 'sports'
    AND to_date(date) BETWEEN date_sub(DATE'{ATP_AS_OF}', {LOOKBACK}) AND DATE'{ATP_AS_OF}'
),
intransit AS (   -- inbound in-transit LEVEL as of the latest snapshot. SUMMING the window double-counts
                 -- units that have since been received (they leave in-transit and reappear in ATP).
  SELECT plant AS store_id, material AS article_code, SUM(Stock_On_Hand_Quantity) AS intransit_qty
  FROM prod_mena.silver.combined_inventory
  WHERE stock_type = 'sports' AND (stor_loc IS NULL OR stor_loc = '')
    AND to_date(date) = (SELECT d FROM it_snap)
    AND plant IN (SELECT store_id FROM stores)
  GROUP BY plant, material
),
eff AS (   -- universe = ATP (sellable) rows; effective_stock = ATP ONLY. in-transit is carried for
           -- DISPLAY only and is NOT used in any calculation (cover, donor_can_give, receiver_can_take).
  SELECT p.store_id, p.article_code,
         p.atp_qty,
         COALESCE(i.intransit_qty, 0) AS intransit_qty,
         p.atp_qty AS effective_stock,
         p.unit_cost_aed
  FROM atp p LEFT JOIN intransit i
    ON p.store_id = i.store_id AND p.article_code = i.article_code
)
SELECT s.country, e.store_id, s.store_name, s.bu, s.store_grade,
       d.generic_article, e.article_code, d.product_size,
       e.atp_qty, e.intransit_qty, e.effective_stock, e.unit_cost_aed,
       DATE'{AS_OF}' AS as_of_date
FROM eff e
JOIN stores  s ON e.store_id     = s.store_id
JOIN art_dim d ON e.article_code = d.article_code
WHERE 1 = 1 {in_scope('e.article_code')}
""")

# COMMAND ----------
# WAREHOUSE STOCK (point 2, 2026-07-28) — the country DC's sellable ATP per article. Used as a GUARD:
# consolidation must NOT recommend a size the DC still holds (replenishment DC->store fulfils it). One DC per
# country via `cfg_country_dc` (deterministic allow-list; a bare ZBU='COMMON' filter is NOT 1-per-country).
# EG has no DC -> no rows -> guard passes through (nothing to replenish from). Same frozen snapshot as ATP.
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.cfg_country_dc AS
SELECT * FROM VALUES
  ('AE','1D00','SSS UAE DC'), ('SA','2D22','SSS RY DC KSA'), ('BH','3D31','SSS - DC BAH'),
  ('KW','3D41','SSS - DC KWT'), ('QA','3D51','SSS - DC QTR'), ('OM','3D61','SSS - DC OMN')
  AS t(country, dc_site, dc_name)
""")
# UNIFIED DC MAP (business 2026-09-22) — ONE map for BOTH the consolidation warehouse guard AND replenishment.
# tier 1 = primary, tier 2 = fallback; pool_id groups sites into ONE shared pool: HUB = 1D00 + 1D01 (UAE/KWT/OMN/QTR
# primary, KSA/BAH fallback), KSA = 2D22, BAH = 3D31. (Was cfg_country_dc = one LOCAL DC per country, 1D00 only for
# UAE -> the 1D01 blind spot that recommended store->store moves despite hub stock.)
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.cfg_replen_dc AS
SELECT * FROM VALUES
  ('AE','1D00','SSS UAE DC','HUB',1),      ('AE','1D01','SSS UAE DC (1D01)','HUB',1),
  ('KW','1D00','SSS UAE DC','HUB',1),      ('KW','1D01','SSS UAE DC (1D01)','HUB',1),
  ('OM','1D00','SSS UAE DC','HUB',1),      ('OM','1D01','SSS UAE DC (1D01)','HUB',1),
  ('QA','1D00','SSS UAE DC','HUB',1),      ('QA','1D01','SSS UAE DC (1D01)','HUB',1),
  ('SA','2D22','SSS RY DC KSA','KSA',1),   ('SA','1D00','SSS UAE DC','HUB',2), ('SA','1D01','SSS UAE DC (1D01)','HUB',2),
  ('BH','3D31','SSS - DC BAH','BAH',1),    ('BH','1D00','SSS UAE DC','HUB',2), ('BH','1D01','SSS UAE DC (1D01)','HUB',2)
  AS t(country, dc_site, dc_name, pool_id, tier)
""")
# Shared pool inventory: ATP summed per (pool, article) — HUB sums 1D00+1D01 ONCE (no double-count across countries).
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.dc_pool_stock AS
WITH atp_snap AS (SELECT MAX(c_file_date) d FROM sap_gmg_datalake.bronze.atp_detail WHERE c_file_date <= DATE'{ATP_AS_OF}'),
pools AS (SELECT DISTINCT dc_site, pool_id FROM {FQ}.cfg_replen_dc)
SELECT pl.pool_id, a.article_code, SUM(a.atp_qty) AS pool_atp
FROM sap_gmg_datalake.bronze.atp_detail a
JOIN pools pl ON pl.dc_site = a.site_code
WHERE a.storage_location = 'SL01' AND a.c_file_date = (SELECT d FROM atp_snap)
GROUP BY pl.pool_id, a.article_code
HAVING SUM(a.atp_qty) > 0
""")
# warehouse_stock = DC stock REACHABLE by each country (its primary + hub pools) per article — the guard's "DC has it"
# set. Boolean availability for the guard, so summing reachable pools (incl. shared hub) per country is fine.
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.warehouse_stock AS
SELECT dc.country, ps.article_code, SUM(ps.pool_atp) AS wh_atp
FROM (SELECT DISTINCT country, pool_id FROM {FQ}.cfg_replen_dc) dc
JOIN {FQ}.dc_pool_stock ps ON ps.pool_id = dc.pool_id
GROUP BY dc.country, ps.article_code
HAVING SUM(ps.pool_atp) > 0
""")

# ATP freshness stamp for the UI — the snapshot date this run used + when that snapshot was loaded upstream
# (etl_timetsamp on atp_detail). Surfaced in the app so users can see how current the stock is.
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.atp_snapshot_info AS
WITH s AS (SELECT MAX(c_file_date) d FROM sap_gmg_datalake.bronze.atp_detail WHERE c_file_date <= DATE'{ATP_AS_OF}')
SELECT (SELECT d FROM s) AS atp_snapshot_date,
       (SELECT MAX(etl_timetsamp) FROM sap_gmg_datalake.bronze.atp_detail WHERE c_file_date = (SELECT d FROM s)) AS atp_updated_at,
       DATE'{AS_OF}' AS as_of_date, current_timestamp() AS captured_at
""")

# STORE IN-TRANSIT (standalone) — the inbound in-transit LEVEL per Nike-BU store × article at the latest
# snapshot. effective_stock carries intransit only on ATP>0 rows; the out-of-stock (ATP=0) latent-demand
# receivers in 03 need intransit too (point 4: receiver counts inbound), so emit it as its own table.
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.store_intransit AS
WITH stores AS (   -- restricted to the active store list
  SELECT sm.PLANT AS store_id FROM prod_mena.gold.mena_sports_store_master_view sm
  JOIN {FQ}.cfg_store_grade csg ON csg.store_code = sm.PLANT
  WHERE sm.PLANTCAT = 'A' AND sm.`/BIC/ZBU` = 'NIKE' {store_country}
),
it_snap AS (
  SELECT MAX(to_date(date)) d FROM prod_mena.silver.combined_inventory
  WHERE stock_type = 'sports'
    AND to_date(date) BETWEEN date_sub(DATE'{ATP_AS_OF}', {LOOKBACK}) AND DATE'{ATP_AS_OF}'
)
SELECT plant AS store_id, material AS article_code, SUM(Stock_On_Hand_Quantity) AS intransit_qty
FROM prod_mena.silver.combined_inventory
WHERE stock_type = 'sports' AND (stor_loc IS NULL OR stor_loc = '')
  AND to_date(date) = (SELECT d FROM it_snap)
  AND plant IN (SELECT store_id FROM stores)
  {in_scope('material')}
GROUP BY plant, material
""")

# COMMAND ----------
display(spark.sql(f"""
  SELECT country, COUNT(*) rows, COUNT(DISTINCT store_id) stores,
         COUNT(DISTINCT article_code) articles, ROUND(SUM(effective_stock),0) total_effective
  FROM {FQ}.effective_stock GROUP BY country ORDER BY total_effective DESC
"""))
