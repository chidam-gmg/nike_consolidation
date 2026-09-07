# Consolidation Engine — UI Specification (POC)

**For:** Claude Design
**Product:** Nike Store-to-Store Consolidation — review & export interface
**Primary user:** Merchandiser (reviews recommended moves, exports them)
**Host:** Databricks-embedded app
**Brand:** GMG Brand Book V6 — **INFORM** end of the spectrum (operational tool: clean hierarchy, restrained colour, generous white space)

---

## 0. Read first — how this UI relates to the backend

The **backend engine is being built in parallel by Claude Code** (see `consolidation_poc_spec.md`). This UI does **not** contain business logic. It is a thin front-end that:
1. lets the merchandiser set run parameters,
2. triggers the Databricks job,
3. displays the recommended moves the job returns, and
4. exports them.

**The two builds share one contract: the output schema and the config schema (§6).** Design must build the results table and the parameter panel against those schemas exactly. If a schema field changes, both builds change together — flag rather than improvise.

**Provisional zones (mark clearly in the design, do not finalise):** any cell showing `expected_sales_uplift` or `net_benefit`, and any indicator of a size-curve "reshaped" move. These depend on business decisions still open in the backend spec (`[OPEN-3]` cost, `[OPEN-4]` conversion factor, `net_sales_per_unit` data). Design them as present but visually flagged "provisional — pending business inputs," so the layout is ready but nobody reads placeholder economics as final.

---

## 1. What the user is trying to do

A merchandiser opens this to answer: *"Which slow-moving / dead Nike stock should move from which store to which store, and is it worth doing?"* Then they export the approved set.

The job is **review, not authoring.** The user doesn't create moves; the engine proposes them. The user filters, scrutinises, and exports. So the interface priority is: **fast scanning of many recommended lines, easy filtering, clear reason/economics per line, one-click export.**

---

## 2. Screens

Single-page app, three stacked regions (top to bottom): **Run panel → Summary strip → Results table.** A detail drawer opens from a results row.

### 2.1 Run panel (collapsible, top)
Where the merchandiser sets parameters and triggers a run.

- **Country selector** (required; single-select — moves are intra-country only). No run without a country.
- **Parameter fields** (scalars from §6.1). Show sensible defaults pre-filled. Group as:
  - *Sales & cover:* ROS window (14), slow-moving threshold (30), target cover (14), protect cover (7), in-transit look-back (14), new-to-store days (14).
  - *Economics (provisional):* movement cost per unit, sell-through factor, generic-ROS uplift min — visually grouped and marked **provisional**.
