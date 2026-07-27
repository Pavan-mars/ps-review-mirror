"""Patch PS1 GATE OOS v4 — PS4 artifacts-bucket join + metric_daily v2 schema."""
import json
from pathlib import Path

NB = Path(__file__).with_name("PS1_3d_GATE_OOS_Optimized_SageMaker_v4.ipynb")
nb = json.loads(NB.read_text(encoding="utf-8"))


def cell_text(cell):
    src = cell.get("source", [])
    return "".join(src) if isinstance(src, list) else src


def set_cell_text(cell, text):
    cell["source"] = text.splitlines(keepends=True)


MARKDOWN_V43 = """
## v4.3 — PS4 scored + metric_daily v2

| Change | Why |
|---|---|
| `ARTIFACT_BUCKET` for PS4 scored | PS4 CELL 19 writes to artifacts bucket, not gold |
| PS4 join schema fix | Export uses `device_id`, `detected_at`, `device_type=GATE` |
| Latest `asof_date` dedupe | Avoid duplicate rows when multiple PS4 export runs exist |
| `silver.metric_daily` v2 cols | M401 tap-timing + comms (`m401_*`, `comms_*`) replace retired KPI cols |
| PS4 prior-window features | 7-day prior anomaly hours/score for temporal signal |

"""

for cell in nb["cells"]:
    t = cell_text(cell)
    if "## v4.2" in t and "v4.3" not in t:
        set_cell_text(cell, t.rstrip() + MARKDOWN_V43)
        break

for cell in nb["cells"]:
    t = cell_text(cell)
    if 'S3_SILVER = "s3://' in t and "ARTIFACT_BUCKET" not in t:
        t = t.replace(
            'S3_GOLD = "s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/gold"\n',
            'S3_GOLD = "s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/gold"\n'
            'ARTIFACT_BUCKET = "cubic-mars-pm-s3-datalake-dev-artifacts-170202974600"\n',
        )
        t = t.replace(
            'PS4_SCORED_S3 = S3_GOLD.replace("/gold", "") + "/ps4/scored"  # chicago/ps4/scored\n',
            'PS4_SCORED_S3 = f"s3://{ARTIFACT_BUCKET}/chicago/ps4/scored"  # PS4 CELL 19 artifacts bucket\n',
        )
        set_cell_text(cell, t)
        break

for cell in nb["cells"]:
    t = cell_text(cell)
    if 'METRIC_FEATURES = [' in t and "met_slow_tap_pct" not in t:
        t = t.replace(
            'METRIC_FEATURES = [\n'
            '    "met_avail_pct", "met_downtime_min", "met_event_count", "met_p95_downtime",\n'
            ']\n',
            'METRIC_FEATURES = [\n'
            '    "met_slow_tap_pct", "met_p95_txn_ms", "met_comms_count", "met_z_score_28d",\n'
            '    "met_txn_count", "met_comms_flag",\n'
            ']\n',
        )
        t = t.replace(
            '    "comms_x_hw_oos_prior",\n'
            ']\n',
            '    "comms_x_hw_oos_prior",\n'
            '    "ps4_anomaly_hours", "ps4_anomaly_score_mean", "ps4_anomaly_flag",\n'
            '    "ps4_signal_active_max", "ps4_if_score_mean",\n'
            '    "ps4_anomaly_hours_prior_7d", "ps4_anomaly_score_prior_7d",\n'
            ']\n',
        )
        set_cell_text(cell, t)
        break

