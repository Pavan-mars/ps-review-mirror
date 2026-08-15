# PS1 Daily Inference — Design, Options and Decisions

**CUBIC MARS Chicago (CTA-Ventra) · Problem Statement 1 — 3-day hardware failure prediction**
**Date:** 2026-08-11 · **Status:** design, pending decisions marked `DECISION-n`
**Companion documents:** `docs/PS1_CANONICAL.md` (what PS1 *is* and its measured state), this file (what PS1 *should become* for daily operation)

---

## 0. How to read this document

Every factual claim in this document carries one of three markers. Nothing is asserted without one.

| Marker | Meaning |
|---|---|
| **[READ]** | Read directly out of a file in this repository on 2026-08-11. The file and line are named. |
| **[MEASURED]** | Observed in the live AWS account via CloudShell during this programme. The command and date are named. |
| **[UNVERIFIED]** | Not checked. Stated as a hypothesis with the exact command that would settle it. |

The distinction matters because this programme has already been damaged twice by an inference presented as a fact — once by `sql/53`, which stamped a confident wrong `run_id` over an honest `NULL`, and once by a `partition_cols` finding that cited a code comment as evidence for a cause nobody had looked at. Both are recorded in `PS1_CANONICAL.md` §8.8. This document does not repeat the pattern.

---

## 1. The finding that changes the architecture

Before answering the ten questions, one fact has to be established, because six of the ten answers depend on it.

### 1.1 What the notebooks actually score

The three fleet notebooks — `PS1_3d_GATE_SageMaker_MLflow_FeatureStore.ipynb`, `..._TVM_...`, `..._VALIDATOR_...` — are structurally identical (25, 25 and 27 cells). **[READ]**

| Cell | Purpose | Class |
|---|---|---|
| 4 (6 in VALIDATOR) | Spark checkpoint load/save | I/O |
| 11 (13) | Spark ML baselines — LR, RF, GBT, XGB, LGB, CatBoost | **TRAIN** |
| 12 (14) | Optuna hyper-parameter tuning | **TRAIN** |
| 14 (16) | Legacy clean-label stub — disabled | dead |
| 19 (21) | SHAP on the Spark tree champion | explain |
| 20 (22) | Ranking metrics + monthly drift + **stores predictions** | score |
| 21 (23) | MLflow model registration, `create_model_package` | **TRAIN/REGISTER** |
| 22 (24) | Deploy SageMaker endpoint | **DEPLOY** |
| 23 (25) | SageMaker Model Quality Monitor | monitor |
| 24 (26) | Cross-wired PS1 output at device + component grain | **EXPORT** |

Cell 20 builds the object that Cell 24 later exports. Its source is unambiguous **[READ]**:

```python
_res     = CATEGORY_RESULTS[CATEGORY]
_test_pdf = _res.get("test_pdf")            # <-- the held-out TEST SPLIT
...
proba    = _test_pdf["score"].astype(float).values
...
_pred_out = _test_pdf.copy()
_pred_out["ps1_fail_prob"]  = proba
_pred_out["ps1_predicted"]  = (proba >= opt_thresh).astype(int)
_res["predictions"] = _pred_out
print(f"[{CATEGORY}] Stored {len(_pred_out):,} test predictions for cross-wired output.")
```

Cell 24 then reads exactly that **[READ]**:

```python
for cat, res in CATEGORY_RESULTS.items():
    if "predictions" not in res or res["predictions"] is None:
        print(f"[{cat}] No predictions found — run CELL 20 (ranking) first.")
        continue
    pred_df = res["predictions"].copy()
```

and writes it to `s3://{ARTIFACT_BUCKET}/chicago/device_ps1_cross_wired_daily/{gate|tvm|validator}/`.

**Therefore: the prefix named `device_ps1_cross_wired_daily` does not contain daily predictions. It contains the model's scores on the held-out test split of its own training data, written once, on the day the notebook was last run end to end.** The word "daily" in that path describes the *grain* (one row per device-day), not the *cadence*.

The notebook's own print statement says so — "Stored N **test** predictions for cross-wired output" — and has said so on every run.

### 1.2 What that means for a new day

`notebooks/cross_wired_daily_job.py` is the Databricks job that assembles the cross-wire and hands it to the RDS loader. Its PS1 join is **[READ]**, lines ~275–305:

```python
ps1_xw_base = f"s3://{artifact_bucket}/chicago/device_ps1_cross_wired_daily"
for _cat, _slug in ps1_xw_slugs.items():
    _path = f"{ps1_xw_base}/{_slug}/"
    try:
        _df = spark.read.parquet(_path)
        ...
        _df = _df.filter(F.col("transit_day").isin(target_days)) ...
    except Exception as exc:
        print(f"[INFO] PS1 cross-wired {_cat} not joined at {_path} ({exc})")
        _missing_ps1_cats.append(_cat)
```

and `target_days` resolves to the **latest `transit_day` in `gold.device_ps1_daily`** **[READ]**, i.e. yesterday, once daily data starts flowing.

Follow the two facts through:

1. The parquet at `.../gate/` contains only `transit_day` values that were in the training test split — historical dates.
2. On a new day, `transit_day IN (target_days)` matches **zero rows**.
3. Zero rows is not an exception. The `try` block succeeds. `_missing_ps1_cats` stays empty. Nothing prints `[INFO]`.
4. The left join contributes nothing. `ps1_fail_prob`, `ps1_predicted`, `ps1_risk_tier`, `is_prob_anomaly` come out **NULL for every row**.
5. The job's two guards are `dupes > 0 → raise` and `row_count == 0 → raise` **[READ]**. Neither fires: the spine comes from `gold.device_ps1_daily`, which is full. The job prints `Cross-wire rows: N` and `cross_wired_daily_job complete`, writes the parquet, writes the manifest, and **succeeds**.

