# GATE DAP episodes are clock-locked to 02:00

**Date** 03-Aug-2026
**Source** PS3 v2.5 run `6a7002b0-a0fc-41f8-bb0f-1ad5a5358edf`, computed_date 2026-04-11,
queried through the live `/ps3/v25/*` API against Aurora.
**Status** Measured. The interpretation at the end is explicitly separated from
the measurements and is not established.

---

## The measurement

**60.9% of all GATE OOS episodes are attributed to DAP** — 23,412 of 38,448.
GATE is 70.9% of every episode PS3 counts (38,448 of 54,239). So DAP-on-gates is
**about 43% of the entire PS3 episode population**.

**Those episodes start at 02:00.** Checked in four independent three-month
windows spanning the whole 24-month extract, not one sample:

| window | GATE episodes sampled | DAP | DAP starting in hour 02 | top 3 hours |
|---|---|---|---|---|
| 2024-05-01 → 2024-08-01 | 1,500 | 514 | **95.5%** | 96.7% |
| 2025-01-01 → 2025-04-01 | 1,500 | 686 | **96.4%** | 97.2% |
| 2025-09-01 → 2025-12-01 | 1,500 | 999 | **97.4%** | 98.1% |
| 2026-01-01 → 2026-04-11 | 1,500 | 1,062 | **89.0%** | 98.2% |

Uniform across the day would be 4.2% in any single hour. Non-DAP GATE episodes
in the same sample are not concentrated this way (SCM peaks at 11.4% in its
busiest hour).

**Device recurrence is locked to the same clock.** For each device PS3 reports a
median inter-episode interval. The share of devices whose median lands within
±6 minutes of an exact multiple of 24 hours:

| fleet | devices | median interval on a 24h multiple | vs chance (0.83%) | p25 – p75 |
|---|---|---|---|---|
| GATE | 848 | **40.4%** | 48.7× | 9.00 – 11.95 days |
| TVM | 449 | 11.1% | 13.4× | 17.50 – 100.42 days |
| VALIDATOR | 1,140 | 1.1% | 1.4× | 17.74 – 63.65 days |

Two checks that this is not an artefact:

- **Jitter control.** Displacing each GATE median by a uniform ±12 h drops the
  on-24h share from 40.4% to **0.9%** — the chance rate. The clustering is in the
  data, not in the tolerance.
- **It strengthens with evidence.** Devices with 5–19 episodes: 13.6%. With
  20–49: 40.0%. With 50+: 44.9%. A median-of-one-gap artefact would do the
  opposite.

GATE's interquartile range for recurrence is 9.00–11.95 days. TVM's is
17.5–100.4 and VALIDATOR's 17.7–63.7. GATE is far tighter than the other two.

## What the episodes look like

Sampled GATE episodes, by group:

| | n | median union downtime | median signal span | median SET events | event types |
|---|---|---|---|---|---|
| DAP starting 02h | 638 | **3.18 min** | 5,760 min (4.0 days) | 8 | `DAP OOS`, `DAPCommsError` |
| DAP other hours | 74 | 4.62 min | 4,354 min | 8 | same + 1 |
| non-DAP GATE | 160 | 47.93 min | 2.90 min | 2 | `Mnt Door Open`, `DeviceCommsLost`, +3 |

So a 02:00 DAP episode is not a single clean event. It opens at 02:00, then
flaps — roughly eight brief OOS assertions spread over about four days, totalling
around three minutes of actual union downtime.

`requires_service_call` is **True on 636 of 638** of them. The source system
treats these as service-requiring. `any_automatic_clear` is False on all of them.

## Why it matters for the model

The GATE component-attribution model is the **only** one of four scopes that
passes its quality gate (macro-F1 0.583 against a 0.185 majority baseline;
TVM 0.196, VALIDATOR 0.517 against a 0.495 baseline, pooled 0.245).

Its feature importances:

| importance | feature |
|---|---|
| **33.8%** | `event_hour` |
| 15.2% | `log_set_signal_span_minutes` |
| 14.7% | `log_oos_set_event_count` |
| 10.8% | `days_since_prior_episode` |
| **8.6%** | `event_month` |
| **6.6%** | `event_day_of_week` |

Clock and calendar alone are **49.1%** of the model. Adding
`days_since_prior_episode` takes it to ~60% temporal.

Given that 89–97% of DAP episodes start in hour 02 and DAP is 61% of GATE
episodes, the most parsimonious reading of that scorecard is that the model
separates DAP from everything else largely by asking *what time did this start*.
That is a real and reproducible signal, but it is not evidence that the model
can identify a failing component from its behaviour.

## What this does NOT establish

Stated plainly because the temptation is to over-read it:

- **It does not show these are not real failures.** The source marks
  99.7% of them as requiring a service call.
- **It does not identify the cause.** A 02:00 onset is consistent with a nightly
  batch or comms window, an end-of-service-day sequence, a scheduled reboot, a
  polling cycle, *and* with a genuine fault mode that happens to be provoked at
  that hour. This data cannot separate those.
- **It does not tell us whether the timestamp is the event or the record.** If
  an upstream job stamps a batch of events at 02:00, the concentration would be
  an artefact of the pipeline rather than of the devices. Nothing here rules
  that out.

## What would settle it

1. **Ask Cubic what runs on the gate estate at 02:00** — batch, reboot,
   reconciliation, comms window. One answer resolves most of the ambiguity.
2. **Check the raw silver timestamps** for a 02:00 spike in *record creation*
   versus *event occurrence*. If both spike identically, suspect the pipeline.
3. **Compare against a fleet with no 02:00 process.** Validators show 1.1% on-24h
   recurrence and no hour concentration, which is what an unaffected fleet looks
   like.

## Consequences if it holds

- ~43% of the PS3 episode population is one clock-locked mode on one fleet.
  Every unfiltered PS3 total is dominated by it.
- PS1 and PS2 draw OOS from the same silver source. If these episodes are
  periodic rather than fault-driven, both inherit the same inflation — PS1 in its
  labels, PS2 in its availability measure.
- The GATE component model should not be presented as component identification
  until it is re-scored with the clock features withheld.

## Reproducing this

Every number above comes from the live API and one script:

```
GET /ps3/v25/episodes?city=CHI&category=GATE&from=<a>&to=<b>&limit=1500
GET /ps3/v25/device-reliability?city=CHI&limit=5000
GET /ps3/v25/feature-importance?city=CHI
GET /ps3/v25/model-scorecard?city=CHI
```
