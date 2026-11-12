"""What the stock movement job does, as data: the 13 steps, every table they touch and the codes they write.
Loaded into agent_pipeline_steps / agent_pipeline_tables / agent_glossary so the Genie agents can answer
"what does step 08 do", "which step writes held", "what does HOLD_RECV_CUT mean" in plain language."""
H = "https://dbc-74c28e5e-272e.cloud.databricks.com"
O = "?o=8517174019523704"
NB = {"00_config_validation": 2208784873396675, "01_effective_stock": 2208784873396676, "02_ros_cover_classify": 2208784873396677,
      "03_pools_guards": 2208784873396678, "04_match_size": 2208784873396679, "05_size_curve": 2208784873396680,
      "06_score_output": 2208784873396681, "07_history_archive": 513551203851668, "08_receiver_guard": 3380146532593582,
      "10_replen_demand": 2278953126573161, "11_replen_ration": 2278953126573162, "12_replen_output": 2278953126573163,
      "13_replen_history": 2278953126573164}

# step_no, task_key, notebook, lane, order (run sequence), depends_on, purpose, rules, parameters, reads, writes, typical_seconds
STEPS = [
 ("00", "config", "00_config_validation", "Shared prep", 1, "", "Checks the run settings and freezes them for the run.",
  "Settings come from the job (the app's Run parameters) and default to cfg_scalar_params. Invalid values stop the run. "
  "COUNTRY is an ISO code or ALL; CATEGORY is a list or ALL. Every later step reads run_config, never the inputs.",
  "all run parameters", "cfg_scalar_params", "run_config", 31),
 ("01", "effective_stock", "01_effective_stock", "Shared prep", 2, "config",
  "Builds the stock picture: sellable stock per store and size, stock in transit to each store, and DC stock pools.",
  "Effective stock = ATP (sellable, SL01) + in-transit to the store over the last IN_TRANSIT_LOOKBACK_DAYS. Only stores on "
  "the active store list (cfg_store_grade) are in scope. Categories come from cfg_category_converter. DC pools: KSA 2D22, "
  "Bahrain 3D31, UAE hub 1D00 + 1D01.",
  "IN_TRANSIT_LOOKBACK_DAYS, COUNTRY, CATEGORY, ATP_AS_OF",
  "sap_gmg_datalake.bronze.atp_detail, prod_mena.silver.combined_inventory, prod_mena.gold.mena_sports_store_master_view, "
  "prod_mena.gold.mena_sports_product_master_view, cfg_store_grade, cfg_replen_dc, cfg_category_converter",
  "effective_stock, warehouse_stock, store_intransit, dc_pool_stock, atp_snapshot_info, run_article_scope, cfg_country_dc", 93),
 ("02", "classify", "02_ros_cover_classify", "Shared prep", 3, "effective_stock",
  "Works out how fast each store and size sells and how many days of stock it holds, then labels it.",
  "ROS = units sold per day over ROS_WINDOW_DAYS (sales channels 10 and 20). Cover days = effective stock / ROS. "
  "DEAD = no sales in the window; SLOW_MOVING = cover above SLOW_MOVING_THRESHOLD_DAYS; NEW_TO_STORE = first goods receipt "
  "within NEW_TO_STORE_DAYS (left alone); otherwise HEALTHY.",
  "ROS_WINDOW_DAYS, SLOW_MOVING_THRESHOLD_DAYS, NEW_TO_STORE_DAYS",
  "effective_stock, sap_gmg_datalake.silver.fct_sap_sales_detail_v, prod_mena.gold.sports_store_and_warehouse_grn_dates",
  "stock_classified, sales_demand", 36),
 ("03", "pools", "03_pools_guards", "Shared prep", 4, "classify",
  "Decides which stores can give stock and which can take it, and applies the guards.",
  "Donor = DEAD or SLOW_MOVING with effective stock above ROS x PROTECT_COVER_DAYS; donor_can_give is the surplus. "
  "Receiver = HEALTHY with cover below TARGET_COVER_DAYS (IN_STOCK), selling but out of stock (OOS_SELLING), or missing an "
  "A+ display size (APLUS_CURVE); receiver_can_take is the need. Guards: APO (store already has an inbound APO for the style), "
  "season freshness (store may not stock that season, cfg_store_season), move-back (moved out of that store in the last 30 days).",
  "PROTECT_COVER_DAYS, TARGET_COVER_DAYS",
  "stock_classified, effective_stock, cfg_size_curve, cfg_store_season, cfg_kids_size_band, recommendations_history, "
  "sap_gmg_datalake.bronze.atp_detail",
  "donor_pool, receiver_pool, apo_block, apo_recv_block, season_block, moveback_block, moveback_recv_block, "
  "aplus_missing_sizes, art_size_key, art_season", 63),
 ("04", "match", "04_match_size", "Store consolidation", 5, "pools",
  "Pairs donor surplus with receiver need for each size and sets the move quantity.",
  "Same country only. Business-unit pairs must be allowed in cfg_bu_eligibility. Receivers are ranked by ROS (highest first), "
  "then lowest cover; donors by largest surplus. Quantities are allocated in order until surplus or need runs out.",
  "", "donor_pool, receiver_pool, cfg_bu_eligibility, art_size_key", "match_sized", 14),
 ("05", "reshape", "05_size_curve", "Store consolidation", 6, "match",
  "Protects each giving store's full size run.",
  "Size rule per division x category x gender in cfg_size_curve: key sizes + a threshold; full-size = more key sizes in stock "
  "than the threshold. No size rule -> move allowed (NO_SIZE_DEF). Donor already cut -> allowed (DONOR_ALREADY_CUT). Donor stays "
  "full -> allowed (FULL_RETAINED). Move would break the donor's run -> the whole run goes to the best receiver only if it sells "
  "at least 25% faster (RESHAPED_FULL_RUN, GENERIC_ROS_UPLIFT_MIN); otherwise held as Size-curve break.",
  "GENERIC_ROS_UPLIFT_MIN", "match_sized, stock_classified, cfg_size_curve", "match_reshaped_all, held_size_curve", 25),
 ("10", "replen_demand", "10_replen_demand", "DC replenishment", 7, "pools",
  "Works out what each Nike store needs from the DC, per size.",
  "Demand = POS rate of sale. Target per size = the larger of 14 days of demand and 1 unit per display size. "
  "Need = target - ATP - in-transit. The DC that serves each country comes from cfg_replen_dc.",
  "cfg_replen_params", "stock_classified, effective_stock, store_intransit, dc_pool_stock, "
  "sap_gmg_datalake.silver.fct_sap_sales_detail_v, cfg_replen_dc, cfg_replen_params", "replen_demand", 24),
 ("11", "replen_ration", "11_replen_ration", "DC replenishment", 8, "replen_demand",
  "Shares out DC stock when there is not enough for every store.",
  "Phase 1: KSA stores draw from 2D22 and Bahrain stores from 3D31 (exclusive pools). Phase 2: what is still needed in every "
  "country is rationed from the UAE hub (1D00 + 1D01). Priority: grade BEACON/JORDAN, then A+, A, B, C, NVS; then demand rate; "
  "then value at risk. Breadth before depth.",
  "cfg_replen_params", "replen_demand, dc_pool_stock, apo_block, moveback_block", "replen_allocated", 15),
 ("08", "receiver_guard", "08_receiver_guard", "Joint guard", 9, "reshape, replen_ration",
  "Checks every receiving store and style after all proposed store moves and DC lines together.",
  "Full-size = key sizes in stock above a grade-aware threshold (A/A+ base, others base - 1, minimum 1). Full after the moves -> "
  "allow (ALLOW_RECV_BECOMES_FULL, ALLOW_RECV_ALREADY_FULL, ALLOW_NO_SIZE_DEF). Still cut -> if the DC can supply the missing key "
  "sizes (current season or CORE), top up 1 per missing key size (TOPUP_TO_FULL, flagged 'Review - moving to make full size'); "
  "otherwise hold every store move and DC line into that store and style (HOLD_RECV_CUT, 'Receiver stays cut-size').",
  "", "match_reshaped_all, replen_allocated, dc_pool_stock, effective_stock, store_intransit, art_size_key, cfg_replen_params",
  "receiver_guard_decision, fullsize_topup, replen_final, match_reshaped, held_receiver_curve, size_status_post_move, "
  "position_post_move", 95),
 ("06", "score", "06_score_output", "Store consolidation", 10, "reshape, receiver_guard",
  "Scores each store move and writes the store-to-store results.",
  "Expected uplift = qty x receiver net sales per unit x SELL_THROUGH_FACTOR. Net benefit = uplift - qty x MOVEMENT_COST_PER_UNIT "
  "(AED 5, provisional). Every stock line ends in exactly one of moved, held or not moved (run_stats.partition_reconciles).",
  "SELL_THROUGH_FACTOR, MOVEMENT_COST_PER_UNIT",
  "match_reshaped, held_size_curve, held_receiver_curve, stock_classified, prod_mena.gold.mena_sports_product_master_view, "
  "cfg_category_converter", "recommendations, held, held_moves, not_moved, run_stats", 63),
 ("12", "replen_output", "12_replen_output", "DC replenishment", 11, "replen_ration, receiver_guard",
  "Writes the DC-to-store lines and the store needs that were not filled.",
  "Line reasons: REPLEN_DEPLETION (sold below cover), REPLEN_DISPLAY (1 per display size), REPLEN_FULL_SIZE (top-up to full "
  "size, needs review). Not filled: NO_DC_STOCK, DC_SHORTFALL_OR_BLOCKED, HELD_RECEIVER_STAYS_CUT, MOVE_BACK_30D.",
  "", "replen_final, fullsize_topup, replen_demand, dc_pool_stock, cfg_replen_dc, cfg_category_converter",
  "replen_recommendations, replen_not_replenished, replen_stats", 30),
 ("13", "replen_history", "13_replen_history", "DC replenishment", 12, "replen_output",
  "Saves this run's DC results under its run ID.", "Appends to the *_history tables; re-running a run ID replaces its rows.",
  "", "replen_recommendations, replen_not_replenished, replen_stats, run_config",
  "replen_recommendations_history, replen_not_replenished_history, replen_stats_history", 22),
 ("07", "archive", "07_history_archive", "Store consolidation", 13, "score",
  "Saves this run's store-to-store results under its run ID.", "Appends to the *_history tables; re-running a run ID replaces its rows.",
  "", "recommendations, held, not_moved, run_stats, run_config",
  "recommendations_history, held_history, not_moved_history, run_stats_history", 28),
]