**On the first day of real Chicago data, the daily job will report success while producing a cross-wire in which every PS1 prediction is NULL.** No alarm, no error, no missing-data message — because the only code path that reports absence is the `except` branch, and an empty filter result does not raise.

This is the same failure the programme has now met four times and named in `PS1_CANONICAL.md`: **a `WHERE` clause that drops every row says nothing; it does not say "no data".** It is why `v_ps1_xw_causation` needed a `sufficient_data` column, why the freshness verdict had to make `UNREADABLE` dominate, and why `sql/54` had to undo `sql/53`. Here it is once more, in the one place where it would have reached the dashboard as a silently blank prediction.

### 1.3 The gap, stated plainly

> **Nothing in this system produces a PS1 score for a date the notebooks did not train on.**

Not the endpoints (0 invocations in 14 days — and PK is right that this is because the daily data has not arrived, not because the endpoints are pointless). Not the daily job, which only *reads* scores. Not the notebooks, which only score their own test split. Daily inference for PS1 does not exist yet. That is the thing to build, and everything below is about building it with the smallest reversible change.

---

## 2. Answers to the ten questions

### Q1 — Daily inferencing: what has to be built

Four things, in this order:

| # | Component | Status today | Action |
|---|---|---|---|
| 1 | A **score-only** artefact that takes a day of Gold/Silver features and emits `ps1_fail_prob` per device-day | does not exist | **build** — `notebooks/ps1_batch_score_daily.py` |
| 2 | A **completion signal** when the Gold layer is ready for that date | Databricks writes Gold; no event is emitted | **build** — one `PutEvents` call at the end of the Gold job |
| 3 | An **orchestrator** that runs 1 when 2 fires, then the cross-wire, then the RDS push | nothing; PS1-A is a 06:40 time-based rule | **build** — Step Functions state machine, EventBridge rule |
| 4 | A **cross-wire that prefers today's scores** and refuses to be silent about their absence | prefers a static historical prefix, silent on absence | **patch** — `cross_wired_daily_job.py`, §3.3 |

The critical ordering constraint: **1 must be delivered before 3 is enabled.** An orchestrator that runs a scoring step that does not exist fails loudly, which is fine; an orchestrator that runs the cross-wire without a scoring step succeeds silently, which is not.

### Q2 — Is ECR required?

**No, and it should be retired for PS1.**

The three PS1 endpoints run the **AWS-managed SageMaker scikit-learn Deep Learning Container**, loading `spark-model-v1/model.tar.gz` from S3 **[MEASURED]**. They have never used a custom image. Meanwhile:

- `cubic-pdm/mars-ps1` holds **2.38 GB** and is referenced by **0 of 28 model packages and 0 endpoints** **[MEASURED]**. It is dead weight that nothing points at.
- `cubic-mars-ecr-training-dev` is **empty**, yet a Step Functions pipeline named after it succeeds every day at 02:00 having launched **zero training jobs** **[MEASURED]**.
- Six further ECR repositories are empty **[MEASURED]**.

Managed DLC covers everything PS1 needs: the model is a gradient-boosted tree bundle, the dependencies are pandas + numpy + the booster library, and the SageMaker Python SDK's `FrameworkModel` supports supplying a `source_dir` containing `inference.py` and `requirements.txt` alongside the model artefact. Custom containers earn their cost when you need a system library the DLC lacks, a non-Python runtime, or a build you must pin byte-for-byte. PS1 needs none of those.

There is one repository this does **not** apply to: `cubic-pdm/mars-ps3` (778 MB) is **live** — model package 14 references it **[MEASURED]** — and, worse, pins the **mutable `:latest` tag**. That is a PS3 problem and it is a real one (an image push silently changes what a registered model package resolves to), but it is out of scope here and must not be swept up in a PS1 cleanup.

> `DECISION-1` — Retire `cubic-pdm/mars-ps1`? Recommended: **yes, by lifecycle policy, not by delete.** See §4.

> ### CORRECTION, 2026-08-11, later the same day
>
> Everything in §Q2 Facts 1–3 below was inferred from `docker/inference_ps1.py` on the assumption that it is the handler the endpoints use. **It is not.** Cell 22 *generates its own handler inline* (`_INF_TEMPLATE`) and tars it into `model.tar.gz` alongside the model. `docker/` is a separate, parallel BYOC artefact that nothing deploys.
>
> The facts below are struck where wrong and the real contract is in **§Q2.1**. I am leaving the wrong version visible rather than quietly rewriting it, because the mistake is instructive: I read one plausible file, found a handler in it, and concluded it was *the* handler — without checking what actually builds the archive. That is the same shape as the `partition_cols` error (grep for a string, infer a cause, never look) and the `sql/53` error (find a nearby value, assume it is the right one). Third instance this week. The tell each time was the same: **I had evidence that something *could* be true and treated it as evidence that it *was*.**
>
> The correction reverses one recommendation (Batch Transform is viable after all) and strengthens another for a different reason.

~~**`docker/` has now been read [READ, 2026-08-11].** It is not a stub.~~ **[SUPERSEDED — see the correction above and §Q2.1. Retained for the record.]** `docker/` has been read. `Dockerfile.ps1` builds a BYOC serving image from `python:3.12-slim`, installs Flask, copies `inference_ps1.py` to `/opt/ml/code/inference.py` and writes a `serve` entrypoint. `inference_ps1.py` is **dual-purpose**: it defines the four managed-DLC handler functions *and*, under `if __name__ == '__main__'`, a Flask app exposing `/ping` and `/invocations` — the BYOC contract.

