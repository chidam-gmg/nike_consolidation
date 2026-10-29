# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Config & Validation — Nike Consolidation POC
# MAGIC
# MAGIC Reads scalar run parameters (widgets, defaulting to `cfg_scalar_params`), applies **fail-fast**
# MAGIC validation, freezes an `AS_OF_DATE` anchor, and writes the authoritative **`run_config`** snapshot
# MAGIC every downstream notebook reads.
# MAGIC
# MAGIC **Multi-country:** `COUNTRY` may be a single ISO2 code (e.g. `AE`) or `ALL` (blank → `ALL`) to process
# MAGIC every country in one run. Moves are still **intra-country only** — enforced in `04` by partitioning
# MAGIC the match on `country` (C7), so a multi-country run never produces cross-border transfers.
# MAGIC
# MAGIC **Determinism:** config is resolved and frozen once here; downstream reads `run_config`, never widgets.

# COMMAND ----------
import uuid
from datetime import datetime, timezone
CATALOG = "sports_analytics_test"; SCHEMA = "nike_consolidation"; FQ = f"{CATALOG}.{SCHEMA}"

# COMMAND ----------
defaults = {r["param_name"]: (r["param_value"] or "")
            for r in spark.table(f"{FQ}.cfg_scalar_params").collect()}

def w(name, fallback=""):
    dbutils.widgets.text(name, defaults.get(name, fallback))
    return dbutils.widgets.get(name).strip()

raw = {
    "ROS_WINDOW_DAYS":            w("ROS_WINDOW_DAYS"),
    "IN_TRANSIT_LOOKBACK_DAYS":   w("IN_TRANSIT_LOOKBACK_DAYS"),
    "SLOW_MOVING_THRESHOLD_DAYS": w("SLOW_MOVING_THRESHOLD_DAYS"),
    "NEW_TO_STORE_DAYS":          w("NEW_TO_STORE_DAYS"),
    "PROTECT_COVER_DAYS":         w("PROTECT_COVER_DAYS"),
    "TARGET_COVER_DAYS":          w("TARGET_COVER_DAYS"),
    "MOVEMENT_COST_PER_UNIT":     w("MOVEMENT_COST_PER_UNIT"),
    "SELL_THROUGH_FACTOR":        w("SELL_THROUGH_FACTOR"),
    "GENERIC_ROS_UPLIFT_MIN":     w("GENERIC_ROS_UPLIFT_MIN"),
    "SALES_DISTR_CHANNELS":       w("SALES_DISTR_CHANNELS"),
    "COUNTRY":                    w("COUNTRY"),
    "CATEGORY":                   w("CATEGORY"),
}
dbutils.widgets.text("AS_OF_DATE", "")
AS_OF_RAW = dbutils.widgets.get("AS_OF_DATE").strip()
dbutils.widgets.text("ATP_AS_OF", "")
ATP_AS_OF_RAW = dbutils.widgets.get("ATP_AS_OF").strip()

# COMMAND ----------
errors = []; cfg = {}
def as_int(name):
    try: return int(raw[name])
    except Exception: errors.append(f"{name} must be an integer, got '{raw[name]}'"); return None
def as_float(name):
    try: return float(raw[name])
    except Exception: errors.append(f"{name} must be a number, got '{raw[name]}'"); return None

for k in ["ROS_WINDOW_DAYS","IN_TRANSIT_LOOKBACK_DAYS","SLOW_MOVING_THRESHOLD_DAYS",
          "NEW_TO_STORE_DAYS","PROTECT_COVER_DAYS","TARGET_COVER_DAYS"]:
    cfg[k] = as_int(k)
cfg["MOVEMENT_COST_PER_UNIT"] = as_float("MOVEMENT_COST_PER_UNIT")   # OPEN-3 placeholder
cfg["SELL_THROUGH_FACTOR"]    = as_float("SELL_THROUGH_FACTOR")      # OPEN-4 placeholder
cfg["GENERIC_ROS_UPLIFT_MIN"] = None if raw["GENERIC_ROS_UPLIFT_MIN"] == "" else as_float("GENERIC_ROS_UPLIFT_MIN")  # CONFIRM-B
cfg["SALES_DISTR_CHANNELS"]   = ",".join([c.strip() for c in raw["SALES_DISTR_CHANNELS"].split(",") if c.strip()])
# COUNTRY: blank => ALL (all countries). Otherwise a comma-separated list of ISO2 codes to INCLUDE
# (e.g. 'OM,QA,BH' or a single 'AE'). Country scope is a RUN parameter — it never edits the store list.
_ctry = [c.strip().upper() for c in raw["COUNTRY"].split(",") if c.strip()]
_bad_ctry = [c for c in _ctry if not (c.isalpha() and 2 <= len(c) <= 3)]
if _bad_ctry:
    errors.append(f"COUNTRY has invalid code(s): {_bad_ctry} (use ISO2 like AE, SA — or blank for ALL).")
