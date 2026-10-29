# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Donor / Receiver Pools + Guards — Nike Consolidation POC
# MAGIC
# MAGIC Spec Steps 4, 5, 5a; C4/C7/C8. **Donor pool**: DEAD/SLOW_MOVING with `effective_stock > ROS*PROTECT`.
# MAGIC **Receiver pool**: HEALTHY, `cover_days < TARGET`, not APO-blocked. **APO guard (C4)**: per store, any
# MAGIC child APO (`atp_detail.apo_recieving_site <> '#'`) blocks the whole parent as receiver. Country carried
# MAGIC on every row; intra-country (C7) enforced at matching (04). BU eligibility (C8) via `cfg_bu_eligibility`.
# MAGIC Multi-country aware (`COUNTRY='ALL'` ⇒ no country filter). Reads frozen `run_config`.

# COMMAND ----------
CATALOG = "sports_analytics_test"; SCHEMA = "nike_consolidation"; FQ = f"{CATALOG}.{SCHEMA}"
cfg = {r["param_name"]: r["param_value"] for r in spark.table(f"{FQ}.run_config").collect()}
COUNTRY = cfg["COUNTRY"]; AS_OF = cfg["AS_OF_DATE"]
# grade rank (active store list): BEACON/JORDAN=0, A+=1, A=2, B=3, C=4, NVS=5, else 6. "A+ class" = rank<=1.
def GRK(c):
    return (f"(CASE {c} WHEN 'BEACON' THEN 0 WHEN 'JORDAN' THEN 0 WHEN 'A+' THEN 1 "
            f"WHEN 'A' THEN 2 WHEN 'B' THEN 3 WHEN 'C' THEN 4 WHEN 'NVS' THEN 5 ELSE 6 END)")
PROTECT = int(cfg["PROTECT_COVER_DAYS"]); TARGET = int(cfg["TARGET_COVER_DAYS"])
ATP_AS_OF = cfg.get("ATP_AS_OF") or AS_OF   # inventory anchor (D) — APO is an inventory-snapshot flag
RUN_ID = cfg.get("RUN_ID", "")
MOVEBACK_DAYS = int(cfg.get("MOVEBACK_BLOCK_DAYS") or 30)   # no move-back window (business 2026-10-07)
_cc = ", ".join("'" + c.strip() + "'" for c in COUNTRY.split(",") if c.strip())   # COUNTRY may be a list
atp_country = "" if COUNTRY == "ALL" else f"AND a.country_code IN ({_cc})"
# Current-season ordinal for the freshness gate; recompute from AS_OF if a partial rerun left it unset.
if (cfg.get("CURRENT_SEASON_ORDINAL") or "").strip():
    CUR_ORDINAL = int(cfg["CURRENT_SEASON_ORDINAL"]); CUR_LABEL = cfg.get("CURRENT_SEASON_LABEL", "")
else:
    from datetime import date
    _d = date.fromisoformat(AS_OF); _i = (_d.month - 1) // 3
    CUR_ORDINAL = _d.year * 4 + _i; CUR_LABEL = ["SPRG","SUMR","FALL","HOLI"][_i] + f" {_d.year}"
print(f"COUNTRY={COUNTRY} AS_OF={AS_OF} PROTECT={PROTECT} TARGET={TARGET} CURRENT_SEASON={CUR_LABEL}(ord {CUR_ORDINAL})")

# COMMAND ----------
# APO block set (store × parent) — C4
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.apo_block AS
SELECT DISTINCT a.country_code AS country, a.site_code AS store_id, a.generic_article
FROM sap_gmg_datalake.bronze.atp_detail a
WHERE a.storage_location = 'SL01' AND a.apo_recieving_site <> '#' {atp_country}
  AND a.c_file_date = (SELECT MAX(c_file_date) FROM sap_gmg_datalake.bronze.atp_detail
                       WHERE c_file_date <= DATE'{ATP_AS_OF}')
