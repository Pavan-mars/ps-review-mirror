# Patch — `notebooks/cross_wired_daily_job.py`: PS1 score source and the end of silent absence

**Answers Q4** of `docs/PS1_DAILY_INFERENCE_DESIGN.md`.
**Status: documented, NOT applied.** This file runs in Databricks. I cannot execute it, and an untested edit to the job that feeds the dashboard is the exact risk the reversibility rule exists to prevent. Apply it by hand, run it against a past date first (§4 step 4 of the design doc), then commit.

---

## Why

Today the job takes PS1 scores from `s3://<artifacts>/chicago/device_ps1_cross_wired_daily/{slug}/` and filters `transit_day IN (target_days)`.

That prefix holds the notebooks' **test-split** predictions — historical dates only. On a new day the filter matches zero rows. Zero rows does not raise, so the `except` branch never runs, `_missing_ps1_cats` stays empty, nothing prints, and the left join contributes `NULL` for every prediction column. The job's two guards (`dupes > 0`, `row_count == 0`) both pass, because the spine comes from `gold.device_ps1_daily`, which is full.

**Result: the job succeeds and writes a cross-wire in which every PS1 prediction is NULL, with no signal of any kind.**

The patch does two things: prefer today's batch-scored output, and make an empty PS1 join a reported fact rather than an absence.

---

## Change 1 — add the batch-scored prefix as the first source

Locate (around line 275):

```python
# ---- optional PS1 SageMaker cross-wired scores from S3 (per device category) ---
ps1_xw_base = f"s3://{artifact_bucket}/chicago/device_ps1_cross_wired_daily"
ps1_xw_legacy_bases = [f"s3://{bucket}/chicago/gold/device_ps1_cross_wired_daily"]
```

Replace with:

```python
# ---- PS1 scores: batch-scored (today) -> cell-24 artifacts -> legacy gold ------
# Precedence matters. The first source is produced by the daily SageMaker
# scoring job and is partitioned by asof=<date>. The second is the notebooks'
# TEST-SPLIT output -- historical dates only, useful for backfill, useless for
# today. The third is the pre-migration layout, kept as a fallback until batch
# scoring has run clean for 14 days.
ps1_batch_base      = f"s3://{artifact_bucket}/chicago/ps1/scored"
ps1_xw_base         = f"s3://{artifact_bucket}/chicago/device_ps1_cross_wired_daily"
ps1_xw_legacy_bases = [f"s3://{bucket}/chicago/gold/device_ps1_cross_wired_daily"]

# which source served each fleet -- carried into the manifest so that a number
# on the dashboard can always be traced to where it came from
ps1_score_source: dict[str, str] = {}
```

Then, immediately **before** the existing `for _cat, _slug in ps1_xw_slugs.items():` loop, insert:

```python
# --- source 1: today's batch scoring run -------------------------------------
for _cat, _slug in ps1_xw_slugs.items():
    _bpath = f"{ps1_batch_base}/asof={target_days[0]}/fleet={_slug}/"
    try:
        _df = spark.read.parquet(_bpath)
        _cols = [c for c in xw_keep if c in _df.columns]
        if "DEVICE_KEY" not in _cols or "transit_day" not in _cols:
            print(f"[WARN] batch-scored {_cat}: missing join keys at {_bpath}")
            continue
        _df = (
            _df.filter(F.col("transit_day").isin(target_days))
               .select(*_cols)
               .dropDuplicates([c for c in ["DEVICE_KEY", "transit_day", "COMPONENT_SERIAL_NBR"] if c in _cols])
        )
        _n = _df.count()
        if _n == 0:
            # deliberate: a readable path that yields no rows for target_days is
            # NOT the same as an unreadable path, and must not be treated as one
            print(f"[WARN] batch-scored {_cat}: path readable but 0 rows for {target_days}")
            continue
        if "device_category" not in _df.columns:
            _df = _df.withColumn("device_category", F.lit(_cat))
        ps1_xw_parts.append(_df)
        ps1_score_source[_cat] = "batch_scored"
        print(f"PS1 {_cat}: {_n:,} rows from batch scoring at {_bpath}")
    except Exception as exc:
        print(f"[INFO] batch-scored {_cat} unavailable at {_bpath} ({exc})")
```

and change the existing loops to skip fleets already served:

```python
for _cat, _slug in ps1_xw_slugs.items():
    if _cat in ps1_score_source:        # <-- ADD THIS LINE
        continue                        # <-- AND THIS
    _path = f"{ps1_xw_base}/{_slug}/"
    ...
        ps1_xw_parts.append(_df)
        ps1_score_source[_cat] = "artifacts_cell24"     # <-- ADD
        print(f"Joined PS1 cross-wired scores from {_path}")
```

