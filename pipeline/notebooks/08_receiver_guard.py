# Databricks notebook source
# MAGIC %md
# MAGIC # 08 · Joint Receiver Full-Size Guard — consolidation + DC replenishment (business 2026-10-07)
# MAGIC
# MAGIC Runs AFTER both branches have proposed their moves (05 `match_reshaped_all` = store→store,
# MAGIC 11 `replen_allocated` = DC→store) and BEFORE either is published (06 / 12).
# MAGIC
# MAGIC Per **receiver × parent** the NET position after ALL proposed moves is
# MAGIC `ATP + in-transit + store moves in + DC replen in − own donations out` (in-transit = point 5).
# MAGIC Full-size = in-stock key sizes > grade-aware threshold (A/A+ base, others base−1, floor 1).
# MAGIC - **Full after** (or no size rule) → allow every move.
# MAGIC - **Still cut** → if the DC can supply enough MISSING key sizes to make it full AND the size is current
# MAGIC   season (or newer) / CORE-YEAR → **top up 1 unit per missing key size** from the DC (residual supply after
# MAGIC   normal replen), decision **"Review - moving to make full size"** (point 4).
# MAGIC - Otherwise → **HOLD every move** (store AND DC) into that receiver × parent (point 3).
# MAGIC
# MAGIC Outputs: `match_reshaped` / `held_receiver_curve` (consolidation), `replen_final` + `fullsize_topup`
# MAGIC (replen), `receiver_guard_decision`, `position_post_move` and `size_status_post_move` (for 06 columns).

# COMMAND ----------
CATALOG = "sports_analytics_test"; SCHEMA = "nike_consolidation"; FQ = f"{CATALOG}.{SCHEMA}"
cfg = {r["param_name"]: r["param_value"] for r in spark.table(f"{FQ}.run_config").collect()}
rp  = {r["param_name"]: r["param_value"] for r in spark.table(f"{FQ}.cfg_replen_params").collect()}
AS_OF = cfg["AS_OF_DATE"]
if (cfg.get("CURRENT_SEASON_ORDINAL") or "").strip():
    CUR_ORDINAL = int(cfg["CURRENT_SEASON_ORDINAL"])
else:
    from datetime import date
    _d = date.fromisoformat(AS_OF); CUR_ORDINAL = _d.year * 4 + (_d.month - 1) // 3
# top-up season window: 0 = current season (or newer) only; CORE/YEAR always eligible (business 2026-10-07)
SEASONS_BACK = int(rp.get("FULLSIZE_TOPUP_SEASONS_BACK", "0"))
def GRK(c):
    return (f"(CASE {c} WHEN 'BEACON' THEN 0 WHEN 'JORDAN' THEN 0 WHEN 'A+' THEN 1 "
            f"WHEN 'A' THEN 2 WHEN 'B' THEN 3 WHEN 'C' THEN 4 WHEN 'NVS' THEN 5 ELSE 6 END)")
REVIEW_TAG = "Review - moving to make full size"
print(f"AS_OF={AS_OF} CURRENT_SEASON_ORDINAL={CUR_ORDINAL} FULLSIZE_TOPUP_SEASONS_BACK={SEASONS_BACK}")

# COMMAND ----------
# Shared lookups: article -> parent/size (product master), store -> grade/country.
spark.sql("""CREATE OR REPLACE TEMP VIEW _art_parent AS
  SELECT MATERIAL AS article_code, MAX(RT_CONFMAT) AS generic_article,
         MAX(CASE WHEN TRIM(RT_SIZE) = '-' THEN '0.5' ELSE TRIM(RT_SIZE) END) AS product_size
  FROM prod_mena.gold.mena_sports_product_master_view WHERE MATL_CAT = '02' GROUP BY MATERIAL""")
