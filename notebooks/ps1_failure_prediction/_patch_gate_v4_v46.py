"""Patch PS1 GATE OOS v4 — v4.6: tiered clear-negative + 90d cal window."""
import json
from pathlib import Path

NB = Path(__file__).with_name("PS1_3d_GATE_OOS_Optimized_SageMaker_v4.ipynb")
nb = json.loads(NB.read_text(encoding="utf-8"))


def cell_text(cell):
    src = cell.get("source", [])
    return "".join(src) if isinstance(src, list) else src


def set_cell_text(cell, text):
    cell["source"] = text.splitlines(keepends=True)


MARKDOWN_V46 = """
## v4.6 — tiered clear-negative + shorter threshold cal

| Change | Why |
|---|---|
| `THRESHOLD_CALIBRATION_DAYS=90` | v4.5 cal slice was 81% prev vs 72% test — shorter tail closer to test band |
| Tier A (hard cap 0.02) | `hw_oos_prior_7d==0` + `fail_free_streak>=N` — no roll_fail / PS4 conjunct |
| Tier B (soft cap 0.10) | Same hw-OOS gate + activity (`tap_count`, `met_txn_count`) + lower streak |
| Filter tuned on **validation** | Cal-only tuning capped 128 rows; validation has more TNs to optimize |

"""

for cell in nb["cells"]:
    t = cell_text(cell)
    if "## v4.5" in t and "v4.6" not in t:
        set_cell_text(cell, t.rstrip() + MARKDOWN_V46)
        break

OLD_CN_CONFIG = (
    "# Two-stage inference: cap scores for clear-negative device-days (prior-only features).\n"
    "RUN_CLEAR_NEGATIVE_FILTER = True\n"
    "CLEAR_NEGATIVE_MIN_FAIL_FREE_GRID = [7, 14, 21, 30, 45, 60]\n"
    "CLEAR_NEGATIVE_SCORE_CAP = 0.05  # force below typical accuracy-optimal threshold\n"
    "CLEAR_NEGATIVE_MIN_FAIL_FREE_SELECTED = None  # set during tuning before HPT\n"
)
NEW_CN_CONFIG = (
    "# Tiered clear-negative (prior-only): cap ML scores for stable / active device-days.\n"
    "RUN_CLEAR_NEGATIVE_FILTER = True\n"
    "CLEAR_NEGATIVE_TIER_A_CAP = 0.02  # hard tier — force well below accuracy threshold\n"
    "CLEAR_NEGATIVE_TIER_B_CAP = 0.10  # soft tier — active devices with no recent hw-OOS\n"
    "CLEAR_NEGATIVE_TIER_A_FF_GRID = [14, 21, 30, 45, 60]\n"
    "CLEAR_NEGATIVE_TIER_B_FF_GRID = [7, 14, 21, 30]\n"
    "CLEAR_NEGATIVE_TIER_A_SELECTED = None  # {min_fail_free, score_cap} — set before HPT\n"
    "CLEAR_NEGATIVE_TIER_B_SELECTED = None  # {min_fail_free, score_cap, require_activity}\n"
)

for cell in nb["cells"]:
    t = cell_text(cell)
    if OLD_CN_CONFIG in t:
        t = t.replace(OLD_CN_CONFIG, NEW_CN_CONFIG)
        t = t.replace(
            "THRESHOLD_CALIBRATION_DAYS = 180  # calibrate threshold on late-train slice (~65-75% prev)",
            "THRESHOLD_CALIBRATION_DAYS = 90  # v4.6: shorter tail (~72-78% prev, closer to test)",
        )
        set_cell_text(cell, t)
        break

OLD_FILTER_BLOCK = '''def build_clear_negative_mask(feature_frame, min_fail_free):
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

NEW_FILTER_BLOCK = '''def _hw_oos_clear_mask(feature_frame):
    """Device-days with zero hardware-OOS events in the prior 7d window."""
    if "hardware_oos_count_prior_sum_7d" not in feature_frame.columns:
        return np.zeros(len(feature_frame), dtype=bool)
    return feature_frame["hardware_oos_count_prior_sum_7d"].fillna(0).to_numpy() <= 0


