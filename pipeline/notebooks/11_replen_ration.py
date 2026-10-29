# Databricks notebook source
# MAGIC %md
# MAGIC # 11 · Replenishment Rationing — primary DC + shared hub (P1, additive)
# MAGIC
# MAGIC Takes `replen_demand` (need per Nike store × article) and rations the finite DC supply with a
# MAGIC **two-phase** waterfall (business 2026-09-18 — primary + fallback, hub globally rationed):
# MAGIC
# MAGIC - **Phase 1 — exclusive primary pool:** KSA stores draw 2D22, BAH stores draw 3D31 (each pool exclusive to
# MAGIC   its country). Two-pass waterfall per `(country, article)`: presentation breadth then demand depth.
# MAGIC - **Phase 2 — shared UAE hub (1D00 + 1D01):** the RESIDUAL need (KSA/BAH shortfall + all UAE/KWT/OMN/QTR
# MAGIC   need, whose primary IS the hub) is filled from the hub, rationed **globally per `article`** so the shared
# MAGIC   hub is never double-counted across countries.
# MAGIC
# MAGIC **Priority (total order):** grade (BEACON/JORDAN→A+→A→B→C→NVS) → demand_rate desc → value-at-risk desc →
# MAGIC store_id. `recommended_qty = alloc_primary + alloc_hub`, whole units, capped at `MAX_LINE_QTY`.
# MAGIC **Invariants:** Σ alloc_primary ≤ primary pool per (country, article); Σ alloc_hub ≤ hub pool per article.

# COMMAND ----------
CATALOG = "sports_analytics_test"; SCHEMA = "nike_consolidation"; FQ = f"{CATALOG}.{SCHEMA}"
rp = {r["param_name"]: r["param_value"] for r in spark.table(f"{FQ}.cfg_replen_params").collect()}
MAX_LINE = int(rp.get("MAX_LINE_QTY", "24"))
print(f"MAX_LINE_QTY={MAX_LINE}")

