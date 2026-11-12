"""Generate the Databricks notebooks 21_new_launch_forecast.py (nl_sql + nl_engine + _driver) and
22_new_launch_opening_pack.py (nl_sql + nl_pack + _pack_driver)."""
import pathlib
here = pathlib.Path(__file__).parent
eng = (here / "nl_engine.py").read_text().replace("from __future__ import annotations\n", "")
nb = ("# Databricks notebook source\n# MAGIC %md # New-article (launch) forecast — target season\n\n"
      "# COMMAND ----------\nfrom __future__ import annotations\n\n# COMMAND ----------\n" + (here / "nl_sql.py").read_text() +
      "\n# COMMAND ----------\n" + eng + "\n" + (here / "_driver.py").read_text())
(here / "21_new_launch_forecast.py").write_text(nb)
print("written", len(nb.splitlines()), "lines")

pk = (here / "nl_pack.py").read_text().replace("from __future__ import annotations\n", "")
nb2 = ("# Databricks notebook source\n# MAGIC %md # New-article opening pack — store x size, within the buy\n\n"
       "# COMMAND ----------\nfrom __future__ import annotations\n\n# COMMAND ----------\n" + (here / "nl_sql.py").read_text() +
       "\n# COMMAND ----------\n" + pk + "\n" + (here / "_pack_driver.py").read_text())
(here / "22_new_launch_opening_pack.py").write_text(nb2)
print("written", len(nb2.splitlines()), "lines")
