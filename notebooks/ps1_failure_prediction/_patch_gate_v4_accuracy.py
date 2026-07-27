"""Patch PS1_3d_GATE_OOS_Optimized_SageMaker_v4.ipynb for v4.1 accuracy-first tuning."""
import json
from pathlib import Path

NB = Path(__file__).with_name("PS1_3d_GATE_OOS_Optimized_SageMaker_v4.ipynb")
nb = json.loads(NB.read_text(encoding="utf-8"))


def cell_text(cell):
    src = cell.get("source", [])
    return "".join(src) if isinstance(src, list) else src


def set_cell_text(cell, text):
    cell["source"] = text.splitlines(keepends=True)


MARKDOWN_V41 = """## v4.1 accuracy push (>= 90% locked-test target)

| Change | Why |
|---|---|
| `SELECTION_OBJECTIVE=accuracy` | Pick model + threshold for validation accuracy, not AP |
| `choose_threshold_accuracy` | Threshold grid maximizes accuracy with min-recall guard |
| Expanded booster grid | Deeper trees, lower LR, extra SPW values |
| `booster_soft_vote` ensemble | Mean score of top-3 boosters by validation accuracy |
| `stacked_top5` meta-learner | Logistic stack on out-of-time validation scores |
| `BENCHMARK_MAX_TRAIN_ROWS=None` | Full train split on large instances |

> **Note:** 90% test accuracy is aspirational — validation ceiling in v4 was ~87%. Re-run tuning; if test accuracy stays < 90%, feature/label work is the next lever (not more HPT alone).

"""

CONFIG_OLD = """THRESHOLD_BETA = 1.0  # 1.0=F1; use 2.0 when recall is worth more than precision.
MIN_THRESHOLD_PRECISION = 0.0"""

CONFIG_NEW = """THRESHOLD_BETA = 1.0  # used when THRESHOLD_OBJECTIVE="fbeta"
MIN_THRESHOLD_PRECISION = 0.0

# v4.1 — accuracy-first selection / thresholding
SELECTION_OBJECTIVE = "accuracy"  # "accuracy" | "avg_precision"
SELECTION_ACCURACY_MARGIN = 0.002
SELECTION_AP_MARGIN = 0.003  # tie-break when objective is AP
THRESHOLD_OBJECTIVE = "accuracy"  # "accuracy" | "fbeta"
THRESHOLD_MIN_RECALL = 0.88  # guard against all-negative when optimizing accuracy
TARGET_TEST_ACCURACY = 0.90
RUN_BOOSTER_ENSEMBLE = True
ENSEMBLE_TOP_K = 3
RUN_STACKED_ENSEMBLE = True
STACK_TOP_K = 5"""

THRESHOLD_FN_OLD = """def choose_threshold_robust(y_true, scores, beta=None, min_precision=None, max_threshold=None):
    beta = THRESHOLD_BETA if beta is None else beta
    min_precision = MIN_THRESHOLD_PRECISION if min_precision is None else min_precision
    max_threshold = THRESHOLD_MAX if max_threshold is None else max_threshold
    threshold, meta = choose_threshold(y_true, scores, beta=beta, min_precision=min_precision)"""

THRESHOLD_FN_NEW = """def choose_threshold_accuracy(y_true, scores, min_recall=None, max_threshold=None):
    \"\"\"Pick threshold maximizing accuracy with optional recall floor.\"\"\"
    y_true = np.asarray(y_true, dtype=np.int8)
    scores = np.asarray(scores, dtype=float)
    min_recall = THRESHOLD_MIN_RECALL if min_recall is None else min_recall
    max_threshold = THRESHOLD_MAX if max_threshold is None else max_threshold
    uniq = np.unique(scores)
    if uniq.size > 5000:
        uniq = np.quantile(scores, np.linspace(0.01, 0.99, 400))
    candidates = np.sort(np.unique(np.concatenate([uniq, [0.5]])))
    best_t, best_acc, best_meta = 0.5, -1.0, {}
    for t in candidates:
        if t > max_threshold:
            continue
        pred = (scores >= t).astype(np.int8)
        acc = float(accuracy_score(y_true, pred))
        rec = float(recall_score(y_true, pred, zero_division=0))
        if rec < min_recall:
            continue
        if acc > best_acc:
            best_acc, best_t = acc, float(t)
            best_meta = {"accuracy": acc, "recall": rec, "objective": "accuracy"}
    if best_acc < 0:
        return choose_threshold(y_true, scores, beta=THRESHOLD_BETA, min_precision=MIN_THRESHOLD_PRECISION)
    return best_t, best_meta


def choose_threshold_robust(y_true, scores, beta=None, min_precision=None, max_threshold=None):
    if str(THRESHOLD_OBJECTIVE).lower() == "accuracy":
        return choose_threshold_accuracy(y_true, scores, max_threshold=max_threshold)
    beta = THRESHOLD_BETA if beta is None else beta
    min_precision = MIN_THRESHOLD_PRECISION if min_precision is None else min_precision
    max_threshold = THRESHOLD_MAX if max_threshold is None else max_threshold
    threshold, meta = choose_threshold(y_true, scores, beta=beta, min_precision=min_precision)"""

