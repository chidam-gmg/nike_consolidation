# Dev schema for the dev app

Prod and the dev app used to share `sports_analytics_test.nike_consolidation`. The dev app now reads its own copy,
`nike_consolidation_dev`, so store-list and scope experiments never reach the scheduled prod run.

| Step | How |
|---|---|
| Copy prod tables | `python clone_dev_schema.py --stores baseline` (shallow clones; store list = all 71 baseline stores) |
| Engine notebooks | copies in `/Users/chidambaram.s@gmg.com/nike-consolidation-pipeline-dev` with `SCHEMA = "nike_consolidation_dev"` |
| Run the engine | `python run_dev_engine.py` (one-time submit of the prod DAG) |
| Dev-only config | `cfg_replen_params.CATEGORY_SCOPE = FOOTWEAR,APPAREL,ACCESSORIES,EQUIPMENT` (prod: FOOTWEAR,APPAREL) |
| Deploy the dev app | copy `build/app` to the dev workspace folder, replace `app.yaml` with `app.dev.yaml`, `databricks apps deploy` |

Workbench tables (`wb_decisions`, `wb_sap_sends`) exist in both schemas; the dev app writes only the dev ones.
