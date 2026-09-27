# PS5 (Remaining Useful Life & SLA Breach) — end-to-end review, 26-Sep-2026

Scope: notebook → S3 → loader Lambda → Aurora → dashboard API → V4 dashboard.
Evidence is file:line in this repo at `c28b543`. Live AWS state was NOT inspected;
items that depend on it are marked **[AWS]** with the command that settles them.

## The live chain (what the client actually sees)

| Layer | Component |
|---|---|
| Model | `notebooks/ps5_reliability_survival/PS5_Reliability_Survival_v5_6.ipynb` (engine inline in cell 3) |
| S3 | `s3://<gold>/chicago/ps5/notebook_outputs/{gates,tvm,validators}/<dev>_<artifact>.csv` (5 artifacts) |
| Loader | `api/lambda/cubic-mars-ps5-rds-loader/handler.py` (daily 07:20 UTC per handler.py:744 note) |
| Aurora | `ps5_device_rul`, `ps5_serial_rul`, `ps5_cindex_leaderboard`, `ps5_permutation_importance`, `ps5_enrich_coverage` + views `v_ps5_device_rul`, `v_ps5_serial_rul` (sql/29–32) |
| API | `api/lambda/cubic-mars-dashboard-api/handler.py:2917–3050` (9 `/ps5/*` routes) + Device-360 (`:1288`) |
| UI | `dashboard/src/v4/V4PS5Overview.jsx` (6 sub-tabs) via `V4api.js:178` |

## 1. Issues with approach, results and algorithms

**P5-1 (critical) — Estimates are frozen at 2026-04-11 and shown as current.**
`CONFIG["RUN_DATE"]` defaults to `2026-04-11` (nb cell 3 :65) and failures are cut there (:653).
Every device's "days to next OOS" (medians ~1 day) is therefore a forecast for mid-April,
presented today (Sep) as live. The OOS event feed runs to 2026-08-29 (PS1 baseline), so
the model can be refit on 4½ more months of events even though enrichment telemetry is frozen at 04-11.

**P5-2 (critical) — Component RUL is not a component model.**
`score_serial_rul` (cell 3 :1292–1315) applies the DEVICE-level inter-OOS Weibull (median ~1 day)
to COMPONENT install age (hundreds–thousands of days). Consequences: every component is
"overdue", `expected_component_rul_days` is a far-tail extrapolation, and
`risk_score = device OOS count / component age` (:1310) ranks components by their host device's
fault count, not their own. GATE/TVM tiers collapse to ~91% CRITICAL (dashboard notes confirm).
No per-serial failure events are used.

**P5-3 (high) — "Act now" fires on ~all devices by construction.**
`act_now = is_overdue AND rul_standard_days <= 30` (sql/29 view). With fitted medians of
0.7–1.5 days, `rul <= 30` is always true, so act_now ≡ is_overdue (≈93% of GATE, per UI notes).
The 30-day threshold was written for wear-out RUL, not inter-event gaps. The hero tile
"Devices to act on now" therefore reports most of the fleet.

**P5-4 (high) — `is_overdue` is length-biased.**
The Weibull is fit on pooled intervals (cell 3 :1199); chronic devices contribute many short
intervals, pulling the pooled median to ~1 day, so almost any device's current gap exceeds it.
The Cox adjustment is clipped to ±3 on the linear predictor (:1224), which limits correction.

**P5-5 (high) — Training leakage from CMDB snapshots.**
`build_static_covariates` (nb cell 4) joins `ci_fault_count` (a lifetime count as of extract)
and ages computed at RUN_DATE onto EVERY historical interval, i.e. features that include the
future of the interval being predicted. Out-of-time C-index for models using them is optimistic.

**P5-6 (medium) — The two "concordance numbers" are now the same number.**
Since 25-Aug `/ps5/status` is derived from `ps5_cindex_leaderboard` (handler.py:744–790), so the
"promoted" figure equals the best candidate. The UI still states the registry figure is "lower on
every fleet… a promotion problem" (V4PS5Overview.jsx:1142–1148, 537–544). Hard-coded narrative, now false.

**P5-7 (medium) — Loader reports success on partial loads.**
Skipped/refused/errored artifacts leave the previous run's rows in place while the rest are
replaced, and the Lambda still returns `statusCode 200 / "committed"` (handler.py:258–275).
Result: one fleet or table can silently show an older run beside newer ones.

Minor: hazard bands are fixed percentiles (top 10% always CRITICAL, cell 3 :1230) — a relative
ranking, correctly caveated in the UI; concordance is evaluated on a fixed-seed 20k subsample (cell 4) — fine.

