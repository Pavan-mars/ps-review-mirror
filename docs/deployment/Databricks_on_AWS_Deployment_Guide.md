# From GitHub commit → running in Databricks on AWS

How to take the committed `sql/silver/*` and `sql/gold/*` CREATE scripts and execute them in the
Databricks workspace on AWS, reproducibly, with a dev → UAT → prod path. CUBIC MARS guardrails:
AWS account **170202974600**, region **us-east-1**, SSO/OIDC identity (no long-lived keys),
encrypt-by-default, Unity Catalog `mars_dev`.

## The gap to close

The repo holds **ordered `.sql` CREATE scripts**, not executable jobs. Three things are needed:
a **git→workspace** path, a **runner** that executes the SQL in dependency order, and
**orchestration + CI/CD** so it runs on commit. Two routes, used together:

- **Route A — Databricks Git folders (Repos):** fast inner loop for dev iteration.
- **Route B — Databricks Asset Bundles (DAB) + GitHub Actions:** the reproducible, gated path. **Recommended as the target state.**

---

## Route A — Databricks Git folders (dev iteration, set up first)

1. In Databricks: **Settings → Linked accounts → Git integration** → GitHub, authenticate
   (GitHub App or a PAT for the `Pavan-mars`/service account).
2. **Workspace → Repos → Add Repo** → `https://github.com/SathishMars/Chicago-Ventra-Mars-Cubic-Analysis.git`.
   The repo now lives in the workspace and can be pulled per branch.
3. Execute the SQL with a **runner notebook** (the scripts are CREATE OR REPLACE, so they're
   idempotent and safe to re-run). Pattern that respects our workspace gotchas:

   ```python
   # notebooks/run_layer.py  — params: layer = "silver" | "gold"
   import os
   spark.sql("USE CATALOG mars_dev")           # warm-up: avoids intermittent NO_SUCH_CATALOG
   base = f"/Workspace/Repos/<you>/Chicago-Ventra-Mars-Cubic-Analysis/sql/{dbutils.widgets.get('layer')}"
   for fn in sorted(f for f in os.listdir(base) if f.endswith(".sql")):   # S01..S18 / gold
       sql = open(f"{base}/{fn}").read()
       for stmt in [s for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]:
           spark.sql(stmt)                      # DIRECT CREATE OR REPLACE — the reliable path
       print("built", fn)
   ```
   Run `silver` then `gold`. (Filenames sort into dependency order: `01_…`→`18_…`, then gold.)
   Skip `17_incident_history__design.sql` until Robin's ServiceNow tables land — it's a design file.

This gets you building in dev today. Graduate to Route B for repeatability and gating.

---

## Route B — Asset Bundles + GitHub Actions (target state)

### B1. Define the bundle (`databricks.yml` at repo root)

```yaml
bundle:
  name: cubic-mars-chicago

variables:
  catalog: { default: mars_dev }

targets:
  dev:
    default: true
    workspace: { host: https://dbc-c05ec222-36c5.cloud.databricks.com }
    variables: { catalog: mars_dev }
  uat:
    workspace: { host: https://<uat-workspace>.cloud.databricks.com }
    variables: { catalog: mars_uat }
  prod:
    workspace: { host: https://<prod-workspace>.cloud.databricks.com }
    variables: { catalog: mars_prod }

resources:
  jobs:
    medallion_build:
      name: "[${bundle.target}] cubic-mars medallion build"
      tasks:
        - task_key: silver
          notebook_task:
            notebook_path: ./notebooks/run_layer.py
            base_parameters: { layer: silver, catalog: ${var.catalog} }
          new_cluster: &job_cluster
            spark_version: 15.4.x-scala2.12
            node_type_id: m5d.xlarge
            num_workers: 2
            data_security_mode: SINGLE_USER       # UC; runs as the bundle service principal
        - task_key: gold
          depends_on: [{ task_key: silver }]
          notebook_task:
            notebook_path: ./notebooks/run_layer.py
            base_parameters: { layer: gold, catalog: ${var.catalog} }
          new_cluster: *job_cluster
      run_as:
        service_principal_name: ${var.sp_application_id}   # builders run as an SP, not a person
```

`databricks bundle validate` (CI on PRs) and `databricks bundle deploy -t dev` (on merge) then
create/update this Job; `databricks bundle run medallion_build -t dev` executes it.

### B2. Unity Catalog grants for the service principal (one-time)

```sql
GRANT USE CATALOG ON CATALOG mars_dev TO `<sp-app-id>`;
GRANT USE SCHEMA, CREATE TABLE, MODIFY ON SCHEMA mars_dev.silver TO `<sp-app-id>`;
GRANT USE SCHEMA, CREATE TABLE, MODIFY ON SCHEMA mars_dev.gold   TO `<sp-app-id>`;
GRANT SELECT ON SCHEMA mars_dev.bronze TO `<sp-app-id>`;
```

