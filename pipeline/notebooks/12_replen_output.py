# Databricks notebook source
# MAGIC %md
# MAGIC # 12 · Replenishment Output — recommendations / not-replenished / stats (P1, additive)
# MAGIC
# MAGIC Shapes the review-ready outputs (recommend-only; the app reads these):
# MAGIC - **`replen_recommendations`** — actionable DC→store lines (`rationed_qty ≥ 1`), tagged reason
# MAGIC   (CENSORED / DISPLAY / DEPLETION) + recurrence propensity (chain breadth) + AED value.
# MAGIC - **`replen_not_replenished`** — the completeness view: real need we did NOT fill, with cause
# MAGIC   (NO_DC_STOCK = allocation/buy problem · DC_SHORTFALL_OR_BLOCKED = rationed out / APO).
# MAGIC - **`replen_stats`** — one headline row for the run log.

# COMMAND ----------
CATALOG = "sports_analytics_test"; SCHEMA = "nike_consolidation"; FQ = f"{CATALOG}.{SCHEMA}"
rc = {r["param_name"]: r["param_value"] for r in spark.table(f"{FQ}.run_config").collect()}
AS_OF = rc["AS_OF_DATE"]
REVIEW_TAG = "Review - moving to make full size"

# Parent (generic article) name without the size — product master MATL_CAT='01' row of RT_CONFMAT; fallback (~2.5%
# of parents have no 01 row/name) = child name with the trailing ", <size>" stripped. Display only (business 2026-10-09).
PNAME_CTE = """pname AS (
  SELECT ch.generic_article, COALESCE(pa.parent_name, ch.stripped_name) AS generic_article_name
  FROM (SELECT RT_CONFMAT AS generic_article, MAX(TRIM(regexp_replace(TXTMD, ',[^,]*$', ''))) AS stripped_name
        FROM prod_mena.gold.mena_sports_product_master_view WHERE MATL_CAT = '02' GROUP BY RT_CONFMAT) ch
  LEFT JOIN (SELECT MATERIAL, MAX(NULLIF(TRIM(TXTMD), '')) AS parent_name
             FROM prod_mena.gold.mena_sports_product_master_view WHERE MATL_CAT = '01' GROUP BY MATERIAL) pa
    ON pa.MATERIAL = ch.generic_article
)"""

# COMMAND ----------
# recurrence propensity prior = chain breadth (# stores demanding the SKU) — known in advance, predicts repeat demand.
# Lines come from `replen_final` (08 joint receiver guard: lines that leave the receiver cut-size are held,
# final_qty = 0) PLUS `fullsize_topup` (1 unit per missing key size so the receiver becomes full-size; current
# season / CORE only). Both carry decision 'Review - moving to make full size' when the pair was topped up.
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.replen_recommendations AS
WITH {PNAME_CTE},
breadth AS (SELECT article_code, COUNT(DISTINCT store_id) AS n_stores FROM {FQ}.replen_demand GROUP BY article_code),
cat AS (   -- business category via the category converter (1 row/article)
  SELECT p.MATERIAL AS article_code, MAX(cc.business_category) AS business_category
  FROM prod_mena.gold.mena_sports_product_master_view p
  JOIN {FQ}.cfg_category_converter cc
    ON UPPER(cc.converter_key) = UPPER(CONCAT(COALESCE(p.RF_BNDID,''), COALESCE(p.`/BIC/ZRWGH3_T`,''),
         COALESCE(p.`/BIC/ZCHWGH5`,''), COALESCE(p.`/BIC/ZRWGH2_T`,'')))
  WHERE p.MATL_CAT = '02' GROUP BY p.MATERIAL),
gdr AS (SELECT MATERIAL AS article_code, MAX(TRIM(`/BIC/ZRWGH2_T`)) AS division
        FROM prod_mena.gold.mena_sports_product_master_view WHERE MATL_CAT = '02' GROUP BY MATERIAL),
pn AS (SELECT country, MAX(CASE WHEN tier = 1 AND pool_id <> 'HUB' THEN dc_name END) AS dc_name
       FROM {FQ}.cfg_replen_dc GROUP BY country),