- **Config uploads** (CSV, from §6.2): Nike brand list, category converter, full/cut-size definition, BU-eligibility matrix. Show filename + last-uploaded state; allow replace.
- **Validation, inline, before run is allowed:**
  - `protect cover < target cover` — if violated, block the run and show the error next to the two fields (this mirrors the backend's fail-fast rule; surfacing it here saves a wasted run).
  - Required config present (country + mapping files).
- **Run button** — primary CTA (Cobalt `#2658F2`). Disabled until valid. On click: show run-in-progress state (the Databricks job is asynchronous — show a spinner + "running" status, not a frozen screen). On completion, populate summary + table. On failure, show the job's error message plainly.
- Collapsed by default after a successful run (so results get the screen), with a summary of the params used and a "re-run / edit" affordance.

### 2.2 Summary strip (below run panel)
A row of KPI tiles summarising the returned recommendation set. These are **aggregates of the output rows**, not new calculations:
- Total recommended moves (row count)
- Total units to move
- Number of donor stores / receiver stores involved
- Total net benefit **(provisional — flag)**
- Count blocked/held by constraint, if the job returns that (e.g. APO-blocked, held at Step 8) — useful for the merchandiser to trust the engine isn't over-moving.

Keep tiles calm and INFORM-style: Sapphire text on Light Silver `#E9EFEF` tiles, one Cobalt accent tile for the headline metric. No bold supergraphics.

### 2.3 Results table (main region)
The heart of the tool. One row per recommended move. Columns map **exactly** to the output schema (§6.3):

`Country · Parent article · Article (child) · Size · Donor store · Receiver store · Qty · Reason · Donor cover (days) · Receiver ROS · Expected uplift* · Movement cost* · Net benefit*`
(* = provisional economics, visually distinguished)

Requirements:
- **Dense, scannable rows** — this is an operational table, expect hundreds of lines. Compact row height, monospaced or tabular-figure numerals for the numeric columns so they align.
- **Sort** on any column. Default sort should mirror the engine's deterministic output order (country, parent article, receiver store, donor store) so the UI order matches the job — but let the user re-sort freely.
- **Filter** by: donor store, receiver store, reason (Dead / Slow-moving), article/parent, size. Quick filters as chips.
- **Reason** shown as a small labelled tag — Dead vs Slow-moving in two distinguishable but restrained tones (e.g. Burnt Copper `#B15940` for Dead, a muted Sapphire tint for Slow-moving). Not red/green alarm colours — this is a planning tool, not an alert system.
- **Row → detail drawer** (§2.4).
- **Selection:** row checkboxes + select-all-filtered, feeding export and the (greyed) push action.

### 2.4 Detail drawer (opens from a row)
Full context for one recommended move, for the merchandiser to scrutinise before trusting it:
- Donor side: store, current effective stock, cover days, class (Dead/Slow), ROS, what's left after donating (post-move cover), size-curve status before/after.
- Receiver side: store, current effective stock, cover, ROS, capacity headroom, projected cover after receiving.
- The move: qty, size(s), and — if the engine reshaped it to a full size run (Step 8) — a clear note that this move was **enlarged to maintain/complete a size curve**, with the parent-level ROS comparison that justified it. **(provisional display — flag)**
- Economics breakdown: uplift, cost, net benefit, with the assumptions named (which cost, which sell-through factor). **(provisional — flag)**
- Any constraint notes (e.g. "capped by receiver capacity", "B/C store — dispatch on next scheduled run").

---

## 3. Actions

- **Export** (enabled): export selected / all-filtered rows to CSV. This is the real output path for the POC — the merchandiser exports and the transfer is actioned downstream. Primary secondary-action styling.
- **Push to SAP** (disabled / greyed for now): render the button but **greyed out with a tooltip "Coming soon"**. This is the future approve-and-execute path; it must be visibly present but non-functional in the POC. Do not wire it to anything.
- No create / edit / delete of recommendations — the user cannot author moves, only review and export.

---

## 4. States to design

- **Empty / first load** — no run yet: run panel expanded, table area shows a neutral "Set parameters and run to see recommended moves" prompt.
- **Running** — async job in progress: non-blocking progress indicator; params locked during run.
- **Results** — populated summary + table.
- **No recommendations** — job ran successfully but returned zero moves (valid outcome — everything's healthy or everything's blocked): show a clear, non-error message, not an empty grid.
- **Job error** — surface the Databricks job's error text plainly; offer re-run.
- **Validation error** — inline on the run panel (esp. the `protect < target` rule).

---

## 5. Visual / brand direction (GMG, INFORM end)

- **Palette:** Sapphire Blue `#20274C` (primary text, headings), Cobalt Blue `#2658F2` (primary CTA, single accent), Bright White `#FFFFFF` (canvas), Light Silver `#E9EFEF` (tiles, table zebra, panel backgrounds). Burnt Copper `#B15940` and Soft Peach `#F4CAA6` **only** as sparing data-tag accents. Avoid arbitrary colour.
- **No alarm red/green.** Reason tags and economics use the brand palette, not traffic-light semantics.
- **Typography:** Recoleta for the app title / section headings (sentence case); Salvo Sans for all UI copy and controls; **tabular/monospaced numerals for all numeric table columns** so figures align down the column. System fallback Arial where web fonts unavailable.
- **Density & hierarchy:** This is an operational review tool — favour a refined, restrained layout with clear hierarchy and white space over decorative elements. No supergraphics, no full-bleed hero. The data is the interface.
- **Logo:** GMG blue logo on the white top bar (from brand assets — do not recreate). Clear space maintained.
- **Tone of copy:** direct, plain English, no padding. Button labels are verbs ("Run", "Export"). Error and empty states are helpful and human, not curt.

---

## 6. Shared contract (build both sides against this — do not drift)

### 6.1 Config — scalar parameters (run panel fields)
| Field | Type | Default | Notes |
|---|---|---|---|
| Country | enum, required | — | single-select; intra-country only |
| ROS window (days) | int | 14 | tunable |
| In-transit look-back (days) | int | 14 | |
| Slow-moving threshold (days) | int | 30 | |
| New-to-store (days) | int | 14 | |
| Protect cover (days) | int | 7 | must be `< target cover` |
| Target cover (days) | int | 14 | eligibility + fill-to level |
| Movement cost / unit | number | placeholder | **provisional** |
| Sell-through factor | number | 1.0 | **provisional** |
| Generic-ROS uplift min | number | TBD | **provisional** |

### 6.2 Config — file uploads (CSV)
Nike brand list · category converter · full-size/cut-size definition (brand×category) · BU-eligibility matrix.

### 6.3 Output — results table columns (one row per move)
`country, parent_article, article, size, donor_store, donor_bu, receiver_store, receiver_bu, recommended_qty, reason (DEAD|SLOW_MOVING), donor_cover_days, receiver_ros, expected_sales_uplift*, movement_cost*, net_benefit*`
(* provisional economics)

Column names/types are the interface with Claude Code's job output. **If either side needs to change a name or type, change it in this contract first and notify both builds.**

---

## 7. Explicitly NOT in this UI (POC)
- Push to SAP (greyed only), any write-back or execution.
- Editing recommendations or parameters mid-run.
- Replenishment (out of scope entirely — different, automated system).
- Non-Nike BUs, cross-country moves.
- User accounts / roles / audit — assume Databricks handles auth as the embedding host.

---

## 8. Recommended parallel-build approach (for the team)
- **Build now, low risk:** run panel, config validation, summary strip, results table, detail drawer, export, all states. These are driven by settled schema.
- **Build provisional, mark clearly:** economics columns/tiles and size-curve-reshape display — depend on open business inputs.
- **Freeze §6 as the contract.** Claude Code builds the job to emit exactly §6.3; Design builds the table to consume exactly §6.3. Open items (`[OPEN-3/4]`, `net_sales_per_unit`) only cause rework where they touch that schema — everywhere else, parallel work is safe.
