# Builds PS3_v2_OOS_Serial_SageMaker.ipynb from ps3_oos_engine_v2.py.
# Regenerated 21-Jul-2026 after: (1) the SEVERITY_COLLAPSE code-keyed fix, (2) 4 new leakage-safe
# enrichment joins (usage_lifecycle_daily/station_network_daily/metric_daily/device_uptime_intervals),
# (3) data-quality guardrails (availability_event_id dedup check, kpi_rule_id fill visibility,
# ServiceNow-conformance probe, auto-discovered candidate features). Splits the engine module on its
# own banner comments (`# ---...--- <title>`) so re-running this script after any future engine edit
# regenerates a notebook that can never drift from the tested .py — same convention as
# build_deepdive_notebook.py in the PS3_DeepDive_deploy_bundle sibling delivery.
# Run: python3 build_notebook.py
import re
import nbformat as nbf
from pathlib import Path

ENGINE_SRC = Path(__file__).parent / "ps3_oos_engine_v2.py"
OUT_PATH = Path(__file__).parent / "PS3_v2_OOS_Serial_SageMaker.ipynb"

src = ENGINE_SRC.read_text()
BANNER = re.compile(r"^# -{10,} (.+)$", re.MULTILINE)
matches = list(BANNER.finditer(src))
assert matches, "no banner comments found -- engine module structure changed unexpectedly"
preamble = src[:matches[0].start()]
# the file's top-of-module preamble (base imports: os/sys/json/pathlib/warnings/numpy/pandas) lives
# before the first banner comment -- capture it so it isn't silently dropped from the notebook.
assert "import numpy as np" in preamble and "import pandas as pd" in preamble, \
    "preamble no longer contains the expected base imports -- update build_notebook.py"
chunks = {}
for i, m in enumerate(matches):
    title = m.group(1).strip()
    start = m.start()
    end = matches[i + 1].start() if i + 1 < len(matches) else len(src)
    chunks[title] = src[start:end].rstrip() + "\n"

expected = ["CONFIG", "imports (graceful)", "serial grain (v2)", "tee logger", "loader (Spark->SQL->parquet)",
           "feature engineering", "enrichment joins (NEW 21-Jul-2026)", "leakage scan",
           "target prep", "model factory", "head trainer", "viz helpers",
           "scoring -> device + serial + incident", "device-type runner",
           "overview EDA (root folder)", "synthetic generator (SMOKE TEST ONLY)",
           "OOS spine guard (v2)", "manifest bridge (v2)", "main"]
missing = [e for e in expected if e not in chunks]
assert not missing, f"expected banner section(s) missing from engine module: {missing}"

# Strip the module's own `if __name__ == "__main__":` guard -- Jupyter's __name__ is ALSO
# "__main__", so left in place this fires main() the instant the cell executes, before the
# SMOKE_TEST_SYNTHETIC toggle cell below has even run. The notebook calls main() explicitly, later,
# in its own cell.
_guard_re = re.compile(r'\nif __name__ == "__main__":\n(?:.*\n)*$')
main_chunk = chunks["main"]
assert _guard_re.search(main_chunk), "engine module's __main__ guard text changed -- update this strip"
chunks["main"] = _guard_re.sub("\n", main_chunk).rstrip() + "\n"

def code(*titles):
    return nbf.v4.new_code_cell("\n".join(chunks[t] for t in titles))

def md(text):
    return nbf.v4.new_markdown_cell(text)

# --- inline ps3_serial_grain so the notebook is self-contained in SageMaker Studio ---
_SG_SRC = (Path(__file__).parent / "ps3_serial_grain.py").read_text()
_SG_CELL = (_SG_SRC.rstrip() + """


# Register as a module so the engine's `import ps3_serial_grain as SG` resolves whether you
# uploaded the .py alongside this notebook or only the notebook itself.
import sys as _sys, types as _types
_m = _types.ModuleType("ps3_serial_grain")
for _n in ("HW_COLS", "load_hw_config", "serial_uniqueness_report",
           "expand_serial_grain", "serial_rollup"):
    setattr(_m, _n, globals()[_n])
_sys.modules["ps3_serial_grain"] = _m
print("[serial] ps3_serial_grain registered - fan-out available")
""")


