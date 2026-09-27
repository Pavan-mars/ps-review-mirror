# PS4 (Anomaly & Outlier Analysis) — end-to-end review, 26-Sep-2026

Live chain: `notebooks/ps4_weekly_v3/PS4_SageMaker_Anomaly_Clustering_Weekly_RDS_v3_gp.ipynb` (per-fleet KMeans,
distance to own centroid vs the cluster's training p99, weekly roll-up) → `s3://<artifacts>/chicago/ps4/v3/...` +
READY manifest → `cubic-mars-ps4-v3-loader` → `ps4_weekly_*`, `ps4_cluster_*`, `ps4_v3_runs` (sql/38, 65) →
9 `/ps4/*` routes → `V4PS4Overview.jsx`, `V4PS4Clusters.jsx`; Device-360 via `v_ps4_device_latest`,
`v_ps4_cluster_latest` (sql/57).

## 1. Issues
| # | Severity | Issue | Evidence |
|---|---|---|---|
| P4-1 | high | Device-360 cluster came from `ps4_cluster_assignments`, written only by the daily loader deleted 13-Sep (last run 28-Jul): every 360 page showed a frozen v2 cluster while the PS4 tab shows v3 clusters | sql/57 :93, `_retired/ps4_daily_loader` |
| P4-2 | medium | `v_ps4_device_latest` ignores `pipeline_version` (part of the key); two versions loading the same week → arbitrary pick. The /ps4 views filter to `v_ps4_v3_current`; Device-360 did not | sql/57 :80, sql/38 :387 |
| P4-3 | high | Loader: a dataset with no parquet parts is skipped but the run is still registered current → views mix two runs (same version) or go empty (new version), with `ok` true | v3-loader handler.py :526–531, :596–615 |
| P4-4 | medium | Loader falls back to hard-coded silhouettes from run `ps4-20260728T213153Z` for ANY run whose manifest lacks them; the dashboard ignores `quality_source`, so another run's cluster quality is shown as this run's. A NULL would also render as "0.000" | handler.py :73–78, :563; V4PS4Clusters.jsx :257, :332 |
| P4-5 | high (model) | "Normal" clusters and their p99 outlier thresholds are learned on ALL device-days (`train_daily` has no health filter, cell 8 :47; the notebook's own note at cell 4 :55–75 says so). Fault-loop days inflate the p99, so the detector under-flags. | notebook |
| P4-6 | low | `/ps4/alerts` (+PATCH) and `/ps4/weekly-device` have no dashboard caller; `ps4_anomaly_alerts` was never wired to scoring | grep of dashboard/src |

## 2. Fixes made on `review/ps4`
- **P4-1/P4-2** `sql/69_ps4_device360_v3.sql`: both Device-360 views read the current v3 run; same column signature (tested on PG16 against the original definitions).
- **P4-3** loader rolls the whole load back when any dataset is missing (`allow_partial=true` to override).
- **P4-4** loader stores NULL + `quality_source='not_published'` instead of another run's silhouettes; dashboard shows "--" for NULL and a provenance badge whenever the source is not this run's manifest.
- **P4-6 + legacy generation** removed: the two routes; `sql/20, 25, 33` deleted and `ps4_anomaly_alerts` removed from sql/01; `sql/70_ps4_legacy_drop.sql` drops 16 tables, 10 views and 1 type (no CASCADE, dependents-first, tested on PG16 against the original DDL); legacy producers moved to `_retired/ps4_daily_loader/producers/`. `gold.device_ps4_hourly` (feeds PS1) untouched.
- **P4-5 (decided 26-Sep)** the clustering fit (centroids and each cluster's p99 outlier line) excludes training device-days with an OOS event on the day or within 3 days after (`TRAIN_EXCLUDE_FAULT_DAYS`, `TRAIN_FAULT_LOOKAHEAD_DAYS`, cell 4). The z-score baselines keep the full history on purpose: without OOS days the OOS-rate spread is zero and `signal_oos` could never fire. The run prints and records in the manifest how many days were excluded. Expect more candidate/actionable weeks than before; compare one run with the flag False vs True.

## 3. Client-facing gaps
- Link each actionable week to what happened next (OOS/incident within 14 days) — the only honest precision measure for an unsupervised detector, and the table the client will ask for.
- Per-device trend of weekly anomaly score (the data is in `v_ps4_weekly_device`; `/ps4/weekly` already serves it).

## 4. Local checks
```bash
python -m pytest -q tests/ps4 tests/ps5
```
Verified here: all 5 tests pass; sql/69 + 70 applied cleanly on Postgres 16 over the original objects.

## 5. Deploy order (you run)
1. Deploy `cubic-mars-dashboard-api`; invoke `{"action":"apply_sql","file":"69_ps4_device360_v3.sql"}`.
2. `{"action":"depends","tables":["ps4_cluster_assignments","ps4_anomaly_alerts","ps4_anomalies","ps4_device_day","ps4_runs"]}` → if clean,
   `{"action":"apply_sql","file":"70_ps4_legacy_drop.sql"}`. Check `failed` in the result: `apply_sql` runs statement by
   statement, so a blocked drop leaves just that object in place; re-running is safe (all `IF EXISTS`).
3. Deploy `cubic-mars-ps4-v3-loader`; `{"action":"dry_run"}` then `{"action":"load"}`.
4. Rebuild the dashboard. Check whether the legacy PS4 SageMaker Feature Store group still exists (cost).

## 6. Deployed state, 27-Sep-2026
- sql/69 applied (Device-360 reads the current v3 run); sql/70 applied (27 statements, 0 failed: 10 views, 16 tables, 1 type dropped). All `/ps4/*` routes return 200 after the drop.
- `cubic-mars-ps4-v3-loader` deployed with the all-or-nothing load and the silhouette provenance fix.
- Device-360 PS4 evidence now counts actionable weeks of the current v3 run (last 12 weeks); the always-empty `ps4_anomaly_alerts` read and the misleading "earlier export disagrees" note are gone (Chicago main `fd0df1d`).
- `cubic-mars-ps4-rds-loader` and rule `cubic-mars-ps4-daily-load` confirmed already deleted.

Open: P4-5 comparison run - run the notebook once with `TRAIN_EXCLUDE_FAULT_DAYS=False` and once with `True` and compare candidate/actionable weeks before publishing the `True` run; check whether the legacy PS4 Feature Store group still exists (cost).

## 7. P4-5 comparison, 27-Sep-2026
The notebook now reads `PS4_TRAIN_EXCLUDE_FAULT_DAYS` (default true) and `PS4_ENABLE_S3_PUBLISH` (default true) from the
environment, and CELL 9 prints an outcome check per fleet: for flagged vs unflagged device-days, the share followed by a
hardware OOS in the next 1-7 days (day 0 excluded, so `signal_oos` cannot score itself), lift over the base rate and recall.
Run twice with `PS4_ENABLE_S3_PUBLISH=false` (exclusion false, then true) and compare `[P4-5 compare]` tables; publish the
setting with the higher lift at a workable flag volume.

## 8. P4-5 compare, run 1 (27-Sep-2026, as_of 2026-08-29, TRAIN_EXCLUDE_FAULT_DAYS=false, not published)
| Fleet | device-days | actionable rate | base rate OOS next 1-7d | P(OOS 7d) flagged / not | lift | recall |
|---|---|---|---|---|---|---|
| GATE | 16,756 | 3.1% | 77.4% | 80.8% / 77.4% | 1.04 | 2.1% |
| TVM | 9,262 | 4.2% | 99.8% | 99.5% / 99.8% | 1.00 | 4.4% |
| VALIDATOR | 47,177 | 4.1% | 98.8% | 93.6% / 99.0% | 0.95 | 3.4% |

Finding: almost every device has a hardware OOS within any 7-day window (77-99.8%), so this outcome cannot separate
flagged from unflagged days, and lift is ~1.0 on every fleet. The exclusion setting cannot be chosen on this measure,
so run 2 was not needed to decide. PS4 flags are "behaves unlike its peers", not failure warnings -- which is what the
dashboard already says. Next: an outcome that is not near-certain (e.g. an OOS count above the device's own baseline
in the next week, or ServiceNow incidents), then repeat the comparison.
