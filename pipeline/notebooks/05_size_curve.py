# Databricks notebook source
# MAGIC %md
# MAGIC # 05 · Size-Curve Reshape (Step 8, Option B) — Nike Consolidation POC
# MAGIC
# MAGIC Guards the donor's full-size curve and reshapes toward faster-selling receivers.
# MAGIC
# MAGIC **Size definition** (`cfg_size_curve`): per `(division, ah2, gender)` a set of **key sizes** + a
# MAGIC **threshold**. A store×parent is **full-size** when the count of key sizes **in stock** > threshold.
# MAGIC Mapping (product master, MATL_CAT='02'): division=`/BIC/ZRWGH2_T`, gender=`/BIC/ZCHWGH5`, size=`RT_SIZE`,
# MAGIC AH2 = `CM_CDT3_TXTMD` for FOOTWEAR / constant `'APPAREL'` for APPAREL.
# MAGIC
# MAGIC **Decision per donor×parent** (from the matched moves in `match_sized`):
# MAGIC - **No size definition** (group not in file — incl. KIDS/accessories/no-size) → **allow move** (graceful
# MAGIC   fallback), tag `NO_SIZE_DEF`.
# MAGIC - Donor **already cut** before the move → allow, tag `DONOR_ALREADY_CUT`.
# MAGIC - Donor **stays full** after the move → allow, tag `FULL_RETAINED`.
# MAGIC - Move would **break** the donor's full run → compare parent-level ROS: if the **best receiver** sells
# MAGIC   **≥ (1+GENERIC_ROS_UPLIFT_MIN)×** the donor's parent ROS (default 25% faster) → **release** the run
# MAGIC   (`RESHAPED_FULL_RUN`): the curve is going to break anyway, so keep ALL the donor's matched moves — every
# MAGIC   giveable size flows to the receivers that need it via the normal target-capped waterfall (Option C,
# MAGIC   business 2026-07-23); else **hold**
# MAGIC   (`HELD_CURVE_BREAK`, no movement).
# MAGIC - **Best receiver** among the matched receivers = highest receiver parent-ROS → bigger store (revenue)
# MAGIC   → `store_id` (deterministic).
# MAGIC
# MAGIC Output `match_reshaped_all` = proposed moves (08 applies the joint receiver guard -> `match_reshaped`) (each with `reshape_action` + business-readable `movement_note`).
# MAGIC Held lines go to `held_size_curve` (surfaced in the "Held by constraint" KPI). Reads frozen `run_config`.

# COMMAND ----------
CATALOG = "sports_analytics_test"; SCHEMA = "nike_consolidation"; FQ = f"{CATALOG}.{SCHEMA}"
cfg = {r["param_name"]: r["param_value"] for r in spark.table(f"{FQ}.run_config").collect()}
UPLIFT = float(cfg["GENERIC_ROS_UPLIFT_MIN"]) if (cfg.get("GENERIC_ROS_UPLIFT_MIN") or "").strip() else None
TARGET = int(cfg["TARGET_COVER_DAYS"])
# P3 (2026-07-24): size-curve "full-size" threshold is EASIER for smaller doors. Base threshold (no -1) for
# the A+ class + A (grade_rank<=2, incl BEACON/JORDAN); every other grade uses threshold-1 (floored at 1).
# grade_rank (active store list): BEACON/JORDAN=0, A+=1, A=2, B=3, C=4, NVS=5, else 6 (lower = preferred).
def GRK(c):
    return (f"(CASE {c} WHEN 'BEACON' THEN 0 WHEN 'JORDAN' THEN 0 WHEN 'A+' THEN 1 "
            f"WHEN 'A' THEN 2 WHEN 'B' THEN 3 WHEN 'C' THEN 4 WHEN 'NVS' THEN 5 ELSE 6 END)")
size_curve_defined = spark.table(f"{FQ}.cfg_size_curve").count() > 0
active = size_curve_defined and UPLIFT is not None
print(f"cfg_size_curve rows>0={size_curve_defined}  GENERIC_ROS_UPLIFT_MIN={UPLIFT}  reshape_active={active}")

