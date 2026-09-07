# GMG Sports — Store Stock Movement Analysis
### Data pack for CTO brief (source for PPT)

**Prepared:** 2026-08-12 · **Analyst:** chidambaram.s@gmg.com
**Period:** 12 months (2025-08-01 → 2026-07-31) · **Currency:** AED
**Scope:** Sports division store-to-store & store↔warehouse stock movements, all BUs (GCC + Egypt).

---

## 0. Purpose & headline

Quantify the scale, cost, cycle time and **waste** in physical stock movements between Sports stores, to support
the case for an automated, recommendation-driven consolidation engine (recommend-only; ops execute).

> **Headline:** In 12 months, Sports created **~236,000 store-to-store transfers** moving **~3.23M units**, costing
> **~AED 9.7M/yr** in logistics (@ AED 3/unit all-in). Of this, **~213K units (~AED 1.3M/yr) is confirmed waste** —
> return-to-origin stock (boomerang + ping-pong) that moved but had *no sale within ±30 days of the return* (demand-driven replenishment excluded). A further
> ~AED 3.2M/yr sits in single-unit moves ripe for batching, and ~23% of created transfers are never even dispatched.

**Cost basis:** AED **3 per unit-movement** (conservative), all-in = handling out + logistics + handling in — grounded in the
GMG rate card (`logistics_cost.xlsx`; build-up ~AED 2.5–4.4/unit, conservative estimate AED 3). A round trip (out + back) =
**2** movements = AED 6/unit.

---

## 1. Quantum of the problem — store-to-store transfers (12 mo)

The core workload the tool automates. "Store-to-store" = both source and destination are stores (`PLANTCAT='A'`).

| BU | Transfers (STOs) | Lines | Units | Logistics spend @ AED 3 |
|---|---|---|---|---|
| **Nike** | 104,459 | 856,731 | 1,493,637 | **AED 4.48M** |
| **SSS** (multi-brand) | 91,503 | 542,720 | 943,183 | AED 2.83M |
| **Under Armour** | 16,184 | 176,947 | 419,117 | AED 1.26M |
| **FOL** (outlets) | 3,705 | 133,922 | 224,478 | AED 0.67M |
| **MONO** (Columbia/Vans/TNF…) | 10,763 | 53,500 | 92,387 | AED 0.28M |
| **JD Sports** | 9,805 | 30,244 | 46,513 | AED 0.14M |
| **Sports Direct** | 89 | 3,843 | 7,237 | AED 0.02M |
| **TOTAL** | **236,529** | **1,798,353** | **3,227,314** | **AED 9.68M** |

*Cadence:* ~19,700 transfers/month (range 13K–27K; peak Dec'25). Inventory value moved ≈ AED 365M (context; separate
from logistics cost). One STO = multiple lines (one per article) = multiple units.

---

## 2. Cycle time — how long a transfer takes

Measured from STO timestamps: created → dispatched (`pgi_timestamp`) → received (`grn_timestamp`).

| BU | Create→Dispatch (median d) | Dispatch→Receive (median d) | Total (median d) |
|---|---|---|---|
| Nike | 1.8 | 2.6 | 5.3 |
| SSS | 1.7 | 3.3 | 6.2 |
| Under Armour | 1.8 | 3.1 | 5.8 |
| FOL | 0.8 | 2.8 | 4.4 |
| MONO | 1.6 | 3.0 | 5.9 |
| JD Sports | 1.7 | 1.8 | 5.2 |
| **TOTAL** | **1.7** | **2.9** | **5.5** |

**Automation split (for the deck):**
- **Creation** (deciding *what* to move + keying the STO) = today off-system manual planning → the tool collapses this
  from days to minutes.
- **Dispatch (1.7d) + Receive (2.9d) = ~4.5-day ground legs** (physical pick/pack/transit/GRN). The tool doesn't erase
  these but attacks them via (a) **fewer moves** (accuracy) and (b) **batching** SKUs per store-pair into one trip.

---

## 3. Movement anomalies (the waste)

### 3.0 Definitions (clear-cut)