PS4_JOIN_BLOCK = '''# Optional PS4 scored daily aggregates (run PS4 anomaly CELL 19 first)
if JOIN_PS4_SCORED:
    try:
        _ps4_base = spark_path(PS4_SCORED_S3)
        _ps4_glob = f"{_ps4_base}/asof=*/anomalies/"
        _ps4_raw = spark.read.parquet(_ps4_glob)
        _latest_asof = None
        _ps4_lc = {c.casefold(): c for c in _ps4_raw.columns}
        if "asof_date" in _ps4_raw.columns:
            _latest_asof = _ps4_raw.agg(F.max("asof_date")).collect()[0][0]
            _ps4_raw = _ps4_raw.where(F.col("asof_date") == F.lit(_latest_asof))
            print(f"PS4 scored: using asof_date={_latest_asof}")
        _dev_col = _ps4_lc.get("device_id") or _ps4_lc.get("device_id".upper()) or "device_id"
        if _dev_col in _ps4_raw.columns and "DEVICE_ID" not in _ps4_raw.columns:
            _ps4_raw = _ps4_raw.withColumn("DEVICE_ID", F.col(_dev_col))
        if "transit_day" not in _ps4_raw.columns:
            _dt_src = _ps4_lc.get("detected_at") or _ps4_lc.get("hour_dt") or _ps4_lc.get("hour_bucket")
            if _dt_src and _dt_src in _ps4_raw.columns:
                _ps4_raw = _ps4_raw.withColumn("transit_day", F.to_date(F.col(_dt_src)))
        _dtype_col = _ps4_lc.get("device_type")
        if _dtype_col and _dtype_col in _ps4_raw.columns:
            _ps4_raw = _ps4_raw.where(F.upper(F.col(_dtype_col)) == F.lit(DEVICE_CAT))
        _score_col = "anomaly_score" if "anomaly_score" in _ps4_raw.columns else None
        _flag_col = (
            "ensemble_anomaly_flag" if "ensemble_anomaly_flag" in _ps4_raw.columns
            else "is_anomaly" if "is_anomaly" in _ps4_raw.columns else None
        )
        _agg = [F.count(F.lit(1)).alias("ps4_anomaly_hours")]
        if _score_col:
            _agg.append(F.mean(F.col(_score_col).cast("double")).alias("ps4_anomaly_score_mean"))
        if _flag_col:
            _agg.append(F.max(F.col(_flag_col).cast("int")).alias("ps4_anomaly_flag"))
        if "signal_active_count" in _ps4_raw.columns:
            _agg.append(F.max(F.col("signal_active_count").cast("int")).alias("ps4_signal_active_max"))
        _ps4_daily = _ps4_raw.groupBy("DEVICE_ID", "transit_day").agg(*_agg)
        df_joined = df_joined.join(_ps4_daily, on=["DEVICE_ID", "transit_day"], how="left")
        print(f"Joined PS4 scored anomalies from {_ps4_base} ({_ps4_daily.count():,} device-days)")

        # Outliers parquet has hourly if_score for all devices — richer daily coverage
        try:
            _out_glob = f"{_ps4_base}/asof=*/outliers/"
            _out_raw = spark.read.parquet(_out_glob)
            if "asof_date" in _out_raw.columns and _latest_asof is not None:
                _out_raw = _out_raw.where(F.col("asof_date") == F.lit(_latest_asof))
            _out_lc = {c.casefold(): c for c in _out_raw.columns}
            _out_dev = _out_lc.get("device_id", "DEVICE_ID")
            if _out_dev in _out_raw.columns and "DEVICE_ID" not in _out_raw.columns:
                _out_raw = _out_raw.withColumn("DEVICE_ID", F.col(_out_dev))
            _rec = _out_lc.get("recorded_at", "recorded_at")
            if "transit_day" not in _out_raw.columns and _rec in _out_raw.columns:
                _out_raw = _out_raw.withColumn("transit_day", F.to_date(F.col(_rec)))
            _out_dtype = _out_lc.get("device_type")
            if _out_dtype and _out_dtype in _out_raw.columns:
                _out_raw = _out_raw.where(F.upper(F.col(_out_dtype)) == F.lit(DEVICE_CAT))
            if "if_score" in _out_raw.columns or "z_score" in _out_raw.columns:
                _iscore = "if_score" if "if_score" in _out_raw.columns else "z_score"
                _out_daily = (
                    _out_raw.groupBy("DEVICE_ID", "transit_day")
                    .agg(F.mean(F.col(_iscore).cast("double")).alias("ps4_if_score_mean"))
                )
                df_joined = df_joined.join(_out_daily, on=["DEVICE_ID", "transit_day"], how="left")
                print(f"Joined PS4 outlier if_score daily aggregates")
        except Exception as _out_exc:
            print(f"INFO: PS4 outliers join skipped ({_out_exc})")

        if "ps4_anomaly_hours" in df_joined.columns:
            _w7 = Window.partitionBy("DEVICE_ID").orderBy(F.col("_day_epoch")).rangeBetween(-7 * 86_400, -1)
            df_joined = (
                df_joined
                .withColumn(
                    "ps4_anomaly_hours_prior_7d",
                    F.sum(F.coalesce(F.col("ps4_anomaly_hours"), F.lit(0.0))).over(_w7),
                )
            )
            if "ps4_anomaly_score_mean" in df_joined.columns:
                df_joined = df_joined.withColumn(
                    "ps4_anomaly_score_prior_7d",
                    F.avg(F.col("ps4_anomaly_score_mean").cast("double")).over(_w7),
                )
    except Exception as _ps4_exc:
        print(f"WARNING: PS4 scored join skipped ({_ps4_exc})")

'''

