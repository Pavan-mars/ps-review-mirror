"""
PS1 full teardown — SageMaker, MLflow (managed), Feature Store, S3, local checkpoints.

Run on SageMaker Studio / notebook instance (needs execution role permissions).

Usage (terminal):
    python ps1_teardown_all.py              # preview
    python ps1_teardown_all.py --execute    # delete

Usage (SageMaker notebook — preferred):
    from ps1_teardown_all import run_teardown
    run_teardown(execute=False)   # preview
    run_teardown(execute=True)    # delete

    # Or:  %run ps1_teardown_all.py --execute
    # (parse_known_args ignores Jupyter kernel -f flags)
"""

from __future__ import annotations

import argparse
import re
import shutil
import time
from typing import Iterable

import boto3
from botocore.exceptions import ClientError

# ── Config (match PS1_3d_* notebooks) ────────────────────────────────────────
REGION = "us-east-1"
ACCOUNT_ID = "170202974600"
ARTIFACT_BUCKET = "cubic-mars-pm-s3-datalake-dev-artifacts-170202974600"
MLFLOW_SERVER_ARN = (
    f"arn:aws:sagemaker:{REGION}:{ACCOUNT_ID}:mlflow-tracking-server/cubic-mars-mlflow-server-dev"
)

# Name patterns for PS1 resources (current + legacy)
NAME_PATTERNS = (
    r"chicago-ps1",
    r"ps1-3d",
    r"ps1_3d",
    r"hardware-oos-sparkml",
    r"^ps1-",  # e.g. ps1-3d-tvm-device-failure
    r"^PS1_",  # e.g. PS1_TVM_OOS_Optimized_SageMaker
)

S3_PREFIXES = (
    "sagemaker/ps1-3d/",
    "sagemaker/ps1/",
    "feature-store/chicago-ps1",
    "feature-store/ps1-3d",
)

LOCAL_PATHS = (
    "/home/sagemaker-user/PS1G_OOS",
    "/home/sagemaker-user/PS1T_OOS",
    "/home/sagemaker-user/PS1V",
    "/home/sagemaker-user/PS1V/checkpoints",
    "/home/sagemaker-user/mlruns",  # local MLflow fallback only
)

# Explicit legacy resources (safe to attempt even if missing)
EXPLICIT_ENDPOINTS = (
    "chicago-ps1-failure-v1",
    "chicago-ps1-3d-tvm-failure-v1",
    "chicago-ps1-3d-gate-failure-v1",
    "chicago-ps1-3d-validator-failure-v1",
    "chicago-ps1-3d-tvm-hardware-oos-sparkml-v1",
    "chicago-ps1-3d-gate-hardware-oos-sparkml-v1",
    "chicago-ps1-3d-validator-hardware-oos-sparkml-v1",
)

EXPLICIT_MONITOR_SCHEDULES = (
    "chicago-ps1-data-quality",
    "chicago-ps1-model-quality",
    "chicago-ps1-3d-tvm-model-quality",
    "chicago-ps1-3d-gate-model-quality",
    "chicago-ps1-3d-validator-model-quality",
)

EXPLICIT_MLFLOW_MODELS = (
    "chicago-ps1-device-failure",
    "chicago-ps1-3d-tvm-failure",
    "chicago-ps1-3d-gate-failure",
    "chicago-ps1-3d-validator-failure",
    "ps1-3d-tvm-device-failure",
    "ps1-3d-gate-device-failure",
    "ps1-3d-validator-device-failure",
    "PS1_TVM_OOS_Optimized_SageMaker",
    "PS1_GATE_OOS_Optimized_SageMaker",
    "PS1_TVM_Optimized_SageMaker_Baseline",
    "PS1_GATE_Optimized_SageMaker_Baseline",
)

EXPLICIT_MLFLOW_EXPERIMENTS = (
    "chicago-ps1-device-failure",
    "chicago-ps1-validator-failure",
    "chicago-ps1v-validator-failure",
    "chicago-ps1-3d-tvm-failure",
    "chicago-ps1-3d-gate-failure",
    "chicago-ps1-3d-validator-failure",
)