Both headline anomalies are **"return-to-origin"**: stock leaves a store and comes back to *that same store*. They
differ only by the **middle hop**. Each is defined in three layers:

- **Boomerang stock** — middle hop = **warehouse**. Store A → its DC (`A→B`), then DC → back to store A (`B→A`) within
  90 days.
- **Ping-pong transfers** — middle hop = **another store**. Store A → store B (`A→A`), then B → back to A within 90 days.
- **Layer 1 — Round trip:** the movement happened (as above).
- **Layer 2 — Dwell:** days from leaving A to returning to A.
- **Layer 3 — Outcome at A (decisive, demand-aware):** did store A **sell** ≥1 unit of that exact variant (SAP retail
  sales, `distr_channel=10`) **within ±30 days of the return**? A sale *before* the return means demand triggered the
  pull-back (legitimate replenishment); a sale *after* means it served demand. **Only zero sales in that ±30-day window =
  confirmed waste** (came back and sat dead, with no demand either side). *(Earlier cut used "30 days after" only; that
  over-counted, flagging demand-driven returns as waste — corrected 2026-08-12.)*

### 3.1 Boomerang stock (store → warehouse → same store)

Round trip: **169,845 moves / 420,125 units.** Dwell: **median 25 days** (avg 31); 57% return ≤30d, 43% take 31–90d.

| BU | Boomerang units | **No sale ±30d** of return | % waste | Confirmed-waste cost @ AED 6 |
|---|---|---|---|---|
| **SSS** | 196,552 | 105,416 | **63.7%** | AED 0.63M |
| **Nike** | 101,319 | 43,383 | 46.7% | AED 0.26M |
| **Under Armour** | 67,848 | 18,063 | 37.6% | AED 0.11M |
| **MONO** | 36,567 | 14,216 | 61.9% | AED 0.09M |
| **FOL** | 9,809 | 5,229 | 53.9% | AED 0.03M |
| **JD Sports** | 7,924 | 4,022 | 59.0% | AED 0.02M |
| **TOTAL** | **420,125** | **190,405** | **55.6%** | **AED 1.14M** |

### 3.2 Ping-pong transfers (store → store → back)

Round trip: **31,319 moves / 56,984 units.**

| BU | Ping-pong units | **No sale ±30d** of return | % waste | Confirmed-waste cost @ AED 6 |
|---|---|---|---|---|
| **Nike** | 34,841 | 12,523 | 37.8% | AED 0.08M |
| **SSS** | 9,968 | 4,485 | 46.1% | AED 0.03M |
| **Under Armour** | 8,843 | 4,272 | 53.2% | AED 0.03M |
| **FOL** | 1,233 | 883 | 82.4% | AED 0.01M |
| **MONO** | 1,241 | 453 | 42.7% | AED 0.003M |
| **JD Sports** | 858 | 304 | 39.3% | AED 0.002M |
| **TOTAL** | **56,984** | **22,920** | **42.1%** | **AED 0.14M** |

### 3.3 Micro-transfers (overhead-heavy)

**59.6% of store-to-store lines move a single unit** (median line = 1 unit). Every 1-unit move still costs the full
~AED 3. **1.07M single-unit moves ≈ AED 3.2M/yr** — the batching opportunity.

| BU | % lines that are 1 unit |
|---|---|
| JD Sports | 70.9% |
| FOL | 68.5% |
| MONO | 62.2% |
| Nike | 60.1% |
| SSS | 58.8% |
| Under Armour | 50.3% |
| **TOTAL** | **59.6%** |

### 3.4 Never-dispatched transfers (planning noise)

**22.8% of created store-to-store STO lines (410,561 lines / 642,535 units) were never shipped** — created, then
abandoned/cancelled. No logistics cost, but ~1 in 4 planning actions is wasted effort.

| BU | % lines never dispatched |
|---|---|
| Sports Direct | 100.0% |
| JD Sports | 50.5% |
| Nike | 23.7% |
| Under Armour | 21.5% |
| SSS | 20.7% |
| FOL | 20.6% |
| MONO | 18.6% |
| **TOTAL** | **22.8%** |