# table, layer, grain, description, refresh
TABLES = [
 ("sap_gmg_datalake.bronze.atp_detail", "Source", "store x article x stock type", "SAP available-to-promise stock", "Upstream"),
 ("sap_gmg_datalake.silver.fct_sap_sales_detail_v", "Source", "sales line", "SAP sales lines (retail and e-commerce)", "Upstream"),
 ("prod_mena.silver.combined_inventory", "Source", "store x article", "Inventory incl. stock in transit to stores", "Upstream"),
 ("prod_mena.gold.mena_sports_product_master_view", "Source", "article", "Article master: style, size, season, category", "Upstream"),
 ("prod_mena.gold.mena_sports_store_master_view", "Source", "store", "Store master: country, plant, grade", "Upstream"),
 ("prod_mena.gold.sports_store_and_warehouse_grn_dates", "Source", "store x article", "Goods-receipt dates (new-to-store rule)", "Upstream"),
 ("cfg_scalar_params", "Config", "parameter", "Default run settings", "Edited by the owner"),
 ("cfg_store_grade", "Config", "store", "Active store list with grades: the store universe of every run", "Uploaded from the app"),
 ("cfg_size_curve", "Config", "division x category x gender x size", "Key sizes and full-size threshold", "Uploaded from the app"),
 ("cfg_bu_eligibility", "Config", "business-unit pair", "Which business units may give to which", "Uploaded from the app"),
 ("cfg_kids_size_band", "Config", "size", "Kids size to age band", "Uploaded from the app"),
 ("cfg_category_converter", "Config", "category", "Business category mapping", "Edited by the owner"),
 ("cfg_replen_dc", "Config", "country x pool", "Which DC pool serves which country", "Edited by the owner"),
 ("cfg_replen_params", "Config", "parameter", "Replenishment settings", "Edited by the owner"),
 ("cfg_store_season", "Config", "store", "Seasons each store may stock", "Edited by the owner"),
 ("run_config", "Step output", "parameter", "Frozen settings of the latest run (run ID, scope, dates, parameters)", "Replaced every run"),
 ("effective_stock", "Step output", "store x size", "ATP + in-transit per store and size", "Replaced every run"),
 ("warehouse_stock", "Step output", "country x article", "Warehouse ATP", "Replaced every run"),
 ("store_intransit", "Step output", "store x article", "Units in transit to each store", "Replaced every run"),
 ("dc_pool_stock", "Step output", "DC pool x article", "Stock available in each DC pool", "Replaced every run"),
 ("atp_snapshot_info", "Step output", "one row", "Date of the stock snapshot the run used", "Replaced every run"),
 ("stock_classified", "Step output", "store x size", "Rate of sale, cover days and DEAD / SLOW_MOVING / HEALTHY / NEW_TO_STORE", "Replaced every run"),
 ("sales_demand", "Step output", "store x size", "Units sold and rate of sale", "Replaced every run"),
 ("donor_pool", "Step output", "store x size", "Stores that can give, with donor_can_give", "Replaced every run"),
 ("receiver_pool", "Step output", "store x size", "Stores that can take, with receiver_can_take and demand_type", "Replaced every run"),
 ("apo_block", "Step output", "store x style", "Stores with an inbound APO for the style (cannot receive)", "Replaced every run"),
 ("season_block", "Step output", "store x size", "Receivers that may not stock the season", "Replaced every run"),
 ("moveback_block", "Step output", "store x size", "Moved out in the last 30 days (cannot receive back)", "Replaced every run"),
 ("aplus_missing_sizes", "Step output", "store x size", "A+ stores missing a display size", "Replaced every run"),
 ("match_sized", "Step output", "donor x receiver x size", "Matched moves before the size-run check", "Replaced every run"),
 ("match_reshaped_all", "Step output", "donor x receiver x size", "Moves after the donor size-run check, with reshape_action", "Replaced every run"),
 ("held_size_curve", "Step output", "donor x style", "Moves held because they would break the donor's run", "Replaced every run"),
 ("receiver_guard_decision", "Step output", "receiver x style", "Joint guard decision per receiving store and style", "Replaced every run"),
 ("fullsize_topup", "Step output", "store x size", "DC units sent only to make a store full-size", "Replaced every run"),
 ("match_reshaped", "Step output", "donor x receiver x size", "Final store moves after the joint guard", "Replaced every run"),
 ("held_receiver_curve", "Step output", "donor x receiver x size", "Store moves held because the receiver stays cut-size", "Replaced every run"),
 ("size_status_post_move", "Step output", "store x style", "Full / cut size after all moves", "Replaced every run"),
 ("position_post_move", "Step output", "store x size", "Stock before and after all moves", "Replaced every run"),
 ("replen_demand", "Step output", "store x size", "DC demand, target and need per store and size", "Replaced every run"),
 ("replen_allocated", "Step output", "store x size", "Units rationed from the primary pool and the UAE hub", "Replaced every run"),
 ("replen_final", "Step output", "store x size", "DC lines after the joint guard (final_qty, guard_decision)", "Replaced every run"),
 ("recommendations", "Result", "donor x receiver x size", "Recommended store-to-store moves", "Replaced every run"),
 ("held", "Result", "donor x size", "Stock that would move but a rule held it", "Replaced every run"),
 ("held_moves", "Result", "donor x receiver x size", "Held moves with the would-be receiver", "Replaced every run"),
 ("not_moved", "Result", "store x size", "Stock left where it is, with the reason", "Replaced every run"),
 ("run_stats", "Result", "one row", "Headline numbers of the latest run", "Replaced every run"),
 ("replen_recommendations", "Result", "store x size", "DC-to-store lines", "Replaced every run"),
 ("replen_not_replenished", "Result", "store x size", "Store needs the DC did not fill, with the reason", "Replaced every run"),
 ("replen_stats", "Result", "one row", "Headline DC numbers of the latest run", "Replaced every run"),
 ("recommendations_history", "History", "run x move", "Every run's store moves", "Appended every run"),
 ("held_history", "History", "run x line", "Every run's held lines", "Appended every run"),
 ("not_moved_history", "History", "run x line", "Every run's not-moved lines (when switched on)", "Appended every run"),
 ("run_stats_history", "History", "run", "One row per run: who, when, scope, headline numbers", "Appended every run"),
 ("replen_recommendations_history", "History", "run x line", "Every run's DC lines", "Appended every run"),
 ("replen_not_replenished_history", "History", "run x line", "Every run's unfilled needs", "Appended every run"),
 ("replen_stats_history", "History", "run", "One row per run of DC headline numbers", "Appended every run"),
]