# COMMAND ----------
if not active:
    # pass-through: 08 (joint receiver guard) consumes match_reshaped_all and builds match_reshaped / statuses
    spark.sql(f"CREATE OR REPLACE TABLE {FQ}.match_reshaped_all AS "
              f"SELECT *, 'PASS_THROUGH_NO_DEFINITION' AS reshape_action, "
              f"'Moved — size-curve step inactive' AS movement_note FROM {FQ}.match_sized")
    spark.sql(f"CREATE OR REPLACE TABLE {FQ}.held_size_curve (country string, donor_store string, "
              f"generic_article string, lines int, units double, movement_note string)")
    print("Size-curve INACTIVE — passed through.")
    dbutils.notebook.exit("inactive")

# COMMAND ----------
# MAGIC %md ### Article → size-def attributes, store revenue, parent-level ROS

# COMMAND ----------
spark.sql(f"CREATE OR REPLACE TEMP VIEW _art_key AS SELECT * FROM {FQ}.art_size_key")  # built in 03

spark.sql(f"""CREATE OR REPLACE TEMP VIEW _store_rev AS
  SELECT store_id, SUM(net_sales) AS store_revenue FROM {FQ}.stock_classified GROUP BY store_id""")

spark.sql(f"""CREATE OR REPLACE TEMP VIEW _parent_ros AS
  SELECT store_id, generic_article, SUM(ros) AS parent_ros FROM {FQ}.stock_classified GROUP BY store_id, generic_article""")

# COMMAND ----------
# MAGIC %md ### Donor×parent curve status (before/after the matched moves) + decision

# COMMAND ----------
spark.sql(f"""
CREATE OR REPLACE TEMP VIEW _donor_parent AS
WITH moved AS (
  SELECT donor_store, generic_article, article_code, SUM(recommended_qty) AS qty_out
  FROM {FQ}.match_sized GROUP BY donor_store, generic_article, article_code
),
dp AS (SELECT DISTINCT country, donor_store, generic_article FROM {FQ}.match_sized)
SELECT dp.country, dp.donor_store, dp.generic_article,
       MAX(e.store_grade) AS donor_grade,
       -- grade-aware effective threshold (P3): A/A+ = base, others = base-1 (floor 1); NULL if no size def
       CASE WHEN MAX(ak.group_defined) = 1
            THEN GREATEST(MAX(ak.threshold) - (CASE WHEN MAX({GRK('e.store_grade')}) <= 2 THEN 0 ELSE 1 END), 1)
            ELSE NULL END AS threshold,
       MAX(ak.group_defined) AS group_defined,
       COUNT(DISTINCT CASE WHEN ak.is_key_size = 1 AND e.effective_stock > 0 THEN e.article_code END) AS key_before,
       COUNT(DISTINCT CASE WHEN ak.is_key_size = 1 AND (e.effective_stock - COALESCE(m.qty_out, 0)) > 0 THEN e.article_code END) AS key_after,
       MAX(pr.parent_ros) AS donor_parent_ros
FROM dp
JOIN {FQ}.effective_stock e ON e.store_id = dp.donor_store AND e.generic_article = dp.generic_article
JOIN _art_key ak ON ak.article_code = e.article_code
LEFT JOIN moved m ON m.donor_store = e.store_id AND m.article_code = e.article_code
LEFT JOIN _parent_ros pr ON pr.store_id = dp.donor_store AND pr.generic_article = dp.generic_article
GROUP BY dp.country, dp.donor_store, dp.generic_article
""")

# best receiver per donor×parent (highest receiver parent-ROS, then store revenue, then store_id)
spark.sql(f"""
CREATE OR REPLACE TEMP VIEW _best_recv AS
WITH cand AS (
  SELECT DISTINCT m.donor_store, m.generic_article, m.receiver_store, m.receiver_grade,
         pr.parent_ros AS recv_parent_ros, COALESCE(sr.store_revenue, 0) AS recv_store_revenue
  FROM {FQ}.match_sized m
  LEFT JOIN _parent_ros pr ON pr.store_id = m.receiver_store AND pr.generic_article = m.generic_article
  LEFT JOIN _store_rev sr ON sr.store_id = m.receiver_store
),
ranked AS (   -- best receiver for the consolidated run: highest parent-ROS, then prefer BEACON/JORDAN/A+, then revenue
  SELECT *, ROW_NUMBER() OVER (PARTITION BY donor_store, generic_article
             ORDER BY recv_parent_ros DESC, {GRK('receiver_grade')} ASC, recv_store_revenue DESC, receiver_store ASC) AS rn
  FROM cand
)
SELECT donor_store, generic_article, receiver_store AS best_receiver, recv_parent_ros AS best_recv_parent_ros
FROM ranked WHERE rn = 1
""")

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW _decision AS
SELECT d.country, d.donor_store, d.generic_article, d.threshold, d.group_defined,
       d.key_before, d.key_after, d.donor_parent_ros, b.best_receiver, b.best_recv_parent_ros,
       CASE
         WHEN d.group_defined = 0 OR d.threshold IS NULL              THEN 'NO_SIZE_DEF'
         WHEN d.key_before <= d.threshold                             THEN 'DONOR_ALREADY_CUT'
         WHEN d.key_after > d.threshold                               THEN 'FULL_RETAINED'
         WHEN COALESCE(b.best_recv_parent_ros,0) >= d.donor_parent_ros * (1 + {UPLIFT}) THEN 'RESHAPED_FULL_RUN'
         ELSE 'HELD_CURVE_BREAK'
       END AS reshape_action
