#!/usr/bin/env python3
"""
ps4_v2_backfill_loader.py -- rewritten 29-Jul against the REAL S3 layout,
confirmed live (manifest JSON + parquet schema pulled from CloudShell):

  s3://<bucket>/chicago/ps4/clustering/manifest/asof=<date>/<device_type>_manifest.json
  s3://<bucket>/chicago/ps4/clustering/<device_type>/asof=<date>/assignments/*.parquet
  s3://<bucket>/chicago/ps4/clustering/<device_type>/asof=<date>/cluster_summary/*.parquet

For each device_type, reads the manifest for run metadata + the paths (do not
guess the paths -- the manifest carries them), reads both parquet sets,
recomputes n_devices/n_clusters independently rather than trusting the
manifest's own claim, upserts into ps4v2_clustering_assignments /
ps4v2_clustering_summary, and writes one ps4v2_load_audit row per device_type.

Usage:
  python3 ps4_v2_backfill_loader.py --asof-date 2026-07-28 --dry-run
  python3 ps4_v2_backfill_loader.py --asof-date 2026-07-28
  # --device-types tvm,gate,validator is the default; pass --device-types tvm to do just one

Requires: pyarrow, psycopg2-binary, boto3 (boto3 is preinstalled in CloudShell).
RDS connection: export PS4_RDS_DSN="host=... dbname=... user=... password=... sslmode=require"
"""
import argparse
import json
import sys

import boto3
import pyarrow.parquet as pq
import pyarrow.fs as pafs

BUCKET = "cubic-mars-pm-s3-datalake-dev-gold-170202974600"
BASE_PREFIX = "chicago/ps4/clustering"

ASSIGN_COLS = ["city_id", "device_type", "device_id", "device_key", "asof_date",
               "cluster_id", "will_fail_3d", "champion_pipeline", "champion_run_id",
               "champion_silhouette", "engine"]
SUMMARY_COLS = ["city_id", "device_type", "asof_date", "cluster_id", "device_count",
                "mean_will_fail_3d", "champion_pipeline"]


def get_manifest(s3, device_type: str, asof_date: str) -> dict:
    key = f"{BASE_PREFIX}/manifest/asof={asof_date}/{device_type.lower()}_manifest.json"
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    return json.loads(obj["Body"].read())


def read_parquet_dir(s3fs_client, s3_uri: str):
    # s3_uri like s3://bucket/prefix/ -- strip scheme, pyarrow wants bucket/prefix
    path = s3_uri.replace("s3://", "").rstrip("/")
    return pq.ParquetDataset(path, filesystem=s3fs_client).read().to_pandas()


def process_device_type(s3, s3fs_client, device_type: str, asof_date: str, dry_run: bool):
    manifest = get_manifest(s3, device_type, asof_date)
    print(f"\n=== {device_type} asof={asof_date} manifest: run_id={manifest['run_id']} "
          f"champion={manifest['champion_pipeline']} silhouette={manifest['champion_silhouette']:.4f} "
          f"n_devices={manifest['n_devices']} n_clusters={manifest['n_clusters']} ===")

    assign_df = read_parquet_dir(s3fs_client, manifest["paths"]["assignments"])
    summ_df = read_parquet_dir(s3fs_client, manifest["paths"]["cluster_summary"])
    assign_df.columns = [c.lower() if c.lower() in
                          ("device_key", "device_id") else c for c in assign_df.columns]
    # normalize DEVICE_KEY/DEVICE_ID -> device_key/device_id to match table columns
    rename = {}
    if "DEVICE_KEY" in assign_df.columns:
        rename["DEVICE_KEY"] = "device_key"
    if "DEVICE_ID" in assign_df.columns:
        rename["DEVICE_ID"] = "device_id"
    assign_df = assign_df.rename(columns=rename)
    assign_df["manifest_run_id"] = manifest["run_id"]
    summ_df["manifest_run_id"] = manifest["run_id"]

    n_devices_loaded = int(assign_df["device_id"].nunique())
    n_clusters_loaded = int(summ_df["cluster_id"].nunique())
    reasons = []
    if n_devices_loaded != manifest["n_devices"]:
        reasons.append(f"assignments has {n_devices_loaded} distinct devices, manifest claims {manifest['n_devices']}")
    if n_clusters_loaded != manifest["n_clusters"]:
        reasons.append(f"cluster_summary has {n_clusters_loaded} clusters, manifest claims {manifest['n_clusters']}")
    if assign_df["device_id"].isnull().any():
        reasons.append("null device_id in assignments")
    promoted = len(reasons) == 0
    print(f"  recomputed: n_devices_loaded={n_devices_loaded} n_clusters_loaded={n_clusters_loaded} "
          f"PROMOTE={promoted}")
    for r in reasons:
        print(f"  BLOCKED: {r}")

    if dry_run:
        return None

    return {
        "device_type": device_type, "asof_date": asof_date, "manifest": manifest,
        "assign_df": assign_df, "summ_df": summ_df,
        "n_devices_loaded": n_devices_loaded, "n_clusters_loaded": n_clusters_loaded,
        "promoted": promoted, "reasons": reasons,
    }