ENSEMBLE_HELPERS = """

class BoosterEnsembleModel:
    \"\"\"Soft-vote average of fitted booster pipelines.\"\"\"

    def __init__(self, members):
        self.members = list(members)

    def predict_proba(self, X):
        X_df = _as_feature_frame(X)
        parts = np.column_stack([predict_scores(m, X_df) for m in self.members])
        mean = parts.mean(axis=1)
        return np.column_stack([1.0 - mean, mean])


class StackedBoosterModel:
    \"\"\"Logistic meta-learner on base booster validation scores.\"\"\"

    def __init__(self, base_models, meta_model):
        self.base_models = list(base_models)
        self.meta_model = meta_model

    def predict_proba(self, X):
        X_df = _as_feature_frame(X)
        Z = np.column_stack([predict_scores(m, X_df) for m in self.base_models])
        return self.meta_model.predict_proba(Z)


def fit_booster_soft_vote(member_specs, X_train, y_train, X_val, y_val):
    members = []
    for algorithm, params in member_specs:
        members.append(
            fit_algorithm_model(algorithm, params, X_train, y_train, X_val, y_val)
        )
    return BoosterEnsembleModel(members)


def fit_stacked_boosters(member_specs, X_train, y_train, X_val, y_val):
    base_models = []
    val_cols = []
    for algorithm, params in member_specs:
        model = fit_algorithm_model(algorithm, params, X_train, y_train, X_val, y_val)
        base_models.append(model)
        val_cols.append(predict_scores(model, X_val))
    Z_val = np.column_stack(val_cols)
    meta = LogisticRegression(C=1.0, max_iter=500, random_state=RANDOM_SEED)
    meta.fit(Z_val, np.asarray(y_val))
    return StackedBoosterModel(base_models, meta)


def fit_algorithm_or_ensemble(algorithm, params, X_train, y_train, X_val=None, y_val=None):
    if algorithm == "booster_soft_vote":
        return fit_booster_soft_vote(params["members"], X_train, y_train, X_val, y_val)
    if algorithm == "stacked_top5":
        return fit_stacked_boosters(params["members"], X_train, y_train, X_val, y_val)
    return fit_algorithm_model(algorithm, params, X_train, y_train, X_val, y_val)


def predict_scores_any(model, X, columns=None):
    columns = columns or ACTIVE_FEATURE_COLS
    X_df = _as_feature_frame(X, columns)
    if isinstance(model, (BoosterEnsembleModel, StackedBoosterModel)):
        return model.predict_proba(X_df)[:, 1]
    return predict_scores(model, X_df, columns)
"""

TUNING_SELECT_OLD = """model_tuning_results = pd.DataFrame(tuning_rows).sort_values(
    ["validation_avg_precision", "validation_roc_auc"],
    ascending=False,
    kind="mergesort",
).reset_index(drop=True)

best_candidate_rows = (
    model_tuning_results
    .sort_values(
        ["validation_avg_precision", "validation_roc_auc"],
        ascending=False,
        kind="mergesort",
    )
    .groupby("algorithm", sort=False, as_index=False)
    .head(1)
    .reset_index(drop=True)
)
model_tuning_results["train_val_ap_gap"] = (
    model_tuning_results["train_avg_precision"] - model_tuning_results["validation_avg_precision"]
)
best_val_ap = float(model_tuning_results["validation_avg_precision"].max())
eligible = model_tuning_results[
    model_tuning_results["validation_avg_precision"] >= best_val_ap - SELECTION_AP_MARGIN
].copy()
selected_candidate = eligible.sort_values(
    ["validation_avg_precision", "train_val_ap_gap", "validation_roc_auc"],
    ascending=[False, True, False],
    kind="mergesort",
).iloc[0]"""