### B3. GitHub Actions workflow (`.github/workflows/databricks.yml`)

Mirrors `git-github-toolkit/references/actions-cicd-aws.md`: PR → validate; main → deploy+run dev;
manual/tag → prod behind an environment approval. **Keyless AWS via OIDC; Databricks via an SP token.**

```yaml
name: databricks
on:
  pull_request: { branches: [main], paths: ['sql/**','notebooks/**','databricks.yml'] }
  push:         { branches: [main], paths: ['sql/**','notebooks/**','databricks.yml'] }
  workflow_dispatch: { inputs: { target: { type: choice, options: [uat, prod] } } }

permissions: { contents: read, id-token: write }   # id-token: write = OIDC→AWS
concurrency: { group: dbx-${{ github.ref }}, cancel-in-progress: true }

jobs:
  validate:
    runs-on: ubuntu-latest
    env: { DATABRICKS_HOST: ${{ vars.DATABRICKS_HOST }}, DATABRICKS_TOKEN: ${{ secrets.DATABRICKS_SP_TOKEN }} }
    steps:
      - uses: actions/checkout@v4
      - uses: databricks/setup-cli@main
      - run: databricks bundle validate -t dev

  deploy-dev:
    if: github.ref == 'refs/heads/main'
    needs: validate
    runs-on: ubuntu-latest
    environment: dev
    env: { DATABRICKS_HOST: ${{ vars.DATABRICKS_HOST }}, DATABRICKS_TOKEN: ${{ secrets.DATABRICKS_SP_TOKEN }} }
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4          # OIDC, no stored keys
        with: { role-to-assume: arn:aws:iam::170202974600:role/github-actions-cubic-mars, aws-region: us-east-1 }
      - uses: databricks/setup-cli@main
      - run: databricks bundle deploy -t dev
      - run: databricks bundle run medallion_build -t dev

  deploy-prod:
    if: github.event_name == 'workflow_dispatch' && inputs.target == 'prod'
    needs: validate
    runs-on: ubuntu-latest
    environment: production        # ← required-reviewers approval gate
    env: { DATABRICKS_HOST: ${{ vars.DATABRICKS_HOST_PROD }}, DATABRICKS_TOKEN: ${{ secrets.DATABRICKS_SP_TOKEN_PROD }} }
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with: { role-to-assume: arn:aws:iam::170202974600:role/github-actions-cubic-mars-prod, aws-region: us-east-1 }
      - uses: databricks/setup-cli@main
      - run: databricks bundle deploy -t prod
```

### B4. Secrets / vars to create in GitHub
- **Variables:** `DATABRICKS_HOST` (dev workspace URL), `DATABRICKS_HOST_PROD`.
- **Secrets:** `DATABRICKS_SP_TOKEN` (dev SP OAuth/token), `DATABRICKS_SP_TOKEN_PROD` — store on the
  matching **Environment** so prod creds aren't visible to dev jobs.
- **AWS:** none stored — add GitHub as an OIDC provider and create the two IAM roles, trust scoped to
  `repo:SathishMars/Chicago-Ventra-Mars-Cubic-Analysis` (dev → `ref:refs/heads/main`, prod → `environment:production`).

### B5. Branch protection
Require the `validate` check + 1 review on `main` (Settings → Rules). Direct pushes blocked; changes land via PR.

---

## Notes & gotchas (from prior runs)

- **Runner reliability:** always `USE CATALOG mars_dev` first; prefer **DIRECT `CREATE OR REPLACE`**
  over a build-then-rename runner (`spark.catalog.tableExists` mis-reports 3-part UC names).
- **Run as a service principal**, not a named user, for all automated builds.
- **Networking:** Databricks control-plane + UC are public APIs, so GitHub-hosted runners reach them
  fine over OIDC — **no self-hosted runner needed for this SQL build.** A self-hosted runner is only
  required for steps that touch the **private Oracle ODS over the VPN** (that's ingestion, not this layer).
- **Post-build:** add the `OPTIMIZE … ZORDER BY (DEVICE_ID, transit_day)` statements (already noted in
  the gold scripts) as a final task.
- **Promotion:** UAT/prod via `-t uat|prod` map to `mars_uat`/`mars_prod` (or schema-per-env). Keep bronze
  ingestion (Oracle→S3) on its own schedule; this pipeline rebuilds silver/gold from bronze.

## Recommended sequence
1. Add `notebooks/run_layer.py` + connect the repo via **Git folders** → build dev manually (Route A).
2. Add `databricks.yml` + the **Actions** workflow; wire the **OIDC role** + **SP token**; PR-validate.
3. Turn on branch protection; merge → auto dev deploy+run.
4. Add UAT/prod targets + the environment approval once dev is green.