""")

# COMMAND ----------
# NO MOVE-BACK block (business 2026-10-07, point 1): if an article was recommended OUT of a store (as donor) in
# any run of the last MOVEBACK_DAYS days, never send that article back INTO the same store — neither by store
# consolidation (receiver pool, 05 display branch) nor by DC replenishment (11) nor full-size top-up (08).
# Source = our own recommendations_history (APO in atp_detail carries no date, so it can't express "30 days").
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.moveback_block AS
SELECT donor_store AS store_id, article AS article_code, MAX(parent_article) AS generic_article,
       MAX(as_of_date) AS last_moved_out, COUNT(DISTINCT run_id) AS runs
FROM {FQ}.recommendations_history
WHERE as_of_date > date_sub(DATE'{AS_OF}', {MOVEBACK_DAYS}) AND as_of_date <= DATE'{AS_OF}'
  AND run_id <> '{RUN_ID}'
GROUP BY donor_store, article
""")

# COMMAND ----------
# Article -> size-curve key lookup (shared by 04 receiver-completion boost, 05 reshape, 08 guard, 10-11 replen).
# ONE definition table `cfg_size_curve` holds ALL genders (business 2026-10-28: combined upload).
# ADULT (MENS/WOMENS/UNISEX): AH2 = CM_CDT3_TXTMD for footwear / constant 'APPAREL' for apparel, one ladder per
# (division, ah2, gender). KIDS: ah2 = the product-master SUB-CATEGORY (CM_CDT3_TXTMD, e.g. HOODIES, SANDALS)
# for apparel AND footwear, and each kids size belongs to an AGE BAND (id1) via `cfg_kids_size_band`
# (size -> id1, e.g. 2T->l, 1C->CRIB/T). A kids LADDER = (division, sub-category, id1) with one threshold.
# A size can sit in several bands (1C-4C = CRIB and T), so each kids PARENT is assigned its band by best
# Jaccard fit of its size footprint vs each ladder's key sizes; every article inherits that ladder's
# threshold + key sizes. Sizes/sub-categories not in the definition -> group_defined=0 => NO_SIZE_DEF fallback.
# Merge point = this one table, so 04/05/06/08/10-11 need no change.
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.art_size_key AS
WITH art AS (
  SELECT p.MATERIAL AS article_code, TRIM(p.`/BIC/ZRWGH2_T`) AS division,
         CASE WHEN TRIM(p.`/BIC/ZRWGH2_T`) = 'APPAREL' THEN 'APPAREL' ELSE TRIM(p.CM_CDT3_TXTMD) END AS ah2,
         TRIM(p.`/BIC/ZCHWGH5`) AS gender, TRIM(p.RT_SIZE) AS size
  FROM prod_mena.gold.mena_sports_product_master_view p WHERE p.MATL_CAT = '02'
),
grp AS (SELECT UPPER(division) d, UPPER(ah2_description) a, UPPER(gender) g, MAX(threshold) threshold
        FROM {FQ}.cfg_size_curve WHERE UPPER(gender) <> 'KIDS' GROUP BY 1,2,3),
sz  AS (SELECT DISTINCT UPPER(division) d, UPPER(ah2_description) a, UPPER(gender) g, TRIM(size) s
        FROM {FQ}.cfg_size_curve WHERE UPPER(gender) <> 'KIDS'),
