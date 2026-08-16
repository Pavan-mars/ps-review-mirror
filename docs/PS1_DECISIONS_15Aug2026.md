# PS1 architecture decisions — settled 2026-08-15

Recorded here rather than only in conversation, because a decision nobody can
find is a decision that gets remade. Supersedes the provisional recommendations
in `PS1_DAILY_INFERENCE_DESIGN.md` §Q10 wherever they differ.

| ID | Decision | Chosen | Was |
|---|---|---|---|
| **D-1** | ECR `cubic-pdm/mars-ps1` | **RETIRE** — lifecycle policy + 30-day re-audit, `--delete-ecr-now` to force | conditional |
| **D-2** | Where daily scoring runs | **SageMaker Processing job** | Processing (unchanged) |
| **D-3** | Completion trigger | **Databricks `PutEvents`** → EventBridge → Step Functions | PutEvents (unchanged) |
| **D-4** | The three real-time endpoints | **DELETE ALL THREE** — capture is committed | pending |
| **D-9** | Container packaging | **Managed DLC, dependencies pinned** — BYOC declined | open |
| **D-10** | Repo scope | Three production notebooks + the bus-map utility + all Databricks jobs. Everything else retired to `_retired/` | — |

---

## D-9 — why BYOC was declined

The request was "SageMaker BYOC instead of ECR, to save cost on daily batch."
Two things in that are not true, and stating them plainly is the whole record:

**BYOC requires ECR.** "Bring Your Own Container" means building an image and
pushing it to *your* ECR repository — that is the only place SageMaker pulls
custom images from. Choosing BYOC would have *kept* `cubic-pdm/mars-ps1` alive,
which is the opposite of D-1.

**Container choice does not affect cost.** An `ml.m5` instance bills identically
whichever image runs on it. The 24×7 cost is the **endpoint**, not the container
— which is why D-2 and D-4 deliver the entire saving, and D-9 delivers none of
it. Three endpoints have billed continuously since 24-Jul for **zero
invocations**.

**What BYOC would genuinely have bought:** xgboost frozen at image-build time
rather than `pip install`-ed at container start. That is a real reproducibility
gain and the strongest argument for it. But an exact pin achieves the same
determinism for one line of change, against a Dockerfile, a build pipeline, an
ECR repo, and an image to patch whenever a CVE lands.

**The condition that would reverse this:** if the container-start `pip install`
ever proves unreliable — a PyPI outage breaking a scheduled run — build the
image then. Not before. Build it when a fact demands it.

---

## D-3 — the trigger, and the hazard it avoids

```
Databricks daily:  raw → bronze → silver → gold
        └─ ps1_gold_complete_event.py   (final task)
              │ verifies the gold tables are non-empty for asof_date FIRST
              └─ PutEvents {"detail-type":"Gold Layer Complete","asof_date":…}
                    └─ EventBridge  cubic-mars-ps1-gold-complete
                          └─ Step Functions  cubic-mars-ps1-daily-scoring
                                ├─ Processing ×3 (parallel)  →  chicago/ps1/scored/asof=…
                                ├─ trigger cross_wired_daily_job (asof_date passed IN)
                                └─ Catch → SNS  cubic-mars-ps1-alerts
```

**Why not S3 → EventBridge.** Enabling it requires
`put-bucket-notification-configuration`, which **replaces** the bucket's whole
notification document. The gold bucket already routes to the
`ps1-cross-wired-push` Lambda. A naive put deletes that notification, returns
success, prints nothing, and the first symptom is a load that stops arriving
days later. `PutEvents` touches no shared configuration and is undone by
deleting one rule.

**Why the event verifies before it fires.** `ps1_gold_complete_event.py` counts
rows for `asof_date` in all five gold tables and **raises** if
`device_ps1_daily` is empty. An event saying "complete" when the layer is empty
would start the scoring chain against nothing — and because an empty filter
raises no exception downstream, that surfaces three steps later as NULL
predictions rather than as a failure.

**Why `asof_date` travels in the payload.** `cross_wired_daily_job.py` currently
re-derives "latest transit_day" on its own. Two components independently
guessing the same fact is a race. One decides; the others are told.

**The fourth rule is the one that matters.** Rules 2 and 3 fire when something
*fails*. Neither fires when nothing *runs* — if the Databricks job dies before
its `PutEvents`, no rule fires, no state change is published, and every
dashboard shows yesterday's numbers looking current. Only
`cubic-mars-ps1-watchdog`, a clock looking for an expected artefact, can observe
that.

---

## D-10 — what stays, and two things that nearly went

**Kept despite not being production model notebooks:**

- `PS1_VALIDATOR_Device_Bus_Serial_Map.ipynb` — it *produces*
  `validator_device_bus_serial_map.xlsx`, named by `sql/41_dim_device_bus.sql`
  as the source of `dim_device_bus` (4,218 devices, 1,832 buses), which a live
  route reads and `cross_wired_daily_job.py` joins for `BUS_ID`. Crews are
  dispatched to a **bus**, not a device id. Retiring it would freeze that table
  permanently — the rows survive, the ability to regenerate them does not.
- All Databricks jobs and the silver/gold tables. `run_layer_gold.py` *builds*
  `device_ps1_daily`, the spine the three production notebooks read, and is
  shared with PS2–PS5.

**Retired to `_retired/`, not deleted:** the v4 GATE notebook (the only one
reading PS4 scored artefacts — moved so that path stays recoverable), the local
7-day demo, the 13-notebook archive, and `docker/`.

**Inventory before the S3 sweep:** `archive/PS1_Evaluation_Fix.ipynb` wrote
`{tvm,gate}_lgb_fixed_<ts>.pkl` and `thresholds_<ts>.json` to the **gold**
bucket. Retiring the notebook is fine, but it is the only record of what wrote
those objects.

---

## What is now built vs still to build

| | Status |
|---|---|
| `notebooks/ps1_batch_score_daily.py` | **built** — Processing job, contract-asserting, C-3 fixed |
| `notebooks/ps1_gold_complete_event.py` | **built** — the D-3 trigger, verifies before firing |
| `notebooks/requirements-ps1-scoring.txt` | **built** — exact pins; versions to be confirmed against the training kernel |
| `tooling/ps1_eventbridge_build.sh` | **built** — 4 rules, created DISABLED |
| `tooling/ps1_cleanup.sh` | **built** — D-1 and D-4 recorded |
| `tooling/ps1_repo_cleanup.sh` | **built** — D-10 |
| **The feature frame** | **NOT BUILT — the only remaining blocker.** Extract notebook CELLS 6–8 into a Databricks task writing `chicago/ps1/features/asof=<date>/fleet=<slug>/` |

Cell 22's `_INF_TEMPLATE` still emits `xgboost>=2.0` into every future
`model.tar.gz`. Pinning the Processing job does not fix that — the notebook
string needs editing so the next export is pinned at source.
