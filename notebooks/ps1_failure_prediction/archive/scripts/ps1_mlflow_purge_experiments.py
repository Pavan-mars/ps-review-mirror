"""
MLflow reset helpers for SageMaker managed tracking server.

IMPORTANT: SageMaker managed MLflow does NOT allow mlflow gc (hard delete).
Error: "This cli can only be used with a backend that allows hard-deleting runs"

You have two options:
  A) restore soft-deleted experiments (same names; old runs return)
  B) use new experiment names (clean history) — set PS1_NAME_SUFFIX in notebook CELL 3
     e.g. PS1_NAME_SUFFIX = "failure-v2"
"""

from __future__ import annotations

import os

import mlflow
from mlflow.tracking import MlflowClient

REGION = "us-east-1"
ACCOUNT_ID = "170202974600"
TRACKING_SERVER_NAME = "cubic-mars-mlflow-server-dev"
MLFLOW_SERVER_ARN = (
    f"arn:aws:sagemaker:{REGION}:{ACCOUNT_ID}:mlflow-tracking-server/{TRACKING_SERVER_NAME}"
)

PS1_EXPERIMENT_NAMES = (
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


def _client() -> MlflowClient:
    os.environ["MLFLOW_TRACKING_SERVER_ARN"] = MLFLOW_SERVER_ARN
    mlflow.set_tracking_uri(MLFLOW_SERVER_ARN)
    return MlflowClient()


def list_soft_deleted_ps1_experiments() -> list[tuple[str, str]]:
    client = _client()
    out: list[tuple[str, str]] = []
    for name in PS1_EXPERIMENT_NAMES:
        exp = client.get_experiment_by_name(name)
        if exp is not None and exp.lifecycle_stage == "deleted":
            out.append((name, exp.experiment_id))
    return out


def restore_ps1_experiments() -> None:
    """Option A — restore soft-deleted PS1 experiments (unblocks notebooks; old runs return)."""
    client = _client()
    print(f"MLflow ARN: {MLFLOW_SERVER_ARN}\n")
    for name in PS1_EXPERIMENT_NAMES:
        exp = client.get_experiment_by_name(name)
        if exp is None:
            print(f"[SKIP] not found: {name}")
        elif exp.lifecycle_stage == "deleted":
            client.restore_experiment(exp.experiment_id)
            print(f"[OK] restored: {name} ({exp.experiment_id})")
        else:
            print(f"[OK] active: {name} ({exp.experiment_id})")


def create_ps1_experiments_with_suffix(suffix: str = "failure-v2") -> dict[str, str]:
    """
    Option B — fresh experiment names (clean MLflow history on SageMaker).

    Example suffix 'failure-v2' -> chicago-ps1-3d-gate-failure-v2
    Set the same suffix in notebook CELL 3: PS1_NAME_SUFFIX = "failure-v2"
    """
    client = _client()
    mapping: dict[str, str] = {}
    for cat in ("gate", "tvm", "validator"):
        name = f"chicago-ps1-3d-{cat}-{suffix}"
        exp = client.get_experiment_by_name(name)
        if exp is None:
            eid = client.create_experiment(name)
            print(f"[OK] created: {name} ({eid})")
        elif exp.lifecycle_stage == "deleted":
            client.restore_experiment(exp.experiment_id)
            print(f"[OK] restored: {name} ({exp.experiment_id})")
        else:
            print(f"[OK] exists: {name} ({exp.experiment_id})")
        mapping[cat.upper()] = name
    print(f"\nSet in notebook CELL 3: PS1_NAME_SUFFIX = \"{suffix}\"")
    print("Then re-run CELL 4 and apply_ps1_resource_names() / training cells.")
    return mapping


def purge_ps1_experiments(*, dry_run: bool = True) -> None:
    """Not supported on SageMaker managed MLflow — documents limitation."""
    deleted = list_soft_deleted_ps1_experiments()
    print("SageMaker managed MLflow does NOT support mlflow gc from Studio.")
    print("Hard permanent delete requires direct DB access (not available).\n")
    if deleted:
        print("Soft-deleted PS1 experiments:")
        for name, eid in deleted:
            print(f"  {name} ({eid})")
    print("\nUse instead:")
    print("  restore_ps1_experiments()                    # same names, old runs return")
    print("  create_ps1_experiments_with_suffix('failure-v2')  # clean new history")


if __name__ == "__main__":
    restore_ps1_experiments()