That is good news for §Q1 item 1: **the handler already exists and is usable by either route.** It is most of the scoring job.

It also produces three facts that change what follows.

**Fact 1 — the model is a joblib, not a Spark model.** `model_fn` is unambiguous **[READ]**:

```python
hits = glob.glob(os.path.join(model_dir, '*_champion.joblib'))
if not hits:
    raise FileNotFoundError(...)
model = joblib.load(hits[0])
if isinstance(model, dict) and 'model' in model:
    model = model['model']
```

The endpoints load an artefact under the key `spark-model-v1/model.tar.gz` **[MEASURED]**. A Spark `PipelineModel` is a directory of parquet and metadata; it is not a joblib and `joblib.load` cannot read it. So `spark-model-v1` is a **path name, not evidence about the contents**. Cell 22's header — "Spark champion → native booster bundle" **[READ]** — describes precisely this: the Spark champion was converted to a plain booster and pickled. The archive almost certainly contains `<fleet>_champion.joblib`.

This is worth stating explicitly because the entire E-1 argument has been conducted in the vocabulary of "Spark run vs sklearn run", and a path called `spark-model-v1` has been reinforcing an assumption that nobody checked. It still may be the Spark-lineage model — but the file inside it is a pickle, not a Spark model, and those are different claims.

**Fact 2 — the champion was pickled from `__main__`, and the shim proves it.** The file opens by reconstructing a `_CBWrapper` class and then doing **[READ]**:

```python
sys.modules['__main__']._CBWrapper = _CBWrapper
```

That line exists for exactly one reason: the pickle references a class defined in a notebook's `__main__`, and unpickling fails with `AttributeError` unless a class of that name is re-attached to `__main__` first. It is a load-bearing hack, and it means the artefact's loadability depends on `_CBWrapper.__init__` keeping the same signature. Anyone who "tidies up" that class breaks every stored model silently.

**Fact 3 — the pinned versions are now a live risk to the managed-DLC plan.** `requirements.txt` **[READ]**:

```
catboost==1.2.10   scikit-learn==1.9.0   xgboost==3.3.0   lightgbm==4.6.0
```

A joblib pickled under scikit-learn 1.9.0 is not guaranteed to load under whatever scikit-learn the managed DLC image ships. Unpickling across a major/minor gap raises `InconsistentVersionWarning` at best and constructs a subtly wrong estimator at worst. **This is the one condition under which ECR earns its place for PS1** — not because a custom image is architecturally nicer, but because these four pins may not exist in any managed image.

So `DECISION-1` is now conditional, and the condition is checkable in one command (§Q5).

---

### Q2.1 — The actual serving contract, read from cell 22 **[READ, 2026-08-11]**

Cell 22 does not use `docker/`. It builds the deployment bundle itself:

```python
_meta = _export_spark_champion_bundle(spark_pipeline, FEATURE_COLS, tag, _code_dir)
joblib.dump(float(opt_thresh), os.path.join(_code_dir, f"ps1_{tag}_threshold.joblib"))
with tarfile.open(f"/tmp/model_{tag}.tar.gz", "w:gz") as tar:
    tar.add(os.path.join(_code_dir, fn), arcname=fn)
MODEL_S3_KEY = f"sagemaker/ps1-3d/{tag}/spark-model-v{MLFLOW_VERSION}/model.tar.gz"
S3_CLIENT.upload_file(f"/tmp/model_{tag}.tar.gz", ARTIFACT_BUCKET, MODEL_S3_KEY)
```

and writes the handler from an inline template, `_INF_TEMPLATE`. Its real body:

```python
def model_fn(model_dir):
    meta   = joblib.load(os.path.join(model_dir, "ps1_TAG_meta.joblib"))
    thresh = joblib.load(os.path.join(model_dir, "ps1_TAG_threshold.joblib"))
    mtype  = meta["model_type"]; mf = os.path.join(model_dir, meta["model_file"])
    if mtype == "xgboost":    b = xgb.Booster(); b.load_model(mf)
    elif mtype == "lightgbm": lgb.Booster(model_file=mf)
    else:                     CatBoostClassifier().load_model(mf)

def _predict_proba(bundle, df):
    cols = bundle["meta"]["feature_cols"]
    X = (_impute(df, cols, meta.get("medians", {}))
         .reindex(columns=cols, fill_value=0.0)
         .astype(float).values)

def input_fn(request_body, content_type):
    if content_type == "text/csv":
        return pd.read_csv(StringIO(request_body))     # HEADER ROW, named columns
```

**Four corrections follow.**

**C-1. The model is a native booster, not a joblib estimator.** `model_fn` loads an xgboost / lightgbm / catboost model file named by `meta["model_file"]`. Fact 1's conclusion — "the artefact is a pickle" — was right that it is not a Spark model and wrong about what it is. `docker/inference_ps1.py`'s `glob('*_champion.joblib')` describes a *different artefact belonging to a different serving path*. Cell 22's header phrase "native booster bundle" was literal, and I should have read it as a specification rather than as colour.

**C-2. The feature contract IS pinned in the archive — this closes a hazard I was about to raise.** `meta["feature_cols"]` travels with the model, and `_predict_proba` reindexes to it **by name**. I had been about to report a blocking defect: `FEATURE_COLS = [c for c in ALL_CANDIDATE_FEATURES if c in available]` **[READ, cell 8]** is computed from whatever columns exist at run time, and `spark_ckpt_meta.joblib` lives on the Studio instance's *local disk*. That would have meant a scoring job recomputing the list could silently disagree with the trained model. It cannot, because the list is in the bundle. **Not a defect. Good design, and it was already there.**

**C-3. `reindex(..., fill_value=0.0)` is a real silent-failure path, and it is the fifth instance of the pattern.** Trace the two cases apart:

