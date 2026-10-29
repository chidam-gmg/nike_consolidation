# Databricks notebook source
# MAGIC %md
# MAGIC # 06 · Net-Benefit Score & Output — Nike Consolidation POC
# MAGIC
# MAGIC Steps 9–10 / C9. `expected_sales_uplift = qty * receiver_net_sales_per_unit * SELL_THROUGH_FACTOR`;
# MAGIC `movement_cost = qty * MOVEMENT_COST_PER_UNIT`; `net_benefit = uplift - cost`; keep `net_benefit > 0`.
# MAGIC Output `recommendations` sorted `country, parent_article, receiver_store, donor_store, article`.
# MAGIC Also emits `run_stats` (moves, units, donors, receivers, uplift, and **held-by-constraint** counts)
# MAGIC for the UI summary tiles.
# MAGIC
# MAGIC **Placeholders (POC):** `MOVEMENT_COST_PER_UNIT` (`[OPEN-3]`, 0) → C9 is a no-op until real cost lands;
# MAGIC `SELL_THROUGH_FACTOR` (`[OPEN-4]`, 1.0). Economics are provisional. Reads frozen `run_config`.

# COMMAND ----------
CATALOG = "sports_analytics_test"; SCHEMA = "nike_consolidation"; FQ = f"{CATALOG}.{SCHEMA}"
cfg = {r["param_name"]: r["param_value"] for r in spark.table(f"{FQ}.run_config").collect()}
MOVE_COST = float(cfg["MOVEMENT_COST_PER_UNIT"]); SELL_FACTOR = float(cfg["SELL_THROUGH_FACTOR"])
TARGET = int(cfg["TARGET_COVER_DAYS"])
print(f"MOVEMENT_COST_PER_UNIT={MOVE_COST}  SELL_THROUGH_FACTOR={SELL_FACTOR}")

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

# Ordered size-list builder (numeric asc, then apparel ladder) — reused for pre- and post-move size columns.
def SIZE_STR(col):
    return (f"array_join(transform(array_sort(collect_set(struct("
            f"CASE WHEN {col} RLIKE '^[0-9]+([.][0-9]+)?$' THEN CAST({col} AS DOUBLE) "
            f"ELSE 1000 + COALESCE(array_position("
            f"array('XXS','XS','S','M','L','XL','XXL','2XL','XXXL','3XL','4XL','5XL'), UPPER(TRIM({col}))), 999) END AS rnk, "
            f"{col} AS sz))), x -> x.sz), ', ')")

