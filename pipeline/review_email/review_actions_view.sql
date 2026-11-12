-- Every action in a completed run that needs a person (same rules as the app's Approvals queue): store moves worth
-- >= AED 1,000 or moving >= 6 units, and DC lines that are full-size top-ups or worth >= AED 1,000 or >= 6 units.
-- Read from the *_history archive, so a run still in progress is never half-read. run_ref = 'latest' (last completed
-- run) or a run_id. line_key identifies the line for the decision import; decision / comment are for the reviewer.
CREATE OR REPLACE FUNCTION sports_analytics_test.nike_consolidation.agent_review_actions_for(
  run_ref STRING DEFAULT 'latest' COMMENT 'latest = last completed run, or an engine run_id')
RETURNS TABLE (run_id STRING, run_by STRING, sales_through DATE, line_type STRING, line_key STRING, review_reason STRING,
               country STRING, category STRING, style_code STRING, style_name STRING, article STRING, size STRING,
               from_site STRING, from_name STRING, to_site STRING, to_name STRING, qty INT, value_aed DOUBLE,
               engine_reason STRING, current_status STRING, decision STRING, comment STRING)
COMMENT 'Exhaustive list of actions in a completed stock movement run that need human review, with the current decision and blank decision / comment columns for the reviewer.'
RETURN
WITH run AS (
  SELECT run_id, CASE WHEN triggered_by IN ('app', 'manual') OR triggered_by IS NULL THEN 'scheduled' ELSE triggered_by END AS run_by,
         as_of_date AS sales_through
  FROM sports_analytics_test.nike_consolidation.run_stats_history
  WHERE run_id = CASE WHEN lower(trim(run_ref)) IN ('', 'latest') OR run_ref IS NULL
                      THEN (SELECT MAX_BY(run_id, run_ts) FROM sports_analytics_test.nike_consolidation.run_stats_history)
                      ELSE trim(run_ref) END),
nm AS (SELECT store_id, MAX(store_name) AS store_name FROM sports_analytics_test.nike_consolidation.effective_stock GROUP BY store_id),
m AS (
  SELECT h.run_id, 'Store move' AS line_type, CONCAT(h.donor_store, '|', h.receiver_store, '|', h.article) AS line_key,
         CONCAT_WS(' · ', CASE WHEN h.inventory_cost_aed >= 1000 THEN 'High value' END, CASE WHEN h.recommended_qty >= 6 THEN 'Large qty' END) AS review_reason,
         h.country, h.business_category AS category, h.parent_article AS style_code, h.generic_article_name AS style_name, h.article, h.size,
         h.donor_store AS from_site, h.donor_store_name AS from_name, h.receiver_store AS to_site, h.receiver_store_name AS to_name,
         CAST(h.recommended_qty AS INT) AS qty, CAST(ROUND(h.inventory_cost_aed, 0) AS DOUBLE) AS value_aed, h.movement_note AS engine_reason
  FROM sports_analytics_test.nike_consolidation.recommendations_history h JOIN run ON run.run_id = h.run_id
  WHERE h.inventory_cost_aed >= 1000 OR h.recommended_qty >= 6),
d AS (
  SELECT r.run_id, 'DC line', CONCAT(r.source, '|', r.receiver_store, '|', r.article),
         CONCAT_WS(' · ', CASE WHEN r.decision IS NOT NULL THEN 'Full-size top-up' END,
                          CASE WHEN r.recommended_qty * r.unit_price_aed >= 1000 THEN 'High value' END,
                          CASE WHEN r.recommended_qty >= 6 THEN 'Large qty' END),
         r.country, r.category, r.parent_article, r.generic_article_name, r.article, r.size,
         r.source, CAST(NULL AS STRING), r.receiver_store, n.store_name,
         CAST(r.recommended_qty AS INT), CAST(ROUND(r.recommended_qty * r.unit_price_aed, 0) AS DOUBLE), r.reason
  FROM sports_analytics_test.nike_consolidation.replen_recommendations_history r JOIN run ON run.run_id = r.run_id
  LEFT JOIN nm n ON n.store_id = r.receiver_store
  WHERE r.decision IS NOT NULL OR r.recommended_qty >= 6 OR r.recommended_qty * r.unit_price_aed >= 1000)
SELECT u.run_id, run.run_by, run.sales_through, u.line_type, u.line_key, u.review_reason, u.country, u.category, u.style_code,
       u.style_name, u.article, u.size, u.from_site, u.from_name, u.to_site, u.to_name, u.qty, u.value_aed, u.engine_reason,
       CASE COALESCE(x.decision, 'PENDING') WHEN 'APPROVED' THEN 'Approved' WHEN 'REJECTED' THEN 'Rejected' ELSE 'Pending' END,
       CASE x.decision WHEN 'APPROVED' THEN 'Approve' WHEN 'REJECTED' THEN 'Reject' END,
       x.comment
FROM (SELECT * FROM m UNION ALL SELECT * FROM d) u JOIN run ON run.run_id = u.run_id
LEFT JOIN sports_analytics_test.nike_consolidation.review_decisions x
  ON x.run_id = u.run_id AND x.line_type = u.line_type AND x.line_key = u.line_key;

CREATE OR REPLACE VIEW sports_analytics_test.nike_consolidation.agent_review_actions
COMMENT 'Actions in the last completed stock movement run that need human review (agent_review_actions_for(''latest'')).'
AS SELECT * FROM sports_analytics_test.nike_consolidation.agent_review_actions_for('latest');
