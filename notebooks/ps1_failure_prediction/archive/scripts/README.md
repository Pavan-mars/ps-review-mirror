# PS1 SageMaker ops scripts (archived)

Run these from **SageMaker Studio** (same folder or on `PYTHONPATH`):

| Script | Purpose |
|--------|---------|
| `ps1_teardown_all.py` | Preview/delete PS1 endpoints, Feature Groups, S3 artifacts, local checkpoints |
| `ps1_mlflow_restore_experiments.py` | Restore soft-deleted MLflow experiments after teardown |
| `ps1_mlflow_purge_experiments.py` | Soft-delete MLflow experiments (managed server — no hard GC) |

```bash
cd notebooks/ps1_failure_prediction/archive/scripts
python ps1_teardown_all.py              # preview
python ps1_teardown_all.py --execute      # delete
python ps1_mlflow_restore_experiments.py  # restore experiments
```

After teardown + restore, re-run notebook CELL 11→13 to log fresh runs.
