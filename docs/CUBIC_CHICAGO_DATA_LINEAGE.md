---
name: chicago-data-lineage
description: "Authoritative end-to-end data lineage for CUBIC MARS Chicago (CTA-Ventra) PS1-PS5: every S3 output path and its file format, which Lambda loader reads it and how it is triggered, which Aurora table it lands in, which API route serves that table, and which dashboard tab and panel renders it. Use for ANY question about where PS1-PS5 data lives, what format it is in, how it reaches RDS, which loader owns a table, which route backs a screen, why a panel is blank, or what breaks if a path/format/table changes. Also use when planning the Boston tenant, standardising S3 prefixes or file formats, adding a new PS output, or auditing the S3 -> Lambda -> RDS -> dashboard chain. Chicago-exclusive; pairs with chicago-oos-contract (event definition, which overrides this on event semantics) and chicago-data-catalog (lakehouse tables upstream of S3)."
---

# CUBIC MARS Chicago — S3 → Lambda → RDS → Dashboard lineage

**Status:** live working document. Verified against the repo on **08-Aug-2026**.
Update it as deployment proceeds; §9 says how.

**Revision 2 (08-Aug-2026):** added §10, source-artifact provenance — the
release point, where retired dashboard generations live, the V2→V4 feature
carry-over check, the machine-readable lineage sources, the deferred quarantine
audit, and the CRLF hazard.

**Scope:** the *serving* chain only — from the moment a notebook writes an
artifact to S3, to the moment a dashboard panel renders it. Upstream lakehouse
tables (bronze/silver/gold in Databricks) are `chicago-data-catalog`'s job. The
event definition is `chicago-oos-contract`'s job and **overrides this document**
wherever they disagree.

---

## 1. The canonical route

```
SageMaker notebook
      │  writes artifacts
      ▼
S3   s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/<prefix>/
      │  read by a per-PS loader Lambda
      ▼
Lambda  cubic-mars-ps<N>-*-loader        (VPC-attached, pg8000, python3.12)
      │  INSERT ... ON CONFLICT
      ▼
Aurora PostgreSQL  cubic-mars-rds-aurora-dev   (db.t3.medium, database `postgres`)
      │  read by one API Lambda
      ▼
Lambda  cubic-mars-dashboard-api          (91 routes in handler.py + v2.5 modules)
      │  HTTP API Gateway  https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com
      ▼
React dashboard  dashboard/src/v4/*.jsx   (mounted at /v4)
```

Account `170202974600`, region `us-east-1`, VPC `vpc-0a7775adc7d382fbb`.

**All five problem statements follow this route.** There is no direct
notebook→RDS path and no direct dashboard→S3 path.

---

## 2. S3 paths and formats — the ground truth

Bucket for every row below is
`cubic-mars-pm-s3-datalake-dev-gold-170202974600` unless stated.

| PS | S3 prefix | Format | Loader Lambda | Pandas layer |
|---|---|---|---|---|
| PS1 | `chicago/gold/device_ps1_cross_wired_daily` | Parquet | `cubic-mars-ps1-rds-push` | ✅ AWSSDKPandas-Python312 |
| PS1 | `chicago/device_ps1_cross_wired_daily` | Parquet | `cubic-mars-ps1-xw-loader` | ✅ |
| PS2 | **`ps2_outputs`** ⚠️ *no `chicago/` prefix* | Parquet | `cubic-mars-ps2-rds-loader` | ✅ |
| PS3 | `chicago/ps3_hardened_remediation/runs` | Parquet | `cubic-mars-ps3-v2-loader` | ✅ |
| PS3 | **`ps3_outputs`** ⚠️ *no `chicago/` prefix* | Parquet | `cubic-mars-ps3-v25-loader` | ✅ |
| PS3 | `chicago/ps3/rootcause_outputs` | **CSV** | `cubic-mars-ps3-rc-loader` | ❌ stdlib csv |
| PS3 | `chicago/ps3_deepdive` | Parquet + CSV | `cubic-mars-ps3-inference` | ❌ |
| PS4 | `chicago/ps4`, `chicago/ps4/clustering` | Parquet | `cubic-mars-ps4-rds-loader` | ✅ |
| PS4 | `chicago/ps4/v3` | Parquet | `cubic-mars-ps4-v3-loader` | ✅ |
| PS5 | `chicago/ps5/notebook_outputs` | **CSV** ⚠️ | `cubic-mars-ps5-rds-loader` | ❌ stdlib csv |
| DIM | `chicago/dim/device_serial` | Parquet | `cubic-mars-dim-loader` | ✅ |