spark.sql(f"""CREATE OR REPLACE TEMP VIEW _store_grade AS
  SELECT store_id, MAX(country) AS country, MAX(store_grade) AS store_grade, MAX(store_name) AS store_name FROM (
    SELECT store_id, country, store_grade, store_name FROM {FQ}.effective_stock
    UNION ALL SELECT store_id, country, store_grade, store_name FROM {FQ}.receiver_pool
    UNION ALL SELECT store_id, country, store_grade, store_name FROM {FQ}.replen_demand
  ) GROUP BY store_id""")
spark.sql(f"""CREATE OR REPLACE TEMP VIEW _parent_ros AS
  SELECT store_id, generic_article, SUM(ros) AS parent_ros FROM {FQ}.stock_classified GROUP BY store_id, generic_article""")

# Position builder: per store × article, for every store × parent touched by the given flows.
# flows_sql must yield (store_id, article_code, consol_in, consol_out, replen_in).
def build_position(table, flows_sql):
    spark.sql(f"""
    CREATE OR REPLACE TABLE {FQ}.{table} AS
    WITH flows AS ({flows_sql}),
    sp AS (SELECT DISTINCT f.store_id, ap.generic_article FROM flows f JOIN _art_parent ap ON ap.article_code = f.article_code),
    uni AS (
      SELECT e.store_id, e.article_code, CAST(e.effective_stock AS DOUBLE) AS atp, 0D AS intransit,
             0D AS consol_in, 0D AS consol_out, 0D AS replen_in
      FROM sp JOIN {FQ}.effective_stock e ON e.store_id = sp.store_id AND e.generic_article = sp.generic_article
      UNION ALL   -- in-transit (point 5): counts as stock already on its way to the store
      SELECT it.store_id, it.article_code, 0D, CAST(it.intransit_qty AS DOUBLE), 0D, 0D, 0D
      FROM {FQ}.store_intransit it
      JOIN _art_parent ap ON ap.article_code = it.article_code
      JOIN sp ON sp.store_id = it.store_id AND sp.generic_article = ap.generic_article
      WHERE it.intransit_qty > 0
      UNION ALL
      SELECT store_id, article_code, 0D, 0D, CAST(consol_in AS DOUBLE), CAST(consol_out AS DOUBLE), CAST(replen_in AS DOUBLE)
      FROM flows
    )
    SELECT u.store_id, ap.generic_article, u.article_code, ap.product_size,
           SUM(u.atp) AS atp_qty, SUM(u.intransit) AS intransit_qty,
           SUM(u.consol_in) AS consol_in, SUM(u.consol_out) AS consol_out, SUM(u.replen_in) AS replen_in,
           SUM(u.atp) + SUM(u.intransit) AS pos_before,
           SUM(u.atp) + SUM(u.intransit) + SUM(u.consol_in) - SUM(u.consol_out) + SUM(u.replen_in) AS pos_after
    FROM uni u JOIN _art_parent ap ON ap.article_code = u.article_code
    GROUP BY u.store_id, ap.generic_article, u.article_code, ap.product_size
    """)

# COMMAND ----------
# MAGIC %md ### 1 · Net position after ALL proposed moves (store + DC) and per receiver × parent evaluation

# COMMAND ----------
build_position("receiver_position", f"""
  SELECT receiver_store AS store_id, article_code, SUM(recommended_qty) AS consol_in, 0 AS consol_out, 0 AS replen_in
  FROM {FQ}.match_reshaped_all GROUP BY receiver_store, article_code
  UNION ALL
  SELECT donor_store, article_code, 0, SUM(recommended_qty), 0 FROM {FQ}.match_reshaped_all GROUP BY donor_store, article_code
  UNION ALL
  SELECT store_id, article_code, 0, 0, SUM(rationed_qty) FROM {FQ}.replen_allocated WHERE rationed_qty >= 1
  GROUP BY store_id, article_code""")

