# Runbook — PS5 on the dashboard, PS3 onto S3 → RDS, PS3 daily inference

03-Aug-2026. Three separate pieces of work. Only the first one matters before
the demo; the other two are the architecture change you asked for and can land
after it.

---

## 0. Your first question: is anything being pushed straight to RDS?

**No. Nothing from the PS3 run has reached Aurora at all.**

`PS3_03_RootCause_Models.ipynb` cell 3 wrote one file into the SageMaker
working directory:

```
PS3_sql_outputs/ps3_run_20260803.sql     797,562 bytes
   ps3_model_runs          1 row
   ps3_head_summary        3 rows
   ps3_device_predictions  2,485 rows
```

That file is not a load. To land it, someone has to copy it into the
dashboard-api's deployment package, register it in `handler.py`, redeploy the
API and invoke `{"action":"load_run"}`. So the answer to *"are you pushing
directly to RDS"* is worse than yes — the **data currently ships inside the
code artifact of the service that serves the dashboard**. A data refresh is a
redeploy of the API. There is no S3 object to re-load, diff, audit or replay,
and none of it can be scheduled.

Section 2 replaces that. The SQL file still works and is still generated, so
you have a fallback for tomorrow either way.

---

## 1. PS5 → the new dashboard  *(demo-critical, do this one)*

### What was actually missing

The Aurora tables and the API routes were already live. I checked all seven
against the deployed gateway and every one returned real rows. The gap was
purely in the **v2 dashboard**: `src/v2/V2Shell.jsx` rendered a "work in
progress" panel on the PS5 tab, and there was no `PS5Overview.jsx` — PS1, PS2,
PS3 and PS4 all had one.

(The *old* `PS5SLAReliabilityTab.jsx` points at a different, separate PS5 API
gateway. It is untouched.)

### Files changed

| file | change |
|---|---|
| `dashboard/src/v2/PS5Overview.jsx` | **new** — five sub-tabs: Fleet status, Devices, Components, Model quality, How we know |
| `dashboard/src/v2/v2api.js` | **new `ps5` helper block** |
| `dashboard/src/v2/V2Shell.jsx` | imports and renders PS5Overview; PS5 estate card now live; PS5 no longer marked WIP |
| `api/lambda/cubic-mars-dashboard-api/handler.py` | `?limit` honoured on two routes, `DISTINCT` on serial-rul, two new aggregate routes |

Backups of every edited file are beside them as `*.bak_ps5wire`.

### What the screen will show tomorrow

Verified against the live API just now:

```
fleet         rows  in_type  act_now  overdue   CRIT   HIGH    MED    LOW
GATE           452      452        7      354     46     90    136    180
TVM            416      416      310      310     42     83    125    166
VALIDATOR    3,000    3,235       78    2,282    324    647    971  1,058

Hero:  395 devices to act on now, of 4,103 scored  =  9.6%
```

### The one thing to know about those numbers

`/ps5/device-rul` and `/ps5/serial-rul` are **capped, worst-first browse
lists**, not denominators. Counting them counts what came back, and because
they are ordered `act_now DESC` the rows that fall off the end are the healthy
ones — so a naive count *overstates* risk. This is the same trap that once put
"600 of 600 devices need a work order" on the PS1 screen.

Two consequences, both handled:

- The tab fetches **one request per fleet**. Without that split, a single
  3,000-row cap is shared across three fleets and the biggest one eats it — an
  unsplit call returns 2,539 validators, 416 TVMs and **45 of 452 gates**.
- Validators still hit the cap at 3,000 of 3,235. The screen shows a
  **"Partial feed"** badge and names the affected fleet, and it separates the
  numbers truncation can distort (overdue, band mix, averages) from the one it
  cannot — **act-now counts are complete**, because every act-now row sorts
  above the cut.

Deploying section 1b removes the caveat entirely.

### 1a. Build and ship the front end

```powershell
cd "D:\work data\NAM_development\AWS\development_project\sathish_repo\dashboard"
npm run build
```

I could not build it for you — `node_modules` here is a Windows install and
the Linux side cannot load `rolldown-binding.linux-x64-gnu.node`. What I did
instead: bundled the whole `src/v2` tree with esbuild, which resolves every
local import and every named export. It links clean, PS5Overview included.

### 1b. Dashboard API — optional, removes the cap caveat

`handler.py` is patched and syntax-checked but **not deployed**. Three changes:

