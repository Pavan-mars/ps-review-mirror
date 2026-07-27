#!/usr/bin/env python3
"""S3 export for PS4 fault-clustering notebooks (GATE / TVM / VALIDATOR).

Writes device-grain cluster assignments + cluster summary parquet and a manifest
for a future load_ps4_clusters_to_rds.py handler (same contract as PS4 anomaly CELL 19).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd


def _asof_from_frame(df: pd.DataFrame, date_col: Optional[str]) -> str:
    if date_col and date_col in df.columns:
        s = pd.to_datetime(df[date_col], errors="coerce").dropna()
        if not s.empty:
            return s.max().strftime("%Y-%m-%d")
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _predict_clusters(model, feature_cols: list[str], df: pd.DataFrame) -> tuple[np.ndarray, Optional[np.ndarray]]:
    """Apply MLflow-logged sklearn Pipeline; return labels and optional confidence."""
    X = df.reindex(columns=feature_cols, fill_value=0.0)
    labels = model.predict(X)
    conf = None
    if hasattr(model, "predict_proba"):
        try:
            proba = model.predict_proba(X)
            conf = np.asarray(proba).max(axis=1)
        except Exception:
            conf = None
    return np.asarray(labels), conf


def export_fault_clusters_to_s3(
    *,
    df_devices: pd.DataFrame,
    feature_cols: list[str],
    model,
    device_type: str,
    champion_pipeline: str,
    champion_run_id: str,
    champion_silhouette: float,
    bucket: str,
    prefix: str = "chicago/ps4/clustering",
    city_code: str = "CHI",
    region: str = "us-east-1",
    device_id_col: str = "DEVICE_ID",
    device_key_col: str = "DEVICE_KEY",
    target_col: Optional[str] = "will_fail_3d",
    asof_date: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Export cluster assignments + summary to S3; return manifest dict."""
    device_type = str(device_type).upper()
    device_slug = device_type.lower()
    asof_date = asof_date or _asof_from_frame(df_devices, None)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = str(uuid.uuid4())

    feat = [c for c in feature_cols if c in df_devices.columns]
    if not feat:
        raise ValueError("No feature columns overlap between model and device frame.")

    labels, conf = _predict_clusters(model, feat, df_devices)
    out = df_devices.copy()
    out["cluster_id"] = labels.astype(int)
    if conf is not None:
        out["cluster_confidence"] = conf.astype(float)

    assign_cols = [
        c for c in [
            device_key_col, device_id_col, "cluster_id", "cluster_confidence",
            target_col,
        ]
        if c in out.columns or c in ("cluster_id", "cluster_confidence")
    ]
    assignments = out[[c for c in assign_cols if c in out.columns]].copy()
    assignments["city_id"] = city_code
    assignments["device_type"] = device_type
    assignments["champion_pipeline"] = champion_pipeline
    assignments["champion_run_id"] = champion_run_id
    assignments["champion_silhouette"] = float(champion_silhouette)
    assignments["asof_date"] = asof_date

    summary_rows = []
    for cid, grp in assignments.groupby("cluster_id"):
        row: dict[str, Any] = {
            "city_id": city_code,
            "device_type": device_type,
            "cluster_id": int(cid),
            "device_count": int(len(grp)),
            "asof_date": asof_date,
            "champion_pipeline": champion_pipeline,
        }
        if target_col and target_col in out.columns:
            merged = grp.merge(
                out[[device_key_col, target_col]].drop_duplicates(device_key_col),
                on=device_key_col,
                how="left",
            )
            row[f"mean_{target_col}"] = float(pd.to_numeric(merged[target_col], errors="coerce").mean())
        if "cluster_confidence" in grp.columns:
            row["mean_cluster_confidence"] = float(grp["cluster_confidence"].mean())
        summary_rows.append(row)
    summary = pd.DataFrame(summary_rows)

    base = f"s3://{bucket}/{prefix}/{device_slug}/asof={asof_date}"
    assign_uri = f"{base}/assignments/part-000.parquet"
    summary_uri = f"{base}/cluster_summary/part-000.parquet"
    manifest_key = f"{prefix}/manifest/asof={asof_date}/{device_slug}_manifest.json"
    manifest_uri = f"s3://{bucket}/{manifest_key}"

    storage = {"client_kwargs": {"region_name": region}}
    if not dry_run:
        assignments.to_parquet(assign_uri, index=False, storage_options=storage)
        summary.to_parquet(summary_uri, index=False, storage_options=storage)

    manifest = {
        "ps": "PS4",
        "pipeline": "fault_clustering",
        "city": city_code,
        "device_type": device_type,
        "asof_date": asof_date,
        "run_ts": run_ts,
        "run_id": run_id,
        "champion_pipeline": champion_pipeline,
        "champion_run_id": champion_run_id,
        "champion_silhouette": float(champion_silhouette),
        "n_devices": int(len(assignments)),
        "n_clusters": int(assignments["cluster_id"].nunique()),
        "paths": {
            "base": f"s3://{bucket}/{prefix}/{device_slug}/asof={asof_date}/",
            "assignments": assign_uri,
            "cluster_summary": summary_uri,
        },
        "manifest_uri": manifest_uri,
        "feature_count": len(feat),
    }
    if not dry_run:
        import boto3
        boto3.client("s3", region_name=region).put_object(
            Bucket=bucket,
            Key=manifest_key,
            Body=json.dumps(manifest, indent=2, default=str).encode("utf-8"),
            ContentType="application/json",
        )
    return manifest


def _spark_path(uri: str) -> str:
    uri = uri.rstrip("/") + "/"
    return "s3a://" + uri[len("s3://"):] if uri.startswith("s3://") else uri