cp AS (SELECT country, MAX(CASE WHEN tier = 1 AND pool_id <> 'HUB' THEN pool_id END) AS primary_pool,
              MAX(CASE WHEN pool_id = 'HUB' THEN 1 ELSE 0 END) AS draws_hub
       FROM {FQ}.cfg_replen_dc GROUP BY country),
lines AS (
  SELECT a.country, a.generic_article, a.article_code, a.product_size, a.store_id, a.alloc_primary, a.alloc_hub,
         a.wh_atp, a.need_units, a.final_qty AS qty,
         CASE WHEN a.ros_censored_flag = 1 THEN 'REPLEN_CENSORED'
              WHEN a.display_floor >= a.demand_rate_day * a.protection_window THEN 'REPLEN_DISPLAY'
              ELSE 'REPLEN_DEPLETION' END AS reason,
         a.demand_rate_day, a.protection_window, a.display_floor, a.effective_stock, a.intransit_qty,
         a.net_per_unit_aed, a.value_at_risk_aed, a.guard_decision AS decision
  FROM {FQ}.replen_final a WHERE a.final_qty >= 1
  UNION ALL
  SELECT t.country, t.generic_article, t.article_code, t.product_size, t.store_id, t.alloc_primary, t.alloc_hub,
         COALESCE(pp.pool_atp, 0) + CASE WHEN cp.draws_hub = 1 THEN COALESCE(hb.pool_atp, 0) ELSE 0 END AS wh_atp,
         CAST(t.topup_qty AS DOUBLE) AS need_units, t.topup_qty AS qty, 'REPLEN_FULL_SIZE' AS reason,
         COALESCE(d.demand_rate_day, 0) AS demand_rate_day, CAST(NULL AS INT) AS protection_window, 1 AS display_floor,
         COALESCE(es.effective_stock, 0) AS effective_stock, COALESCE(it.intransit_qty, 0) AS intransit_qty,
         d.net_per_unit_aed, CAST(0 AS DOUBLE) AS value_at_risk_aed, '{REVIEW_TAG}' AS decision
  FROM {FQ}.fullsize_topup t
  LEFT JOIN cp ON cp.country = t.country
  LEFT JOIN {FQ}.dc_pool_stock pp ON pp.pool_id = cp.primary_pool AND pp.article_code = t.article_code
  LEFT JOIN {FQ}.dc_pool_stock hb ON hb.pool_id = 'HUB' AND hb.article_code = t.article_code
  LEFT JOIN {FQ}.replen_demand d ON d.store_id = t.store_id AND d.article_code = t.article_code
  LEFT JOIN {FQ}.effective_stock es ON es.store_id = t.store_id AND es.article_code = t.article_code
  LEFT JOIN {FQ}.store_intransit it ON it.store_id = t.store_id AND it.article_code = t.article_code
)
SELECT a.country, c.business_category AS category, g.division,
       a.generic_article AS parent_article, pnm.generic_article_name, a.article_code AS article, a.product_size AS size,
       a.store_id AS receiver_store,
       CASE WHEN a.alloc_primary > 0 AND a.alloc_hub > 0 THEN COALESCE(pn.dc_name, 'primary DC') || ' + UAE hub'
            WHEN a.alloc_hub > 0 THEN 'UAE hub (1D00/1D01)'
            ELSE COALESCE(pn.dc_name, a.country || ' DC') END AS source,
       a.wh_atp AS source_available,
       ROUND(a.need_units, 2) AS store_need, a.qty AS recommended_qty,
       a.reason,
       a.demand_rate_day, a.protection_window, a.display_floor AS presentation_floor,
       a.effective_stock AS store_atp, (a.effective_stock + a.qty) AS store_atp_after,
       a.intransit_qty AS store_intransit,
       a.net_per_unit_aed AS unit_price_aed, a.value_at_risk_aed,
       CASE WHEN b.n_stores >= 50 THEN 'HIGH' WHEN b.n_stores >= 10 THEN 'MED'
            WHEN b.n_stores >= 3 THEN 'LOW' ELSE 'THIN' END AS recurrence_propensity,
       'DC_REPLEN' AS source_type, a.decision, DATE'{AS_OF}' AS as_of_date
