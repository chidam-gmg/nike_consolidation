# DC → Store Replenishment — P1 Build Spec

*Prepared 2026-08-19. Companion to `consolidation_poc_spec.md` (store→store engine) and
`Replenishment_Approach.md` (theory). P1 = the fastest, lowest-risk value slice.*

## 0. Positioning — one engine, two movement types

Replenishment is **not a separate tool**. DC→store and store→store are both **stock movements**; the only
difference is the **supply node**. We extend the consolidation engine into a **unified fulfilment engine** where
each `(store, article, size)` **need** is sourced from a **ranked supply stack: DC pool (replenishment) → donor
stores (consolidation)**, DC preferred (`Replenishment_Approach.md` §8, Option B). This dissolves the current
"warehouse-first" guard into the sourcing rank — no size is ever both replenished and consolidated, and DC
scarcity spills automatically to store donors.

**The one genuinely new idea vs the existing approach doc:** the demand signal is **de-censored with PDA
loss-of-sale**. Today RoS comes from POS sales only, which are blind to demand on a size that was out of stock
(it shows 0 sales). The runner PDA log is the **direct observed demand** for exactly those out-of-stock sizes.
This is the piece that turns "replenish what sold" into "replenish what was *demanded*."

**P1 target (from the Jan–Aug 2026 waterfall):** the **AED 45.8M "Replenishment" root cause** — a store carried
a size, ran to zero, and kept losing it (recurring). P1 acts where the **DC clearly has the stock**; DC-scarcity
rationing is deferred to P2.

## 1. Scope

**In:** DC→store replenishment recommendations, Nike BU, intra-country, all ~300 stores (all grades — RoS is
from POS, not the PDA completion field, so B/C doors are fully in). Recommend only; do not execute STOs.

**Out (P1):** fair-share rationing when DC < total need (→ P2); review-cadence/min-line batching beyond a simple
trigger (→ P2); lifecycle EOL cutoff (→ P3); launch/initial allocation (→ Allocation roadmap, needs planogram).

## 2. Reuse — the ~70% already built (bind, don't rebuild)

| Component | Existing source |
|---|---|
| RoS (store×child×parent×size, retail ch.10, config window) | `build/sql/ros_sales.sql` → `fct_sap_sales_detail_v` |
| Effective stock (ATP + in-transit netting) | consolidation Step 1a |
| Cover / classification | Steps 2–3 |
| Size-curve / display definition | `size_definition.xlsm` → `cfg_size_curve` |
| APO guard, BU/country filters, capacity cap | Steps 5/5a, C4/C6/C7/C8 |
| Deterministic waterfall allocator + tie-breaks | Step 6, §2.1 |
| Net-benefit scoring, output, Streamlit review app | Steps 9–10, `build/app` |
| Config snapshot / determinism harness | `cfg_scalar_params`, §2.1 |

## 3. New for P1 (the missing pieces)

### 3.1 DC supply node  *(new module `01b_dc_pool`)*
- DC sellable pool = `atp_detail` where `storage_location='SL01'` and site is a DC
  (`dim_site_master_v.site_type='DC'` / master `PLANTCAT='B'`), keyed `country × article × size`, with
  `unit_cost_aed`. Country→DC mapping via existing `cfg_country_dc`.
- Model the DC as a **donor with priority 0** (ranks above all store donors) in the supply stack.
- Net DC availability of already-committed outbound (open STO / in-transit out) so two runs can't ship twice.

### 3.2 PDA de-censoring  *(extend demand module `03_receiver_pool`)*  ← the differentiator
- Source: `runner_app_catalog.gold.fct_runner_app_item_detail_v` (`store_code`, `variant_article`=child SKU,
  `los_flag`, `txn_date`, `order_qty`). Join `variant_article = article_code`.
- Per `store × article × size` over the RoS window compute:
  - `pda_lost_qty` = Σ order_qty where `los_flag=1`
  - `pda_miss_days` = distinct loss dates  → **recurrence filter: only `pda_miss_days ≥ 2` counts** (kills the
    ~AED 45M one-off tail / 1-unit churn).
- **De-censored demand rate:**
  ```
  demand_rate = RoS_effective
              = ( units_sold_in_window + pda_lost_qty[recurring] )
                / max(days_size_was_available, 1)        -- exclude ATP=0 days from the denominator
  ```
  i.e. add the observed lost demand to the numerator and stop dividing by days the size couldn't sell.