FROM _donor_parent d LEFT JOIN _best_recv b
  ON b.donor_store = d.donor_store AND b.generic_article = d.generic_article
""")

# COMMAND ----------
# MAGIC %md ### Build match_reshaped (all allowed moves) and held_size_curve

# COMMAND ----------
# receiver store attributes (for consolidated lines whose receiver-size row may not exist in stock_classified)
# store dims from stocked stores UNION receiver_pool (so a pure zero-ATP latent receiver still resolves name/bu/grade)
spark.sql(f"""CREATE OR REPLACE TEMP VIEW _store_dim AS
  SELECT DISTINCT store_id, store_name, bu, store_grade FROM (
    SELECT store_id, store_name, bu, store_grade FROM {FQ}.effective_stock
    UNION SELECT store_id, store_name, bu, store_grade FROM {FQ}.receiver_pool)""")

# P1 (business 2026-07-24): "a store only SELLS a style if it can DISPLAY the full size ladder; a partial
# run sits in the back = dead." So:
#  • FULL_RETAINED / NO_SIZE_DEF → keep the target-capped matched moves as-is (donor keeps a full run / no ladder).
#  • DONOR_ALREADY_CUT / RESHAPED_FULL_RUN (donor being cleared) → CONSOLIDATE the donor's ENTIRE giveable run
#    (core + fringe sizes) to the PRIMARY receiver (highest parent-ROS), so that store displays the full ladder
#    and the donor is emptied. Guardrail (carried from the 140-day fix): skip any size the receiver is ALREADY
#    ABOVE target cover on, or already holds as dead stock — never pile onto an overstocked/dead size.
#  • HELD_CURVE_BREAK → withheld (protects a still-full donor run when no receiver builds a better one).
KEPT_NOTE = """CASE reshape_action
  WHEN 'FULL_RETAINED' THEN 'Moved · donor stays full-size'
  WHEN 'NO_SIZE_DEF'   THEN 'Moved · no size rule (fallback)'
END"""
CONSOL_NOTE = """CASE reshape_action
  WHEN 'DONOR_ALREADY_CUT' THEN 'Moved · donor cut — full remaining run consolidated to one receiver to display'
  WHEN 'RESHAPED_FULL_RUN' THEN 'Moved · run released & consolidated to a faster receiver (>=25%) to display'
