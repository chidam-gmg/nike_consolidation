"""SQL for the new-article (launch) forecast. Shared by the Databricks notebook and local tests."""
FQ = "sports_analytics_test.nike_consolidation"
PM = "prod_mena.gold.mena_sports_product_master_view"
SM = "prod_mena.gold.mena_sports_store_master_view"
SALES = "sap_gmg_datalake.silver.fct_sap_sales_detail_v"
TARGET_SEASON, TARGET_YEAR = "SPRG", "2027"

_ART = f"""art AS (SELECT MATERIAL art, MAX(RT_CONFMAT) parent FROM {PM} WHERE MATL_CAT = '02' AND MATERIAL LIKE 'NK%' GROUP BY MATERIAL),
nike AS (SELECT PLANT store, MAX(COUNTRY) country FROM {SM} WHERE PLANTCAT = 'A' AND `/BIC/ZBU` = 'NIKE' AND COUNTRY <> '' GROUP BY PLANT)"""

# Launch history: units per style x country x week since the style first sold anywhere (weeks 0-12), retail + ecom.
# Styles whose first sale is from 1 Jun 2024 (data starts Mar 2024, so earlier "first sales" may be old styles)
# up to 13 weeks before the last full week (so all 13 weeks are observed).
LAUNCH = f"""WITH {_ART},
s AS (SELECT a.parent, n.country, s.site_code store, s.txn_date d, GREATEST(s.txn_qty, 0) q FROM {SALES} s
      JOIN art a ON a.art = s.variant_article_code JOIN nike n ON n.store = s.site_code
      WHERE s.distr_channel IN ('10', '20') AND s.txn_date >= DATE'2024-03-01'),
f AS (SELECT parent, MIN(d) first_sale FROM s GROUP BY parent
      HAVING MIN(d) >= DATE'2024-06-01' AND MIN(d) <= date_sub(date_trunc('week', current_date()), 7 * 14))
SELECT s.parent, s.country, f.first_sale, CAST(floor(datediff(s.d, f.first_sale) / 7) AS INT) wk,
       SUM(s.q) units, COUNT(DISTINCT s.store) stores
FROM s JOIN f USING (parent) WHERE s.d < date_add(f.first_sale, 91)
GROUP BY s.parent, s.country, f.first_sale, CAST(floor(datediff(s.d, f.first_sale) / 7) AS INT)"""

# Attributes for every Nike style (history and the target season)
ATTRS = f"""SELECT RT_CONFMAT parent, MAX(split(RT_CONFMAT, '-')[0]) model, MAX(get(split(RT_CONFMAT, '-'), 1)) colourway,
       MAX(TRIM(`/BIC/ZRWGH2_T`)) division, MAX(TRIM(CM_CDT3_TXTMD)) ah2, MAX(TRIM(`/BIC/ZCHWGH5`)) gender,
       MAX(TRIM(`/BIC/ZRWGH3_T`)) sport, MAX(TRIM(CM_CDT4_TXTMD)) silhouette, MAX(Global_price) rrp,
       MAX(TRIM(`/BIC/ZZPLC_TXTMD`)) plc, MAX(upper(trim(`/BIC/ZCHSEASON`))) season, MAX(`/BIC/ZCHSEASYR`) season_year,
       MAX(regexp_replace(TXTMD, ',[^,]*$', '')) name, MIN(CREATEDON) created
FROM {PM} WHERE MATL_CAT = '02' AND MATERIAL LIKE 'NK%' GROUP BY RT_CONFMAT"""

COLOUR = f"""SELECT parent, colour, url FROM {FQ}.dim_product_image"""

# Product attributes read from images (Databricks vision endpoint); styles without a row use the attribute fallback
IMAGE_TAGS_SQL = f"""SELECT parent, source, fit, pattern, graphic_size, text_graphic, logo_size, style, base_colour
FROM {FQ}.nl_image_tags"""