- This replaces the doc's peer-imputation censoring correction (§3.2) with **direct observed** demand.

### 3.3 Cover-based order-up-to sizing  *(extend `04`)*
```
S_units = demand_rate × protection_window
        + presentation_floor(display size @ grade)        -- from cfg_size_curve; dominates for slow sizes
S_need  = max(0, S_units − effective_stock − pipeline)     -- pipeline = ATP + in-transit + open STO
replen_qty = FLOOR(min(S_need, dc_available))              -- P1: only where DC can cover; whole units
raise only if replen_qty ≥ MIN_LINE_QTY                    -- batch trigger, kills 1-unit churn
```
- `protection_window = REVIEW_PERIOD + lead_time_quantile` (empirical, per §3.4).
- Poisson safety collapses to a small integer for lumpy demand — for P1 fold it into the presentation_floor
  rather than a separate term (keep P1 simple; formal Poisson term is P2).

### 3.4 Empirical lead-time table  *(new `cfg_leadtime`)* — LANE-SPECIFIC, not global
- Per-**lane** (`source_site → destination_country/grade`) quantile from `sto_table/obd_table/ibd_table`
  (create→dispatch + dispatch→GRN). Carry **P75 and P90** and the **variability** (protection window AND
  safety scale to the lane tail).
- **Intra-country DC→store:** short (~4–8d) — cover math valid → **P1 scope**.
- **Cross-border DC legs (e.g. UAE→KSA):** long & variable (customs). DC topology confirms depth is
  UAE-centric (UAE 5.43M DC units vs KSA 0.49M @ 2026-08-17), so GCC depth is fed cross-border from UAE.
  These legs are **OUT of P1** — reactive replenishment can't win when lead time > remaining product life;
  cross-border DC depth is an **allocation / forward-buy** decision (see §3.6).

### 3.5 Forward demand layer *(new — do NOT carry trailing RoS flat)*  ← handles events/seasonality
Order-up-to target is driven by a **forward projection over the protection window**, not the trailing mean:
```
demand_over_window = de-censored RoS × seasonality_index × event_uplift
```
- `cfg_seasonality` — per (category/cluster × week-of-year) index (winter jackets: trailing summer RoS≈0 but
  season index ramps in Nov → pre-season fill; this is allocation cold-start, shared).
- `cfg_event_calendar` — promos, DSF/White Friday, Ramadan/Eid, back-to-school, GCC national days, launches,
  sporting events; per-(event × category × cluster) uplift from prior-year lift.
- **Pre-build, lead-time-shifted:** trigger at `event_start − lead_time − review_period` so cover lands BEFORE
  the event. On long lanes this means ordering weeks ahead — another reason long-lead = forward, not reactive.
- P1: stub `cfg_seasonality` + `cfg_event_calendar` (business-populated); RoS is the floor, these shape it forward.

### 3.6 Lead-time-aware sourcing (refines "DC preferred" → "fastest viable source")
The supply stack ranks sources by **effective lead time + cost**, NOT a fixed DC-first. For a long-lead market
(KSA) an OOS store is served fastest by **local store consolidation (~1d)** before a cross-border UAE DC pull
(weeks). So consolidation is the **in-season tool for long-lead markets**; cross-border DC is last resort /
upstream allocation. Rank: `local store consolidation → in-country DC → cross-border DC (P1: excluded)`.

## 4. Unified supply stack — the matching change  *(extend `04_waterfall`)*
For each `(store, article, size)` need (ranked by AED-at-risk = demand_rate × net_sales_per_unit, then grade,
then store_id), fill from the **fastest viable source** (lead-time-aware, per §3.6), not a fixed DC-first:
1. **Local (in-country) store consolidation** where a fast donor exists (~1d) — existing consolidation logic.
2. **In-country DC pool** (replenishment), up to `dc_available` — P1's core add.
3. **Cross-border DC** — EXCLUDED in P1 (long-lead → allocation/forward-buy, §3.6).
(For short-lead markets where the in-country DC is well-stocked, DC-first and fastest-source coincide.)
- Tag each output line `source_type ∈ {DC_REPLEN, STORE_CONSOL}`. **Invariant:** `Σ allocated ≤ dc_available`
  per (country, article, size) — no over-allocation.
