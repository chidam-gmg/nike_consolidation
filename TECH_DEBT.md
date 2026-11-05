# Nike Consolidation — Tech Debt

Running backlog of known shortcuts / deferred work. Newest first.

---

## TD-10 · New-article (no history) forecast and opening pack (2026-11-04)

**Status:** v1 BUILT 2026-11-04 for Spring 2027 (`pipeline/newlaunch/`, notebook `newlaunch/21_new_launch_forecast`, one-off submit; tables `nl_forecast`, `nl_forecast_country`, `nl_forecast_weekly`, `nl_analogues`, `nl_backtest`; app tab New launches). 5,851 SP27 styles, 398K units in first 13 weeks. Back-test SP26: ±10% by country, ±12% division × gender, ±16% division × gender × country, style-level 83% (44% within 2×), bias +8%, P80 covers 80%. Open: buyer launch list + dates (dates assumed from last spring), ranging (which stores), images for SP27 not in Akeneo yet (similarity by image not used), opening pack per store/size, schedule the job.
- **Why:** the forecast covers only articles with sales history (CORE/YEAR). About 1,500 new Nike styles launch a
  month (seasonal); 45% are new colourways of a model that already sold (62% of footwear launches).
- **Approach:** borrow from the closest thing with history — A) sibling colourways of the same model; B) the 10–20
  most similar past launches (cluster, sport, silhouette `CM_CDT4`, retail price band, season, life cycle `/BIC/ZZPLC`,
  colour family, image similarity); C) cluster-level demand × typical new-style share. Blend with the buyer's plan
  (weight from history); weekly launch curve + P80; hand over to the regular forecast after ~6 weeks of in-stock sales.
  Store + size split reuses the cluster logic → opening pack per store and size; Launch planner cards in the brief.
- **Data gaps:** colour fields (`RT_COLOR`, `/BIC/ZCHCOLOR`) are empty for Nike articles — derive colour family from
  the colourway code (e.g. `-060`) until a colour feed exists; images are not in any table (user is sourcing links);
  buyer launch list source/owner open (SAP open POs or buyer file); ranging owner (who picks launch stores) open.
