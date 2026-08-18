#!/usr/bin/env python3
"""Replace PS1 notebook CELLS 6-8 with thin wrappers around notebooks/ps1_features.py."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NB_DIR = ROOT / "notebooks" / "ps1_failure_prediction"
NOTEBOOKS = [
    "PS1_3d_GATE_SageMaker_MLflow_FeatureStore.ipynb",
    "PS1_3d_TVM_SageMaker_MLflow_FeatureStore.ipynb",
    "PS1_3d_VALIDATOR_SageMaker_MLflow_FeatureStore.ipynb",
]

CELL6 = '''\
#  CELL 6 : Gold/silver reads + hardware OOS label (PySpark)
# MAGIC %run ../ps1_features
if should_run_etl():
    df_ps1 = read_spine(
        spark,
        DEVICE_CAT,
        TRAIN_START,
        None,
        with_label=True,
        s3_gold=S3_GOLD_RUNTIME,
        s3_silver=S3_SILVER_RUNTIME,
    )
else:
    print("[CHECKPOINT] Skip CELL 6 ETL — restored/saved checkpoint.")
'''

CELL7 = '''\
#  CELL 7 : Auxiliary reads + prior-window tap rolling (PySpark)
if should_run_etl():
    _ps1_aux = add_auxiliary(
        spark,
        df_ps1,
        DEVICE_CAT,
        TRAIN_START,
        None,
        s3_gold=S3_GOLD_RUNTIME,
        s3_silver=S3_SILVER_RUNTIME,
    )
    df_ps2 = _ps1_aux["df_ps2"]
    df_ps4 = _ps1_aux["df_ps4"]
    df_ps5 = _ps1_aux["df_ps5"]
    df_metric = _ps1_aux["df_metric"]
    df_mttr = _ps1_aux["df_mttr"]
    df_usage_ext = _ps1_aux["df_usage_ext"]
    df_read_tap = _ps1_aux["df_read_tap"]
else:
    print("[CHECKPOINT] Skip CELL 7 ETL — restored/saved checkpoint.")
'''

CELL8 = '''\
#  CELL 8 : Joins, grain audit, materialize features (PySpark)
if should_run_etl():
    df_features, FEATURE_COLS = join_and_materialise(
        spark,
        {
            "df_ps1": df_ps1,
            "df_ps2": df_ps2,
            "df_ps4": df_ps4,
            "df_ps5": df_ps5,
            "df_metric": df_metric,
            "df_mttr": df_mttr,
            "df_usage_ext": df_usage_ext,
            "df_read_tap": df_read_tap,
        },
        DEVICE_CAT,
        with_label=True,
        materialize=True,
    )
else:
    print("[CHECKPOINT] Skip CELL 8 ETL — restored/saved checkpoint.")
'''


def to_source(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    return lines if lines else [text]


def patch_notebook(path: Path) -> None:
    nb = json.loads(path.read_text(encoding="utf-8"))
    patched = 0
    for cell in nb["cells"]:
        src = "".join(cell.get("source", []))
        if src.lstrip().startswith("#  CELL 6"):
            cell["source"] = to_source(CELL6)
            patched += 1
        elif src.lstrip().startswith("#  CELL 7"):
            cell["source"] = to_source(CELL7)
            patched += 1
        elif src.lstrip().startswith("#  CELL 8"):
            cell["source"] = to_source(CELL8)
            patched += 1
    if patched != 3:
        raise RuntimeError(f"{path.name}: expected 3 cells, patched {patched}")
    path.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Patched {path.name}")


def main() -> None:
    for name in NOTEBOOKS:
        patch_notebook(NB_DIR / name)


if __name__ == "__main__":
    main()
