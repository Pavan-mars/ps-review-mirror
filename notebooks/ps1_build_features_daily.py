# Databricks notebook source
# =============================================================================
# ps1_build_features_daily — the PS1 daily feature frame
#
#   Output:  s3://<artifacts>/chicago/ps1/features/asof=<YYYY-MM-DD>/fleet=<slug>/
#   Consumed by: notebooks/ps1_batch_score_daily.py (SageMaker Processing job)
#
# THIS FILE DOES NOT REIMPLEMENT CELLS 6-8. IT CALLS THEM.
# ========================================================
# The three fleet notebooks build the feature frame in CELLS 6-8 — roughly 690
# lines of Spark across ~11 silver/gold sources. Copying that here would create
# a SECOND implementation of the same feature definitions. The first time the
# two drift, the model scores columns that share a name and mean something
# different, and nothing reports it.
#
# So the migration is a REFACTOR, not a rewrite:
#
#   STEP 1  Move the bodies of CELLS 6, 7 and 8 verbatim into
#           notebooks/ps1_features.py, as three functions:
#
#               read_spine(spark, fleet, start_day, end_day)      <- CELL 6
#               add_auxiliary(spark, df, fleet, start, end)       <- CELL 7
#               join_and_materialise(spark, df, fleet)            <- CELL 8
#
#           Change nothing inside them. Take the parameters that were globals
#           (DEVICE_CAT, TRAIN_START, S3_SILVER_RUNTIME, ...) as arguments.
#
#   STEP 2  In each fleet notebook, replace CELLS 6-8 with:
#               %run ../ps1_features
#               df_ps1 = read_spine(spark, DEVICE_CAT, TRAIN_START, None)
#               df_ps1 = add_auxiliary(spark, df_ps1, DEVICE_CAT, TRAIN_START, None)
#               df_features, FEATURE_COLS = join_and_materialise(spark, df_ps1, DEVICE_CAT)
#           Re-run one notebook end to end. Its metrics must be UNCHANGED. If
#           they move, the extraction was not verbatim — stop and diff.
#
#   STEP 3  This file then calls the same three functions with a scoring
#           window instead of a training window. One implementation, two
#           callers, no drift possible.
#
# FOUR TRAPS IN THAT REFACTOR. All four are silent.
# =================================================
#
# TRAP 1 — THE LABEL MUST NOT BE ATTACHED, AND MUST NOT FILTER.
#   CELL 6 calls attach_hardware_oos_label(), which looks FORWARD:
#       .withColumn("label_day", F.date_sub(F.col("failure_date"), F.col("n")))
#   and then CELL 6 does:
#       df_ps1 = df_ps1.where(F.col(TARGET_COL).isin(0, 1))
#   For a day you are SCORING, the 3-day-forward outcome does not exist yet, so
#   that filter drops EVERY ROW for the most recent 3 days. The job would
#   succeed and write an empty frame. Empty is not an exception.
#   => read_spine() must take with_label=False for scoring, skipping BOTH the
#      label attach and that filter. See _assert_rows() below, which refuses an
#      empty frame rather than writing one.
#
# TRAP 2 — ROLLING FEATURES NEED 90 DAYS OF HISTORY.
#   CELL 7 and CELL 8 use prior windows up to
#       Window...rangeBetween(-90 * 86_400, -1)
#   To compute ONE day correctly you must READ ~90 days and then EMIT ONE.
#   Reading only asof_date silently produces zeros and nulls for every
#   *_prior_*, *_90d and cumulative feature — plausible values, wrong answers.
#   => LOOKBACK_DAYS below, and the emit-one-day filter at the end.
#
# TRAP 3 — FEATURE_COLS MUST NOT BE RECOMPUTED.
#   CELL 8 does:
#       FEATURE_COLS = [c for c in ALL_CANDIDATE_FEATURES if c in available]
#   i.e. it derives the feature list from whatever columns happen to exist that
#   day. At training time that is fine — it defines the model. At scoring time
#   it is the bug: a source that fails to land quietly shortens the list, and
#   the model is then fed a different frame than it was trained on.
#   => The contract is PINNED in the model bundle
#      (tooling/out/ps1_<fleet>_feature_contract.json). This job asserts against
#      it and FAILS on a mismatch. Never the other way round.
#
# TRAP 4 — THE PRIOR WINDOWS EXCLUDE TODAY, AND THAT IS DELIBERATE.
#   rangeBetween(-N * 86_400, -1) — the upper bound is -1, not 0. Features are
#   built from strictly-prior days. Do not "fix" this to 0 to get fresher
#   signal: it would leak same-day information the model never saw in training,
#   and scores would silently stop matching the training distribution.
#   The useful consequence: asof_date's own aggregates need not be complete.
# =============================================================================
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