Same two additions in the legacy-fallback loop, with `ps1_score_source[_cat] = "legacy_gold"`.

> `ps1_xw_parts` is currently initialised *after* this block in the original file. Move `ps1_xw_parts: list = []` and `_missing_ps1_cats: list[str] = []` **above** the new source-1 loop, or it will `NameError` on the first iteration.

---

## Change 2 — count what actually joined, and refuse to be silent

After the PS1 parts are unioned into `cross` and before the `dupes` / `row_count` guards, insert:

```python
# ---- PS1 coverage check -------------------------------------------------------
# The failure this catches: a left join that contributed nothing looks exactly
# like a left join that contributed correctly. Everything downstream -- the
# manifest, the RDS loader, the dashboard -- sees a successful run either way.
# Count the rows that carry a real probability and say so out loud.
ps1_rows_joined: dict[str, int] = {}
for _cat in ps1_xw_slugs:
    if "ps1_fail_prob" in cross.columns and "device_category" in cross.columns:
        ps1_rows_joined[_cat] = cross.filter(
            (F.col("device_category") == _cat) & F.col("ps1_fail_prob").isNotNull()
        ).count()
    else:
        ps1_rows_joined[_cat] = 0

ps1_categories_missing = [c for c, n in ps1_rows_joined.items() if n == 0]
ps1_partial = len(ps1_categories_missing) > 0

print(f"PS1 coverage: {ps1_rows_joined}  source={ps1_score_source}")

if len(ps1_categories_missing) == len(ps1_xw_slugs):
    raise RuntimeError(
        f"NO PS1 PREDICTIONS for {target_days}. All three fleets contributed "
        f"zero non-null ps1_fail_prob rows. Sources tried: batch={ps1_batch_base}, "
        f"artifacts={ps1_xw_base}, legacy={ps1_xw_legacy_bases}. "
        f"Refusing to write a cross-wire of NULLs that would be indistinguishable "
        f"from a healthy one downstream."
    )
if ps1_partial:
    print(f"[WARN] PS1 PARTIAL -- no predictions for {ps1_categories_missing}. "
          f"Manifest will carry ps1_partial=true; the RDS loader will refuse it "
          f"without --allow-partial.")
```

**Why raise on all-three-missing but only warn on one or two:** one fleet absent is a real operational state — a scoring job for TVM can fail while GATE and VALIDATOR succeed, and writing the two good fleets is better than writing nothing. All three absent means the chain is broken upstream, and a cross-wire of pure NULLs is worse than no cross-wire, because it looks like data.

---

## Change 3 — put it in the manifest

In the manifest dict (around line 420), add four keys:

```python
    manifest = {
        "job": "cross_wired_daily",
        ...
        "schema_version": "1.1",              # <-- was "1.0"; the shape changed
        "ps1_score_source":       ps1_score_source,        # NEW
        "ps1_rows_joined":        ps1_rows_joined,         # NEW
        "ps1_categories_missing": ps1_categories_missing,  # NEW
        "ps1_partial":            ps1_partial,             # NEW
    }
```

Bump `schema_version` — a consumer that reads `1.0` and gets these keys should be able to tell that the contract moved.

---

## Change 4 — the loader honours it

In the RDS loader (`load_cross_wired_to_rds.py`, referenced by the manifest's own `loader_hint`):

```python
if manifest.get("ps1_partial") and not args.allow_partial:
    raise SystemExit(
        f"Manifest {manifest_uri} reports ps1_partial=true "
        f"(missing: {manifest.get('ps1_categories_missing')}). "
        f"Loading it would write NULL predictions for those fleets into "
        f"failure_predictions, where they are indistinguishable from "
        f"'the model predicted nothing'. Pass --allow-partial to override."
    )
```

---

## Test before committing

1. Pick a past date that **is** in the cell-24 output. Run the job with `asof_date=<that date>`. Expect `ps1_score_source = {"GATE": "artifacts_cell24", ...}` and non-zero `ps1_rows_joined`. This proves the patch did not break the existing path.
2. Run with `asof_date=` a date that is in **no** source. Expect the `RuntimeError`. This proves the silence is gone — and it is the test that would have caught the original defect.
3. After the scoring job exists, run with a date it has scored. Expect `ps1_score_source = {"GATE": "batch_scored", ...}`.

Test 2 is the one that matters. Do not skip it because it is the one that is supposed to fail.

---

## Rollback

`git revert` the commit. The patch adds a source and adds checks; it removes nothing. Reverting restores the previous behaviour exactly — including the silence, which is why the revert should be a deliberate decision and not a reflex.
