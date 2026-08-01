# Wiping the PS1 & PS3 RDS tables before the new runs land
CUBIC MARS — Chicago / CTA-Ventra · 26 Jul 2026

I have no direct database access, so this is built as something you invoke. It
is deliberately **not** a migration: `migrate()` runs on every deploy, and a
DELETE living inside it would silently wipe production every time someone
shipped a schema change.

---

## The one command

**Step 1 — dry run. Changes nothing, tells you exactly what would go.**

```bash
aws lambda invoke --function-name cubic-mars-dashboard-api \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"purge","scope":"both","city":"CHI","dry_run":true}' \
  /dev/stdout
```

Read `rows_before` and `would_delete_rows`. Check `tables_absent` — anything
listed there simply does not exist in this database and is skipped.

**Step 2 — execute.** The confirm token must match the scope and city exactly.

```bash
aws lambda invoke --function-name cubic-mars-dashboard-api \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"purge","scope":"both","city":"CHI","dry_run":false,"confirm":"WIPE-BOTH-CHI"}' \
  /dev/stdout
```

`scope` can be `ps1`, `ps3` or `both` — token follows it (`WIPE-PS1-CHI`,
`WIPE-PS3-CHI`, `WIPE-BOTH-CHI`).

You get back `rows_deleted`, `rows_after`, `total_deleted` and
`non_empty_after`. **`non_empty_after` should be `{}`** — anything in it is a
table that still holds rows and needs a look.

---

## How you can be sure only PS1 and PS3 are touched

The database also holds PS2, PS4, PS5 and reference tables. Four independent
things stop this reaching them, and the last one is a measurement rather than a
promise.

**1. There is exactly one DELETE statement in the entire handler.**

```
handler.py:949:   c.run(f"DELETE FROM {tb} WHERE city_id=:c", c=city)
```

`{tb}` can only come from `PURGE_TABLES`, a hardcoded Python list of 28 names.
There is no `information_schema` enumeration in the delete path, no
`LIKE 'ps%'`, no `DROP` and no `TRUNCATE` anywhere in the file — grep it.

**2. A prefix guard that runs before any statement.** Every target must start
with `ps1_` or `ps3_`, or be one of the two explicitly opted-in tables. If a
future edit pastes a PS2/PS5 table into the list, the whole action refuses:

```
400  REFUSING TO PURGE: ['ps5_device_rul'] are outside the PS1/PS3 scope this
     action is allowed to touch (prefixes ['ps1_', 'ps3_'], plus the explicitly
     opted-in ['ml_batch_load_audit', 'servicenow_staging']). No statement was executed.
```

This fires in dry run as well, so you would see it before ever passing a token.

**3. The dry run lists every table by name.** `tables_to_delete` in the response
is the complete, explicit set — read it before you execute. Nothing is inferred
at runtime.

**4. Witness tables: PS2/PS4/PS5 are counted before AND after, and diffed.**
This is the part that makes it provable. The response carries:

| Field | What it tells you |
|---|---|
| `witness_tables_untouched` | every table in the database that is **not** in the delete list — PS2, PS4, PS5, `dim_station`, `cities`, and anything else |
| `witness_rows_before` | their exact row counts before the purge |
| `witness_rows_after` | their exact row counts after |
| `witness_drift` | any table whose count changed — **should be `{}`** |
| `witness_verified_unchanged` | `true` only if `witness_drift` is empty |

So you do not have to trust the target list. If a single row moved in a PS5
table, it appears in `witness_drift` by name, and the note in the response says
so in words.

Verified against a simulated database containing `ps2_device_catalog`,
`ps2_cascade_windows`, `ps2_hmm_regimes`, `ps4_alerts`, `ps5_device_rul`,
`ps5_serial_health`, `dim_station` and `cities`:

```
tables_to_delete           : 28  (all ps1_/ps3_: True)
every DELETE target prefix : ['ps1_', 'ps3_']
witness_verified_unchanged : True
witness_drift              : {}
scope guard vs a PS5 table : 400, nothing executed
```

---

## Four more safety properties

1. **Confirm token.** `dry_run` defaults to **true**. Executing needs both
   `dry_run:false` *and* `confirm` matching `WIPE-<SCOPE>-<CITY>`. A missing or
   mistyped token is a 400, not a wipe.
2. **City-scoped, never TRUNCATE.** Every PS1/PS3 table carries `city_id`, so
   this is `DELETE ... WHERE city_id = :c`. Boston, LA and TOC rows in the same
   tables are untouched. `TRUNCATE` would have taken every tenant.
