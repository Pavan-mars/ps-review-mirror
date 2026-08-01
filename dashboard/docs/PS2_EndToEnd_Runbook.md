# PS2 v1 — serial-grain analytics, auto-schema RDS push, ServiceNow dispatcher,
# dashboard: what's built + what's left

**Grain:** device AND serial. **Scope:** cascading-failure chains — subsystem-to-subsystem fault
sequences within a device/time window (Markov, HMM, phi-correlation, conditional probability,
association rules, network centrality, cascade velocity/timing, recurrence, business impact,
ignition/termination, facility contagion), plus chronic-recurrence, cross-PS attribution, and a
cascade cost/severity Sankey as new analysis families beyond the original 31-chart device/city-grain
reference notebook.

---

## 1. What's built (verified — most in the sandbox, the RDS push now verified live)

| # | Artifact | What it does | Verified |
|---|---|---|---|
| 1 | `notebooks/ps2_cascading_failure/PS2_Serial_Grain_Analysis_v1_FIXED.ipynb` | Device+serial grain across the full family taxonomy, plus chronic recurrence, cross-PS attribution, lead/lag timing, cascade Sankey source, CMDB-map extraction | Locally verified 2026-07-21; **first live production run confirmed 2026-07-24** (run_id `c56095fa-26a9-4bf9-8426-cfa7610494ed`) |
| 2 | `cubic-mars-ps2-rds-push` Lambda (S3-manifest-triggered, auto-ADD-COLUMN / CREATE-TABLE, idempotent per `grain,computed_date,run_id`) | Loads notebook's S3 Parquet+manifest exports into RDS, evolving schema automatically | **Verified live 2026-07-26** (this session) — all 5 tables for the `c56095fa` run confirmed with exact expected row counts: `ps2_recurrence_serial` 4522/4522, `ps2_chronic_recurrence_serial` 4522/4522, `ps2_leadlag_timing_device` 80/80, `ps2_leadlag_timing_serial` 2505/2505, `ps2_conditional_prob_serial` **89990/89990** |
| 3 | `dashboard/src/components/tabs/PS2RichAnalytics.jsx` | CorrelationHeatmap, ConditionalProbGrid, MarkovView, HMMRegimePanel, ErrorCodePanel, DeviceCatalog+drilldown, `PS2ServiceNowButton` (real dispatcher) | All 7 originally-planned dashboard components — **already built and wired**, confirmed by source read 2026-07-26 |
| 4 | `dashboard/src/components/tabs/PS2SerialGrainAnalytics.jsx` | 11 more serial-grain panels: suppression summary, chronic recurrence, cascade-day recurrence, lead/lag timing, association rules, business impact+velocity, cross-PS attribution, cascade Sankey, subsystem network centrality, ignition/termination, facility contagion | Wired as the "Serial-Grain & New Analytics" sub-tab of `PS2CascadingFailureTab.jsx` |
| 5 | `cubic-mars-dashboard-api`'s `/ps2/servicenow/create-incident` + `/ps2/servicenow/status` routes | Real ServiceNow incident-creation dispatcher against `ctsdev2cubic` incwowot, `ps2_device_cmdb_map` lookup, `servicenow_incidents` audit table | Per code comment in `api.js`: **verified end-to-end 2026-07-22** — got a real `401` back from `ctsdev2cubic`, confirming the network path + request shape are correct |

**Bottom line:** the dashboard build-out and ServiceNow dispatcher wiring that this repo's standing
plan scoped as "Phase 3/4, still to build" **turned out to already be done** — found by reading the
actual source this session (2026-07-26), not by re-doing the work. The remaining gaps are narrower
than the plan assumed (see below).

---

## 2. What's actually left (the real gap)

1. **`ps2_device_cmdb_map` looks unpopulated.** The `useServiceNowCmdbMap` hook's code comment in
   `api.js` says plainly: if `apiPS2SerialMetric(city, 'cmdb')` returns `[]`, the map is empty and
   *every* device's ServiceNow button stays disabled/unresolvable — regardless of whether credentials
   are live. This blocks the whole dispatcher chain end-to-end. **Action:** confirm the live row count
   (query below); if zero, re-run the notebook's CMDB-extraction cell against
   `bronze.cta_servicenow_cmdb_ci` and let the same S3→push-Lambda path populate it.
2. **ServiceNow live credentials not yet provisioned.** `cubic-mars-secret-servicenow-dev` returned a
   real `401` in the 2026-07-22 test — the wiring is right, the creds aren't live yet. This is an
   operational action for you/your ServiceNow admin, not something I can do from here.
3. **Lambda timeout headroom is tighter than it looks.** Even after raising `cubic-mars-ps2-rds-push`'s
   timeout 120s→600s (this session), the *first* attempt at the `c56095fa` reload still used the full
   600s and timed out — only the automatic retry (S3 async-invoke retry), which ran against an
   already-emptied table, finished in 573.8s. Worth a fast-follow: raise toward the 900s Lambda max,
   or optimize the loader's batch/COPY insert, rather than relying on the retry margin.
4. **Explicitly parked by you, not touched:** 3 uncommitted files (PS5 reliability engine script, this
   PS2 serial-grain notebook's local edits, `device_ps1_daily__create.sql`) and the pre-existing
   `CityOverviewTab.jsx` NaN/empty-pie bug.

---

## 3. How to verify each once addressed

**CMDB map row count** — run in the SageMaker Studio notebook (reconnect pattern, same as the RDS
fixes this session):
```python
cur.execute("SELECT COUNT(*) FROM ps2_device_cmdb_map")
print(cur.fetchone())
```
If `(0,)`, the notebook's CMDB cell needs a run; if non-zero, spot-check a few `device_id -> cmdb_ci_sys_id`
rows resolve to real CMDB records before trusting the dispatcher end-to-end.

**ServiceNow dispatcher** — once credentials are live, open the dashboard's PS2 tab → Device Catalog
→ select a row → the `PS2ServiceNowButton` should flip from "ServiceNow Integration awaited" to an
actual `INC#` on click, and `GET /ps2/servicenow/status?correlation_id=...` should reflect it without
a duplicate POST on re-render (idempotency via `u_correlation_id = device|PS2|window`).

**RDS push Lambda timeout margin** — after any future change, re-run the same `aws logs tail
/aws/lambda/cubic-mars-ps2-rds-push` pattern used this session against a manifest re-copy, and confirm
the *first* attempt (not just the retry) completes within the timeout.

---

## 4. Honest caveats

- The "Phase 3/4 already done" finding is based on reading source code, not on running the dashboard
  live end-to-end against production data — the CMDB-map and credentials gaps above are exactly the
  reason a fully live click-through hasn't been possible yet.
- The 15 original device-grain `ps2_*` tables still load via the older hand-run-backfill-SQL path; the
  new `cubic-mars-ps2-rds-push` Lambda only owns the new serial-grain/cross-PS/CMDB tables unless a
  full cutover is requested later.
- `servicenow_incidents` audit rows land in RDS (not Databricks Unity Catalog) so the dashboard's
  synchronous flow can read them directly — flag if a Unity Catalog mirror is wanted for governance
  reporting later.