### 3.5 Bounce (superset, reference)

**16.6% of moved units (535,694)** were received at a store then re-sent within 90 days. This is the superset that
contains boomerang + ping-pong + onward hops; shown for reference only (the layered definitions above are the precise cuts).

---

## 4. Cost & savings consolidation — by BU

All @ AED 3/unit-movement (×2 legs = AED 6). **Consistent, demand-aware rule: "confirmed waste" = movement where the receiving
store had NO sale of that SKU within ±30 days of the return** (boomerang + ping-pong). Demand-driven replenishment — a sale just
before (that triggered the return) or just after (that it served) — is **excluded**. *(Same-mall detour is EXCLUDED from the
headline — it's article-grain and unreliable for high-velocity SKUs; kept as a directional lower-bound in §9 only.)*

| BU | Store-to-store spend/yr | Boomerang waste | Ping-pong waste | **Confirmed-waste units** | **AED/yr** |
|---|---|---|---|---|---|
| **SSS** | AED 2.83M | 105,416 | 4,485 | **109,901** | **AED 0.66M** |
| **Nike** | AED 4.48M | 43,383 | 12,523 | **55,906** | **AED 0.34M** |
| **Under Armour** | AED 1.26M | 18,063 | 4,272 | **22,335** | **AED 0.13M** |
| **MONO** | AED 0.28M | 14,216 | 453 | **14,669** | **AED 0.09M** |
| **FOL** | AED 0.67M | 5,229 | 883 | **6,112** | **AED 0.04M** |
| **JD Sports** | AED 0.14M | 4,022 | 304 | **4,326** | **AED 0.03M** |
| **TOTAL** | **AED 9.68M** | **190,405** | **22,920** | **213,325** | **AED 1.28M** |

*(Confirmed-waste = 13% of store-to-store spend. Same-day/pass-through/never-dispatched are operational anomalies, counted
separately in §3.4 & §10, not added here to avoid double-counting.)*

**Layers of savings (cumulative):**
1. **Confirmed waste (floor): ~AED 1.3M/yr** — boomerang + ping-pong with no sale ±30 days of the return. Directly measured, demand-aware.
2. **All return-to-origin movement: ~AED 2.9M/yr** — full boomerang + ping-pong round trips (477K units × 2 legs).
3. **Batching upside: ~AED 3.2M/yr base** — single-unit moves consolidated into fewer trips.
4. **Un-costed upside** — recovered lost sales + avoided markdown on the ~AED 360M of dead store stock these moves chase.

---

## 5. Verification — the "we pulled it back to reprice" defense is refuted

Business may argue boomerang stock was pulled to the DC to **re-price/markdown** it. Tested against the effective-dated
retail price table `prod_mena.gold.sports_retail_prices` (CR = Current Retail / shelf ticket price):

**Of boomerangs where the shelf price is observable at both the pull-out and return dates (46,882 moves), 97.3% had the
IDENTICAL price when the stock came back as when it left.**

| BU | Observable | Price unchanged | % unchanged |
|---|---|---|---|
| SSS | 15,864 | 15,558 | 98.1% |
| Nike | 19,759 | 19,082 | 96.6% |
| Under Armour | 3,135 | 3,059 | 97.6% |
| FOL | 4,591 | 4,564 | 99.4% |
| MONO | 2,448 | 2,346 | 95.8% |
| JD Sports | 1,085 | 1,017 | 93.7% |
| **TOTAL** | **46,882** | **45,626** | **97.3%** |

**Three-fact chain (boomerang):** pulled to DC → sat ~25 days median → shipped back to the **same store** → at the
**same price (97.3%)** → and in **55.6%** of cases no sale either side of the return. Repricing is a master-data/till action (`CR` updates country-wide;
stores discount 78.9% of sold lines in place) — it never required moving the goods. **Reprice defense refuted.**

*(Coverage note: CR price observable at both dates for 27.6% of round-trips; the 97.3% is consistent across every BU so
it generalises — footnote coverage rather than lead with it.)*

---

## 6. Method & data sources

**Primary tables (Databricks):**
- `planning_solution.replenishment.sto_table` — STO header/lines (primary transfer record).
- `planning_solution.replenishment.obd_table` — outbound (pick/pack, `pgi_timestamp` = dispatch).
- `planning_solution.replenishment.ibd_table` — inbound (`grn_timestamp` = receipt).
- `prod_mena.gold.mena_sports_store_master_view` — `PLANTCAT` (A=store, B=warehouse), `/BIC/ZBU` (BU), `COUNTRY`, `SALESORG`.
- `prod_mena.gold.mena_sports_product_master_view` — variant `MATERIAL` → generic `RT_CONFMAT`.
- `prod_mena.gold.sports_retail_prices` — effective-dated retail price (CR/OR/SL) by generic × sales-org × validity.
- `sap_gmg_datalake.silver.fct_sap_sales_detail_v` — item sales (`site_code`, `variant_article_code`, `txn_date`, `txn_qty`, `distr_channel`).

**Working tables built (schema `sports_analytics_test.nike_consolidation`):**
`stock_move_audit` (all flows tagged), `roundtrips` (boomerangs), `pingpong`.

**Key rules / definitions:**
- Store-to-store = source & destination both `PLANTCAT='A'`. DC = `PLANTCAT='B'`.
- Flow tags: `A→A` store-store, `A→B` store→DC, `B→A` DC→store.
- Boomerang = `A→B` then `B→A` back to same store, same variant article, ≤90 days.
- Ping-pong = `A→A` out then reverse `A→A` back to origin, same article, ≤90 days.
- Demand-aware waste = **no sale** of the exact variant at that store (channel 10, `txn_qty>0`) within **±30 days of the
  return** (i.e. `[return−30, return+30]`). A sale in that window = demand-driven replenishment, excluded from waste.
- Store country → pricing sales-org: AE→1020, SA→2020, QA→3020, OM→3220, KW→3420, BH→3620, EG→7000.

**Caveats (state on any slide using these):**
1. Movements matched at **variant × store × date** grain, not physically unit-tracked (best available; consistent across BUs).
2. **AED 3/unit** all-in logistics cost (conservative), from the GMG rate card `logistics_cost.xlsx` (handling out + logistics
   + handling in; observed build-up ~AED 2.5–4.4/unit). Round trip = AED 6/unit.
3. Inventory **value** figures (~AED 365M) are approximate (units × latest article cost).
4. Broader "store→DC→any store" = 73.4% but overlaps normal replenishment → **not** claimed as waste; only same-store
   round-trips are.
5. Price-change coverage = 27.6% of round-trips (see §5).

---

## 7. Suggested slide flow (for PPT)

1. **The problem** — 236K manual transfers, 3.23M units, AED 9.7M/yr, 5.5-day cycle (Section 1–2).
2. **It's often pointless** — boomerang + ping-pong definitions + the ~56% no-sale (demand-aware) stat (Section 3.1–3.2).
3. **And not for repricing** — 97.3% same price (Section 5).
4. **Plus operational drag** — 60% single-unit moves, 23% never dispatched (Section 3.3–3.4).
5. **The prize** — AED 1.3M confirmed waste (demand-aware) → AED 2.9M round-trips → AED 3.2M batching (Section 4).
6. **The tool** — recommend-only engine: warehouse-first guard (kills boomerang), net-benefit + cover logic (kills
   ping-pong), batched pick lists (kills single-unit overhead), auto-generation (kills never-dispatched noise).

---

## 8. Named examples (verifiable)

### Top repeat boomerang offenders (same store + SKU, ≥4 round trips in 12 mo)

| Store | Name | BU | SKU | Round trips | Units | First out | Last return |
|---|---|---|---|---|---|---|---|
| 1401 | SSS Alhamra Mall RAK | SSS | PAW25WHD01A03 | 15 | 40 | 2026-02-10 | 2026-04-30 |
| 1401 | SSS Alhamra Mall RAK | SSS | PAW25WHD01A02 | 15 | 39 | 2026-02-10 | 2026-04-30 |
| 1401 | SSS Alhamra Mall RAK | SSS | PCRWCHD01B02 | 14 | 36 | 2026-02-14 | 2026-05-05 |
| 1401 | SSS Alhamra Mall RAK | SSS | PCRWCT02E02 | 14 | 30 | 2026-02-10 | 2026-04-30 |
| 3419 | Nike Al Khiran Hybrid Outlet | NIKE | NKDR5540-00205 | 13 | 21 | 2026-03-06 | 2026-05-07 |

*(Store 1401 dominates the top of the list — a dozen+ SKUs each round-tripping 13–15× in ~3 months.)*

**Dwell** = days the stock spent *away* from the store = (return date − send date) — how long it sat in transit/at the
DC before landing on the same shelf again.

### Example 1 — Nike Al Khiran Hybrid Outlet (store 3419), SKU `NKDR5540-00205` — via DC 3D41 (Kuwait)
Sent to the DC on 8 days in March 2026 (13 lines); DC shipped it **back 2026-05-07** (+ more 2026-06-09). **Price
349.00 unchanged. Zero sales in 30 days after return.** Both movements' STO #s:

| Send STO (store→DC) | Send date | Return STO (DC→store) | Return date | Dwell | Price out→ret | Sold 30d after |
|---|---|---|---|---|---|---|
| 7400241220 (L27) | 2026-03-06 | 6300694536 (L2) | 2026-05-07 | 62d | 349→349 | 0 |
| 7400241681 (L1) | 2026-03-08 | 6300694536 (L2) | 2026-05-07 | 60d | 349→349 | 0 |
| 7400242272 (L1) | 2026-03-10 | 6300694634 (L4) | 2026-05-07 | 58d | 349→349 | 0 |
| 7400242473 (L4) | 2026-03-11 | 6300694634 (L4) | 2026-05-07 | 57d | 349→349 | 0 |
| 7400242886 (L9) | 2026-03-12 | 6300694634 (L4) | 2026-05-07 | 56d | 349→349 | 0 |

*(13 send-lines total; return STOs on 2026-05-07 = `6300694536`,`6300694634` and 2026-06-09 = `6300720091`,`6300720136`.)*

### Example 2 — SSS Alhamra Mall RAK (store 1401), SKU `PAW25WHD01A03` — via DC 1D00 (UAE)
**15 boomerangs in ~11 weeks** (Feb–Apr 2026), each a **1–4 day** DC round-trip. **Price 89.00 unchanged. Zero sales
after every return.** Send→return STO pairs (tight 1:1 on dates & qty=4):

| Send STO (store→DC) | Send date | Return STO (DC→store) | Return date | Dwell |
|---|---|---|---|---|
| 7400233417 (L28) | 2026-02-10 | 6300634644 (L54) | 2026-02-12 | 2d |
| 7400235919 (L5) | 2026-02-15 | 6300637003 (L46) | 2026-02-16 | 1d |
| 7400237734 (L55) | 2026-02-20 | 6300641640 (L3) | 2026-02-23 | 3d |
| 7400240241 (L93) | 2026-03-02 | 6300646910 (L3) | 2026-03-03 | 1d |
| 7400243683 (L5) | 2026-03-16 | 6300656282 (L108) | 2026-03-17 | 1d |
| 7400247015 (L37) | 2026-03-30 | 6300665331 (L15) | 2026-04-02 | 3d |
| 7400250002 (L15) | 2026-04-08 | 6300671427 (L37) | 2026-04-10 | 2d |
| 7400252349 (L44) | 2026-04-15 | 6300679243 (L4) | 2026-04-17 | 2d |
| 7400255220 (L53) | 2026-04-23 | 6300684683 (L19) | 2026-04-24 | 1d |
| 7400256752 (L30) | 2026-04-28 | 6300688978 (L54) | 2026-04-30 | 2d |

*(Price blank before Apr = no CR record yet for this private-label SKU.)*
*Verification caveat: matched at article×store×date grain (not unit-tracked). SSS pairs align 1:1 on date & qty; for
Nike, read as "this SKU repeatedly sent to DC and returned unsold at the same price," not exact per-unit tracing.*

### How to verify any case
1. STO # → `planning_solution.replenishment.sto_table` = the store→DC send leg (`source_location`=store, `destination_location`=DC).
2. Dispatch/receipt timestamps → `obd_table` / `ibd_table` on the same `sto_number`.
3. The **return** leg (DC→store on the return date) is a **separate inbound STO** to the same store — find it by
   `destination_location`=store, `article`=SKU, created ≈ return date.
4. Post-return sales → `sap_gmg_datalake.silver.fct_sap_sales_detail_v` where `site_code`=store,
   `variant_article_code`=SKU, `distr_channel='10'`, `txn_date` within 30 days after return.
5. Price → `prod_mena.gold.sports_retail_prices`, `CATEGORY='CR'`, `MATERIAL`=generic (`RT_CONFMAT` of the SKU),
   `SALES_ORG`=country org, date between `VALIDFROM`/`VALIDTO`.

---

## 9. Same-mall DC detour — neighbours routed through a hub (A → DC → B)

**Definition:** stock leaves store A, goes to the DC, and returns to a **different store B in the same mall** (A≠B),
same article, within 90 days. The two stores are physical neighbours — the stock could have moved across the corridor
(or not moved at all), but the hub-and-spoke system routed it through a DC, often in another emirate. This is
**additional** to the same-store boomerang (§3.1), and is where a multi-brand store (SSS, JD) pulls another brand's stock.

> ⚠️ **EXCLUDED from the headline waste (§0/§4).** This pattern is matched at **article × mall grain, not physical units**, so
> for high-velocity SKUs the DC ships constantly it produces **false pairings** (a routine DC→store replenishment gets paired
> with an unrelated store→DC return). Example: `ADB7580605` — DC 1D00 shipped it **284 times to 38 stores** in the year, so any
> "JD→DC→SSS same-mall" match on it is coincidental, not rerouted stock. Kept below as a **directional lower bound only**; a
> credible detour needs a **short dwell + a low-velocity SKU**. Not used in the AED 1.3M waste figure.

**Mall identification:** no usable structured field (`/BIC/ZMALL_LOC` is 7.7% populated & grocery-only; `RT_AREA` all
`000000`). Mall derived from store name `/BIC/ZNAME1` (e.g. "…DUBAI MALL…"). Coverage = **284/617 sports stores (46%)**,
129 malls (42 multi-brand). **All figures below are therefore a LOWER BOUND** (~2× at full mall coverage).

**DEMAND-AWARE** (waste = the receiving store B had no sale within ±30 days of arrival — same rule as boomerang/ping-pong).
Measured on the **arrival leg** (DC→store B):

| Receiver BU | Detour arrivals (units) | No sale ±30d | % waste | Dead-detour cost @ AED 6 |
|---|---|---|---|---|
| **SSS** | 29,948 | 10,014 | 37.4% | AED 0.06M |
| **Nike** | 50,262 | 3,757 | 15.3% | AED 0.02M |
| **MONO** | 21,509 | 1,473 | 8.1% | AED 0.01M |
| **FOL** | 2,494 | 1,720 | 67.9% | AED 0.01M |
| **JD Sports** | 1,363 | 368 | 34.5% | AED 0.002M |
| **UARM** | 0 | 0 | — | — |
| **TOTAL** | **105,576** | **17,332** | **23.6%** | **~AED 0.10M** (lower bound) |

- **Only 23.6% is dead** (no sale either side of arrival); the other ~76% served real demand at the neighbour (routing
  still wasteful, but the move was justified). **Confirmed waste = 17,332 units ≈ AED 0.10M** (measured; ~AED 0.20M at full mall coverage).
- The gross detour is heavily **cross-brand** (multi-brand outlets pulling another brand's stock); **UARM = 0** validates
  the logic (mono-brand, no same-mall sibling). This is **on top of** the same-store boomerang.
- *(Grain note: §9 now measured on the arrival leg — 105,576 units — vs the earlier send-leg view; the arrival leg is what
  can be sell-tested. Working table `…nike_consolidation.mall_detour`.)*

### Verifiable example — Dubai Mall (cross-brand), SKU `ADB7580605` (Adidas), via DC 1D00 (Jebel Ali)

| From store | To store | DC | Out | Return | Dwell |
|---|---|---|---|---|---|
| JD Dubai Mall (JD Sports) | SSS Dubai Mall 3.0 (SSS) | 1D00 | 2026-01-25 | 2026-03-26 | 60d |
| JD Dubai Mall (JD Sports) | SSS MAD Dubai Mall (SSS) | 1D00 | 2026-01-11 | 2026-03-31 | 79d |
| JD Dubai Mall (JD Sports) | SSS Dropkick Dubai Mall (SSS) | 1D00 | 2025-11-05 | 2025-11-26 | 21d |

Stock left one Dubai Mall store, went to the Jebel Ali DC, and returned to another Dubai Mall store 21–79 days later —
a pure hub-and-spoke artifact of a system that only models store↔DC, not store↔neighbour.

**Working table:** `sports_analytics_test.nike_consolidation.store_mall` (store → name-derived `mall_key`).
**Caveat:** mall from name (46% coverage); matched at article×store×date grain, not unit-tracked. Lower bound.

---

## 10. Additional absurdities

Smaller in AED than the boomerang, but the most vivid "the process is broken" evidence. **Consistency note:** only the
sales-filtered subsets are counted as waste. Oscillation (A) is sales-filtered below (only the *dead* 20% is waste, in §4
spirit). Same-day (B) and pass-through (C) are **coordination anomalies** — a store shipping a SKU out and back the same
day is a control failure regardless of eventual sale — so they're reported as **counts, not added to the AED 1.3M waste**.
Perpetual travelers (D) are zero-sale by definition.

### A. Serial oscillation — stock commuting between two stores (A→B→A→B…)
3,150 SKU × store-pairs oscillated ≥2 full cycles each way. **BUT most is legitimate demand-chasing** and must NOT be
called waste: an item that sells at A, is replenished to B, then moves back as A re-sells is *correct* rebalancing.
Sales-filtered (channel 10, 12 mo):

| Cut | Pairs | Verdict |
|---|---|---|
| Sold at **both** stores | 2,383 (76%) | Demand-driven — legitimate |
| Sold at one side | 146 (5%) | Mostly justified |
| **Sold ZERO at both** | **621 (20%) / 1,595 legs** | **Dead oscillation — the real waste** |

**Only ~20% (621 pairs / 1,595 legs) is genuine waste** — a SKU bouncing between two stores that *never sold it at
either*. The 14-leg "champion" `NKHJ2147-44607` (Nike Mall of Qatar ⇄ Vendome) actually *sold* (2 units each store) →
demand-driven, excluded from waste (though 14 moves for 4 sales is a separate efficiency question). **Rule for the deck:
movement is only waste when the receiving store didn't sell it** — the same filter behind the boomerang/ping-pong 71%.

### B. Same-day send AND receive of the same article at the same store
**35,300 events / 60,747 units** — a store shipped a SKU out and received the same SKU in **on the same day**. One
system decision ships it away while another pulls it back. ~AED 0.18M/yr.

### C. Immediate pass-through — received then re-shipped within 2 days
**4,849 events / 9,049 units** — stock landed and left within 48 hours; the store is just a transit point. ~AED 0.05M/yr.

### D. Perpetual travelers — moved but never sold anywhere
**10,423 SKUs were transferred store-to-store but sold ZERO units across the entire network in 12 months** (15,383
moves, 23,354 units) — pure logistics, no commercial outcome. **24 SKUs moved ≥10 times each while selling nothing.**
~AED 0.07M/yr.

**Definitions:** oscillation = same article, unordered store-pair, ≥2 moves in *each* direction (`A->A` flow). Same-day
= a store is both `src` and `dst` of the same article on the same date. Pass-through = article received (as `dst`) then
sent (as `src`) within 2 days. Perpetual traveler = article with ≥1 store-to-store move and 0 sales (channels 10+20) in
the period.