## 2. Fixes required (proposed; decisions flagged)

| # | Fix | Where | Needs |
|---|---|---|---|
| P5-1 | Refit with failures through the event-feed edge; keep enrichment windowed at 04-11; show a staleness banner when `feature_asof_date` is > 14 days old | notebook CONFIG + UI | **decision**, you run notebook |
| P5-2 | Hide component RUL/overdue until a per-serial event model exists; keep roster + host-device risk, relabelled | UI + API columns | **decision** |
| P5-3 | Re-base act-now on the fleet's own scale (e.g. overdue AND hazard band CRITICAL, or P(event in 7d) ≥ x) | sql view + UI copy | **decision** |
| P5-4 | Fit per-device-weighted (one interval sample per device, or frailty) and report overdue vs device-own history | notebook | re-run |
| P5-5 | Drop `ci_fault_count`; compute ages at interval start, not RUN_DATE | notebook cell 4 | re-run |
| P5-6 | Replace hard-coded concordance narrative with text computed from the feeds | V4PS5Overview.jsx | none — code only |
| P5-7 | Return `status:"partial"` + HTTP 207/500 when any artifact is skipped/refused/errored; optional all-or-nothing flag | loader handler.py | none — code only |

## 3. Client-facing gaps (cheap additions)
- Survival curves per fleet: `ps5_weibull_params` / `ps5_cox_hazard_ratios` are declared (sql/01 :631, :642) but never written. Notebook already has shape/scale — add them to the export + loader.
- Data-as-of / staleness banner on every PS5 tab (see P5-1).
- Event-rate trend per fleet (OOS episodes per device-week) — the honest companion to "days to next OOS".
- Location view by fleet with hazard-band mix (already computed client-side; add export of the top-N list for work planning).

## 4. Stale / redundant — retire list
Nothing below is read by the V4 dashboard (grep of dashboard/src). **[AWS]** checks before deleting are listed.

| Item | Evidence | AWS check |
|---|---|---|
| Lambda `ps5-api` + API GW `b1s4xxlddb` | only consumer was V1 `PS5SLAReliabilityTab.jsx`, no longer in src; release condition in api/lambda/ps5-rds-loader/README.md now met in code | CloudWatch `Invocations`/API GW `Count` = 0 for 14d |
| Lambda `ps5-rds-loader` (bare name) | writes shadow tables only | EventBridge/S3 triggers; Invocations |
| `api/lambda/ps5_daily_scorer/`, `sagemaker/ps5/batch/**`, `sagemaker/ps5/processing/**` | write `ps5_reliability_estimates`/`ps5_serial_reliability`/`ps5_scoring_runs` only | Lambda `cubic-mars-ps5-daily-scorer` exists? schedules? |
| `fastapi_app/ps5_reliability_routes.py` | reads shadow views; not referenced by infra/deploy | none |
| Tables `ps5_reliability_estimates`, `ps5_serial_reliability`, `ps5_scoring_runs`, `ps5_reliability_status`; views `v_ps5_reliability_oos_latest`, `v_ps5_serial_oos_latest` | only Device-360 fallback (handler.py:1296) + shadow stack | row counts / last write |
| Device-360 legacy fallback | handler.py:1296–1301 | none |
| `dashboard/backfill/06_*.sql`, `07_*.sql` | byte-identical to `api/lambda/ps5-rds-loader/sql/06,07` | none |
| `sql/02` PS5 seed rows for `ps5_reliability_status` | handler.py:744 docstring: stale v1 C-indexes re-asserted on each migrate | none |

## 5. Local verification
`tests/` is empty. A harness will be added: Postgres (docker) + sql/01,29–32 + fixture CSVs →
loader (local S3 stub) → API routes → assertions; dashboard `npm run build`.

## 6. Status — changes made on `review/ps5` (26-Sep-2026)

Decisions (26-Sep): refit with events to 29-Aug; keep component columns with a UAT-only notice;
act_now = P(OOS within 7 days) >= per-fleet threshold.

