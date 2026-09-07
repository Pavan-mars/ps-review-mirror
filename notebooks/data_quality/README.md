# Data Quality Framework (Great Expectations)

Databricks notebooks for medallion-layer DQ on `mars_dev`. Run in order after each layer is built.

| Notebook | Layer | Audit tables |
|----------|-------|--------------|
| `Raw_dq_framework.ipynb` | Raw S3 (optional, warning-only) | `audit.dq_raw_audit` |
| `Bronze_dq_framework.ipynb` | Bronze + Raw S3 | `audit.bronze_dq_scorecard_v1`, `audit.bronze_dq_expectation_results_v1` |
| `Silver_dq_framework.py` | Silver (29 tables) | `audit.silver_dq_scorecard_v1`, `audit.silver_dq_expectation_results_v1` |
| `Gold_dq_framework.py` | Gold (5 PS tables) | `audit.gold_dq_scorecard_v1`, `audit.gold_dq_expectation_results_v1` |

**Run order:** Raw DQ (optional) → Bronze DQ → `run_layer_silver` → Silver DQ → `run_layer_gold` → Gold DQ

**Silver checks (5 per table):** row count + bronze reconcile · PK/completeness · RI joins · freshness · business rules

**Gold checks (5 per PS table):** row count + silver source reconcile · grain/PK · completeness · RI joins + cross-gold spine · business invariants

Set `FAIL_ON_BLOCKING = True` in Bronze/Silver/Gold notebooks for scheduled jobs.

Lighter operational checks (non-GX): `notebooks/validation/validate_silver.py`, `validate_gold.py`
