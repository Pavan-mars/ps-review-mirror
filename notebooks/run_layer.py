# Databricks notebook source
# =============================================================================
# run_layer — execute the ordered silver/gold CREATE scripts from the repo
#
# Idempotent runner for sql/silver/*.sql and sql/gold/*.sql (all DROP+CREATE,
# so safe to re-run). Use from a Databricks Git folder (Repos) or wire into
# an Asset Bundle Job.
#
# Widgets:
#   layer       silver | gold          which folder to build
#   catalog     mars_dev (default)     target Unity Catalog
#   repo_root   (empty)                absolute path to repo root in workspace;
#                                      auto-detected when running from a Repo
#
# Notes:
#   - Files run in filename order: 01_... -> 23_... for silver, ps1... -> ps5... for gold.
#   - USE CATALOG warm-up runs first to avoid intermittent NO_SUCH_CATALOG errors.
#   - All scripts are DROP+CREATE — safe to re-run from the beginning every time.
# =============================================================================
import os
import re
from typing import Any

# Databricks runtime globals — injected into the notebook namespace before execution.
# globals().get() returns the real object in Databricks and None locally (satisfies Pylance).
dbutils: Any = globals().get("dbutils")
spark: Any   = globals().get("spark")

dbutils.widgets.text("layer",     "silver")
dbutils.widgets.text("catalog",   "mars_dev")
dbutils.widgets.text("repo_root", "")

layer     = dbutils.widgets.get("layer").strip()
catalog   = dbutils.widgets.get("catalog").strip()
repo_root = dbutils.widgets.get("repo_root").strip()

assert layer in ("silver", "gold"), f"layer must be silver|gold, got {layer!r}"

if not repo_root:
    nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
    repo_root = os.path.dirname(os.path.dirname("/Workspace" + nb))

sql_dir = os.path.join(repo_root, "sql", layer)
assert os.path.isdir(sql_dir), f"not a directory: {sql_dir}"

# Warm-up: pin the catalog before any 3-part-name resolution.
spark.sql(f"USE CATALOG {catalog}")

SKIP = {
    "device_ps1_daily__label_compare.sql",  # read-only label analysis, not a table build
    "06b_dim_device_completion.sql",         # diagnostic SELECT only — run interactively after S06 to verify orphan rescue
}


def statements(sql_text):
    """Yield executable SQL statements.

    Strip ALL -- comments (full-line AND inline) before splitting on ;
    so that semicolons inside comments never create spurious fragments.
    Caveat: -- inside a string literal is not handled; our SQL has none.
    """
    clean_lines = []
    for ln in sql_text.splitlines():
        code = re.sub(r'--.*$', '', ln)
        if code.strip():
            clean_lines.append(code)
    clean = "\n".join(clean_lines)
    for chunk in clean.split(";"):
        if chunk.strip():
            yield chunk.strip()


# Build the ordered file list — runs every file in sequence, no skipping
files = sorted(f for f in os.listdir(sql_dir) if f.endswith(".sql") and f not in SKIP)

print(f"[{layer}] catalog={catalog}  dir={sql_dir}  -> {len(files)} file(s) to run")

for fn in files:
    path = os.path.join(sql_dir, fn)
    with open(path) as fh:
        text = fh.read()
    n = 0
    for stmt in statements(text):
        spark.sql(stmt)
        n += 1
    print(f"  built {fn}  ({n} statement(s))")

print(f"[{layer}] done -- {len(files)} script(s) executed")