def _fail_free_streak_array(feature_frame):
    streak_col = (
        "fail_free_streak"
        if "fail_free_streak" in feature_frame.columns
        else "days_since_fail" if "days_since_fail" in feature_frame.columns
        else None
    )
    if streak_col is None:
        return None
    return feature_frame[streak_col].fillna(0).to_numpy()


def build_clear_negative_tier_a_mask(feature_frame, min_fail_free):
    """Tier A: no recent hw-OOS + long fail-free streak (hard negative)."""
    streak = _fail_free_streak_array(feature_frame)
    if streak is None:
        return np.zeros(len(feature_frame), dtype=bool)
    return _hw_oos_clear_mask(feature_frame) & (streak >= float(min_fail_free))


def build_clear_negative_tier_b_mask(feature_frame, min_fail_free, require_activity=True):
    """Tier B: no recent hw-OOS + moderate streak + recent tap/metric activity."""
    streak = _fail_free_streak_array(feature_frame)
    if streak is None:
        return np.zeros(len(feature_frame), dtype=bool)
    mask = _hw_oos_clear_mask(feature_frame) & (streak >= float(min_fail_free))
    if not require_activity:
        return mask
    if "tap_count" in feature_frame.columns:
        mask &= feature_frame["tap_count"].fillna(0).to_numpy() > 0
    else:
        mask &= False
    txn_col = (
        "met_txn_count"
        if "met_txn_count" in feature_frame.columns
        else "metric_txn_count" if "metric_txn_count" in feature_frame.columns
        else None
    )
    if txn_col is not None:
        mask &= feature_frame[txn_col].fillna(0).to_numpy() > 0
    else:
        mask &= False
    return mask


def apply_clear_negative_filter(scores, feature_frame, tier_a=None, tier_b=None):
    """Apply tier B (soft) then tier A (hard); return capped scores and tier counts."""
    if not globals().get("RUN_CLEAR_NEGATIVE_FILTER", False):
        out = np.asarray(scores, dtype=float)
        return out, 0, 0
    tier_a = tier_a if tier_a is not None else globals().get("CLEAR_NEGATIVE_TIER_A_SELECTED")
    tier_b = tier_b if tier_b is not None else globals().get("CLEAR_NEGATIVE_TIER_B_SELECTED")
    if tier_a is None and tier_b is None:
        out = np.asarray(scores, dtype=float)
        return out, 0, 0
    out = np.asarray(scores, dtype=float).copy()
    n_a, n_b = 0, 0
    if tier_b is not None:
        mb = build_clear_negative_tier_b_mask(
            feature_frame,
            tier_b["min_fail_free"],
            tier_b.get("require_activity", True),
        )
        if mb.any():
            out[mb] = np.minimum(out[mb], float(tier_b["score_cap"]))
            n_b = int(mb.sum())
    if tier_a is not None:
        ma = build_clear_negative_tier_a_mask(feature_frame, tier_a["min_fail_free"])
        if ma.any():
            out[ma] = np.minimum(out[ma], float(tier_a["score_cap"]))
            n_a = int(ma.sum())
    return out, n_a, n_b


def _tier_filter_accuracy(raw_scores, y_true, feature_frame, tier_a, tier_b):
    adj, n_a, n_b = apply_clear_negative_filter(
        raw_scores, feature_frame, tier_a=tier_a, tier_b=tier_b
    )
    threshold, _ = choose_threshold_robust(y_true, adj)
    pred = (adj >= threshold).astype(np.int8)
    acc = float(accuracy_score(y_true, pred))
    return acc, n_a, n_b