**Eight of eleven loaders already attach the managed `AWSSDKPandas-Python312`
layer** (`arn:aws:lambda:us-east-1:<aws-sdk-pandas-acct>:layer:AWSSDKPandas-Python312`).
Only the two CSV loaders and `ps3-inference` do not. Parquet is therefore the
house standard and PS5 is the outlier — see §6.

### 2.1 PS5 artifact inventory (36 files, 2.47 MB, run 08-Aug-2026)

Per fleet folder `gates/` `tvm/` `validators/`:

| File | Format | Loaded? |
|---|---|---|
| `*_device_rul_estimates.csv` | CSV | ✅ → `ps5_device_rul` |
| `*_serial_reliability.csv` | CSV | ✅ → `ps5_serial_rul` |
| `*_cindex_leaderboard_v5.csv` | CSV | ✅ → `ps5_cindex_leaderboard` |
| `*_permutation_importance.csv` | CSV | ✅ → `ps5_permutation_importance` |
| `*_enrich_coverage.csv` | CSV | ✅ → `ps5_enrich_coverage` |
| `*_device_state.parquet` | Parquet | ❌ model internal |
| `*_device_survival_params.json` | JSON | ❌ Weibull shape/scale + Cox coefficients for the scorer |
| `*_serial_params.json` | JSON | ❌ |
| `*_cindex_lift.png`, `*_permutation_importance.png` | PNG | ❌ |

Root: `ps5_rds_load_manifest.json`, `ps5_reliability_v5_summary.json`,
`run_console_log.txt`.

---

## 3. How each loader is invoked

| Loader | Invocation | Idempotency |
|---|---|---|
| `ps1-rds-push` | manual `aws lambda invoke` | DELETE-then-INSERT per run ⚠️ see §5.1 |
| `ps1-xw-loader` | manual | upsert on PK |
| `ps2-rds-loader` | manual | per-file `computed_date` delete |
| `ps3-v2-loader` | manual, `run_id` arg | per-run |
| `ps3-v25-loader` | manual | per-run |
| `ps4-v3-loader` | manual, reads `READY.json` | per-run |
| `ps5-rds-loader` | manual, `dry_run` first | per-table savepoints |
| `dim-loader` | manual | upsert |

**No loader is on a schedule today.** EventBridge for PS1 and PS3 daily
inferencing is a decision taken but **UNVERIFIED as implemented** — check before
claiming the dashboard self-refreshes. Without it, `as_of_date` freezes and
nobody notices for weeks.

`dry_run` on the PS5 loader doubles as the S3-vs-Aurora reconciliation report.
Read it before the real load.

---

## 4. RDS tables by problem statement

Aurora `postgres`, schema `public`. `v_` prefix = view.

**PS1** (35 API routes) — `ps1_failure_predictions`, `ps1_serial_predictions`,
`ps1_station_summary`, `ps1_risk_bands`, `ps1_risk_trend`, `ps1_leaderboard`,
`ps1_confusion`, `ps1_threshold_sweep`, `ps1_calibration`, `ps1_explainability`,
`ps1_features`, `ps1_feature_importance`, `ps1_model_performance`,
`ps1_inference_runs`, `ps1_failure_summary`, plus the `v_ps1_xw_*` cross-wired
view family (`_summary`, `_tiers`, `_drivers`, `_state_mix`, `_causation`,
`_flag_reason`, `_performance`, `_performance_onset`, `_base_rate`,
`_chronic_devices`, `_facility`, `_act_now`).

**PS2** (18 core + ~25 v2.5 routes) — `ps2_device_catalog`, `ps2_top_devices`,
`ps2_cascade_paths`, `ps2_cascade_window_summary`, `ps2_window_detail`,
`ps2_subsystem_associations`, `ps2_subsystem_hub_summary/_edges`,
`ps2_network_centrality`, `ps2_phi_matrix`, `ps2_markov_transitions`,
`ps2_conditional_prob`, `ps2_ignition_termination`, `ps2_business_impact`,
`ps2_facility_contagion_summary/_facility`, `ps2_hmm_regimes`,
`ps2_error_codes/_transitions`, `ps2_device_cascades`, `v_ps2_v25_status`.