EXPLICIT_FEATURE_GROUPS = (
    "chicago-ps1-features",
    "chicago-ps1-3d-tvm-failure-features",
    "chicago-ps1-3d-gate-failure-features",
    "chicago-ps1-3d-validator-failure-features",
    "chicago-ps1-3d-tvm-features",
    "chicago-ps1-3d-gate-features",
    "chicago-ps1-3d-validator-features",
)

EXPLICIT_MODEL_PACKAGE_GROUPS = (
    "chicago-ps1-device-failure",
    "chicago-ps1-3d-tvm-failure",
    "chicago-ps1-3d-gate-failure",
    "chicago-ps1-3d-validator-failure",
)

# ── Helpers ───────────────────────────────────────────────────────────────────
_NAME_RES = [re.compile(p, re.I) for p in NAME_PATTERNS]


def _matches(name: str) -> bool:
    if not name:
        return False
    return any(r.search(name) for r in _NAME_RES)


def _log(dry_run: bool, action: str, resource: str) -> None:
    prefix = "[DRY-RUN]" if dry_run else "[DELETE]"
    print(f"  {prefix} {action}: {resource}")


def _ignore_not_found(exc: ClientError) -> bool:
    code = exc.response["Error"]["Code"]
    return code in ("ValidationException", "ResourceNotFound", "ResourceNotFoundException")


def _paginate(client, method_name: str, result_key: str, **kwargs):
    paginator = client.get_paginator(method_name)
    for page in paginator.paginate(**kwargs):
        for item in page.get(result_key, []):
            yield item


def _wait_endpoint_gone(sm, name: str, timeout_s: int = 900) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            sm.describe_endpoint(EndpointName=name)
        except ClientError as exc:
            if _ignore_not_found(exc):
                return
            raise
        time.sleep(15)
    raise TimeoutError(f"Endpoint still deleting: {name}")


