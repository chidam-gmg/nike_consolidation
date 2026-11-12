"""Shallow-clone every managed table of nike_consolidation into nike_consolidation_dev, then set the dev store list.

The dev app (APP_MODE=dev, NIKE_SCHEMA=nike_consolidation_dev) and dev engine runs read/write only the dev schema,
so prod (scheduled job 553755400746008, live app) is never touched. Usage: python clone_dev_schema.py [--stores baseline]
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from databricks.sdk import WorkspaceClient

WH, SRC, DST = "1c9706d9594f1f2f", "sports_analytics_test.nike_consolidation", "sports_analytics_test.nike_consolidation_dev"
w = WorkspaceClient(profile="gmg-sso")


def sql(q):
    r = w.statement_execution.execute_statement(warehouse_id=WH, statement=q, wait_timeout="50s")
    if r.status.state.value != "SUCCEEDED":
        raise RuntimeError(f"{q[:80]}: {r.status.error.message if r.status.error else r.status.state}")
    return r.result.data_array if r.result else []


sql(f"CREATE SCHEMA IF NOT EXISTS {DST}")
tables = [t[0] for t in sql("SELECT table_name FROM sports_analytics_test.information_schema.tables "
                            "WHERE table_schema = 'nike_consolidation' AND table_type = 'MANAGED'")]


def clone(t):
    try:
        sql(f"CREATE OR REPLACE TABLE {DST}.{t} SHALLOW CLONE {SRC}.{t}")
    except Exception as e:      # row-level-security tables cannot be cloned (rls_demo)
        print("skip", t, str(e)[:100])


with ThreadPoolExecutor(8) as ex:
    list(ex.map(clone, tables))
if "--stores" in sys.argv and sys.argv[sys.argv.index("--stores") + 1] == "baseline":
    sql(f"CREATE OR REPLACE TABLE {DST}.cfg_store_grade AS SELECT * FROM {DST}.cfg_store_grade_baseline")   # all 71 stores
print(f"cloned {len(tables)} tables into {DST}")