**PS3** (23 core + ~19 v2.5 routes) — `ps3_severity_summary/_drivers/_predictions`
(**all three empty**), `ps3_incident_predictions`, `ps3_serial_predictions`,
`ps3_device_predictions`, `ps3_head_summary`, `ps3_v2_*`, `ps3_v25_run_status`,
and the `v_ps3_*` view family (`_latest_run`, `_device_all`, `_serial_all`,
`_device_360`, `_device_risk`, `_serial_risk`, `_category_coverage`,
`_collapse_health`, `_rollup_all`, `_two_head_scorecard`).

**PS4** (12 routes) — `ps4_anomaly_alerts`, `ps4_anomaly_timeline`,
`ps4_cluster_assignments`, `ps4_cluster_summary`, `ps4_cluster_profile`,
`ps4_cluster_quality`, `ps4_weekly_device_summary`, `ps4_weekly_alerts`,
`ps4_weekly_timeline`, `ps4_v3_runs`, `v_ps4_v3_current`, `v_ps4_v3_table_status`.

**PS5** (9 routes) — `ps5_device_rul`, `ps5_serial_rul`,
`ps5_cindex_leaderboard`, `ps5_permutation_importance`, `ps5_enrich_coverage`,
`ps5_reliability_status`, and views `v_ps5_device_rul`, `v_ps5_serial_rul`,
`v_ps5_serial_dupes`, `v_ps5_serial_fanout`.

**Shared / dimension** — `dim_device_station` (18,616 rows, 2,183 facilities all
named), `dim_station` (18 seeded), `dim_device_bus`, `dim_device_serial`,
`dim_device_component`, `cities`, `ml_models`, `ml_batch_load_audit`,
`servicenow_staging`, `v_device_serial`, `v_component_inventory`,
`v_fleet_event_baseline`, `v_device_bus`.

### 4.1 Tables declared but never populated

| Table | Why |
|---|---|
| `ps5_weibull_params` | declared in `01_schema_core.sql`; **no loader writes it.** The Weibull shape/scale exist in `*_device_survival_params.json`, not in any CSV |
| `ps5_cox_hazard_ratios` | same |
| `ps3_severity_summary/_drivers/_predictions` | render as blank panels |
| `ps1_model_performance`, `ps1_feature_importance` | **dropped and recreated empty by `sql/11` on every migrate** |
| `ps1_prediction_explainability` | 26 rows only — per-device driver bars appear for a small minority |
| `ps5_reliability_estimates` (legacy) | `device_type` on an enum never created in Aurora. Loader deliberately targets `ps5_device_rul` instead |
| `ps5_serial_reliability` (legacy) | `as_of_date` + `component_serial_nbr` NOT NULL, export fills neither → 23502. Loader targets `ps5_serial_rul` instead |

---

## 5. Known defects in the chain

### 5.1 PS1 loader DELETE is not category-scoped — **highest severity**
`cubic-mars-ps1-rds-push` deletes by run without `AND device_category = :cat`,
so a gates run wipes the TVM run. Fix before any scheduled inferencing.

### 5.2 Two prefixes sit outside the city partition
`ps2_outputs` and `ps3_outputs` have no `chicago/` prefix. Boston will collide.
Remedy: **copy, verify, then repoint — never move.**

### 5.3 `chicago` is a literal, not derived from `CITY_ID`
`ps3-v2-loader`, `ps4-v3-loader`, `ps1-rds-push` default their roots to a
literal `chicago/…` string. Env-overridable, so nothing is broken today.

### 5.4 `deploy.sh` config drift
The dashboard-api deploy script rewrites memory to 256 and replaces the whole
environment map on every run — that is how `D360_PARALLEL` or `DB_NAME` gets
silently reset. Prefer `aws lambda update-function-code` for code-only changes.

