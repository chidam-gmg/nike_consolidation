# Nike Stock Consolidation Engine — Business Overview

**What it does.** Recommends (does **not** auto-execute) store-to-store transfers of Nike stock — moving
dead/slow inventory to stores where it will actually sell, and completing size runs at flagship doors.
Moves are **intra-country only** and limited to the **business's active store list**. Reviewers approve or
adjust in the app before anything ships.

**Every store × size sits in exactly one of three tabs** (nothing is lost or double-counted):
- **Recommended moves** — ship these.
- **Held** — a real opportunity that a guardrail blocked; needs a human call (with a one-click export).
- **Not moved** — nothing to do (no demand, too new, or already selling fine).

---

## The tags, in plain English (with live examples)

### 1. Why the receiving store needs it — *Demand type*
| Tag | Meaning | Live example |
|---|---|---|
| **Out-of-stock demand** | The store **sells** this size but is **stocked out**, and the country warehouse can't refill it — lost sales today. | **Beacon Dubai Mall** sells the *NK Club Half-Zip Jacket (L)* but has zero on hand → engine moves **4 units each from Dubai Hills and Marina Mall** (both holding it dead). |
| **In-stock top-up** | The store has some stock but is below target cover for how fast it sells. | A store down to ~3 days' cover on a fast size is topped up from a slow-moving store. |
| **A+ curve completion** | A flagship (**Beacon / Jordan / A+**) door carries a current-season style but is **missing display sizes** — 1 unit of each missing size. | **Beacon** completes *Air Jordan 1 Low SE (9)* and *Calm Slide 2.0 (12)* with **1 unit each**, pulled from dead stock elsewhere. |

### 2. Why it moved — *Movement note*
| Tag | Meaning |
|---|---|
| **Donor stays full-size** | The giving store still shows a complete size run after giving — safe to move. |
| **Donor cut — run consolidated to display** | The giving store's run was already broken; its remaining sizes are consolidated into **one** receiver so that store can display the full ladder. |
| **Run released to a faster receiver (≥25%)** | Moving would break the giver's run, but a receiver sells **≥25% faster** — worth it. |
| **No size rule (fallback)** | Item has no size-curve definition (e.g., accessories) — moved on demand alone. |

### 3. Why the giving store qualifies — *Reason*
**Dead** = no *gross* sales in the window · **Slow-moving** = cover above the slow threshold.
*(Demand is measured on **gross** units sold — a size that sold and was later returned still counts as demand, so it isn't treated as dead. Revenue figures remain net of returns.)*

### 4. Why something is **Held** (needs a call)
Each Held line shows a **would-be receiver** and an **indicative quantity** (what could actually move).
- **Size-curve break** — moving would break the giver's display run and no receiver sells fast enough.
- **Season-freshness** — the receiving store may only take current-or-newer season stock.
- **APO** — the receiver already has an inbound allocation for that style.

### 5. Why something is **Not moved**
No receiving store needs the size · New to store (too recent) · Retained (selling within cover).

---

## Guardrails the engine respects (constraints)
| Guardrail | Rule |
|---|---|
| **Active store list** | Only stores on the business-maintained list participate — nothing off-list. |
| **Store grade** | Grades come from the business list; **Beacon & Jordan rank above A+** and win ties. |
| **Intra-country** | Recommendations never cross borders. |
| **Warehouse first** | If the country DC still holds the size, leave it to **replenishment** — no store-to-store move. |
| **Protect the seller** | A giving store keeps enough cover to keep selling (protect cover). |
| **Target cover** | A receiver is filled only up to its target days of cover — no over-stocking. |
| **In-transit aware** | Stock already on its way to a receiver counts — no over-shipping. |
| **Size curve** | Preserve full size runs at the giver and complete them at flagships, so stores can display. |
| **Season freshness** | Fresh doors only receive current-or-newer seasons. |
| **APO** | Don't send to a store/style that already has an inbound allocation. |
| **Net benefit** | A move must be worth more than its cost (flagship display completion is exempt — justified by display). |

---

## Scale (latest run)
~**11,000 recommended moves** — of which ~**7,100 recover out-of-stock demand** (previously invisible) and
~**520 complete flagship displays**; ~**2,500 lines are deferred to replenishment** (the DC already has them);
~**1,800 are held** for a business call. Every recommendation is reviewable, filterable by
country / category / demand type, and exportable to Excel/CSV.

*Recommendation only — the engine proposes; the business decides and executes.*