# Image fingerprints (CLIP) for visual similarity in the image + attribute look-alikes
EMBEDDINGS_SQL = f"""SELECT parent, embedding FROM {FQ}.nl_image_embeddings"""

# ---- Opening pack (22_new_launch_opening_pack): reads the saved launch forecast, so it reruns alone when the buy lands
PACK_FC = f"SELECT parent, name, division, ah2, gender, units_13w, units_p80, confidence, launch_week FROM {FQ}.nl_forecast"
PACK_FC_COUNTRY = f"SELECT parent, country, units_13w FROM {FQ}.nl_forecast_country"
PACK_WEEKLY = f"SELECT parent, wk, units_p50 FROM {FQ}.nl_forecast_weekly"
PACK_SIZES = f"""SELECT p.RT_CONFMAT parent, p.MATERIAL art, MAX(CASE WHEN TRIM(p.RT_SIZE) = '-' THEN '0.5' ELSE TRIM(p.RT_SIZE) END) size,
       MAX(COALESCE(ak.is_key_size, 0)) key_size
FROM {PM} p JOIN (SELECT DISTINCT parent FROM {FQ}.nl_forecast) f ON f.parent = p.RT_CONFMAT
LEFT JOIN {FQ}.art_size_key ak ON ak.article_code = p.MATERIAL
WHERE p.MATL_CAT = '02' GROUP BY p.RT_CONFMAT, p.MATERIAL"""
PACK_STORES = f"""SELECT sm.COUNTRY country, sm.PLANT store, MAX(sm.`/BIC/ZNAME1`) store_name,
       MAX(COALESCE(csg.grade, CASE WHEN TRIM(sm.`/BIC/ZCH_CL_GR`) IN ('', 'NA', 'N-A') OR sm.`/BIC/ZCH_CL_GR` IS NULL
                                    THEN 'UNGRADED' ELSE TRIM(sm.`/BIC/ZCH_CL_GR`) END)) grade
FROM {SM} sm LEFT JOIN {FQ}.cfg_store_grade csg ON csg.store_code = sm.PLANT
WHERE sm.PLANTCAT = 'A' AND sm.`/BIC/ZBU` = 'NIKE' AND sm.COUNTRY <> ''
GROUP BY sm.COUNTRY, sm.PLANT"""
# store x division x gender units, last 26 full weeks, every Nike article (in-season launches included, not only CORE)
PACK_STORE_UNITS = f"""WITH {_ART},
g AS (SELECT MATERIAL art, MAX(TRIM(`/BIC/ZRWGH2_T`)) division, MAX(TRIM(`/BIC/ZCHWGH5`)) gender FROM {PM}
      WHERE MATL_CAT = '02' AND MATERIAL LIKE 'NK%' GROUP BY MATERIAL),
bounds AS (SELECT date_sub(date_trunc('week', current_date()), 1) AS last_day)
SELECT n.country, s.site_code store, g.division, g.gender, SUM(GREATEST(s.txn_qty, 0)) units
FROM {SALES} s JOIN g ON g.art = s.variant_article_code JOIN nike n ON n.store = s.site_code
WHERE s.distr_channel IN ('10', '20')
  AND s.txn_date > date_sub((SELECT last_day FROM bounds), 7 * 26) AND s.txn_date <= (SELECT last_day FROM bounds)
GROUP BY 1, 2, 3, 4 HAVING SUM(GREATEST(s.txn_qty, 0)) > 0"""
PACK_CURVE = f"SELECT country, cluster, size, units_26w FROM {FQ}.fc_size_curve"
PACK_BANDS = f"SELECT band_order, nb_alpha FROM {FQ}.fc_error_bands"
# buyer's buy file, when loaded (parent, country, buy_qty); absent -> buy assumed = 13-week P80
PACK_BUY = f"SELECT parent, country, buy_qty FROM {FQ}.nl_buy"
