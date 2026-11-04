"""SQL used by the CORE forecast + allocation pipeline (dev). Shared by the Databricks notebook and local tests."""
FQ = "sports_analytics_test.nike_consolidation"
PM = "prod_mena.gold.mena_sports_product_master_view"
SM = "prod_mena.gold.mena_sports_store_master_view"
SALES = "sap_gmg_datalake.silver.fct_sap_sales_detail_v"

_CORE = f"""core AS (SELECT p.MATERIAL art, MAX(p.RT_CONFMAT) parent, MAX(TRIM(p.`/BIC/ZRWGH2_T`)) division,
                       MAX(TRIM(p.`/BIC/ZCHWGH5`)) gender, MAX(TRIM(p.CM_CDT3_TXTMD)) category, MAX(TRIM(p.`/BIC/ZRWGH3_T`)) sport,
                       MAX(CASE WHEN TRIM(p.RT_SIZE) = '-' THEN '0.5' ELSE TRIM(p.RT_SIZE) END) size
              FROM {PM} p WHERE p.MATL_CAT = '02' AND UPPER(TRIM(p.RF_SEASON)) IN ('CORE', 'YEAR')
                AND p.MATERIAL LIKE 'NK%' GROUP BY p.MATERIAL),
nike AS (SELECT PLANT store, MAX(COUNTRY) country FROM {SM} WHERE PLANTCAT = 'A' AND `/BIC/ZBU` = 'NIKE' GROUP BY PLANT),
bounds AS (SELECT date_sub(date_trunc('week', current_date()), 1) AS last_day)"""

# weekly style x country history: all CORE history (from 4 Mar 2024) to last Sunday (retail + ecom)
WEEKLY = f"""WITH {_CORE}
SELECT n.country, c.parent, MAX(c.division) division, MAX(c.gender) gender, MAX(c.category) category,
       CAST(date_trunc('week', s.txn_date) AS DATE) wk, SUM(GREATEST(s.txn_qty, 0)) units
FROM {SALES} s JOIN core c ON c.art = s.variant_article_code JOIN nike n ON n.store = s.site_code
WHERE s.distr_channel IN ('10', '20')
  AND s.txn_date >= DATE'2024-03-04' AND s.txn_date <= (SELECT last_day FROM bounds)
GROUP BY n.country, c.parent, CAST(date_trunc('week', s.txn_date) AS DATE)"""

# store x size sales, last 26 full weeks — store share of style and size curve
STORE_SIZE = f"""WITH {_CORE}
SELECT n.country, s.site_code store, c.parent, c.art, c.size, SUM(GREATEST(s.txn_qty, 0)) units
FROM {SALES} s JOIN core c ON c.art = s.variant_article_code JOIN nike n ON n.store = s.site_code
WHERE s.distr_channel IN ('10', '20')
  AND s.txn_date > date_sub((SELECT last_day FROM bounds), 7 * 26) AND s.txn_date <= (SELECT last_day FROM bounds)
GROUP BY 1, 2, 3, 4, 5 HAVING SUM(GREATEST(s.txn_qty, 0)) > 0"""

# every Nike retail store in the store master (not only the consolidation engine's uploaded store list): today's ATP
# (latest snapshot, SL01) + in-transit (latest combined_inventory snapshot) of CORE sizes + key-size flag
STOCK = f"""WITH {_CORE},
atp_snap AS (SELECT MAX(c_file_date) d FROM sap_gmg_datalake.bronze.atp_detail WHERE c_file_date <= current_date()),
atp AS (SELECT a.site_code store, a.article_code art, SUM(a.atp_qty) atp FROM sap_gmg_datalake.bronze.atp_detail a
        JOIN core c ON c.art = a.article_code JOIN nike n ON n.store = a.site_code
        WHERE a.storage_location = 'SL01' AND a.c_file_date = (SELECT d FROM atp_snap) GROUP BY 1, 2),
it_snap AS (SELECT MAX(to_date(date)) d FROM prod_mena.silver.combined_inventory
            WHERE stock_type = 'sports' AND to_date(date) BETWEEN date_sub(current_date(), 7) AND current_date()),
it AS (SELECT i.plant store, i.material art, SUM(i.Stock_On_Hand_Quantity) intransit FROM prod_mena.silver.combined_inventory i
       JOIN core c ON c.art = i.material JOIN nike n ON n.store = i.plant
       WHERE i.stock_type = 'sports' AND (i.stor_loc IS NULL OR i.stor_loc = '') AND to_date(i.date) = (SELECT d FROM it_snap)
       GROUP BY 1, 2),
s AS (SELECT COALESCE(atp.store, it.store) store, COALESCE(atp.art, it.art) art,
             COALESCE(atp.atp, 0) atp, COALESCE(it.intransit, 0) intransit
      FROM atp FULL OUTER JOIN it ON it.store = atp.store AND it.art = atp.art)
SELECT n.country, s.store, c.parent, s.art, MAX(c.size) size, SUM(s.atp) atp, SUM(s.intransit) intransit,
       MAX(COALESCE(ak.is_key_size, 0)) key_size
FROM s JOIN core c ON c.art = s.art JOIN nike n ON n.store = s.store
LEFT JOIN {FQ}.art_size_key ak ON ak.article_code = s.art
WHERE n.country <> ''
GROUP BY n.country, s.store, c.parent, s.art"""

