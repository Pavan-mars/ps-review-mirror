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
