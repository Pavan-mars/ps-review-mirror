# PS4 retired notebooks

Moved here 2026-08-19 - not part of the live pipeline. Nothing deleted; reversible via `git mv`.
Two files carry a source-folder prefix to avoid a name collision (both were `ps4_export_to_s3.py`):
`ps4__ps4_export_to_s3.py` (from `notebooks/ps4/`) and `ps4_anomaly__ps4_export_to_s3.py`
(from `notebooks/ps4_anomaly/`, which is now empty and drops out of the tree).

## Still live

| File | Role |
|---|---|
| `notebooks/ps4_fault_clustering/PS4_FaultClustering_GATE_PySpark.ipynb` | Production clustering, GATE (current outputs: manifest engine=pyspark, 16-Aug) |
| `notebooks/ps4_fault_clustering/PS4_FaultClustering_TVM_PySpark.ipynb` | Production clustering, TVM |
| `notebooks/ps4_fault_clustering/PS4_FaultClustering_VALIDATOR_PySpark.ipynb` | Production clustering, VALIDATOR |
| `notebooks/ps4_fault_clustering/ps4_cluster_s3_export.py` | Clustering S3 exporter - `chicago/ps4/clustering` + manifests (daily-loader source) |
| `notebooks/ps4_anomaly_detection/PS4_SageMaker_MLflow_FeatureStore_PySpark.ipynb` | Anomaly scoring - `chicago/ps4/scored` + `chicago/ps4/manifest` (daily loader; anomalies/outliers refused by design, timeline loads) |
| `notebooks/ps4/ps4_device_daily_export.py` | Designed CELL-19 add-on (device_daily / outlier_bins aggregates) - not yet deployed; kept as a design asset |

KNOWN GAP: no tracked code writes `chicago/ps4/v3` (the weekly family the Monday
loader reads). The producer is being recovered from SageMaker Studio; see
`docs/PS4_AUDIT_16Aug2026.md` Addendum B.

## Retired here

| File | Why |
|---|---|
| `PS4_FaultClustering_GATE.ipynb` | Plain (non-Spark) variant; superseded - current outputs are engine=pyspark. Named capacity fallback in the 16-Aug scheduling note; recover via git mv if that fallback is exercised |
| `PS4_FaultClustering_TVM.ipynb` | Same |
| `PS4_FaultClustering_VALIDATOR.ipynb` | Same |
| `PS4_SageMaker_MLflow_FeatureStore.ipynb` | Earlier non-Spark family; contains no live-prefix export code |
| `Chicago_PS4_Anomaly_Detection.ipynb` | Early analysis |
| `ps4__ps4_export_to_s3.py` | Writes `chicago/ml_outputs/ps4` - a path that "does not exist and never did" (daily-loader header, 27-Jul rewrite); its target tables are empty in Aurora |
| `ps4_anomaly__ps4_export_to_s3.py` | Same dead `chicago/ml_outputs/ps4` path; superseded by the real scored/clustering layout |