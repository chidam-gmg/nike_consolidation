# Stock Movement — Unified-Ready Design (Consolidation + Replenishment)

*Proposal only (2026-08-24). No build. Companion to `consolidation_poc_spec.md` and `Replenishment_P1_spec.md`.*

## Decisions locked
1. **Section switch**, not just a tab — the tool becomes **"Stock Movement"** with two selectable sections.
2. **Independent runs now**, but architected so a **single unified run** ("do all stock movements") drops in later
   **without a rewrite**.
3. **No changes to existing consolidation logic** — it is in business validation. Replenishment is **strictly
   additive**; the PDA-into-consolidation demand upgrade stays **TD-5** until the business is given a heads-up.

## 1. UI — section switch
```
Stock Movement            [ Store ↔ Store · Consolidation ]   [ DC → Store · Replenishment ]   ← segmented switch
  Consolidation section  → All | Moves | Held | Not moved            (today's tabs, unchanged)
  Replenishment section  → Recommendations | Rationed-out | Not replenished
```
Shared header, run panel and run-history across both. Each section keeps its own sub-tabs and review UX.

## 2. Guiding principle — shared *contract*, independent *execution*
Both engines conform to four common **contracts** but run as **separate jobs** today. A future orchestrator merges
them by reusing those contracts — not by rewriting either engine. This is what makes "independent now / unified
later / no consolidation change" mutually consistent.

## 3. The four contracts (the forward-compatibility backbone)
| Contract | Now | Unified later |
|---|---|---|
| **Input layer** (sales/RoS, ATP, masters, size-curve, in-transit) — read-only, neither engine owns | both read the same source tables | unchanged |
| **Need** = (store, article, size, target, need_qty) | replenishment writes it in this canonical shape; consolidation keeps computing internally | orchestrator builds Need **once** (shared, de-censored) and both sourcing modules read it |
| **Supply node** = {type, location, qty, priority, lead_time} | DC pool = node(priority 0); replen allocator consumes supply-nodes | donor stores added as nodes(priority 1+) → one merged ranked stack, one waterfall |
| **Output** = movement_recommendations(**flow**, **source_type**, …common review cols…) | two separate tables (consolidation live table untouched; replen in dev schema) | a UNION view over both; UI already reads via a common loader |
| **AS_OF** snapshot | each engine takes AS_OF as an **injected parameter** (not self-computed) | orchestrator freezes ONE AS_OF for the whole run |

Designing replenishment to these shapes now = it slots into the unified engine later with no rebuild.

## 4. Source order = DC-first (business order) + no-double-supply, zero consolidation change
**Business currently fulfils DC → store FIRST, then consolidates.** We follow that order now (DC-first). Whether
store-first is better (it would preserve the finite DC pool and leans on the 63% consolidation lever) is a
**hypothesis to PROVE, not assume** — logged as a future experiment (§8), not adopted yet.

Because runs are **independent** and consolidation code is **untouched**, DC-first is honoured **operationally, not
by coupling code**:
- Replenishment is reviewed/actioned **first**; consolidation then runs on **post-replenishment ATP** (a size just
  replenished is no longer OOS, so consolidation naturally won't re-propose it). This needs **no consolidation
  change** — it only reads current stock, which now reflects the replenishment.
- If both are run on the **same snapshot** (not yet sequenced), the replenishment section **flags overlaps** with
  consolidation's published output (read-only) for reviewer awareness — it does **not** suppress or alter either.

## 4b. Prove-it experiment (future) — DC-first vs store-first
Before changing the business order, quantify: cost of depleting the finite DC pool (lost future flexibility,
cross-border refill cost) vs store→store transfer cost & speed, at equal service level. Only flip the unified
stack to store-first if the data shows net benefit. Until then the unified stack default = **DC-first**.

## 5. Run model — now
- **Two jobs:** consolidation (`00–06`, unchanged) + replenishment (its own notebook chain in a dev schema).
- **Run panel:** shared config (country · AS_OF · RoS window · brand scope) + two collapsible param blocks
  (Consolidation params / Replenishment params). **Mode = Consolidation · Replenishment · Both.**
  "Both" = run consolidation, then run replenishment netting off consolidation's output (same AS_OF).
- Determinism preserved independently in each engine (frozen config, total-ordered waterfall).

## 6. Migration to the single unified run — later, no rewrite
Future **Stock Movement** orchestrator: freeze one AS_OF → build the shared **Need** once (unified demand incl. the
PDA de-censored signal — this is TD-5, the one change that needs a business heads-up) → merge **supply nodes**
(DC pool + donor stores) → **one ranked waterfall**, **DC-first (business order)** unless §4b proves store-first →
tag `source_type` → same two sections. The allocators and guards are **reused** because the contracts already exist.
The only substantive change is consolidation adopting the shared Need — deliberately deferred (**TD-5; business
confirmation expected ~2026-08-26**).

## 7. Input taxonomy (what's shared vs distinct)
- **Shared:** country, AS_OF, brand scope, product/store master, grade, season, RoS window + sales source, store
  ATP + in-transit, size-curve/display def, APO guard, BU eligibility.
- **Replenishment-only:** DC pool + country-DC map, empirical lead-time table, review cadence, min/max line qty,
  max(cover, floor) sizing, rationing priority, PDA signal + recurrence threshold/window.
- **Consolidation-only:** slow-moving/protect-cover/target-cover, movement cost, cut-size reshape threshold,
  new-to-store days, donor = store dead/slow stock.
- **Divergent (unify at TD-5):** demand signal (sales-only OOS vs sales+PDA); supply source (donor stores vs DC).

## 8. Open items / decisions
- **Source order:** DECIDED — follow business order **DC-first** for now (§4). Store-first only if the §4b
  experiment proves net benefit.
- **TD-5 timing:** business confirmation expected **~2026-08-26**; until then no change to consolidation demand.
- **Run coupling:** DECIDED — **independent** for now (two jobs, no code coupling; DC-first honoured operationally).
- **Prove-it experiment (§4b):** schedule the DC-first vs store-first cost/benefit analysis before any order change.