### 5.5 `/ps1/device-360` latency
5.7–7.6 s server-side against a hard **30 s API Gateway cap**; 2 concurrent
→ 10.4 s, 4 → 21.0 s. Cause is 25 sequential round trips at ~220 ms each — not
CPU, RAM or I/O, all three tested and excluded. The parallel path exists but is
disabled (`D360_PARALLEL=0`) because it gave no benefit. Real fix is
`pg_stat_statements` to find which queries dominate.

### 5.6 Two overlapping station dimensions
`dim_device_station` (broad, 2,183 facilities) and `dim_station` (18 seeded).
`/ps1/facilities` FULL OUTER JOINs both — 18 facilities are named only by the
seed table.


### 5.7 Loader roles are prefix-fenced — an S3 move is also an IAM change

Discovered 08-Aug-2026 while repointing PS3 v2.5. Each loader's inline policy
grants `s3:ListBucket` on the bucket **with an `s3:prefix` condition**, and
`s3:GetObject` on the prefix path only:

```json
{ "Action": ["s3:ListBucket"],
  "Resource": "arn:aws:s3:::…-artifacts-…",
  "Condition": {"StringLike": {"s3:prefix": ["ps3_outputs/*", "ps3_replay_outputs/*"]}} }
```

Move data to a new prefix and the loader gets `AccessDenied` on
`ListObjectsV2` — with wording that reads as if the permission is missing
entirely rather than scoped. This is least privilege behaving correctly, not
a misconfiguration, and it means **every prefix migration needs the role
policy extended before the repoint, not after.**

Two mechanics worth knowing:

- `survey()` builds `root = prefix.rstrip("/") + "/"`, so the request carries
  `Prefix=chicago/ps3_outputs/`. IAM `StringLike` lets `*` match the empty
  string, so a `chicago/ps3_outputs/*` condition matches it.
- `aws iam put-role-policy` **replaces the named inline policy wholesale**.
  Always send the complete document and back up the live one first. Same
  failure mode as `aws lambda update-function-configuration --environment`.

**PS2 has the same fence.** Check `cubic-mars-ps2-rds-loader`'s role before
copying 709 objects to `chicago/ps2_outputs`, or the copy completes and the
loader still cannot read it.

Resolved for PS3 on 08-Aug-2026: `chicago/ps3_outputs/*` and
`chicago/ps3_replay_outputs/*` added to both statements, old grants retained
so rollback stays available.


---

## 6. Moving PS5 from CSV to Parquet

**Why bother:** type fidelity (CSV has none — booleans arrive as `"True"`,
NaN as the literal `"nan"`, and `norm()` is the only thing holding that line),
and consistency with the other four problem statements. **Why not today:** the
data is already produced, verified and about to load. Changing the artifact
format of the thing you are loading is the wrong risk at this moment. Schedule
it with the Boston standardisation work.

Five changes, ~1 hour:

1. **Notebook (Cell 3)** — the five serving artifacts change writer:
   ```python
   dev.to_csv(out / f"{sub}_device_rul_estimates.csv", index=False)
   # becomes
   dev.to_parquet(out / f"{sub}_device_rul_estimates.parquet", index=False)
   ```
   Same for `_serial_reliability`, `_cindex_leaderboard_v5`,
   `_permutation_importance`, `_enrich_coverage`.

2. **Manifest** — `main()` records relative paths; the `.parquet` extension
   propagates automatically. Verify `ps5_rds_load_manifest.json` lists the new
   names before loading.

3. **Cell 7 (publish)** — walks the output folder and uploads whatever it finds.
   **No change needed.**

4. **Loader `handler.py`** — replace the reader:
   ```python
   def read_csv(bucket, key):
       body = _s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8-sig")
       return list(csv.DictReader(io.StringIO(body)))
   # becomes
   def read_table(bucket, key):
       import pandas as pd, io as _io
       body = _s3.get_object(Bucket=bucket, Key=key)["Body"].read()
       return pd.read_parquet(_io.BytesIO(body)).to_dict("records")
   ```
   Update the file-suffix matching from `.csv` to `.parquet`. **Keep `norm()`** —
   parquet NaN still stringifies to `"nan"`.

5. **`deploy.sh`** — attach the layer the other eight already use:
   ```bash
   --layers arn:aws:lambda:us-east-1:$AWS_SDK_PANDAS_ACCT:layer:AWSSDKPandas-Python312:<ver>
   ```
   Copy the resolution block verbatim from `cubic-mars-ps4-v3-loader/deploy.sh`,
   which already solves the cross-account layer lookup that cost several rounds
   on PS2 and PS4.