adult AS (   -- unchanged adult logic; kids excluded (handled by the kids branch below)
  SELECT art.article_code, grp.threshold,
         CASE WHEN grp.threshold IS NOT NULL THEN 1 ELSE 0 END AS group_defined,
         CASE WHEN sz.s IS NOT NULL THEN 1 ELSE 0 END AS is_key_size
  FROM art
  LEFT JOIN grp ON grp.d = UPPER(art.division) AND grp.a = UPPER(art.ah2) AND grp.g = UPPER(art.gender)
  LEFT JOIN sz  ON sz.d = UPPER(art.division) AND sz.a = UPPER(art.ah2) AND sz.g = UPPER(art.gender) AND sz.s = art.size
  WHERE UPPER(art.gender) <> 'KIDS'
),
-- ===== KIDS module: gender = KIDS; ladder = (division, sub-category, age band id1) =====
kart AS (   -- kids articles + parent + sub-category (CM_CDT3_TXTMD, apparel and footwear alike)
  SELECT p.MATERIAL AS article_code, p.RT_CONFMAT AS parent, UPPER(TRIM(p.`/BIC/ZRWGH2_T`)) AS d,
         UPPER(TRIM(p.CM_CDT3_TXTMD)) AS a,
         UPPER(TRIM(CASE WHEN TRIM(p.RT_SIZE) = '-' THEN '0.5' ELSE TRIM(p.RT_SIZE) END)) AS size
  FROM prod_mena.gold.mena_sports_product_master_view p
  WHERE p.MATL_CAT = '02' AND UPPER(TRIM(p.`/BIC/ZCHWGH5`)) = 'KIDS'
),
kc   AS (   -- kids key sizes per ladder: definition rows (gender KIDS) x size->age-band mapping
  SELECT DISTINCT UPPER(TRIM(sc.division)) d, UPPER(TRIM(sc.ah2_description)) a, b.id1 g,
         UPPER(TRIM(sc.size)) s, CAST(sc.threshold AS INT) threshold
  FROM {FQ}.cfg_size_curve sc
  JOIN (SELECT DISTINCT UPPER(TRIM(size)) s, UPPER(TRIM(id1)) id1 FROM {FQ}.cfg_kids_size_band) b
    ON b.s = UPPER(TRIM(sc.size))
  WHERE UPPER(sc.gender) = 'KIDS'
),
kgrp AS (SELECT d, a, g, MAX(threshold) threshold, COUNT(DISTINCT s) grp_n FROM kc GROUP BY d, a, g),
kfoot AS (   -- parent size footprint, restricted to sizes defined in SOME grouping of that (div, id2)
  SELECT DISTINCT ka.parent, ka.d, ka.a, ka.size AS s
  FROM kart ka JOIN (SELECT DISTINCT d, a, s FROM kc) ks ON ks.d = ka.d AND ks.a = ka.a AND ks.s = ka.size
),
kpar AS (SELECT parent, d, a, COUNT(DISTINCT s) par_n FROM kfoot GROUP BY parent, d, a),
kscore AS (   -- |footprint ∩ grouping| per (parent, grouping)
  SELECT f.parent, kgrp.d, kgrp.a, kgrp.g, kgrp.threshold, kgrp.grp_n,
         COUNT(DISTINCT CASE WHEN kc2.s IS NOT NULL THEN f.s END) AS inter
  FROM kfoot f
  JOIN kgrp ON kgrp.d = f.d AND kgrp.a = f.a
  LEFT JOIN kc kc2 ON kc2.d = kgrp.d AND kc2.a = kgrp.a AND kc2.g = kgrp.g AND kc2.s = f.s
  GROUP BY f.parent, kgrp.d, kgrp.a, kgrp.g, kgrp.threshold, kgrp.grp_n
),
kbest AS (   -- best-fit grouping per parent = max Jaccard; deterministic tiebreak by grouping name
  SELECT s.parent, s.g AS grouping, s.threshold
  FROM kscore s JOIN kpar p ON p.parent = s.parent AND p.d = s.d AND p.a = s.a
  WHERE s.inter > 0
  QUALIFY ROW_NUMBER() OVER (PARTITION BY s.parent
            ORDER BY (s.inter * 1.0) / NULLIF(s.grp_n + p.par_n - s.inter, 0) DESC, s.g ASC) = 1
),
kids AS (
  SELECT ka.article_code, kb.threshold,
         CASE WHEN kb.grouping IS NOT NULL THEN 1 ELSE 0 END AS group_defined,
         CASE WHEN kk.s IS NOT NULL THEN 1 ELSE 0 END AS is_key_size
  FROM kart ka
  LEFT JOIN kbest kb ON kb.parent = ka.parent
  LEFT JOIN kc kk ON kk.d = ka.d AND kk.a = ka.a AND kk.g = kb.grouping AND kk.s = ka.size
)
SELECT article_code, threshold, group_defined, is_key_size FROM adult
UNION ALL
SELECT article_code, threshold, group_defined, is_key_size FROM kids
""")

# COMMAND ----------
# Article -> season identity (C10 freshness). season = RF_SEASON + RT_SEAYR (product master).
# Carry-forward codes (FACF/SPCF/SUCF/HOCF) collapse to their base season. CORE/YEAR (and any
# unmapped/blank season, or non-numeric year) => always_allowed=1 (season-agnostic, never blocked).
# ordinal = year*4 + {SPRG:0,SUMR:1,FALL:2,HOLI:3}; consecutive seasons differ by 1.
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.art_season AS
WITH s AS (
  SELECT p.MATERIAL AS article_code, UPPER(TRIM(p.RF_SEASON)) AS season_raw, TRIM(p.RT_SEAYR) AS season_yr_raw,
         CASE UPPER(TRIM(p.RF_SEASON))
           WHEN 'SPRG' THEN 0 WHEN 'SPCF' THEN 0
           WHEN 'SUMR' THEN 1 WHEN 'SUCF' THEN 1
           WHEN 'FALL' THEN 2 WHEN 'FACF' THEN 2
           WHEN 'HOLI' THEN 3 WHEN 'HOCF' THEN 3 ELSE NULL END AS season_idx
  FROM prod_mena.gold.mena_sports_product_master_view p WHERE p.MATL_CAT = '02'
),
d AS (
  SELECT article_code,
         MAX(CASE WHEN season_yr_raw RLIKE '^[0-9]{{4}}$' THEN CAST(season_yr_raw AS INT) END) AS season_year,
         MAX(season_idx) AS season_idx,
         MAX(season_raw) AS season_raw
  FROM s GROUP BY article_code
)
SELECT article_code,
       CASE WHEN season_idx IS NOT NULL AND season_year IS NOT NULL THEN season_year * 4 + season_idx END AS season_ordinal,
       CASE WHEN season_idx IS NULL OR season_year IS NULL OR season_raw IN ('CORE','YEAR') THEN 1 ELSE 0 END AS always_allowed,
       CASE WHEN season_idx IS NULL OR season_year IS NULL THEN COALESCE(season_raw,'UNKNOWN')
            ELSE CONCAT(element_at(array('SPRG','SUMR','FALL','HOLI'), season_idx + 1), ' ', season_year) END AS season_label
FROM d
""")

