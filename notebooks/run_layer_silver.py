# Databricks notebook source
# run_layer_silver — execute sql/silver/*.sql in filename order
#
# Silver audit fixes applied 2026-07-22 (Michael Silver_Tables_Audit_22Jul2026):
#   SIL-C1  17_incident_root_cause     — QUALIFY dedup on availability_event_id
#   SIL-H1c 25_incident_task_ci_link   — servicenow_incident_conformed source
#   SIL-H2  30_read_tap_device_daily   — device-day collapse of read_tap_daily
#   SIL-H3  13_tap_event_daily         — DEVICE_KEY via dim_device
#   SIL-M5  20_usage_lifecycle_daily   — transit_day <= current_date() filter
#
# Prerequisite (run BEFORE this notebook if incident tables are stale):
#   NB101 (run_mode=merge) → bronze.servicenow_incident_conformed
#   NB100 is superseded by NB101 (see NB101's own handover checklist, step 6) —
#   do not run NB100; it appends directly to bronze.servicenow_incident, which
#   NB101's insert-only merge into the conformed table has replaced.
#
# Recommended full rebuild order after SQL changes:
#   0. notebooks/data_quality/data_quality_framework_bronze.py (optional, post-ingest)
#   1. run_layer_silver (this notebook)
#   2. notebooks/data_quality/Silver_dq_framework.py
#   3. notebooks/validation/validate_silver.py (optional)
#   4. run_layer_gold
#   5. notebooks/data_quality/Gold_dq_framework.py
#   6. notebooks/validation/validate_gold.py (optional)
#   7. export_silver_to_s3 → export_gold_to_s3
#
# Widgets: catalog (mars_dev), repo_root (auto-detected)
import os
import re
from typing import Any

dbutils: Any = globals().get("dbutils")
spark: Any   = globals().get("spark")

dbutils.widgets.text("catalog",   "mars_dev")
dbutils.widgets.text("repo_root", "")

catalog   = dbutils.widgets.get("catalog").strip()
repo_root = dbutils.widgets.get("repo_root").strip()

if not repo_root:
    nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
    repo_root = os.path.dirname(os.path.dirname("/Workspace" + nb))

sql_dir = os.path.join(repo_root, "sql", "silver")
assert os.path.isdir(sql_dir), f"not a directory: {sql_dir}"

spark.sql(f"USE CATALOG {catalog}")

SKIP = {
    "06b_dim_device_completion.sql",
}

def statements(sql_text):
    clean_lines = []
    for ln in sql_text.splitlines():
        code = re.sub(r'--.*$', '', ln)
        if code.strip():
            clean_lines.append(code)
    clean = "\n".join(clean_lines)
    for chunk in clean.split(";"):
        if chunk.strip():
            yield chunk.strip()

files = sorted(f for f in os.listdir(sql_dir) if f.endswith(".sql") and f not in SKIP)

print(f"[silver] catalog={catalog}  dir={sql_dir}  -> {len(files)} file(s) to run")
print("[silver] audit fixes 2026-07-22: S17 dedup | S25 conformed SN | S30 read_tap_device_daily | S13 DEVICE_KEY | S20 date filter")
print("-" * 70)

for idx, fn in enumerate(files, 1):
    path = os.path.join(sql_dir, fn)
    with open(path) as fh:
        text = fh.read()
    n = 0
    try:
        for stmt in statements(text):
            spark.sql(stmt)
            n += 1
        print(f"  [{idx:02d}/{len(files):02d}] OK   {fn}  ({n} statement(s))")
    except Exception as e:
        print(f"  [{idx:02d}/{len(files):02d}] FAIL {fn}")
        raise RuntimeError(f"Failed on {fn}:\n{e}") from e

print("-" * 70)
print(f"[silver] done — {len(files)} script(s) executed successfully")
print("[silver] next: data_quality/Silver_dq_framework → validate_silver → run_layer_gold → …")