# COMMAND ----------
# Final recommendations (Steps 9-10) — enriched with business_category/business_brand via the
# category converter. Converter key = UPPER(CONCAT(RF_BNDID, /BIC/ZRWGH3_T, /BIC/ZCHWGH5, /BIC/ZRWGH2_T))
# from product master (99.9% join to cfg_category_converter).
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.recommendations AS
WITH {PNAME_CTE},
cat AS (
  SELECT p.MATERIAL AS article_code, cc.business_category, cc.business_brand
  FROM prod_mena.gold.mena_sports_product_master_view p
  JOIN {FQ}.cfg_category_converter cc
    ON UPPER(cc.converter_key) = UPPER(CONCAT(
         COALESCE(p.RF_BNDID,''), COALESCE(p.`/BIC/ZRWGH3_T`,''),
         COALESCE(p.`/BIC/ZCHWGH5`,''), COALESCE(p.`/BIC/ZRWGH2_T`,'')))
  WHERE p.MATL_CAT = '02'
),
gdr AS (
  SELECT p.MATERIAL AS article_code, TRIM(p.`/BIC/ZCHWGH5`) AS gender, TRIM(p.TXTMD) AS article_name,
         TRIM(p.`/BIC/ZRWGH2_T`) AS division
  FROM prod_mena.gold.mena_sports_product_master_view p WHERE p.MATL_CAT = '02'
),
pros AS (   -- parent-level ROS per store × parent (sum of article ROS) — repeats across the parent's sizes
  SELECT store_id, generic_article, SUM(ros) AS parent_ros FROM {FQ}.stock_classified GROUP BY store_id, generic_article
),
sizes AS (   -- current in-stock sizes per store × parent, e.g. 'S, M, L'
  SELECT store_id, generic_article,
         array_join(transform(array_sort(collect_set(struct(
           CASE WHEN product_size RLIKE '^[0-9]+([.][0-9]+)?$' THEN CAST(product_size AS DOUBLE)
                ELSE 1000 + COALESCE(array_position(
                     array('XXS','XS','S','M','L','XL','XXL','2XL','XXXL','3XL','4XL','5XL'),
                     UPPER(TRIM(product_size))), 999) END AS rnk,
           product_size AS sz))), x -> x.sz), ', ') AS avail_sizes
  FROM {FQ}.effective_stock WHERE effective_stock > 0
  GROUP BY store_id, generic_article
),
-- POST-MOVE position per store × size from 08 (`position_post_move`): ATP + in-transit + FINAL store moves in
-- − donated out + DC replen/top-up in (business 2026-10-07, point 5 — in-transit and DC inbound now count).
-- One source for the available-size lists, the key-size run counts and (via size_status_post_move) the status.
sizes_post AS (   -- the size ladder each store holds AFTER all moves (same builder for donor and receiver)
  SELECT store_id, generic_article, {SIZE_STR('product_size')} AS avail_sizes
  FROM {FQ}.position_post_move WHERE pos_after > 0 GROUP BY store_id, generic_article
),
run_post AS (     -- key-size count before (ATP + in-transit) vs after all moves
  SELECT p.store_id, p.generic_article,
         COUNT(DISTINCT CASE WHEN ak.is_key_size = 1 AND p.pos_before > 0 THEN p.article_code END) AS keys_before,
         COUNT(DISTINCT CASE WHEN ak.is_key_size = 1 AND p.pos_after  > 0 THEN p.article_code END) AS keys_after
  FROM {FQ}.position_post_move p JOIN {FQ}.art_size_key ak ON ak.article_code = p.article_code
  GROUP BY p.store_id, p.generic_article
),
scored AS (
  SELECT
    m.country, m.generic_article AS parent_article, m.article_code AS article, pn.generic_article_name, g.article_name, m.product_size AS size,
    c.business_category, c.business_brand, g.gender, g.division, asn.season_label AS article_season,
    -- APO key present at PARENT level (atp_detail apo flag is per child; apo_block = store×parent where present)
    CASE WHEN abd.generic_article IS NOT NULL THEN 'Y' ELSE 'N' END AS donor_apo,
    CASE WHEN abr.generic_article IS NOT NULL THEN 'Y' ELSE 'N' END AS receiver_apo,
    m.unit_cost_aed,
    ROUND(m.recommended_qty * m.unit_cost_aed, 2) AS inventory_cost_aed,
    ds.avail_sizes AS donor_available_sizes, rs.avail_sizes AS receiver_available_sizes,
    dsp.avail_sizes AS donor_available_sizes_post_move, rsp.avail_sizes AS receiver_available_sizes_post_move,
    dss.size_status AS donor_size_status_post_move, rss.size_status AS receiver_size_status_post_move,
    m.donor_store, m.donor_store_name, m.donor_bu, m.donor_grade,
    m.receiver_store, m.receiver_store_name, m.receiver_bu, m.receiver_grade,
    m.recommended_qty, m.donor_class AS reason,
    m.donor_effective_stock            AS donor_stock,
    m.receiver_effective_stock         AS receiver_stock,
    -- P2 (2026-07-24): a receiver fed by several donors must reflect ALL incoming, not this row only.
    SUM(m.recommended_qty) OVER (PARTITION BY m.receiver_store, m.article_code) AS receiver_units_in,
    m.receiver_effective_stock
      + SUM(m.recommended_qty) OVER (PARTITION BY m.receiver_store, m.article_code) AS receiver_stock_post_move,
    COALESCE(esd.intransit_qty, 0)     AS donor_intransit,      -- display only, not used in any calc
    COALESCE(esr.intransit_qty, 0)     AS receiver_intransit,   -- display only, not used in any calc
    ROUND(m.donor_cover_days, 2)       AS donor_cover_days,
    ROUND(m.receiver_cover_days, 2)    AS receiver_cover_days,
    ROUND(m.donor_ros, 4)              AS donor_ros,
    ROUND(m.receiver_ros, 4)           AS receiver_ros,
    ROUND(dpr.parent_ros, 4)           AS donor_parent_ros,     -- parent-level ROS (sum of the parent's sizes)
    ROUND(rpr.parent_ros, 4)           AS receiver_parent_ros,
    -- post-movement cover: cumulative across ALL lines for that store×article (donor total out / receiver total in)
    ROUND(CASE WHEN m.donor_ros > 0
               THEN (m.donor_effective_stock - SUM(m.recommended_qty) OVER (PARTITION BY m.donor_store, m.article_code)) / m.donor_ros
               ELSE NULL END, 2)       AS donor_cover_post_move,
    ROUND((m.receiver_effective_stock + SUM(m.recommended_qty) OVER (PARTITION BY m.receiver_store, m.article_code))
           / NULLIF(m.receiver_ros, 0), 2) AS receiver_cover_post_move,
    COALESCE(m.receiver_net_sales_per_unit, 0) AS receiver_net_sales_per_unit,
    m.demand_type, m.receiver_in_warehouse,
    m.reshape_action, m.movement_note,
    dr.keys_before AS donor_run_before, dr.keys_after AS donor_run_after,
    rr.keys_before AS receiver_run_before, rr.keys_after AS receiver_run_after,
    ROUND(m.recommended_qty * COALESCE(m.receiver_net_sales_per_unit, 0) * {SELL_FACTOR}, 2) AS expected_sales_uplift,
    ROUND(m.recommended_qty * {MOVE_COST}, 2) AS movement_cost,
    m.as_of_date
  FROM {FQ}.match_reshaped m
  LEFT JOIN cat c ON m.article_code = c.article_code
  LEFT JOIN gdr g ON m.article_code = g.article_code
  LEFT JOIN pname pn ON pn.generic_article = m.generic_article
  LEFT JOIN {FQ}.art_season asn ON asn.article_code = m.article_code
  LEFT JOIN sizes ds ON ds.store_id = m.donor_store    AND ds.generic_article = m.generic_article
  LEFT JOIN sizes rs ON rs.store_id = m.receiver_store AND rs.generic_article = m.generic_article
  LEFT JOIN sizes_post dsp ON dsp.store_id = m.donor_store    AND dsp.generic_article = m.generic_article
  LEFT JOIN sizes_post rsp ON rsp.store_id = m.receiver_store AND rsp.generic_article = m.generic_article
  LEFT JOIN {FQ}.size_status_post_move dss ON dss.store_id = m.donor_store    AND dss.generic_article = m.generic_article
  LEFT JOIN {FQ}.size_status_post_move rss ON rss.store_id = m.receiver_store AND rss.generic_article = m.generic_article
  LEFT JOIN {FQ}.effective_stock esd ON esd.store_id = m.donor_store    AND esd.article_code = m.article_code
  LEFT JOIN {FQ}.effective_stock esr ON esr.store_id = m.receiver_store AND esr.article_code = m.article_code
  LEFT JOIN {FQ}.apo_block abd ON abd.store_id = m.donor_store    AND abd.generic_article = m.generic_article
  LEFT JOIN {FQ}.apo_block abr ON abr.store_id = m.receiver_store AND abr.generic_article = m.generic_article
  LEFT JOIN run_post dr ON dr.store_id = m.donor_store    AND dr.generic_article = m.generic_article
  LEFT JOIN run_post rr ON rr.store_id = m.receiver_store AND rr.generic_article = m.generic_article
  LEFT JOIN pros dpr ON dpr.store_id = m.donor_store    AND dpr.generic_article = m.generic_article
  LEFT JOIN pros rpr ON rpr.store_id = m.receiver_store AND rpr.generic_article = m.generic_article
)
SELECT *, ROUND(expected_sales_uplift - movement_cost, 2) AS net_benefit
FROM scored
-- C9 net-benefit gate applies to ordinary moves; consolidated DISPLAY moves (whole run to the primary
-- receiver so a cut donor is emptied and the receiver displays the full ladder) are justified by display,
-- not per-size uplift — a fringe size the receiver doesn't individually sell has 0 uplift, so keep it.
WHERE (expected_sales_uplift - movement_cost) > 0
   OR reshape_action IN ('DONOR_ALREADY_CUT','RESHAPED_FULL_RUN')
   OR demand_type = 'APLUS_CURVE'   -- flagship display completion has no sell-through (ros=0); justified by display
""")

# COMMAND ----------
# Residual accounting -> HELD + NOT-MOVED (complete 3-way partition of the ATP universe).
# Every store×article line with ATP>0 lands in exactly one of: recommendations (moved) / held / not_moved.
# HELD = donor-eligible surplus a soft guard blocked (size-curve break → season-freshness → APO, in that
# precedence). NOT-MOVED = genuine no-demand + new-to-store + HEALTHY retained.

# --- shared enrichment (built once, reused by held + not_moved) ---
spark.sql(f"""CREATE OR REPLACE TEMP VIEW _enr_cat AS
  SELECT p.MATERIAL AS article_code, cc.business_category
  FROM prod_mena.gold.mena_sports_product_master_view p
  JOIN {FQ}.cfg_category_converter cc
    ON UPPER(cc.converter_key) = UPPER(CONCAT(COALESCE(p.RF_BNDID,''), COALESCE(p.`/BIC/ZRWGH3_T`,''),
         COALESCE(p.`/BIC/ZCHWGH5`,''), COALESCE(p.`/BIC/ZRWGH2_T`,'')))
  WHERE p.MATL_CAT = '02'""")
spark.sql(f"""CREATE OR REPLACE TEMP VIEW _enr_gdr AS
  SELECT p.MATERIAL AS article_code, TRIM(p.`/BIC/ZCHWGH5`) AS gender, TRIM(p.TXTMD) AS article_name,
         TRIM(p.`/BIC/ZRWGH2_T`) AS division
  FROM prod_mena.gold.mena_sports_product_master_view p WHERE p.MATL_CAT = '02'""")
spark.sql(f"""CREATE OR REPLACE TEMP VIEW _enr_pros AS
  SELECT store_id, generic_article, SUM(ros) AS parent_ros FROM {FQ}.stock_classified GROUP BY store_id, generic_article""")
spark.sql(f"""CREATE OR REPLACE TEMP VIEW _enr_sizes AS
  SELECT store_id, generic_article, array_join(transform(array_sort(collect_set(struct(
           CASE WHEN product_size RLIKE '^[0-9]+([.][0-9]+)?$' THEN CAST(product_size AS DOUBLE)
                ELSE 1000 + COALESCE(array_position(
                     array('XXS','XS','S','M','L','XL','XXL','2XL','XXXL','3XL','4XL','5XL'),
                     UPPER(TRIM(product_size))), 999) END AS rnk,
           product_size AS sz))), x -> x.sz), ', ') AS avail_sizes
  FROM {FQ}.effective_stock WHERE effective_stock > 0 GROUP BY store_id, generic_article""")

# --- held_moves: REAL de-duplicated allocation of blocked donor stock to blocked receivers -----------
# (Tier 2, 2026-07-28). Instead of tagging a donor line with an article-level "would-be receiver" and a
# per-row min() indicative qty (which double-counted a receiver's need across every donor), we now run an
# actual whole-unit allocation so each held row is a genuine donor->receiver move:
#   Part A · Size-curve break — the moves 05 WITHHELD to protect a donor's full run. These already carry a
#            real per-(donor,receiver,size) qty from 04's waterfall (match_sized rows for the HELD_CURVE_BREAK
#            donor×parents in held_size_curve).
#   Part B · Season-freshness / APO — the blocked receivers (season_block ∪ apo_recv_block) were removed from
#            receiver_pool, so 04 never allocated to them. We run the SAME cumulative-interval waterfall as 04
#            over (country, article): un-moved, non-size-curve-held donor surplus vs blocked-receiver need.
# FLOOR(overlap) ⇒ whole units, so a receiver whose need is a fraction (<1 unit) gets 0 and its donors fall
# through to not_moved (this is Tier 1 — no more 0-qty held rows). Precedence size-curve > season > APO is
# preserved by excluding size-curve-held donor lines from Part B.
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.held_moves AS
-- Part A: size-curve withheld moves (already a real allocation from 04)
SELECT m.country, m.article_code, m.generic_article, m.product_size,
       m.donor_store, m.donor_store_name, m.donor_bu, m.donor_grade, m.donor_class,
       m.donor_cover_days, m.donor_ros, m.donor_effective_stock, m.unit_cost_aed,
       m.receiver_store, m.receiver_store_name, m.receiver_ros,
       'Size-curve break' AS held_reason, CAST(m.recommended_qty AS BIGINT) AS held_qty
FROM {FQ}.match_sized m
JOIN {FQ}.held_size_curve h
  ON h.country = m.country AND h.donor_store = m.donor_store AND h.generic_article = m.generic_article
WHERE m.recommended_qty > 0
UNION ALL
-- Part B: season / APO waterfall (mirror of 04's cumulative-interval allocation, on the blocked pools)
SELECT di2.country, di2.article_code, di2.generic_article, di2.product_size,
       di2.donor_store, di2.donor_store_name, di2.donor_bu, di2.donor_grade, di2.donor_class,
       di2.donor_cover_days, di2.donor_ros, di2.donor_effective_stock, di2.unit_cost_aed,
       ri2.receiver_store, ri2.receiver_store_name, ri2.receiver_ros,
       ri2.held_reason,
       CAST(FLOOR(LEAST(di2.d_end, ri2.r_end) - GREATEST(di2.d_start, ri2.r_start) + 1e-4) AS BIGINT) AS held_qty
FROM (
  SELECT *, d_end - donor_can_give AS d_start FROM (
    SELECT *, SUM(donor_can_give) OVER (
               PARTITION BY country, article_code
               ORDER BY dead_rank ASC, donor_cover_days DESC, donor_store ASC
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS d_end
    FROM (
      SELECT dp.country, dp.article_code, dp.generic_article, dp.product_size,
             dp.store_id AS donor_store, dp.store_name AS donor_store_name, dp.bu AS donor_bu,
             dp.store_grade AS donor_grade, dp.classification AS donor_class,
             dp.cover_days AS donor_cover_days, dp.ros AS donor_ros,
             dp.effective_stock AS donor_effective_stock, dp.unit_cost_aed, dp.donor_can_give,
             CASE WHEN dp.classification = 'DEAD' THEN 0 ELSE 1 END AS dead_rank
      FROM {FQ}.donor_pool dp
      LEFT ANTI JOIN (SELECT DISTINCT donor_store AS store_id, article FROM {FQ}.recommendations) mv
             ON mv.store_id = dp.store_id AND mv.article = dp.article_code
      LEFT ANTI JOIN (SELECT DISTINCT donor_store, generic_article FROM {FQ}.held_size_curve) sk
             ON sk.donor_store = dp.store_id AND sk.generic_article = dp.generic_article
      LEFT ANTI JOIN (SELECT DISTINCT donor_store AS store_id, article_code FROM {FQ}.held_receiver_curve) rc
             ON rc.store_id = dp.store_id AND rc.article_code = dp.article_code  -- receiver-cut held (Part C) owns these
      WHERE dp.donor_can_give > 0
    )
  )
) di2
JOIN (
  SELECT *, r_end - receiver_can_take AS r_start FROM (
    SELECT *, SUM(receiver_can_take) OVER (
               PARTITION BY country, article_code
               ORDER BY receiver_can_take DESC, receiver_store ASC
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS r_end
    FROM (
      SELECT country, article_code, store_id AS receiver_store, store_name AS receiver_store_name,
             receiver_can_take, ros AS receiver_ros, 'Season-freshness' AS held_reason
      FROM {FQ}.season_block WHERE receiver_can_take > 0
      UNION ALL
      SELECT country, article_code, store_id, store_name, receiver_can_take, ros, 'APO'
      FROM {FQ}.apo_recv_block WHERE receiver_can_take > 0
      UNION ALL
      SELECT country, article_code, store_id, store_name, receiver_can_take, ros, 'Move-back <30d'
      FROM {FQ}.moveback_recv_block WHERE receiver_can_take > 0
    )
  )
) ri2
  ON di2.country = ri2.country AND di2.article_code = ri2.article_code
 AND LEAST(di2.d_end, ri2.r_end) > GREATEST(di2.d_start, ri2.r_start)
WHERE FLOOR(LEAST(di2.d_end, ri2.r_end) - GREATEST(di2.d_start, ri2.r_start) + 1e-4) > 0
UNION ALL
-- Part C: receiver-cut guard (business 2026-09-01) — moves 05 withheld because the RECEIVER would stay
-- cut-size (won't display). Real per-(donor,receiver,size) rows. Anti-join recommendations so a donor line
-- that ALSO moved elsewhere stays classified 'moved' (keeps the 3-way partition exact).
SELECT m.country, m.article_code, m.generic_article, m.product_size,
       m.donor_store, m.donor_store_name, m.donor_bu, m.donor_grade, m.donor_class,
       m.donor_cover_days, m.donor_ros, m.donor_effective_stock, m.unit_cost_aed,
       m.receiver_store, m.receiver_store_name, m.receiver_ros,
       'Receiver stays cut-size' AS held_reason, CAST(m.recommended_qty AS BIGINT) AS held_qty
FROM {FQ}.held_receiver_curve m
LEFT ANTI JOIN (SELECT DISTINCT donor_store AS store_id, article FROM {FQ}.recommendations) mv
       ON mv.store_id = m.donor_store AND mv.article = m.article_code
WHERE m.recommended_qty > 0
""")