def upsert(cur, table, df, cols, key_cols):
    from psycopg2.extras import execute_values
    cols = [c for c in cols if c in df.columns]
    set_cols = [c for c in cols if c not in key_cols]
    rows = list(df[cols].itertuples(index=False, name=None))
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s "
           f"ON CONFLICT ({', '.join(key_cols)}) DO UPDATE SET "
           + ", ".join(f"{c}=EXCLUDED.{c}" for c in set_cols))
    execute_values(cur, sql, rows)
    print(f"  [{table}] upserted {len(rows)} row(s)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof-date", required=True)
    ap.add_argument("--device-types", default="tvm,gate,validator")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s3 = boto3.client("s3")
    s3fs_client = pafs.S3FileSystem()
    results = []
    for dt in args.device_types.split(","):
        results.append(process_device_type(s3, s3fs_client, dt.strip(), args.asof_date, args.dry_run))

    if args.dry_run:
        print("\n--dry-run set, no writes performed.")
        return 0

    import os
    import psycopg2
    dsn = os.environ.get("PS4_RDS_DSN")
    if not dsn:
        print("Set PS4_RDS_DSN before running for real.")
        sys.exit(1)
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            for r in results:
                if r is None:
                    continue
                upsert(cur, "ps4v2_clustering_assignments", r["assign_df"], ASSIGN_COLS,
                       ["city_id", "device_type", "device_id", "asof_date"])
                upsert(cur, "ps4v2_clustering_summary", r["summ_df"], SUMMARY_COLS,
                       ["city_id", "device_type", "asof_date", "cluster_id"])
                cur.execute(
                    "INSERT INTO ps4v2_load_audit (device_type, asof_date, manifest_run_id, "
                    "n_devices_manifest, n_devices_loaded, n_clusters_manifest, n_clusters_loaded, "
                    "champion_pipeline, champion_silhouette, promoted, notes) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (device_type, asof_date) DO UPDATE SET "
                    "n_devices_loaded=EXCLUDED.n_devices_loaded, n_clusters_loaded=EXCLUDED.n_clusters_loaded, "
                    "promoted=EXCLUDED.promoted, notes=EXCLUDED.notes, loaded_at=now()",
                    (r["device_type"], r["asof_date"], r["manifest"]["run_id"],
                     r["manifest"]["n_devices"], r["n_devices_loaded"],
                     r["manifest"]["n_clusters"], r["n_clusters_loaded"],
                     r["manifest"]["champion_pipeline"], r["manifest"]["champion_silhouette"],
                     r["promoted"], "; ".join(r["reasons"]) or "clean"),
                )
        conn.commit()
        print("\nCommitted.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
