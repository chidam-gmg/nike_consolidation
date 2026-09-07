# Nike Consolidation — Tech Debt

Running backlog of known shortcuts / deferred work. Newest first.

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
