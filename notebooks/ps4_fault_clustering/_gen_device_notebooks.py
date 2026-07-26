"""Generate TVM / VALIDATOR fault clustering notebooks from GATE templates."""
import json
import copy
from pathlib import Path

BASE = Path(__file__).resolve().parent

DEVICES = {
    "TVM": {
        "device": "TVM",
        "device_lower": "tvm",
        "pandas_out": "ps4_clustering_tvm_outputs",
        "pyspark_out": "ps4_clustering_tvm_pyspark_outputs",
        "mlflow_model": "chicago-ps4-tvm-fault-clustering",
        "mlflow_model_pyspark": "chicago-ps4-tvm-fault-clustering-pyspark",
        "ckpt_grain": "tvm_device_grain.parquet",
        "ckpt_version": "ps4-cluster-tvm-pyspark-v1",
        "spark_app": "PS4-TVM-Clustering-PySpark",
        "nb_pandas": "PS4_FaultClustering_TVM.ipynb",
        "nb_pyspark": "PS4_FaultClustering_TVM_PySpark.ipynb",
        "generated_by": "PS4_FaultClustering_TVM",
        "generated_by_pyspark": "PS4_FaultClustering_TVM_PySpark",
        "gate_ref_pandas": "PS4_FaultClustering_GATE.ipynb",
        "tsne_comment": "None = all TVM devices after aggregation",
    },
    "VALIDATOR": {
        "device": "VALIDATOR",
        "device_lower": "validator",
        "pandas_out": "ps4_clustering_validator_outputs",
        "pyspark_out": "ps4_clustering_validator_pyspark_outputs",
        "mlflow_model": "chicago-ps4-validator-fault-clustering",
        "mlflow_model_pyspark": "chicago-ps4-validator-fault-clustering-pyspark",
        "ckpt_grain": "validator_device_grain.parquet",
        "ckpt_version": "ps4-cluster-validator-pyspark-v1",
        "spark_app": "PS4-VALIDATOR-Clustering-PySpark",
        "nb_pandas": "PS4_FaultClustering_VALIDATOR.ipynb",
        "nb_pyspark": "PS4_FaultClustering_VALIDATOR_PySpark.ipynb",
        "generated_by": "PS4_FaultClustering_VALIDATOR",
        "generated_by_pyspark": "PS4_FaultClustering_VALIDATOR_PySpark",
        "gate_ref_pandas": "PS4_FaultClustering_GATE.ipynb",
        "tsne_comment": "None = all VALIDATOR devices after aggregation",
    },
}


def replace_text(text: str, d: dict, pyspark: bool = False) -> str:
    r = text
    r = r.replace("PS4_FaultClustering_GATE_PySpark", d["generated_by_pyspark"])
    r = r.replace("PS4_FaultClustering_GATE", d["generated_by"])
    r = r.replace("chicago-ps4-gate-fault-clustering-pyspark", d["mlflow_model_pyspark"])
    r = r.replace("chicago-ps4-gate-fault-clustering", d["mlflow_model"])
    r = r.replace("ps4_clustering_pyspark_outputs", d["pyspark_out"])
    r = r.replace("ps4_clustering_outputs", d["pandas_out"])
    r = r.replace("gate_device_grain.parquet", d["ckpt_grain"])
    r = r.replace("ps4-cluster-gate-pyspark-v1", d["ckpt_version"])
    r = r.replace("PS4-GATE-Clustering-PySpark", d["spark_app"])
    r = r.replace("None = all GATE devices after aggregation", d["tsne_comment"])
    r = r.replace("GATE Model Registry", f"{d['device']} Model Registry")
    r = r.replace("GATE champion", f"{d['device']} champion")
    r = r.replace("PS4 GATE fault clustering", f"PS4 {d['device']} fault clustering")
    r = r.replace("tags.device_type = 'GATE'", f"tags.device_type = '{d['device']}'")
    r = r.replace('"device_type": "GATE"', f'"device_type": "{d["device"]}"')
    r = r.replace('_DEV = "GATE"', f'_DEV = "{d["device"]}"')
    r = r.replace('TARGET_DEVICE = "GATE"', f'TARGET_DEVICE = "{d["device"]}"')
    r = r.replace("[GATE]", f"[{d['device']}]")
    r = r.replace("## GATE —", f"## {d['device']} —")
    r = r.replace(": GATE", f": {d['device']}")
    r = r.replace("gate_sdf", f"{d['device_lower']}_sdf")
    r = r.replace("filter GATE", f"filter {d['device']}")
    r = r.replace("PS4_Clustering/PS4_FaultClustering_GATE", f"PS4_Clustering/PS4_FaultClustering_{d['device']}")
    r = r.replace("PS4_FaultClustering_GATE_output_", f"PS4_FaultClustering_{d['device']}_output_")
    if pyspark:
        r = r.replace(
            f"Full PySpark port of `{d['gate_ref_pandas']}`",
            f"Full PySpark port of `{d['gate_ref_pandas']}`",
        )
    return r


def transform_nb(src_path: Path, dst_path: Path, d: dict, pyspark: bool = False) -> None:
    nb = json.loads(src_path.read_text(encoding="utf-8"))
    nb = copy.deepcopy(nb)
    for cell in nb.get("cells", []):
        src = cell.get("source", [])
        if isinstance(src, list):
            cell["source"] = [replace_text(line, d, pyspark) for line in src]
        elif isinstance(src, str):
            cell["source"] = replace_text(src, d, pyspark)
    dst_path.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {dst_path.name} ({len(nb['cells'])} cells)")


def main() -> None:
    gate_pandas = BASE / "PS4_FaultClustering_GATE.ipynb"
    gate_pyspark = BASE / "PS4_FaultClustering_GATE_PySpark.ipynb"
    for cfg in DEVICES.values():
        transform_nb(gate_pandas, BASE / cfg["nb_pandas"], cfg, pyspark=False)
        transform_nb(gate_pyspark, BASE / cfg["nb_pyspark"], cfg, pyspark=True)


if __name__ == "__main__":
    main()
