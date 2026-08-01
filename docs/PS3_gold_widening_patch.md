# PS3 gold widening — patch for `sql/gold/device_ps3_incident__create.sql`

Applies to the **severity** head only. The root-cause head moves to the new
`gold.device_ps3_oos_component` spine (separate file).

## Why

`gold.device_ps3_incident` keeps **34,612** of `silver.incident_root_cause`'s
**375,533** rows — it discards **91%** of its own source. Two filters do it:

```sql
WHERE AE_FAILURE_LEVEL IN (1,2,3,4,5,16)     -- hardcoded "cat-2 hardware faults"
  AND transit_day >= '2023-07-01'
```

The 26-Jul run had TVM severity at macro-F1 **0.478** against a 0.55 floor, with a
train-validation gap of 0.330. Sparsity is the most likely first-order cause.

## Change 1 — replace the hardcoded level list with a dimension join

`silver.dim_failure_level` already encodes this properly:

```
failure_level | description | metric_category | metric_category_name
              | is_device_fault BOOLEAN | is_operational BOOLEAN | severity_ordinal INT
```

**Before** (in the `all_incidents` CTE):

```sql
    FROM mars_dev.silver.incident_root_cause
    WHERE ...
      -- FIX 8 (updated 2026-06-23): levels 4/5/16 are real hardware faults
      AND AE_FAILURE_LEVEL IN (1,2,3,4,5,16)
```

**After:**

```sql
    FROM mars_dev.silver.incident_root_cause irc
    -- FIX 10 (2026-07-26): the hardcoded level list is replaced by the
    -- dim_failure_level contract. Levels 6/98/99 were excluded as
    -- "operational, not hardware" -- is_operational encodes exactly that, and
    -- is_device_fault encodes the inclusion rule. Using the dimension means a
    -- new failure level is classified automatically instead of silently
    -- dropping out of PS3 until someone edits this IN-list.
    JOIN mars_dev.silver.dim_failure_level dfl
      ON dfl.failure_level = irc.AE_FAILURE_LEVEL
    WHERE ...
      AND dfl.is_device_fault = TRUE
      AND COALESCE(dfl.is_operational, FALSE) = FALSE
```

Also carry the ordinal through to the output column list — it is a better-structured
signal than the unordered label, and costs nothing:

```sql
    dfl.severity_ordinal        AS failure_severity_ordinal,
    dfl.metric_category_name    AS failure_metric_category,
```

## Change 2 — relax the date floor

**Before:** `AND transit_day >= '2023-07-01'`
**After:**  `AND transit_day >= '2023-01-01'`

Kept as an explicit floor rather than removed. `incident_root_cause` reaches back to
2017-03-17, but `device_event_enriched` — which supplies every `events_*_prior` /
`oos_onsets_*` feature — only starts **2024-01-01**. Pre-2024 incidents would carry
structurally-zero event features and quietly poison training. **Do not widen past the
event table's start without first re-checking its min date.**

## Change 3 — drop the dead column

`kpi_rule_id` is **0.0% filled** (catalog action item #3). Remove it from the output
list, or the notebook keeps carrying an all-null feature through the NZV filter.

## Optional — exclude chargeable events

`incident_root_cause` carries `is_chargeable BOOLEAN`, so this is now a one-line
filter if wanted. **Not applied by default** — it changes what the severity model
means, and the chargeable population is what the SLA is measured on:

```sql
      -- AND COALESCE(irc.is_chargeable, FALSE) = FALSE
```

## After rebuilding — verify before retraining

```sql
-- row count: expect materially more than 34,612
SELECT mars_device_category, COUNT(*) AS rows, COUNT(DISTINCT device_id) AS devices,
       MIN(transit_day) AS min_day, MAX(transit_day) AS max_day
FROM mars_dev.gold.device_ps3_incident GROUP BY 1 ORDER BY rows DESC;

-- the widened population must not be temporally lopsided; a spike in old years
-- means the extra rows are not comparable to the recent window
SELECT YEAR(transit_day) AS yr, mars_device_category, COUNT(*)
FROM mars_dev.gold.device_ps3_incident GROUP BY 1,2 ORDER BY 1,2;

-- event-feature fill must stay high; near-zero means the date floor went too far
SELECT YEAR(transit_day) AS yr,
       ROUND(100.0*AVG(CASE WHEN events_24h_prior > 0 THEN 1 ELSE 0 END),1) AS pct_with_events
FROM mars_dev.gold.device_ps3_incident GROUP BY 1 ORDER BY 1;

-- grain must stay unique (silver has the 670-dup FAIL; gold dedups downstream)
SELECT COUNT(*) - COUNT(DISTINCT availability_event_id) AS dup_rows
FROM mars_dev.gold.device_ps3_incident;
```

The third query is the one that decides the date floor. If `pct_with_events` collapses
for 2023, move the floor back to 2024-01-01 and take the smaller gain.

## Export both tables to S3

`export_silver_to_s3.py` / `export_gold_to_s3.py` feed the SageMaker notebook, which
has no Spark. Add:

```python
# gold
"device_ps3_oos_component",     # PS3 root-cause head, hardware-OOS spine
# silver
"dim_failure_level",            # severity ordinal + is_device_fault contract
```

landing at `s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/{gold,silver}/...`.

## Order

1. Apply Change 1–3, rebuild `device_ps3_incident`, run the four verification queries.
2. Build `device_ps3_oos_component`, run its own validation block.
3. Export both, plus `dim_failure_level`.
4. Re-run the patched notebook.

Steps 1 and 2 are independent and can run in parallel.
