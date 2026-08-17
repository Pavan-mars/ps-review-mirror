# PS1-PS5 — Right / Wrong / Fix: Consolidated Summary, Recommendations, Next Steps

**16-Aug-2026, post SET W live verification.** Companion to the five per-PS audits
(`docs/PS*_AUDIT_16Aug2026.md`) and `docs/PS_SCHEDULING_AND_ECR_EXIT_DECISIONS_16Aug2026.md`.
Tags: `[M]` measured live, `[R]` repo, `[D]` decision, `[U]` unverified.

## 1. What is RIGHT (verified — keep and build on)

- **The bottom half of the target architecture is DONE for all five PS** `[M]`:
  S3 -> per-PS loader Lambdas -> Aurora `appdb` -> dashboard-api (`a9yuqt9j9b`) ->
  V4 dashboard on main. Every ENABLED daily rule ran on schedule 16-Aug; all 8
  loader read-only self-checks came back clean (0 errors).
- **Loader engineering is production-grade** `[M+R]`: dry-run/verify modes,
  per-fleet SAVEPOINTs, manifest discipline, refuse-on-collapse. PS5 and PS4-v3
  show EXACT S3-vs-RDS parity; ps1-xw's own `verify` confirms the 786,525-row
  contract in Aurora.
- Per PS `[M]`:
  - **PS1** — Path A feed + xw-loader are the reference pattern; the real 3-fleet
    scorecard is PASS/promoted (GATE AUC 0.8961 / AP 0.9486, TVM 0.9040 / 0.9835,
    VALIDATOR 0.9956 / 0.9903).
  - **PS2** — 47 tables / 294,749 rows load clean; migrated prefix byte-exact;
    the screen carries a vintage StatusBar.
  - **PS3** — V26 production run loaded to the row (20/20 tables, 117,377 rows);
    `/ps3/summary` and `/ps3/collapse-health` both serving (sql/55 fix).
  - **PS4** — two ENABLED schedules (daily + weekly v3); v3 parity exact
    (7,742 / 1,108 / 6 / 9 / 3); manifest discipline exemplary.
  - **PS5** — the completest chain of the five; GATE 0.678 and TVM 0.798 clear
    the 0.65 C-index gate on the v5.6 leaderboard.
- RDS is documented to the column (306 objects, live row counts):
  `docs/reference/RDS_LIVE_INVENTORY_16Aug2026.md` `[M]`.

## 2. What is WRONG -> the FIX (severity order)