FROM lines a
LEFT JOIN breadth b ON b.article_code = a.article_code
LEFT JOIN pn ON pn.country = a.country
LEFT JOIN cat c ON c.article_code = a.article_code
LEFT JOIN gdr g ON g.article_code = a.article_code
LEFT JOIN pname pnm ON pnm.generic_article = a.generic_article
""")

# COMMAND ----------
# Completeness: real need NOT fulfilled by the DC — the "where else" story (allocation / buy / consolidation).
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.replen_not_replenished AS
SELECT d.country, d.generic_article AS parent_article, d.article_code AS article, d.product_size AS size,
       d.store_id AS receiver_store, d.store_grade, ROUND(d.need_units, 2) AS store_need, d.wh_atp,
       COALESCE(al.rationed_qty, 0) AS rationed_qty,
       CASE WHEN al.guard_action = 'HOLD_RECV_CUT' THEN 'HELD_RECEIVER_STAYS_CUT'   -- 08 joint receiver guard
            WHEN mb.store_id IS NOT NULL THEN 'MOVE_BACK_30D'                       -- article left this store <30d
            WHEN d.wh_atp = 0 THEN 'NO_DC_STOCK' ELSE 'DC_SHORTFALL_OR_BLOCKED' END AS not_replenished_reason,
       ROUND(d.value_at_risk, 0) AS value_at_risk_aed, DATE'{AS_OF}' AS as_of_date
FROM (SELECT *, ROUND(demand_rate_day * 365 * net_per_unit_aed, 0) AS value_at_risk FROM {FQ}.replen_demand) d
LEFT JOIN {FQ}.replen_final al ON al.store_id = d.store_id AND al.article_code = d.article_code
LEFT JOIN {FQ}.moveback_block mb ON mb.store_id = d.store_id AND mb.article_code = d.article_code
LEFT ANTI JOIN {FQ}.fullsize_topup t ON t.store_id = d.store_id AND t.article_code = d.article_code
WHERE d.need_units > 0 AND COALESCE(al.final_qty, 0) = 0
""")

# COMMAND ----------
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.replen_stats AS
SELECT
  (SELECT COUNT(*) FROM {FQ}.replen_recommendations) AS replen_lines,
  (SELECT COALESCE(SUM(recommended_qty),0) FROM {FQ}.replen_recommendations) AS replen_units,
  (SELECT COUNT(DISTINCT receiver_store) FROM {FQ}.replen_recommendations) AS stores,
  (SELECT COUNT(DISTINCT article) FROM {FQ}.replen_recommendations) AS skus,
  (SELECT ROUND(SUM(recommended_qty * unit_price_aed),0) FROM {FQ}.replen_recommendations) AS fill_value_aed,
  (SELECT COUNT(*) FROM {FQ}.replen_not_replenished WHERE not_replenished_reason='NO_DC_STOCK') AS no_dc_stock_lines,
  (SELECT COUNT(*) FROM {FQ}.replen_not_replenished WHERE not_replenished_reason='DC_SHORTFALL_OR_BLOCKED') AS shortfall_or_blocked_lines,
  (SELECT COUNT(*) FROM {FQ}.replen_not_replenished WHERE not_replenished_reason='HELD_RECEIVER_STAYS_CUT') AS held_receiver_cut_lines,
  (SELECT COALESCE(SUM(rationed_qty),0) FROM {FQ}.replen_final WHERE guard_action='HOLD_RECV_CUT') AS held_receiver_cut_units,
  (SELECT COUNT(*) FROM {FQ}.replen_not_replenished WHERE not_replenished_reason='MOVE_BACK_30D') AS moveback_blocked_lines,
  (SELECT COUNT(*) FROM {FQ}.fullsize_topup) AS fullsize_topup_units,
  (SELECT COUNT(DISTINCT store_id || '|' || generic_article) FROM {FQ}.fullsize_topup) AS fullsize_topup_pairs,
  DATE'{AS_OF}' AS as_of_date
""")

# COMMAND ----------
display(spark.sql(f"SELECT reason, COUNT(*) lines, SUM(recommended_qty) units, ROUND(SUM(value_at_risk_aed)/1e6,2) value_M FROM {FQ}.replen_recommendations GROUP BY reason ORDER BY reason"))
display(spark.table(f"{FQ}.replen_stats"))