# per donor line: total movable (indicative) qty + the primary would-be receiver (largest single move)
spark.sql(f"""CREATE OR REPLACE TEMP VIEW _held_agg AS
  SELECT donor_store, article_code, SUM(held_qty) AS indicative_qty FROM {FQ}.held_moves GROUP BY donor_store, article_code""")
spark.sql(f"""CREATE OR REPLACE TEMP VIEW _held_primary AS
  SELECT donor_store, article_code, receiver_store, receiver_store_name, receiver_ros, held_reason
  FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY donor_store, article_code
               ORDER BY held_qty DESC, receiver_store ASC) rn FROM {FQ}.held_moves) WHERE rn = 1""")

# --- HELD table (donor lines with a REAL movable qty to a blocked receiver; override via `decision`) ---
# Grain = donor line (store × child article). indicative_qty = SUM of that line's held_moves (de-duplicated,
# always >= 1 — Tier 1). would_be_receiver = the primary (largest-single-move) blocked receiver.
# indicative_value_aed = indicative_qty × unit cost = the ACTIONABLE value (what the KPI should show), vs
# inventory_cost_aed = the donor's full sitting holding (kept for context only).
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.held AS
WITH {PNAME_CTE}
SELECT dp.country, c.business_category, g.gender, g.division, dp.generic_article AS parent_article,
       dp.article_code AS article, pn.generic_article_name, g.article_name, dp.product_size AS size, asn.season_label AS article_season,
       dp.store_id AS donor_store, dp.store_name AS donor_store_name, dp.bu, dp.classification AS class,
       CASE WHEN ab.generic_article IS NOT NULL THEN 'Y' ELSE 'N' END AS apo_key,
       sz.avail_sizes AS available_sizes, dp.effective_stock, dp.intransit_qty,
       ROUND(dp.effective_stock * dp.unit_cost_aed, 2) AS inventory_cost_aed,
       ROUND(dp.ros, 4) AS ros, ROUND(pp.parent_ros, 4) AS parent_ros, ROUND(dp.cover_days, 2) AS cover_days,
       pr.held_reason, pr.receiver_store AS would_be_receiver, pr.receiver_store_name AS would_be_receiver_name,
       ROUND(pr.receiver_ros, 4) AS would_be_receiver_ros,
       CAST(ha.indicative_qty AS INT) AS indicative_qty,
       ROUND(ha.indicative_qty * dp.unit_cost_aed, 2) AS indicative_value_aed,
       CAST(NULL AS STRING) AS decision
