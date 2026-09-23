# PS3 Root Cause: the remaining dependency

DRAFT - for internal review before sending. Not yet sent.

To: Robin, Cubic (ServiceNow / CMDB)
From: Mars-Techs, CUBIC MARS programme
Date: 23-Sep-2026
Subject: PS3 root cause - what is delivered, and the one input still needed

## Summary

PS3 is three layers, not one. Two of them are built and running against Cubic's
own EDW event data and need nothing further from ServiceNow. The third, the
nine-class ServiceNow root-cause taxonomy, is the one that is blocked, and it is
blocked on a ServiceNow-side data dependency rather than on modelling work.

| Layer | Question it answers | Source | Status |
|---|---|---|---|
| Component attribution | Which subsystem failed | Cubic Device Event Matrix, via the EDW event stream | Working |
| Severity | How serious, in service terms | Measured out-of-service episode duration, EDW-native and current | Working |
| Root cause (9-class ServiceNow taxonomy) | Why it failed, in Cubic's own taxonomy | ServiceNow fault and action code reference data | Blocked |

Component attribution is derived from `silver.dim_event_matrix`, which is the
Cubic Device Event Matrix loaded as a reference table, decoded per event code
through `silver.dim_event_type`. Severity was re-based onto measured episode
duration, banded at 60 and 240 minutes, precisely so that it would stop
depending on a ServiceNow join. Both are current as of the latest EDW extract.

## What is blocked, and the evidence

1. `SVN_STAGE.U_FS_FAULT_CODES` and `SVN_STAGE.U_FS_ACTION_CODES` return 0 rows.
   These two tables carry the fault-code and action-code reference data that
   defines the nine-class taxonomy. This is recorded in our build as of
   13-Jul-2026 and has not changed since. More broadly, all 35 `SVN_STAGE`
   tables are empty; the schema exists but the ServiceNow-to-Oracle staging
   load has not run.

2. The ServiceNow incident feed is frozen at 30-May-2026. Nothing after that
   date reaches the platform through the existing path.

3. `silver.incident_root_cause`, which is built from the ServiceNow-derived
   bronze tables, spans 2017-03-17 to 2026-04-11 and has not advanced since.

4. Consequence, measured. On the PS3 production run, across every
   out-of-service episode on all three fleets, `confirmed_root_cause_coverage`
   is 0.000. In the run we can reproduce end to end, as-of 11-Apr-2026, that is
   0 confirmed root causes across 54,239 episodes: GATE 38,448, TVM 5,665,
   VALIDATOR 10,126. The figure is zero, not low.

PS3 does not paper over this. Where no confirmed cause exists, the value is
labelled `model_estimate_not_confirmed` rather than presented as a finding, and
the episode funnel on the dashboard ends in a stop rather than a narrowing bar,
because the source was never configured rather than the data being lost along
the way.

## What is already built and waiting

So that the request below is a plug-in rather than a project:

- The confirmed-root-cause path is already implemented end to end. PS3 links
  incident evidence to an out-of-service episode within a 72-hour tolerance and
  will only call a cause confirmed where an approved taxonomy mapping exists.
  It reads an optional taxonomy export via `PS3_ROOTCAUSE_TAXONOMY_EXPORT`,
  expecting one row per root-cause key with the root cause, its domain, its
  component and an approval flag. Today that input is absent, so the lane runs
  and publishes zero.
- The ServiceNow ingestion path exists: a Table API refresh into raw and bronze,
  with staged, verified and guarded live-promote steps.
- The silver conformed layer is built: `incident_history`,
  `incident_task_ci_link` and the conformed ServiceNow incident table.
- PS3 already publishes its full table set and serving routes, including the
  `label-maturity` route that reports the 0.000 coverage above. The moment the
  input arrives, that number moves without any code change.

## What would unblock it

1. Load `SVN_STAGE.U_FS_FAULT_CODES` and `SVN_STAGE.U_FS_ACTION_CODES`, or
   confirm they are retired and name the replacement objects.
   Enables: the nine-class taxonomy key itself. Without it there is no class
   list to predict.

2. Provide the approved root-cause taxonomy as a governed export: root-cause
   key, root cause, domain, component, approved flag.
   Enables: PS3 to mark a cause as confirmed rather than estimated. This is the
   single highest-value item, and the format is already supported.

3. Resume the ServiceNow incident feed past 30-May-2026, via either the Table
   API path we have built or a scheduled export.
   Enables: incident-to-episode evidence linkage, a linked severity label
   alongside the duration band, and `silver.incident_root_cause` advancing past
   11-Apr-2026.

4. Confirm the CI-to-device join rule. `u_ncs_device_id` is populated on roughly
   2.8 to 3 percent of records, which is too sparse to join on, so we currently
   work around it.
   Enables: attributing a ServiceNow task or CI to the correct physical device,
   which is a prerequisite for per-device root cause rather than per-fleet.

5. Confirm whether validators raise ServiceNow records at all. Validator
   failures do not appear in the availability-event feed by construction; they
   surface only as device out-of-service events.
   Enables: an informed decision on whether a validator root-cause model is
   feasible, or whether validators should stay explicitly out of scope for this
   layer.

Items 1 and 2 unblock root cause. Item 3 additionally improves severity. Items 4
and 5 determine how far the result can be pushed once it is unblocked.

Happy to walk through any of this, or to show the current PS3 tab with the
coverage figure on screen.

---

## Note to the internal reader - check before this goes out

- Episode count. The body cites 54,239 episodes at as-of 11-Apr-2026, because
  that is the run whose outputs are committed to the repository and which could
  be verified line by line. The figure supplied for the 2026-08-29 run was
  51,557. That run's artefacts live in the serving database, not the repository,
  so it could not be confirmed here. Decide which run this memo should cite and
  make it consistent; do not mix the two.
- Table name. Our records say `SVN_STAGE.U_FS_ACTION_CODES`, not `ACTION_CODES`.
  The longer name is used throughout. Worth a check against whatever Robin will
  search for on his side.
- Dating the emptiness. The repository records these tables as empty as of the
  13-Jul-2026 build note, and records all 35 `SVN_STAGE` tables as empty; it
  does not establish a "since June 2026" start date. The memo therefore says
  "recorded as of 13-Jul-2026" rather than asserting a date we cannot evidence.
  Sharpen it only if there is a source for the earlier date.
- The ServiceNow freeze. A refresh notebook dated 11-Sep-2026 exists that would
  move the 30-May-2026 boundary if it has been promoted live. The repository
  does not show whether it has. If it has, blockage point 2 and ask 3 both need
  rewording before this is sent.
- Tone. Ask 4 names a 2.8 to 3 percent fill rate on a ServiceNow field. It is
  accurate and it comes from our own diagnostics, but it is the one line a
  reader could take as criticism. Consider letting "we currently work around it"
  carry more of the weight, or dropping the percentage.
- Scope. This memo deliberately says nothing about PS3 model performance or
  acceptance thresholds. That belongs in the separate quality-gate paper and
  should not be merged into a memo whose purpose is a data dependency.