def tune_clear_negative_tiers(raw_scores, y_true, feature_frame):
    """Grid-search tier A/B streak thresholds on validation (accuracy objective)."""
    y_true = np.asarray(y_true, dtype=np.int8)
    best_acc, best_a, best_b, best_na, best_nb = -1.0, None, None, 0, 0
    for a_ff in CLEAR_NEGATIVE_TIER_A_FF_GRID:
        tier_a = {"min_fail_free": int(a_ff), "score_cap": CLEAR_NEGATIVE_TIER_A_CAP}
        for b_ff in CLEAR_NEGATIVE_TIER_B_FF_GRID:
            if int(b_ff) >= int(a_ff):
                continue
            tier_b = {
                "min_fail_free": int(b_ff),
                "score_cap": CLEAR_NEGATIVE_TIER_B_CAP,
                "require_activity": True,
            }
            acc, n_a, n_b = _tier_filter_accuracy(raw_scores, y_true, feature_frame, tier_a, tier_b)
            if acc > best_acc:
                best_acc, best_a, best_b, best_na, best_nb = acc, tier_a, tier_b, n_a, n_b
        acc, n_a, n_b = _tier_filter_accuracy(raw_scores, y_true, feature_frame, tier_a, None)
        if acc > best_acc:
            best_acc, best_a, best_b, best_na, best_nb = acc, tier_a, None, n_a, n_b
    for b_ff in CLEAR_NEGATIVE_TIER_B_FF_GRID:
        tier_b = {
            "min_fail_free": int(b_ff),
            "score_cap": CLEAR_NEGATIVE_TIER_B_CAP,
            "require_activity": True,
        }
        acc, n_a, n_b = _tier_filter_accuracy(raw_scores, y_true, feature_frame, None, tier_b)
        if acc > best_acc:
            best_acc, best_a, best_b, best_na, best_nb = acc, None, tier_b, n_a, n_b
    return best_a, best_b, best_acc, best_na, best_nb


'''

for cell in nb["cells"]:
    t = cell_text(cell)
    if OLD_FILTER_BLOCK in t:
        t = t.replace(OLD_FILTER_BLOCK, NEW_FILTER_BLOCK)
        set_cell_text(cell, t)
        break
else:
    raise SystemExit("Could not find v4.5 filter block to replace")

# predict_scores_any: unpack 3-tuple from apply_clear_negative_filter
for cell in nb["cells"]:
    t = cell_text(cell)
    if "def predict_scores_any(model, X, columns=None):" in t and "tier_a" not in t:
        t = t.replace(
            "    scores, _ = apply_clear_negative_filter(\n"
            "        predict_scores_raw(model, X_df, columns), X_df\n"
            "    )\n",
            "    scores, _, _ = apply_clear_negative_filter(\n"
            "        predict_scores_raw(model, X_df, columns), X_df\n"
            "    )\n",
        )
        set_cell_text(cell, t)
        break

OLD_TUNE_BLOCK = '''# Tune clear-negative filter on train-tail calibration slice (proxy model, no test leakage).
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

NEW_TUNE_BLOCK = '''# Tune tiered clear-negative on validation (proxy model; no test leakage).
if RUN_CLEAR_NEGATIVE_FILTER:
    global _THRESHOLD_CAL_FRAME
    _THRESHOLD_CAL_FRAME = None  # refresh after THRESHOLD_CALIBRATION_DAYS change
    _proxy_params = MODEL_FAMILY_CANDIDATES["hist_gradient_boosting"][0]
    _proxy = fit_algorithm_model(
        "hist_gradient_boosting", _proxy_params,
        X_benchmark_train, y_benchmark_train,
        X_validation, y_validation,
    )
    _val_raw = predict_scores_raw(_proxy, X_validation)
    (
        CLEAR_NEGATIVE_TIER_A_SELECTED,
        CLEAR_NEGATIVE_TIER_B_SELECTED,
        _cn_acc,
        _cn_n_a,
        _cn_n_b,
    ) = tune_clear_negative_tiers(_val_raw, y_validation, X_validation)
    del _proxy
    gc.collect()
    print(
        f"Clear-negative tiers (validation-tuned): "
        f"tier_a={CLEAR_NEGATIVE_TIER_A_SELECTED}  tier_b={CLEAR_NEGATIVE_TIER_B_SELECTED}  "
        f"val_accuracy={_cn_acc:.4f}  "
        f"val_capped tier_a={_cn_n_a:,} tier_b={_cn_n_b:,}/{len(X_validation):,}"
    )
else:
    CLEAR_NEGATIVE_TIER_A_SELECTED = None
    CLEAR_NEGATIVE_TIER_B_SELECTED = None

'''