cells = []

cells.append(md(
"""# CUBIC MARS — Chicago / CTA-Ventra — **PS3: Root-Cause + Severity** (SageMaker)

**Two heads in one delivery** (per the standing v7.0 rules — PS3 is gated on **macro-F1**, never raw accuracy):

| Head | Target | Honest baseline | Gate |
|---|---|---|---|
| **Severity** | `failure_level_label` (code-keyed: PURCHASE_CARD / PURCHASE_PRODUCT / NONPAYMENT / ALL_PURCHASE / ALL_FUNCTIONS / BUS_READER_ASSEMBLY) | macro-F1 ≈ 0.74-0.77 on live TVM | macro-F1 ≥ 0.55 (provisional) |
| **Root-cause (component)** | `derived_component_type` (CSC_READER / PRINTER / BHU / CHU / GATE_MECH / COMMS) | macro-F1 ≈ 0.54-0.67 on live | macro-F1 ≥ 0.45 (provisional) |

**Honest scope banner.** The *true 9-class ServiceNow* root cause stays **BLOCKED** — `SVN_STAGE U_FS_FAULT_CODES / U_FS_ACTION_CODES = 0 rows` as of the 18-Jul-2026 live verdict. What we ship instead is a **derived component/subsystem root cause** built from the fault taxonomy + pre-incident behaviour — the honest, data-backed version. Section 3's new `probe_servicenow_conformance()` checks every run whether the 21-Jul-2026 ServiceNow conformance repoint (`silver.servicenow_incident_conformed`, deprecating bronze `cta_servicenow_incident`+`servicenow_incident` as join sources for `incident_history`/`incident_root_cause`/`incident_task_ci_link`) has unblocked this yet — diagnostic only, never changes what the two heads train on.

**Leakage discipline (this is where PS3 goes wrong).** The incident free-text *names the answer*, so `AE_FAULT_DESCRIPTION / AE_SYMPTOM / AE_PROBLEM / AE_RESOLUTION` and the 7 text-derived `desc_*_flag` columns are **hard-excluded** — training on them produced a fake 0.999 F1. Both heads train on **structured pre-incident device behaviour** (24h/7d event windows, subsystem counts, OOS history, component age, and — new in this revision — 4 additional leakage-safe as-of enrichment sources, see section 4). `sn_event_code_id` (the ServiceNow fault code) is kept but **flagged as the dominant driver** and put through the solo-AUC ≤ 0.95 leakage gate (it typically fails the gate and gets dropped — confirmed both in the 18-Jul live run and this revision's synthetic smoke test).

**Device scope.** `TVM` (dominant), `GATE` (sparse — honest caveat), `VALIDATOR` = **0 availability-event incidents** by construction → honest stub + a device_event **OOS-proxy** (validators surface as OOS `Set` events, not availability events).

**What changed 21-Jul-2026 (this revision):**
1. **Fixed `CONFIG["SEVERITY_COLLAPSE"]`** — the 18-Jul live run found `pred_severity_collapsed` collapsed to 100% MAJOR because the map was keyed on human-readable labels while real `failure_level_label` values are CODES. Now keyed to match `ps3_collapse_fix.py` exactly. Confirmed on this revision's synthetic smoke test: a believable 66/34 CRITICAL/MAJOR split, not 100% MAJOR.
2. **4 new leakage-safe enrichment joins** (section 4) — `usage_lifecycle_daily`, `station_network_daily`, `metric_daily` (M401 tap-timing + comms), `device_uptime_intervals` (silence/heartbeat precursor, same confirmed columns the PS1 AUC-boost patch validated in production). Each is an as-of (`merge_asof`, backward, 1-day-lag-guaranteed) join, CONFIG-gated, and skips silently if the export isn't available yet. All new columns land with an `enr_` prefix and automatically flow through the existing leakage scan + feature list — no separate wiring needed. On the synthetic smoke test these appeared in the top-5 SHAP drivers for both heads on both device types, without tripping the 0.95 leakage gate.
3. **Data-quality guardrails** (section 3) — an `availability_event_id` duplicate check + dedup (the notebook has always assumed gold arrives pre-deduped; the 21-Jul ServiceNow-conformance repoint could quietly break that assumption), a `kpi_rule_id` fill-rate print (still 0% as of the 17-Jul catalog validation — visibility only, not yet a feature), and the ServiceNow-conformance probe above.
4. **Auto-discovered candidate features** (section 4) — if `gold.device_ps3_incident` ever starts carrying richer conformed-ServiceNow columns (`sn_category`, `sn_priority`, `sn_chargeable_level`, `from_svn_stage`, `from_cta_sn_mirror`), `build_feature_list()` now detects and routes them through the same leakage gate automatically, rather than requiring a manual code change to notice them.

**Outputs (exhaustive — a superset of the prior `ps3_v3_outputs`).** Per device type and **per head**: model bake-off leaderboard; **per-model** confusion matrix + ROC(OVR) + PR(OVR) + feature-importance (LogReg/RF/HistGBM/XGBoost/LightGBM/CatBoost + an **Optuna-tuned XGBoost** challenger); champion confusion + per-class F1 + calibration/reliability; class-distribution; a full `classification_report` → `report.txt`; the SHAP suite (global importance + beeswarm + per-class mean-|SHAP| + dependence plots); a rich `champion_summary.json` (macro-F1, weighted-F1, accuracy, balanced-accuracy, ROC-AUC, **PR-AUC**, Cohen-κ, MCC, log-loss, per-class precision/recall/F1/support); plus the RDS-ready incident/device/**serial** prediction feeds and the joblib serving bundle. Every cell tees to `run_console_log.txt`; cross-device figures + RDS load manifest + model-registry rows land at the `PS3_outputs_pavan/` root. Each figure renders inline **and** is saved."""))