cfg["COUNTRY"] = "ALL" if (not _ctry or "ALL" in _ctry) else ",".join(dict.fromkeys(_ctry))
# CATEGORY: blank => ALL. Otherwise a '|'-separated list of business categories (category converter, e.g.
# 'FOOTBALL|RUNNING MENS') — the run is scoped to those articles only. A RUN parameter, like COUNTRY.
_cat = [c.strip().upper() for c in raw["CATEGORY"].split("|") if c.strip()]
cfg["CATEGORY"] = "ALL" if (not _cat or "ALL" in _cat) else "|".join(dict.fromkeys(_cat))

if not cfg["SALES_DISTR_CHANNELS"]:
    errors.append("SALES_DISTR_CHANNELS is required (e.g. '10' for retail).")
for k in ["ROS_WINDOW_DAYS","IN_TRANSIT_LOOKBACK_DAYS","SLOW_MOVING_THRESHOLD_DAYS",
          "NEW_TO_STORE_DAYS","PROTECT_COVER_DAYS","TARGET_COVER_DAYS"]:
    if cfg.get(k) is not None and cfg[k] <= 0:
        errors.append(f"{k} must be > 0, got {cfg[k]}")
if cfg.get("PROTECT_COVER_DAYS") is not None and cfg.get("TARGET_COVER_DAYS") is not None:
    if not (cfg["PROTECT_COVER_DAYS"] < cfg["TARGET_COVER_DAYS"]):
        errors.append(f"PROTECT_COVER_DAYS ({cfg['PROTECT_COVER_DAYS']}) must be < "
                      f"TARGET_COVER_DAYS ({cfg['TARGET_COVER_DAYS']}) — otherwise stock would circulate.")
if errors:
    raise ValueError("Config validation FAILED:\n- " + "\n- ".join(errors))
print("Config validation passed.")

# COMMAND ----------
if AS_OF_RAW == "":
    as_of = spark.sql("SELECT date_sub(current_date(), 1) AS d").first()["d"]
else:
    as_of = datetime.strptime(AS_OF_RAW, "%Y-%m-%d").date()
# INVENTORY anchor (business 2026-09-29): ATP + in-transit use the LATEST snapshot = TODAY (D); demand
# (sales/GRN/season) stays AS_OF = D-1. Blank => current_date (today); overridable for backtests (reproducible).
if ATP_AS_OF_RAW == "":
    atp_as_of = spark.sql("SELECT current_date() AS d").first()["d"]
else:
    atp_as_of = datetime.strptime(ATP_AS_OF_RAW, "%Y-%m-%d").date()
_now = datetime.now(timezone.utc)
run_ts = _now.isoformat()
# RUN_ID: stable per-run key for the *_history archive (07). Time-ordered + short uuid so two runs in
# the same second never collide. Downstream 07 re-uses this to key/replace this run's archived rows.
# The app supplies RUN_ID + TRIGGERED_BY so it can read back exactly ITS run (per-user isolation); a
# manual/CLI run leaves them blank and we mint/attribute here.
dbutils.widgets.text("RUN_ID", ""); dbutils.widgets.text("TRIGGERED_BY", "")
run_id = dbutils.widgets.get("RUN_ID").strip() or (_now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
triggered_by = dbutils.widgets.get("TRIGGERED_BY").strip() or "manual"

# Current season (Nike calendar) from AS_OF_DATE — frozen for the season-freshness gate (03).
# Jan-Mar=SPRG(0), Apr-Jun=SUMR(1), Jul-Sep=FALL(2), Oct-Dec=HOLI(3). ordinal = year*4 + idx,
# so consecutive seasons differ by 1 and "S-1" = ordinal-1. CORE/YEAR articles are season-agnostic.
_SEASON_CODES = ["SPRG", "SUMR", "FALL", "HOLI"]
_season_idx = (as_of.month - 1) // 3
season_code = _SEASON_CODES[_season_idx]
season_ordinal = as_of.year * 4 + _season_idx

snapshot = {k: ("" if v is None else str(v)) for k, v in cfg.items()}
snapshot["AS_OF_DATE"] = str(as_of); snapshot["ATP_AS_OF"] = str(atp_as_of); snapshot["RUN_TS"] = run_ts; snapshot["RUN_ID"] = run_id
snapshot["TRIGGERED_BY"] = triggered_by
snapshot["CURRENT_SEASON_CODE"]    = season_code
snapshot["CURRENT_SEASON_YEAR"]    = str(as_of.year)
snapshot["CURRENT_SEASON_ORDINAL"] = str(season_ordinal)
snapshot["CURRENT_SEASON_LABEL"]   = f"{season_code} {as_of.year}"
spark.createDataFrame([(k, v) for k, v in snapshot.items()],
                      "param_name string, param_value string") \
     .write.mode("overwrite").saveAsTable(f"{FQ}.run_config")
display(spark.table(f"{FQ}.run_config").orderBy("param_name"))
print(f"run_config frozen. COUNTRY={cfg['COUNTRY']}  CATEGORY={cfg['CATEGORY']}  AS_OF_DATE={as_of} (demand D-1)  ATP_AS_OF={atp_as_of} (inventory D)  RUN_TS={run_ts}  RUN_ID={run_id}")
print(f"Current season: {season_code} {as_of.year} (ordinal {season_ordinal})")
