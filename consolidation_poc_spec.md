# Store-to-Store Consolidation Engine — Build Specification (POC)

**Project:** Nike Consolidation POC
**Scope:** Store-to-store stock movement recommendations (consolidation only)
**Target platform:** Databricks
**Execution model:** Deterministic batch job
**Status:** Ready to build. Open business items are flagged inline as `[OPEN]` and must not be assumed by the implementer.

---

## 1. Purpose & scope

### 1.1 What this builds
A deterministic engine that recommends **store-to-store transfers** of Nike stock: moving slow-moving / dead stock from a *donor* store to a *receiver* store where the same article is more likely to sell, subject to a defined set of business constraints, and only where the move produces a positive net benefit (sales uplift minus movement cost).

The output is a set of recommended transfer lines (donor store → receiver store, at article × size level, with quantity, reason, expected uplift, cost, and net benefit). **The engine recommends; it does not execute transfers.**

### 1.2 Explicitly in scope
- Consolidation only: **store-to-store** movement.
- **Nike business unit only** (including Jordan, per how Nike is grouped). Non-Nike BUs are out.
- One country at a time — moves are **intra-country only** (see constraint C7).

### 1.3 Explicitly out of scope
- **Replenishment** (warehouse/DC-to-store). Already automated; not touched here. To be layered on later.
- All non-Nike Sports brands and other divisions (EDG, HNB).
- Physical execution / STO creation. The engine outputs recommendations only.
- APO-to-DC pull-out process — runs as-is, untouched (see C4 and `[OPEN-1]`).

---

## 2. Non-functional requirements

### 2.1 Determinism (hard requirement)
The same input data + same config **must** produce the identical output, byte-for-byte, on every run.

Implementation rules:
- **No randomness.** No `rand()`, no sampling, no non-seeded operations.
- **No reliance on row order.** Never depend on the physical/scan order of a DataFrame. Any operation whose result could depend on ordering (ranking donors/receivers, "pick the top N") must use an **explicit, total sort key with deterministic tie-breakers.**
- **Tie-breaking must be explicit and total.** Wherever two candidates rank equally on the primary metric, break ties on a defined, stable secondary key (e.g. then by `article_id`, then by `store_id` ascending). Never leave a tie to be resolved arbitrarily.
- **Stable aggregations.** Do not use functions whose output ordering is unspecified (e.g. `collect_list` without a subsequent sort) in a way that affects results.
- Config values are read at the **start** of the run and held constant for the whole run.

### 2.2 Platform
- Implemented on **Databricks** (PySpark / Spark SQL). Set-based transformations, not row-by-row loops.
- Config (scalars) passed in as **job parameters** (widgets); config lists/mappings supplied as **CSV/table inputs** (see §5).
- Job should be runnable per-country (parameter) and per full-run.

### 2.3 Data layer (interface — to be bound to real tables)
> The actual **table names and data dictionary will be provided as a file in this project folder.** The implementer must bind the logical entities below to the real tables/columns from that dictionary. **Do not invent table or column names.** If a required field in §5.1 has no counterpart in the supplied dictionary, **stop and flag it** rather than assuming a source.

