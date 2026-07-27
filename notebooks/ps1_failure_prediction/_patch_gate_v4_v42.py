"""Patch PS1 GATE OOS v4 notebook for v4.2 feature + selection improvements."""
import json
from pathlib import Path

NB = Path(__file__).with_name("PS1_3d_GATE_OOS_Optimized_SageMaker_v4.ipynb")
nb = json.loads(NB.read_text(encoding="utf-8"))


def cell_text(cell):
    src = cell.get("source", [])
    return "".join(src) if isinstance(src, list) else src


def set_cell_text(cell, text):
    cell["source"] = text.splitlines(keepends=True)


MARKDOWN_V42 = """
## v4.2 — feature quality + diverse ensembles

| Change | Why |
|---|---|
| Drop features >90% null | MTTR cols were ~99.7% null — median impute adds noise |
| Interaction features | Combine top permutation drivers (hw OOS × days-since-failure, etc.) |
| Optional PS4 scored join | Daily anomaly score/count from `chicago/ps4/scored/` when present |
| `SELECTION_OBJECTIVE=accuracy_mcc` | Balance accuracy with MCC on high-prevalence OOS label |
| Diverse soft-vote ensemble | One best spec per booster family (XGB + CatBoost + LGB) |
| Weighted ensemble scores | Validation-accuracy weights for soft vote |

"""

for cell in nb["cells"]:
    t = cell_text(cell)
    if "## v4.1 accuracy push" in t and "v4.2" not in t:
        set_cell_text(cell, t.rstrip() + MARKDOWN_V42)
        break

for cell in nb["cells"]:
    t = cell_text(cell)
    if "SELECTION_OBJECTIVE = \"accuracy\"" in t and "SPARSE_FEATURE_MAX_NULL_RATE" not in t:
        t = t.replace(
            'SELECTION_OBJECTIVE = "accuracy"  # "accuracy" | "avg_precision"',
            'SELECTION_OBJECTIVE = "accuracy_mcc"  # "accuracy" | "accuracy_mcc" | "avg_precision"',
        )
        t = t.replace(
            "STACK_TOP_K = 5\n",
            "STACK_TOP_K = 5\n"
            "SELECTION_MCC_WEIGHT = 0.35  # v4.2 composite: accuracy + weight*mcc\n"
            "SPARSE_FEATURE_MAX_NULL_RATE = 0.90  # drop ultra-sparse optional features\n"
            "ENSEMBLE_DIVERSIFY = True  # one top spec per booster family in soft vote\n"
            "JOIN_PS4_SCORED = True  # join daily PS4 anomaly aggregates when S3 path exists\n"
            "PS4_SCORED_S3 = S3_GOLD.replace(\"/gold\", \"\") + \"/ps4/scored\"  # chicago/ps4/scored\n",
        )
        set_cell_text(cell, t)
        break

for cell in nb["cells"]:
    t = cell_text(cell)
    if 'SPARK_DERIVED_FEATURES = [' in t and "hw_oos_x_days_since_failure" not in t:
        t = t.replace(
            '    "hardware_oos_count_prior_sum_7d", "array_peer_hw_oos_7d",\n',
            '    "hardware_oos_count_prior_sum_7d", "array_peer_hw_oos_7d",\n'
            '    "hw_oos_x_days_since_failure", "tap_reject_x_hw_oos_prior",\n'
            '    "comms_x_hw_oos_prior",\n',
        )
        t = t.replace(
            '    "anomaly_score_7d", "anomaly_score_30d",\n',
            '    "anomaly_score_7d", "anomaly_score_30d",\n'
            '    "ps4_anomaly_score_mean", "ps4_anomaly_hours", "ps4_anomaly_flag",\n',
        )
        set_cell_text(cell, t)
        break