| Fix | Change |
|---|---|
| P5-1 | Notebook: `EVENT_END_DATE` (default 2026-08-29, env `PS5_EVENT_END_DATE`) now bounds OOS events, censoring and `feature_asof_date`; enrichment stays windowed at `RUN_DATE` (2026-04-11). Dashboard: staleness banner on every PS5 tab when as-of > 14 days. |
| P5-2 | Components tab: permanent "verification and UAT only" notice; component act_now = host device act_now (sql/67). |
| P5-3 | Notebook exports `p_oos_7d`; sql/67 adds the column, `ps5_act_now_policy` (per-fleet threshold, default 0.90) and rebuilds both views; API returns `p_oos_7d`, `act_now_threshold`, p50/p90; UI shows the column and the rule. |
| P5-5 | Notebook cell 4: `ci_fault_count` dropped; CMDB ages measured at interval start. |
| P5-6 | Hard-coded concordance narrative replaced by text computed from the feeds. |
| P5-7 | Loader: `committed_partial` + HTTP 500 when any artifact is skipped/refused/errored; `incomplete` lists them. |
| Retire (code-only) | `fastapi_app/ps5_reliability_routes.py` removed; Device-360 fallback to `ps5_reliability_estimates` removed. |
| P5-4 (decided 26-Sep) | `PS5_DEVICE_WEIGHTED=true` weights each device equally in the final Weibull/Cox fit (default off). Run the refit once as the baseline, then again with it on, and compare typical interval, overdue share and p_oos_7d per fleet. Test: `test_device_weighting_stops_chronic_device_dominating` (needs numpy/scipy, run in Studio). |
| P5-3 revised (26-Sep, after the refit) | p_oos_7d saturated (p50/p90 GATE 0.956/0.992, TVM 0.9995/1.0, VALIDATOR 0.988/1.0), so no 7-day threshold separates devices. Notebook now also exports `p_oos_1d`; `sql/71_ps5_act_now_1d.sql` adds the column, moves the policy to `horizon_days=1` (placeholder threshold 0.50) and computes `act_now_p` from the policy's horizon; API returns `p_oos_1d`, `act_now_p`, `act_now_horizon_days` and p50/p90 of `act_now_p`; UI column is "Act-now probability". |
| P5-8 (found 26-Sep, after sql/71) | `p_event_within` returned 1.0 when S(age) underflowed, and `conditional_mrl` fell back to ~1 day, so the LONGEST-quiet devices (lowest risk when shape < 1) scored as certain: 51 gates, 6 TVMs, 298 validators at p_oos_1d = 1.0. Both now use the cumulative-hazard difference (no underflow). Test: `test_long_quiet_device_is_not_scored_as_certain`. Needs a notebook re-run + load before thresholds are set. |
| Thresholds (27-Sep, after P5-8 re-run) | No device at p = 1.0 any more. `sql/72_ps5_act_now_thresholds.sql` sets GATE 0.49 / TVM 0.66 / VALIDATOR 0.51 (~p90 each, ~10% work list). VALIDATOR stays below the 0.65 floor and its 1-day spread is narrow (p90 0.514): a ranking, not a forecast. |
| Not done | survival curves (write `ps5_weibull_params`) — next. Shadow stack retirement waits on the AWS check. |

### Local checks
```bash
python -m pytest -q tests/ps5                      # engine p_oos_7d maths + loader partial-load status
psql "$RDS_URL" -f tests/ps5/check_sql.sql         # sql/29-32 + 67 in a scratch schema, always ROLLBACK
cd dashboard && npm ci && npm run build            # dashboard compiles
```
Verified here: the three pytest cases pass, and `check_sql.sql` passes on Postgres 16. The dashboard build could NOT be run in the review container (npm registry blocked).

### Deploy order (you run these)
1. Deploy `cubic-mars-dashboard-api` (handler + sql/67), then invoke
   `{"action":"apply_sql","file":"67_ps5_act_now_probability.sql"}`.
2. Deploy `cubic-mars-ps5-rds-loader`.
3. Re-run the PS5 notebook (defaults already set; `PS5_EVENT_END_DATE=2026-08-29`), let it write to S3,
   invoke the loader, confirm `"status": "committed"`.
4. (after sql/71) Read `/ps5/summary` p50/p90 of `act_now_p` (the 1-day probability) per fleet and set thresholds:
   `UPDATE ps5_act_now_policy SET p_threshold=<x>, note='<why>' WHERE city_id='CHI' AND device_type='<T>';`
5. Rebuild and deploy the dashboard image.
Until step 3 runs, `p_oos_7d` is NULL on every row, so act_now is FALSE everywhere; that is deliberate, not a guess.

### Retirement (26-Sep, after the CloudShell check)
Source moved to `_retired/ps5_shadow_stack/` (see its RETIRED.md); `sql/68_ps5_shadow_drop.sql` drops the 4 tables and
3 views (no CASCADE) and their CREATE/seed blocks are removed from sql/01, 02, 29. Kept: `ps5_weibull_params`,
`ps5_cox_hazard_ratios` for the survival-curve panel. Note on the refit: enrichment is joined as-of (backward), so
intervals after 11-Apr carry the frozen April features forward -- nothing is read past 11-Apr, but those features are stale
for late intervals; judge the refit on the event-history features first.