# receiver × parent = any pair with at least one proposed inbound unit (store or DC)
spark.sql(f"""
CREATE OR REPLACE TEMP VIEW _pair_eval AS
WITH rp AS (SELECT store_id, generic_article FROM {FQ}.receiver_position
            GROUP BY store_id, generic_article HAVING SUM(consol_in) + SUM(replen_in) > 0)
SELECT rp.store_id, rp.generic_article, MAX(sg.country) AS country, MAX(sg.store_grade) AS store_grade,
       CASE WHEN MAX(ak.group_defined) = 1
            THEN GREATEST(MAX(ak.threshold) - (CASE WHEN MAX({GRK('sg.store_grade')}) <= 2 THEN 0 ELSE 1 END), 1)
            ELSE NULL END AS threshold,
       MAX(ak.group_defined) AS group_defined,
       COUNT(DISTINCT CASE WHEN ak.is_key_size = 1 AND p.pos_before > 0 THEN p.article_code END) AS key_before,
       COUNT(DISTINCT CASE WHEN ak.is_key_size = 1 AND p.pos_after  > 0 THEN p.article_code END) AS key_after,
       SUM(p.consol_in) AS consol_in_units, SUM(p.replen_in) AS replen_in_units
FROM rp
JOIN {FQ}.receiver_position p ON p.store_id = rp.store_id AND p.generic_article = rp.generic_article
JOIN {FQ}.art_size_key ak ON ak.article_code = p.article_code
LEFT JOIN _store_grade sg ON sg.store_id = rp.store_id
GROUP BY rp.store_id, rp.generic_article
""")

# COMMAND ----------
# MAGIC %md ### 2 · DC top-up to full size (point 4) — current season / CORE, 1 unit per missing key size

# COMMAND ----------
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.fullsize_topup AS
WITH cut AS (SELECT * FROM _pair_eval WHERE group_defined = 1 AND threshold IS NOT NULL AND key_after <= threshold),
pka AS (   -- every key-size article of the cut parents (product master)
  SELECT ap.generic_article, ap.article_code, ap.product_size
  FROM _art_parent ap
  JOIN {FQ}.art_size_key ak ON ak.article_code = ap.article_code
  JOIN (SELECT DISTINCT generic_article FROM cut) c ON c.generic_article = ap.generic_article
  WHERE ak.is_key_size = 1 AND ak.group_defined = 1
),
cp AS (SELECT country, MAX(CASE WHEN tier = 1 AND pool_id <> 'HUB' THEN pool_id END) AS primary_pool,
              MAX(CASE WHEN pool_id = 'HUB' THEN 1 ELSE 0 END) AS draws_hub
       FROM {FQ}.cfg_replen_dc GROUP BY country),