FROM _held_agg ha
JOIN {FQ}.donor_pool dp ON dp.store_id = ha.donor_store AND dp.article_code = ha.article_code
JOIN _held_primary pr   ON pr.donor_store = ha.donor_store AND pr.article_code = ha.article_code
LEFT JOIN _enr_cat   c  ON c.article_code = ha.article_code
LEFT JOIN _enr_gdr   g  ON g.article_code = ha.article_code
LEFT JOIN pname pn ON pn.generic_article = dp.generic_article
LEFT JOIN _enr_pros  pp ON pp.store_id = ha.donor_store AND pp.generic_article = dp.generic_article
LEFT JOIN _enr_sizes sz ON sz.store_id = ha.donor_store AND sz.generic_article = dp.generic_article
LEFT JOIN {FQ}.art_season asn ON asn.article_code = ha.article_code
LEFT JOIN {FQ}.apo_block ab ON ab.store_id = ha.donor_store AND ab.generic_article = dp.generic_article
WHERE ha.indicative_qty >= 1
""")

# not_moved — all RETAINED stock, completing the partition. Donor-eligible lines that did NOT move AND are
# NOT in `held` (no receiver needs a full unit — incl. the fractional-need Tier-1 drop-outs) + new-to-store
# + HEALTHY. Built in the SAME cell as the _enr_* temp views (they must share one cell in a job session).
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.not_moved AS
WITH {PNAME_CTE},
b1 AS (   -- donor-eligible, not moved, not held → no receiver needs a full unit
  SELECT dp.country, dp.store_id, dp.store_name, dp.bu, dp.generic_article AS parent_article,
         dp.article_code AS article, dp.product_size AS size, dp.classification AS class,
         dp.effective_stock, dp.intransit_qty, dp.unit_cost_aed,
         ROUND(dp.ros,4) AS ros, ROUND(dp.cover_days,2) AS cover_days,
         'Not moved - no receiving store needs this size' AS not_moved_reason
  FROM {FQ}.donor_pool dp
  LEFT ANTI JOIN (SELECT DISTINCT donor_store AS store_id, article FROM {FQ}.recommendations) mv
         ON mv.store_id = dp.store_id AND mv.article = dp.article_code
  LEFT ANTI JOIN (SELECT DISTINCT donor_store AS store_id, article FROM {FQ}.held) hl
         ON hl.store_id = dp.store_id AND hl.article = dp.article_code
),
b2 AS (   -- new-to-store: excluded from computation
  SELECT country, store_id, store_name, bu, generic_article AS parent_article, article_code AS article,
         product_size AS size, classification AS class, effective_stock, intransit_qty, unit_cost_aed,
         ROUND(ros,4) AS ros, ROUND(cover_days,2) AS cover_days,
         'Not moved - new to store (too recent to consider)' AS not_moved_reason
  FROM {FQ}.stock_classified WHERE classification = 'NEW_TO_STORE' AND effective_stock > 0
),
b3 AS (   -- HEALTHY retained (selling within cover) — completes the ATP partition
  SELECT country, store_id, store_name, bu, generic_article AS parent_article, article_code AS article,
         product_size AS size, classification AS class, effective_stock, intransit_qty, unit_cost_aed,
         ROUND(ros,4) AS ros, ROUND(cover_days,2) AS cover_days,
         'Not moved - retained (selling within cover)' AS not_moved_reason
  FROM {FQ}.stock_classified WHERE classification = 'HEALTHY' AND effective_stock > 0
),
u AS (SELECT * FROM b1 UNION ALL SELECT * FROM b2 UNION ALL SELECT * FROM b3)
SELECT u.country, c.business_category, g.gender, g.division, u.parent_article, u.article, pn.generic_article_name, g.article_name, u.size,
       asn.season_label AS article_season,
       u.store_id, u.store_name, u.bu, u.class, u.effective_stock, u.intransit_qty,
       CASE WHEN ab.generic_article IS NOT NULL THEN 'Y' ELSE 'N' END AS apo_key,
       ROUND(u.effective_stock * u.unit_cost_aed, 2) AS inventory_cost_aed,
       s.avail_sizes AS available_sizes, u.ros, ROUND(pp.parent_ros, 4) AS parent_ros, u.cover_days, u.not_moved_reason
FROM u
LEFT JOIN _enr_cat   c ON c.article_code = u.article
LEFT JOIN _enr_gdr   g ON g.article_code = u.article
LEFT JOIN pname pn ON pn.generic_article = u.parent_article
LEFT JOIN _enr_pros  pp ON pp.store_id = u.store_id AND pp.generic_article = u.parent_article
LEFT JOIN _enr_sizes s ON s.store_id = u.store_id AND s.generic_article = u.parent_article
LEFT JOIN {FQ}.art_season asn ON asn.article_code = u.article
LEFT JOIN {FQ}.apo_block ab ON ab.store_id = u.store_id AND ab.generic_article = u.parent_article
""")

