"""Run the 13-step engine once against nike_consolidation_dev (one-time submit; same DAG as the prod job).

Copies of the step notebooks with SCHEMA = "nike_consolidation_dev" live in NB_DEV (import them first, e.g.
sed 's/SCHEMA = "nike_consolidation"/SCHEMA = "nike_consolidation_dev"/'). Prints the job run id and URL.
"""
import uuid
from datetime import datetime, timezone
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs

NB_DEV = "/Users/chidambaram.s@gmg.com/nike-consolidation-pipeline-dev"
w = WorkspaceClient(profile="gmg-sso")
src = w.jobs.get(553755400746008).settings
rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
params = {"COUNTRY": "ALL", "CATEGORY": "ALL", "RUN_ID": rid, "TRIGGERED_BY": "dev-71-stores", "AS_OF_DATE": "",
          "ATP_AS_OF": "", "ROS_WINDOW_DAYS": "14", "IN_TRANSIT_LOOKBACK_DAYS": "14", "SLOW_MOVING_THRESHOLD_DAYS": "30",
          "NEW_TO_STORE_DAYS": "14", "PROTECT_COVER_DAYS": "7", "TARGET_COVER_DAYS": "14"}
tasks = [jobs.SubmitTask(task_key=t.task_key,
                         notebook_task=jobs.NotebookTask(notebook_path=f"{NB_DEV}/{t.notebook_task.notebook_path.rsplit('/', 1)[1]}",
                                                         base_parameters=params if t.task_key == "config" else None),
                         depends_on=[jobs.TaskDependency(task_key=d.task_key) for d in (t.depends_on or [])] or None)
         for t in src.tasks]
run = w.jobs.submit(run_name=f"nike_consolidation_DEV {rid}", tasks=tasks)
print(run.run_id, rid, w.jobs.get_run(run.run_id).run_page_url)