# field, code, meaning, step
GLOSSARY = [
 ("classification", "DEAD", "No sales in the rate-of-sale window", "02"),
 ("classification", "SLOW_MOVING", "Cover above the slow-moving threshold (30 days by default)", "02"),
 ("classification", "HEALTHY", "Selling within cover", "02"),
 ("classification", "NEW_TO_STORE", "First received within the new-to-store window (14 days); left alone", "02"),
 ("demand_type", "IN_STOCK", "Receiver has stock but cover is below target", "03"),
 ("demand_type", "OOS_SELLING", "Receiver sold the size but is out of stock", "03"),
 ("demand_type", "APLUS_CURVE", "A+ store missing a display size", "03"),
 ("reshape_action", "FULL_RETAINED", "Donor keeps a full size run after giving", "05"),
 ("reshape_action", "DONOR_ALREADY_CUT", "Donor's run was already broken; its remaining run is consolidated to one receiver", "05"),
 ("reshape_action", "RESHAPED_FULL_RUN", "Donor's run released to a receiver selling at least 25% faster", "05"),
 ("reshape_action", "NO_SIZE_DEF", "No size rule for the item; moved on demand alone", "05"),
 ("recv_action", "ALLOW_RECV_ALREADY_FULL", "Receiver was already full-size", "08"),
 ("recv_action", "ALLOW_RECV_BECOMES_FULL", "Receiver becomes full-size with the proposed moves", "08"),
 ("recv_action", "ALLOW_NO_SIZE_DEF", "No size rule; allowed", "08"),
 ("recv_action", "TOPUP_TO_FULL", "DC sends 1 unit per missing key size so the receiver becomes full-size (needs review)", "08"),
 ("recv_action", "HOLD_RECV_CUT", "Receiver would still be cut-size; every move into it is held", "08"),
 ("held_reason", "Size-curve break", "Move would break the donor's full size run", "05"),
 ("held_reason", "Season-freshness", "Receiver may not stock this season", "03"),
 ("held_reason", "APO", "Receiver already has an inbound APO for the style", "03"),
 ("held_reason", "Receiver stays cut-size", "Receiver would not reach a full size run even after all moves", "08"),
 ("held_reason", "Move-back <30d", "The article left that receiver in the last 30 days", "03"),
 ("not_moved_reason", "Not moved - no receiving store needs this size", "Surplus exists but no store needs that size", "06"),
 ("not_moved_reason", "Not moved - retained (selling within cover)", "Healthy stock, kept", "06"),
 ("not_moved_reason", "Not moved - new to store (too recent to consider)", "Received too recently to judge", "06"),
 ("replen reason", "REPLEN_DEPLETION", "Store sold below its 14-day cover", "12"),
 ("replen reason", "REPLEN_DISPLAY", "1 unit per display size", "12"),
 ("replen reason", "REPLEN_FULL_SIZE", "Top-up to make the store full-size (needs review)", "12"),
 ("not_replenished_reason", "NO_DC_STOCK", "The DC has none of this size", "12"),
 ("not_replenished_reason", "DC_SHORTFALL_OR_BLOCKED", "DC stock ran out before this store or a guard blocked it", "12"),
 ("not_replenished_reason", "HELD_RECEIVER_STAYS_CUT", "Held by the joint guard: store would stay cut-size", "12"),
 ("not_replenished_reason", "MOVE_BACK_30D", "Moved out of the store in the last 30 days", "12"),
 ("guard_decision", "Review - moving to make full size", "DC top-up line that a person should approve", "08"),
 ("store grade", "BEACON / JORDAN", "Flagship stores; first in every priority", "11"),
 ("store grade", "A+ / A / B / C", "Store grades in priority order", "11"),
 ("store grade", "NVS", "Nike Value Store (outlet)", "11"),
 ("DC pool", "2D22", "KSA DC (KSA stores only)", "11"),
 ("DC pool", "3D31", "Bahrain DC (Bahrain stores only)", "11"),
 ("DC pool", "1D00 + 1D01 (HUB)", "UAE hub; serves UAE, Kuwait, Oman, Qatar and what KSA / Bahrain still need", "11"),
]


def notebook_url(nb):
    return f"{H}/editor/notebooks/{NB[nb]}{O}"


def table_url(t):
    full = t if t.count(".") == 2 else f"sports_analytics_test.nike_consolidation.{t}"
    return f"{H}/explore/data/{full.replace('.', '/')}{O}"
