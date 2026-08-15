# Three defects in `mars_dev.silver.device_outage`

**Prepared by** MARS analytics, 2026-08-02
**Source examined** `mars_dev.silver.device_outage`
**Window** 2025-09-13 to 2026-04-11 (210 days), device categories TVM / GATE / VALIDATOR
**Rows examined** 9,872,437 -- the complete population in that window, not a sample

Every figure below is measured. Nothing is estimated or inferred. The queries are in
`notebooks/ps2_cascading_failure/PS2_Overlap_Diagnostic_v4.ipynb` and are read-only.

---

## 1. Near-duplicate outage records

A large share of rows share an exact `(DEVICE_ID, outage_start, duration_min)` with one or
more other rows, differing only by `source_event_id`.

| fleet | rows | rows in a duplicated interval | duplicated intervals | worst single interval |
|---|---|---|---|---|
| GATE | 574,435 | 28,616 | 13,430 | 17 rows |
| TVM | 3,310,008 | **2,749,554** | 903,079 | **189 rows** |
| VALIDATOR | 5,987,994 | 206,127 | 102,585 | 4 rows |

We tested whether these are legitimate per-component records that happen to share timing.
For each duplicated interval we counted distinct `component_subsystem`,
`COMPONENT_SERIAL_NBR` and `failure_level`:

| fleet | differ by subsystem | differ by serial | differ by failure level | **indistinguishable apart from `source_event_id`** |
|---|---|---|---|---|
| GATE | 0.80% | 0.01% | **0.00%** | **99.20%** |
| TVM | 44.07% | 0.32% | **0.00%** | 55.71% |
| VALIDATOR | 1.01% | 0.77% | **0.00%** | **98.99%** |

For gates and validators, ~99% of duplicated intervals carry no distinguishing attribute at
all. TVM is a genuine mix: 44% do name different subsystems.

The ten worst are all TVMs, and all have `outage_start = outage_end`:

```
TVM02102  2025-11-06 12:21:49 -> 12:21:49   189 rows, 2 subsystems, 1 serial, 189 event ids
TVM02202  2025-09-14 09:53:22 -> 09:53:22   138 rows, 2 subsystems, 1 serial, 138 event ids
TVM11801  2026-01-12 04:55:46 -> 04:55:46   129 rows, 2 subsystems, 1 serial, 129 event ids
```

189 distinct event records at one instant on one machine, resolving to two subsystems.

**Question for Cubic:** are these intended as one record per detecting subsystem or per
message, or is this unintended fan-out in the pipeline that builds `device_outage`?

---

## 2. Sentinel values in `outage_end`

**20,991 rows** carry `outage_end = 3000-01-01 00:00:00`.

| fleet | rows with `outage_end` past the window | of which more than a year past |
|---|---|---|
| GATE | 2,913 | 2,913 |
| TVM | 1,900 | 1,900 |
| VALIDATOR | 16,178 | 16,178 |

The two counts are identical on every fleet: no interval runs slightly past the window.
Every one that does runs roughly 975 years past it, so this is a sentinel, not a long outage.

`CLEAR_DTM` in `device_event_enriched` carries the same sentinel -- the 99th percentile of
`CLEAR_DTM - EVENT_DTM` on GATE hardware-OOS rows is 512,431,600 minutes.

**Impact:** any consumer computing duration from `outage_end` gets a nonsense value, and
pandas cannot represent the date at all (its timestamp ceiling is 2262-04-11), so naive
tooling fails outright rather than degrading.

**Question for Cubic:** is `3000-01-01` an "unclosed" marker? If so it should be NULL, or
documented, so consumers can treat it as censored rather than as a date.

---

## 3. Undocumented cap on `duration_min`

`duration_min` is capped at exactly **10,080 minutes (7 days)**. No row exceeds it on any
fleet. The rows sitting on the cap are precisely the rows whose `outage_end` is a sentinel:

| fleet | rows at exactly 10,080 min | rows whose raw span exceeds 7 days |
|---|---|---|
| GATE | 3,048 | 3,018 |
| TVM | 2,517 | 2,391 |
| VALIDATOR | 19,080 | 17,281 |

Separately, `duration_min` is **0.0** on roughly half of all rows: 50.9% of GATE, 61.6% of
TVM, 50.5% of VALIDATOR.

**Question for Cubic:** is the 7-day cap deliberate? If so it should be documented, because
a capped value is a censored observation and any total built from it is a lower bound.

---

## Why this matters to us

Our PS2 cascading-failure metric summed `duration_min` across episodes to report
out-of-service time. Devices are routinely in several concurrent OOS episodes, so that sum
counts the same wall-clock minute more than once. On this window it overstated real device
downtime by **4.63x for gates, 2.53x for TVMs and 2.05x for validators** -- the published
validator figure implied 22.9 out-of-service hours per device per day.

**That part is ours, and it is already fixed** -- we now compute the union of each device's
intervals rather than their sum. We are not asking Cubic to fix that.

What we are asking about is the three items above, because they set a floor on how accurate
any downstream measure can be:

1. The duplicate records inflate **event and episode counts** on every consumer, most
   severely TVM, where 55.4% of rows are redundant.
2. The sentinels make `outage_end` unusable for duration, forcing every consumer to derive
   duration from the capped column instead.
3. The cap means long outages are silently truncated, so totals are lower bounds and the
   truncation is invisible.

## What would help

- Confirmation of whether the duplicates are intended, and if not, de-duplication upstream.
- `NULL` rather than `3000-01-01` for an unclosed outage, or documentation of the sentinel.
- Documentation of the 7-day cap, and ideally a flag marking a capped row as censored.

We are happy to share the read-only diagnostic notebook so the figures can be reproduced
directly.