dbutils: Any = globals().get("dbutils")
spark: Any = globals().get("spark")

import boto3
from pyspark.sql import functions as F

# STEP 1/2 above must be done first. Then this import is the whole point.
# %run ./ps1_features

ARTIFACT_BUCKET = "cubic-mars-pm-s3-datalake-dev-artifacts-170202974600"
FEATURES_PREFIX = "chicago/ps1/features"

# 90 for the longest window, +7 so the 90-day window is itself fully populated
# on the first emitted day rather than being computed from a truncated history.
LOOKBACK_DAYS = 97

FLEETS = {"GATE": "gate", "TVM": "tvm", "VALIDATOR": "validator"}

dbutils.widgets.text("asof_date", "")
dbutils.widgets.dropdown("fleet", "ALL", [*FLEETS, "ALL"])
dbutils.widgets.text("catalog", "mars_dev")
dbutils.widgets.text("artifact_bucket", ARTIFACT_BUCKET)
dbutils.widgets.dropdown("dry_run", "false", ["true", "false"])

asof_param = dbutils.widgets.get("asof_date").strip()
fleet_param = dbutils.widgets.get("fleet").strip().upper()
catalog = dbutils.widgets.get("catalog").strip()
bucket = dbutils.widgets.get("artifact_bucket").strip()
dry_run = dbutils.widgets.get("dry_run").strip().lower() == "true"

spark.sql(f"USE CATALOG {catalog}")
GOLD = f"{catalog}.gold"

# ---- resolve asof --------------------------------------------------------
if asof_param:
    asof_date = asof_param
else:
    latest = (spark.table(f"{GOLD}.device_ps1_daily")
              .agg(F.max("transit_day").alias("d")).collect()[0]["d"])
    if latest is None:
        raise RuntimeError(
            "device_ps1_daily has no transit_day. Nothing to build features from.")
    asof_date = str(latest)

