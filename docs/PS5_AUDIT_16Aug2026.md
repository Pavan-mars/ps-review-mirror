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


---

## Addendum A — Live verification, 16-Aug-2026 evening [M]

**RDS (PS5 family — 13 tables, 30,476 rows, 8 views):**

| object | kind | rows | cols |
|---|---|---:|---:|
| ps5_serial_reliability | table | 12,904 | 22 |
| ps5_serial_rul | table | 11,718 | 17 |
| ps5_reliability_estimates | table | 4,103 | 24 |
| ps5_device_rul | table | 1,536 | 17 |
| ps5_permutation_importance | table | 164 | 5 |
| ps5_enrich_coverage | table | 24 | 9 |
| ps5_cindex_leaderboard | table | 18 | 7 |
| ps5_scoring_runs | table | 4 | 15 |
| ps5_reliability_status | table | 3 | 7 |
| ps5_event_definition | table | 2 | 9 |
| ps5_cox_hazard_ratios | table | 0 | 5 |
| ps5_feature_alignment_audit | table | 0 | 10 |
| ps5_weibull_params | table | 0 | 7 |
| v_ps5_dashboard_ready | view | - | 7 |
| v_ps5_device_360 | view | - | 13 |
| v_ps5_device_rul | view | - | 20 |
| v_ps5_reliability_oos_latest | view | - | 24 |
| v_ps5_serial_dupes | view | - | 10 |
| v_ps5_serial_fanout | view | - | 6 |
| v_ps5_serial_oos_latest | view | - | 16 |
| v_ps5_serial_rul | view | - | 21 |

Full column-level schemas for every object: `docs/reference/RDS_LIVE_INVENTORY_16Aug2026.md`
(generated from the live catalog capture).

**Confirmations, corrections, and one RED FLAG:**
- **`cubic-mars-ps5-daily-scorer` is NOT deployed** (ResourceNotFoundException) — repo-only, as suspected.
- **Prefix mismatch CONFIRMED:** `chicago/ps5/params/` and `chicago/ps5/state/` are EMPTY;
  params + state live under `chicago/ps5/notebook_outputs/<fleet>/` (verified listing).
  Before deploying the scorer: either add a small publish step to the notebook writing
  flat copies to the scorer's expected prefixes, or set the scorer's env prefixes — noting
  the notebook layout nests per-fleet folders while the scorer expects flat `<prefix>/<type>_...` keys.
- CORRECTION to §(d): `ps5_serial_reliability` (12,904 rows) and `ps5_reliability_estimates`
  (4,103 rows) ARE populated — the earlier "dead by schema defect" note is superseded by
  live state; which loader/backfill wrote them is [U]. `ps5_scoring_runs` (4 rows) also
  exists — part of the scorer's table family is already migrated.
- **RED FLAG — served C-index vs the 0.65 floor:** `/ps5/status` returns gates
  concordance_index **0.5906**, tvms **0.5071** with `registry_status:
  broken_champion_selection` (tvms) and `dashboard_ready: false`. As served, PS5 does NOT
  meet the 0.65 gate. This may be stale v1 status rows sitting beside the newer v5.6
  leaderboard (`ps5_cindex_leaderboard`, 18 rows) — reconcile before any client
  conversation. Tracker #53 is now OPEN-RED pending that reconciliation (follow-up below).

---

## Addendum B — SET W self-checks, 16-Aug-2026 late evening [M]

- `{"dry_run": true}` (doubles as the reconciliation report): **EXACT S3-vs-RDS
  parity on every loaded family** — device_rul 1,536 (429+190+917), serial_rul
  11,718 (1,236+1,963+8,519), cindex_leaderboard 18 (6x3), permutation_importance
  164 (64+41+59), enrich_coverage 24 (8x3). Aurora holds exactly one generation —
  delete-then-insert semantics proven by the loader's own reconcile block.
- The loader maps `*_serial_reliability.csv` -> `ps5_serial_rul`: the populated
  legacy tables `ps5_serial_reliability` (12,904) and `ps5_reliability_estimates`
  (4,103) are fed by something OTHER than this loader — writer still `[U]`.
- `cubic-mars-ps5-daily-scorer`: absent from the live function inventory —
  repo-only status re-confirmed a second way.
- W1 (re-paste received) — **#53 RECONCILED: the served C-indexes are stale v1
  rows.** `/ps5/status` serves `ps5_reliability_status` (3 v1-era rows: gates
  0.5906 clean_v1; tvms 0.5071 broken_champion_selection, blocker "#89 MLflow
  CI=nan churn"; validators 0.597, blocker "cox_ph_model.pkl missing /
  .crdownload") — all pre-v5.6 language. The CURRENT v5.6 leaderboard champions:
  GATE 0.67795 (Baseline CoxPH, 13 feats) PASS; TVM 0.79848 (Cox + facility
  frailty) PASS; VALIDATOR 0.64926 (CoxPH perm-selected) — 0.001 UNDER the 0.65
  floor with sd 0.0036, i.e. statistically AT the floor. Actions: re-point or
  retire the status route/table (the v5.6 loader never writes it), and decide
  at-floor waiver vs lift pass for VALIDATOR. #53: OPEN-RED -> AMBER.