1. `/ps5/device-rul` and `/ps5/serial-rul` honour `?limit` (default still
   3,000, ceiling 12,000). The front end already sends it; an undeployed
   Lambda ignores it and the UI falls back honestly.
2. `SELECT DISTINCT` on `/ps5/serial-rul`. `v_ps5_serial_dupes` measures 3,823
   duplicated `(device_id, component_serial_nbr)` keys on validators at up to
   25 rows each, with `max_component_types = max_risk_tiers = max_rul_values =
   1` inside every one — a roster fan-out, not a second reading of the part.
   DISTINCT can only ever drop rows equal on every selected column, so it
   cannot lose a measurement. `/ps5/serial-grain` stays live so the defect
   remains visible rather than papered over.
3. Two new routes — **the denominators** — aggregating in SQL over the whole
   view instead of over a page of it:
   - `/ps5/summary` — per fleet: devices, act-now, overdue, the four bands,
     median RUL
   - `/ps5/component-summary` — `COUNT(DISTINCT (device_id, serial))` with
     `n_rows` beside it, so the fan-out stays measurable from the route alone

**These four statements were executed, not just read.** I stood up
PostgreSQL 16 locally, created stand-in `v_ps5_device_rul` and
`v_ps5_serial_rul` with the exact column list the routes select, and ran the
SQL *extracted from the patched handler* — f-strings resolved, `?limit`
applied — rather than a copy I retyped. All four return. `LIMIT` resolves to
9,999 when asked; `DISTINCT` collapses a duplicated serial pair from 2 rows
to 1; and `/ps5/component-summary` reports `n_rows=2, n_components=1` on that
pair, which is the fan-out made visible from the route alone.

```bash
# AWS CloudShell, us-east-1
rm -f ~/handler.py           # the upload fails silently if it already exists
# upload api/lambda/cubic-mars-dashboard-api/handler.py
cd ~/cubic-mars-dashboard-api 2>/dev/null || { mkdir -p ~/dash && cd ~/dash; }
cp ~/handler.py . && bash deploy.sh
```

Then confirm — and add `_cb=$RANDOM` so a warm container cannot answer for a
cold one:

```bash
B=https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com
curl -s "$B/ps5/summary?city=CHI&_cb=$RANDOM" | python3 -m json.tool | head -30
```

---

## 2. PS3 → S3 → RDS  *(the architecture change)*

```
notebook ─► s3://…artifacts…/chicago/ps3/rootcause_outputs/<run_id>/
                ps3_model_runs.csv
                ps3_head_summary.csv
                ps3_device_predictions.csv
                manifest.json
                     │
        cubic-mars-ps3-rc-loader   (Lambda, inside the RDS VPC)
                     │
                 Aurora ps3_*  ─►  dashboard
```

### New files

```
api/lambda/cubic-mars-ps3-rc-loader/handler.py
api/lambda/cubic-mars-ps3-rc-loader/deploy.sh
api/lambda/cubic-mars-ps3-rc-loader/requirements.txt
notebooks/ps3_root_cause_analysis/PS3_03_RootCause_Models_v2.ipynb
```

### Why a new Lambda rather than reusing `cubic-mars-ps3-v25-loader`

That loader is good and I wanted to reuse it. It refuses any run missing one
of its 20 `EXPECTED_TABLES` — correctly, because a half-loaded PS3 shows
today's metrics beside last week's device list and nothing on screen reveals
it. A 3-artifact root-cause run would be refused as incomplete, and widening
`EXPECTED_TABLES` would make every existing V26 run incomplete instead. So:
separate loader, three artifacts, same guard applied to its own run.

The new loader inherits the four guards that were earned the hard way on PS2
and PS5 — schema-adaptive columns from `information_schema`, quoted
identifiers, per-table savepoints, PK-collapse detection in Python before the
INSERT — and adds two:

- **Structural allow-list.** `target_table()` maps three artifact names to
  three tables and returns `None` for everything else. No payload can point it
  at `ps3_v25_*`, `ps3_v2_*` or the severity tables. Those feeds keep running;
  rollback is "stop invoking this Lambda".
- **Run-scoped DELETE** on `(city_id, run_id)`. Re-loading a run replaces it;
  it cannot wipe another run. `ps3_model_runs` stays a registry.

And if any artifact fails to insert, the **whole run rolls back** rather than
committing the survivors.

### Deploy and run

```bash
# CloudShell, us-east-1 — upload the three files first
cd ~ && rm -rf ps3rc && mkdir ps3rc && cd ps3rc
bash deploy.sh
```

