# Nike Consolidation — Review & Export UI (Streamlit)

Thin front-end over the consolidation engine (notebooks `00`–`06`). It sets run parameters,
triggers the Databricks pipeline, and lets a merchandiser review + export recommended moves.
No business logic lives here — the engine produces `sports_analytics_test.nike_consolidation.recommendations`
and `.run_stats`; this app reads them.

This is a **Streamlit approximation** of the Claude Design mock (`../../ui_handoff/.../Consolidation Engine.dc.html`),
built in the GMG INFORM palette. It is not pixel-perfect React — the slide-over drawer is a modal
dialog, and the dense table is `st.dataframe`.

## Files
- `app.py` — the UI (run panel → summary tiles → filters → results table → detail dialog → export).
- `databricks_io.py` — SQL reads + Jobs trigger via the Databricks SDK; config-CSV uploads.
- `requirements.txt`, `app.yaml` — Databricks Apps deployment.

## Contract (shared with the engine, UI spec §6.3)
Reads `recommendations`: `country, parent_article, article, size, donor_store, donor_store_name,
donor_bu, receiver_store, receiver_store_name, receiver_bu, recommended_qty, reason, donor_stock,
receiver_stock, donor_cover_days, donor_ros, receiver_ros, expected_sales_uplift*, movement_cost*,
net_benefit*` (`*` provisional). Summary tiles read `run_stats`.

## Run locally
```bash
export PATH="$HOME/.local/bin:$PATH"
cd build/app
pip install -r requirements.txt
export DATABRICKS_CONFIG_PROFILE=gmg-sso    # local auth; deployed app uses its service principal
streamlit run app.py
```

## Deploy as a Databricks App
```bash
databricks apps create nike-consolidation -p gmg-sso           # once
databricks sync build/app /Workspace/Users/<you>/nike-consolidation-app -p gmg-sso
databricks apps deploy nike-consolidation \
  --source-code-path /Workspace/Users/<you>/nike-consolidation-app -p gmg-sso
```
Grant the app's service principal: SQL warehouse `CAN_USE`, `SELECT` on the schema tables,
`CAN_MANAGE_RUN` on the pipeline notebooks (or run-job permission), and `MODIFY` on the `cfg_*`
tables if config uploads are used.

## Notes / provisional
- Runs **all countries** in one job (`COUNTRY=ALL`); country is a results filter (matches the design).
- Economics columns/tiles are **provisional** until `MOVEMENT_COST_PER_UNIT` (OPEN-3) and
  `SELL_THROUGH_FACTOR` (OPEN-4) land. With cost = 0, net benefit == uplift.
- "Reshaped" filter stays empty until the size-curve step (05) is activated.
- Nike brand list (`cfg_brand_mapping`, 13 IDs) and category converter (`cfg_category_converter`, 375 rows)
  are **engine-maintained tables** in `sports_analytics_test.nike_consolidation` — not uploads. Size-curve
  and BU-eligibility remain CSV uploads that overwrite their `cfg_*` tables.
- `cfg_category_converter` is loaded/available but not yet **joined** into the pipeline: binding it to
  articles (to derive `business_category`) needs the product-master attribute mapping confirmed — a follow-up,
  and only consumed once the size-curve step (05) activates.
- Push-to-SAP is intentionally disabled (POC).