cells.append(md(
"""### Dependencies (SageMaker Studio kernel)
```bash
%pip install -q "numpy<2" pandas scikit-learn lightgbm xgboost catboost shap optuna pyarrow s3fs joblib matplotlib
```
Run `notebooks/export_gold_to_s3.py` + `export_silver_to_s3.py` in Databricks **first** so the S3 parquet exports exist (SageMaker Studio has no Spark — it reads the S3 export, not Unity Catalog directly). On a Databricks cluster the loader auto-detects Spark and reads UC with zero config. The 4 new enrichment sources (section 4) and the ServiceNow-conformance probe (section 3) each independently need their own S3 export or UC table to be populated — every one of them degrades to a silent skip (printed, not raised) if its source isn't there yet, so this notebook still runs end-to-end without them."""))

cells.append(md("## 1. Configuration — the only block you normally edit\nIncludes the fixed `SEVERITY_COLLAPSE` map, the 4 new `ENRICH_*` toggles + their table/parquet paths, the `PROBE_SERVICENOW_CONFORMANCE` diagnostic toggle, and `CANDIDATE_CONFORMANCE_FEATURES` (auto-discovered, not hardcoded as features)."))
cfg_cell = nbf.v4.new_code_cell(preamble.strip() + "\n\n" + chunks["CONFIG"]
                                + '\nCONFIG["SMOKE_TEST_SYNTHETIC"] = False   # True -> offline synthetic dry-run (no S3 / no Spark)\n')
cells.append(cfg_cell)

cells.append(md("## 2. Environment, graceful imports & the run-log tee"))
cells.append(md("""### 2b. Component/serial grain module (`ps3_serial_grain`)
Inlined so the notebook runs standalone. This is the fix for the v1 serial gap: it reads
`silver.hw_config_current` directly and keys the serial grain on **(device_id, serial)** because
`COMPONENT_SERIAL_NBR` is **not unique across devices** in the source. Every output row carries
`attribution_method` \u2014 `description_match` when the component description matched the
incident's `affected_component`, `exposure` when the component was merely installed at the time
(78.32% of incidents have a blank `affected_component`, so most rows are exposure). Attributed
and exposed counts are reported **separately**; a serial risk ranking must use the attributed
column or say plainly that it is exposure."""))
cells.append(nbf.v4.new_code_cell(_SG_CELL))
cells.append(code("imports (graceful)", "serial grain (v2)", "tee logger"))

