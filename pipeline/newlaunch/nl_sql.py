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