**Reversible:** keep the CSV writers alongside the parquet ones for one run and
compare row counts through `/ps5/device-rul` before deleting them.

---

## 7. RDS table → API route → dashboard element

| Dashboard tab | Component file | api helper / feed | Route | Backing table |
|---|---|---|---|---|
| **Failure Prediction (PS1)** | `V4PS1Overview.jsx` | `api.predictions` | `/ps1/predictions` | `v_ps1_predictions_xw`, `dim_station` |
| › Location | | `api.stations` | `/ps1/station-summary` | `ps1_station_summary` ⋈ `dim_device_station` |
| › How we know | | `api.leaderboard`, `api.confusion`, `api.thresholdSweep` | `/ps1/leaderboard`, `/ps1/confusion`, `/ps1/threshold-sweep` | `ps1_leaderboard`, `ps1_confusion`, `ps1_threshold_sweep` |
| › cross-wired family | | `api.xw*` (12) | `/ps1/xw-*` | `v_ps1_xw_*` |
| **Cascades (PS2)** | `V4PS2Overview.jsx` | inline `getRows` | `/ps2/v25/*` (25) + `/ps2/phi`, `/network`, `/paths`, `/ignition` | `ps2_*`, `v_ps2_*` |
| › Location | | `clusters` | `/ps2/v25/clusters` | v2.5 cluster tables |
| **Root Cause (PS3)** | `V4PS3Overview.jsx` | inline `getRows` | `/ps3/v25/*` (19) | `ps3_v25_*`, `v_ps3_*` |
| › Location | | `facilities` | `/ps3/v25/facility-rollup` | `ps3_v25_facility_rollup` |
| **Anomaly (PS4)** | `V4PS4Overview.jsx` | `api.weekly`, `api.alerts`, `api.clusterQuality` … | `/ps4/weekly*`, `/ps4/cluster-*`, `/ps4/v3-status` | `ps4_weekly_*`, `ps4_cluster_*`, `v_ps4_v3_*` |
| › Location | | `api.facility` | `/ps4/weekly-facility` | `ps4_weekly_device_summary` |
| **Remaining Life (PS5)** | `V4PS5Overview.jsx` | `api.deviceRul` | `/ps5/device-rul` | `v_ps5_device_rul` → `ps5_device_rul` |
| › Location *(new 07-Aug)* | | `api.deviceRul` (client rollup) | `/ps5/device-rul` | same — no new endpoint |
| › Components | | `api.serialRul`, `api.componentSummary` | `/ps5/serial-rul`, `/ps5/component-summary` | `v_ps5_serial_rul` → `ps5_serial_rul` |
| › Model quality | | `api.leaderboard`, `api.importance` | `/ps5/leaderboard`, `/ps5/importance` | `ps5_cindex_leaderboard`, `ps5_permutation_importance` |
| › How we know | | `api.coverage`, `api.summary` | `/ps5/coverage`, `/ps5/summary` | `ps5_enrich_coverage`, `v_ps5_device_rul` |
| **Device 360** | `V4Device360.jsx`, `V4Device360Popup.jsx` | `api.device360` | `/ps1/device-360` | 25 queries across PS1–PS5 + `dim_*` |
| *(all tabs)* Location names | `V4Locations.js` | `api.facilities` | `/ps1/facilities` | `dim_device_station` ⊍ `dim_station` |
| *(all tabs)* Evidence wording | `V4Evidence.js` | — | consumes `/ps1/device-360` | `cross_ps` block |

**Two shared front-end modules deliberately have no endpoint of their own:**
`V4Locations.js` reuses `/ps1/facilities` so PS1 is the single authority for
what a place is called; `V4Evidence.js` recomposes `cross_ps` fields into plain
sentences so the popup and the full tab cannot drift apart.

---

## 8. Verification commands

```bash
# every table + column + row count, straight from information_schema
aws lambda invoke --function-name cubic-mars-dashboard-api \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"catalog"}' catalog.json --region us-east-1

# schema migration (55 sql files; tolerates "already exists", skips absent)
--payload '{"action":"migrate"}'

# PS1/PS3 table existence + column list
--payload '{"action":"inspect"}'

# location dimension health
curl -s "$API/ps1/facilities?city=CHI" | python3 -c \
 "import sys,json;d=json.load(sys.stdin);print(len(d),'facilities');\
print(sum(1 for r in d if r['source']=='dim_station'),'seed-only')"
```

