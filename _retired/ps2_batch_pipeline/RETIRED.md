# Retired — PS2 batch scoring pipeline

**Retired 23-Sep-2026.** One path per problem statement, on the precedent set by
`_retired/ps3_batch_transform/`.

## What the live PS2 chain is

Two producer notebooks write to `chicago/ps2_outputs/`:

- `notebooks/ps2_cascading_failure/PS2_Failure_Patterns_v2_5_4_Union_Minutes.ipynb` — Spark, 20 tables
- `notebooks/ps2_cascading_failure/PS2_Serial_Grain_Analysis_v1_FIXED.ipynb` — pandas, 27 tables

`cubic-mars-ps2-rds-loader` loads all 47 into Aurora, `cubic-mars-dashboard-api` serves `/ps2/*`,
and `dashboard/src/v4/V4PS2Overview.jsx` renders it. That is the whole path.

## Why this kit went

It was a second, parallel architecture for the same problem, and it was never built: no image was
ever pushed for either of its two Dockerfiles, and the migration its loader depends on
(`dashboard/backfill/07_phase1e_ps2_cascade_scoring.sql`) does not exist anywhere in the repo.

What made it worth removing rather than leaving alone:

- **`deploy_pipeline.py:94` created its EventBridge rule `State="ENABLED"`,** at a default
  `cron(0 9 * * ? *)`. Every other PS2, PS3 and DIC schedule builder in this repo deliberately
  creates DISABLED. Running that deployer would have put a second daily writer against live tables.
- **`pipeline/load_transform_output_to_rds.py` INSERTs into six tables that the live loader owns**
  — `ps2_subsystem_hub_edges`, `ps2_subsystem_hub_summary`, `ps2_subsystem_associations`,
  `ps2_facility_contagion_summary`, `ps2_conditional_prob`, `ps2_hmm_regimes`. Two writers, one
  set of tables, no coordination.
- Four of its own tables (`ps2_markov_device_scores`, `ps2_hmm_device_regime`, `ps2_scoring_runs`,
  `ps2_device_recurrence_scores`) appear in no DDL and on no screen.

## The one thing worth salvaging

`pipeline/refresh_device_state.py` holds `compute_facility_contagion`, which computes contagion
correctly — the share of a facility's cascade-days on which two or more distinct devices cascaded.
The live serial-grain notebook was published a *count* under that name until 23-Sep; its fix was
modelled on this function.

That file also carried a second copy of the `phi_corr` int64 overflow, fixed here on 23-Sep in the
same batch as the producer's, so the arithmetic in this tree is correct as of retirement rather
than left broken on the way out.

If the Markov / HMM / recurrence *scoring* ideas are wanted later, they belong in the serial-grain
notebook alongside the descriptive families, not in a second pipeline with its own container,
state machine and Aurora writer.