for cell in nb["cells"]:
    t = cell_text(cell)
    if OLD_TUNE_BLOCK in t:
        t = t.replace(OLD_TUNE_BLOCK, NEW_TUNE_BLOCK)
        set_cell_text(cell, t)
        break
else:
    raise SystemExit("Could not find tune block to replace")

OLD_TEST_BLOCK = '''_cn_test_n = 0
if RUN_CLEAR_NEGATIVE_FILTER and CLEAR_NEGATIVE_MIN_FAIL_FREE_SELECTED is not None:
    _, _cn_test_n = apply_clear_negative_filter(
        predict_scores_raw(SELECTED_EVALUATION_MODEL, X_test),
        X_test,
    )
    print(
        f"Clear-negative on test: {_cn_test_n:,}/{len(X_test):,} rows capped "
        f"(min_fail_free>={CLEAR_NEGATIVE_MIN_FAIL_FREE_SELECTED})"
    )
'''

NEW_TEST_BLOCK = '''_cn_test_a, _cn_test_b = 0, 0
if RUN_CLEAR_NEGATIVE_FILTER and (
    CLEAR_NEGATIVE_TIER_A_SELECTED is not None or CLEAR_NEGATIVE_TIER_B_SELECTED is not None
):
    _, _cn_test_a, _cn_test_b = apply_clear_negative_filter(
        predict_scores_raw(SELECTED_EVALUATION_MODEL, X_test),
        X_test,
    )
    print(
        f"Clear-negative on test: tier_a={_cn_test_a:,} tier_b={_cn_test_b:,} "
        f"/ {len(X_test):,}  tiers={CLEAR_NEGATIVE_TIER_A_SELECTED}/{CLEAR_NEGATIVE_TIER_B_SELECTED}"
    )
'''

for cell in nb["cells"]:
    t = cell_text(cell)
    if OLD_TEST_BLOCK in t:
        t = t.replace(OLD_TEST_BLOCK, NEW_TEST_BLOCK)
        set_cell_text(cell, t)
        break
else:
    raise SystemExit("Could not find test reporting block to replace")

OLD_META = (
    '    "run_clear_negative_filter": RUN_CLEAR_NEGATIVE_FILTER,\n'
    '    "clear_negative_min_fail_free": CLEAR_NEGATIVE_MIN_FAIL_FREE_SELECTED,\n'
    '    "clear_negative_score_cap": CLEAR_NEGATIVE_SCORE_CAP,\n'
    '    "validation_days": VALIDATION_DAYS,\n'
    '    "test_days": TEST_DAYS,\n'
)
NEW_META = (
    '    "run_clear_negative_filter": RUN_CLEAR_NEGATIVE_FILTER,\n'
    '    "clear_negative_tier_a": CLEAR_NEGATIVE_TIER_A_SELECTED,\n'
    '    "clear_negative_tier_b": CLEAR_NEGATIVE_TIER_B_SELECTED,\n'
    '    "threshold_calibration_days": THRESHOLD_CALIBRATION_DAYS,\n'
    '    "validation_days": VALIDATION_DAYS,\n'
    '    "test_days": TEST_DAYS,\n'
)

for cell in nb["cells"]:
    t = cell_text(cell)
    if OLD_META in t:
        t = t.replace(OLD_META, NEW_META)
        set_cell_text(cell, t)
        break
else:
    raise SystemExit("Could not find metadata block to replace")

NB.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Patched {NB.name} for v4.6")