start_day = (datetime.strptime(asof_date, "%Y-%m-%d")
             - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

print(f"asof={asof_date}  lookback_from={start_day} ({LOOKBACK_DAYS}d)  catalog={catalog}")


def _load_contract(fleet: str) -> list[str]:
    """The pinned feature list from the model bundle. TRAP 3.

    Read from the committed contract JSON rather than recomputed, so this job
    and the scoring job cannot disagree about what the model expects.
    """
    key = f"chicago/ps1/contracts/ps1_{FLEETS[fleet]}_feature_contract.json"
    try:
        body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
        return list(json.loads(body)["feature_cols"])
    except Exception as exc:                                    # noqa: BLE001
        raise RuntimeError(
            f"[{fleet}] cannot read the pinned feature contract at "
            f"s3://{bucket}/{key} ({exc}).\n"
            f"  Upload tooling/out/ps1_{FLEETS[fleet]}_feature_contract.json there "
            f"first. Building features against a RECOMPUTED list is TRAP 3 and is "
            f"exactly what this job exists to prevent."
        ) from exc


def _assert_rows(df, fleet: str, label: str):
    """An empty frame is a finding, not a quiet success. TRAP 1."""
    n = df.count()
    if n == 0:
        raise RuntimeError(
            f"[{fleet}] {label} produced ZERO rows for {asof_date}.\n"
            f"  The most likely cause is the forward-looking label filter\n"
            f"      df_ps1.where(F.col(TARGET_COL).isin(0, 1))\n"
            f"  which drops every row whose 3-day outcome is not yet known — i.e.\n"
            f"  every recent day. Scoring frames must be built with with_label=False.\n"
            f"  Refusing to write an empty partition: downstream, empty and healthy\n"
            f"  are indistinguishable."
        )
    return n


def build_fleet(fleet: str) -> dict:
    slug = FLEETS[fleet]
    print(f"\n{'=' * 62}\n {fleet}\n{'=' * 62}")

    contract = _load_contract(fleet)
    print(f"  contract: {len(contract)} features (pinned)")

    # ---- the three extracted functions, called with a SCORING window -------
    # with_label=False is TRAP 1. start_day/asof_date is TRAP 2.
    df = read_spine(spark, fleet, start_day, asof_date, with_label=False)   # noqa: F821
    _assert_rows(df, fleet, "read_spine")

    df = add_auxiliary(spark, df, fleet, start_day, asof_date)              # noqa: F821
    df, _recomputed_cols = join_and_materialise(spark, df, fleet)           # noqa: F821
    # _recomputed_cols is DISCARDED on purpose. TRAP 3: the contract wins.

    # ---- emit ONE day. The other 96 existed only to fill the windows. -----
    day = df.filter(F.col("transit_day") == F.to_date(F.lit(asof_date)))
    n = _assert_rows(day, fleet, f"the {asof_date} slice")

    # ---- assert the contract BEFORE writing -------------------------------
    have = set(day.columns)
    missing = [c for c in contract if c not in have]
    if missing:
        raise RuntimeError(
            f"[{fleet}] {len(missing)} of {len(contract)} pinned features are missing "
            f"from the built frame: {missing[:20]}\n"
            f"  A source did not land, or the extraction dropped a column. Writing "
            f"this frame would push the failure into the scoring job, where "
            f"reindex(fill_value=0.0) turns each absent feature into a plausible 0.0."
        )

    ident = [c for c in ("DEVICE_KEY", "DEVICE_ID", "transit_day",
                         "FACILITY_ID", "OPERATOR_ID", "mars_device_category")
             if c in have]
    for k in ("DEVICE_KEY", "transit_day"):
        if k not in ident:
            raise RuntimeError(f"[{fleet}] identity column '{k}' missing — a "
                               f"probability could not be attached to a device.")

    out = day.select(*ident, *contract)

    # grain: one row per device per day
    dupes = (out.groupBy("DEVICE_KEY", "transit_day").count()
             .where(F.col("count") > 1).count())
    if dupes:
        raise RuntimeError(
            f"[{fleet}] {dupes} duplicate (DEVICE_KEY, transit_day) groups. "
            f"An auxiliary join fanned out. Scoring a fanned-out frame silently "
            f"double-counts devices.")

    uri = f"s3://{bucket}/{FEATURES_PREFIX}/asof={asof_date}/fleet={slug}/"
    print(f"  rows={n:,}  features={len(contract)}  dupes=0")
    if dry_run:
        print(f"  DRY RUN — would write {uri}")
    else:
        out.write.mode("overwrite").parquet(uri)
        verified = spark.read.parquet(uri).count()
        if verified != n:
            raise RuntimeError(f"[{fleet}] wrote {n} rows, read back {verified}.")
        print(f"  wrote {uri}  verified={verified:,}")

    return {"fleet": fleet, "rows": n, "n_features": len(contract), "uri": uri}


targets = list(FLEETS) if fleet_param == "ALL" else [fleet_param]
results, failures = [], []
for f in targets:
    try:
        results.append(build_fleet(f))
    except Exception as exc:                                     # noqa: BLE001
        print(f"[{f}] FAILED: {exc}")
        failures.append({"fleet": f, "error": str(exc)})

manifest = {
    "job": "ps1_build_features_daily",
    "asof_date": asof_date,
    "lookback_days": LOOKBACK_DAYS,
    "run_ts_utc": datetime.now(timezone.utc).isoformat(),
    "fleets_built": [r["fleet"] for r in results],
    "fleets_failed": failures,
    "partial": bool(failures),
    "results": results,
    "schema_version": "1.0",
}
print("\n" + json.dumps(manifest, indent=2))

if not dry_run:
    boto3.client("s3").put_object(
        Bucket=bucket,
        Key=f"{FEATURES_PREFIX}/manifest/asof={asof_date}/manifest.json",
        Body=json.dumps(manifest, indent=2).encode())

if not results:
    raise RuntimeError(
        f"ALL FLEETS FAILED for {asof_date}. Not signalling completion — the "
        f"scoring job must not run against a frame that was never built.")
if failures:
    print(f"[WARN] PARTIAL: {[f['fleet'] for f in failures]} failed. The manifest "
          f"carries partial=true; the scoring job scores only what exists.")