def _unique(items: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ── SageMaker ─────────────────────────────────────────────────────────────────
def _collect_sm_names(sm) -> dict[str, list[str]]:
    endpoints = [e["EndpointName"] for e in _paginate(sm, "list_endpoints", "Endpoints")]
    endpoint_configs = [
        c["EndpointConfigName"] for c in _paginate(sm, "list_endpoint_configs", "EndpointConfigs")
    ]
    models = [m["ModelName"] for m in _paginate(sm, "list_models", "Models")]
    schedules = [
        s["MonitoringScheduleName"]
        for s in _paginate(sm, "list_monitoring_schedules", "MonitoringScheduleSummaries")
    ]
    mpg = [
        g["ModelPackageGroupName"]
        for g in _paginate(sm, "list_model_package_groups", "ModelPackageGroupSummaryList")
    ]
    fg = [
        g["FeatureGroupName"] for g in _paginate(sm, "list_feature_groups", "FeatureGroupSummaries")
    ]

    def filt(names):
        return sorted({n for n in names if _matches(n)})

    return {
        "endpoints": _unique([*filt(endpoints), *EXPLICIT_ENDPOINTS]),
        "endpoint_configs": _unique([*filt(endpoint_configs), *EXPLICIT_ENDPOINTS]),
        "models": filt(models),
        "monitor_schedules": _unique([*filt(schedules), *EXPLICIT_MONITOR_SCHEDULES]),
        "model_package_groups": _unique([*filt(mpg), *EXPLICIT_MODEL_PACKAGE_GROUPS]),
        "feature_groups": _unique([*filt(fg), *EXPLICIT_FEATURE_GROUPS]),
    }


def teardown_sagemaker(sm, dry_run: bool) -> None:
    print("\n=== SageMaker ===")
    names = _collect_sm_names(sm)

    for sched in names["monitor_schedules"]:
        if dry_run:
            _log(True, "stop+delete monitoring schedule", sched)
            continue
        try:
            sm.stop_monitoring_schedule(MonitoringScheduleName=sched)
        except ClientError as exc:
            if not _ignore_not_found(exc):
                print(f"  [WARN] stop schedule {sched}: {exc}")
        try:
            sm.delete_monitoring_schedule(MonitoringScheduleName=sched)
            print(f"  [OK] deleted monitoring schedule: {sched}")
        except ClientError as exc:
            if not _ignore_not_found(exc):
                print(f"  [WARN] delete schedule {sched}: {exc}")

    for ep in names["endpoints"]:
        if dry_run:
            _log(True, "delete endpoint", ep)
            continue
        try:
            sm.delete_endpoint(EndpointName=ep)
            _wait_endpoint_gone(sm, ep)
            print(f"  [OK] deleted endpoint: {ep}")
        except ClientError as exc:
            if not _ignore_not_found(exc):
                print(f"  [WARN] delete endpoint {ep}: {exc}")

    for cfg in names["endpoint_configs"]:
        if dry_run:
            _log(True, "delete endpoint config", cfg)
            continue
        try:
            sm.delete_endpoint_config(EndpointConfigName=cfg)
            print(f"  [OK] deleted endpoint config: {cfg}")
        except ClientError as exc:
            if not _ignore_not_found(exc):
                print(f"  [WARN] delete endpoint config {cfg}: {exc}")

    for model in names["models"]:
        if dry_run:
            _log(True, "delete SageMaker model", model)
            continue
        try:
            sm.delete_model(ModelName=model)
            print(f"  [OK] deleted SageMaker model: {model}")
        except ClientError as exc:
            if not _ignore_not_found(exc):
                print(f"  [WARN] delete model {model}: {exc}")

    for group in names["model_package_groups"]:
        if dry_run:
            _log(True, "delete model package group (+ packages)", group)
            continue
        try:
            while True:
                resp = sm.list_model_packages(ModelPackageGroupName=group, MaxResults=50)
                pkgs = resp.get("ModelPackageSummaryList", [])
                if not pkgs:
                    break
                for pkg in pkgs:
                    # API expects ModelPackageName (accepts name or ARN)
                    pkg_id = pkg.get("ModelPackageName") or pkg["ModelPackageArn"]
                    try:
                        sm.delete_model_package(ModelPackageName=pkg_id)
                        print(f"  [OK] deleted model package: {pkg_id}")
                        time.sleep(1)
                    except ClientError as exc:
                        if not _ignore_not_found(exc):
                            print(f"  [WARN] delete package {pkg_id}: {exc}")
            sm.delete_model_package_group(ModelPackageGroupName=group)
            print(f"  [OK] deleted model package group: {group}")
        except ClientError as exc:
            if not _ignore_not_found(exc):
                print(f"  [WARN] delete model package group {group}: {exc}")

    for fg in names["feature_groups"]:
        if dry_run:
            _log(True, "delete feature group", fg)
            continue
        try:
            sm.delete_feature_group(FeatureGroupName=fg)
            print(f"  [OK] deleted feature group: {fg}")
        except ClientError as exc:
            if not _ignore_not_found(exc):
                print(f"  [WARN] delete feature group {fg}: {exc}")


# ── MLflow (SageMaker managed) ────────────────────────────────────────────────
def teardown_mlflow(dry_run: bool) -> None:
    print("\n=== MLflow (managed server) ===")
    try:
        import mlflow
        from mlflow.tracking import MlflowClient
    except ImportError:
        print("  [SKIP] mlflow not installed")
        return

    mlflow.set_tracking_uri(MLFLOW_SERVER_ARN)
    client = MlflowClient()

    reg_names = set(EXPLICIT_MLFLOW_MODELS)
    try:
        for rm in client.search_registered_models(max_results=500):
            if _matches(rm.name):
                reg_names.add(rm.name)
    except Exception as exc:
        print(f"  [WARN] search_registered_models: {exc}")

    for name in sorted(reg_names):
        if dry_run:
            _log(True, "delete registered model (+ all versions)", name)
            continue
        try:
            client.delete_registered_model(name)
            print(f"  [OK] deleted registered model: {name}")
        except Exception as exc:
            print(f"  [WARN] delete registered model {name}: {exc}")

    exp_ids = {}
    try:
        for exp in client.search_experiments(max_results=500):
            if _matches(exp.name) or exp.name in EXPLICIT_MLFLOW_EXPERIMENTS:
                exp_ids[exp.experiment_id] = exp.name
    except Exception as exc:
        print(f"  [WARN] search_experiments: {exc}")

    for eid, ename in sorted(exp_ids.items(), key=lambda x: x[1]):
        if dry_run:
            _log(True, "delete experiment", f"{ename} ({eid})")
            continue
        try:
            client.delete_experiment(eid)
            print(f"  [OK] soft-deleted experiment: {ename}")
        except Exception as exc:
            print(f"  [WARN] delete experiment {ename}: {exc}")

    if not dry_run and exp_ids:
        # Soft-delete blocks reusing experiment names; gc permanently purges them.
        import subprocess

        try:
            proc = subprocess.run(
                [
                    "mlflow",
                    "gc",
                    "--tracking-uri",
                    MLFLOW_SERVER_ARN,
                    "--experiment-ids",
                    ",".join(exp_ids.keys()),
                    "--artifacts-destination",
                    f"s3://{ARTIFACT_BUCKET}",
                ],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            if proc.returncode == 0:
                print("  [OK] mlflow gc — permanently purged soft-deleted experiments")
            else:
                print(f"  [WARN] mlflow gc exit={proc.returncode}: {proc.stderr or proc.stdout}")
                print("  Run ps1_mlflow_purge_experiments.purge_ps1_experiments(dry_run=False)")
        except Exception as exc:
            print(f"  [WARN] mlflow gc failed: {exc}")
            print("  Run ps1_mlflow_purge_experiments.purge_ps1_experiments(dry_run=False)")


# ── S3 ────────────────────────────────────────────────────────────────────────
def _delete_s3_prefix(s3, bucket: str, prefix: str, dry_run: bool) -> int:
    count = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objs = page.get("Contents", [])
        if not objs:
            continue
        keys = [o["Key"] for o in objs]
        count += len(keys)
        if dry_run:
            _log(True, f"delete {len(keys)} S3 objects under", f"s3://{bucket}/{prefix}")
            continue
        for i in range(0, len(keys), 1000):
            batch = keys[i : i + 1000]
            s3.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
        print(f"  [OK] deleted {len(keys)} objects under s3://{bucket}/{prefix}")
    return count


def teardown_s3(s3, dry_run: bool) -> None:
    print(f"\n=== S3 ({ARTIFACT_BUCKET}) ===")
    total = 0
    for prefix in S3_PREFIXES:
        total += _delete_s3_prefix(s3, ARTIFACT_BUCKET, prefix, dry_run)
    if total == 0 and dry_run:
        print("  (no objects found under PS1 prefixes — may already be clean)")


# ── Local checkpoints ─────────────────────────────────────────────────────────
def teardown_local(dry_run: bool) -> None:
    print("\n=== Local SageMaker checkpoints ===")
    import os

    for path in LOCAL_PATHS:
        if not os.path.exists(path):
            continue
        if dry_run:
            _log(True, "remove local path", path)
            continue
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            os.remove(path)
        print(f"  [OK] removed: {path}")


# ── Main ──────────────────────────────────────────────────────────────────────
def run_teardown(*, execute: bool = False) -> None:
    dry_run = not execute
    print("=" * 72)
    print("PS1 TEARDOWN")
    print(f"  Region   : {REGION}")
    print(f"  Bucket   : {ARTIFACT_BUCKET}")
    print(f"  MLflow   : {MLFLOW_SERVER_ARN}")
    print(f"  Mode     : {'EXECUTE (destructive)' if execute else 'DRY-RUN (preview only)'}")
    print("=" * 72)

    if execute:
        print("\n*** EXECUTING DELETES IN 5 SECONDS — Ctrl+C to abort ***")
        time.sleep(5)

    sm = boto3.client("sagemaker", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)

    teardown_sagemaker(sm, dry_run)
    teardown_mlflow(dry_run)
    teardown_s3(s3, dry_run)
    teardown_local(dry_run)

    print("\n" + "=" * 72)
    if dry_run:
        print("DRY-RUN complete. Re-run with --execute (or EXECUTE=True) to delete.")
    else:
        print("Teardown complete. Re-run notebooks from CELL 4/5 with FORCE_RELOAD=True for fresh state.")
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(description="Delete all PS1 SageMaker/MLflow/S3 resources")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete (default is dry-run preview)",
    )
    # parse_known_args: Jupyter %run injects `-f <kernel.json>` — must ignore those.
    args, _unknown = parser.parse_known_args()
    run_teardown(execute=args.execute)


if __name__ == "__main__":
    main()
