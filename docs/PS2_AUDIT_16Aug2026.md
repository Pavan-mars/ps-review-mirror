# PS2 — Complete Audit vs the Target Daily-Run Architecture

**16-Aug-2026.** Audience: the Chicago delivery team. Structure follows PK's audit
request, then verdicts. Evidence tags: `[M <date>]` measured live, `[R]` repo,
`[D]` documented decision, `[U]` unverified.

**The target architecture (PK, 16-Aug):** EventBridge trigger on medallion
completion -> run the PS2 SageMaker notebook on a daily schedule (once Gold is
updated) -> outputs to S3 -> Lambda loader pushes latest S3 outputs to RDS ->
dashboard daily refresh. (PS2 needs a daily RUN, not per-event inference — correct
framing for its cascade/association analytics.)

**Headline verdict:** same shape as PS1 — the S3 -> loader -> RDS -> dashboard half
is built, scheduled daily and verified clean; the notebook itself is MANUAL, there
is no medallion-complete trigger, and one live landmine exists: the notebook still
writes the OLD S3 prefix while the scheduled loader reads the NEW one.

---

## (a) PS2 SageMaker notebooks

All under `notebooks/ps2_cascading_failure/` `[R]`.

| Notebook | Role | Status |
|---|---|---|
| `PS2_Failure_Patterns_v2_5_4_Union_Minutes.ipynb` | CURRENT v2.5 production run family — produced the 47-table export the loader serves | current; manual runs |
| `PS2_Failure_Patterns_v2_5_3_Storage_Safe.ipynb` | prior v2.5 iteration | kept |
| `PS2_Serial_Grain_Analysis_v1_FIXED.ipynb` | serial-grain delivery (21-Jul): component/serial grain + cross-PS tagging | delivered |
| `PS2_SageMaker_MLflow_FeatureStore.ipynb` | earlier SageMaker family | superseded |
| `PS2_Episode_Closure_Diagnostic_v1.ipynb`, `PS2_Overlap_Diagnostic_v2/v3/v4.ipynb` | diagnostics | kept |
| `Chicago_PS2_Cascade_Analysis.ipynb` | early analysis | kept |
| `ps2_overlap_resume_cell.py`, `ps2_v254_union_verify_cell.py` | helper cells | kept |

**There is NO scheduled execution of any PS2 notebook** — every run to date has been
manual in SageMaker `[R + M]`. The daily cadence that exists is the LOADER's, not
the notebook's.

## (b) S3 locations for PS2 outputs

| Path | What | Status |
|---|---|---|
| `chicago/ps2_outputs` (both gold + artifacts buckets granted) | THE prefix the loader reads — migrated 08-Aug from bare `ps2_outputs` (709 objects, 16,397,197 bytes copied, byte-exact) `[M 08-Aug]` | live |
| bare `ps2_outputs` | the ORIGINAL prefix — **the notebook still writes HERE** | landmine, see below |
| `ps2_v25_{category_profile,failure_definition_alignment,failure_label_summary,ps1_label_parity,ps1_model_performance}_audit` | run-quality + PS1-parity evidence (~20 rows/run: pr_auc, recall, brier, parity_rate...) | written every run, NO Aurora tables exist for them — nothing can query how labels were validated `[M 08-Aug]` |

**THE LANDMINE `[M 08-Aug, still open]`:** loader reads `chicago/ps2_outputs`; the
notebook's export env (`PS2_PRODUCTION_EXPORT_PREFIX`) has NOT been repointed. The
next manual run lands at the OLD prefix, where the scheduled loader no longer looks
— and the dashboard freezes at the copied snapshot with NO error, just a
`computed_date` that stops moving. One env var, set before the next run:
`PS2_PRODUCTION_EXPORT_PREFIX=chicago/ps2_outputs`. The notebook's own guard
(EXPORT_PREFIX compared to PRODUCTION_EXPORT_PREFIX) survives the change.

## (c) EventBridge for PS2

| Rule | Schedule (UTC) | State |
|---|---|---|
| PS2 daily LOAD (`cubic-mars-ps2-rds-loader`) | cron(10 7 * * ? *) — 07:10 | ENABLED `[M 08/09-Aug]` |
| Medallion-complete trigger for the notebook run | — | DOES NOT EXIST (not even designed for PS2 yet; the PS1 PutEvents pattern is the template to reuse) |
| Scheduled notebook execution | — | DOES NOT EXIST |