| Scoring frame | What happens | Correct? |
|---|---|---|
| column present, value `NaN` | `_impute` fills it with `meta["medians"][c]` | yes |
| column **absent entirely** | `_impute` skips it (`if c in out.columns`), then `reindex` inserts **`0.0`** | **no** |

A feature that fails to materialise in Gold on a given day is scored as **zero**, not as its median, and not as an error. For a count or rate feature, 0.0 is a perfectly ordinary value — the model returns a confident probability and nothing anywhere records that an input was missing. Same shape as the empty `WHERE` clause in §1.2: *absence rendered as a plausible value instead of as absence.*

**This is the single most important thing for the scoring job to guard.** Before calling predict, assert:

```python
missing = [c for c in meta["feature_cols"] if c not in frame.columns]
if missing:
    raise RuntimeError(
        f"{len(missing)} of {len(meta['feature_cols'])} trained features are "
        f"absent from the scoring frame: {missing[:20]}. reindex would fill "
        f"these with 0.0 and the model would return confident, wrong, "
        f"plausible probabilities. Refusing to score."
    )
```

**C-4. Batch Transform is viable after all — my objection was to the wrong handler.** `input_fn` uses `pd.read_csv`, so it consumes a **header row and named columns**, and `_predict_proba` reindexes by name. Column order at the caller is irrelevant. The "only row order binds a probability to a `DEVICE_KEY`" argument I gave in `DECISION-2` was true of `docker/inference_ps1.py` — which is not deployed — and false of the real handler. `DECISION-2` is re-derived below on grounds that survive this correction.

### Q3 — Scheduling the notebooks vs writing a scoring notebook

PK's option 3 was "automate the notebooks at scheduled time **or** write new notebooks to score and write into S3". The two halves are not equivalent.

**Scheduling the existing notebooks daily is not feasible, and would be harmful if it were.** Cells 11, 12 and 21 train, tune and register **[READ]**. Running the notebook daily means retraining and re-registering a model every day against a training window that grows by one day. That is not inference; it is uncontrolled continuous retraining with no champion/challenger gate, no approval step and no way to attribute a prediction to a model version. It would also break the one provenance guarantee the programme just spent three migrations (`sql/52`, `53`, `54`) establishing: that a scorecard row names the run that produced it.

**Writing a score-only artefact is feasible and is the right answer.** It reuses the notebook's feature construction verbatim and drops everything from cell 11 onward, substituting a load-and-score step. Concretely:

- **keep**: cells 1–10 (config, Silver/Gold reads, feature engineering, the Feature Store lookup at cell 15)
- **drop**: 11, 12 (train), 13 (split), 17–19 (CV/SHAP), 21 (register), 22 (deploy), 23 (monitor)
- **replace cell 20** with: load the approved model package → score the feature frame for `asof_date` → write `ps1_fail_prob` / `ps1_predicted` / `threshold_used`
- **keep cell 24 nearly unchanged**: it is good code. The joins to `hw_config_current`, `dim_device`, `device_ps2_chains`, `device_ps5_component`, the p95 anomaly flag, the risk tiers and the SHAP top-3 attachment are all reusable **[READ]**. Its only defect is its *input*.

PK's constraint — "the scoring should happen in SageMaker" — is respected: the model load and `predict_proba` run in a SageMaker job, not in Databricks. Databricks stays responsible for features and for the cross-wire, which is what it is already good at.

> `DECISION-2` — Where does the scoring job run?
>
> | Option | How it works | For | Against |
> |---|---|---|---|
> | **A. SageMaker Batch Transform** | Model artefact + managed DLC; SageMaker feeds it S3 objects, writes predictions back to S3 | Purpose-built for exactly this; no infrastructure to manage; `join_source=Input` carries the keys through; failure emits a first-class EventBridge event | Rigid I/O contract (CSV/JSONL, one record per line); needs an `inference.py` inside `model.tar.gz`; carrying `DEVICE_KEY`/`transit_day` through requires `input_filter`/`output_filter` and is fiddly to get right |
> | **B. SageMaker Processing job** | A container runs your script; you read parquet, score, write parquet | Total freedom over I/O; can do the scoring *and* the cross-wire joins in one place; trivially debuggable; same code shape as the notebook | You own the retry/partitioning logic; no `Transform Job State Change` event (uses `Processing Job State Change` instead — equally available) |
> | C. Keep real-time endpoints, invoke from Lambda | Loop over devices, call `InvokeEndpoint` | Nothing new to build | Three endpoints running 24×7 for one burst a day; ~790k rows through a request/response API; explicitly against the stated requirement |
>
> **Recommendation: B (Processing job)** — for one reason that outweighs Batch Transform's elegance. The prediction is not the deliverable; the *cross-wire row* is. A Processing job produces the finished per-component artefact in a single step with pandas code lifted straight from cell 24. Batch Transform produces a column of floats that then has to be re-joined to its own keys — and a key-alignment bug in that re-join would be silent, produce plausible numbers, and put the wrong probability against the wrong device. Given this programme's history with silent wrong values, the option with fewer joins wins.
>
> **RE-DERIVED after the §Q2.1 correction.** The argument below rests on `docker/inference_ps1.py`, which is **not the deployed handler**. Batch Transform is *not* disqualified: the real `input_fn` reads a CSV header and reindexes by column name, so keys can ride along via `input_filter` / `join_source=Input` and nothing depends on row order.
>
> **The recommendation stays B, on two grounds that survive the correction:**
>
> 1. **C-3 cannot be guarded from inside Batch Transform.** The missing-column check has to run *before* the payload is serialised — once the frame is a CSV, an absent feature is just a column that is not there, and `reindex(fill_value=0.0)` turns it into a plausible zero inside the container where nothing can intervene. A Processing job owns the frame and can refuse. This is the single highest-severity defect in the chain, so the architecture that can check it wins.
> 2. **The deliverable is the cross-wire row, not a probability.** A Processing job produces the finished per-component artefact in one step using cell 24's pandas code verbatim. Batch Transform produces probabilities that then need re-joining to keys and re-running the cell 24 joins somewhere else anyway.
>
> Ground 1 is the load-bearing one. If a future change makes the column contract enforceable upstream, Batch Transform becomes a reasonable choice and this decision should be revisited rather than treated as settled.
>
> ~~The argument I originally gave, retained for the record:~~ The handler's I/O contract is **[READ]** — *of `docker/inference_ps1.py`, which is not deployed*:
>
> ```python
> def input_fn(request_body, content_type):
>     if content_type == 'text/csv':
>         return np.array(list(csv.reader(io.StringIO(request_body))), dtype=float)
> ...
> def output_fn(prediction, accept):
>     return '\n'.join('{:.6f}'.format(p) for p in prediction), 'text/csv'
> ```
>
> In goes a bare float matrix with **no header and no keys**. Out comes a bare column of floats. There is nothing in either direction that identifies a device. Under Batch Transform, the *only* thing binding a probability to a `DEVICE_KEY` is **row order**, reconstructed afterwards by `join_source=Input`. Any reordering — a repartition, a multi-shard split, a retry that re-emits a chunk — puts the wrong probability against the wrong device, produces a perfectly plausible number, and raises nothing.
>
> ~~This programme has already shipped one confident wrong value (`sql/53`)... **B, firmly.**~~ **[WRONG — the deployed handler takes named columns. See the re-derivation above.]**
>
> C is rejected outright and drives `DECISION-4`.