# COMMAND ----------
# Donor pool (Step 4)
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.donor_pool AS
SELECT country, store_id, store_name, bu, store_grade, generic_article, article_code, product_size,
       atp_qty, intransit_qty, effective_stock, unit_cost_aed,
       units_sold, net_sales, net_sales_per_unit, ros, cover_days, classification, as_of_date,
       (effective_stock - ros * {PROTECT}) AS donor_can_give
FROM {FQ}.stock_classified
WHERE classification IN ('DEAD', 'SLOW_MOVING') AND effective_stock > ros * {PROTECT}
""")

# COMMAND ----------
# A+ SIZE-CURVE COMPLETION demand (point 3, 2026-07-28) — flagship display, no ROS required. For A+ grade
# doors, CURRENT SEASON only, complete the size ladder of parents the store ALREADY carries: for each KEY
# size the store is MISSING (no ATP row) that is NOT in the country DC, create 1 unit of demand (display goal
# = one of each on the wall), sourced from dead/slow donors. Skip if the size is already inbound (in-transit).
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.aplus_missing_sizes AS
WITH aplus AS (   -- A+ class doors (incl BEACON/JORDAN, grade_rank<=1) + parents they already carry (>=1 size)
  SELECT DISTINCT e.store_id, e.country, e.store_name, e.bu, e.store_grade, e.generic_article
  FROM {FQ}.effective_stock e WHERE {GRK('e.store_grade')} <= 1 AND e.effective_stock > 0
),
parent_key_arts AS (   -- current-season KEY-size article_codes per parent that EXIST in the network (fillable)
  SELECT DISTINCT e.generic_article, e.article_code, e.product_size
  FROM {FQ}.effective_stock e
  JOIN {FQ}.art_size_key ak ON ak.article_code = e.article_code
  JOIN {FQ}.art_season   s  ON s.article_code = e.article_code
  WHERE ak.is_key_size = 1 AND ak.group_defined = 1 AND s.season_ordinal = {CUR_ORDINAL}
)
SELECT ap.country, ap.store_id, ap.store_name, ap.bu, ap.store_grade,
       pk.generic_article, pk.article_code, pk.product_size
FROM aplus ap
JOIN parent_key_arts pk ON pk.generic_article = ap.generic_article
LEFT JOIN {FQ}.effective_stock es ON es.store_id = ap.store_id AND es.article_code = pk.article_code
LEFT JOIN {FQ}.warehouse_stock ws ON ws.country = ap.country AND ws.article_code = pk.article_code
LEFT JOIN {FQ}.store_intransit it ON it.store_id = ap.store_id AND it.article_code = pk.article_code
WHERE es.article_code IS NULL              -- key size MISSING at the A+ store
  AND COALESCE(ws.wh_atp, 0) = 0           -- not in the country DC (guard)
  AND COALESCE(it.intransit_qty, 0) = 0    -- nothing already inbound
""")