Consequence: "daily refresh" today means the loader re-reads the same S3 snapshot
every morning at 07:10. Because PS2 is on an ENABLED schedule, **never leave PS2
half-migrated or half-repointed overnight** — whatever state it is left in runs
unattended the next morning.

## (d) Loaders, schedulers, dispatchers, handlers, routes for RDS

| Component | Role | Key facts |
|---|---|---|
| `cubic-mars-ps2-rds-loader` (Lambda) | S3 -> Aurora, 47 tables | per-file `computed_date` delete idempotency; last verified dry run: 47 tables, 294,749 rows, 0 refused / 0 errors / 0 skipped `[M 08-Aug]`. Does NOT echo which prefix it swept — the env var is the only authority (`PS2_PREFIX`, read once at module load) |
| IAM shape | differs from PS3's | `ListBucket` on bare bucket ARNs with NO prefix condition; only `GetObject` is path-scoped. A repoint therefore LISTS happily and fails later at first READ — never infer one loader's failure mode from another's `[M 08-Aug]` |
| `cubic-mars-dashboard-api` | routes | ~18 core `/ps2/*` routes + ~25 v2.5 routes via `ps2_v25_routes.py` (`/ps2/v25/*`) `[R]` |

Aurora tables (core + v2.5): `ps2_device_catalog`, `ps2_top_devices`,
`ps2_cascade_paths`, `ps2_cascade_window_summary`, `ps2_window_detail`,
`ps2_subsystem_associations`, `ps2_subsystem_hub_summary/_edges`,
`ps2_network_centrality`, `ps2_phi_matrix`, `ps2_markov_transitions`,
`ps2_conditional_prob`, `ps2_ignition_termination`, `ps2_business_impact`,
`ps2_facility_contagion_summary/_facility`, `ps2_hmm_regimes`,
`ps2_error_codes/_transitions`, `ps2_device_cascades`, `v_ps2_v25_status` + the
v2.5 table family `[R, lineage doc]`.

Note for cross-PS work: `ps2_v25_ps1_label_parity` and
`ps2_v25_ps1_model_performance` are PS2 tables holding a SECOND, independent scoring
of PS1. Nobody has compared them to `ps1_model_performance` — if they disagree,
that is a signal, not noise `[R]`.

## (e) PS2 elements on the dashboard

`V4PS2Overview.jsx` — calls 6 core routes (`/ps2/phi`, `/network`, `/paths`,
`/ignition`, `/status`, `/devices`) + 21 `/ps2/v25/*` routes (clusters,
cross-ps, precursors, deterioration, drift, exposure, governance, label-*,
leadlag, model-performance, oos-trend, repairs, run-quality, serials, topology,
category-profile, definition-alignment...) `[R, V4 source grep 16-Aug]`.

PS2's screen DOES carry a StatusBar with "Analysis as of ..." and the source-extract
end date — the vintage-disclosure pattern PS1 lacks `[R, 16-Aug audit]`.

---

## Verdict vs the target architecture

| Target step | Reality | Verdict |
|---|---|---|
| (a) EventBridge on medallion-complete | Nothing exists for PS2 (PS1's PutEvents design is the reusable template) | MISSING |
| (b) Daily scheduled notebook run after Gold updates | All runs manual; no scheduler chosen (Databricks job vs SageMaker scheduled-notebook vs Processing job — open decision) | MISSING + decision needed |
| (c) Outputs to S3 | Prefix migrated and verified — but the NOTEBOOK still writes the old prefix | RIGHT with a LANDMINE (1-line fix) |
| (d) Lambda -> RDS | Loader live, daily 07:10, clean dry-run evidence | RIGHT |
| (e) Dashboard daily refresh | V4 tab fully wired, vintage disclosed | RIGHT (content static until (a)-(b) exist) |

Also open, cheap and valuable: create the five `ps2_v25_*_audit` Aurora tables so
each run's label-validation evidence becomes queryable instead of stranded in S3.
