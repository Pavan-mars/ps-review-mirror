# Databricks notebook source
# run_layer_gold — execute sql/gold/*.sql in filename order
#
# Run AFTER run_layer_silver + validate_silver when silver audit fixes (2026-07-22)
# have been applied — gold reads rebuilt silver tables (especially S17/S18/S20).
#
# Gold tables (5):
#   device_ps1_daily, device_ps2_chains, device_ps3_incident,
#   device_ps4_hourly, device_ps5_component
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

sql_dir = os.path.join(repo_root, "sql", "gold")
assert os.path.isdir(sql_dir), f"not a directory: {sql_dir}"

spark.sql(f"USE CATALOG {catalog}")

SKIP = {
    "device_ps1_daily__label_compare.sql",
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

print(f"[gold] catalog={catalog}  dir={sql_dir}  -> {len(files)} file(s) to run")
print("[gold] requires fresh silver rebuild (S17 dedup, S25 conformed SN, S20 date filter)")
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
print(f"[gold] done — {len(files)} script(s) executed successfully")
print("[gold] next: validate_gold → export_gold_to_s3")