Expected today: **2,210 facilities, 18 seed-only.**

---

## 9. How to keep this current

Update when any of these change, and bump the date at the top:

- a notebook writes to a new S3 prefix, or changes an artifact's format
- a loader is added, retired, or repointed
- a table is created, renamed, or its feed changes
- an API route is added or its backing table changes
- a dashboard panel is wired to a different feed
- anything in §5 is fixed — move it to a "Resolved" list with the date rather
  than deleting it, so the history stays readable
- a dashboard generation is retired, or an archived screen is revived (§10.2)
- one of the seven open V2→V4 panel gaps is closed or formally dropped (§10.3)
- the quarantine folder is audited and removed (§10.5)
- `.gitattributes` is added, closing the CRLF hazard (§10.6)

**Record numbers with their date.** Row counts and facility totals in this
document are observations, not constants.


---

## 10. Source-artifact provenance — where the code behind this chain lives

Added 08-Aug-2026, at the point the V4 dashboard was committed. This section
exists because the *code* implementing the lineage above was, until that day,
largely untracked — and reconstructing which version of a screen or a loader
was live is a lineage question as much as which table feeds which panel.

### 10.1 The release point

| | |
|---|---|
| Repo | `Chicago-Ventra-Mars-Cubic-Analysis`, branch `feat/dashboard-v2` |
| Baseline before this work | `40fabf5` (03-Aug-2026) |
| Release candidate | `a897a35`, tagged **`v4.0.0-rc1`** |
| ECR image tag to use | the short SHA of the tagged commit |

Nine commits. Before this, `dashboard/src/v4/` — every screen §7 describes —
was **entirely untracked**, as were `runtimeConfig.js`, `public/config.js` and
`docker-entrypoint.sh`, without which the container cannot boot.

### 10.2 Retired dashboard generations

| Generation | Status | Where it lives now |
|---|---|---|
| V1 | still present, still routed | `dashboard/src/pages`, `components`, `context`, `data` |
| V2 | retired | `dashboard/archive/v2-final/` (18 files) |
| V3 | retired | `dashboard/archive/v3-final/` (4 files) |
| V4 | **current** | `dashboard/src/v4/` (19 files) |

`dashboard/archive/` is inert: nothing under `src/` imports it, so it is not
bundled, routed or built. It is version-control ballast, deliberately kept.

**A trap worth recording.** The V2 first committed as "removed" was the 03-Aug
version. The V2 actually on disk had moved a long way past it and had never
been committed — PS3Overview 1,341 → 1,850 lines, PS2Overview 1,110 → 1,623,
Device360 297 → 622, plus `PS5Overview.jsx` (54 KB) that had never been tracked
at all. `dashboard/archive/v2-final/` holds the **real** final V2. If you ever
need to see how a panel behaved before V4, use the archive, not
`git show <pre-removal-sha>:dashboard/src/v2/...`.

### 10.3 Feature carry-over from V2 to V4 — what was checked

Endpoint coverage is **complete**: V4 calls all 99 endpoints V2 called, and adds
`/ps1/facilities`. No feed in §4 or §7 lost its consumer.

Panel coverage is **not** complete. Twelve V2 panels have no V4 equivalent.
Three were removed by explicit client instruction and two were renamed
(`Facility rollup` → `Location rollup`, `Top precursor patterns` →
`Precursor patterns`). The remaining seven are open:

| Panel | Origin | Note |
|---|---|---|
| Physical identity | V2 AnalyseModal | Device 360 organises by problem statement instead |
| Failure risk | V2 AnalyseModal | ” |
| Observed root cause | V2 AnalyseModal | ” |
| Fault cascade profile | V2 AnalyseModal | ” |
| Behaviour vs its peers | V2 AnalyseModal | ” |
| **Raise a work order** | V2 AnalyseModal | **no equivalent anywhere in V4** |
| Component age against device risk | V2 PS1Overview | endpoint still called, nothing renders it |
| What is fitted across the fleet | V2 PS1Overview | endpoint still called, nothing renders it |

