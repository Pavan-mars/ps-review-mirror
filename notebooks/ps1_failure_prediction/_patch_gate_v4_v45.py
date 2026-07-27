"""Patch PS1 GATE OOS v4 — v4.5: 60/45 split + two-stage clear-negative filter."""
import json
from pathlib import Path

NB = Path(__file__).with_name("PS1_3d_GATE_OOS_Optimized_SageMaker_v4.ipynb")
nb = json.loads(NB.read_text(encoding="utf-8"))


def cell_text(cell):
    src = cell.get("source", [])
    return "".join(src) if isinstance(src, list) else src


def set_cell_text(cell, text):
    cell["source"] = text.splitlines(keepends=True)


MARKDOWN_V45 = """
## v4.5 — larger train split + clear-negative filter

| Change | Why |
|---|---|
| `VALIDATION_DAYS=60`, `TEST_DAYS=45` | Match VALIDATOR — ~105 extra calendar days in train |
| Two-stage clear-negative filter | Prior-only rule caps scores for stable devices → more TNs |
| Filter tuned on train-tail cal slice | Same prevalence band as threshold calibration (~74%) |

"""

for cell in nb["cells"]:
    t = cell_text(cell)
    if "## v4.4" in t and "v4.5" not in t:
        set_cell_text(cell, t.rstrip() + MARKDOWN_V45)
        break

for cell in nb["cells"]:
    t = cell_text(cell)
    if "VALIDATION_DAYS = 120" in t:
        t = t.replace(
            "# Locked chronological holdout. Hyperparameters and threshold use validation only;\n"
            "# the most recent TEST_DAYS are not touched until the winner is frozen.\n"
            "VALIDATION_DAYS = 120\n"
            "TEST_DAYS = 90\n",
            "# Locked chronological holdout. Hyperparameters and threshold use validation only;\n"
            "# the most recent TEST_DAYS are not touched until the winner is frozen.\n"
            "# v4.5 GATE split (aligned with VALIDATOR OOS): shorter val/test → larger train.\n"
            "VALIDATION_DAYS = 60\n"
            "TEST_DAYS = 45\n"
            "\n"
            "# Two-stage inference: cap scores for clear-negative device-days (prior-only features).\n"
            "RUN_CLEAR_NEGATIVE_FILTER = True\n"
            "CLEAR_NEGATIVE_MIN_FAIL_FREE_GRID = [7, 14, 21, 30, 45, 60]\n"
            "CLEAR_NEGATIVE_SCORE_CAP = 0.05  # force below typical accuracy-optimal threshold\n"
            "CLEAR_NEGATIVE_MIN_FAIL_FREE_SELECTED = None  # set during tuning before HPT\n",
        )
        set_cell_text(cell, t)
        break