for cell in nb["cells"]:
    t = cell_text(cell)
    if 'print("Spark prior-window GATE features materialized (lazy)")' in t and "hw_oos_x_days_since_failure" not in t:
        insert = '''
if "hardware_oos_count_prior_sum_7d" in df_joined.columns and "usage_days_since_last_failure" in df_joined.columns:
    df_joined = df_joined.withColumn(
        "hw_oos_x_days_since_failure",
        F.col("hardware_oos_count_prior_sum_7d") * F.coalesce(F.col("usage_days_since_last_failure"), F.lit(0.0)),
    )
if "gate_tap_reject_prior_7d" in df_joined.columns and "hardware_oos_count_prior_sum_7d" in df_joined.columns:
    df_joined = df_joined.withColumn(
        "tap_reject_x_hw_oos_prior",
        F.col("gate_tap_reject_prior_7d") * F.col("hardware_oos_count_prior_sum_7d"),
    )
if "comms_events_prior_sum_7d" in df_joined.columns and "hardware_oos_count_prior_sum_7d" in df_joined.columns:
    df_joined = df_joined.withColumn(
        "comms_x_hw_oos_prior",
        F.col("comms_events_prior_sum_7d") * F.col("hardware_oos_count_prior_sum_7d"),
    )

# Optional PS4 scored daily aggregates (run PS4 anomaly CELL 19 first)
if JOIN_PS4_SCORED:
    try:
        _ps4_base = spark_path(PS4_SCORED_S3)
        _ps4_glob = f"{_ps4_base}/asof=*/anomalies/"
        _ps4_raw = spark.read.parquet(_ps4_glob)
        if "hour_bucket" in _ps4_raw.columns and "transit_day" not in _ps4_raw.columns:
            _ps4_raw = _ps4_raw.withColumn("transit_day", F.to_date(F.col("hour_bucket")))
        _score_col = "anomaly_score" if "anomaly_score" in _ps4_raw.columns else None
        _flag_col = "is_anomaly" if "is_anomaly" in _ps4_raw.columns else (
            "ensemble_anomaly_flag" if "ensemble_anomaly_flag" in _ps4_raw.columns else None
        )
        _agg = [F.count(F.lit(1)).alias("ps4_anomaly_hours")]
        if _score_col:
            _agg.append(F.mean(F.col(_score_col).cast("double")).alias("ps4_anomaly_score_mean"))
        if _flag_col:
            _agg.append(F.max(F.col(_flag_col).cast("int")).alias("ps4_anomaly_flag"))
        _ps4_daily = _ps4_raw.groupBy("DEVICE_ID", "transit_day").agg(*_agg)
        df_joined = df_joined.join(_ps4_daily, on=["DEVICE_ID", "transit_day"], how="left")
        print(f"Joined PS4 scored aggregates from {_ps4_base}")
    except Exception as _ps4_exc:
        print(f"WARNING: PS4 scored join skipped ({_ps4_exc})")

'''
        t = t.replace(
            'print("Spark prior-window GATE features materialized (lazy)")',
            insert + 'print("Spark prior-window GATE features materialized (lazy)")',
        )
        set_cell_text(cell, t)
        break

for cell in nb["cells"]:
    t = cell_text(cell)
    if "ACTIVE_FEATURE_COLS = [" in t and "SPARSE_FEATURES" not in t:
        t = t.replace(
            'print(f"Active features     : {len(ACTIVE_FEATURE_COLS)}")\n',
            'null_report = (\n'
            '    df_ml[ACTIVE_FEATURE_COLS].isna().mean()\n'
            '    .sort_values(ascending=False)\n'
            '    .rename("null_rate")\n'
            '    .to_frame()\n'
            ')\n'
            'SPARSE_FEATURES = [\n'
            '    c for c in ACTIVE_FEATURE_COLS\n'
            '    if float(null_report.loc[c, "null_rate"]) > SPARSE_FEATURE_MAX_NULL_RATE\n'
            ']\n'
            'if SPARSE_FEATURES:\n'
            '    print(f"Sparse dropped (>{SPARSE_FEATURE_MAX_NULL_RATE:.0%} null): {SPARSE_FEATURES}")\n'
            '    ACTIVE_FEATURE_COLS = [c for c in ACTIVE_FEATURE_COLS if c not in SPARSE_FEATURES]\n'
            'print(f"Active features     : {len(ACTIVE_FEATURE_COLS)}")\n',
        )
        t = t.replace(
            'print(null_report.head(15).to_string())\n',
            'print(null_report.loc[ACTIVE_FEATURE_COLS].head(15).to_string())\n',
        )
        set_cell_text(cell, t)
        break