# COMMAND ----------
# Receiver pool (Step 5 + APO guard 5a + season-freshness gate C10) — a UNION of THREE demand universes:
#   • OOS_SELLING (demand_priority 0) — sells it (ros>0) but STOCKED OUT (no ATP row); point 1. From `sales_demand`.
#   • IN_STOCK  (demand_priority 1) — classic receivers: HEALTHY, ATP>0, under target cover.
#   • APLUS_CURVE (demand_priority 2) — A+ flagship current-season size-curve completion (no ROS); point 3.
# All branches carry (point 4) INTRANSIT-aware need/cover — receiver_can_take = ros*TARGET-(ATP+intransit),
# gate on (ATP+intransit)/ros < TARGET — and (point 2, D-guard=ALL receivers) the WAREHOUSE GUARD: drop any
# receiver whose size the country DC still holds (`warehouse_stock`) — replenishment DC->store fulfils it,
# this engine is consolidation only. `receiver_in_warehouse` kept for audit (always 'N' after the guard).
# Season-freshness (C10) + APO guard apply to all branches. DONORS are unaffected (they give ATP only).
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.receiver_pool AS
WITH in_stock AS (
  SELECT r.country, r.store_id, r.store_name, r.bu, r.store_grade, r.generic_article, r.article_code, r.product_size,
         r.atp_qty, r.intransit_qty, r.effective_stock, r.unit_cost_aed,
         r.units_sold, r.net_sales, r.net_sales_per_unit, r.ros, r.cover_days, r.classification,
         (r.ros * {TARGET} - (r.effective_stock + r.intransit_qty)) AS receiver_can_take,
         (r.effective_stock + r.intransit_qty) / r.ros AS receiver_cover_incl_intransit,
         'IN_STOCK' AS demand_type, 1 AS demand_priority
  FROM {FQ}.stock_classified r
  WHERE r.classification = 'HEALTHY' AND ((r.effective_stock + r.intransit_qty) / r.ros) < {TARGET}
),
oos AS (   -- out-of-stock but selling: ros>0, no ATP row (effective_stock=0), intransit-aware need
  SELECT sd.country, sd.store_id, sd.store_name, sd.bu, sd.store_grade, sd.generic_article, sd.article_code, sd.product_size,
         0 AS atp_qty, COALESCE(it.intransit_qty, 0) AS intransit_qty, 0 AS effective_stock,
         CAST(NULL AS double) AS unit_cost_aed,
         sd.units_sold, sd.net_sales, sd.net_sales_per_unit, sd.ros, CAST(0 AS double) AS cover_days, 'OOS_SELLING' AS classification,
         (sd.ros * {TARGET} - COALESCE(it.intransit_qty, 0)) AS receiver_can_take,
         COALESCE(it.intransit_qty, 0) / sd.ros AS receiver_cover_incl_intransit,
         'OOS_SELLING' AS demand_type, 0 AS demand_priority
  FROM {FQ}.sales_demand sd
  LEFT JOIN {FQ}.stock_classified sc ON sc.store_id = sd.store_id AND sc.article_code = sd.article_code
  LEFT JOIN {FQ}.store_intransit  it ON it.store_id = sd.store_id AND it.article_code = sd.article_code
  WHERE sd.ros > 0
    AND COALESCE(sc.effective_stock, 0) = 0                               -- pure stocked-out (in-stock owned by in_stock branch)
    AND (sd.ros * {TARGET} - COALESCE(it.intransit_qty, 0)) > 0           -- still needs a unit after inbound intransit
),
aplus AS (   -- A+ curve completion: 1 unit per missing current-season key size, ros=0
  SELECT ams.country, ams.store_id, ams.store_name, ams.bu, ams.store_grade,
         ams.generic_article, ams.article_code, ams.product_size,
         0 AS atp_qty, 0 AS intransit_qty, 0 AS effective_stock, CAST(NULL AS double) AS unit_cost_aed,
         CAST(0 AS bigint) AS units_sold, CAST(0 AS double) AS net_sales, CAST(NULL AS double) AS net_sales_per_unit,
         CAST(0 AS double) AS ros, CAST(0 AS double) AS cover_days, 'APLUS_CURVE' AS classification,
         CAST(1 AS double) AS receiver_can_take, CAST(0 AS double) AS receiver_cover_incl_intransit,
         'APLUS_CURVE' AS demand_type, 2 AS demand_priority
  FROM {FQ}.aplus_missing_sizes ams
),
u AS (SELECT * FROM in_stock UNION ALL SELECT * FROM oos UNION ALL SELECT * FROM aplus)
SELECT u.country, u.store_id, u.store_name, u.bu, u.store_grade, u.generic_article, u.article_code, u.product_size,
       u.atp_qty, u.intransit_qty, u.effective_stock, u.unit_cost_aed,
       u.units_sold, u.net_sales, u.net_sales_per_unit, u.ros, u.cover_days, u.classification,
       DATE'{AS_OF}' AS as_of_date, u.receiver_can_take, u.receiver_cover_incl_intransit,
       u.demand_type, u.demand_priority,
       CASE WHEN ws.article_code IS NOT NULL THEN 'Y' ELSE 'N' END AS receiver_in_warehouse