TUNING_SELECT_NEW = """model_tuning_results = pd.DataFrame(tuning_rows).sort_values(
    ["validation_accuracy", "validation_avg_precision"],
    ascending=[False, False],
    kind="mergesort",
).reset_index(drop=True)

# v4.1 — ensemble candidates from top boosters by validation accuracy
if RUN_BOOSTER_ENSEMBLE or RUN_STACKED_ENSEMBLE:
    _boosters_only = model_tuning_results[
        model_tuning_results["algorithm"].isin(list(BOOSTER_ALGOS))
    ].sort_values(
        ["validation_accuracy", "validation_avg_precision", "validation_mcc"],
        ascending=[False, False, False],
        kind="mergesort",
    )
    _top_specs = []
    for _, _r in _boosters_only.head(max(STACK_TOP_K, ENSEMBLE_TOP_K)).iterrows():
        _spec = candidate_lookup[str(_r["candidate_id"])]
        _top_specs.append((_spec["algorithm"], dict(_spec["params"])))
    if RUN_BOOSTER_ENSEMBLE and len(_top_specs) >= 2:
        candidate_lookup["booster_soft_vote_01"] = {
            "algorithm": "booster_soft_vote",
            "params": {"members": _top_specs[:ENSEMBLE_TOP_K]},
        }
        _row, _err = _evaluate_one_candidate(
            "booster_soft_vote", 1, {"members": _top_specs[:ENSEMBLE_TOP_K]}
        )
        if _row:
            tuning_rows.append(_row)
        elif _err:
            tuning_failures.append(_err)
    if RUN_STACKED_ENSEMBLE and len(_top_specs) >= 2:
        candidate_lookup["stacked_top5_01"] = {
            "algorithm": "stacked_top5",
            "params": {"members": _top_specs[:STACK_TOP_K]},
        }
        _row, _err = _evaluate_one_candidate(
            "stacked_top5", 1, {"members": _top_specs[:STACK_TOP_K]}
        )
        if _row:
            tuning_rows.append(_row)
        elif _err:
            tuning_failures.append(_err)
    model_tuning_results = pd.DataFrame(tuning_rows)

if str(SELECTION_OBJECTIVE).lower() == "accuracy":
    model_tuning_results = model_tuning_results.sort_values(
        ["validation_accuracy", "validation_mcc", "validation_avg_precision", "validation_roc_auc"],
        ascending=[False, False, False, False],
        kind="mergesort",
    ).reset_index(drop=True)
    _primary = "validation_accuracy"
    _margin = SELECTION_ACCURACY_MARGIN
else:
    model_tuning_results = model_tuning_results.sort_values(
        ["validation_avg_precision", "validation_roc_auc"],
        ascending=False,
        kind="mergesort",
    ).reset_index(drop=True)
    _primary = "validation_avg_precision"
    _margin = SELECTION_AP_MARGIN

best_candidate_rows = (
    model_tuning_results
    .sort_values(
        [_primary, "validation_mcc", "validation_avg_precision"],
        ascending=[False, False, False],
        kind="mergesort",
    )
    .groupby("algorithm", sort=False, as_index=False)
    .head(1)
    .reset_index(drop=True)
)
model_tuning_results["train_val_ap_gap"] = (
    model_tuning_results["train_avg_precision"] - model_tuning_results["validation_avg_precision"]
)
best_primary = float(model_tuning_results[_primary].max())
eligible = model_tuning_results[
    model_tuning_results[_primary] >= best_primary - _margin
].copy()
if str(SELECTION_OBJECTIVE).lower() == "accuracy":
    selected_candidate = eligible.sort_values(
        ["validation_accuracy", "validation_mcc", "validation_avg_precision", "train_val_ap_gap"],
        ascending=[False, False, False, True],
        kind="mergesort",
    ).iloc[0]
else:
    selected_candidate = eligible.sort_values(
        ["validation_avg_precision", "train_val_ap_gap", "validation_roc_auc"],
        ascending=[False, True, False],
        kind="mergesort",
    ).iloc[0]"""

