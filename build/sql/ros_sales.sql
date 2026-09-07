-- Nike Consolidation POC — SAP retail sales for Rate-of-Sale (ROS)
-- Source: sap_gmg_datalake.silver.fct_sap_sales_detail_v (SAP sales, per user direction)
-- Grain out: store (site_code) x child article x parent article x size
-- net sales = txn_net_amount ; qty sold = txn_qty
-- Channel: retail only (distr_channel = '10') for now — OPEN-2 (ecom/marketplace) still open.
-- :ros_window_days is injected from cfg_scalar_params at run start (determinism: config snapshot).
-- Data is available up to yesterday (today = current_date - 1), so window is [current_date - N, current_date - 1].
--
-- Nike-universe scope: brand lives in atp_detail.brand_id (NOT in product master — /BIC/ZZKGMBRND is
-- age/gender). We scope ROS to the set of Nike/Jordan articles that exist in ATP, via cfg_brand_mapping.
-- Nike/Jordan stock uses alphanumeric style codes that match product master MATERIAL 100%; zero-padded
-- numeric variant codes in raw sales are non-Nike/non-fashion and fall away with this scope.

WITH nike_articles AS (
  SELECT DISTINCT a.article_code
  FROM sap_gmg_datalake.bronze.atp_detail a
  JOIN sports_analytics_test.nike_consolidation.cfg_brand_mapping b
    ON a.brand_id = b.brand_id AND b.is_nike = true
)
SELECT
  s.site_code                                        AS store_id,
  s.variant_article_code                             AS article_code,     -- child
  p.RT_CONFMAT                                       AS generic_article,  -- parent
  p.RT_SIZE                                          AS product_size,
  SUM(s.txn_qty)                                     AS units_sold,
  SUM(s.txn_net_amount)                              AS net_sales,
  SUM(s.txn_net_amount) / NULLIF(SUM(s.txn_qty), 0)  AS net_sales_per_unit
FROM sap_gmg_datalake.silver.fct_sap_sales_detail_v s
JOIN nike_articles n
  ON s.variant_article_code = n.article_code
JOIN prod_mena.gold.mena_sports_product_master_view p
  ON s.variant_article_code = p.MATERIAL
WHERE s.distr_channel = '10'                          -- SALES_DISTR_CHANNELS param
  AND s.txn_date >= date_sub(current_date(), :ros_window_days)
  AND s.txn_date <  current_date()
GROUP BY 1, 2, 3, 4
HAVING SUM(s.txn_qty) > 0;
