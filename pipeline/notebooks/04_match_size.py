# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Deterministic Match & Size — Nike Consolidation POC
# MAGIC
# MAGIC Matches donors to receivers per SKU and sizes the move (Steps 6–7; C7/C8) via a **cumulative-interval
# MAGIC waterfall** (set-based, deterministic):
# MAGIC - Receiver rank: `ros DESC, cover_days ASC, store_id ASC`. Donor rank: `donor_can_give DESC` (largest
# MAGIC   supplier first so a receiver is sourced from one donor where possible), then `DEAD-first, cover_days
# MAGIC   DESC, store_id ASC`. Each donor/receiver occupies a cumulative interval per **`(country, article, BU)`**;
# MAGIC   overlap = allocation → strongest receiver drains from deadest donor. `recommended_qty = FLOOR(overlap)`.
# MAGIC - **Intra-country (C7)** is enforced by including `country` in the partition keys AND the join, so a
# MAGIC   multi-country run never matches across borders.
# MAGIC - **BU eligibility (C8)** via `cfg_bu_eligibility` (default NIKE→NIKE). Fail-fast if the matrix has
# MAGIC   donor_bu fan-out (waterfall assumes a clean partition). Capacity cap (C6) parked. Reads `run_config`.

# COMMAND ----------
CATALOG = "sports_analytics_test"; SCHEMA = "nike_consolidation"; FQ = f"{CATALOG}.{SCHEMA}"
cfg = {r["param_name"]: r["param_value"] for r in spark.table(f"{FQ}.run_config").collect()}
print(f"COUNTRY={cfg['COUNTRY']} AS_OF={cfg['AS_OF_DATE']}")
# grade rank (active store list): BEACON/JORDAN (above A+) = 0, A+ = 1, A = 2, B = 3, C = 4, NVS = 5, else 6.
# Lower = preferred. "A+ class" = rank<=1 (incl BEACON/JORDAN); size-curve BASE threshold (no -1) = rank<=2.
def GRK(c):
    return (f"(CASE {c} WHEN 'BEACON' THEN 0 WHEN 'JORDAN' THEN 0 WHEN 'A+' THEN 1 "
            f"WHEN 'A' THEN 2 WHEN 'B' THEN 3 WHEN 'C' THEN 4 WHEN 'NVS' THEN 5 ELSE 6 END)")

# COMMAND ----------
fanout = spark.sql(f"""
  SELECT donor_bu FROM {FQ}.cfg_bu_eligibility WHERE allowed = true
  GROUP BY donor_bu HAVING COUNT(DISTINCT receiver_bu) > 1
""").collect()
if fanout:
    raise ValueError(f"cfg_bu_eligibility donor_bu fan-out {[r['donor_bu'] for r in fanout]} — "
                     "POC waterfall assumes a clean partition; revisit allocation before enabling fan-out.")
print("BU matrix partition check passed.")