# COMMAND ----------
# run_stats — headline aggregates + held/partition reconciliation for the UI
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.run_stats AS
WITH rec AS (SELECT * FROM {FQ}.recommendations),
-- held units/value are now INDICATIVE (actually movable), not the donor's full sitting inventory (Tier 2)
held_agg AS (SELECT COUNT(*) lines, COALESCE(SUM(indicative_qty),0) units,
                    ROUND(COALESCE(SUM(indicative_value_aed),0),2) inv,
                    ROUND(COALESCE(SUM(inventory_cost_aed),0),2) donor_inv FROM {FQ}.held),
nm_agg   AS (SELECT COUNT(*) lines FROM {FQ}.not_moved),
mv_agg   AS (SELECT COUNT(DISTINCT donor_store || '|' || article) lines FROM {FQ}.recommendations),
uni_agg  AS (SELECT COUNT(*) lines FROM {FQ}.stock_classified WHERE effective_stock > 0),
step8_held AS (SELECT COALESCE(SUM(lines), 0) AS n FROM {FQ}.held_size_curve),
season_held AS (SELECT COUNT(*) AS n FROM {FQ}.season_block),
apo_held AS (SELECT COUNT(*) AS n FROM {FQ}.apo_recv_block),
-- out-of-stock demand (point 1/2): candidate receivers after the warehouse guard, moves that filled them,
-- and demand deferred to replenishment because the country DC still holds the size (guard suppressed).
oos_cand AS (SELECT COUNT(*) AS n FROM {FQ}.receiver_pool WHERE demand_type = 'OOS_SELLING'),
oos_mv   AS (SELECT COUNT(*) AS n FROM {FQ}.recommendations WHERE demand_type = 'OOS_SELLING'),
oos_defer AS (SELECT COUNT(*) AS n FROM {FQ}.sales_demand sd
              JOIN {FQ}.warehouse_stock ws ON ws.country = sd.country AND ws.article_code = sd.article_code
              LEFT JOIN {FQ}.stock_classified sc ON sc.store_id = sd.store_id AND sc.article_code = sd.article_code
              WHERE sd.ros > 0 AND COALESCE(sc.effective_stock,0) = 0),