reach AS (   -- DC stock reachable per country × article (primary + hub), before rationing
  SELECT cp.country, d.article_code, SUM(d.pool_atp) AS reach_atp
  FROM cp JOIN {FQ}.dc_pool_stock d
    ON d.pool_id = cp.primary_pool OR (d.pool_id = 'HUB' AND cp.draws_hub = 1)
  GROUP BY cp.country, d.article_code
),
miss AS (   -- missing key sizes the DC holds: nothing on hand/in-transit/incoming, not being donated, season-eligible,
            -- not moved OUT of this store in the last 30 days (move-back block, point 1)
  SELECT c.store_id, c.generic_article, c.country, c.store_grade, c.threshold, c.key_after,
         k.article_code, k.product_size
  FROM cut c
  JOIN pka k ON k.generic_article = c.generic_article
  JOIN reach r ON r.country = c.country AND r.article_code = k.article_code AND r.reach_atp > 0
  LEFT JOIN {FQ}.receiver_position p ON p.store_id = c.store_id AND p.article_code = k.article_code
  LEFT JOIN {FQ}.art_season s ON s.article_code = k.article_code
  LEFT ANTI JOIN {FQ}.moveback_block mb ON mb.store_id = c.store_id AND mb.article_code = k.article_code
  LEFT ANTI JOIN {FQ}.apo_block ab ON ab.store_id = c.store_id AND ab.generic_article = c.generic_article
  WHERE COALESCE(p.pos_after, 0) <= 0 AND COALESCE(p.consol_out, 0) = 0 AND COALESCE(p.atp_qty, 0) <= 0
    AND (COALESCE(s.always_allowed, 1) = 1 OR s.season_ordinal >= {CUR_ORDINAL} - {SEASONS_BACK})
),
cand AS (   -- enough fillable missing key sizes to cross the threshold (before rationing)
  SELECT store_id, generic_article FROM miss
  GROUP BY store_id, generic_article, threshold, key_after
  HAVING COUNT(*) >= threshold + 1 - key_after
),
nocand AS (SELECT c.store_id, c.generic_article FROM cut c LEFT ANTI JOIN cand USING (store_id, generic_article)),
used AS (   -- normal-replen allocations that stay reserved; lines of cut pairs with NO top-up path are released
  SELECT a.primary_pool, a.article_code, a.alloc_primary, a.alloc_hub
  FROM {FQ}.replen_allocated a
  LEFT ANTI JOIN nocand x ON x.store_id = a.store_id AND x.generic_article = a.generic_article
  WHERE a.rationed_qty >= 1
),
usedp AS (
  SELECT pool_id, article_code, SUM(q) AS q FROM (
    SELECT primary_pool AS pool_id, article_code, alloc_primary AS q FROM used WHERE primary_pool IS NOT NULL
    UNION ALL SELECT 'HUB', article_code, alloc_hub FROM used
  ) GROUP BY pool_id, article_code
),
resid AS (SELECT d.pool_id, d.article_code, d.pool_atp - COALESCE(u.q, 0) AS rem
          FROM {FQ}.dc_pool_stock d LEFT JOIN usedp u ON u.pool_id = d.pool_id AND u.article_code = d.article_code),
req AS (
  SELECT m.*, {GRK('m.store_grade')} AS grk, COALESCE(pr.parent_ros, 0) AS parent_ros,
         cp.primary_pool, cp.draws_hub
  FROM miss m
  JOIN cand USING (store_id, generic_article)
  LEFT JOIN _parent_ros pr ON pr.store_id = m.store_id AND pr.generic_article = m.generic_article
  LEFT JOIN cp ON cp.country = m.country
),
r1 AS (   -- phase 1: exclusive primary pool (KSA/BAH), 1 unit per request, priority grade → parent ROS → store
  SELECT r.*, COALESCE(p.rem, 0) AS rem1,
         ROW_NUMBER() OVER (PARTITION BY r.primary_pool, r.article_code
                            ORDER BY r.grk, r.parent_ros DESC, r.store_id) AS rn1
  FROM req r LEFT JOIN resid p ON p.pool_id = r.primary_pool AND p.article_code = r.article_code
),
r1a AS (SELECT *, CASE WHEN primary_pool IS NOT NULL AND rn1 <= rem1 THEN 1 ELSE 0 END AS from_primary FROM r1),
r2 AS (   -- phase 2: shared UAE hub, rationed globally per article
  SELECT r.*, COALESCE(h.rem, 0) AS rem2,
         ROW_NUMBER() OVER (PARTITION BY r.article_code ORDER BY r.grk, r.parent_ros DESC, r.store_id) AS rn2
  FROM r1a r LEFT JOIN resid h ON h.pool_id = 'HUB' AND h.article_code = r.article_code
  WHERE r.from_primary = 0 AND r.draws_hub = 1
),
served AS (
  SELECT store_id, generic_article, country, store_grade, threshold, key_after, article_code, product_size,
         primary_pool, 1 AS alloc_primary, 0 AS alloc_hub FROM r1a WHERE from_primary = 1
  UNION ALL
  SELECT store_id, generic_article, country, store_grade, threshold, key_after, article_code, product_size,
         primary_pool, 0, 1 FROM r2 WHERE rn2 <= rem2
),
ok AS (   -- keep a pair's top-ups only if they actually make it full; otherwise the pair is held
  SELECT store_id, generic_article FROM served
  GROUP BY store_id, generic_article, threshold, key_after HAVING key_after + COUNT(*) > threshold
)
SELECT s.country, s.store_id, sg.store_name, s.store_grade, s.generic_article, s.article_code, s.product_size,
       s.primary_pool, s.alloc_primary, s.alloc_hub, 1 AS topup_qty, s.threshold, s.key_after AS key_before_topup