for cell in nb["cells"]:
    t = cell_text(cell)
    if "# Optional PS4 scored daily aggregates" in t and "_ps4_lc" not in t:
        start = t.index("# Optional PS4 scored daily aggregates")
        end = t.index('print("Spark prior-window GATE features materialized (lazy)")')
        t = t[:start] + PS4_JOIN_BLOCK + t[end:]
        set_cell_text(cell, t)
        break

METRIC_BLOCK_OLD = '''metric_raw_cols = [
    "availability_pct", "total_downtime_min", "total_events", "p95_downtime_min"
]
df_metric_raw = read_feature_source(
    "silver.metric_daily",
    f"{S3_SILVER_RUNTIME}/metric_daily/",
    metric_raw_cols,
    TRAIN_START,
    LABEL_CUTOFF_EXPR,
    required_features=metric_raw_cols,
    optional_source=True,
    optional_guidance=(
        "The expected metric fields are unavailable in this export, so metric_daily will be omitted. "
        "Inspect SOURCE_SKIPS and the source schema before defining any semantic aliases."
    ),
)
if df_metric_raw is None:
    df_metric = None
else:
    df_metric = df_metric_raw.select(
        *KEY_COLS,
        F.col("availability_pct").alias("met_avail_pct"),
        F.col("total_downtime_min").alias("met_downtime_min"),
        F.col("total_events").alias("met_event_count"),
        F.col("p95_downtime_min").alias("met_p95_downtime"),
    )
'''

METRIC_BLOCK_NEW = '''metric_raw_cols = [
    "m401_slow_tap_pct", "m401_p95_txn_time_ms", "comms_total_count",
    "m401_z_score_vs_28d", "m401_daily_txn_count", "comms_event_flag",
]
df_metric_raw = read_feature_source(
    "silver.metric_daily",
    f"{S3_SILVER_RUNTIME}/metric_daily/",
    metric_raw_cols,
    TRAIN_START,
    LABEL_CUTOFF_EXPR,
    required_features=[],  # v2 schema — join any available M401/comms columns
    optional_source=True,
    optional_guidance=(
        "silver.metric_daily v2 exposes M401 tap-timing and comms counters. "
        "Legacy availability_pct/total_downtime_min columns are retired."
    ),
)
if df_metric_raw is None:
    df_metric = None
else:
    _metric_alias = {
        "m401_slow_tap_pct": "met_slow_tap_pct",
        "m401_p95_txn_time_ms": "met_p95_txn_ms",
        "comms_total_count": "met_comms_count",
        "m401_z_score_vs_28d": "met_z_score_28d",
        "m401_daily_txn_count": "met_txn_count",
        "comms_event_flag": "met_comms_flag",
    }
    _metric_exprs = [F.col(k).alias(v) for k, v in _metric_alias.items() if k in df_metric_raw.columns]
    df_metric = df_metric_raw.select(*KEY_COLS, *_metric_exprs) if _metric_exprs else None
    if df_metric is None:
        print("WARNING: silver.metric_daily loaded but no v2 metric columns mapped — skipped")
'''

for cell in nb["cells"]:
    t = cell_text(cell)
    if 'metric_raw_cols = [' in t and "m401_slow_tap_pct" not in t:
        if METRIC_BLOCK_OLD in t:
            t = t.replace(METRIC_BLOCK_OLD, METRIC_BLOCK_NEW)
        set_cell_text(cell, t)
        break

NB.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Patched {NB.name} for v4.3")