FILTER_HELPERS = '''
def build_clear_negative_mask(feature_frame, min_fail_free):
    """Prior-only device-days with no recent hw-OOS / failure / PS4 prior signal."""
    mask = np.ones(len(feature_frame), dtype=bool)
    if "hardware_oos_count_prior_sum_7d" in feature_frame.columns:
        mask &= feature_frame["hardware_oos_count_prior_sum_7d"].fillna(0).to_numpy() <= 0
    if "roll_fail_30d" in feature_frame.columns:
        mask &= feature_frame["roll_fail_30d"].fillna(0).to_numpy() <= 0
    if "ps4_anomaly_hours_prior_7d" in feature_frame.columns:
        mask &= feature_frame["ps4_anomaly_hours_prior_7d"].fillna(0).to_numpy() <= 0
    streak_col = (
        "fail_free_streak"
        if "fail_free_streak" in feature_frame.columns
        else "days_since_fail" if "days_since_fail" in feature_frame.columns
        else None
    )
    if streak_col is not None:
        mask &= feature_frame[streak_col].fillna(0).to_numpy() >= float(min_fail_free)
    else:
        mask &= False
    return mask


def apply_clear_negative_filter(scores, feature_frame, min_fail_free=None, score_cap=None):
    if not globals().get("RUN_CLEAR_NEGATIVE_FILTER", False):
        return np.asarray(scores, dtype=float), 0
    min_ff = (
        globals().get("CLEAR_NEGATIVE_MIN_FAIL_FREE_SELECTED")
        if min_fail_free is None
        else min_fail_free
    )
    if min_ff is None:
        return np.asarray(scores, dtype=float), 0
    cap = float(CLEAR_NEGATIVE_SCORE_CAP if score_cap is None else score_cap)
    mask = build_clear_negative_mask(feature_frame, min_ff)
    out = np.asarray(scores, dtype=float).copy()
    if mask.any():
        out[mask] = np.minimum(out[mask], cap)
    return out, int(mask.sum())


def tune_clear_negative_rule(raw_scores, y_true, feature_frame):
    """Pick min fail-free streak on calibration slice (accuracy objective)."""
    best_acc, best_ff, best_n = -1.0, None, 0
    y_true = np.asarray(y_true, dtype=np.int8)
    for min_ff in CLEAR_NEGATIVE_MIN_FAIL_FREE_GRID:
        adj, n = apply_clear_negative_filter(raw_scores, feature_frame, min_fail_free=min_ff)
        threshold, _ = choose_threshold_robust(y_true, adj)
        pred = (adj >= threshold).astype(np.int8)
        acc = float(accuracy_score(y_true, pred))
        if acc > best_acc:
            best_acc, best_ff, best_n = acc, int(min_ff), n
    return best_ff, best_acc, best_n


'''

for cell in nb["cells"]:
    t = cell_text(cell)
    if "def choose_threshold_for_model(model" in t and "build_clear_negative_mask" not in t:
        t = t.replace(
            "def choose_threshold_for_model(model, train_frame, validation_frame, feature_cols):",
            FILTER_HELPERS + "def choose_threshold_for_model(model, train_frame, validation_frame, feature_cols):",
        )
        set_cell_text(cell, t)
        break

OLD_PREDICT = (
    "def predict_scores_any(model, X, columns=None):\n"
    "    columns = columns or ACTIVE_FEATURE_COLS\n"
    "    X_df = _as_feature_frame(X, columns)\n"
    "    if isinstance(model, (BoosterEnsembleModel, StackedBoosterModel)):\n"
    "        return model.predict_proba(X_df)[:, 1]\n"
    "    return predict_scores(model, X_df, columns)\n"
)
NEW_PREDICT = (
    "def predict_scores_raw(model, X, columns=None):\n"
    "    columns = columns or ACTIVE_FEATURE_COLS\n"
    "    X_df = _as_feature_frame(X, columns)\n"
    "    if isinstance(model, (BoosterEnsembleModel, StackedBoosterModel)):\n"
    "        return model.predict_proba(X_df)[:, 1]\n"
    "    return predict_scores(model, X_df, columns)\n"
    "\n"
    "\n"
    "def predict_scores_any(model, X, columns=None):\n"
    "    columns = columns or ACTIVE_FEATURE_COLS\n"
    "    X_df = _as_feature_frame(X, columns)\n"
    "    scores, _ = apply_clear_negative_filter(\n"
    "        predict_scores_raw(model, X_df, columns), X_df\n"
    "    )\n"
    "    return scores\n"
)

for cell in nb["cells"]:
    t = cell_text(cell)
    if OLD_PREDICT in t:
        t = t.replace(OLD_PREDICT, NEW_PREDICT)
        set_cell_text(cell, t)
        break