3. **Existence-checked.** `to_regclass()` is consulted first. Four PS3 tables
   (`ps3_severity_predictions`, `ps3_severity_summary`, `ps3_severity_drivers`,
   `ps3_device_metrics`) are created by migrations `03`/`06`, which are in the
   deployed Lambda package but not in the repo checkout — so they are reported
   as `absent` where missing rather than aborting the run.
4. **Transactional.** `BEGIN` / `COMMIT`, with `ROLLBACK` on any error. Either
   every listed table is cleared or none is — no half-purged database.

Unit-tested against a fake connection: dry run deletes nothing; execution
without a token refuses; a wrong-city token refuses; `scope:"ps1"` touches only
`ps1_*`; missing tables are skipped without aborting; `BEGIN`/`COMMIT` present;
every `DELETE` carries the city filter.

---

## What gets cleared

**PS1 — 15 tables.** Serving grain first, then model metadata:
`ps1_failure_predictions`, `ps1_serial_predictions`, `ps1_inference_runs`,
`ps1_explainability`, `ps1_station_summary`, `ps1_risk_trend`, `ps1_risk_bands`,
`ps1_failure_summary`, `ps1_leaderboard`, `ps1_features`,
`ps1_model_performance`, `ps1_feature_importance`, `ps1_threshold_sweep`,
`ps1_calibration`, `ps1_confusion`.

This is all three device types — Validators, Gates and TVMs — because the wipe
is by `city_id`, not by category. That includes the ~495k VALIDATOR prediction
rows and the stale April backfill whose thresholds matched no registry table.

**PS3 — 13 tables.** The two-head run (`ps3_incident_predictions`,
`ps3_device_predictions`, `ps3_serial_predictions`, `ps3_head_summary`,
`ps3_head_leaderboard`, `ps3_head_class_metrics`, `ps3_head_feature_importance`,
`ps3_leakage_scan`, `ps3_model_runs`) plus the earlier severity model's tables
(`ps3_severity_predictions`, `ps3_severity_summary`, `ps3_severity_drivers`,
`ps3_device_metrics`), which is where the `INC-PS1-000x` bridge test rows and
the hand-seeded metrics live.

## What is deliberately left alone

| Table | Why |
|---|---|
| `ml_batch_load_audit` | The record of what every prior load did. It is how you would prove where the bad rows came from. Opt in with `"include_audit":true` |
| `servicenow_staging` | Operator-created records, not model output. Opt in with `"include_servicenow":true` |
| `dim_station`, `cities` | Reference data the new run needs |
| `ps2_*`, `ps4_*`, `ps5_*` | Out of scope — untouched |
| Views (`v_ps3_*`) | Views hold no rows of their own; they will simply return nothing until the tables refill |

---

## What the dashboard does immediately after

Every PS1 and PS3 panel goes to its named empty state — *"No rows in
`ps3_device_predictions` for this selection"* — rather than showing zeros or a
blank chart. That is the intended behaviour and it is how you will know the wipe
worked. Both tabs fill again the moment the RDS push Lambda loads the new run.

---

## Direct psql alternative

`sql/manual/purge_ps1_ps3_city.sql` does the same thing for a psql session. It
prints BEFORE counts, deletes, prints AFTER counts, and leaves you at a `COMMIT`
you can turn into a `ROLLBACK`.

It lives in `sql/manual/`, outside the numbered sequence, precisely so
`migrate()` cannot pick it up — `migrate()` uses an explicit list of
`sql/NN_*.sql`. **Do not renumber it into that sequence.**

---

## One thing worth doing in the same window

`sql/18` (purge the `INC-PS1-%` bridge test rows) and `sql/19` (the severity
collapse fix) are registered in `migrate()` but may not have been applied yet. If
you are wiping anyway the `sql/18` DELETE becomes a no-op, but **`sql/19` still
matters** — it recomputes `pred_severity_collapsed` from the code and rebuilds
`pct_critical_pred`, and it self-corrects any future run that lands with the old
collapse:

```bash
aws lambda invoke --function-name cubic-mars-dashboard-api \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"migrate"}' /dev/stdout
```

Run the migrate **before** the new data loads, so the fix is in place when it
arrives.

---

## Also corrected in this pass

The PS3 tab's empty state named `ps3_head_scorecard`. The real table is
**`ps3_head_summary`** — `v_ps3_two_head_scorecard` is a view built on it. Fixed,
so the message points at a table that exists.