`V4AnalyseModal.jsx` and `V4DeviceBrief.jsx` were written for V4 and never
imported. They are preserved at `dashboard/archive/v4-unwired/` so the decision
can be revisited without rewriting them.

### 10.4 Machine-readable lineage sources

Three files under `docs/reference/` are the parsed inputs behind §4 and §7 and
should be regenerated whenever the chain changes:

| File | Contents |
|---|---|
| `endpoint_to_table_xref.json` | API route → the Aurora tables and views it reads |
| `loader_to_target_map.json` | Lambda loader → bucket, S3 prefix, target tables |
| `rds_schema_snapshot.json` | table → column list, at the time of capture |

They are snapshots, not live truth. `{"action":"catalog"}` in §8 is live truth.

### 10.5 The quarantine folder — audit deferred by decision

`_to_delete/` at the repo root holds **117 files, ~13 MB**, gitignored.
Composition, verified 08-Aug-2026 by hashing every file against every blob in
git history (CRLF-normalised, so line endings cannot mask a match):

| Count | What |
|---|---|
| 112 | intermediate `.bak` / checkpoint snapshots, each superseded by a committed file |
| 3 | build/deploy zips |
| 2 | the CRLF backup manifest and one placeholder text file |

Everything of unique value has already been lifted out into
`dashboard/archive/` and `docs/reference/`. The loose deploy zip was checked
file-by-file: its `handler.py` and `sql/48_ps1_state_evaluable_day.sql` are
**older** than the repo copies (the repo carries revision 2 of sql/48; the zip
carries revision 1), so it represents no drift and holds nothing unique.

**Decision (PK, 08-Aug-2026): leave `_to_delete/` in place until after go-live;
a detailed audit follows then.** It is gitignored, excluded from the Docker
build context, and costs nothing but disk. Do not delete it as part of a
pre-deploy cleanup.

### 10.6 The CRLF hazard — affects anyone editing these files

The repo has **no `.gitattributes`**, and `core.autocrlf` is unset in the repo
config. Git for Windows sets `autocrlf=true` in its *system* config, so a
Windows checkout sees a clean tree while a Linux checkout of the same worktree
sees hundreds of files "modified" with zero content change.

On 08-Aug-2026, **175 of 250 apparently-changed files were LF→CRLF churn, not
work.** They were restored to their committed bytes before committing; without
that, the release commit would have carried a ~50,000-line diff of pure noise
across `sql/silver/`, `docs/`, `notebooks/` and `skills/`, and destroyed
`git blame` on all of them.

To separate real changes from churn — note the flag is honoured by `--numstat`
but **ignored** by `--name-only`, which is why the naive check reads as "all
files changed":

```bash
git diff --name-only | wc -l                    # every touched file
git diff --ignore-cr-at-eol --numstat | wc -l   # files with REAL changes
```

Permanent fix, deferred to after go-live: add a `.gitattributes` with
`* text=auto eol=lf`.

### 10.7 PS3 v2.5 provenance — the dashboard is showing a REPLAY-labelled run

Established 08-Aug-2026 and **not yet resolved**.

`ps3_outputs` (the production prefix) held **zero objects**. The live loader
was pointed at `ps3_replay_outputs` because `deploy.sh` overrode `handler.py`'s
correct default. Aurora's PS3 v2.5 tables were confirmed to hold replay run
`6a7002b0-a0fc-41f8-bb0f-1ad5a5358edf` — manifest row counts match the
database exactly (54,239 / 54,239 / 2,806 / 366).

**The numbers are sound.** `RUN_MODE` appears in seven places in the V26
notebook — the prefix redirect, a healthcheck row, and five metadata fields.
It never branches the computation. A PRODUCTION run of the same revision
(`source_first_ps1_ps2_ps3_label_aligned_v26`) at the same
`DATA_AS_OF_DATE=2026-04-11` produces the same figures. What is wrong is the
label, not the data.

State as of 08-Aug-2026:

| | |
|---|---|
| `chicago/ps3_outputs` | holds a verified copy of run `6a7002b0` (21 objects, 9.3 MB) |
| Loader IAM | extended, both prefixes granted |
| Dry run from new prefix | **passes** — 20/20 tables, 117,377 rows, 0 errors |
| Live `PS3_PREFIX` | parked back at `ps3_replay_outputs` |
| Committed `deploy.sh` | sets `chicago/ps3_outputs` |