# COMMAND ----------
# Priority ordering reused by both phases (grade → demand → value-at-risk → store_id).
PRIO = "grade_rank, demand_rate_day DESC, value_at_risk_aed DESC, store_id"
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.replen_allocated AS
WITH elig AS (   -- real need + some reachable DC stock (primary or hub) + not APO-blocked + not moved out <30d
  SELECT d.*,
    (CASE d.store_grade WHEN 'BEACON' THEN 0 WHEN 'JORDAN' THEN 0 WHEN 'A+' THEN 1
       WHEN 'A' THEN 2 WHEN 'B' THEN 3 WHEN 'C' THEN 4 WHEN 'NVS' THEN 5 ELSE 6 END) AS grade_rank,
    ROUND(d.demand_rate_day * 365 * d.net_per_unit_aed, 0) AS value_at_risk_aed
  FROM {FQ}.replen_demand d
  LEFT ANTI JOIN {FQ}.apo_block ab ON ab.store_id = d.store_id AND ab.generic_article = d.generic_article
  LEFT ANTI JOIN {FQ}.moveback_block mb ON mb.store_id = d.store_id AND mb.article_code = d.article_code  -- no move-back <30d
  WHERE d.need_units > 0 AND (d.primary_atp > 0 OR d.hub_atp > 0)
),
-- ===== PHASE 1: exclusive primary pool (KSA=2D22, BAH=3D31), per (country, article) =====
p1e  AS (SELECT * FROM elig WHERE primary_pool IS NOT NULL AND primary_atp > 0),
p1_1 AS (SELECT *, LEAST(need_units, GREATEST(display_floor, 1)) AS d1 FROM p1e),
p1_1c AS (SELECT *, COALESCE(SUM(d1) OVER (PARTITION BY country, article_code
            ORDER BY {PRIO} ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS cum1 FROM p1_1),
p1_1a AS (SELECT *, GREATEST(0, LEAST(d1, primary_atp - cum1)) AS a1 FROM p1_1c),
p1_ps AS (SELECT country, article_code, MAX(primary_atp) AS dc, SUM(a1) AS used1 FROM p1_1a GROUP BY country, article_code),
p1_2 AS (SELECT p1_1a.*, (need_units - a1) AS d2, (ps.dc - ps.used1) AS supply2
         FROM p1_1a JOIN p1_ps ps USING (country, article_code)),
p1_2c AS (SELECT *, COALESCE(SUM(d2) OVER (PARTITION BY country, article_code
            ORDER BY {PRIO} ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS cum2 FROM p1_2),
p1 AS (SELECT store_id, article_code,
         CAST(FLOOR(a1 + GREATEST(0, LEAST(d2, supply2 - cum2)) + 1e-4) AS INT) AS alloc_primary FROM p1_2c),
-- residual need carried to the hub phase
resid AS (SELECT e.*, COALESCE(p1.alloc_primary, 0) AS alloc_primary,
                 (e.need_units - COALESCE(p1.alloc_primary, 0)) AS residual_need
          FROM elig e LEFT JOIN p1 ON p1.store_id = e.store_id AND p1.article_code = e.article_code),
-- ===== PHASE 2: shared UAE hub (1D00+1D01), rationed GLOBALLY per article =====
p2e  AS (SELECT * FROM resid WHERE hub_atp > 0 AND residual_need > 0),
p2_1 AS (SELECT *, LEAST(residual_need, GREATEST(display_floor - alloc_primary, 1)) AS d1 FROM p2e),
p2_1c AS (SELECT *, COALESCE(SUM(d1) OVER (PARTITION BY article_code
            ORDER BY {PRIO} ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS cum1 FROM p2_1),
p2_1a AS (SELECT *, GREATEST(0, LEAST(d1, hub_atp - cum1)) AS a1 FROM p2_1c),
p2_ps AS (SELECT article_code, MAX(hub_atp) AS dc, SUM(a1) AS used1 FROM p2_1a GROUP BY article_code),
p2_2 AS (SELECT p2_1a.*, (residual_need - a1) AS d2, (ps.dc - ps.used1) AS supply2
         FROM p2_1a JOIN p2_ps ps USING (article_code)),
p2_2c AS (SELECT *, COALESCE(SUM(d2) OVER (PARTITION BY article_code
            ORDER BY {PRIO} ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS cum2 FROM p2_2),
p2 AS (SELECT store_id, article_code,
         CAST(FLOOR(a1 + GREATEST(0, LEAST(d2, supply2 - cum2)) + 1e-4) AS INT) AS alloc_hub FROM p2_2c)
SELECT r.*, COALESCE(p2.alloc_hub, 0) AS alloc_hub,
       LEAST(CAST(r.alloc_primary + COALESCE(p2.alloc_hub, 0) AS INT), {MAX_LINE}) AS rationed_qty
FROM resid r LEFT JOIN p2 ON p2.store_id = r.store_id AND p2.article_code = r.article_code
""")

# COMMAND ----------
# Invariants — no pool over-allocates (fail fast). Primary per (country, article); hub GLOBAL per article.
bad_p = spark.sql(f"""
  WITH p AS (SELECT country, article_code, MAX(primary_atp) dc, SUM(alloc_primary) alloc
             FROM {FQ}.replen_allocated WHERE primary_pool IS NOT NULL GROUP BY country, article_code)
  SELECT COUNT(*) n FROM p WHERE alloc > dc
""").first()["n"]
bad_h = spark.sql(f"""
  WITH p AS (SELECT article_code, MAX(hub_atp) dc, SUM(alloc_hub) alloc
             FROM {FQ}.replen_allocated GROUP BY article_code)
  SELECT COUNT(*) n FROM p WHERE alloc > dc
""").first()["n"]
if (bad_p or 0) > 0 or (bad_h or 0) > 0:
    raise ValueError(f"Rationing over-allocated — primary pools:{bad_p} hub-articles:{bad_h} (waterfall bug).")
print(f"Rationing invariants OK: primary ≤ pool per (country,article); hub ≤ pool per article.")

display(spark.sql(f"""
  SELECT country,
         SUM(CASE WHEN rationed_qty>=1 THEN 1 ELSE 0 END) lines, SUM(rationed_qty) units,
         SUM(alloc_primary) via_primary, SUM(alloc_hub) via_hub,
         COUNT(DISTINCT store_id) stores, COUNT(DISTINCT article_code) skus
  FROM {FQ}.replen_allocated GROUP BY country ORDER BY units DESC
"""))