- **Image links (samples 2026-11-04):** Nike → `omniversemedia` S3, keyed by the size-level barcode (`EANUPC`, 99.9% filled; `685068170041` = NK805160-09104) — the sample host `s3.eu-central1` is a typo, `s3.eu-central-1` works; UA → `athlocity.asset.akeneo.cloud/.../U_V5_<model>_<colour>_1740.jpg`, derivable from the article code. Both public, 2000px studio shots on white. Open: full link extract, Databricks egress to both hosts (or copy into a UC volume), usage rights.
- **Image + attribute source (decided 2026-11-04): `akeneo_gmg_datalake.gold.product_images` only** — view owned by c_udit@gmg.com; join on `sap_generic_article` / `sap_variant_article`; image columns `fluent_image`, `gmg_fluent_image`, `gmg_fluent_image_new`, `gmg_fluent_image_new_1`; also `color`, `variant_color`, `gmg_franchise`, `collections`, `campaign`, `current_srp`, `online_from`, per-country available dates. Access granted 2026-11-04; copied daily by the forecast run into `dim_product_image` (117K Nike/Jordan styles, 102K with an image; 766 of 782 plan styles; CORE 97% image / 99.6% colour / 75% franchise; launches since May 67% / 74% / ~0%). `gmg_fluent_image` loaded 15/15 in a sample, the others are fallbacks; ~1 in 12 links 404. When the forecast job moves to a service principal it needs SELECT on product_images too. Copy links + attributes into the project schema daily so app viewers need no Akeneo access.
- **Image test (2026-11-05, men's tees):** image features work technically — Databricks vision endpoint (`databricks-claude-sonnet-5-5`) turns an image into structured tags (fit, print type, graphic size, style); CLIP ViT-B-32 fingerprints for 4,700 past men's tee launches. Back-test on 358 Spring 2026 men's tees: attributes 86% error / ranking 0.44; image only 100% / 0.29; attributes shortlist re-ranked by image 95% / 0.45 — images did NOT improve accuracy. Use them for explainability ('looks like') and test vision-model tags as attributes next; product-name family match is the bigger expected win. Deck images: composite transparent PNGs on white before embedding (black background skews matches).
- **Image ATTRIBUTES with learned weights (2026-11-05, men's tees):** vision-model tags (fit, pattern, graphic, logo size, style, base colour) added to the look-alike score, weights learned on Mar–Oct 2025 tees and checked on 301 Spring 2026 tees: error 86% → 79%, within 2× 38% → 45%, ranking 0.42 → 0.48, bias +10%. Top weights: sport, silhouette, logo size (21% each), price 12%, name family 8%. Code in `pipeline/newlaunch/image_attributes/`. To productionise: tag all categories (not just tees), learn weights per division, rerun the season.
- **First step:** back-test on the last 12 months of launches — first 8 weeks from pre-launch information only:
  cluster average vs analogues vs + attributes vs + images (shows the CEO what images add).

## TD-9 · Allocation sign-off: connect directory, notifications, SAP (2026-10-30)

**Status:** Flow built in the dev app (Forecast & allocation → Allocation plan + Line manager review); three stubs
to connect.
- **Line manager lookup** — `databricks_io.line_manager_of()` reads `cfg_line_manager` (empty today). Replace with
  Microsoft Entra ID (Graph `GET /users/{email}/manager`) or the HR system; keep the signature. Until then, Modify /
  Reject lines have no manager; the dev app lets anyone act as manager (toggle) to test.
- **Notification** — `databricks_io.notify_line_manager()` is a no-op; connect Teams / Outlook.
- **SAP push** — reads view `al_final` (Approved → recommended qty; Change approved → planner qty, 0 for a
  reject; Change declined → recommended qty). Lines still awaiting a manager are not in the view.
- Tables: `al_decisions` (one row per run × store|article), `cfg_line_manager`.

## TD-8 · Article-level open-STO feed for receiver checks (2026-10-07)

**Status:** Requested from data team — engine uses in-transit only until it lands.

**Issue:** receiver checks now count `ATP + in-transit` (business point 5). "In-process" moves that are
**released but not yet shipped** (open STOs / APO / delivery not goods-issued) are invisible: `combined_inventory`
blank-stor_loc rows only carry shipped stock, `atp_detail.apo_*` is a flag with no qty/date, and `omni_sto_report`
is not article-level. So a store with an open STO can still be recommended the same size.

**Request (data team):** daily table, grain = receiving site × article (variant) × STO, with: STO number + line,
supplying site, receiving site, article, open qty (ordered − goods-issued − received), created date, planned
delivery date, status (open / partially shipped). Refreshed before 09:30 GST (pipeline runs 10:30).

**Wiring when available:** add open-STO qty to `store_intransit` (nb 01) so every check — receiver pool, nb05
display gate, nb10 replen need, nb08 joint guard — picks it up with no further change.

---

## TD-7 · Store-level allowed season list (2026-10-07)

**Status:** PARKED by business (2026-10-07) — do not build.

**Ask:** a per-store list of seasons a store may receive (instead of / on top of `cfg_store_season.allow_seasons_back`,
which is a single "N seasons back" rule). Today the season gate is store × seasons-back only (nb 03 receiver pool).
When picked up: new config `cfg_store_allowed_season (store_id, season_label)` + UI upload, applied in nb 03
receiver pool, nb 10/11 replen eligibility and the nb 08 full-size top-up season check.

---

## TD-6 · Replenishment lead times — cross-border lanes & the whole lead-time model (2026-09-18)

**Status:** Deferred by business (parked as tech debt). The DC→store replenishment model now sources
cross-border (UAE hub 1D00/1D01 serves UAE/KWT/OMN/QTR as primary and KSA/BAH as fallback top-up), but the
**lead time used for the cover window was NOT upgraded to match** — flagged and parked here.

**Current state (the shortcut):** the order-up-to target in `10_replen_demand` is
`S = demand_rate_day × protection_window`, where `protection_window = REVIEW_PERIOD_DAYS (flat 3) + LT`, and
`LT` = the **destination country's** single percentile from `cfg_replen_leadtime` — **P75 for all, hardcoded
override to P90 for BH** (`LTCOL`/`LT_Q`, with the BH special-case inline in the `REVIEW_LT` join). One number
per destination country, regardless of which DC actually ships.

**The gaps:**
1. **Cross-border lanes not modelled (the specific ask).** A KSA/KWT/OMN/QTR/BAH store fed from the **UAE hub**
   crosses a border (customs + longer transit) but is costed with its **own destination-country lead time**, so
   hub-fed lines' cover windows are **understated** → under-buys the protection for exactly the fallback lines.
   Need a lead time per **(source pool → destination country)** lane — at least: hub→KSA, hub→KWT, hub→OMN,
   hub→QTR, hub→BAH (distinct from the local-DC lanes 2D22→KSA, 3D31→BAH, and the domestic 1D00→UAE).
2. **The lead-time model overall is coarse.** One percentile per country; the **P75-vs-P90 choice is a single
   global switch** with a hardcoded BH exception baked into SQL (should be config, per lane); the **3-day review
   period is flat** (not per country/format); provenance of the `cfg_replen_leadtime` P50/P75/P90 figures is
   unclear (which historical STO lanes they were derived from). No SKU/mode variation.

**Suggested direction (when picked up):** replace `cfg_replen_leadtime` (country → percentile) with a
**lane table** keyed `(source_pool, dest_country)` carrying the review period + chosen percentile per lane;
join it in `10_replen_demand` on the pool that actually sources each line (needs the sourcing decision, which
today is resolved later in `11_replen_ration` — so either pre-resolve the dominant source, or compute the
protection window per candidate lane). Removes the inline BH/P90 hack. Cross-border lane lead times to be
supplied by business/logistics.

**Related:** the AED value of hub-fed cross-border recommendations is large (~AED 28.6M at the 2026-09-17
refresh); realistic lead times will reshape those quantities.

---

## TD-4 · Cross-BU (multi-brand) consolidation flows (planned, ~few months out) (2026-08-06)

**Status:** Deferred by business. Scope stays brand-to-brand within a brand's OWN stores for now
(Nike→Nike unchanged; Columbia/Vans/TNF/Converse each within their own mono stores). Architecture must be
built **mindful of this** so enabling it later is a config change, not a rewrite.

**Business intent (later):** a brand's stock should consolidate across ALL stores that carry it, not just its
own mono doors — e.g. **Columbia mono ↔ SSS, Nike ↔ SSS**, and an **SSS store sends its Nike stock to Nike
stores / its Columbia stock to Columbia stores**. Confirmed in data: multi-brand stores hold several brands at
once (SSS: 103 Nike / 65 Columbia / 56 Converse sites; FOL similar), so **store BU does NOT identify the
article brand**. Converse in particular has ≈no mono stores → it *needs* this flow to be useful.

**Why the current model can't express it:** the engine scopes by store-BU and the `cfg_bu_eligibility` matrix
is store-BU → store-BU. The real axis is the **article brand (`brand_id`)**; a multi-brand store needs
**different eligible partners per brand**, which a store-BU pair table cannot represent.

**Forward-compatibility already designed in (2026-08-06 multi-brand scaling):**
- Each run scopes by **(article `brand_id`) × (active store list)** — the `brand_id` article filter is present
  from day one (the exact hook multi-brand stores need).
- The store list carries a **`role` column** (mono / SSS / FOL); today every row = `MONO`.
- Output is **schema-per-brand**; a multi-brand store's Nike vs Columbia stock is separated by `brand_id`.

**To turn it ON later (the simple change):** (a) add the SSS/FOL stores that carry a brand into that brand's
store list, tagged by `role`; (b) enable a **store-role directional policy** (repurpose `cfg_bu_eligibility`
to role-based — e.g. outlets donor-only, no push into a flagship SSS); (c) **re-scope Nike the same way** to
capture Nike stock currently missed in SSS/FOL (103 SSS + 34 FOL sites). No change to classification, matching,
size-curve, guardrails or the 3-way partition.

**Decisions needed when picked up:** store-role directional rules (can outlets receive? no-go directions?);
whether Nike is re-scoped then; re-validate each brand's expanded universe.

---

## TD-3 · Display-consolidation overshoots target cover (needs business confirmation) (2026-07-31)

**Status:** Awaiting business decision — do NOT change until confirmed.

**Issue:** the display-consolidation branches (`DONOR_ALREADY_CUT` / `RESHAPED_FULL_RUN` in `05_size_curve`)
send the donor's **full giveable quantity per size** (`recommended_qty = FLOOR(donor_can_give)`) to the one
best receiver — they do **not** cap to the receiver's target-cover need. So a receiver can be pushed **well
past the 14-day cover rule** that governs every other (normal / waterfall) move.

**Live evidence — `NKII9807-20012` (Air Force 1 '07 Low LV8 Summer, 9.5), Jimi Mall (1286) → MOE Dubai (1009):**
MOE had 1 unit (7 days' cover), ROS 0.143 → target need = ~1 unit to reach 14 days. Engine sent Jimi's full
**3** dead units → MOE = 4 units = **28 days cover**. Sibling sizes 8.5→**42 d**, 10.5→**28 d**. The extra depth
adds **zero display value** — a display ladder needs **breadth** (1 of each size), not **depth** (3 of one size).

**The tension:** the branch was intentionally built (P1, 2026-07-24) to ignore the cover cap and empty a cut/
reshaped donor into one store so it can display the full ladder. The question for business: **is completing the
flagship/receiver display worth breaking the 14-day cover rule, or should the move be capped?**

**Proposed fix (if business agrees to cap):** move `LEAST(donor_can_give, GREATEST(receiver_can_take, 1))` per
size — completes the ladder (breadth, ≥1/size) **without** breaking target cover; leftover donor depth stays
available for genuinely stocked-out stores. Applies to both `DONOR_ALREADY_CUT` and `RESHAPED_FULL_RUN`.

**Related — the movement-note LABEL overstates what happens.** The tag reads *"full remaining run consolidated
to one receiver to display,"* but the guardrail (skip a size the receiver already holds DEAD or is over target
cover on) frequently **prunes the move to only the receiver's genuine gaps** — so it is *not* the full run.
**Live evidence — `NKHM6804-101` (Vomero 18), 2230 (Mall of Arabia) → 2056 (Nakheel Mall Riyadh):** donor had
**6 giveable dead sizes** (7.5, 8, 8.5, 9.5, 10.5, 11) but only **3 moved** (8, 8.5, 9.5); 7.5/10.5/11 were
skipped because 2056 already holds them as **dead** stock (moving dead→dead = no benefit). 2056 already carries
almost the whole ladder — mostly dead — so "consolidate the run to display" is largely moot here. **Fix options:**
(a) reword the tag to reflect the pruning (e.g. *"cut donor's giveable sizes moved to the best receiver's gaps"*),
and/or (b) revisit whether dead→dead skips + the TD-3 cap change which receiver "best" is.

---

## TD-2 · Consolidation cover-guardrail knife-edge at exactly TARGET (needs business confirmation) (2026-07-31)

**Status:** Awaiting business decision — do NOT change until confirmed.

**Issue:** the consolidation guardrail that skips piling onto a size the receiver is already covered on uses
`rc.cover_days <= TARGET` (05). At exactly TARGET (14 d) the decision **flips on floating-point rounding of
ROS**, so near-identical sizes behave inconsistently.

**Live evidence — same parent `NKII9807-200` at MOE (1009):** sizes MOE holds **1 unit** compute cover
**13.99999** (just under 14 → move, then overshoot per TD-3); sizes MOE holds **2 units** compute **14.00001**
(just over 14 → skipped). Same "≈14 days" to the eye, opposite outcome (e.g. 8.5/10.5 moved, 9/10 held).

**Proposed fix (if confirmed):** use a consistent boundary with an epsilon, e.g. `cover_days <= TARGET + 1e-4`
(or decide the intended strict/inclusive rule), so sizes sitting essentially *at* target are treated the same.
Small, deterministic; pairs naturally with TD-3.

---

## TD-1 · Config-file upload persistence, audit & download (2026-07-23)

**Partial progress (2026-10-02) — size curve only:** the full/cut-size uploader now (a) reads the CSV as text
(stops `10` → `10.0` when sizes are all numeric), (b) **validates before replacing** the live table (required
headers, whole-number threshold, no blanks, allowed division/gender, APPAREL→ah2=APPAREL, one threshold per group,
KIDS rejected) and disables Run on error, (c) warns on partial files that would drop groups, `.0` size artefacts and
duplicates, and (d) has a **Download current definition** button. Still open for all configs: raw-file archive,
upload log/audit (who/when), version history/rollback, `.xlsx` support, and BU-eligibility validation. Kids
(`cfg_size_curve_kids`) still has no UI upload.

**Ask (business):** every CSV/Excel file uploaded under the run parameters must be **saved to a
Databricks table with its upload date**, and the user must be able to **download the latest uploaded
file from the UI**. Required for:
1. **Size definition** (`cfg_size_curve` ← `size_definition.xlsm`)
2. **BU eligibility** (`cfg_bu_eligibility`)
3. **Store–season constraint** (`cfg_store_season` ← `Season-Store constraint.xlsx`)

### Current state (the gap)
- The app has CSV uploaders for **size-curve** and **BU-eligibility** only. On upload
  `dbio.replace_config_table()` does a bare `CREATE OR REPLACE` of the cfg table —
  **no raw file stored, no upload date, no uploader, no version history, no way to download** what's
  currently live. Last upload silently overwrites the previous one.
- **Store–season** and **category-converter** are **not in the UI at all** — they are engine-maintained,
  seeded via CLI from the source workbooks. So part of this debt is *also bringing them into the UI
  upload flow.*
- Uploaders accept **CSV only**, but the business maintains these as **`.xlsm`/`.xlsx`** — today they
  must hand-convert to CSV before upload (lossy, error-prone).

### Suggested design
- **Raw-file archive table** (one shared), e.g. `cfg_upload_log(config_name, file_name, mime, file_bytes
  BINARY, row_count, uploaded_by, uploaded_at, is_active)` — store the *original* file bytes so it can be
  handed back byte-for-byte on download. (Alternatively a UC Volume for the blob + a metadata table.)
- On upload: write the raw file + metadata (`uploaded_by` from OBO identity, `uploaded_at` = now,
  `row_count`), mark previous rows for that `config_name` `is_active=false`, then rebuild the derived
  `cfg_*` table. Keep history (don't delete) → gives an audit trail + rollback.
- **UI "Download latest" button** per config → pulls the most-recent `is_active` blob back down in its
  original format.
- Accept **`.xlsx`/`.xlsm`** in the uploaders (parse with openpyxl), not just CSV.
- Surface **"last updated <date> by <user>"** next to each config in the run panel.

---

## Frequently-changing business config — apply the same pattern

Beyond the three named above, these are business-maintained and change on a cadence, so they belong in
the same upload → persist → audit → download flow:

- **Category converter** (`cfg_category_converter` ← `CATEGORY CONVERTER 8.xlsx`, 375 rows) — **high
  churn**: business remaps category/brand groupings as ranges evolve. Currently CLI-seeded, not in UI.
  Should be a first-class upload alongside the three above.
- **Scalar run parameters** (`cfg_scalar_params`: cover thresholds, ROS window, movement cost,
  sell-through / probability-to-sell, ROS-uplift %, sales channels) — already UI form fields and frozen
  per run in `run_config` (so there's a per-run trace), but **no audit of who changed the defaults or
  when**. Lower priority than the file uploads; a change-log on the defaults table would close it.
- **Movement cost / probability-to-sell** (`MOVEMENT_COST_PER_UNIT`, `SELL_THROUGH_FACTOR`) — still
  placeholders (OPEN-3/4). When the real figures land they may arrive as a **rate card** that changes
  periodically → candidate for a file-driven, versioned input rather than a single scalar.

**Not currently churning:** `cfg_brand_mapping` (13 Nike IDs) is **unused** after the BU-scope change —
skip unless article-brand scope returns.

---

## TD-5 · Enrich consolidation receiver-demand with PDA de-censored signal (2026-08-24)

**Status:** Deferred (parked). Build core DC→store replenishment (P1) first, as planned.

**Why:** As-of-T backtest (T=2026-06-30, fwd Jul) on where stock for *actual recurring losses* sits:
same-country **sister store (consolidation) 63%** · network-dry (buy/allocate) 27% · **in-country DC (replen) 6%** ·
UAE hub 4%. So the biggest in-season lever is **consolidation**, and its engine currently builds receiver-demand
from sales-based OOS only — **blind to censored (RoS=0) demand** that the PDA runner log captures directly.

**The enhancement:** feed `runner_app_catalog` PDA loss (recurring, de-censored) into the consolidation
`receiver_pool` (module 03) demand universe, so consolidation recommends transfers for sizes that show zero sales
but real repeated demand. Expands/sharpens the 63% lever, especially RoS=0 censored sizes.

**Caveat on the 6% / 63% split:** the backtest ran on **July = launch season** (3/6-mo cycle) — unrepresentative
(new products, thin DC, allocation-dominated). Re-run on an in-season month before quoting; the in-country-DC
share is biased low. Structural finding (thin country DCs, UAE=85% of DC stock) is robust regardless.

**Dev artifacts:** `sports_analytics_test.replen_p1_dev.{pda_demand_signal, bt_worklist_T, bt_forward}`.