aplus_cand AS (SELECT COUNT(*) AS n FROM {FQ}.receiver_pool WHERE demand_type = 'APLUS_CURVE'),
aplus_mv   AS (SELECT COUNT(*) AS n FROM {FQ}.recommendations WHERE demand_type = 'APLUS_CURVE'),
mb_held    AS (SELECT COUNT(*) AS n FROM {FQ}.moveback_recv_block),
topup_pairs AS (SELECT COUNT(*) AS n FROM {FQ}.receiver_guard_decision
                WHERE recv_action = 'TOPUP_TO_FULL' AND consol_in_units > 0),
recv_cut   AS (SELECT COUNT(*) AS moves, COALESCE(SUM(recommended_qty),0) AS units,
                      COUNT(DISTINCT receiver_store) AS receivers FROM {FQ}.held_receiver_curve)
SELECT
  (SELECT COUNT(*) FROM rec)                        AS recommended_moves,
  (SELECT COALESCE(SUM(recommended_qty),0) FROM rec) AS total_units,
  (SELECT COUNT(DISTINCT donor_store) FROM rec)     AS donor_stores,
  (SELECT COUNT(DISTINCT receiver_store) FROM rec)  AS receiver_stores,
  (SELECT COUNT(DISTINCT country) FROM rec)         AS countries,
  (SELECT ROUND(COALESCE(SUM(expected_sales_uplift),0),2) FROM rec) AS total_expected_uplift,
  (SELECT ROUND(COALESCE(SUM(net_benefit),0),2) FROM rec)          AS total_net_benefit,
  (SELECT lines FROM held_agg)                      AS held_lines,
  (SELECT units FROM held_agg)                      AS held_units,            -- indicative movable units
  (SELECT inv   FROM held_agg)                      AS held_inventory_aed,    -- indicative movable value AED
  (SELECT donor_inv FROM held_agg)                  AS held_donor_inventory_aed, -- donor full holding (context)
  (SELECT n FROM apo_held)                          AS apo_blocked_candidates,
  (SELECT n FROM step8_held)                        AS held_step8,
  (SELECT n FROM season_held)                       AS season_blocked_candidates,
  (SELECT lines FROM mv_agg)                        AS moved_donor_lines,
  (SELECT lines FROM nm_agg)                         AS not_moved_lines,
  (SELECT lines FROM uni_agg)                        AS universe_lines,
  ((SELECT lines FROM mv_agg) + (SELECT lines FROM held_agg) + (SELECT lines FROM nm_agg)
     = (SELECT lines FROM uni_agg))                  AS partition_reconciles,
  (SELECT n FROM oos_cand)                          AS oos_receiver_candidates,   -- out-of-stock demand (post-guard)
  (SELECT n FROM oos_mv)                            AS oos_moves,                 -- moves that filled OOS demand
  (SELECT n FROM oos_defer)                         AS oos_deferred_to_replen,    -- suppressed: DC holds the size
  (SELECT n FROM aplus_cand)                        AS aplus_curve_candidates,    -- A+ missing key sizes (post-guard)
  (SELECT n FROM aplus_mv)                          AS aplus_curve_moves,         -- A+ curve-completion moves
  (SELECT moves FROM recv_cut)                      AS receiver_cut_blocked_moves,    -- moves suppressed: receiver stays cut-size
  (SELECT units FROM recv_cut)                      AS receiver_cut_blocked_units,
  (SELECT receivers FROM recv_cut)                  AS receiver_cut_blocked_receivers,
  (SELECT n FROM mb_held)                           AS moveback_blocked_candidates,   -- article left that store <30d ago
  (SELECT n FROM topup_pairs)                       AS receiver_topup_pairs,          -- store-move receivers made full by DC top-up
  current_timestamp()                               AS computed_at
""")

# COMMAND ----------
display(spark.sql(f"SELECT 'held' t, held_reason reason, COUNT(*) lines, SUM(indicative_qty) units, ROUND(SUM(indicative_value_aed),0) value_aed FROM {FQ}.held GROUP BY 2 UNION ALL SELECT 'not_moved', not_moved_reason, COUNT(*), ROUND(SUM(effective_stock),0), ROUND(SUM(inventory_cost_aed),0) FROM {FQ}.not_moved GROUP BY 2 ORDER BY lines DESC"))
display(spark.sql(f"SELECT moved_donor_lines, held_lines, not_moved_lines, universe_lines, partition_reconciles FROM {FQ}.run_stats"))

# COMMAND ----------
display(spark.table(f"{FQ}.run_stats"))
display(spark.sql(f"""
  SELECT country, parent_article, article, size, donor_store, donor_store_name, receiver_store,
         reason, recommended_qty, donor_stock, receiver_stock, donor_ros, receiver_ros, expected_sales_uplift
  FROM {FQ}.recommendations
  ORDER BY country, parent_article, receiver_store, donor_store, article LIMIT 20
"""))