replacements = [
    (CONFIG_OLD, CONFIG_NEW),
    ("BENCHMARK_MAX_TRAIN_ROWS = 750_000", "BENCHMARK_MAX_TRAIN_ROWS = None  # v4.1: full train; set 750_000 to cap"),
    ("BOOSTER_N_ESTIMATORS = 800", "BOOSTER_N_ESTIMATORS = 1200"),
    (
        'SPW_CANDIDATES = [3, 5, 8, 10, 15, 20]  # OOS label — higher prevalence than chargeable SLA\nSELECTION_AP_MARGIN = 0.003',
        'SPW_CANDIDATES = [1, 2, 3, 5, 8, 10, 15]  # v4.1: extended SPW grid for high-prevalence OOS',
    ),
    (
        '''    "xgboost": _expand_spw([
        {"learning_rate": 0.05, "max_depth": 5, "min_child_weight": 30, "subsample": 0.7, "colsample_bytree": 0.7},
        {"learning_rate": 0.05, "max_depth": 6, "min_child_weight": 50, "subsample": 0.75, "colsample_bytree": 0.75},
    ]),''',
        '''    "xgboost": _expand_spw([
        {"learning_rate": 0.05, "max_depth": 5, "min_child_weight": 30, "subsample": 0.7, "colsample_bytree": 0.7},
        {"learning_rate": 0.05, "max_depth": 6, "min_child_weight": 50, "subsample": 0.75, "colsample_bytree": 0.75},
        {"learning_rate": 0.03, "max_depth": 7, "min_child_weight": 40, "subsample": 0.8, "colsample_bytree": 0.8},
    ]),''',
    ),
    (
        '''        {"learning_rate": 0.08, "depth": 5, "l2_leaf_reg": 8.0, "min_data_in_leaf": 250},
    ]),
}''',
        '''        {"learning_rate": 0.08, "depth": 5, "l2_leaf_reg": 8.0, "min_data_in_leaf": 250},
        {"learning_rate": 0.03, "depth": 7, "l2_leaf_reg": 6.0, "min_data_in_leaf": 150},
    ]),
}''',
    ),
    (THRESHOLD_FN_OLD, THRESHOLD_FN_NEW),
    ('    raise ValueError(f"Unknown algorithm: {algorithm}")', '    raise ValueError(f"Unknown algorithm: {algorithm}")' + ENSEMBLE_HELPERS),
    (TUNING_SELECT_OLD, TUNING_SELECT_NEW),
    (
        '''print(
    f"Selection: {SELECTED_CANDIDATE_ID}  "
    f"val_AP={selected_candidate['validation_avg_precision']:.4f}  "
    f"val_accuracy={selected_candidate['validation_accuracy']:.4f}  "
    f"(majority={selected_candidate['validation_majority_accuracy']:.4f}, "
    f"gain={selected_candidate['validation_accuracy_gain_vs_majority']:+.4f})  "
    f"gap={selected_candidate['train_val_ap_gap']:.4f}  (margin={SELECTION_AP_MARGIN})"
)''',
        '''print(
    f"Selection: {SELECTED_CANDIDATE_ID}  objective={SELECTION_OBJECTIVE}  "
    f"val_accuracy={selected_candidate['validation_accuracy']:.4f}  "
    f"val_AP={selected_candidate['validation_avg_precision']:.4f}  "
    f"val_MCC={selected_candidate['validation_mcc']:.4f}  "
    f"(majority={selected_candidate['validation_majority_accuracy']:.4f}, "
    f"gain={selected_candidate['validation_accuracy_gain_vs_majority']:+.4f})  "
    f"target_test_acc>={TARGET_TEST_ACCURACY:.0%}"
)''',
    ),
    (
        """        model = fit_algorithm_model(
            algorithm, params,
            X_benchmark_train, y_benchmark_train,
            X_validation, y_validation,
        )
        train_scores = predict_scores(model, X_benchmark_train)
        validation_scores = predict_scores(model, X_validation)""",
        """        model = fit_algorithm_or_ensemble(
            algorithm, params,
            X_benchmark_train, y_benchmark_train,
            X_validation, y_validation,
        )
        train_scores = predict_scores_any(model, X_benchmark_train)
        validation_scores = predict_scores_any(model, X_validation)""",
    ),
    (
        """        model = fit_algorithm_model(
            algorithm, params,
            X_benchmark_train, y_benchmark_train,
            X_validation, y_validation,
        )
        validation_scores = predict_scores(model, X_validation)""",
        """        model = fit_algorithm_or_ensemble(
            algorithm, params,
            X_benchmark_train, y_benchmark_train,
            X_validation, y_validation,
        )
        validation_scores = predict_scores_any(model, X_validation)""",
    ),
    ("            scores = predict_scores(model, split_x)", "            scores = predict_scores_any(model, split_x)"),
    (
        """    final_pipeline = fit_algorithm_model(
        SELECTED_ALGORITHM, FINAL_PARAMS,
        X_all, y_all,
        X_validation, y_validation,
    )

final_classifier = final_pipeline.named_steps["model"]""",
        """    final_pipeline = fit_algorithm_or_ensemble(
        SELECTED_ALGORITHM, FINAL_PARAMS,
        X_all, y_all,
        X_validation, y_validation,
    )

if isinstance(final_pipeline, Pipeline):
    final_classifier = final_pipeline.named_steps["model"]
else:
    final_classifier = final_pipeline""",
    ),
    (
        """            model = fit_algorithm_model(
                SELECTED_ALGORITHM, FINAL_PARAMS,
                subset[ACTIVE_FEATURE_COLS], subset[TARGET_COL],
                X_validation, y_validation,
            )
            subset_scores = predict_scores(model, subset[ACTIVE_FEATURE_COLS])
            validation_scores = predict_scores(model, X_validation)""",
        """            model = fit_algorithm_or_ensemble(
                SELECTED_ALGORITHM, FINAL_PARAMS,
                subset[ACTIVE_FEATURE_COLS], subset[TARGET_COL],
                X_validation, y_validation,
            )
            subset_scores = predict_scores_any(model, subset[ACTIVE_FEATURE_COLS])
            validation_scores = predict_scores_any(model, X_validation)""",
    ),
    (
        'risk_score = predict_scores(final_pipeline, frame[ACTIVE_FEATURE_COLS])',
        'risk_score = predict_scores_any(final_pipeline, frame[ACTIVE_FEATURE_COLS])',
    ),
    (
        '    f"\\nLOCKED BEFORE TEST: {SELECTED_CANDIDATE_ID} selected by validation AP; "',
        '    f"\\nLOCKED BEFORE TEST: {SELECTED_CANDIDATE_ID} selected by validation {SELECTION_OBJECTIVE}; "',
    ),
]