TUNE_BLOCK = '''
# Tune clear-negative filter on train-tail calibration slice (proxy model, no test leakage).
if RUN_CLEAR_NEGATIVE_FILTER:
    global _THRESHOLD_CAL_FRAME
    _THRESHOLD_CAL_FRAME = None  # refresh cal frame after split change
    _proxy_params = MODEL_FAMILY_CANDIDATES["hist_gradient_boosting"][0]
    _proxy = fit_algorithm_model(
        "hist_gradient_boosting", _proxy_params,
        X_benchmark_train, y_benchmark_train,
        X_validation, y_validation,
    )
    _cal_df = get_threshold_calibration_frame(benchmark_train_df, locked_validation_df)
    _cal_x = _cal_df[ACTIVE_FEATURE_COLS]
    _cal_y = _cal_df[TARGET_COL]
    _cal_raw = predict_scores_raw(_proxy, _cal_x)
    (
        CLEAR_NEGATIVE_MIN_FAIL_FREE_SELECTED,
        _cn_acc,
        _cn_n,
    ) = tune_clear_negative_rule(_cal_raw, _cal_y, _cal_x)
    del _proxy
    gc.collect()
    print(
        f"Clear-negative filter: min_fail_free_streak>={CLEAR_NEGATIVE_MIN_FAIL_FREE_SELECTED} "
        f"score_cap<={CLEAR_NEGATIVE_SCORE_CAP}  cal_accuracy={_cn_acc:.4f}  "
        f"cal_rows_capped={_cn_n:,}/{len(_cal_df):,}"
    )
else:
    CLEAR_NEGATIVE_MIN_FAIL_FREE_SELECTED = None

'''

for cell in nb["cells"]:
    t = cell_text(cell)
    if "y_test = locked_test_df[TARGET_COL]" in t and "tune_clear_negative_rule" not in t:
        t = t.replace(
            "y_test = locked_test_df[TARGET_COL]\n\n"
            "TUNING_METRICS = [",
            "y_test = locked_test_df[TARGET_COL]\n"
            + TUNE_BLOCK
            + "\nTUNING_METRICS = [",
        )
        set_cell_text(cell, t)
        break

for cell in nb["cells"]:
    t = cell_text(cell)
    if '"target_test_accuracy": TARGET_TEST_ACCURACY,' in t and "clear_negative" not in t:
        t = t.replace(
            '    "target_test_accuracy": TARGET_TEST_ACCURACY,\n',
            '    "target_test_accuracy": TARGET_TEST_ACCURACY,\n'
            '    "run_clear_negative_filter": RUN_CLEAR_NEGATIVE_FILTER,\n'
            '    "clear_negative_min_fail_free": CLEAR_NEGATIVE_MIN_FAIL_FREE_SELECTED,\n'
            '    "clear_negative_score_cap": CLEAR_NEGATIVE_SCORE_CAP,\n'
            '    "validation_days": VALIDATION_DAYS,\n'
            '    "test_days": TEST_DAYS,\n',
        )
        set_cell_text(cell, t)
        break

for cell in nb["cells"]:
    t = cell_text(cell)
    if "=== ACCURACY TARGET CHECK (locked test) ===" in t and "clear-negative" not in t:
        t = t.replace(
            'print("\\n=== ACCURACY TARGET CHECK (locked test) ===")\n',
            '_cn_test_n = 0\n'
            'if RUN_CLEAR_NEGATIVE_FILTER and CLEAR_NEGATIVE_MIN_FAIL_FREE_SELECTED is not None:\n'
            '    _, _cn_test_n = apply_clear_negative_filter(\n'
            '        predict_scores_raw(SELECTED_EVALUATION_MODEL, X_test),\n'
            '        X_test,\n'
            '    )\n'
            '    print(\n'
            '        f"Clear-negative on test: {_cn_test_n:,}/{len(X_test):,} rows capped "\n'
            '        f"(min_fail_free>={CLEAR_NEGATIVE_MIN_FAIL_FREE_SELECTED})"\n'
            '    )\n'
            'print("\\n=== ACCURACY TARGET CHECK (locked test) ===")\n',
        )
        set_cell_text(cell, t)
        break

NB.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Patched {NB.name} for v4.5")
