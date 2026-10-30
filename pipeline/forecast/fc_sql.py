"""SQL used by the CORE forecast + allocation pipeline (dev). Shared by the Databricks notebook and local tests."""
FQ = "sports_analytics_test.nike_consolidation"
PM = "prod_mena.gold.mena_sports_product_master_view"
SM = "prod_mena.gold.mena_sports_store_master_view"
SALES = "sap_gmg_datalake.silver.fct_sap_sales_detail_v"

_CORE = f"""core AS (SELECT p.MATERIAL art, MAX(p.RT_CONFMAT) parent, MAX(TRIM(p.`/BIC/ZRWGH2_T`)) division,
                       MAX(TRIM(p.`/BIC/ZCHWGH5`)) gender, MAX(TRIM(p.CM_CDT3_TXTMD)) category,
                       MAX(CASE WHEN TRIM(p.RT_SIZE) = '-' THEN '0.5' ELSE TRIM(p.RT_SIZE) END) size
              FROM {PM} p WHERE p.MATL_CAT = '02' AND UPPER(TRIM(p.RF_SEASON)) IN ('CORE', 'YEAR')
                AND p.MATERIAL LIKE 'NK%' GROUP BY p.MATERIAL),
nike AS (SELECT PLANT store, MAX(COUNTRY) country FROM {SM} WHERE PLANTCAT = 'A' AND `/BIC/ZBU` = 'NIKE' GROUP BY PLANT),
bounds AS (SELECT date_sub(date_trunc('week', current_date()), 1) AS last_day)"""

# weekly style x country history, 78 full weeks ending last Sunday (retail + ecom)
WEEKLY = f"""WITH {_CORE}
SELECT n.country, c.parent, MAX(c.division) division, MAX(c.gender) gender, MAX(c.category) category,
       CAST(date_trunc('week', s.txn_date) AS DATE) wk, SUM(GREATEST(s.txn_qty, 0)) units
FROM {SALES} s JOIN core c ON c.art = s.variant_article_code JOIN nike n ON n.store = s.site_code
WHERE s.distr_channel IN ('10', '20')
  AND s.txn_date > date_sub((SELECT last_day FROM bounds), 7 * 78) AND s.txn_date <= (SELECT last_day FROM bounds)
GROUP BY n.country, c.parent, CAST(date_trunc('week', s.txn_date) AS DATE)"""

# store x size sales, last 26 full weeks — store share of style and size curve
STORE_SIZE = f"""WITH {_CORE}
SELECT n.country, s.site_code store, c.parent, c.art, c.size, SUM(GREATEST(s.txn_qty, 0)) units
FROM {SALES} s JOIN core c ON c.art = s.variant_article_code JOIN nike n ON n.store = s.site_code
WHERE s.distr_channel IN ('10', '20')
  AND s.txn_date > date_sub((SELECT last_day FROM bounds), 7 * 26) AND s.txn_date <= (SELECT last_day FROM bounds)
GROUP BY 1, 2, 3, 4, 5 HAVING SUM(GREATEST(s.txn_qty, 0)) > 0"""

# active stores (store list) with grade + today's stock / in-transit of CORE sizes + key-size flag
STOCK = f"""WITH {_CORE}
SELECT e.country, e.store_id store, MAX(e.store_name) store_name, MAX(e.store_grade) grade, c.parent, c.art, MAX(c.size) size,
       SUM(e.effective_stock) atp, MAX(COALESCE(it.intransit_qty, 0)) intransit, MAX(COALESCE(ak.is_key_size, 0)) key_size
FROM {FQ}.effective_stock e JOIN core c ON c.art = e.article_code
LEFT JOIN {FQ}.store_intransit it ON it.store_id = e.store_id AND it.article_code = e.article_code
LEFT JOIN {FQ}.art_size_key ak ON ak.article_code = e.article_code
GROUP BY e.country, e.store_id, c.parent, c.art"""

STORES = f"""SELECT DISTINCT country, store_id store, store_name, store_grade grade FROM {FQ}.effective_stock"""

KEY_SIZES = f"""WITH {_CORE} SELECT c.parent, c.art, c.size, MAX(COALESCE(ak.is_key_size, 0)) key_size
FROM core c LEFT JOIN {FQ}.art_size_key ak ON ak.article_code = c.art GROUP BY 1, 2, 3"""

DC = f"""SELECT d.pool_id, d.article_code art, d.pool_atp FROM {FQ}.dc_pool_stock d WHERE d.pool_atp > 0 AND d.article_code LIKE 'NK%'"""
DC_MAP = f"""SELECT country, MAX(CASE WHEN tier = 1 AND pool_id <> 'HUB' THEN pool_id END) primary_pool,
                    MAX(CASE WHEN pool_id = 'HUB' THEN 1 ELSE 0 END) draws_hub FROM {FQ}.cfg_replen_dc GROUP BY country"""

# today's engine DC replen for CORE articles (comparison with the forecast-based plan)
CURRENT_REPLEN = f"""WITH {_CORE}
SELECT r.receiver_store store, r.article art, r.recommended_qty qty FROM {FQ}.replen_recommendations r JOIN core c ON c.art = r.article"""
