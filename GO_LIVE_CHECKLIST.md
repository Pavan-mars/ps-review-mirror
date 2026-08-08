# CUBIC MARS Chicago dashboard — package, containerize, ECR, endpoint

05-Aug-2026. Grounded in what is actually in `sathish_repo`, not a generic
runbook. Anything I could not verify is marked **UNVERIFIED** rather than
assumed.

---

## 0. What already exists (so we don't rebuild it)

A previous session already wrote most of the packaging layer. Confirmed present:

| Artefact | Path | State |
|---|---|---|
| Dockerfile (multi-stage node → nginx) | `dashboard/Dockerfile` | written, **never built** |
| nginx config, SPA fallback, `/healthz` | `dashboard/nginx.conf` | written |
| `.dockerignore` | `dashboard/.dockerignore` | correct — excludes `node_modules`, `dist`, `.env` |
| ECS task definition | `infrastructure/app_spoke/ecs-task-def.dashboard.json` | 3 × `REPLACE_ME` |
| Go-live runbook (6 phases) | `infrastructure/app_spoke/deploy_dashboard_ecs.sh` | 5 × `REPLACE_ME` |
| PS5 → Aurora loader | `api/lambda/cubic-mars-ps5-rds-loader/` | written, deploy script ready |

Known-live AWS facts the runbook already carries: account `170202974600`,
`us-east-1`, VPC `vpc-0a7775adc7d382fbb`, three private subnets, ECS cluster
`cubic-mars-ecs-cluster-dev`, API GW `https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com`.

**Scope correction worth stating to the client up front:** only the *SPA* is
being containerized. The API is API Gateway + Lambda and stays that way. ECR
and ECS give us the front end; they do not change how the data is served.

---

## A. Pre-flight — fix before packaging

These are blockers I found in the current tree. Each is small.

- [ ] **A1 — V4 is mounted at `/v4`, not at `/`.**
  `App.jsx` routes `/` → the old landing, `/v2` → V2, `/v4` → V4. If we hand
  Chicago a bare ALB DNS name they land on the wrong build. Decide: redirect
  `/` → `/v4`, or promote V4 to `/` and move the old one to `/legacy`.
  **This is the single highest-risk item — it is invisible until a user opens the URL.**
- [ ] **A2 — Delete `dashboard/src/v3/` and the `_to_delete*` folders.**
  Vite tree-shakes unimported files so they don't bloat the bundle, but they
  are dead weight in the image build context and in review.
- [ ] **A3 — Decide the auth boundary.** The app already has `ProtectedRoute`;
  the runbook adds an `authenticate-cognito` action on the ALB listener. Two
  auth layers that don't know about each other is a support problem. Pick one
  as authoritative. **UNVERIFIED:** I have not read what `ProtectedRoute`
  authenticates against.
- [ ] **A4 — `VITE_API_BASE_URL` is baked at build time**, not read at
  container start. That means **one image per environment** — a dev image
  cannot be promoted to UAT. Accept that, or add a `/config.json` fetched at
  boot so one image serves all three. Recommend deciding now, not after the
  first promotion fails.
- [ ] **A5 — Run `npm run build` locally once and read the output.** The image
  has never been built. A build error surfaces in CloudShell otherwise, which
  is a slow place to debug. Run from `dashboard/`.
- [ ] **A6 — Commit the tree.** Image tags should be a git short-SHA, not a
  timestamp, or we cannot answer "which commit is running in Chicago".

---

## B. Build and push to ECR

Runbook Phase 1. Must run somewhere with Docker — **CloudShell or your own
machine, not the remote session Linux VM** (its `node_modules` holds Windows binaries).

- [ ] **B1** — Run runbook **Phase 0** first. It is read-only and every later
  phase depends on its answers: existing ALBs, Cognito pools, ACM certs, ECR
  repo naming.
- [ ] **B2** — Create the ECR repo. Runbook uses `cubic-pdm/ps2-dashboard`.
  **Rename it** — this ships PS1–PS5 and Device 360, not PS2.
  Suggest `cubic-pdm/chicago-dashboard`.
- [ ] **B3** — Set `scanOnPush=true` (runbook does) **and** add two things it
  omits: `imageTagMutability=IMMUTABLE`, and a lifecycle policy keeping the
  last ~10 images. Without them, `:latest` silently moves under a running
  service and untagged layers accumulate cost.
- [ ] **B4** — `docker build --build-arg VITE_API_BASE_URL=https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com -t <uri>:<git-sha> .`
- [ ] **B5** — Push `<git-sha>`. Push `:latest` **only** if you accept mutable
  tags; if B3 sets IMMUTABLE, drop `:latest` entirely and reference the SHA.
- [ ] **B6** — Smoke the image before ECS ever sees it:
  `docker run -p 8080:8080 <uri>:<sha>` then `curl localhost:8080/healthz` and
  open the page. Catches a broken build in seconds instead of via a failing
  ECS deployment circuit breaker.

---

## C. Deploy and expose the endpoint

Runbook Phases 2–5.

- [ ] **C1 — Get the VPN CIDR from the network team.** Runbook has
  `VPN_CIDR="REPLACE_ME"`. **Do not default this to `0.0.0.0/0`** — the ALB is
  internal but the SG still governs who reaches it.
- [ ] **C2 — IAM.** Execution role needs ECR pull + CloudWatch Logs only. The
  task role should be near-empty: this container calls no AWS API. Don't
  over-grant it because a template said to.