def write_spark_parquet_s3(spark, sdf, uri: str, coalesce: int = 1) -> str:
    """Write Spark DataFrame to S3 as Snappy Parquet (s3a://)."""
    path = _spark_path(uri)
    (
        sdf.coalesce(int(coalesce))
        .write.mode("overwrite")
        .option("compression", "snappy")
        .parquet(path)
    )
    return path


def _normalize_pred_sdf(pred_sdf, F, device_key_col, device_id_col, target_col):
    """Standardize champion prediction output to assignment grain."""
    cluster_col = "cluster" if "cluster" in pred_sdf.columns else "prediction"
    cols = [F.col(device_key_col).alias(device_key_col)]
    if device_id_col in pred_sdf.columns:
        cols.append(F.col(device_id_col).alias(device_id_col))
    if target_col and target_col in pred_sdf.columns:
        cols.append(F.col(target_col).alias(target_col))
    cols.append(F.col(cluster_col).cast("int").alias("cluster_id"))
    out = pred_sdf.select(*cols)
    if "probability" in pred_sdf.columns:
        from pyspark.ml.functions import vector_to_array
        out = (
            pred_sdf.select(
                F.col(device_key_col).alias(device_key_col),
                *([F.col(device_id_col).alias(device_id_col)] if device_id_col in pred_sdf.columns else []),
                *([F.col(target_col).alias(target_col)] if target_col and target_col in pred_sdf.columns else []),
                F.col(cluster_col).cast("int").alias("cluster_id"),
                vector_to_array(F.col("probability")).alias("_prob"),
            )
            .withColumn("cluster_confidence", F.array_max(F.col("_prob")))
            .drop("_prob")
        )
    return out


def export_fault_clusters_pyspark_to_s3(
    spark,
    pred_sdf,
    F,
    *,
    device_type: str,
    champion_pipeline: str,
    champion_run_id: str,
    champion_silhouette: float,
    bucket: str,
    prefix: str = "chicago/ps4/clustering",
    city_code: str = "CHI",
    region: str = "us-east-1",
    device_key_col: str = "DEVICE_KEY",
    device_id_col: str = "DEVICE_ID",
    target_col: Optional[str] = "will_fail_3d",
    asof_date: Optional[str] = None,
    coalesce_partitions: int = 1,
    engine: str = "pyspark",
    dry_run: bool = False,
) -> dict[str, Any]:
    """PySpark export — same S3 contract as export_fault_clusters_to_s3."""
    device_type = str(device_type).upper()
    device_slug = device_type.lower()
    asof_date = asof_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = str(uuid.uuid4())

    base = pred_sdf
    assignments = (
        _normalize_pred_sdf(base, F, device_key_col, device_id_col, target_col)
        .withColumn("city_id", F.lit(city_code))
        .withColumn("device_type", F.lit(device_type))
        .withColumn("champion_pipeline", F.lit(str(champion_pipeline)))
        .withColumn("champion_run_id", F.lit(str(champion_run_id)))
        .withColumn("champion_silhouette", F.lit(float(champion_silhouette)))
        .withColumn("asof_date", F.lit(asof_date))
        .withColumn("engine", F.lit(engine))
    )

    agg_exprs = [F.count(F.lit(1)).alias("device_count")]
    if target_col and target_col in assignments.columns:
        agg_exprs.append(F.mean(F.col(target_col)).alias(f"mean_{target_col}"))
    if "cluster_confidence" in assignments.columns:
        agg_exprs.append(F.mean(F.col("cluster_confidence")).alias("mean_cluster_confidence"))

    summary = (
        assignments.groupBy("cluster_id")
        .agg(*agg_exprs)
        .withColumn("city_id", F.lit(city_code))
        .withColumn("device_type", F.lit(device_type))
        .withColumn("asof_date", F.lit(asof_date))
        .withColumn("champion_pipeline", F.lit(str(champion_pipeline)))
    )

    n_devices = assignments.count()
    n_clusters = assignments.select("cluster_id").distinct().count()

    s3_base = f"s3://{bucket}/{prefix}/{device_slug}/asof={asof_date}"
    assign_uri = f"{s3_base}/assignments/"
    summary_uri = f"{s3_base}/cluster_summary/"
    manifest_key = f"{prefix}/manifest/asof={asof_date}/{device_slug}_manifest.json"
    manifest_uri = f"s3://{bucket}/{manifest_key}"

    if not dry_run:
        write_spark_parquet_s3(spark, assignments, assign_uri, coalesce=coalesce_partitions)
        write_spark_parquet_s3(spark, summary, summary_uri, coalesce=coalesce_partitions)

    manifest = {
        "ps": "PS4",
        "pipeline": "fault_clustering",
        "engine": engine,
        "city": city_code,
        "device_type": device_type,
        "asof_date": asof_date,
        "run_ts": run_ts,
        "run_id": run_id,
        "champion_pipeline": champion_pipeline,
        "champion_run_id": champion_run_id,
        "champion_silhouette": float(champion_silhouette),
        "n_devices": int(n_devices),
        "n_clusters": int(n_clusters),
        "paths": {
            "base": f"{s3_base}/",
            "assignments": assign_uri,
            "cluster_summary": summary_uri,
        },
        "manifest_uri": manifest_uri,
    }
    if not dry_run:
        import boto3
        boto3.client("s3", region_name=region).put_object(
            Bucket=bucket,
            Key=manifest_key,
            Body=json.dumps(manifest, indent=2, default=str).encode("utf-8"),
            ContentType="application/json",
        )
    return manifest