for cell in nb["cells"]:
    t = cell_text(cell)
    if "class BoosterEnsembleModel:" in t and "self.weights" not in t:
        t = t.replace(
            "    def __init__(self, members):\n        self.members = list(members)\n",
            "    def __init__(self, members, weights=None):\n"
            "        self.members = list(members)\n"
            "        self.weights = None if weights is None else np.asarray(weights, dtype=float)\n"
            "        if self.weights is not None:\n"
            "            self.weights = self.weights / self.weights.sum()\n",
        )
        t = t.replace(
            "        parts = np.column_stack([predict_scores(m, X_df) for m in self.members])\n"
            "        mean = parts.mean(axis=1)\n",
            "        parts = np.column_stack([predict_scores(m, X_df) for m in self.members])\n"
            "        if self.weights is not None:\n"
            "            mean = (parts * self.weights).sum(axis=1)\n"
            "        else:\n"
            "            mean = parts.mean(axis=1)\n",
        )
        t = t.replace(
            "def fit_booster_soft_vote(member_specs, X_train, y_train, X_val, y_val):\n"
            "    members = []\n"
            "    for algorithm, params in member_specs:\n"
            "        members.append(\n"
            "            fit_algorithm_model(algorithm, params, X_train, y_train, X_val, y_val)\n"
            "        )\n"
            "    return BoosterEnsembleModel(members)\n",
            "def fit_booster_soft_vote(member_specs, X_train, y_train, X_val, y_val, weights=None):\n"
            "    members = []\n"
            "    for algorithm, params in member_specs:\n"
            "        members.append(\n"
            "            fit_algorithm_model(algorithm, params, X_train, y_train, X_val, y_val)\n"
            "        )\n"
            "    return BoosterEnsembleModel(members, weights=weights)\n",
        )
        set_cell_text(cell, t)
        break

for cell in nb["cells"]:
    t = cell_text(cell)
    if "if algorithm == \"booster_soft_vote\":" in t and "weights" not in t.split("fit_booster_soft_vote")[1][:200]:
        t = t.replace(
            '        return fit_booster_soft_vote(params["members"], X_train, y_train, X_val, y_val)\n',
            '        return fit_booster_soft_vote(\n'
            '            params["members"], X_train, y_train, X_val, y_val,\n'
            '            weights=params.get("weights"),\n'
            '        )\n',
        )
        set_cell_text(cell, t)
        break