cells.append(md("## 3. Data loader (Spark → databricks-sql → S3 parquet) + NEW data-quality guardrails\n`check_availability_event_dedup()` and `print_kpi_rule_fill()` are new in this revision (see the change list above) — they live alongside the loader since they run once, right after the gold load, before any per-device-type work starts. This cell only **defines** functions; nothing executes yet."))
cells.append(code("loader (Spark->SQL->parquet)"))

cells.append(md("""## 3b. OOS spine guard (v2)
PS3 v2 trains on **hardware OOS**. `is_chargeable` is a *contract* classification applied
**after** the physical event and is a strict **subset** of OOS \u2014 filtering on it teaches the
model the contract rather than the device. `assert_oos_spine()` prints the chargeable share for
reference and **raises** if the frame arrives already filtered to it. `SERIAL_UNIQUENESS` is
populated later by the fan-out and travels into every manifest."""))
cells.append(code("OOS spine guard (v2)"))

cells.append(md("## 4. Feature engineering + NEW leakage-safe enrichment joins\nTemporal + facility-frequency features (unchanged), then the 4 new `enrich_*` functions — each an as-of (`merge_asof`, backward, 1-day lag guaranteed by construction) join to a silver table, CONFIG-gated and independently skip-silent if unavailable. `enrich_all()` runs all four; every resulting `enr_*` column is auto-included by `build_feature_list()` (section 6) and auto-covered by the leakage scan (section 5) — no separate wiring. Still definitions only."))
cells.append(code("feature engineering", "enrichment joins (NEW 21-Jul-2026)"))

cells.append(md("## 5. Leakage scan (solo-AUC gate)\nUnchanged logic — now also scans every new `enr_*` / auto-discovered column, since `build_feature_list()` includes them in `feats` before this runs."))
cells.append(code("leakage scan"))

cells.append(md("## 6. Target prep + model factory\nBake-off builders (LogReg/RF/HistGBM/XGBoost/LightGBM/CatBoost) + the Optuna-tuned XGBoost challenger. Unchanged."))
cells.append(code("target prep", "model factory"))

cells.append(md("## 7. Head trainer\nBake-off, champion selection (macro-F1 gate), per-model + champion confusion/ROC/PR/calibration, `classification_report`, rich `champion_summary.json`, real per-instance-capable SHAP suite, and the joblib serving bundle. Unchanged."))
cells.append(code("head trainer"))

cells.append(md("## 8. Visualization helpers"))
cells.append(code("viz helpers"))

cells.append(md("## 9. Scoring & aggregation (incident / device / serial grain)\nUnchanged — the RDS-ready CSV feeds this produces are identical in shape; they just carry richer upstream features now."))
cells.append(code("scoring -> device + serial + incident"))

cells.append(md("## 10. Device-type runner\nPer-category orchestration: `enrich_all()` now runs here (right after `_ae_date` is built, before `build_feature_list()`), so every device type gets the same 4 new sources joined independently."))
cells.append(code("device-type runner"))

cells.append(md("## 11. Cross-device overview EDA (rich visualization)\nDefinition only — `main()` (section 13) calls this once real data is loaded."))
cells.append(code("overview EDA (root folder)"))

cells.append(md("""## 11b. Inference bridge \u2014 manifests + `score_new_data()`
`write_manifests()` emits one `manifest.json` beside every output CSV carrying
`table_name / grain / computed_date / run_id / s3_data_key / row_count`. That is the exact
contract **`cubic-mars-ps3-rds-push`** triggers on (S3 ObjectCreated, suffix `manifest.json`),
so daily inference needs no bespoke loader. `RDS_TABLE_PREFIX = "ps3v2_"` keeps v2 **additive**
\u2014 the live `ps3_*` tables are never written.

`score_new_data()` scores an arbitrary incident slice with the saved champion bundles by calling
the **same** `score_and_aggregate()` training uses, so a daily score cannot drift from what the
notebook produced."""))
cells.append(code("manifest bridge (v2)"))

