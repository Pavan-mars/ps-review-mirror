"""Restore or recreate PS1 MLflow experiments after teardown soft-delete."""

from __future__ import annotations

import mlflow
from mlflow.tracking import MlflowClient

REGION = "us-east-1"
ACCOUNT_ID = "170202974600"
MLFLOW_SERVER_ARN = (
    f"arn:aws:sagemaker:{REGION}:{ACCOUNT_ID}:mlflow-tracking-server/cubic-mars-mlflow-server-dev"
)

PS1_EXPERIMENTS = (
    "chicago-ps1-3d-gate-failure",
    "chicago-ps1-3d-tvm-failure",
    "chicago-ps1-3d-validator-failure",
    "chicago-ps1-3d-gate-hardware-oos-sparkml",
    "chicago-ps1-3d-tvm-hardware-oos-sparkml",
    "chicago-ps1-3d-validator-hardware-oos-sparkml",
    "chicago-ps1-device-failure",
    "chicago-ps1-validator-failure",
    "chicago-ps1v-validator-failure",
)


def ensure_ps1_experiment(experiment_name: str, client: MlflowClient | None = None) -> str:
    mlflow.set_tracking_uri(MLFLOW_SERVER_ARN)
    client = client or MlflowClient()
    exp = client.get_experiment_by_name(experiment_name)
    if exp is None:
        eid = client.create_experiment(experiment_name)
        print(f"[OK] created: {experiment_name} ({eid})")
        return eid
    if exp.lifecycle_stage == "deleted":
        client.restore_experiment(exp.experiment_id)
        print(f"[OK] restored: {experiment_name} ({exp.experiment_id})")
        return exp.experiment_id
    print(f"[OK] active: {experiment_name} ({exp.experiment_id})")
    return exp.experiment_id


def restore_all_ps1_experiments() -> None:
    mlflow.set_tracking_uri(MLFLOW_SERVER_ARN)
    client = MlflowClient()
    print(f"MLflow URI: {MLFLOW_SERVER_ARN}\n")
    for name in PS1_EXPERIMENTS:
        try:
            ensure_ps1_experiment(name, client=client)
        except Exception as exc:
            print(f"[WARN] {name}: {exc}")


if __name__ == "__main__":
    restore_all_ps1_experiments()