for cell in nb["cells"]:
    t = cell_text(cell)
    if "_top_specs = []" in t and "_diverse_top_specs" not in t:
        t = t.replace(
            "    _top_specs = []\n"
            "    for _, _r in _boosters_only.head(max(STACK_TOP_K, ENSEMBLE_TOP_K)).iterrows():\n"
            "        _spec = candidate_lookup[str(_r[\"candidate_id\"])]\n"
            "        _top_specs.append((_spec[\"algorithm\"], dict(_spec[\"params\"])))\n",
            "    def _diverse_top_specs(frame, k):\n"
            "        specs, weights, seen = [], [], set()\n"
            "        for _, _r in frame.iterrows():\n"
            "            _alg = str(_r[\"algorithm\"])\n"
            "            if ENSEMBLE_DIVERSIFY and _alg in seen:\n"
            "                continue\n"
            "            _spec = candidate_lookup[str(_r[\"candidate_id\"])]\n"
            "            specs.append((_spec[\"algorithm\"], dict(_spec[\"params\"])))\n"
            "            weights.append(float(_r[\"validation_accuracy\"]))\n"
            "            seen.add(_alg)\n"
            "            if len(specs) >= k:\n"
            "                break\n"
            "        if len(specs) < k:\n"
            "            for _, _r in frame.iterrows():\n"
            "                _spec = candidate_lookup[str(_r[\"candidate_id\"])]\n"
            "                _pair = (_spec[\"algorithm\"], json.dumps(_spec[\"params\"], sort_keys=True))\n"
            "                if any(json.dumps(p, sort_keys=True) == _pair[1] and a == _pair[0] for a, p in specs):\n"
            "                    continue\n"
            "                specs.append((_spec[\"algorithm\"], dict(_spec[\"params\"])))\n"
            "                weights.append(float(_r[\"validation_accuracy\"]))\n"
            "                if len(specs) >= k:\n"
            "                    break\n"
            "        return specs, weights\n"
            "\n"
            "    _top_specs, _top_weights = _diverse_top_specs(\n"
            "        _boosters_only, max(STACK_TOP_K, ENSEMBLE_TOP_K)\n"
            "    )\n",
        )
        t = t.replace(
            '            "params": {"members": _top_specs[:ENSEMBLE_TOP_K]},\n',
            '            "params": {\n'
            '                "members": _top_specs[:ENSEMBLE_TOP_K],\n'
            '                "weights": _top_weights[:ENSEMBLE_TOP_K],\n'
            '            },\n',
        )
        t = t.replace(
            '            "booster_soft_vote", 1, {"members": _top_specs[:ENSEMBLE_TOP_K]}\n',
            '            "booster_soft_vote", 1, {\n'
            '                "members": _top_specs[:ENSEMBLE_TOP_K],\n'
            '                "weights": _top_weights[:ENSEMBLE_TOP_K],\n'
            '            }\n',
        )
        set_cell_text(cell, t)
        break

for cell in nb["cells"]:
    t = cell_text(cell)
    if 'if str(SELECTION_OBJECTIVE).lower() == "accuracy":' in t and "accuracy_mcc" not in t:
        t = t.replace(
            'if str(SELECTION_OBJECTIVE).lower() == "accuracy":\n'
            '    model_tuning_results = model_tuning_results.sort_values(\n'
            '        ["validation_accuracy", "validation_mcc", "validation_avg_precision", "validation_roc_auc"],\n'
            '        ascending=[False, False, False, False],\n'
            '        kind="mergesort",\n'
            '    ).reset_index(drop=True)\n'
            '    _primary = "validation_accuracy"\n'
            '    _margin = SELECTION_ACCURACY_MARGIN\n',
            'if str(SELECTION_OBJECTIVE).lower() in ("accuracy", "accuracy_mcc"):\n'
            '    if str(SELECTION_OBJECTIVE).lower() == "accuracy_mcc":\n'
            '        model_tuning_results["validation_acc_mcc"] = (\n'
            '            model_tuning_results["validation_accuracy"]\n'
            '            + SELECTION_MCC_WEIGHT * model_tuning_results["validation_mcc"]\n'
            '        )\n'
            '        _primary = "validation_acc_mcc"\n'
            '    else:\n'
            '        _primary = "validation_accuracy"\n'
            '    model_tuning_results = model_tuning_results.sort_values(\n'
            '        [_primary, "validation_mcc", "validation_avg_precision", "validation_roc_auc"],\n'
            '        ascending=[False, False, False, False],\n'
            '        kind="mergesort",\n'
            '    ).reset_index(drop=True)\n'
            '    _margin = SELECTION_ACCURACY_MARGIN\n',
        )
        t = t.replace(
            'if str(SELECTION_OBJECTIVE).lower() == "accuracy":\n'
            '    selected_candidate = eligible.sort_values(\n',
            'if str(SELECTION_OBJECTIVE).lower() in ("accuracy", "accuracy_mcc"):\n'
            '    selected_candidate = eligible.sort_values(\n',
        )
        set_cell_text(cell, t)
        break

NB.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Patched {NB.name} for v4.2")