### Q4 — Keeping both cell-24 cross-wire and batch predictions available

Yes, and `cross_wired_daily_job.py` can be modified to do it. The change is small and it makes the system louder rather than quieter.

Today the job tries two sources in order **[READ]**: the artifacts prefix (cell 24's output), then a legacy gold-bucket prefix. The patch adds a third source *ahead* of both, and records which source served each fleet:

```
precedence:  batch_scored (today's job)  →  artifacts (cell 24)  →  legacy gold
```

Three changes, in `notebooks/cross_wired_daily_job.py`:

1. **New first source.** `ps1_batch_base = f"s3://{artifact_bucket}/chicago/ps1/scored"`, partitioned `asof=<date>`. Read it first; only fall through to the existing sources for fleets it does not cover.
2. **Record provenance per fleet.** A dict `ps1_score_source = {"GATE": "batch_scored", "TVM": "artifacts", ...}` carried into the manifest, so the loader and the dashboard can state where a number came from.
3. **Break the silence.** After the joins, count the rows that actually carry a non-null `ps1_fail_prob` for each fleet on `target_days`. If a fleet contributes zero, that is a **fact to report, not an absence to ignore**:
   - add `ps1_rows_joined`, `ps1_categories_missing` and `ps1_score_source` to the manifest;
   - raise if **all three** fleets are missing (the pipeline is broken — do not write a cross-wire of NULLs);
   - warn but proceed if one or two are missing, and mark the manifest `ps1_partial: true`;
   - the RDS loader refuses a manifest with `ps1_partial` unless `--allow-partial` is passed.

That last point is the whole lesson of §1.2 turned into code. A left join that contributes nothing must not look like a left join that contributed correctly.

The patch is supplied as `tooling/patches/cross_wired_daily_job_ps1_source.md` — as a documented diff rather than an applied edit, because this file runs in Databricks where I cannot test it, and an untested edit to the job that feeds the dashboard is exactly the risk the reversibility rule exists to prevent.

### Q5 — Can ECR be avoided entirely with managed DLC?

**Conditionally yes — and the condition is a library-version question, not an architecture question.**

The handler exists and satisfies the managed-DLC contract already (§Q2). What managed DLC additionally requires is that the image can *unpickle* the artefact. Given Fact 3, that reduces to one question:

> Does the managed DLC image ship a scikit-learn (and CatBoost / XGBoost / LightGBM) close enough to `1.9.0 / 1.2.10 / 3.3.0 / 4.6.0` to load a joblib pickled under them?

Three outcomes:

| Outcome | Meaning | Action |
|---|---|---|
| Versions match | managed DLC is sufficient | **retire ECR for PS1** — `DECISION-1` proceeds |
| Versions differ, pickle still loads cleanly | works, but on luck | pin `requirements.txt` into `code/` so the DLC installs the exact versions at container start; verify a probability matches the notebook to 6 dp |
| Pickle fails or warns | managed DLC is **not** sufficient | **keep ECR for PS1.** `docker/Dockerfile.ps1` already builds the right image; it was written for this reason |

Note the third row honestly: it is possible the correct answer is that ECR *is* required, and that whoever wrote `docker/Dockerfile.ps1` had already worked that out. Recommending retirement before checking would be asserting a conclusion over somebody else's undocumented reason.

**The check.** `tooling/ps1_read_docker_and_model.sh` does all of this in one read-only run: it resolves `ModelDataUrl` from `DescribeModel`, downloads the archive, lists the members, prints `code/inference.py` if present, and — new — reports whether a `*_champion.joblib` is in there and what the container image is. Run it before anything else.

The three possible listings and what each means:

| Archive contains | Interpretation |
|---|---|
| `*_champion.joblib` **and** `code/inference.py` | self-contained; the DLC uses the bundled handler. Best case |
| `*_champion.joblib` only | the DLC's **default** sklearn handler is being used. It expects a specific filename/format; a `*_champion.joblib` is not it. The endpoint may have been broken since creation and **nobody would know — 0 invocations in 14 days [MEASURED]**. Package `docker/inference_ps1.py` as `code/inference.py` |
| neither | the archive is something else entirely and the whole serving assumption needs re-deriving from `DescribeModel` output |

Row 2 deserves emphasis. `model_fn` runs at container start, so a *load* failure would have shown as a failed endpoint creation, and the endpoints are `InService`. But `input_fn` / `predict_fn` only run on invocation — and there have been none. **An endpoint being `InService` proves the model loaded. It proves nothing about whether it can answer.**

### Q6 — Triggering on completion rather than on time

The current PS1 trigger is a 06:40 cron rule **[MEASURED]**. Cron encodes a guess about when upstream will finish. When Chicago's data is late, cron runs anyway, over stale Gold, and succeeds.

> `DECISION-3` — How does completion reach EventBridge?
>
> | Option | Mechanism | For | Against |
> |---|---|---|---|
> | **A. Databricks `PutEvents`** | The Gold job's final task calls `events:PutEvents` with `DetailType: "Gold Layer Complete"`, `Detail: {city, asof_date, tables, row_counts}` | **Touches no shared AWS configuration at all.** The payload carries the as-of date, so no downstream step has to guess it. Fully reversible — delete the rule and nothing else changed. Signals *semantic* completion, not object arrival | Databricks needs `events:PutEvents` on one event bus; adds ~10 lines to the Gold job |
> | B. S3 → EventBridge on the Gold bucket | Enable `EventBridgeConfiguration`, rule matches the manifest key | No Databricks change | **`put-bucket-notification-configuration` is a REPLACE, not a merge.** The Gold bucket already carries a notification to the `ps1-cross-wired-push` Lambda **[MEASURED]**. A naive `put` deletes it, breaking a working path with no error. Also fires on the *first* object, not on job completion |
> | C. Databricks Jobs API webhook → API Gateway → EventBridge | Job-level success webhook | Job-level granularity | Three more components; API Gateway is another public surface to secure |
>
> **Recommendation: A.** It is the only option that changes nothing anyone else depends on. This is directly downstream of the standing rule *"ensure everything is reversible — else we will be in soup."*
>
> If B is ever chosen anyway, the *only* safe sequence is `get-bucket-notification-configuration` → merge in `EventBridgeConfiguration` → `put` the merged document → re-read and diff. `tooling/ps1_eventbridge_build.sh` implements exactly that guarded sequence, behind an opt-in flag, and refuses to run if the GET fails.

The manifest that already exists — `s3://<gold>/chicago/cross_wired/manifest/asof=<date>/manifest.json`, written by `cross_wired_daily_job.py` **[READ]** — is a genuine completion signal, but it is the **wrong** one for scoring: it is written *after* the cross-wire, which is *after* the point at which PS1 scores were needed. It is the right trigger for the **RDS push**, which is the step that follows it. Use it there.

The resulting chain:

```
Databricks: raw → bronze → silver → gold
        │
        └─ PutEvents  {"detail-type": "Gold Layer Complete", "asof_date": "..."}
               │
        EventBridge rule  cubic-mars-ps1-gold-complete
               │
        Step Functions  cubic-mars-ps1-daily-scoring
               ├─ 1. SageMaker Processing: score GATE / TVM / VALIDATOR   (parallel)
               ├─ 2. write s3://<artifacts>/chicago/ps1/scored/asof=<date>/
               ├─ 3. trigger Databricks cross_wired_daily_job (asof_date passed in)
               └─ Catch → SNS  cubic-mars-ps1-alerts
                                  │
        manifest written ─────────┴──→ EventBridge → RDS push Lambda
```

Note that step 3 passes `asof_date` explicitly rather than letting the job re-derive "latest transit_day". Two components independently guessing the same date is a race; one component deciding and telling the other is not.

### Q7 — Failure signalling

Four rules, because there are four distinct ways this can fail and only two of them raise an exception.

| # | Failure | Detection | Rule |
|---|---|---|---|
| 1 | Scoring job errors | `SageMaker Processing Job State Change`, status `Failed`/`Stopped` | → SNS `cubic-mars-ps1-alerts` |
| 2 | Orchestration errors | `Step Functions Execution Status Change`, status `FAILED`/`TIMED_OUT`/`ABORTED` | → SNS |
| 3 | **Nothing ran at all** | scheduled watchdog at a deadline (e.g. 09:30 UTC) → Lambda checks whether `chicago/ps1/scored/asof=<today>/` exists | → SNS if absent |
| 4 | **It ran and produced nothing** | the cross-wire job's own `ps1_rows_joined == 0` check (§Q4) raises → caught by rule 2 | → SNS |

Rows 3 and 4 are the ones that matter and the ones a conventional setup omits. **A pipeline that never starts emits no failure event.** If the Databricks Gold job dies before its `PutEvents`, the EventBridge rule never fires, Step Functions never runs, no state change is published, and every dashboard shows yesterday's numbers with no indication that they are yesterday's. The watchdog is the only component that can observe an absence. It is not optional.

Rule 3's Lambda should also compare the newest `asof=` partition against today's date and alert on staleness ≥ 1 day, so that a silent stall on day 3 is caught even if day 1 alerted and was acknowledged.

`DECISION-5`: SNS topic subscribers and the watchdog deadline. Suggested deadline 09:30 UTC — the current cron chain runs 05:45→08:00 **[MEASURED]**, so 09:30 gives 90 minutes of slack.

### Q8 — What to clean up, retire or remove

Ordered by risk, safest first. **Nothing in this list is a delete on the first pass.**

| # | Item | Evidence | Action | Reversible? |
|---|---|---|---|---|
| 1 | 6 empty ECR repositories | **[MEASURED]** 0 images | **list first**; delete only behind `--delete-empty-repos` | yes — an empty repo is recreated by `create-repository`, but a CI job pushing to a deleted repo fails in someone else's pipeline. Several of these empties belong to unfinished PS2–PS5 work. Read the list and recognise every name before arming |
| 2 | `cubic-mars-ecr-training-dev` (empty) + the 02:00 `cubic-mars-sfn-training-pipeline-dev` that succeeds daily having launched **zero** training jobs, pointing at `s3://cubic-mars-artifacts-dev/` which is not the known artifacts bucket | **[MEASURED]** | **disable the EventBridge rule**; leave the state machine intact | yes — `enable-rule` |
| 3 | `cubic-pdm/mars-ps1`, 2.38 GB, referenced by 0/28 model packages and 0 endpoints | **[MEASURED]** | apply a lifecycle policy that expires untagged images >90 days; re-audit in 30 days; delete only after | yes for 30 days, then no |
| 4 | 3 real-time endpoints `chicago-ps1-3d-{gate,tvm,validator}-failure-v1`, 0 invocations/14 days | **[MEASURED]** | **capture first, then delete the endpoint only** | see below |
| 5 | Path B `cubic-mars-ps1-rds-push` — rule DISABLED, concurrency 0, no invocation since 10-Aug 12:00Z | **[MEASURED]** | leave exactly as is | n/a |
| 6 | Legacy prefix `s3://<gold>/chicago/gold/device_ps1_cross_wired_daily` | **[READ]** — still the fallback source in the daily job | keep until batch scoring has run clean for 14 days | n/a |

**Item 4 carries an ordering constraint that must not be violated.**

`E-1` — the open finding that the scorecard describes `ps1_sklearn_20260726` while the endpoints were registered with the Spark run `ps1_20260726` — is currently unresolvable from the database. `sql/54`'s own view comment says it: *registration is not deployment; only `DescribeEndpointConfig` observes what an endpoint actually runs* **[READ]**. Deleting the endpoints destroys the only artefact that can close E-1, permanently and unrecoverably.

So: **capture `DescribeEndpoint`, `DescribeEndpointConfig` and `DescribeModel` for all three fleets to a file, commit that file to the repository, and only then delete the endpoints.** Delete the *endpoint*; keep the *endpoint-config* and the *model* — they cost nothing and they are the record. `tooling/ps1_cleanup.sh` enforces this: it will not emit a delete command for any endpoint whose capture file is not already on disk.

Endpoint deletion is *not* reversible in the sense that matters — you can recreate an endpoint from its config, but you cannot recover the config's own history. Hence the capture.

> `DECISION-4` — Delete the three real-time endpoints? Recommended: **yes, after capture**, because the requirement is daily batch and a real-time endpoint bills for idle time. But note this is a cost decision, not a correctness one — PK's correction stands: the endpoints show 0 invocations because the daily data has not arrived, not because they are useless. If there is any near-term requirement for on-demand single-device scoring (a dispatcher asking "what about *this* gate, right now?"), keep one endpoint and delete two.

### Q9 — EventBridge rules to build

All four created **DISABLED**, enabled explicitly one at a time. `tooling/ps1_eventbridge_build.sh` does this and prints every command before running it.

| Rule | Pattern | Target |
|---|---|---|
| `cubic-mars-ps1-gold-complete` | `{"source":["cubic.mars.databricks"],"detail-type":["Gold Layer Complete"],"detail":{"city":["CHI"]}}` | Step Functions `cubic-mars-ps1-daily-scoring` |
| `cubic-mars-ps1-sfn-failed` | `{"source":["aws.states"],"detail-type":["Step Functions Execution Status Change"],"detail":{"status":["FAILED","TIMED_OUT","ABORTED"],"stateMachineArn":[...]}}` | SNS `cubic-mars-ps1-alerts` |
| `cubic-mars-ps1-processing-failed` | `{"source":["aws.sagemaker"],"detail-type":["SageMaker Processing Job State Change"],"detail":{"ProcessingJobStatus":["Failed","Stopped"]}}` | SNS |
| `cubic-mars-ps1-watchdog` | `cron(30 9 * * ? *)` | Lambda `cubic-mars-ps1-freshness-watchdog` |

**Two hazards, both already met in this programme and both guarded in the script:**

- `events put-rule` and `put-targets` are **replace** operations. Re-running with a partial target list silently drops targets. The script reads the existing rule and targets first and refuses to proceed if a rule of that name already exists with targets it did not create.
- The existing schedule chain (dim 05:45 → PS1-A 06:40 → PS2 07:10 → PS5 07:20 → PS4 07:35 → PS4-v3 Mon 08:00) **[MEASURED]** is currently collision-free. Adding an event-driven PS1 path while PS1-A's 06:40 cron is still enabled means PS1 can run twice. The script's final step disables `PS1-A`'s cron — as a separate, explicitly confirmed action, printed as a command rather than executed by default.

### Q10 — Options, trade-offs and decision points, consolidated

| ID | Decision | Options | Recommendation | Reversible |
|---|---|---|---|---|
| **DECISION-1** | Retire ECR `cubic-pdm/mars-ps1` | **delete (lifecycle + re-audit)** / keep | **Retire.** §Q2.1 settles it: cell 22 tars a self-contained bundle — model file, `meta` (with `feature_cols` and `medians`), threshold and a generated `inference.py` — and uploads it for the managed DLC. `docker/` is a parallel BYOC path that nothing deploys. The version-pin worry was about `docker/requirements.txt`, which is not in the serving path. Remaining check: the DLC image must import `xgboost`/`lightgbm`/`catboost`, which `model_fn` does lazily per branch | yes for 30 days |
| **DECISION-2** | Where scoring runs | Batch Transform / **Processing job** / endpoints | **Processing job** — because the C-3 missing-column check must run *before* serialisation. Not because of row order; that objection was wrong | yes |
| **DECISION-8** *(new)* | Guard `reindex(fill_value=0.0)` — C-3 | assert-and-refuse / fill with medians / leave | **assert and refuse.** An absent feature silently becomes `0.0` and the model returns a confident wrong probability. Highest-severity defect found in this review | yes |
| **DECISION-3** | Completion trigger | **Databricks PutEvents** / S3→EventBridge / Jobs webhook | **PutEvents** — touches no shared configuration | yes |
| **DECISION-4** | Delete the 3 endpoints | delete all / keep one / keep all | **capture, then delete two, keep one** if on-demand scoring is wanted | capture makes it safe |
| **DECISION-5** | Watchdog deadline + SNS subscribers | — | 09:30 UTC; subscribers are PK's call | yes |
| **DECISION-6** | `VALIDATOR.recall_floor` | still NULL by decision (`sql/52`) | **outstanding from 26-Jul** — VALIDATOR is gated on nothing | n/a |
| **DECISION-7** | PS3's absent EventBridge schedule; PS3's mutable `:latest` ECR tag | — | **out of scope here**; carry into tomorrow's PS3 session | n/a |

---

## 3. Deliverables in this change set

| File | What it is | Runs where |
|---|---|---|
| `docs/PS1_DAILY_INFERENCE_DESIGN.md` | this document | — |
| `tooling/ps1_read_docker_and_model.sh` | reads `docker/*` and lists `model.tar.gz`; settles the two `[UNVERIFIED]` items in §Q2/§Q5 | CloudShell |
| `tooling/ps1_cleanup.sh` | **dry-run by default**; prints every command; refuses endpoint deletion without a capture file; blocks the ECR lifecycle `put` if the current policy is unreadable *or* already present; refuses to disable a rule if the name pattern matches more than one | CloudShell |
| `tooling/sfn/ps1_daily_scoring.asl.json` | Step Functions definition, four `REPLACE_WITH_` placeholders left deliberately | written by the build script |
| `tooling/lambda/ps1_freshness_watchdog.py` | the absence detector — the only component that can see a run that never started | written by the build script |
| `tooling/ps1_eventbridge_build.sh` | creates the four rules DISABLED; guarded merge for any replace-semantics call | CloudShell |
| `tooling/patches/cross_wired_daily_job_ps1_source.md` | the documented §Q4 patch, not applied | Databricks |

`notebooks/ps1_batch_score_daily.py` is **not** in this change set. It cannot be written honestly until `docker/inference_ps1.py` and the contents of `model.tar.gz` are known — writing it now would mean inventing a model-loading contract and testing it against a fixture built from that invention, which is precisely how `sql/52` failed. Run `ps1_read_docker_and_model.sh` first; the scoring script follows in the same session.

---

## 4. Sequence

1. Run `tooling/ps1_read_docker_and_model.sh`. → closes `[UNVERIFIED]` ×2.
2. Write `notebooks/ps1_batch_score_daily.py` against what that returns.
3. Run it once, manually, for a single past date. Compare its output against the cell-24 parquet for the same date — **the same model on the same features must produce the same probabilities.** If it does not, stop; the loading contract is wrong.
4. Apply the §Q4 patch to `cross_wired_daily_job.py`; run the daily job for that same past date; confirm `ps1_score_source = batch_scored` in the manifest.
5. Run `tooling/ps1_eventbridge_build.sh` — rules created DISABLED.
6. Add `PutEvents` to the Gold job. Enable `cubic-mars-ps1-gold-complete` only.
7. Enable the three signalling rules. Verify by deliberately failing a run.
8. Disable the PS1-A 06:40 cron. **This is the cut-over.**
9. Run `tooling/ps1_cleanup.sh --dry-run`, review, then `--apply`.
10. Re-audit ECR in 30 days; delete `cubic-pdm/mars-ps1` if still unreferenced.

Steps 1–4 are reversible by deleting files. Steps 5–7 are reversible by `delete-rule`. Step 8 is reversible by `enable-rule`. Step 9 is reversible except where §Q8 says otherwise. Step 10 is not reversible, which is why it is 30 days out.

---

## 5. What this document does not claim

- It does not claim the batch-scored probabilities will match the notebook's. Step 3 of §4 exists to test that, and it may fail.
- It does not claim the managed DLC can load the champion. §Q2.1 shows the archive is self-contained, but whether the DLC image can `import xgboost` / `lightgbm` / `catboost` is `[UNVERIFIED]`. `model_fn` imports lazily inside each branch, so a missing library fails at container start — loudly, which is the good case.
- **It no longer claims what §Q2 Facts 1–3 claimed.** Those were inferred from `docker/inference_ps1.py`, which is not the deployed handler. They are struck in place rather than deleted, so the reasoning error stays visible.
- It does not claim the three endpoints have ever successfully answered an inference request. `InService` proves the model loaded at container start. With 0 invocations in 14 days **[MEASURED]**, `input_fn` and `predict_fn` have not been exercised in production at all.
- It does not claim the endpoints are serving the Spark model. `E-1` remains open, and `sql/54`'s view reports `GAP` on all three fleets **[MEASURED]** using a *proxy* — the latest train-kind row in `ps1_inference_runs`. Only the capture in §Q8 item 4 can settle it.
- It does not claim the 954/429 device-population gap is resolved. It is not, and per the standing constraint it does not go on the dashboard.