## 7. Deployed state, 27-Sep-2026 (end of day)
| Layer | State |
|---|---|
| Notebook | refit with events to 29-Aug, P5-8 fix; 860/450/1,545 devices scored; C-index 0.673 / 0.757 / 0.630 (VALIDATOR below floor) |
| RDS | sql/67, 71, 72 applied; thresholds GATE 0.49, TVM 0.66, VALIDATOR 0.51 on 1-day P(OOS) -> act_now 94 / 53 / 206; sql/68 applied (shadow stack dropped, 10 statements, 0 failed) |
| Loader | `cubic-mars-ps5-rds-loader` committed, nothing incomplete |
| API | `cubic-mars-dashboard-api` at Chicago main `fd0df1d` |
| Dashboard | ECS `FrontEndDashboard:24` = `dashboard/reactui:fd0df1d` (rollback: `:23` = d059a4b, `:22` = 4af4f2f) |
| Retired in AWS | Lambdas `ps5-api`, `ps5-rds-loader` and API GW `b1s4xxlddb` deleted (0 invocations in 14 days); code + config archived at `s3://cubic-mars-pm-s3-datalake-dev-artifacts-170202974600/deploy/retired_27sep/` |

Open: P5-4 weighted comparison run (`PS5_DEVICE_WEIGHTED=true`, do not publish) - compare overdue share and 1-day spread, then decide; survival-curve panel (`ps5_weibull_params`); VALIDATOR model below floor - treat its act-now list as a ranking. Re-check thresholds after every refit (counting script in this review's history: `/ps5/device-rul?limit=12000`).

## 8. Follow-ups, 27-Sep-2026
| Item | Change | How to run / check |
|---|---|---|
| P5-4 weighted comparison | `notebooks/ps5_reliability_survival/ps5_compare_runs.py` summarises local outputs (overdue share, median RUL, p_oos_1d p10/p50/p90 and spread, act-now at the sql/72 thresholds, best C-index) | `snapshot baseline.json` BEFORE the run, run with `PS5_DEVICE_WEIGHTED=true` and no `PS5_PUBLISH`, then `compare baseline.json` |
| VALIDATOR below floor | Lead: `inc` (incident features) and `mnt` (maintenance ledger) join 0% of validator intervals although both tables hold data, while gates/TVMs join 93-100%. Suspected DEVICE_KEY domain mismatch for validators. TVM `mtr` 0% is expected (no M401 metric on TVMs) | diagnostic cell in the 27-Sep session notes; fix the key mapping, then refit |
| Survival-curve panel | `sql/74_ps5_fleet_survival.sql` (table + drops never-written `ps5_weibull_params`, `ps5_cox_hazard_ratios`); loader writes one row per fleet from `<fleet>_device_survival_params.json`; `/ps5/survival` returns S(t), median days, P(OOS within 1/3/7/14 days); new "Time to next OOS" tab | apply sql/74 BEFORE deploying the loader (else the params step errors and the load reports committed_partial) |

## 9. Outcomes, 27-Sep-2026 (afternoon)
| Item | Result | Decision |
|---|---|---|
| P5-4 device weighting | Validator overdue 92% -> 16%, but GATE 28% -> 35%, TVM 14% -> 30%; 1-day probability spread shrank on every fleet; validator Weibull shape 0.34 (two populations: chronic and quiet devices) | NOT adopted (`PS5_DEVICE_WEIGHTED` stays false) |
| Overdue definition | Now: current fault-free run > p90 of the device's own closed gaps (last 90 days if >= 5, else full history if >= 5, else "insufficient" and never flagged). Published and loaded: overdue GATE 62 (7%), TVM 7 (2%), VALIDATOR 511 (33%); act-now unchanged 94 / 53 / 206 | Adopted. `overdue_reference_days`, `overdue_basis` in the device CSV (not loaded) |
| VALIDATOR below floor | `inc`/`mnt` join 0% because `maintenance_ledger` (1,521 keys) and `device_incident_features_daily` (1,301 keys) hold only the 3,346xxx-3,347xxx gate/TVM key range; validator keys (e.g. 3,359,512, 3,380,844, 31,757) are absent | Upstream (Databricks silver) gap, not a notebook bug. Validator act-now stays a ranking |
| Validator overdue caveat | For a fleet whose risk FALLS with quiet time (shape < 1), "overdue" means "unusually quiet for this device", which can also mean out of service (a bus validator not in use logs no OOS) | State this in UAT; do not read validator overdue as "about to fail" |
| Dashboard | `FrontEndDashboard:25` = `reactui:4fcb008` (survival tab). Rollback `:24` | |