| # | Wrong (evidence) | Fix |
|---|---|---|
| 1 | RDS secret exposed in chat 16-Aug; NEVER rotated, rotation not configured (LastChanged 22-Jul) `[M]` | Rotate `cubic-mars-secret-rds-dev` NOW; then re-run the four loader dry-runs as the connectivity proof |
| 2 | No incremental data since 11-Apr — every screen static `[M]` | Ingestion 12-Apr -> present (Cubic + teams, ~23-Aug) with per-layer reconciliation; gates everything below |
| 3 | No medallion-complete trigger and no daily scoring/notebook run anywhere `[M]` | Build the committed-but-undeployed PS1 chain: features task (CELLS 6-8) -> Processing job -> PutEvents trigger; then clone for PS3 |
| 4 | E-1: endpoint thresholds 0.1228/0.0265/0.3965 match NO scorecard row; dashboard publishes 0.648/0.495/0.605 `[M]` | Resolved by construction when the Processing job ships (it loads the endpoint's own bundle); until then never derive flagged-device counts from published thresholds |
| 5 | PS2 landmine: the notebook still exports to BARE `ps2_outputs` (last real export, 03-Aug, went there) `[M]` | Set `PS2_PRODUCTION_EXPORT_PREFIX=chicago/ps2_outputs` TODAY — one env var; silent dashboard freeze otherwise |
| 6 | `/ps5/status` serves STALE v1 rows (0.59/0.51/0.60, `dashboard_ready:false`) `[M]` | Re-point the status route/table at v5.6 or retire it — the v5.6 loader never writes it |
| 7 | PS5 VALIDATOR champion 0.64926 vs the 0.65 floor (sd 0.0036) `[M]` | Team decision: at-floor waiver or a lift pass; no client briefing until decided |
| 8 | #66: three `/ps3/*/drivers` routes dark — `ps3_head_feature_importance` 0 rows, NOT in the rc artifact set; `ps3_serial_predictions.csv` unreadable `[M]` | Fresh export from the PS3_03 notebook or retire the drivers routes — a reload cannot fix it |
| 9 | PS3 v25 severity 100% '(unlabelled)' `[M]` | Ship the severity head before pointing ANY severity display or guard at v25; track via `v_ps3_v25_severity_maturity` |
| 10 | Dead 02:00 SFN "training pipeline" fires daily — empty `$.models`, ~45 ms runs, empty training ECR `[M]` | Disable rule `cubic-mars-events-training-daily-dev` (after `list-targets-by-rule` confirms the empty input); record as retired scaffold |
| 11 | PS1 screen: 26-Jul HAND-SEEDED station-summary/risk-trend feed every headline; no vintage badge; UI never calls `/ps1/model-performance`; six frozen Path B routes `[M+R]` | Dashboard hygiene pass (next-steps table, last row) |
| 12 | `mars-ps3:latest` mutable pin (lives in model-package `chicago-ps3-root-cause/14`) + 14-package sprawl `[M]` | Fix the pin in the package's InferenceSpecification during the PS3 exit (or let endpoint deletion moot it); prune to one Approved champion |
| 13 | ECR `dashboard/reactui` newest image is pre-V4 `latest` (15-Jul); 6 of 10 repos empty `[M]` | Rebuild from main with a git-SHA tag before ANY container publish; lifecycle-policy the empties |
| 14 | Census drift: `cubic-mars-ps2-rds-push` un-cataloged `[U]`; PS4 live rule 07:35 vs script 07:10 `[M]`; `ps4v2_*` migration parked since 29-Jul `[M]` | Investigate/retire ps2-rds-push; reconcile the PS4 cron; decide run/revise/retire for ps4v2 (fold #52 threshold check into that decision) |
| 15 | Five `ps2_v25_*_audit` exports have no Aurora tables; PS2's independent PS1 scoring never compared to `ps1_model_performance` `[M+R]` | Create the audit table pair; run the comparison once — disagreement is signal, not noise |
| 16 | PS5 scorer undeployed + prefix mismatch (`chicago/ps5/params` + `/state` EMPTY); `ps5_weibull_params`/`ps5_cox_hazard_ratios` never loaded; 3,899-device population gap `[M]` | Align publishing (or scorer env) before deploying; load the params tables or drop them; resolve the gap cause, then apply the conditional wording in documentation |
| 17 | Four idle endpoints — 0 invocations in 30 days on all of them `[M]` | Delete per D-4 after each PS's first supervised batch run (PS1 x3 + PS3) — cost and attack-surface reduction |

## 3. Recommendations (all evidence-complete after SET W)

- **Serving `[D, settled — do not relitigate]`:** D-1 retire ECR `cubic-pdm/mars-ps1`;
  D-2 daily scoring = SageMaker Processing job (managed DLC, pinned deps — pin
  `xgboost` in requirements AND the bundle template); D-3 trigger = Databricks
  EventBridge PutEvents (NEVER S3 bucket notifications); D-4 delete all four
  endpoints after first supervised runs (the 0-invocation evidence is complete `[M]`).
- **Scheduling per PS (PS4 now RATIFIED):** PS1 + PS3 = the Processing-job chain;
  PS2 = Databricks scheduled job (Spark-native, 31 pyspark hits `[R]`); PS4 =
  Databricks job on the PySpark variants (manifest `engine: pyspark` `[M]`); PS5 =
  split — daily scorer Lambda (after prefix alignment) + weekly SageMaker notebook
  retrain (~27 min `[M]`).
- **Gates `[D]`:** metric-appropriate only — PS1 AP/PR-AUC + recall floor; PS3
  macro-F1; PS4 lead-indicator precision/recall; PS5 C-index >= 0.65. Never raw
  accuracy (rare-event base rates).
- **Operational safety (new, from SET W `[M]`):** an EMPTY payload `{}` = a REAL
  LOAD on every loader — manual invokes always use the explicit dry-run forms;
  never `load` the PS3 v2 loader while the hollow 01-Aug run is newest; label
  PS4's two vintages (scored 2026-04-11 vs cluster 2026-07-28) separately on screen.
- **Housekeeping:** add `.gitattributes` (ends the CRLF churn); GitHub Support
  ticket to purge pre-rewrite PR refs; review the 51 empty RDS tables (22 legacy
  scaffold) after go-live.

## 4. Next steps, in order

| When | Step | Gate |
|---|---|---|
| NOW (this week) | Rotate the RDS secret; set the PS2 env var; disable the dead SFN rule; decide VALIDATOR waiver-vs-lift and #66 export-vs-retire; investigate ps2-rds-push; reconcile the PS4 cron | None — all independent |
| ~23-Aug | Incremental ingestion 12-Apr -> present lands (Cubic + teams), with per-layer reconciliation evidence | GATES everything below |
| After ingestion | PS1 batch chain: features task -> Processing job -> PutEvents trigger -> first supervised run (parity vs endpoints resolves E-1) -> wire loader path -> DELETE the PS1 endpoints + retire mars-ps1 ECR | D-2 / D-3 / D-4 |
| Then | PS3 chain: score-only path from V26 on the same trigger -> delete the PS3 endpoint; prune the 14 model packages | D-4 extension |
| Then | PS2 + PS4 Databricks jobs chained after the Gold build; PS5 scorer deploy + weekly retrain job | Section 3 |
| Then | Model refresh on the extended window; re-gate per the metric table (incl. the VALIDATOR resolution) | Ingestion + chains |
| Alongside | Dashboard hygiene: PS1 vintage badge, wire `/ps1/model-performance`, replace the 26-Jul seeds, label or retire the six frozen routes, re-point `/ps5/status`, dual-vintage PS4 labels | Any time |

**Tracker state:** #65 CLOSED `[M]` - #61 CLOSED `[M]` - #53 AMBER (stale status
feed + VALIDATOR decision) - #66 OPEN (export-vs-retire decision) - #52 OPEN
(folded into the ps4v2 decision) - #44 OPEN (package prune).
