"""Generate the Databricks notebook 21_new_launch_forecast.py from nl_sql + nl_engine + _driver."""
import pathlib
here = pathlib.Path(__file__).parent
eng = (here / "nl_engine.py").read_text().replace("from __future__ import annotations\n", "")
nb = ("# Databricks notebook source\n# MAGIC %md # New-article (launch) forecast — target season\n\n"
      "# COMMAND ----------\nfrom __future__ import annotations\n\n# COMMAND ----------\n" + (here / "nl_sql.py").read_text() +
      "\n# COMMAND ----------\n" + eng + "\n" + (here / "_driver.py").read_text())
(here / "21_new_launch_forecast.py").write_text(nb)
print("written", len(nb.splitlines()), "lines")
