# Databricks notebook source
# =============================================================================
# export_gold_ps3_incremental_to_s3 -- push gold.device_ps3_incident to S3 for
# the SageMaker scorer + notebook. Writes TWO things:
#   1. FULL snapshot (overwrite) -> chicago/gold/device_ps3_incident/
#        (the PS3 notebook + EDA read this; parquet, no Delta protocol features)
#   2. NEW-INCIDENT slice        -> chicago/gold/device_ps3_incident_incr/asof=<date>/
#        (the daily batch scorer reads ONLY this -- score just the new incidents)
# Mirrors export_gold_to_s3.py conventions (parquet, formatCheck off, region).
# =============================================================================
from typing import Any
import datetime as _dt
dbutils: Any = globals().get("dbutils")
spark: Any = globals().get("spark")

dbutils.widgets.text("catalog", "mars_dev")
dbutils.widgets.text("bucket", "cubic-mars-pm-s3-datalake-dev-gold-170202974600")
dbutils.widgets.text("prefix", "chicago/gold")
dbutils.widgets.text("since_date", "")     # blank -> pull from ps3_daily_watermark

CAT = dbutils.widgets.get("catalog").strip()
BUCKET = dbutils.widgets.get("bucket").strip()
PREFIX = dbutils.widgets.get("prefix").strip().rstrip("/")
GOLD = f"{CAT}.gold.device_ps3_incident"
WM = f"{CAT}.audit.ps3_daily_watermark"
TODAY = _dt.date.today().strftime("%Y-%m-%d")

since = dbutils.widgets.get("since_date").strip()
if not since:
    r = spark.sql(f"SELECT MAX(last_transit_day) m FROM {WM}").collect()[0]["m"]
    since = r.strftime("%Y-%m-%d") if r is not None else "2024-01-01"

spark.conf.set("spark.databricks.delta.formatCheck.enabled", "false")
full_dest = f"s3://{BUCKET}/{PREFIX}/device_ps3_incident"
incr_dest = f"s3://{BUCKET}/{PREFIX}/device_ps3_incident_incr/asof={TODAY}"

# 1) full snapshot (overwrite) for the notebook/EDA
for sub in [f"{full_dest}/_delta_log", full_dest]:
    try: dbutils.fs.rm(sub, recurse=True)
    except Exception: pass
spark.table(GOLD).write.format("parquet").mode("overwrite").save(full_dest)
n_full = spark.read.parquet(full_dest).count()
print(f"[full]  {n_full:,} rows -> {full_dest}")

# 2) new-incident slice for the batch scorer (only transit_day >= watermark)
slice_df = spark.table(GOLD).where(f"transit_day >= DATE '{since}'")
n_incr = slice_df.count()
slice_df.write.format("parquet").mode("overwrite").save(incr_dest)
print(f"[incr]  {n_incr:,} new incidents (since {since}) -> {incr_dest}")

# 3) JSON-lines scoring feed for SageMaker Batch Transform.
#    The transform is ContentType application/json + SplitType LINE, and the
#    container's input_fn parses JSON records -- it cannot eat the parquet
#    slice (execution 53495197 failed exactly there, AlgorithmError 48s in).
#    predict_fn tolerates missing feature columns, so raw gold rows will not
#    crash the container -- but a declared feature the payload lacks is imputed
#    to a training median, producing a confident-looking label derived from a
#    constant. Parity between the champion bundle's feature list and these
#    columns is UNVERIFIED; verify before trusting any score.
#    Written per-part via boto3 so NO _SUCCESS/_committed
#    marker files land in the prefix -- the transform ingests every object
#    under its S3Prefix, and a marker file would poison the batch.
jsonl_dest = f"s3://{BUCKET}/{PREFIX}/device_ps3_incident_incr_jsonl/asof={TODAY}"
if n_incr == 0:
    print("[jsonl] 0 new incidents -- no scoring feed written (nothing to score)")
else:
    import boto3
    _s3 = boto3.client("s3")
    _prefix_key = jsonl_dest.replace(f"s3://{BUCKET}/", "")
    _part, _rows, _n = 0, [], 0
    for _r in slice_df.toJSON().toLocalIterator():
        _rows.append(_r); _n += 1
        if len(_rows) >= 100000:
            _s3.put_object(Bucket=BUCKET, Key=f"{_prefix_key}/scoring_input_{_part:04d}.jsonl",
                           Body="\n".join(_rows).encode("utf-8"))
            _part += 1; _rows = []
    if _rows:
        _s3.put_object(Bucket=BUCKET, Key=f"{_prefix_key}/scoring_input_{_part:04d}.jsonl",
                       Body="\n".join(_rows).encode("utf-8"))
        _part += 1
    print(f"[jsonl] {_n:,} records in {_part} file(s) -> {jsonl_dest}")

print(f"\nScorer input : s3://{BUCKET}/{PREFIX}/device_ps3_incident_incr/asof={TODAY}")
print(f"Notebook input: s3://{BUCKET}/{PREFIX}/device_ps3_incident")
# emit for the job's downstream task
# The scorer consumes the JSONL feed, not the parquet slice.
dbutils.jobs.taskValues.set(key="incr_s3_uri", value=jsonl_dest) if hasattr(dbutils, "jobs") else None
dbutils.jobs.taskValues.set(key="asof", value=TODAY) if hasattr(dbutils, "jobs") else None