# COMMAND ----------
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.match_sized AS
WITH elig AS (SELECT donor_bu, receiver_bu FROM {FQ}.cfg_bu_eligibility WHERE allowed = true),
d AS (
  SELECT dp.country, dp.generic_article, dp.article_code, dp.product_size,
         dp.store_id, dp.store_name, dp.bu, dp.store_grade, dp.classification, dp.cover_days,
         dp.effective_stock, dp.unit_cost_aed, dp.ros AS donor_ros, dp.donor_can_give,
         e.receiver_bu AS serve_bu,
         CASE WHEN dp.classification = 'DEAD' THEN 0 ELSE 1 END AS dead_rank
  FROM {FQ}.donor_pool dp JOIN elig e ON dp.bu = e.donor_bu
  WHERE dp.donor_can_give > 0
),
rc_curve AS (   -- receiver's current key-size count per parent (for the completion boost)
  SELECT e.store_id, e.generic_article,
         -- grade-aware effective threshold (P3): A/A+ = base, others = base-1 (floor 1); NULL if no size def
         CASE WHEN MAX(ak.group_defined) = 1
              THEN GREATEST(MAX(ak.threshold) - (CASE WHEN MAX({GRK('e.store_grade')}) <= 2 THEN 0 ELSE 1 END), 1)
              ELSE NULL END AS threshold,
         MAX(ak.group_defined) AS group_defined,
         COUNT(DISTINCT CASE WHEN ak.is_key_size = 1 AND e.effective_stock > 0 THEN e.article_code END) AS key_before
  FROM {FQ}.effective_stock e
  JOIN {FQ}.art_size_key ak ON ak.article_code = e.article_code
  GROUP BY e.store_id, e.generic_article
),
r AS (
  SELECT rp.country, rp.generic_article, rp.article_code, rp.store_id, rp.store_name, rp.bu, rp.store_grade,
         rp.ros, rp.cover_days, rp.receiver_cover_incl_intransit, rp.effective_stock, rp.net_sales_per_unit, rp.receiver_can_take,
         rp.demand_type, rp.demand_priority, rp.receiver_in_warehouse,
         -- completes_run: this key size goes to a receiver still below its full-size threshold for the parent
         CASE WHEN ak.is_key_size = 1 AND rcc.group_defined = 1 AND rcc.key_before <= rcc.threshold
              THEN 1 ELSE 0 END AS completes_run
  FROM {FQ}.receiver_pool rp
  JOIN {FQ}.art_size_key ak ON ak.article_code = rp.article_code
  LEFT JOIN rc_curve rcc ON rcc.store_id = rp.store_id AND rcc.generic_article = rp.generic_article
  WHERE rp.receiver_can_take > 0
),
di AS (
  -- Donor rank: LARGEST giveable first so a receiver's need is met from a single donor where possible
  -- (fewer split shipments); deadest-first is the tiebreak, then higher cover, then store_id.
  SELECT *, SUM(donor_can_give) OVER (
             PARTITION BY country, article_code, serve_bu
             ORDER BY donor_can_give DESC, dead_rank ASC, cover_days DESC, store_id ASC
             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS d_end
  FROM d
),
di2 AS (SELECT *, d_end - donor_can_give AS d_start FROM di),
ri AS (   -- receiver rank: demand_priority (OOS lost-sales 0 → in-stock 1 → A+ 2) first, then completion boost,
          -- then demand (ROS), then cover (intransit-aware), then store_id (deterministic total order)
  SELECT *, SUM(receiver_can_take) OVER (
             PARTITION BY country, article_code, bu
             -- business 2026-09-22: ROS beats full-size completion — a receiver that SELLS more wins the stock
             -- before one that would merely complete its size display (ros DESC now precedes completes_run DESC).
             ORDER BY demand_priority ASC, ros DESC, completes_run DESC, receiver_cover_incl_intransit ASC,
                      {GRK('store_grade')} ASC, store_id ASC   -- tie-break: prefer BEACON/JORDAN, then A+, ...
             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS r_end
  FROM r
),
ri2 AS (SELECT *, r_end - receiver_can_take AS r_start FROM ri)
SELECT
  di2.country, di2.generic_article, di2.article_code, di2.product_size,
  di2.store_id AS donor_store, di2.store_name AS donor_store_name, di2.bu AS donor_bu, di2.store_grade AS donor_grade,
  di2.classification AS donor_class, di2.cover_days AS donor_cover_days, di2.donor_ros,
  di2.effective_stock AS donor_effective_stock, di2.donor_can_give, di2.unit_cost_aed,
  ri2.store_id AS receiver_store, ri2.store_name AS receiver_store_name, ri2.bu AS receiver_bu, ri2.store_grade AS receiver_grade,
  ri2.ros AS receiver_ros, ri2.cover_days AS receiver_cover_days,
  ri2.effective_stock AS receiver_effective_stock, ri2.receiver_can_take,
  ri2.net_sales_per_unit AS receiver_net_sales_per_unit,
  ri2.demand_type, ri2.receiver_in_warehouse,
  FLOOR(LEAST(di2.d_end, ri2.r_end) - GREATEST(di2.d_start, ri2.r_start) + 1e-4) AS recommended_qty,
  DATE'{cfg['AS_OF_DATE']}' AS as_of_date
FROM di2
JOIN ri2
  ON di2.country     = ri2.country
 AND di2.article_code = ri2.article_code
 AND di2.serve_bu     = ri2.bu
 AND LEAST(di2.d_end, ri2.r_end) > GREATEST(di2.d_start, ri2.r_start)
WHERE FLOOR(LEAST(di2.d_end, ri2.r_end) - GREATEST(di2.d_start, ri2.r_start) + 1e-4) > 0
""")

# COMMAND ----------
display(spark.sql(f"""
  SELECT country, COUNT(*) match_lines, COUNT(DISTINCT donor_store) donors,
         COUNT(DISTINCT receiver_store) receivers, SUM(recommended_qty) units
  FROM {FQ}.match_sized GROUP BY country ORDER BY units DESC
"""))