END"""

spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.match_reshaped_all AS
SELECT m.*, dc.reshape_action, {KEPT_NOTE} AS movement_note
FROM {FQ}.match_sized m
JOIN _decision dc ON dc.donor_store = m.donor_store AND dc.generic_article = m.generic_article
WHERE dc.reshape_action IN ('FULL_RETAINED','NO_SIZE_DEF')
UNION ALL
SELECT
  dp.country, dp.generic_article, dp.article_code, dp.product_size,
  dp.store_id AS donor_store, dp.store_name AS donor_store_name, dp.bu AS donor_bu, dp.store_grade AS donor_grade,
  dp.classification AS donor_class, dp.cover_days AS donor_cover_days, dp.ros AS donor_ros,
  dp.effective_stock AS donor_effective_stock, dp.donor_can_give, dp.unit_cost_aed,
  rd.store_id AS receiver_store, rd.store_name AS receiver_store_name, rd.bu AS receiver_bu, rd.store_grade AS receiver_grade,
  COALESCE(rc.ros, rp2.ros) AS receiver_ros, rc.cover_days AS receiver_cover_days,
  COALESCE(rc.effective_stock, 0) AS receiver_effective_stock,
  CAST(NULL AS double) AS receiver_can_take,
  COALESCE(rc.net_sales_per_unit, rp2.net_sales_per_unit) AS receiver_net_sales_per_unit,
  COALESCE(rp2.demand_type, 'IN_STOCK') AS demand_type,
  COALESCE(rp2.receiver_in_warehouse, 'N') AS receiver_in_warehouse,
  -- A+ curve completion wants ONLY 1 unit per missing key size (display), so cap it even in the
  -- whole-run consolidation branch; all other consolidated moves empty the donor's giveable run.
  CASE WHEN rp2.demand_type = 'APLUS_CURVE' THEN CAST(LEAST(FLOOR(dp.donor_can_give + 1e-4), 1) AS BIGINT)
       ELSE CAST(FLOOR(dp.donor_can_give + 1e-4) AS BIGINT) END AS recommended_qty, dp.as_of_date,
  dec.reshape_action, {CONSOL_NOTE} AS movement_note
FROM _decision dec
JOIN {FQ}.donor_pool dp ON dp.store_id = dec.donor_store AND dp.generic_article = dec.generic_article
JOIN _best_recv b       ON b.donor_store = dec.donor_store AND b.generic_article = dec.generic_article
JOIN _store_dim rd      ON rd.store_id = b.best_receiver
LEFT JOIN {FQ}.stock_classified rc ON rc.store_id = b.best_receiver AND rc.article_code = dp.article_code
LEFT JOIN {FQ}.receiver_pool rp2 ON rp2.store_id = b.best_receiver AND rp2.article_code = dp.article_code
LEFT JOIN {FQ}.warehouse_stock ws2 ON ws2.country = dp.country AND ws2.article_code = dp.article_code  -- unified DC guard
LEFT JOIN {FQ}.store_intransit it5 ON it5.store_id = b.best_receiver AND it5.article_code = dp.article_code
LEFT ANTI JOIN {FQ}.moveback_block mb ON mb.store_id = b.best_receiver AND mb.article_code = dp.article_code  -- no move-back <30d
WHERE dec.reshape_action IN ('DONOR_ALREADY_CUT','RESHAPED_FULL_RUN')
  AND FLOOR(dp.donor_can_give + 1e-4) > 0
  -- receiver size gate on ATP + IN-TRANSIT (business 2026-10-07, point 5): skip a size the receiver already
  -- holds/has inbound above target cover (was ATP-only -> shipped onto sizes already arriving).
  AND ((COALESCE(rc.effective_stock,0) + COALESCE(it5.intransit_qty,0)) = 0
       OR (COALESCE(rc.ros, rp2.ros, 0) > 0
           AND (COALESCE(rc.effective_stock,0) + COALESCE(it5.intransit_qty,0)) / COALESCE(rc.ros, rp2.ros) <= {TARGET}))
  -- DC-first (business 2026-09-22): don't consolidate a size the DC can replenish. UAE: any hub stock blocks it;
  -- outstation: only when the receiver's size cover < 4 weeks (28d) — matches the nb 03 receiver-pool guard.
  AND (COALESCE(ws2.wh_atp, 0) = 0 OR (dp.country <> 'AE' AND COALESCE(rc.cover_days, 0) >= 28))
""")

# held lines (no movement) — for the 'Held by constraint' KPI / business visibility
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.held_size_curve AS
SELECT m.country, m.donor_store, m.generic_article,
       COUNT(*) AS lines, SUM(m.recommended_qty) AS units,
       'Not moved · would break donor full-size curve (no receiver sells >=25% faster)' AS movement_note
FROM {FQ}.match_sized m
JOIN _decision dc ON dc.donor_store = m.donor_store AND dc.generic_article = m.generic_article
WHERE dc.reshape_action = 'HELD_CURVE_BREAK'
GROUP BY m.country, m.donor_store, m.generic_article
""")

# COMMAND ----------
# RECEIVER full-size guard + post-move size status MOVED to 08_receiver_guard (business 2026-10-07): the receiver
# is now judged on ATP + in-transit + store moves + DC replen − own donations, jointly across BOTH branches.

# COMMAND ----------
display(spark.sql(f"SELECT reshape_action, COUNT(*) lines, SUM(recommended_qty) units FROM {FQ}.match_reshaped_all GROUP BY reshape_action ORDER BY lines DESC"))
display(spark.sql(f"SELECT COUNT(*) held_donor_parents, SUM(lines) held_lines, SUM(units) held_units FROM {FQ}.held_size_curve"))