FROM served s
JOIN ok USING (store_id, generic_article)
LEFT JOIN _store_grade sg ON sg.store_id = s.store_id
""")

# COMMAND ----------
# MAGIC %md ### 3 · Decision per receiver × parent, applied to BOTH branches

# COMMAND ----------
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.receiver_guard_decision AS
SELECT e.*, COALESCE(t.topup_lines, 0) AS topup_lines,
  CASE
    WHEN e.group_defined = 0 OR e.threshold IS NULL THEN 'ALLOW_NO_SIZE_DEF'
    WHEN e.key_after > e.threshold AND e.key_before > e.threshold THEN 'ALLOW_RECV_ALREADY_FULL'
    WHEN e.key_after > e.threshold THEN 'ALLOW_RECV_BECOMES_FULL'
    WHEN COALESCE(t.topup_lines, 0) > 0 THEN 'TOPUP_TO_FULL'
    ELSE 'HOLD_RECV_CUT'
  END AS recv_action,
  DATE'{AS_OF}' AS as_of_date
FROM _pair_eval e
LEFT JOIN (SELECT store_id, generic_article, COUNT(*) AS topup_lines FROM {FQ}.fullsize_topup
           GROUP BY store_id, generic_article) t
  ON t.store_id = e.store_id AND t.generic_article = e.generic_article
""")

# Consolidation: held = moves into HOLD pairs; actioned = the rest (top-up pairs get a review note).
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.held_receiver_curve AS
SELECT m.* FROM {FQ}.match_reshaped_all m
JOIN {FQ}.receiver_guard_decision d ON d.store_id = m.receiver_store AND d.generic_article = m.generic_article
WHERE d.recv_action = 'HOLD_RECV_CUT'
""")
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.match_reshaped AS
SELECT m.* EXCEPT (movement_note),
       CASE WHEN d.recv_action = 'TOPUP_TO_FULL'
            THEN m.movement_note || ' · {REVIEW_TAG} (DC tops up missing key sizes)'
            ELSE m.movement_note END AS movement_note
FROM {FQ}.match_reshaped_all m
LEFT JOIN {FQ}.receiver_guard_decision d ON d.store_id = m.receiver_store AND d.generic_article = m.generic_article
WHERE COALESCE(d.recv_action, 'ALLOW') <> 'HOLD_RECV_CUT'
""")

# Replenishment: final qty per allocated line (0 when the receiver stays cut) + review tag for top-up pairs.
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.replen_final AS
SELECT a.*, d.recv_action AS guard_action,
       CASE WHEN d.recv_action = 'HOLD_RECV_CUT' THEN 0 ELSE a.rationed_qty END AS final_qty,
       CASE WHEN d.recv_action = 'TOPUP_TO_FULL' THEN '{REVIEW_TAG}' END AS guard_decision
FROM {FQ}.replen_allocated a
LEFT JOIN {FQ}.receiver_guard_decision d ON d.store_id = a.store_id AND d.generic_article = a.generic_article
""")

# Invariant: shipped replen (lines NOT held by the guard) + top-ups never exceed any DC pool (same rule as 11).
bad = spark.sql(f"""
  WITH t AS (SELECT primary_pool AS pool_id, article_code, SUM(alloc_primary) q FROM {FQ}.fullsize_topup
             WHERE alloc_primary > 0 GROUP BY 1, 2
             UNION ALL SELECT 'HUB', article_code, SUM(alloc_hub) FROM {FQ}.fullsize_topup WHERE alloc_hub > 0 GROUP BY 2),
  a AS (SELECT primary_pool AS pool_id, article_code, SUM(alloc_primary) q FROM {FQ}.replen_final
        WHERE primary_pool IS NOT NULL AND final_qty >= 1 GROUP BY 1, 2
        UNION ALL SELECT 'HUB', article_code, SUM(alloc_hub) FROM {FQ}.replen_final WHERE final_qty >= 1 GROUP BY 2),
  tot AS (SELECT pool_id, article_code, SUM(q) q FROM (SELECT * FROM t UNION ALL SELECT * FROM a) GROUP BY 1, 2)
  SELECT COUNT(*) n FROM tot JOIN {FQ}.dc_pool_stock d USING (pool_id, article_code)
  WHERE tot.q > d.pool_atp AND tot.article_code IN (SELECT article_code FROM {FQ}.fullsize_topup)
