# DC → Store Replenishment — Proposed Approach

*Prepared 2026-08-18. Companion to the store→store consolidation engine (`consolidation_poc_spec.md`).
Grounded in live data pulled 2026-08-18 (evidence in §11).*

## 0. Recommendation in one paragraph

Do **not** build a classic FMCG `(s,S)` / `z·σ√LT` safety-stock reorder system. It is the wrong physics for
fashion. Build a **cover-based, size-curve-aware, order-up-to model that rations a finite, non-replenishing DC
pool** across stores, using **empirical lead time** and **Poisson / small-integer service math** (not the normal
approximation). Architecturally this is the **twin of the consolidation engine** — the same demand universe, the
same cumulative-interval waterfall — with the **DC added as the preferred donor**. ~70% of it is already built.

---

## 1. Does FMCG replenishment theory apply to fashion? (honest verdict)

The instinct — forecast → lead time → safety stock → reorder point — is right in *spirit* but wrong in *math* for
most of a fashion assortment. Verdict per concept, with evidence:

| FMCG concept | FMCG assumption | Fashion reality (this business) | Verdict |
|---|---|---|---|
| **Forecast a stable mean μ** | Long-lived SKU, months of history | Article×size lives ~one season (~13 wk); most never accumulate enough history | **Drop** for the tail; **keep** a recency-weighted rate-of-sale for continuity/core |
| **Safety stock `z·σ·√LT`** (normal) | Demand ≈ Normal, σ estimable | **85% of store×size sell ≤3 units in 90 days; 55% sell exactly 1** (§11a) → intermittent/lumpy, σ meaningless | **Drop.** Use Poisson / empirical quantile of lead-time demand |
| **Lead time matters** | Yes | Yes — and **variable**: DC→store total P50 4d / P75 8d / **P90 15d** (§11b) | **Keep**, but as an **empirical per-lane quantile**, not mean±σ |
| **Continuous reorder from supplier** | Infinite upstream supply | Seasonal buy is **fixed**; the DC pool only **depletes**, never refills | **Drop.** This is **rationing/allocation**, not reorder |
| **Service level / fill rate objective** | Target 95–98% | Same goal | **Keep**, but **bounded by the scarce pool** (can't fill everyone) |
| **EOQ / economic order qty** | Order in cost-optimal batches | 59.6% of transfer lines are 1 unit → churn waste (CTO analysis) | **Adapt** → min line qty + review cadence to stop 1-unit churn |
| **Presentation / display minimum** | Weak (shelf facings) | **Strong** — a store only *sells* a style if it can **display the size ladder**; a partial run sits dead | **Add** — no real FMCG analogue; often the *dominant* driver |
| **Lifecycle phase** | Flat | Launch → full-price → markdown; replen logic differs by phase | **Add** — EOL cutoff, launch push |

**Bottom line:** keep *lead time* and *fill-rate intent*; **replace** normal safety-stock with Poisson/quantile
service math; **replace** the reorder loop with **pool rationing**; and **add** the two fashion-specific forces —
**size-curve presentation** and **lifecycle** — that FMCG models don't carry.

---

## 2. The core reframe

> Fashion "replenishment" from a DC is **the allocation of a finite, non-replenishing pool to stores so as to
> (a) keep the size curve presentable at the right doors and (b) chase realized rate-of-sale — subject to lead
> time, season and grade.**

That is a **rationing waterfall**, not an `(s,S)` reorder controller. Which is exactly why it is the natural twin
of the consolidation engine: consolidation already rations *donor-store* surplus to receivers under demand, size
curve, season, grade, in-transit and APO guardrails. Replenishment is the **same problem with the DC as the
donor** — a donor with a big, non-replenishing pool that should be preferred over other stores.

---

## 3. Demand signal — per (store × article × size)

The signal, not the forecast, is what we can defend.

1. **Recency-weighted rate of sale (ROS)** — EWMA over trailing weeks (heavier weight on recent), **gross units**
   (a sold-then-returned unit still signals demand — same call already made in consolidation `02`).
2. **Censoring correction** — a stocked-out size shows 0 sales but had demand. Exclude (or deflate) **ATP=0 days**
   from the ROS denominator, or impute from peer stores. (This is the exact blind spot consolidation already fixed
   with the OOS-demand universe — reuse it.)
3. **Cold-start hierarchy** (new SKU, no history): borrow strength —
   `ROS_est = size_share(parent) × peer_store_ROS(same size, same grade) × grade_index`.
   The size ladder + peer doors + the style's early sell-through substitute for absent history.
4. **Regime tag** — *continuity/fast* (the ~2% tail selling ≥13u/90d, 44% of volume) vs *intermittent* (the 85%).
   The two get different targets: continuity is cover-driven; intermittent is **presentation-driven**.

---

## 4. Target = order-up-to level **S**, expressed in cover

```
S_units(store,article,size) =
      ROS × protection_window                     -- demand during exposure
    + safety_units                                -- Poisson quantile, NOT z·σ
    + presentation_floor                          -- size-curve display minimum
S_need = max(0,  S_units − pipeline)              -- pipeline = ATP + in-transit + open STO
```

- **protection_window** = review_period + **lead_time** taken as an **empirical per-lane quantile** (P75 ≈ 8d for
  most doors; P90 ≈ 15d for flagship/critical). Note the lead-time variability lives in **DC create→dispatch**
  (P50 2 → P90 11d), not transit — so it is partly a *controllable* internal SLA, and the cushion should size to
  the DC's actual processing tail.
- **safety_units** = the service-level quantile of **Poisson(λ = ROS × lead_time)** minus its mean — for lumpy
  demand this is a **small integer**. Worked example: a size selling ~0.2/wk, LT 8d → λ ≈ 0.23; the 95%-service
  order-up-to is **1 unit**. *This is the whole point:* for 85% of the assortment, statistical safety stock
  **collapses to "carry 1 if it sells at all,"** and the real lever becomes presentation, not σ.
- **presentation_floor** — key display sizes (from `cfg_size_curve`) must reach a **minimum display quantity at
  grade-appropriate doors even when ROS ≈ 0**. Reuse the size-curve + A+ curve-completion logic already in `03/05`.
  For slow sizes this term **dominates** and *replaces* statistical safety stock.
- **pipeline** — on-hand ATP + in-transit (`combined_inventory`) + not-yet-received open STO. In-transit-aware
  netting is already built.

---

## 5. Trigger & cadence (kill the 1-unit churn)

- **Trigger:** raise a replen only when `S_need ≥ trigger` where `trigger = max(min_line_qty, presentation_gap)`.
  This directly attacks the 59.6%-single-unit-move waste.
- **Review period R:** a **fixed cadence per lane** (e.g. 2×/week) rather than continuous. A defined `R` is what
  makes the protection-window math well-posed *and* batches small needs into fewer, fuller shipments.

---

## 6. Rationing the finite DC pool (the crux, and the fashion-specific part)

For a `(country, article, size)`, store need routinely **exceeds** DC ATP (the pool is finite and only depletes).
Allocate the scarce pool by a **priority waterfall** — reuse the `04` cumulative-interval allocator verbatim, with
DC ATP as the supply line:

**Priority order:** (1) **presentation / curve completion** at high-grade doors → (2) **genuine OOS at selling
stores** (lost sales today) → (3) **proportional fill** for the rest (fair-share ∝ gap-to-target or ∝ ROS),
`FLOOR(overlap + 1e-4)` to whole units, deterministic tiebreak `grade_rank → ROS desc → store_id`.

Fair-share under scarcity (supply < demand) is where FMCG "fill to S at every store" fails and a **rationing rule**
is mandatory — this is genuinely fashion-shaped and must be explicit, auditable and deterministic.

---

## 7. Lifecycle guardrails

- **Season / newness** — reuse `cfg_store_season`: fresh doors receive current-or-newer only.
- **Markdown / EOL cutoff** — **stop replenishing** an article within *N* weeks of season end or once markdown-
  flagged: shipping stock that will be marked down where it lands is pure cost. *(Gap: needs a season-end/markdown
  signal; interim proxy = season-ordinal age already computed for C10.)*
- **Launch push** — at launch there is no ROS; initial fill by plan/grade/size-curve. That is the **Allocation**
  roadmap item; keep *replenishment* to items with a demand signal and let **curve-completion** cover the no-ROS
  display need (as consolidation already does).

---

## 8. Architecture — build it as the twin of consolidation

Two ways to combine the two flows:

- **(A) Sequential** — run replen (DC→store) first, then consolidation on the residual the DC can't cover. Simple,
  but two passes and a hand-off to keep consistent.
- **(B) Unified fulfilment engine — *recommended*.** One decision per `(store, article, size)` **need**, sourced
  from a **ranked supply stack: DC pool first (replen) → donor stores (consolidation)**. A single waterfall whose
  supply sources are `[DC pool] ∪ [donor stores]`, DC preferred. This **dissolves today's "warehouse-first"
  guardrail into the sourcing rank**, so the two flows are reconciled by construction — no size is both
  replenished and consolidated, and DC scarcity automatically spills to store donors.

**Why B is cheap to build — replen is ~70% done inside consolidation already:**

| Needed for replen | Status in consolidation engine |
|---|---|
| Country DC identity | ✅ `cfg_country_dc` (01) |
| DC sellable pool | ✅ `warehouse_stock` (01, SL01 ATP) |
| Full store demand incl. **OOS** + **curve** | ✅ `receiver_pool` (03) |
| In-transit-aware need | ✅ (Phase-1 demand-side work) |
| Size curve / grade / season / APO guards | ✅ (03/04/05) |
| Deterministic waterfall allocator | ✅ `04` |
| **DC as a donor supply node (can_give, priority)** | ❌ to build |
| **Rationing when DC short of total store need** | ❌ to build (fair-share) |
| **Empirical per-lane lead-time table** | ❌ to build (data exists — §11b) |
| **Review cadence + min-line batching** | ❌ to build |
| **Lifecycle EOL cutoff** | ❌ to build (needs signal) |

So the missing 30% is: (i) model the DC as a donor with its own `can_give`+priority, (ii) fair-share rationing,
(iii) an empirical lead-time table, (iv) review-cadence/min-line batching, (v) lifecycle EOL cutoff.

---

## 9. Data readiness

**Have:** `atp_detail` (DC SL01 = the pool + `unit_cost_aed`), SAP + POS sales (ROS, censoring, gross/net),
`sto_table`/`obd_table`/`ibd_table` (empirical lead time — computed in §11b), `combined_inventory` (in-transit),
store & product master (grade, BU, PLANTCAT, season), `cfg_size_curve`, `cfg_store_season`, `cfg_store_grade`.

**Gaps:** (a) explicit **open-STO / on-order** pipeline snapshot (derivable: STO lines created, not yet GRN'd);
(b) a **markdown / season-end** signal for the EOL cutoff; (c) a **review-cadence** config.

---

## 10. Determinism, validation, phasing

- **Determinism** inherited: config frozen in `00`, AS_OF frozen, total-ordered waterfall, `FLOOR(x+1e-4)`.
- **Validation / backtest:** fill-rate lift, OOS-days reduction, size-curve completeness at A/A+, single-unit-move
  reduction, and the hard invariant **Σ allocated ≤ DC ATP per (country,article,size)** (no over-allocation).
  Backtest against actual STO+sales history (would the model have shipped what sold?).
- **Phasing:**
  - **P1** — empirical lead-time table + **DC-as-donor** supply node; replenish where the DC clearly has stock and
    a size is OOS/below-cover. (Ships value fast, low risk.)
  - **P2** — **rationing** (fair-share when DC short) + review cadence + min-line batching.
  - **P3** — lifecycle **EOL cutoff** + cold-start **launch allocation** (bridges into the Allocation roadmap item).

---

## 11. Evidence appendix (queries run 2026-08-18)

**(a) Demand is intermittent — settles the safety-stock question.**
Store × variant (article×size), channel 10, trailing 90 days:

| Units sold in 90d | store×size pairs | share of pairs | units |
|---|---|---|---|
| 1 | 953,044 | **55%** | 0.95M |
| 2–3 | 509,008 | 29% | 1.18M |
| 4–6 | 168,733 | 10% | 0.79M |
| 7–12 | 68,139 | 4% | 0.59M |
| 13+ | 40,017 | **2.3%** | 2.40M |

≤3 units/90d = **84%** of all selling pairs. The normal approximation for safety stock is invalid across the vast
majority of the assortment; only the ~2% "continuity" tail (which is 44% of *volume*) behaves FMCG-like.

**(b) DC→store lead time — empirical, and variable.**
Replen transfer types `ZICR/ZTLT/ZCCR/ZIPT`, source PLANTCAT='B' → dest 'A', 6 months, 3.94M legs:

| Leg | P50 | P75 | P90 |
|---|---|---|---|
| Create → dispatch (DC processing) | 2d | 6d | **11d** |
| Dispatch → GRN (transit) | 1d | 2d | 5d |
| **Total** | **4d** | **8d** | **15d** |

Variability is dominated by DC **create→dispatch**, i.e. an internal processing SLA (partly controllable), not
physical transit. The lead-time cushion in §4 should size to this tail (P75–P90), not the mean.
