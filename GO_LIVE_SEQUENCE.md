# CUBIC MARS Chicago — GO-LIVE SEQUENCE

**Version 2.0 — 16-Aug-2026.** Supersedes `GO_LIVE_CHECKLIST.md` (05-Aug), renamed to
match how the team refers to it. Grounded in repo `main` (`7f9e34f`) and live checks
run 16-Aug — not a generic runbook. Evidence tags: `[M <date>]` measured live,
`[R]` read from the repo, `[D]` documented decision, `[U]` unverified — check before
relying on it.

Companions: `docs/DASHBOARD_LINEAGE_AND_DEPENDENCIES.md` (v1.1 — the full dependency
map), `README_V4.md`, the ECR publish runbook (Word doc, circulated separately),
`docs/PS1_CANONICAL.md` and `docs/PS1_DECISIONS_15Aug2026.md`.

---

## 0. What is already DONE since the 05-Aug checklist (do not redo)

- [x] V4 is the shipped dashboard, fully merged to `main` (PR #11 + PR #13), tags
      `v4.0.0-rc1`/`rc2`; V2/V3 removed; repo swept of 47 stale files 16-Aug
      (recovery tag `archive-sweep-base`).
- [x] Root routing fixed: `/` and `*` redirect to `/v4` — a bare ALB hostname lands
      on the shipped product `[R App.jsx]`. (Old item A1 — closed 05-Aug.)
- [x] One-image-many-environments: `docker-entrypoint.sh` rewrites `/config.js`
      from `API_BASE_URL` at container start; build-time `VITE_API_BASE_URL` is only
      the fallback. (Old item A4 — closed 05-Aug.)
- [x] Tree committed and SHA-taggable; local = remote at `7f9e34f`. (Old A6.)
- [x] PS5 data plane COMPLETE (old section E): v5.6 notebook run (contract OK,
      leak-check PASS), loader deployed and scheduled 07:20 UTC, Aurora holds 1,536
      device + 11,718 serial rows `[M 08-Aug]`.
- [x] `/ps3/summary` serves the full severity scorecard (#63) `[M 16-Aug]`.
- [x] `/ps3/collapse-health` guard RESTORED via sql/55 after 12 days dark (#65):
      TVM MAJOR 57.89% / CRITICAL 42.11%; GATE NULL-by-design `[M 16-Aug]`.
- [x] PS1 scorecard live for all three fleets (#61): `/ps1/summary` +
      `/ps1/model-performance` serve GATE AUC 0.8961/AP 0.9486, TVM 0.9040/0.9835,
      VALIDATOR 0.9956/0.9903 — PASS, promoted, target `will_hardware_oos_3d`
      `[M 16-Aug]`.

---

## A. Pre-flight — decisions and hygiene before packaging

- [ ] **A3 — Auth boundary (OPEN).** The app has `ProtectedRoute` + `LoginPage`;
      the ECS runbook adds ALB `authenticate-cognito`. Two auth layers that don't
      know about each other is a support problem. Pick one as authoritative before
      C5. `[U]` what ProtectedRoute validates against.
- [ ] **A5 — Prove the build on current main.** ECR `dashboard/reactui` held 3
      images at the 09-Aug audit `[M]`, but the CURRENT `main` has never been proven
      to build. Run `npm run build` in `dashboard/` locally and read the output
      before the next image.
- [ ] **A7 — Add `.gitattributes`** (`* text=auto eol=lf`). One line; ends the
      CRLF churn that has repeatedly shown hundreds of phantom diffs on Linux
      checkouts. Cheap now, annoying forever otherwise.
- [ ] **A8 — Rotate the dev RDS secret** (`cubic-mars-secret-rds-dev`). It has
      circulated in working sessions; rotate before widening team access. Loaders
      read it from Secrets Manager, so rotation is transparent to them.

---

## B. Build and publish to ECR

Full commands live in the ECR runbook (Word doc) and
`docs/DASHBOARD_LINEAGE_AND_DEPENDENCIES.md` §9. The sequence:

- [ ] **B1 — Naming decision, once:** live repo is `dashboard/reactui` (3 images
      `[M 09-Aug]`); the old checklist proposed `cubic-pdm/chicago-dashboard`.
      Recommend KEEPING `dashboard/reactui` to avoid IAM/task-def churn — but
      record the decision; the account already has a three-way prefix split being
      tracked.
- [ ] **B2 — Repo settings:** `scanOnPush=true`, `imageTagMutability=IMMUTABLE`,
      lifecycle policy keeping ~10 images. `[U]` current settings — check once.
- [ ] **B3 — Build from `dashboard/` (NEVER `src/v4` alone)** with
      `--build-arg VITE_API_BASE_URL=https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com`,
      tag = git short SHA of `main`. No `latest`, ever.
- [ ] **B4 — Smoke before push:** `docker run -p 8080:8080 -e API_BASE_URL=...`,
      then `curl localhost:8080/healthz` (expect `ok`) and open the page.
- [ ] **B5 — Push the SHA tag; verify** with `aws ecr describe-images`.

---

## C. Deploy and expose (THE remaining go-live construction work)

State: ECS cluster `cubic-mars-ecs-cluster-dev` exists with 1 orphan task / 0
services; no ALB, Cognito or WAF `[M 09-Aug]`. `infrastructure/app_spoke/` carries
`ecs-task-def.dashboard.json` (3 REPLACE_MEs) and `deploy_dashboard_ecs.sh`
(6 phases, 5 REPLACE_MEs) `[R]`.

- [ ] **C0 — Run the runbook's read-only Phase 0 first** — it answers what ALBs,
      Cognito pools, ACM certs and ECR names already exist.
- [ ] **C1 — VPN CIDR from the network team.** Never default to `0.0.0.0/0`.
      (External lead time — raise TODAY.)
- [ ] **C2 — IAM:** execution role = ECR pull + logs only; task role near-empty
      (this container calls no AWS API).
- [ ] **C3 — Security groups:** ALB 443 from VPN CIDR; task 8080 from ALB SG only.
- [ ] **C4 — ACM cert** for the hostname Chicago will type. `[U]` — Phase 0 lists.
      (External lead time — raise TODAY.)
- [ ] **C5 — Cognito pool + client + domain** (see A3 first). `[U]` existence.
- [ ] **C6 — Internal ALB + target group:** health check `/healthz`, target type
      `ip`, port 8080.
- [ ] **C7 — Fill the task definition** (execution role, task role, image URI =
      the SHA from B); rename the family off `cubic-mars-ps2-dashboard` to match B1.
- [ ] **C8 — Service:** desired-count 2 across two AZs, deployment circuit breaker
      with rollback ON.
- [ ] **C9 — Friendly DNS** (Route 53, e.g. `chicago-pdm.<internal-zone>`) — never
      hand a client a raw ALB hostname.

---

## D. Verification — "deployed" means real data on screen

- [ ] **D1 —** `aws ecs wait services-stable`; target group shows healthy targets.
- [ ] **D2 —** From a VPN-connected machine: sign in, and confirm PS1-PS5 +
      Device 360 each render real numbers. Pre-ALB smokes that are already green
      `[M 16-Aug]`: `/ps3/summary`, `/ps3/collapse-health`, `/ps1/summary`,
      `/ps1/model-performance`, `/ps1/coverage`.
- [ ] **D3 — Concurrency:** `/ps1/device-360` runs 5.7-7.6 s server-side against a
      hard 30 s API GW cap; measured optimum is 2 concurrent. Before many users hit
      it, check Lambda reserved concurrency + RDS connection limits. Likeliest
      first production complaint.
- [ ] **D4 — Data completeness — brief the client BEFORE they find these:**
      - PS1 headline panels ride a **26-Jul hand-seeded vintage** and the PS1
        screen carries **no as-of badge** — full fix list in
        `docs/V4_DASHBOARD_PS1_AUDIT_16Aug2026.md`. PS1 data as-of 2026-04-11.
      - Dark by known cause: legacy `ps3_severity_*` trio (empty),
        `ps3_head_feature_importance` (0 rows → the three `/ps3/*/drivers` routes
        return `[]`), `ps1_prediction_explainability` (26 rows),
        `ps3_v25_prediction_explainability` (3 status rows),
        `ps5_weibull_params`/`ps5_cox_hazard_ratios` (declared, never loaded).
      - The PS5 device/component population gap (3,899 roster devices without an
        RUL estimate) is deliberately documentation-only — not a dashboard change.
- [ ] **D5 — Model-language briefing:** PS3 severity is TVM-led (GATE severity is
      degenerate by design and masked NULL); PS1 flagged-device counts depend on
      deployed thresholds that do not match the published sweep (E-1, being
      resolved by the serving rework) — position everything as a prioritisation
      aid, not auto-dispatch.
- [ ] **D6 — Rehearse rollback once, on purpose, and time it.** Re-point the task
      definition at the previous SHA. A rollback never run is not a plan.

---

## E. Data-plane items that gate freshness (parallel track — start now)

- [ ] **E1 — PS2 notebook repoint (DO FIRST — silent-failure risk).** The loader
      reads `chicago/ps2_outputs`; the notebook still writes the old prefix. Set
      `PS2_PRODUCTION_EXPORT_PREFIX=chicago/ps2_outputs` before the next PS2 run,
      or the 07:10 UTC daily loader keeps serving the frozen copy with no error.
- [ ] **E2 — PS1 daily inference (longest lead).** Design is committed
      (`docs/PS1_DAILY_INFERENCE_DESIGN.md`, `notebooks/ps1_batch_score_daily.py`,
      PutEvents trigger with gold-non-empty guard). Remaining blocker: extract the
      notebook feature cells into the Databricks feature-frame task — never port
      that logic to Python `[D 15-Aug]`. Until it lands, the nightly crons re-load
      the same 29-Jul artifacts by design.
- [ ] **E3 — Serving rework (D-2/D-4):** daily scoring moves to a SageMaker
      Processing job; the always-on endpoints are slated for deletion once the
      capture is complete. Endpoint section of the lineage doc stays
      "Being Updated" until then.
- [ ] **E4 — PS3 refreshes stay manual by design** (no EventBridge rules on the
      three PS3 loaders). Production reruns of V26 write `chicago/ps3_outputs`;
      the loader picks the newest COMPLETE run automatically.
- [ ] **E5 — Migrations discipline:** `sql/50-55` are NOT registered in
      `migrate()` — they are applied manually via psql (CloudShell VPC env). Never
      assume a deploy applied them; never ship one SQL file via `deploy.sh`
      (it runs `migrate()` unconditionally and resets Lambda memory/env).

---

## F. Program gates for an ACCEPTED go-live (not dashboard work, but they gate sign-off)

- [ ] Acceptance criteria agreed with the client (PSA §3.1.2 / Annexure 1 vs
      per-PS operating-point metrics) — the standing top program item.
- [ ] ServiceNow integration decision: the SQS FIFO path has never been built;
      today's `/ps1/servicenow-stage` writes to a staging table only.
- [ ] Observability: TGW attachment PR pending confirmations.
- [ ] CI/CD + IaC for UAT promotion (the app tier is deployed imperatively today).

---

## Sequencing

**A → B → C → D is strictly ordered.** The C-items with external lead time —
**C1 (VPN CIDR)** and **C4/C5 (cert + identity)** — should be raised today, not
when the checklist reaches them. **E runs in parallel**: E1 today (it guards a
scheduled loader), E2 is the longest lead and controls when the dashboard shows
fresh dates instead of 2026-04-11.

Keep this file current: tick items with the date and evidence, and when a section
completes, move it to §0 with its completion date rather than deleting it.