- P1 assumption: **no DC scarcity contention** — where DC need across stores exceeds DC stock, P1 fills
  deterministically by rank and simply **logs the shortfall** (rationing = P2). Never silently drop.

## 5. Data bindings (all verified)
| Logical | Table |
|---|---|
| Sales / RoS | `sap_gmg_datalake.silver.fct_sap_sales_detail_v` (distr_channel='10') |
| Stock / ATP / DC pool | `sap_gmg_datalake.bronze.atp_detail` (SL01, `unit_cost_aed`, `c_file_date`) |
| PDA loss (de-censoring) | `runner_app_catalog.gold.fct_runner_app_item_detail_v` |
| Product master | `prod_mena.gold.mena_sports_product_master_view` (MATERIAL/RT_CONFMAT/RT_SIZE) |
| Store master (grade, PLANTCAT, DC) | `prod_mena.gold.mena_sports_store_master_view` / `dim_site_master_v` |
| Lead time | `sto_table` / `obd_table` / `ibd_table` |
| Config | schema `sports_analytics_test.nike_consolidation` |

## 6. New config (all external, fail-fast validated)
| Param | Default | Notes |
|---|---|---|
| `REVIEW_PERIOD_DAYS` | 3 | fixed replen cadence |
| `LEAD_TIME_QUANTILE_DAYS` | 8 | P75 cushion (from cfg_leadtime) |
| `MIN_LINE_QTY` | 2 | anti-churn batch trigger |
| `PDA_RECURRENCE_MIN_DAYS` | 2 | recurring-demand filter |
| `PDA_WINDOW_DAYS` | = RoS window | de-censoring window |
| `LEAD_TIME_QUANTILE` | P75 | which lane quantile to use (P75/P90) |
| `MAX_LANE_LEADTIME_DAYS` | 12 | P1 excludes lanes slower than this (cross-border cutoff) |
| existing scalars | — | reuse `cfg_scalar_params` |

New config tables (stub in P1, business-populated): `cfg_leadtime` (per source→dest lane, P75/P90),
`cfg_seasonality` (category/cluster × week index), `cfg_event_calendar` (event × category/cluster × uplift).

## 7. Determinism & guardrails
Inherited unchanged: config frozen at run start, AS_OF frozen, total-ordered waterfall with explicit
tie-breaks, `FLOOR(x+1e-4)`, APO/BU/country/capacity guards, `net_benefit > 0`. DC-as-donor and PDA demand add
inputs only — they cannot create a move that violates a guard.

## 8. Output & app
Same schema as consolidation output + `source_type=DC_REPLEN`, `dc_site`, `demand_rate`, `pda_lost_qty`,
`aed_at_risk`, `dc_shortfall_flag`. Surfaces in the existing Streamlit app as a movement type (or a
"Replenishment" tab); reviewers approve/adjust as today. Recommend-only.

## 9. Validation
1. **Backtest** vs actual STO + sales history: would the model have shipped what actually sold? Metrics:
   fill-rate lift, OOS-days reduction, size-curve completeness at A/A+, single-unit-move reduction, and the
   PDA-specific one — **would the trigger have fired before the recorded PDA loss** (lead-time coverage; earlier
   finding: 68% of misses had ≥3 days of ATP=0 warning).
2. **Pilot:** 3–5 Pareto stores, 4 weeks, recorded-LOS drop vs matched controls.

## 10. Open items (take to business)
- `[R-1]` Validate PDA-de-censored demand against a POS sample (runner misses self-serve shelf sales; may
  slightly overstate). Do before quoting hard AED.
- `[R-2]` `MIN_LINE_QTY` and `REVIEW_PERIOD_DAYS` values — business.
- `[R-3]` Presentation-floor for out-of-display-ladder tail sizes: replenish to floor, or leave to Allocation?
  (ties to store-cluster display thresholds — Engine 2).
- `[R-4]` Season/EOL cutoff signal (also open in the approach doc) — deferred to P3 but flag.
- `[R-5]` `[OPEN-2]` ecom/marketplace in RoS — inherit consolidation's decision.

## 11. Build order
1. `01b_dc_pool` (DC as donor node) + `cfg_leadtime`.
2. Extend `03` with PDA de-censoring + recurrence filter.
3. Extend `04` to unified supply stack (DC→stores) + cover/order-up-to sizing + `MIN_LINE_QTY`.
4. Output `source_type` + app tab.
5. Backtest harness, then pilot.