Decision taken: re-run the notebook with `PS3_RUN_MODE=PRODUCTION` writing to
`chicago/ps3_outputs`, rather than promote the replay run. The loader picks
the newest **complete** run by `(computed_date, run_id)` — run ids are epoch
prefixed — so a production run supersedes the copy automatically, and a
partial one is skipped rather than half-loaded.

Unrelated to provenance, and true of a production run too:
`ps3_v25_prediction_explainability` holds **3 rows** across 2,806 devices.
Per-episode explanations will be blank for effectively the whole estate.

### 10.8 PS2 city-prefix migration — COMPLETE (08-Aug-2026)

Done and verified end to end. PS2 now reads `chicago/ps2_outputs`.

| Step | Result |
|---|---|
| Copy `ps2_outputs` → `chicago/ps2_outputs` | **709 objects, 16,397,197 bytes** — exact match to source |
| IAM (`cubic-mars-ps2-rds-loader-role-dev`) | `chicago/ps2_outputs/*` added on both buckets; originals retained |
| Live `PS2_PREFIX` | `chicago/ps2_outputs` |
| Dry run | 47 tables, **294,749 rows, 0 refused, 0 errors, 0 skipped** |

**PS2's IAM differs from PS3's and fails differently.** PS3 fenced `ListBucket`
with an `s3:prefix` condition, so it failed instantly at `survey()`. PS2 grants
`ListBucket` on the bare bucket ARNs with **no condition**; only `GetObject` is
path-scoped. So a PS2 migration lists the new prefix happily and fails later, at
first read. Do not assume one loader's failure mode predicts another's — read
each policy.

PS2's unconditioned `ListBucket` on two whole buckets is looser than PS3's and
should be tightened, but **not** as part of a migration: adding a prefix
condition to a role whose full access pattern has not been traced is how a
loader breaks unattended.

**Still open — the notebook has NOT been repointed.** The loader reads
`chicago/ps2_outputs`; the notebook still writes `ps2_outputs`. Harmless while
nobody runs PS2, but the next run lands where the loader no longer looks and the
dashboard freezes at the copied snapshot **with no error** — just a
`computed_date` that stops moving. Set before the next run:

```
PS2_PRODUCTION_EXPORT_PREFIX = chicago/ps2_outputs
```

The notebook guard survives it: `EXPORT_PREFIX` is compared against
`PRODUCTION_EXPORT_PREFIX`, both from environment, so they stay equal.

**PS2 is on an ENABLED daily rule (07:10 UTC)** — unlike PS3, which has none. A
half-finished PS2 repoint runs unattended; a half-finished PS3 one does not.
Never leave PS2 mid-migration overnight.

### 10.9 Five PS2 audit outputs have no Aurora table

Surfaced by the migration dry run; **pre-existing, not caused by it.**

| S3 prefix | Rows |
|---|---|
| `ps2_v25_category_profile_audit` | 5 |
| `ps2_v25_failure_definition_alignment_audit` | 4 |
| `ps2_v25_failure_label_summary_audit` | 4 |
| `ps2_v25_ps1_label_parity_audit` | 3 |
| `ps2_v25_ps1_model_performance_audit` | 4 |

Twenty rows carrying `quality_status`, `run_disposition`, `parity_rate`,
`pr_auc`, `recall`, `brier_score`, `label_positive_rate` — the run-quality and
PS1-parity evidence. It is written to S3 every run and never reaches the
database, so nothing can query how the labels were validated. Worth a table pair
after go-live.

(`_runs`, `charts` and `ps2_v2_run_quality` also report `no_target` and are
expected — control prefixes and empty.)

### 10.10 Two AWS CLI calls that replace rather than update

Both bit during this migration:

- `aws lambda update-function-configuration --environment` — replaces the whole
  environment map.
- `aws iam put-role-policy` — replaces the named inline policy entirely.

Always send a complete document and back up the live one first. And **derive the
value inside the command** rather than depending on a file written earlier: a
file saved from `--query 'Environment.Variables'` has no `Variables` wrapper and
is rejected, while a malformed-but-valid document would apply silently.

