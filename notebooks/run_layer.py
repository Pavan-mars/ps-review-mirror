# Databricks notebook source
# =============================================================================
# run_layer — execute the ordered silver/gold CREATE scripts from the repo
#
# Idempotent runner for sql/silver/*.sql and sql/gold/*.sql (all CREATE OR REPLACE
# / DROP+CREATE, so safe to re-run). Use from a Databricks Git folder (Repos) or
# wire into an Asset Bundle Job (see docs/deployment/Databricks_on_AWS_Deployment_Guide.md).
#
# Widgets:
#   layer      silver | gold        (which folder to build)
#   catalog    mars_dev (default)   (target Unity Catalog)
#   repo_root  absolute path to the repo root in the workspace
#              e.g. /Workspace/Repos/<you>/Chicago-Ventra-Mars-Cubic-Analysis
#
# Notes:
#   - Files run in filename order: 01_… → 18_… for silver, then gold.
#   - 17_incident_history__design.sql is a DESIGN file (skipped) until the
#     ServiceNow tables land — see the validation report.
#   - USE CATALOG warm-up first to avoid intermittent NO_SUCH_CATALOG on the cluster.
# =============================================================================
import os

dbutils.widgets.text("layer", "silver")
dbutils.widgets.text("catalog", "mars_dev")
dbutils.widgets.text("repo_root", "")

layer = dbutils.widgets.get("layer").strip()
catalog = dbutils.widgets.get("catalog").strip()
repo_root = dbutils.widgets.get("repo_root").strip()

assert layer in ("silver", "gold"), f"layer must be silver|gold, got {layer!r}"
if not repo_root:
    # Fallback: resolve relative to this notebook's location when run from a Repo.
    nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
    repo_root = os.path.dirname(os.path.dirname("/Workspace" + nb))  # .../<repo>/notebooks/run_layer -> <repo>
sql_dir = os.path.join(repo_root, "sql", layer)
assert os.path.isdir(sql_dir), f"not a directory: {sql_dir}"

# Warm-up: pin the catalog before any 3-part-name resolution.
spark.sql(f"USE CATALOG {catalog}")

SKIP = {"17_incident_history__design.sql"}  # design-only until ServiceNow tables exist


def statements(sql_text):
    """Yield executable statements, skipping comment-only / blank chunks."""
    for chunk in sql_text.split(";"):
        body = "\n".join(
            ln for ln in chunk.splitlines() if ln.strip() and not ln.strip().startswith("--")
        )
        if body.strip():
            yield chunk.strip()


files = sorted(f for f in os.listdir(sql_dir) if f.endswith(".sql") and f not in SKIP)
print(f"[{layer}] catalog={catalog} dir={sql_dir} -> {len(files)} files")

for fn in files:
    path = os.path.join(sql_dir, fn)
    with open(path) as fh:
        text = fh.read()
    n = 0
    for stmt in statements(text):
        spark.sql(stmt)
        n += 1
    print(f"  built {fn}  ({n} statement(s))")

print(f"[{layer}] done — {len(files)} scripts executed")