Then, in the notebook: run **cell 4**, and it prints the exact invoke for its
own run_id. Always dry-run first — it applies every guard, writes nothing, and
reports S3 row counts against what is already in Aurora.

The deploy script deliberately does **not** create an EventBridge schedule.
The other loaders run on a clock because their upstream does; PS3's does not
yet. A clock rule would, on a day the scorer failed, silently re-load
yesterday's run under yesterday's run_id — which looks identical to a good day
on every screen. The S3-trigger alternative is written out in a comment in
`deploy.sh`, with the warning that
`put-bucket-notification-configuration` **replaces** the bucket's whole
notification config.

---

## 3. PS3 daily inference

### New files

```
notebooks/ps3_root_cause_analysis/ps3_rc_features.py       feature contract
notebooks/ps3_root_cause_analysis/ps3_rc_daily_score.py    the daily job
```

### The thing that actually makes this safe

Training built its features inside the notebook. If daily scoring rebuilds
them from a second copy of that code, the two drift — and training/serving
skew does not announce itself. It shows up as a model that scored 0.91 in the
notebook and quietly predicts the majority class in production, which is
indistinguishable from "the model was always weak" unless someone diffs two
files that were never meant to be compared.

So both sides import **one** module, `ps3_rc_features.py`. Notebook cell 1
writes it; the scorer imports it. The packaged artifact records
`FEATURE_CONTRACT_VERSION`, and the scorer **aborts** — not warns — if it does
not match.

Three specific failure modes are closed:

- **Column order.** numpy has no column names, so a reordered feature list is
  not an error, it is a wrong answer. The artifact stores `feat_num` and
  `feat_cat` verbatim and `design_matrix()` assembles in that order or raises.
- **Class pinning.** `prior_share_*` columns are rebuilt from the **trained**
  class list, not from what the scoring window happens to contain. Otherwise a
  week with no `CARD_READER` failures produces a narrower matrix, every column
  after the gap shifts by one, and the model scores confidently on the wrong
  numbers.
- **Missing columns are not filled.** A zero in `prior_oos_count` means "this
  device has never failed before" — a real, confident statement. Inventing it
  because a column did not arrive turns a pipeline fault into a prediction.

What gets packaged per fleet, to
`s3://…/chicago/ps3/models/<run_id>/<fleet>/`: the classifier, the **fitted**
encoder (refitting it at scoring time remaps every category — the classic way
to ship a model that is nonsense in production), the feature order, the class
list, the contract version, and the training metrics.

### Run it

```bash
python ps3_rc_daily_score.py --model-run latest --score-date latest --invoke-loader
```

Deliberately not a Lambda: scoring reads the whole device-day spine to rebuild
each device's history, and a 15-minute ceiling with a 512 MB `/tmp` is the
wrong shape. It belongs where the spine is built — a SageMaker Processing job
or a Glue Python-shell job on a daily rule. The Lambda in this pipeline is the
**loader**, which is small and I/O-bound, and it is already in the VPC.

Note on `ps3_head_summary` for a scoring run: it carries the **training**
metrics and marks itself `run_kind='batch_score'`. A scoring day has no labels
and measures nothing; publishing re-labelled numbers would imply otherwise.

---

## What I have not done

- **Not deployed anything.** No AWS credentials in this session. Every command
  above is for you to run; every file is syntax-checked and the loader's guards
  are unit-exercised.
- **Not re-run the notebook.** Cell 2 in v2 imports the shared module rather
  than computing features inline. The computation is the same, but the
  `prior_share_*` columns are now in sorted order rather than order of
  appearance, so metrics may move in the third decimal. If you would rather
  not re-run before the demo: **run v1 for the demo, v2 after.** Cells 4 and 5
  need the fitted objects that only v2 keeps.
- **Not touched** the PS5 notebook, the PS3 spine build, the PS3 taxonomy
  notebook, the old PS5 SLA tab, or any existing loader.

## Still open from before

- PS2 Fleet-impact hero repoint, held pending validation of the VALIDATOR
  46.26% downtime figure.
- PS5 concordance below the 0.65 floor on TVM and validators; the registry
  reports lower numbers again and marks all three fleets not ready. Both
  numbers are now on the Model quality tab, side by side, unmerged.
- The validator serial fan-out is deduplicated **at read time**. The pipeline
  still needs to write one row per part.
- PS4's self-referential label (`signal_active_count >= 2`).