# all Nike retail stores; grade from the uploaded store list where the store is on it, else the store master grade
STORES = f"""SELECT sm.COUNTRY country, sm.PLANT store, MAX(sm.`/BIC/ZNAME1`) store_name,
       MAX(COALESCE(csg.grade, CASE WHEN TRIM(sm.`/BIC/ZCH_CL_GR`) IN ('', 'NA', 'N-A') OR sm.`/BIC/ZCH_CL_GR` IS NULL
                                    THEN 'UNGRADED' ELSE TRIM(sm.`/BIC/ZCH_CL_GR`) END)) grade,
       MAX(CASE WHEN csg.store_code IS NOT NULL THEN 1 ELSE 0 END) on_store_list
FROM {SM} sm LEFT JOIN {FQ}.cfg_store_grade csg ON csg.store_code = sm.PLANT
WHERE sm.PLANTCAT = 'A' AND sm.`/BIC/ZBU` = 'NIKE' AND sm.COUNTRY <> ''
GROUP BY sm.COUNTRY, sm.PLANT"""

# every CORE article with its size, key-size flag and the product attributes the store / size clusters are built from
# style name = the parent's own product-master name (MATL_CAT 01), else a child name with the trailing ", <size>" cut
KEY_SIZES = f"""WITH {_CORE},
pname AS (SELECT ch.parent, COALESCE(pa.name, ch.stripped) style_name
          FROM (SELECT RT_CONFMAT parent, MAX(TRIM(regexp_replace(TXTMD, ',[^,]*$', ''))) stripped FROM {PM}
                WHERE MATL_CAT = '02' GROUP BY RT_CONFMAT) ch
          LEFT JOIN (SELECT MATERIAL, MAX(NULLIF(TRIM(TXTMD), '')) name FROM {PM} WHERE MATL_CAT = '01' GROUP BY MATERIAL) pa
            ON pa.MATERIAL = ch.parent)
SELECT c.parent, c.art, c.size, MAX(pn.style_name) style_name, MAX(c.division) division, MAX(c.gender) gender,
       MAX(c.sport) sport, MAX(c.category) category, MAX(COALESCE(ak.is_key_size, 0)) key_size
FROM core c LEFT JOIN {FQ}.art_size_key ak ON ak.article_code = c.art LEFT JOIN pname pn ON pn.parent = c.parent
GROUP BY 1, 2, 3"""

DC = f"""SELECT d.pool_id, d.article_code art, d.pool_atp FROM {FQ}.dc_pool_stock d WHERE d.pool_atp > 0 AND d.article_code LIKE 'NK%'"""
DC_MAP = f"""SELECT country, MAX(CASE WHEN tier = 1 AND pool_id <> 'HUB' THEN pool_id END) primary_pool,
                    MAX(CASE WHEN pool_id = 'HUB' THEN 1 ELSE 0 END) draws_hub FROM {FQ}.cfg_replen_dc GROUP BY country"""

# today's engine DC replen for CORE articles (comparison with the forecast-based plan)
CURRENT_REPLEN = f"""WITH {_CORE}
SELECT r.receiver_store store, r.article art, r.recommended_qty qty FROM {FQ}.replen_recommendations r JOIN core c ON c.art = r.article"""

# weekly availability per style x country: number of Nike stores holding any stock of the style on the
# Wednesday snapshot (ATP history starts 2025-01-20). Used to gross up sales in weeks with stock-outs.
AVAIL = f"""WITH {_CORE}
SELECT n.country, c.parent, CAST(date_trunc('week', a.c_file_date) AS DATE) wk, COUNT(DISTINCT a.site_code) stores_in_stock
FROM sap_gmg_datalake.bronze.atp_detail a JOIN core c ON c.art = a.article_code JOIN nike n ON n.store = a.site_code
WHERE a.storage_location = 'SL01' AND dayofweek(a.c_file_date) = 4 AND a.atp_qty > 0
  AND a.c_file_date >= DATE'2025-01-20' AND a.c_file_date <= (SELECT last_day FROM bounds)
GROUP BY 1, 2, 3"""

# product image + attributes per style, from Akeneo (akeneo_gmg_datalake.gold.product_images — the only image source,
# business decision 2026-11-04). gmg_fluent_image is the most reliable link; the other columns are fallbacks.
# Colour is normalised to one family ("BLACK/WHITE" -> "Black"). Copied into the project schema so app viewers need
# no Akeneo access.
IMAGES = f"""CREATE OR REPLACE TABLE {FQ}.dim_product_image
COMMENT 'Style image link + colour / franchise / price from Akeneo product_images (refreshed by the forecast run)' AS
WITH a AS (
  SELECT sap_generic_article parent,
         coalesce(nullif(trim(gmg_fluent_image), ''), nullif(trim(gmg_fluent_image_new), ''),
                  nullif(trim(gmg_fluent_image_new_1), ''), nullif(trim(fluent_image), '')) url,
         initcap(trim(regexp_replace(split(nullif(trim(color), ''), '/')[0], '[()]', ''))) colour,
         nullif(trim(gmg_franchise), '') franchise, brand, try_cast(current_srp AS DOUBLE) srp, online_from
  FROM akeneo_gmg_datalake.gold.product_images
  WHERE sap_generic_article LIKE 'NK%')
SELECT parent, MAX_BY(url, url IS NOT NULL) url,
       CASE WHEN MAX(colour) IN ('Multi', 'Multicolour', 'Multi-colour') THEN 'Multi-Colour' ELSE MAX(colour) END colour,
       MAX(franchise) franchise, MAX(brand) brand, MAX(srp) srp, MIN(online_from) online_from, current_timestamp() refreshed_at
FROM a GROUP BY parent"""