Logical inputs the engine requires (bind each to the dictionary):
- Stock / ATP position by store × article × size.
- In-transit transfers (dispatched, not yet GRN'd) by store × article × size, with dispatch date.
- Sales history by store × article × size × date (with channel, so ecom/marketplace can be included or excluded — see `[OPEN-2]`).
- First-GRN date per store × article.
- APO flags at store × article × size level.
- Store master: store, BU, country, store grade, store capacity.
- Article master: article (child) → generic/parent mapping; size; brand; category.

---

## 3. Core logic (deterministic pipeline)

Process one **country** at a time. All matching happens **within a single country and within eligible BU pairs** (C7, C8).

### Step 1 — Set the universe
- Filter to **Nike** articles via the brand mapping (§5.2).
- Compute **effective stock** (Step 1a).
- Classify **new-to-store** and exclude from computation (but retain for output visibility — see §3, Step 3).

**Step 1a — Effective stock definition**
```
effective_stock[store, article, size] =
      ATP[store, article, size]
    + in_transit_toward_store[store, article, size]   (dispatched, not yet GRN'd, within trailing IN_TRANSIT_LOOKBACK days)
```
- In-transit toward a store is **added to the receiver's position** so we don't over-send to a store that already has stock arriving.
- In-transit already dispatched **out of** a donor (not yet GRN'd at destination) must be **excluded from the donor's available-to-give**, so two runs can't move the same units twice.
- `[CONFIRM-A]` The dictionary must let us track in-transit **both inbound and outbound**. If in-transit is only tracked inbound, flag it — the donor-side netting cannot be done and must be raised, not silently skipped.

### Step 2 — Rate of sale & cover
```
ROS[store, article, size]  = units_sold_in_window / ROS_WINDOW_DAYS
cover_days[store, article, size] = effective_stock / ROS        (ROS > 0)
cover_days = +infinity                                          (ROS = 0)
```
- `ROS_WINDOW_DAYS` is a **configurable parameter, default 14** (§5.1).
- Sales window channel inclusion (ecom / marketplace) governed by `[OPEN-2]`.

### Step 3 — Classification (4 classes)
Each store × article × size is exactly one of:
| Class | Rule |
|---|---|
| `NEW_TO_STORE` | first GRN to store < `NEW_TO_STORE_DAYS` (default 14) ago → **excluded from computation**, retained in output for visibility |
| `DEAD` | zero sales in the ROS window (ROS = 0) |
| `SLOW_MOVING` | `cover_days > SLOW_MOVING_THRESHOLD` (default 30) |
| `HEALTHY` | `cover_days <= SLOW_MOVING_THRESHOLD` |

Precedence: evaluate `NEW_TO_STORE` first (it removes the row from matching regardless of its cover).

### Step 4 — Donor pool
A store × article × size is a **donor candidate** if:
- class is `DEAD` or `SLOW_MOVING`, **and**
- it holds more than its protected reserve: `effective_stock > ROS * PROTECT_COVER_DAYS` (dead stock: entire stock is giveable since ROS = 0).

### Step 5 — Receiver pool
A store is a **receiver candidate** for an article if **all** hold:
- class is `HEALTHY` for that article, **and**
- `cover_days < TARGET_COVER_DAYS` (default 14) — i.e. it has room, **and**
- room remains under store capacity (C6), **and**
- same **country** as the donor (C7), **and**
- donor BU → receiver BU is allowed by the BU-eligibility matrix (C8), **and**
- passes the **APO guard** (Step 5a / C4).

**Step 5a — APO guard (receiver block, parent-level)**
- APO is flagged at **child (size)** level, but blocks at **parent (generic)** level for that receiver store.
- Build, per receiver store, the set of **parent articles that have ANY child size flagged APO** at that store.
- **Any** transfer whose article rolls up to a parent in that set is **blocked entirely** for that receiver — regardless of which size is being moved.
- Example: size M of article 1 is APO at store 1001 → **no** size of article 1 (incl. S, L) may transfer into 1001.
- **This guard also serves as the pull-out guard** — APO *is* the pull-out list; there is no separate pull-out list (confirmed).

### Step 6 — Match donors to receivers (per article, deterministic)
For each parent article, within country and eligible BU pairs:
- **Rank receivers** by demand strength: primary = highest ROS, then lowest `cover_days`. **Tie-break** on `store_id` ascending (total order — see §2.1).
- **Rank donors** by deadest-first: `DEAD` before `SLOW_MOVING`, then highest `cover_days`. **Tie-break** on `store_id` ascending.
- Allocate from strongest receiver need to weakest-selling donor, greedily and deterministically. Allocation order and tie-breaks must be fully specified so the result is reproducible.

### Step 7 — Size the move (recommended quantity)
For each donor → receiver pair, per size:
```
donor_can_give    = effective_stock_donor - (ROS_donor * PROTECT_COVER_DAYS)     # dead donor: = full stock
receiver_can_take = (ROS_receiver * TARGET_COVER_DAYS) - effective_stock_receiver
receiver_can_take = min(receiver_can_take, capacity_headroom_receiver)            # C6 capacity cap
recommended_qty   = floor( min(donor_can_give, receiver_can_take) )              # integer units, never negative
```
- Quantities are **whole units**. Drop any line where `recommended_qty <= 0`.

### Step 8 — Size-curve test (configurable, can reshape the move)
- Full-size vs cut-size is defined by business at **brand × category** level (§5.2) and supplied as config.
- After Step 7 sizing, evaluate the **donor's** resulting size curve:
  - If donor **remains full-size** after donating → release `recommended_qty` as-is.
  - If donating would drop the donor to **cut-size** → do **not** auto-block. Instead evaluate whether moving the donor's **complete size run** to the receiver is worthwhile, judged at the **generic (parent) article** level using **ROS at generic level**:
    - If receiver parent-level ROS beats donor parent-level ROS by enough to justify it → move the **full size run** (reshape: send complete sizes, potentially clearing/cut-sizing the donor).
    - Else → **hold** the move.
- `[CONFIRM-B]` The exact "beats by enough" threshold for the parent-level ROS comparison is a business rule. Expose it as a config parameter (e.g. `GENERIC_ROS_UPLIFT_MIN`); do **not** hard-code a magic number. Flag if business has not supplied it.
- Net effect: the size-curve rule can **shrink, block, OR enlarge** a move (into a complete run), never break the donor's curve silently.

### Step 9 — Score net benefit
```
expected_uplift_units = recommended_qty                          # valued at RECEIVER ROS (see note)
sales_uplift_value    = expected_uplift_units * net_sales_per_unit_at_receiver
movement_cost         = recommended_qty * MOVEMENT_COST_PER_UNIT  # placeholder cost, see [OPEN-3]
net_benefit           = sales_uplift_value - movement_cost
```
- **Uplift is valued at the receiver's ROS** (confirmed): the moved units are expected to sell through at the receiver's rate of sale.
- **Keep a move only if `net_benefit > 0`.**
- `[OPEN-4]` **Uplift conversion assumption.** Current model treats moved qty as selling through at receiver ROS. This likely **overstates** benefit — not every moved unit converts within the window. Confirm with business whether to apply a **sell-through probability / conversion factor** (expose as `SELL_THROUGH_FACTOR`, default 1.0 for POC) before this goes beyond POC. Do not silently assume 100%.
- `[OPEN-3]` **Movement cost** is a **placeholder** — real figures to be supplied. Keep as a single tunable parameter (`MOVEMENT_COST_PER_UNIT`, or per-carton/per-move if business defines it that way) so swapping in the real cost is one config change.

### Step 10 — Output
One row per recommended move:
`country, parent_article, article (child), size, donor_store, donor_bu, receiver_store, receiver_bu, recommended_qty, classification/reason (DEAD|SLOW_MOVING), donor_cover_days, receiver_ros, expected_sales_uplift, movement_cost, net_benefit`
- Sort output deterministically (e.g. by `country, parent_article, receiver_store, donor_store`).
- Optionally include a metadata note for B/C-grade receivers: "dispatch on next scheduled run" (informational only — see §4).

---

## 4. Notes on lead time & delivery cadence (informational — NOT modelled)
- Actual **transit is ~1 day** (intra-country). This is **not** modelled as a delay; target-cover is not inflated for it.
- **Delivery cadence**: daily to A/A+ stores, once every 3 days to B/C stores. This is a **delivery frequency**, not transit time. A move to a B/C store may wait up to 3 days for the next scheduled run — **negligible against a 14-day cover, so not computed.** May appear as an informational note in output only.
- The earlier "14-day STO" figure was a **look-back window on in-transit stock** (Step 1a), **not** a lead time. Do not treat it as a delay.
- **Caution (not a rule):** A/A+ stores are already topped up daily by the automated replenishment engine. Pushing consolidation stock into them risks double-supplying and competing with that engine. This is why the APO/replenishment coordination guard matters. Business may later prefer B/C stores as consolidation receivers; not enforced in POC — flag as observation.

---

## 5. Configuration (all external — no hard-coding)

All config is exposed either as **UI/job parameters** (scalars) or **CSV/table uploads** (lists & mappings). The code reads config; it never hard-codes any of these values.

### 5.1 Scalar parameters (job widgets)
| Parameter | Default | Owner | Notes |
|---|---|---|---|
| `ROS_WINDOW_DAYS` | 14 | tunable (UI) | Rate-of-sale window |
| `IN_TRANSIT_LOOKBACK_DAYS` | 14 | business | In-transit look-back for effective stock |
| `SLOW_MOVING_THRESHOLD_DAYS` | 30 | business | Cover above this = slow-moving |
| `NEW_TO_STORE_DAYS` | 14 | business | GRN younger than this → excluded |
| `PROTECT_COVER_DAYS` | 7 | business | Donor reserve |
| `TARGET_COVER_DAYS` | 14 | business | Receiver eligibility threshold **and** fill-to level |
| `MOVEMENT_COST_PER_UNIT` | placeholder | **[OPEN-3]** | Real cost TBD |
| `SELL_THROUGH_FACTOR` | 1.0 | **[OPEN-4]** | Conversion factor on uplift |
| `GENERIC_ROS_UPLIFT_MIN` | **[CONFIRM-B]** | business | Threshold for cut-size reshape decision |
| `COUNTRY` | — | run param | Country to process (intra-country only) |

**Validation checks the code must enforce at startup (fail fast):**
- `PROTECT_COVER_DAYS < TARGET_COVER_DAYS` — if violated, stock would circulate; **abort with a clear error.**
- All defaults present; any required config missing → **abort with a named error**, do not run on assumptions.

### 5.2 Mapping / list inputs (CSV upload or table)
| Input | Purpose | Source |
|---|---|---|
| Nike brand list | Filter to Nike articles | Nidhin |
| Category converter | Category normalisation | Nidhin (ongoing maintenance) |
| Full-size vs cut-size definition | Size-curve rule, at **brand × category** level | business |
| BU-to-BU eligibility matrix | Which donor BU may send to which receiver BU. **Default: Nike → Nike only.** Configurable to open to SSS/JD later without code change | config |
| Article → generic (parent) mapping | Parent-level APO guard & generic ROS | article master / dictionary |

---

## 6. Business constraints (consolidated — the guardrails)
Constraints can only **reduce or block** a move (or, for C1, reshape it) — never create one.

| # | Constraint | Rule |
|---|---|---|
| C1 | Full size curve | Donor's curve must not be silently broken; receiver's curve favoured. Cut-size reshaping per Step 8. |
| C2 | Slow-moving threshold | Cover > 30 days (config) → donor trigger. |
| C3 | New-to-store | First GRN < 14 days (config) → excluded from computation. |
| C4 | **APO guard** | Any child size APO at receiver → **whole parent blocked** into that store. Also serves as the pull-out guard (APO = pull-out list). |
| C5 | Protect donor cover | Reserve `PROTECT_COVER_DAYS` (7) at donor if it still sells. |
| C6 | Capacity cap | Receiver total units must not exceed store capacity after the move. |
| C7 | Intra-country only | Donor and receiver must be in the same country. No cross-border moves. |
| C8 | BU eligibility | Donor BU → receiver BU must be allowed by the matrix. Default Nike → Nike only; configurable. |
| C9 | Net benefit | Keep move only if `net_benefit > 0` (uplift at receiver ROS − movement cost). |

APO stock is excluded from consolidation on **both** sides for the POC: it cannot be a receiver's incoming article (C4), and it is **not** surfaced as a donor (`[OPEN-1]`).

---

## 7. Open items — take to business, do NOT assume
| Tag | Item | Impact |
|---|---|---|
| `[OPEN-1]` | **APO donor routing.** Currently APO stock returns to DC (unchanged). Business to confirm whether APO stock should instead be diverted to a live-selling store via consolidation. Parked, not dismissed. | Could add a whole donor source |
| `[OPEN-2]` | **Ecom / marketplace in the sales signal.** For Sports, own-ecom carries no inventory and marketplace partners aren't queryable by name — so ecom/marketplace can only ever be a **demand signal**, never a donor/receiver node. Confirm whether their sales count toward ROS / probability-to-sell. | Changes ROS values |
| `[OPEN-3]` | **Movement cost** — placeholder until real figures supplied. | Changes which moves pass C9 |
| `[OPEN-4]` | **Uplift conversion assumption** — moved qty × receiver ROS likely overstates benefit; confirm sell-through factor. | Biggest modelling choice; changes net benefit |
| `[OPEN-5]` | **Season inclusion** (NFS / prime door) — TBD with category heads: which seasons are eligible for consolidation. | Filters the universe |
| `[CONFIRM-A]` | In-transit tracked **both** inbound and outbound? If inbound-only, donor-side netting can't be done — flag. | Correctness of effective stock |
| `[CONFIRM-B]` | Parent-level ROS "beats by enough" threshold for cut-size reshape (`GENERIC_ROS_UPLIFT_MIN`). | Step 8 behaviour |

---

## 8. Recommended build order
1. **Data binding & effective-stock layer** (Steps 1–1a) against the supplied dictionary; resolve `[CONFIRM-A]` here.
2. **ROS, cover, classification** (Steps 2–3).
3. **Donor / receiver pools + APO guard + BU/country filters** (Steps 4–5a, C4/C7/C8).
4. **Deterministic matching & sizing** (Steps 6–7) — get tie-breaking and determinism right here.
5. **Size-curve reshaping** (Step 8) — needs `[CONFIRM-B]`.
6. **Net-benefit scoring & output** (Steps 9–10) — placeholder cost `[OPEN-3]`, factor `[OPEN-4]`.
7. **Config validation harness** (§5.1 fail-fast checks).

A worked single-article / multi-store example (with an APO'd size at one store so C4 visibly fires) is recommended as a test fixture to validate Steps 6–9 and to let business react to the uplift assumption `[OPEN-4]` against real numbers.