- [ ] **C3 — Security groups.** ALB SG allows 443 from the VPN CIDR; task SG
  allows 8080 **from the ALB SG only**.
- [ ] **C4 — ACM certificate.** Needs a cert covering whatever hostname
  Chicago will type. **UNVERIFIED** — Phase 0 will list what exists. If none,
  this becomes a DNS + cert request with its own lead time; find out early.
- [ ] **C5 — Cognito user pool + client + domain.** Same: Phase 0 tells us
  whether one exists. If it does not, provisioning it is a separate task —
  don't let it get skipped silently because the ARN field was left blank.
- [ ] **C6 — Internal ALB + target group.** Health check path `/healthz`,
  target type `ip`, port 8080.
- [ ] **C7 — Fill the task definition's three `REPLACE_ME`s** (execution role
  ARN, task role ARN, image URI) and register it. Rename the family off
  `cubic-mars-ps2-dashboard` to match B2.
- [ ] **C8 — Create the service.** `desired-count 2` across two AZs, circuit
  breaker with rollback enabled (runbook has this — keep it).
- [ ] **C9 — Friendly DNS.** A raw ALB DNS name is not something to hand a
  client team. Route 53 record, e.g. `chicago-pdm.<internal-zone>`.

---

## D. Verification — "deployed" means real data on screen

- [ ] **D1** — `aws ecs wait services-stable`, then `describe-target-health`
  shows healthy targets.
- [ ] **D2** — From a **VPN-connected** machine, open the URL, complete the
  Cognito sign-in, and confirm PS1–PS5 and Device 360 each render real
  numbers. A blank panel is the live-only pattern working correctly and
  telling you `VITE_API_BASE_URL` or the API GW is wrong.
- [ ] **D3 — Concurrency check.** `/ps1/device-360` measures **5.7–7.6s**
  server-side and API Gateway has a **hard 30s cap**. Measured optimum is two
  concurrent requests. Before many Chicago users hit this at once, check
  Lambda reserved concurrency and RDS connection limits — this is the most
  likely first production complaint.
- [ ] **D4 — Data completeness.** These tables are **empty** and will render
  as blank panels: all three `ps3_severity_*`, `ps1_model_performance`,
  `ps1_feature_importance`. `ps1_prediction_explainability` has only **26
  rows**, so per-device driver bars appear for a small minority of devices —
  by design, the panel degrades to history, but the client should be told
  rather than discover it.
- [ ] **D5 — Model-gate language.** PS1 and PS5 both return REVIEW-ONLY
  caveats ("has not passed its quality gate", "no fleet is signed off in the
  model registry yet"). The UI surfaces these. Confirm the client is briefed
  that this is a prioritisation aid, not an auto-dispatch system.
- [ ] **D6 — Rollback rehearsed.** Deploy the previous image tag once, on
  purpose, and time it. A rollback you have never run is not a rollback plan.

---

## E. PS5 — run the model and land it in RDS

Order matters. The loader has **no DDL** and cannot create its own tables.

- [ ] **E1 — Rerun the notebook.**
  `PS5_Reliability_Survival_v2_CIndexLift.ipynb` in SageMaker. It writes CSVs
  to `s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/ps5/notebook_outputs/<device>/`
  where `<device>` ∈ `{gates, tvm, validators}`, plus a
  `ps5_rds_load_manifest.json` declaring what the run intends for RDS.
- [ ] **E2 — Confirm the S3 listing** matches the manifest before loading.
  Cheap, and it catches a half-finished notebook run.
- [ ] **E3 — Run the schema migration first:**
  ```
  aws lambda invoke --function-name cubic-mars-dashboard-api \
    --cli-binary-format raw-in-base64-out \
    --payload '{"action":"migrate"}' /dev/null
  ```
  `sql/29_ps5_outputs.sql` creates every table the loader writes.
- [ ] **E4 — Deploy the loader** — `api/lambda/cubic-mars-ps5-rds-loader/deploy.sh`.
  It needs no AWSSDKPandas layer (CSV via stdlib), which is why it avoids the
  cross-account layer probe that cost several rounds on PS2 and PS4.
- [ ] **E5 — Invoke with `dry_run` first.** In this loader `dry_run` doubles
  as the S3-vs-Aurora reconciliation report. Read it before the real load.
- [ ] **E6 — Real load.** The loader is schema-adaptive (columns from
  `information_schema` at runtime), quotes every identifier, uses per-table
  savepoints so one bad file doesn't discard the other twenty-one, and refuses
  a file whose PK would collapse rather than raising 23505 mid-transaction.
  Read its per-table summary; a refused file is a signal, not noise.
- [ ] **E7 — Verify through the API, not the database.** Hit `/ps5/device-rul`
  (device grain) and `/ps5/serial-rul` (component grain) and confirm row
  counts and a fresh `as_of_date`. Passing in SQL but failing through the view
  layer is a failure mode we have already hit twice on this project.
- [ ] **E8 — Decide the schedule.** One-off run, or EventBridge on a cadence?
  If scheduled, the loader must be idempotent for the same `as_of_date` —
  **UNVERIFIED**, needs a second run against unchanged S3 to prove it.

---

## Sequencing

**A → B → C → D** is strictly ordered. **E is independent** and can run in
parallel — in fact it should start now, because E1 (notebook rerun) has the
longest lead time of anything on this list and D2 will show stale PS5 numbers
until it lands.

Two items have external lead time and should be raised today, not when the
checklist reaches them: **C1** (VPN CIDR, network team) and **C4/C5** (ACM
cert and Cognito pool, whoever owns identity).