FROM u
LEFT ANTI JOIN {FQ}.apo_block ab
  ON ab.store_id = u.store_id AND ab.generic_article = u.generic_article
LEFT ANTI JOIN {FQ}.moveback_block mb                                     -- no move-back within 30 days
  ON mb.store_id = u.store_id AND mb.article_code = u.article_code
LEFT JOIN {FQ}.warehouse_stock ws ON ws.country = u.country AND ws.article_code = u.article_code  -- point 2 guard
LEFT JOIN {FQ}.art_season asn ON asn.article_code = u.article_code
LEFT JOIN {FQ}.cfg_store_season cs ON cs.store_id = u.store_id
WHERE (COALESCE(ws.wh_atp, 0) = 0                                         -- UNIFIED DC GUARD (business 2026-09-22):
       OR (u.country <> 'AE' AND u.cover_days >= 28))                     -- UAE: DC-first whenever the hub (1D00+1D01)
                                                                          -- holds it. Outstation: DC-first only when the
                                                                          -- receiver's size cover < 4 weeks (28d); else
                                                                          -- keep for in-country store consolidation.
  AND (cs.store_id IS NULL                                                -- store not season-restricted
       OR COALESCE(asn.always_allowed, 1) = 1                            -- CORE/YEAR/unknown season
       OR (asn.season_ordinal IS NOT NULL
           AND asn.season_ordinal >= {CUR_ORDINAL} - cs.allow_seasons_back))  -- current-or-newer window
-- DEDUPE to ONE demand per (store, article): a stocked-out size at an A+ door can qualify as BOTH
-- OOS_SELLING and APLUS_CURVE (both need ATP=0). Keep the highest priority (OOS 0 > IN_STOCK 1 > A+ 2) so
-- the same store×size is never counted/served twice (else a donor over-allocates, e.g. 4+1 > stock 4).
QUALIFY ROW_NUMBER() OVER (PARTITION BY u.store_id, u.article_code
                           ORDER BY u.demand_priority ASC, u.demand_type ASC) = 1
