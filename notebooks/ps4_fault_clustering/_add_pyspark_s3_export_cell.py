"""Add optional self-contained S3 export cell (CELL 19) to PySpark fault-clustering notebooks."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent

CONFIG_SNIPPET = '''
# ── S3 export (optional CELL 19 — PySpark, same contract as PS4 anomaly CELL 19) ─
ENABLE_S3_EXPORT = True
PS4_CLUSTER_S3_PREFIX = "chicago/ps4/clustering"
CITY_CODE = "CHI"
S3_WRITE_PARTITIONS = 1
DATA_ASOF_DATE = None  # set in CELL 6 from gold max(event_date); optional override
'''

EXPORT_CELL = '''# ── CELL 19 : Export fault-clustering results to S3 (PySpark, optional) ─────
# Run after CELL 17 (champion selected). Self-contained — no external .py file required.
# Writes: s3://{bucket}/chicago/ps4/clustering/<device>/asof=<date>/assignments/ + summary/ + manifest

import json
import uuid
from datetime import datetime, timezone

ensure_spark_alive()
_apply_s3a_hadoop_conf(spark)

if not ENABLE_S3_EXPORT:
    print("[SKIP] ENABLE_S3_EXPORT=False")
elif not globals().get("_champ_run_id"):
    print("[SKIP] Run CELL 17 first — champion run_id not set")
else:

    def _cluster_spark_path(uri):
        uri = uri.rstrip("/") + "/"
        return "s3a://" + uri[len("s3://"):] if uri.startswith("s3://") else uri

    def _write_cluster_parquet(sdf, uri, coalesce=1):
        path = _cluster_spark_path(uri)
        (
            sdf.coalesce(int(coalesce))
            .write.mode("overwrite")
            .option("compression", "snappy")
            .parquet(path)
        )
        return path

    def _normalize_cluster_pred(pred_sdf):
        cluster_col = "cluster" if "cluster" in pred_sdf.columns else "prediction"
        if "probability" in pred_sdf.columns:
            from pyspark.ml.functions import vector_to_array
            out = pred_sdf.select(
                F.col(DEVICE_KEY).alias(DEVICE_KEY),
                *([F.col(DEVICE_ID_COL).alias(DEVICE_ID_COL)] if DEVICE_ID_COL in pred_sdf.columns else []),
                *([F.col(TARGET_COL).alias(TARGET_COL)] if TARGET_COL in pred_sdf.columns else []),
                F.col(cluster_col).cast("int").alias("cluster_id"),
                vector_to_array(F.col("probability")).alias("_prob"),
            ).withColumn("cluster_confidence", F.array_max(F.col("_prob"))).drop("_prob")
        else:
            out = pred_sdf.select(
                F.col(DEVICE_KEY).alias(DEVICE_KEY),
                *([F.col(DEVICE_ID_COL).alias(DEVICE_ID_COL)] if DEVICE_ID_COL in pred_sdf.columns else []),
                *([F.col(TARGET_COL).alias(TARGET_COL)] if TARGET_COL in pred_sdf.columns else []),
                F.col(cluster_col).cast("int").alias("cluster_id"),
            )
        return out

    def export_fault_clusters_pyspark_to_s3(
        pred_sdf, *, asof_date, coalesce_partitions=S3_WRITE_PARTITIONS,
    ):
        device_slug = TARGET_DEVICE.lower()
        run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        assignments = (
            _normalize_cluster_pred(pred_sdf)
            .withColumn("city_id", F.lit(CITY_CODE))
            .withColumn("device_type", F.lit(TARGET_DEVICE))
            .withColumn("champion_pipeline", F.lit(str(_champ_name)))
            .withColumn("champion_run_id", F.lit(str(_champ_run_id)))
            .withColumn("champion_silhouette", F.lit(float(_champ_sil)))
            .withColumn("asof_date", F.lit(asof_date))
            .withColumn("engine", F.lit("pyspark"))
        )
        agg_exprs = [F.count(F.lit(1)).alias("device_count")]
        if TARGET_COL in assignments.columns:
            agg_exprs.append(F.mean(F.col(TARGET_COL)).alias(f"mean_{TARGET_COL}"))
        if "cluster_confidence" in assignments.columns:
            agg_exprs.append(F.mean(F.col("cluster_confidence")).alias("mean_cluster_confidence"))
        summary = (
            assignments.groupBy("cluster_id").agg(*agg_exprs)
            .withColumn("city_id", F.lit(CITY_CODE))
            .withColumn("device_type", F.lit(TARGET_DEVICE))
            .withColumn("asof_date", F.lit(asof_date))
            .withColumn("champion_pipeline", F.lit(str(_champ_name)))
        )
        n_devices = assignments.count()
        n_clusters = assignments.select("cluster_id").distinct().count()
        s3_base = f"s3://{S3_BUCKET}/{PS4_CLUSTER_S3_PREFIX}/{device_slug}/asof={asof_date}"
        assign_uri = f"{s3_base}/assignments/"
        summary_uri = f"{s3_base}/cluster_summary/"
        manifest_key = f"{PS4_CLUSTER_S3_PREFIX}/manifest/asof={asof_date}/{device_slug}_manifest.json"
        _write_cluster_parquet(assignments, assign_uri, coalesce_partitions)
        _write_cluster_parquet(summary, summary_uri, coalesce_partitions)
        manifest = {
            "ps": "PS4",
            "pipeline": "fault_clustering",
            "engine": "pyspark",
            "city": CITY_CODE,
            "device_type": TARGET_DEVICE,
            "asof_date": asof_date,
            "run_ts": run_ts,
            "run_id": str(uuid.uuid4()),
            "champion_pipeline": str(_champ_name),
            "champion_run_id": str(_champ_run_id),
            "champion_silhouette": float(_champ_sil),
            "n_devices": int(n_devices),
            "n_clusters": int(n_clusters),
            "paths": {
                "base": f"{s3_base}/",
                "assignments": assign_uri,
                "cluster_summary": summary_uri,
            },
            "manifest_uri": f"s3://{S3_BUCKET}/{manifest_key}",
        }
        import boto3
        boto3.client("s3", region_name=REGION).put_object(
            Bucket=S3_BUCKET,
            Key=manifest_key,
            Body=json.dumps(manifest, indent=2, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        return manifest

    _champ_entry = PIPELINE_RESULTS.get(_champ_name)
    if _champ_entry is None:
        _champ_entry = next(
            (v for v in PIPELINE_RESULTS.values() if v.get("run_id") == _champ_run_id),
            None,
        )

    if _champ_entry and _champ_entry.get("pred_sdf") is not None:
        _pred_sdf = _champ_entry["pred_sdf"]
        print(f"[S3] Using cached pred_sdf from {_champ_name} (run={_champ_run_id[:8]}...)")
    elif _champ_entry and _champ_entry.get("pdf") is not None:
        _pdf = _champ_entry["pdf"]
        _cols = [DEVICE_KEY, "cluster"]
        if DEVICE_ID_COL in _pdf.columns:
            _cols.append(DEVICE_ID_COL)
        if TARGET_COL in _pdf.columns:
            _cols.append(TARGET_COL)
        _pred_sdf = spark.createDataFrame(_pdf[_cols].copy())
        print(f"[S3] Rebuilt pred_sdf from {_champ_name} pdf ({len(_pdf):,} rows, run={_champ_run_id[:8]}...)")
    else:
        import mlflow.spark
        print(f"[S3] Loading Spark ML champion model  pipeline={_champ_name}  run={_champ_run_id[:8]}...")
        _champ_model = mlflow.spark.load_model(f"runs:/{_champ_run_id}/model")
        if _champ_name in ("P3_TSNE_KMeans", "P5_IF_TSNE_KMeans"):
            raise RuntimeError(
                f"{_champ_name} logs KMeans-on-t-SNE only (not end-to-end). "
                "Re-run pipeline cells in this session so PIPELINE_RESULTS has pdf/pred_sdf, then CELL 19."
            )
        _pred_sdf = _champ_model.transform(device_sdf).withColumnRenamed("prediction", "cluster")
        if "cluster" not in _pred_sdf.columns and "prediction" in _pred_sdf.columns:
            _pred_sdf = _pred_sdf.withColumnRenamed("prediction", "cluster")

    # Resolve asof_date (checkpoint path skips df_full / {device}_sdf in memory)
    _asof_date = globals().get("DATA_ASOF_DATE")
    if not _asof_date and os.path.exists(SPARK_CKPT_META):
        import joblib
        _asof_date = joblib.load(SPARK_CKPT_META).get("asof_date")
    if not _asof_date:
        _device_daily = globals().get(f"{TARGET_DEVICE.lower()}_sdf")
        _df_full = globals().get("df_full")
        if _device_daily is None and _df_full is not None:
            _device_daily = _df_full.filter(F.col(DEV_COL) == TARGET_DEVICE)
        if _device_daily is not None and DATE_COL in _device_daily.columns:
            _asof_row = _device_daily.agg(F.max(F.to_date(F.col(DATE_COL))).alias("d")).collect()[0]["d"]
            if _asof_row is not None:
                _asof_date = pd.Timestamp(_asof_row).strftime("%Y-%m-%d")
    if not _asof_date:
        _asof_date = pd.Timestamp.utcnow().strftime("%Y-%m-%d")
        print(f"[S3] WARN: asof_date unknown — using UTC today: {_asof_date}")

    _manifest = export_fault_clusters_pyspark_to_s3(_pred_sdf, asof_date=_asof_date)
    print(f"[S3] Exported {_manifest['n_devices']:,} devices  "
          f"{_manifest['n_clusters']} clusters  asof={_manifest['asof_date']}")
    print(f"  assignments -> {_manifest['paths']['assignments']}")
    print(f"  summary     -> {_manifest['paths']['cluster_summary']}")
    print(f"  manifest    -> {_manifest['manifest_uri']}")
'''

SUMMARY_MARKER = "# ── CELL 18 : Run summary"
EXPORT_MARKER = "# ── CELL 19 : Export fault-clustering results to S3 (PySpark, optional)"


def patch_notebook(path: Path) -> None:
    nb = json.loads(path.read_text(encoding="utf-8"))
    changed = False

    for cell in nb["cells"]:
        src = cell.get("source", [])
        text = "".join(src) if isinstance(src, list) else src
        if "PIPELINE_RESULTS = {}" in text and "ENABLE_S3_EXPORT" not in text:
            text = text.replace("PIPELINE_RESULTS = {}", "PIPELINE_RESULTS = {}" + CONFIG_SNIPPET, 1)
            cell["source"] = text.splitlines(keepends=True)
            changed = True
        if "filter_string=f\"tags.device_type = 'GATE'" in text:
            text = text.replace(
                "filter_string=f\"tags.device_type = 'GATE'",
                "filter_string=f\"tags.device_type = '{TARGET_DEVICE}'",
            )
            cell["source"] = text.splitlines(keepends=True)
            changed = True

    for i, cell in enumerate(nb["cells"]):
        text = "".join(cell.get("source", []))
        if EXPORT_MARKER in text:
            nb["cells"][i] = {
                "cell_type": "code",
                "execution_count": None,
                "id": "ps4-cluster-pyspark-s3-export",
                "metadata": {},
                "outputs": [],
                "source": EXPORT_CELL.splitlines(keepends=True),
            }
            changed = True
            break
    else:
        insert_idx = len(nb["cells"])
        for i, cell in enumerate(nb["cells"]):
            if SUMMARY_MARKER in "".join(cell.get("source", [])):
                insert_idx = i
                break
        nb["cells"].insert(
            insert_idx,
            {
                "cell_type": "code",
                "execution_count": None,
                "id": "ps4-cluster-pyspark-s3-export",
                "metadata": {},
                "outputs": [],
                "source": EXPORT_CELL.splitlines(keepends=True),
            },
        )
        changed = True

    for cell in nb["cells"]:
        if cell.get("cell_type") != "markdown":
            continue
        text = "".join(cell.get("source", []))
        if "**Run order:**" in text and "→ **19** → 18" not in text:
            if "→ **19** → **19**" in text:
                text = text.replace("→ **19** → **19**", "→ **19** → 18")
            elif "→ **19**" not in text:
                text = text.replace("→ 17\n", "→ 17 → **19** → 18\n", 1)
            elif "→ **19**\n" in text and "→ 18" not in text:
                text = text.replace("→ **19**\n", "→ **19** → 18\n", 1)
            cell["source"] = text.splitlines(keepends=True)
            changed = True

    if changed:
        path.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Patched {path.name}")


def main() -> None:
    for name in [
        "PS4_FaultClustering_GATE_PySpark.ipynb",
        "PS4_FaultClustering_TVM_PySpark.ipynb",
        "PS4_FaultClustering_VALIDATOR_PySpark.ipynb",
    ]:
        p = BASE / name
        if p.is_file():
            patch_notebook(p)


if __name__ == "__main__":
    main()