cells.append(md("## 12. Synthetic generator (SMOKE TEST ONLY)\n`make_synthetic()`'s `failure_level_label` values are now CODE-style (matching real `gold.device_ps3_incident`) so the smoke test actually exercises the fixed `SEVERITY_COLLAPSE` map. `make_synthetic_enrichment_tables()` is new — it fabricates stand-ins for all 4 new silver sources so `--synthetic` genuinely runs the new join code paths rather than just skip-silently. Definition only — nothing runs until section 14."))
cells.append(code("synthetic generator (SMOKE TEST ONLY)"))

cells.append(md("## 13. Main entrypoint, run summary + RDS load manifest, and the offline-synthetic loader monkeypatch\n`main()` runs the whole pipeline in the right order: load gold (or fabricate synthetic + the 4 new synthetic enrichment tables) → the section-3 guardrails (`check_availability_event_dedup`, `print_kpi_rule_fill`) → `probe_servicenow_conformance()` → `overview_eda()` → `run_device_type()` per category in `CONFIG[\"DEVICE_SCOPE\"]` (which itself calls `enrich_all()` then trains both heads) → `_finalize()` (run summary, model-registry rows, RDS load manifest). The monkeypatch at the bottom lets `--synthetic`/`SMOKE_TEST_SYNTHETIC` mode serve the 4 new enrichment tables (and `None` for `incident_root_cause`, honestly reflecting that this sandbox has no live silver access) without touching the real Spark/S3 loader path. This cell only defines `main()`/`_finalize()`/the monkeypatch — nothing executes until section 14."))
cells.append(code("main"))

cells.append(md("## 14. Run it\nSet `CONFIG[\"SMOKE_TEST_SYNTHETIC\"]` in section 1 (`True` to dry-run offline first), then Run All up to here, then run this cell — this is the only cell in the notebook that actually executes the pipeline."))
cells.append(nbf.v4.new_code_cell('results = main(synthetic=CONFIG.get("SMOKE_TEST_SYNTHETIC", False))\nresults'))

cells.append(md(
"""## 15. Next steps — wire to RDS + dashboard (daily)

The CSV feeds this notebook writes map 1:1 onto the additive RDS migration shipped with this delivery
(`rds/04_phase1b_ps3_rootcause_and_serial.sql`):

| Notebook output | RDS table |
|---|---|
| `{cat}_incident_predictions.csv` | `ps3_incident_predictions` (both heads, one row per incident × head) |
| `{cat}_device_predictions.csv` | `ps3_device_predictions` (device grain) |
| `{cat}_serial_predictions.csv` | `ps3_serial_predictions` (serial grain — `matched_serial_nbr`) |
| `{cat}_*_shap_importance.csv` / `{cat}_prediction_explainability.csv` | `ps3_prediction_explainability` |
| `ps3_model_registry_rows.csv` | `ml_model_registry` / `ml_models` |

**Daily production path** (see `docs/PS3_End_to_End_Architecture_Daily.md`): Databricks incremental silver+gold (MERGE on new `availability_event_id`) → S3 export → SageMaker batch scoring (`inference.py`, both heads, `/ping`+`/invocations` tested locally first) → predictions to S3 → `ps3_rds_writer.py` upserts to RDS → the existing Lambda API + React dashboard read the new tables. Set `MLFLOW_TRACKING_URI` to log the champions to the SageMaker MLflow server and register to the `ps3-root-cause` / `ps3-severity` model package groups.

**Before the next live run:** make sure `export_silver_to_s3.py` (or the Databricks/UC read path) actually has fresh exports of `usage_lifecycle_daily`, `station_network_daily`, `metric_daily`, and `device_uptime_intervals` — the 4 new enrichment sources this revision added. If any is missing, this notebook still runs correctly (that source silently skips), just without that lift."""))

nb = nbf.v4.new_notebook()
nb["cells"] = cells
nb["metadata"] = {
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python", "version": "3.10"},
}
OUT_PATH.write_text(nbf.writes(nb))
print(f"wrote {OUT_PATH} ({len(cells)} cells)")