""")

# COMMAND ----------
# Season-block diagnostic (C10): receiver candidates that WOULD qualify (HEALTHY, cover<TARGET,
# not APO-blocked, positive need) but were dropped purely because their season is too old for the
# store's freshness rule. Counted in run_stats for business visibility (mirrors the APO guard KPI).
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.season_block AS
SELECT r.country, r.store_id, r.store_name, r.generic_article, r.article_code, asn.season_label,
       cs.allow_seasons_back, '{CUR_LABEL}' AS current_season,
       (r.ros * {TARGET} - r.effective_stock) AS receiver_can_take, r.ros
FROM {FQ}.stock_classified r
LEFT ANTI JOIN {FQ}.apo_block ab
  ON ab.store_id = r.store_id AND ab.generic_article = r.generic_article
JOIN {FQ}.cfg_store_season cs ON cs.store_id = r.store_id
LEFT JOIN {FQ}.art_season asn ON asn.article_code = r.article_code
WHERE r.classification = 'HEALTHY' AND r.cover_days < {TARGET}
  AND (r.ros * {TARGET} - r.effective_stock) > 0
  AND COALESCE(asn.always_allowed, 1) = 0
  AND (asn.season_ordinal IS NULL OR asn.season_ordinal < {CUR_ORDINAL} - cs.allow_seasons_back)
""")

# COMMAND ----------
# APO receiver-block diagnostic (C4): receiver candidates that WOULD qualify (HEALTHY, cover<TARGET,
# positive need) but were removed by the APO guard (store×parent in apo_block). Mirror of season_block;
# lets 06 attribute donor stock that could not move to an APO-blocked receiver.
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.apo_recv_block AS
SELECT r.country, r.store_id, r.store_name, r.generic_article, r.article_code,
       (r.ros * {TARGET} - r.effective_stock) AS receiver_can_take, r.ros
FROM {FQ}.stock_classified r
JOIN {FQ}.apo_block ab ON ab.store_id = r.store_id AND ab.generic_article = r.generic_article
WHERE r.classification = 'HEALTHY' AND r.cover_days < {TARGET}
  AND (r.ros * {TARGET} - r.effective_stock) > 0
""")

# COMMAND ----------
# Move-back receiver-block diagnostic: receiver candidates (HEALTHY, cover<TARGET, positive need, not APO) removed
# only because the article left that store in the last 30 days. Feeds 06 held Part B ('Move-back <30d').
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.moveback_recv_block AS
SELECT r.country, r.store_id, r.store_name, r.generic_article, r.article_code,
       (r.ros * {TARGET} - (r.effective_stock + r.intransit_qty)) AS receiver_can_take, r.ros, mb.last_moved_out
FROM {FQ}.stock_classified r
JOIN {FQ}.moveback_block mb ON mb.store_id = r.store_id AND mb.article_code = r.article_code
LEFT ANTI JOIN {FQ}.apo_block ab ON ab.store_id = r.store_id AND ab.generic_article = r.generic_article
WHERE r.classification = 'HEALTHY' AND ((r.effective_stock + r.intransit_qty) / r.ros) < {TARGET}
  AND (r.ros * {TARGET} - (r.effective_stock + r.intransit_qty)) > 0
""")

# COMMAND ----------
display(spark.sql(f"""
  SELECT 'donor_pool' pool, COUNT(*) rows, COUNT(DISTINCT store_id) stores, ROUND(SUM(donor_can_give),0) units FROM {FQ}.donor_pool
  UNION ALL SELECT 'receiver_pool', COUNT(*), COUNT(DISTINCT store_id), ROUND(SUM(receiver_can_take),0) FROM {FQ}.receiver_pool
  UNION ALL SELECT 'apo_block', COUNT(*), COUNT(DISTINCT store_id), NULL FROM {FQ}.apo_block
  UNION ALL SELECT 'season_block', COUNT(*), COUNT(DISTINCT store_id), NULL FROM {FQ}.season_block
  UNION ALL SELECT 'apo_recv_block', COUNT(*), COUNT(DISTINCT store_id), NULL FROM {FQ}.apo_recv_block
  UNION ALL SELECT 'moveback_block', COUNT(*), COUNT(DISTINCT store_id), NULL FROM {FQ}.moveback_block
  UNION ALL SELECT 'moveback_recv_block', COUNT(*), COUNT(DISTINCT store_id), NULL FROM {FQ}.moveback_recv_block
"""))
