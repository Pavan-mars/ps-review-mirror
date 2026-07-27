"""Add optional S3 export cell (CELL 15) to fault-clustering notebooks."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent

CONFIG_SNIPPET = '''
# ── S3 export (optional CELL 15 — same pattern as PS4 anomaly CELL 19) ───────
ENABLE_S3_EXPORT = True
PS4_CLUSTER_S3_PREFIX = "chicago/ps4/clustering"
CITY_CODE = "CHI"
'''

EXPORT_CELL = '''# ── CELL 15 : Export fault-clustering results to S3 (optional) ───────────────
# Run after CELL 14 (champion selected). Skips safely if ENABLE_S3_EXPORT=False.
# Writes: s3://{bucket}/chicago/ps4/clustering/<gate|tvm|validator>/asof=<date>/
#         assignments/ + cluster_summary/ + manifest JSON for RDS loader.

import sys
from pathlib import Path

if not ENABLE_S3_EXPORT:
    print("[SKIP] ENABLE_S3_EXPORT=False")
elif not globals().get("_champ_run_id"):
    print("[SKIP] Run CELL 14 first — champion run_id not set")
else:
    _helper_paths = [
        Path.cwd(),
        Path.cwd() / "ps4_fault_clustering",
        Path("/home/sagemaker-user/PS4_Clustering"),
        Path(__file__).resolve().parent if "__file__" in dir() else Path.cwd(),
    ]
    for _hp in _helper_paths:
        if (_hp / "ps4_cluster_s3_export.py").is_file():
            sys.path.insert(0, str(_hp))
            break
    from ps4_cluster_s3_export import export_fault_clusters_to_s3
    import mlflow.sklearn

    print(f"[S3] Loading champion model  pipeline={_champ_name}  run={_champ_run_id[:8]}...")
    _champ_model = mlflow.sklearn.load_model(f"runs:/{_champ_run_id}/model")
    _export_df = _samp(_DEV, None)
    _manifest = export_fault_clusters_to_s3(
        df_devices=_export_df,
        feature_cols=FEATURE_COLS,
        model=_champ_model,
        device_type=_DEV,
        champion_pipeline=str(_champ_name),
        champion_run_id=str(_champ_run_id),
        champion_silhouette=float(_champ_sil),
        bucket=S3_BUCKET,
        prefix=PS4_CLUSTER_S3_PREFIX,
        city_code=CITY_CODE,
        region=REGION,
        device_id_col=DEVICE_ID_COL,
        device_key_col="DEVICE_KEY",
        target_col=TARGET_COL,
    )
    print(f"[S3] Exported {_manifest['n_devices']:,} devices  "
          f"{_manifest['n_clusters']} clusters  asof={_manifest['asof_date']}")
    print(f"  assignments -> {_manifest['paths']['assignments']}")
    print(f"  summary     -> {_manifest['paths']['cluster_summary']}")
    print(f"  manifest    -> {_manifest['manifest_uri']}")
'''

HTML_CELL_MARKER = "import subprocess, os, datetime"


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

    has_export = any("CELL 15 : Export fault-clustering" in "".join(c.get("source", [])) for c in nb["cells"])
    if not has_export:
        insert_idx = None
        for i, cell in enumerate(nb["cells"]):
            t = "".join(cell.get("source", []))
            if HTML_CELL_MARKER in t or "nbconvert" in t and "html" in t.lower():
                insert_idx = i
                break
        if insert_idx is None:
            insert_idx = len(nb["cells"])
        nb["cells"].insert(
            insert_idx,
            {
                "cell_type": "code",
                "execution_count": None,
                "id": "ps4-cluster-s3-export",
                "metadata": {},
                "outputs": [],
                "source": EXPORT_CELL.splitlines(keepends=True),
            },
        )
        changed = True

    if changed:
        path.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Patched {path.name}")
    else:
        print(f"No changes {path.name}")


def main() -> None:
    for name in [
        "PS4_FaultClustering_GATE.ipynb",
        "PS4_FaultClustering_TVM.ipynb",
        "PS4_FaultClustering_VALIDATOR.ipynb",
    ]:
        p = BASE / name
        if p.is_file():
            patch_notebook(p)


if __name__ == "__main__":
    main()
