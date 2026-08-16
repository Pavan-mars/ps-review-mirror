# PS5 — Complete Audit vs the Target Daily-Run Architecture

**16-Aug-2026.** Same framework as the PS1/PS2 audits. Tags: `[M]` measured live,
`[R]` repo, `[D]` decision, `[U]` unverified.

**Target (PK):** medallion-complete trigger -> daily scheduled notebook run once
Gold updates -> S3 -> Lambda -> RDS -> dashboard. PS5 (RUL / survival) does not
need per-event inference.

**Headline verdict:** the most COMPLETE chain of the five — notebook family mature
(v5.6), artifact contract manifest-verified, loader deployed with the best dry-run
discipline, daily load ENABLED, dashboard fully wired with 9 routes. Missing the
same two things as everyone else (trigger + scheduled run), plus two PS5-specific
items: the survival-parameter tables never load, and the device/component
population gap is a documentation commitment.

## (a) PS5 SageMaker notebooks

`notebooks/ps5_reliability_survival/` + `ps5_remaining_useful_life/` `[R]`:

| Notebook / asset | Role | Status |
|---|---|---|
| `PS5_Reliability_Survival_v5_6.ipynb` | CURRENT production run — v5.6: 27.4 min, contract OK, leak-check PASS `[M 08-Aug]` | current; manual |
| `PS5_Reliability_Survival_v5_1 ... v5_5.ipynb` | version history | kept |
| `PS5_OOS_Spine_and_Serial_Builder.ipynb` | spine/serial builder | kept |
| `ps5_reliability_engine_v51.py` (+ `PS5_cell5b_memoise.py`, `PS5_speed_patch.py`) | engine module + performance patches | kept |
| `ps5_remaining_useful_life/PS5_{GATE,TVM,VALIDATOR}_Device_Reliability_Analysis.ipynb`, `Chicago_PS5_Survival_RUL.ipynb` | earlier analysis family | superseded |

Model quality gate: concordance index (C-index) with floor 0.65; the v2 lift work
raised it above the floor via leakage-safe as-of enrichment `[R]`. Live C-index
re-verification vs 0.65 is tracker #53 — open `[U]`.

## (b) S3 locations for PS5 outputs

Gold bucket, `chicago/ps5/notebook_outputs/{gates,tvm,validators}/` — 36 files,
2.47 MB per run `[M 08-Aug]`:

| File per fleet | Loaded to Aurora? |
|---|---|
| `*_device_rul_estimates.csv` | YES -> `ps5_device_rul` |
| `*_serial_reliability.csv` | YES -> `ps5_serial_rul` |
| `*_cindex_leaderboard_v5.csv` | YES -> `ps5_cindex_leaderboard` |
| `*_permutation_importance.csv` | YES -> `ps5_permutation_importance` |
| `*_enrich_coverage.csv` | YES -> `ps5_enrich_coverage` |
| `*_device_survival_params.json` / `*_serial_params.json` | NO — Weibull shape/scale + Cox coefficients live ONLY here; the declared `ps5_weibull_params` / `ps5_cox_hazard_ratios` tables have no loader |
| `*_device_state.parquet`, PNGs | NO — model internals / charts |
| root: `ps5_rds_load_manifest.json`, `ps5_reliability_v5_summary.json`, `run_console_log.txt` | manifest declares the RDS intent — confirm S3 listing matches it before loading |

PS5 is the CSV outlier (the other four PS export Parquet). The Parquet migration is
specified (5 changes, ~1 hour) and deliberately deferred to the Boston
standardisation window `[D]`.

## (c) EventBridge for PS5

| Rule | Schedule (UTC) | State |
|---|---|---|
| `ps5-rds-loader` daily load | cron(20 7 * * ? *) — 07:20 | ENABLED `[M 09-Aug]` |
| Medallion-complete trigger / scheduled notebook run | — | DOES NOT EXIST |
| `ps5_daily_scorer` (new Lambda in repo, `api/lambda/ps5_daily_scorer/`) | `[U]` | purpose/schedule/deploy state UNVERIFIED — inspect before building anything that overlaps it |

## (d) Loaders, handlers, routes for RDS

`cubic-mars-ps5-rds-loader` `[R]`: CSV via stdlib (no pandas layer needed);
schema-adaptive (columns from information_schema at runtime); quotes every
identifier; per-table SAVEPOINTs so one bad file cannot discard the rest; refuses a
file whose PK would collapse rather than erroring mid-transaction; `dry_run`
doubles as the S3-vs-Aurora reconciliation report — read it before every real load.

Aurora state `[M 08-Aug]`: `ps5_device_rul` 1,536 rows, `ps5_serial_rul` 11,718
rows, plus leaderboard/importance/coverage/status tables and views
(`v_ps5_device_rul`, `v_ps5_serial_rul`, `v_ps5_serial_dupes`, `v_ps5_serial_fanout`
— the old roster fan-out is GONE in v5.6). Legacy `ps5_reliability_estimates` /
`ps5_serial_reliability` tables are dead by schema defect (enum never created /
NOT NULL columns the export cannot fill) — the loader deliberately targets the
`*_rul` tables instead.

Routes (9): `/ps5/device-rul`, `/ps5/serial-rul`, `/ps5/component-summary`,
`/ps5/leaderboard`, `/ps5/importance`, `/ps5/coverage`, `/ps5/summary`,
`/ps5/status`, `/ps5/serial-grain` `[R, 16-Aug grep]`. Served by dashboard-api at
API GW `a9yuqt9j9b`. A SEPARATE ps5-api Lambda behind API GW `b1s4xxlddb`
($default catch-all, route map inside the Lambda) belongs to the UNMERGED
`feat/ps5-hardware-oos-dashboard` branch — the V4 on main does not use it `[M 09-Aug]`.

## (e) PS5 elements on the dashboard

`V4PS5Overview.jsx` — RUL tab (`device-rul`), Location (client-side rollup of the
same feed, no extra endpoint by design), Components (`serial-rul`,
`component-summary`), Model quality (`leaderboard`, `importance`), How-we-know
(`coverage`, `summary`) `[R]`. Display measure renamed to "days to next OOS";
`is_overdue` / `act_now` / tier shares shown (08-Aug display fixes).

**The population gap (OPEN, deliberately NOT on the dashboard `[D 08-Aug, PK]`):**
the component roster covers 5,434 devices but only ~1,536 carry an RUL estimate —
3,899 devices (72%) have components and no estimate; strictly one-directional.
Candidate causes: `is_current` not applied to the component roster, and/or the
2024-01-01 telemetry window. Agreed wording "no RUL risk" is CONDITIONAL on
confirming those devices have no hardware-OOS events at all — resolve the cause
first. Goes into Chicago documentation after go-live, not onto a screen.

## Verdict vs target

| Target step | Reality | Verdict |
|---|---|---|
| Trigger on medallion-complete | none | MISSING |
| Daily scheduled notebook run | manual (27-min runtime is schedule-friendly); mechanism undecided; `ps5_daily_scorer` Lambda unexamined | MISSING + decision needed |
| Outputs to S3 | manifest-declared, verified inventory | RIGHT (CSV outlier; Parquet deferred by decision) |
| Lambda -> RDS | best-discipline loader, daily 07:20 ENABLED, row counts verified | RIGHT |
| Dashboard refresh | all 9 routes wired across 5 sub-tabs | RIGHT |
| Extra | weibull/cox params never land in RDS; population gap documentation owed; #53 live C-index check | close during rollout |