""").first()["n"]
if (bad or 0) > 0:
    raise ValueError(f"Full-size top-up over-allocated {bad} pool×article(s).")


# COMMAND ----------
# MAGIC %md ### 4 · Final post-move position + full/cut status (FINAL store moves + DC replen + top-ups + in-transit)

# COMMAND ----------
build_position("position_post_move", f"""
  SELECT receiver_store AS store_id, article_code, SUM(recommended_qty) AS consol_in, 0 AS consol_out, 0 AS replen_in
  FROM {FQ}.match_reshaped GROUP BY receiver_store, article_code
  UNION ALL
  SELECT donor_store, article_code, 0, SUM(recommended_qty), 0 FROM {FQ}.match_reshaped GROUP BY donor_store, article_code
  UNION ALL
  SELECT store_id, article_code, 0, 0, SUM(final_qty) FROM {FQ}.replen_final WHERE final_qty >= 1 GROUP BY store_id, article_code
  UNION ALL
  SELECT store_id, article_code, 0, 0, SUM(topup_qty) FROM {FQ}.fullsize_topup GROUP BY store_id, article_code""")

spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.size_status_post_move AS
SELECT store_id, generic_article, threshold, group_defined, key_after_net,
       CASE WHEN group_defined = 0 OR threshold IS NULL THEN 'No size rule'
            WHEN key_after_net > threshold THEN 'Full size' ELSE 'Cut size' END AS size_status
FROM (
  SELECT p.store_id, p.generic_article,
    CASE WHEN MAX(ak.group_defined) = 1
         THEN GREATEST(MAX(ak.threshold) - (CASE WHEN MAX({GRK('sg.store_grade')}) <= 2 THEN 0 ELSE 1 END), 1)
         ELSE NULL END AS threshold,
    MAX(ak.group_defined) AS group_defined,
    COUNT(DISTINCT CASE WHEN ak.is_key_size = 1 AND p.pos_after > 0 THEN p.article_code END) AS key_after_net
  FROM {FQ}.position_post_move p
  JOIN {FQ}.art_size_key ak ON ak.article_code = p.article_code
  LEFT JOIN _store_grade sg ON sg.store_id = p.store_id
  GROUP BY p.store_id, p.generic_article
)
""")

# COMMAND ----------
# Guard invariant: no actioned receiver × parent (store or DC inbound) may end cut-size unless no size rule.
leak = spark.sql(f"""
  WITH recv AS (SELECT DISTINCT receiver_store AS store_id, generic_article FROM {FQ}.match_reshaped
                UNION SELECT DISTINCT store_id, generic_article FROM {FQ}.replen_final WHERE final_qty >= 1
                UNION SELECT DISTINCT store_id, generic_article FROM {FQ}.fullsize_topup)
  SELECT COUNT(*) n FROM recv JOIN {FQ}.size_status_post_move s USING (store_id, generic_article)
  WHERE s.size_status = 'Cut size'
""").first()["n"]
print(f"receivers left cut-size after final moves: {leak} (0 expected, barring donor-side interactions)")

display(spark.sql(f"""SELECT recv_action, COUNT(*) pairs, SUM(consol_in_units) consol_units, SUM(replen_in_units) replen_units,
  SUM(topup_lines) topup_units FROM {FQ}.receiver_guard_decision GROUP BY recv_action ORDER BY pairs DESC"""))