for cell in nb["cells"]:
    text = cell_text(cell)
    for old, new in replacements:
        if old in text:
            text = text.replace(old, new, 1)
    set_cell_text(cell, text)

# markdown v4.1
md = cell_text(nb["cells"][1])
if "v4.1 accuracy push" not in md:
    md = md.replace(
        "| Performance | Sequential tuning only |",
        MARKDOWN_V41 + "| Performance | Sequential tuning only |",
        1,
    )
    set_cell_text(nb["cells"][1], md)

# accuracy target report
for cell in nb["cells"]:
    text = cell_text(cell)
    if 'print("\\n=== SELECTED MODEL — locked split metrics ===")' in text and "ACCURACY TARGET CHECK" not in text:
        needle = "    )\n\n\n\nlearning_curve_rows = []"
        insert = '''    )

_test_acc = float(SELECTED_TEST_METRICS.get("accuracy", float("nan")))
print("\\n=== ACCURACY TARGET CHECK (locked test) ===")
print(f"  test_accuracy={_test_acc:.4f}  target>={TARGET_TEST_ACCURACY:.2f}  met={_test_acc >= TARGET_TEST_ACCURACY}")
if _test_acc < TARGET_TEST_ACCURACY:
    print(
        "  WARNING: locked-test accuracy is below target. "
        "Next levers: richer PS4/PS5 features, label review, or operational threshold by recall floor."
    )


learning_curve_rows = []'''
        if needle in text:
            text = text.replace(needle, insert, 1)
            set_cell_text(cell, text)

# skip permutation importance for ensemble wrappers
for cell in nb["cells"]:
    text = cell_text(cell)
    if "with timed_stage(\"locked_test_permutation_importance\"):" in text and "BoosterEnsembleModel" not in text:
        text = text.replace(
            "else:\n    importance_frame = deterministic_time_sample(",
            "elif isinstance(final_classifier, (BoosterEnsembleModel, StackedBoosterModel)):\n"
            "    feature_importance = pd.DataFrame({\n"
            '        "feature": ACTIVE_FEATURE_COLS,\n'
            '        "importance": np.nan,\n'
            '        "importance_std": np.nan,\n'
            '        "abs_importance": np.nan,\n'
            '        "method": "ensemble_no_single_importance",\n'
            "    })\n"
            "else:\n    importance_frame = deterministic_time_sample(",
            1,
        )
        set_cell_text(cell, text)

# metadata fields
for cell in nb["cells"]:
    text = cell_text(cell)
    if '"threshold_beta": THRESHOLD_BETA,' in text and '"selection_objective"' not in text:
        text = text.replace(
            '"threshold_beta": THRESHOLD_BETA,',
            '"selection_objective": SELECTION_OBJECTIVE,\n'
            '    "threshold_objective": THRESHOLD_OBJECTIVE,\n'
            '    "target_test_accuracy": TARGET_TEST_ACCURACY,\n'
            '    "threshold_beta": THRESHOLD_BETA,',
            1,
        )
        set_cell_text(cell, text)

NB.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
print("Patched", NB)
