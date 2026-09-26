"""PS1 native GBDT lab: LightGBM, XGBoost, CatBoost and a rank-averaged ensemble,
tuned for ROC-AUC on purged walk-forward folds, read straight from a PS1 notebook's
ETL checkpoint. No Spark.

Why this exists: the notebook's Spark path cannot load LightGBM or CatBoost (their
JARs are built for Spark 3.5; the instance runs Spark 4.2), tunes XGBoost for about
eight trials on one validation window, and picks a single champion. This script
closes those three gaps without re-running the ETL.

Run from the repo root on the SageMaker instance, after a PS1 notebook has printed
"[CHECKPOINT] ETL saved" in CELL 9:

    nice -n 10 python notebooks/ps1_failure_prediction/ps1_native_gbdt_lab.py --fleet GATE

The test window is never used for tuning or model choice. Every model is chosen on
walk-forward folds inside the train+val period, and the test window is scored once
at the end. The figure to quote is the one marked CV-SELECTED.
"""
import argparse
import datetime as _dt
import json
import os
import time
import warnings

import joblib
import numpy as np
import pandas as pd
from scipy.stats import rankdata
from sklearn.metrics import average_precision_score, roc_auc_score

HERE = os.path.dirname(os.path.abspath(__file__))
warnings.filterwarnings("ignore", message="X does not have valid feature names")

# Features whose value on day D includes day D itself or information only known after D.
# Found 25-Sep by code review: usage_failure_count_30d and the S20 cumulative counters end
# at CURRENT ROW; availability_pct_7d holds today's outage minutes; chain_length and met_comms_*
# are same-day counts; evq_dur_* sum durations that may clear after D. "honest" measures the
# figure without them -- the one to quote.
DROP_PRESETS = {
    "honest": ["usage_failure_count_30d", "usage_daily_failure_count", "usage_cumulative_failure_count",
               "usage_cumulative_outage_min", "availability_pct_7d", "chain_length",
               "met_comms_*", "evq_dur_*", "facility_peer_hw_oos_7d"],
}


def log(msg):
    print(f"[{_dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


# --------------------------------------------------------------------------- data
def load_checkpoint(fleet, ckpt_subdir, target_override=""):
    import pyarrow.dataset as ds

    base = os.path.join(HERE, f"ps1_{fleet.lower()}_oos_outputs", ckpt_subdir)
    meta = joblib.load(os.path.join(base, "spark_ckpt_meta.joblib"))
    feats, target = list(meta["FEATURE_COLS"]), meta["TARGET"]
    dset = ds.dataset(os.path.join(base, "spark_splits"), format="parquet")
    names = set(dset.schema.names)
    if target_override:
        # a label variant written beside the headline label (PS1_LABEL_VARIANTS): same rows and
        # features, only the failure days that count differ
        variants = sorted(c for c in names if c.startswith("lblv_"))
        if target_override not in names:
            raise SystemExit(f"--target {target_override} is not in this checkpoint; label variants "
                             f"present: {variants or 'none'}")
        if target_override in feats:
            raise SystemExit(f"--target {target_override} is a feature column, not a label")
        target = target_override
    missing = [c for c in feats + [target, "split", "event_date"] if c not in names]
    if missing:
        raise SystemExit(f"checkpoint lacks columns {missing[:10]} -- re-run the notebook ETL")
    extra = [c for c in ("DEVICE_ID",) if c in names]
    df = dset.to_table(columns=feats + [target, "split", "event_date"] + extra).to_pandas()
    for c in feats:
        if not np.issubdtype(df[c].dtype, np.number):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df[c] = df[c].astype("float32")
    df["event_date"] = pd.to_datetime(df["event_date"])
    df[target] = df[target].astype(int)
    return base, meta, feats, target, df


def walk_forward_folds(dates, n_folds, embargo_days, recent_share):
    """Validation blocks tile the most recent `recent_share` of the dev calendar;
    each fold trains only on days more than `embargo_days` before its block."""
    u = np.sort(dates.unique())
    blocks = np.array_split(u[int(len(u) * (1 - recent_share)):], n_folds)
    folds = []
    for b in blocks:
        vs, ve = b[0], b[-1]
        tr = np.where(dates < vs - np.timedelta64(embargo_days, "D"))[0]
        va = np.where((dates >= vs) & (dates <= ve))[0]
        folds.append((tr, va, str(pd.Timestamp(vs).date()), str(pd.Timestamp(ve).date())))
    return folds


def add_device_prior(df, target, horizon, m=30.0):
    """Leak-safe device propensity: the device's own positive rate over rows whose label
    window had closed before day D (event_date <= D - horizon - 1) -- those failures are
    already observed at scoring time -- shrunk toward the TRAIN-split rate with m pseudo-rows.
    Never reads a label whose window reaches day D or later."""
    p0 = float(df.loc[df["split"] == "train", target].mean())
    lag = np.timedelta64(horizon + 1, "D")
    cy_at, cn_at = np.zeros(len(df)), np.zeros(len(df))
    pos = np.arange(len(df))
    order = df.assign(_i=pos).sort_values(["DEVICE_ID", "event_date"])
    for _, g in order.groupby("DEVICE_ID", sort=False):
        dates = g["event_date"].to_numpy()
        y = g[target].to_numpy()
        cy = np.concatenate([[0], np.cumsum(y)])
        j = np.searchsorted(dates, dates - lag, side="right")
        cy_at[g["_i"].to_numpy()] = cy[j]
        cn_at[g["_i"].to_numpy()] = j
    df["dev_prior_pos_rate"] = ((cy_at + m * p0) / (cn_at + m)).astype("float32")
    return p0


def leak_scan(X, y, feats, seed=0, n=200_000):
    """Solo AUC of each feature against the label. A single feature near 1.0 on its own is
    the classic leak signature -- PS3's text leak showed exactly this."""
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(y), min(n, len(y)), replace=False)
    out = []
    for j, f in enumerate(feats):
        x, yy = X[idx, j], y[idx]
        ok = ~np.isnan(x)
        if ok.sum() < 1000 or yy[ok].min() == yy[ok].max():
            continue
        a = roc_auc_score(yy[ok], x[ok])
        out.append((max(a, 1 - a), f, ok.mean()))
    return sorted(out, reverse=True)


def adversarial(Xd, Xt, feats, threads, seed=0, n=150_000):
    """Train a model to tell dev rows from test rows. AUC near 0.5 = the periods look alike;
    the top features are what changed -- stale or zeroed sources and calendar-like counters."""
    import lightgbm as lgb

    rng = np.random.default_rng(seed)
    a = Xd[rng.choice(len(Xd), min(n, len(Xd)), replace=False)]
    b = Xt[rng.choice(len(Xt), min(n, len(Xt)), replace=False)]
    X = np.vstack([a, b]); y = np.r_[np.zeros(len(a)), np.ones(len(b))]
    perm = rng.permutation(len(y)); X, y = X[perm], y[perm]
    cut = int(len(y) * 0.7)
    m = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.1, num_leaves=31, n_jobs=threads, verbose=-1)
    m.fit(X[:cut], y[:cut])
    auc = roc_auc_score(y[cut:], m.predict_proba(X[cut:])[:, 1])
    imp = sorted(zip(m.booster_.feature_importance("gain"), feats), reverse=True)[:8]
    return auc, imp


# --------------------------------------------------------------------------- models
# dtr / dva: the day of each row. Only the ranker reads them; the classifiers ignore them.
def fit_lgb(p, Xtr, ytr, Xva, yva, threads, n_estimators=None, wtr=None, dtr=None, dva=None):
    import lightgbm as lgb

    m = lgb.LGBMClassifier(
        n_estimators=n_estimators or 2000, learning_rate=p["lr"], num_leaves=p["leaves"],
        min_child_samples=p["mcs"], subsample=p["ss"], subsample_freq=1,
        colsample_bytree=p["cs"], reg_alpha=p["ra"], reg_lambda=p["rl"],
        n_jobs=threads, verbose=-1, random_state=42)
    if n_estimators:
        m.fit(Xtr, ytr, sample_weight=wtr)
        return m, n_estimators
    m.fit(Xtr, ytr, sample_weight=wtr, eval_set=[(Xva, yva)], eval_metric="auc",
          callbacks=[lgb.early_stopping(100, verbose=False)])
    return m, int(m.best_iteration_ or m.n_estimators)


def fit_xgb(p, Xtr, ytr, Xva, yva, threads, n_estimators=None, wtr=None, dtr=None, dva=None):
    import xgboost as xgb

    kw = dict(n_estimators=n_estimators or 2000, learning_rate=p["lr"], max_depth=p["depth"],
              min_child_weight=p["mcw"], subsample=p["ss"], colsample_bytree=p["cs"],
              reg_alpha=p["ra"], reg_lambda=p["rl"], tree_method="hist",
              eval_metric="auc", n_jobs=threads, random_state=42)
    if n_estimators:
        m = xgb.XGBClassifier(**kw)
        m.fit(Xtr, ytr, sample_weight=wtr, verbose=False)
        return m, n_estimators
    m = xgb.XGBClassifier(early_stopping_rounds=100, **kw)
    m.fit(Xtr, ytr, sample_weight=wtr, eval_set=[(Xva, yva)], verbose=False)
    return m, int(m.best_iteration + 1)


def fit_cat(p, Xtr, ytr, Xva, yva, threads, n_estimators=None, wtr=None, dtr=None, dva=None):
    from catboost import CatBoostClassifier

    kw = dict(iterations=n_estimators or 2000, learning_rate=p["lr"], depth=p["depth"],
              l2_leaf_reg=p["l2"], eval_metric="AUC", thread_count=threads,
              verbose=False, allow_writing_files=False, random_seed=42)
    if n_estimators:
        m = CatBoostClassifier(**kw)
        m.fit(Xtr, ytr, sample_weight=wtr)
        return m, n_estimators
    m = CatBoostClassifier(od_type="Iter", od_wait=100, **kw)
    m.fit(Xtr, ytr, sample_weight=wtr, eval_set=(Xva, yva), use_best_model=True)
    return m, int(m.get_best_iteration() + 1)


def by_day(X, y, w, d):
    """Rows in date order plus the size of each day's block: one ranking query per day."""
    if d is None:
        raise SystemExit("lgbrank needs the day of each row")
    d = np.asarray(d)
    if (np.diff(d) < np.timedelta64(0, "D")).any():
        o = np.argsort(d, kind="stable")
        X, y, d, w = X[o], y[o], d[o], (None if w is None else w[o])
    g = np.unique(d, return_counts=True)[1]
    if g.max() > 10_000:
        raise SystemExit(f"lgbrank: a day holds {g.max():,} rows; LightGBM caps a ranking query at 10,000")
    return X, y, w, g


def fit_lgbrank(p, Xtr, ytr, Xva, yva, threads, n_estimators=None, wtr=None, dtr=None, dva=None):
    """LambdaRank with one query per day: learns to order a day's devices, the daily-flag rule,
    rather than a probability. Raw scores compare within a day only; pooled AUC and the
    rank-mean blend read them as they are. Days with one row or one label add no pairs."""
    import lightgbm as lgb

    m = lgb.LGBMRanker(
        objective="lambdarank", n_estimators=n_estimators or 2000, learning_rate=p["lr"],
        num_leaves=p["leaves"], min_child_samples=p["mcs"], subsample=p["ss"], subsample_freq=1,
        colsample_bytree=p["cs"], reg_alpha=p["ra"], reg_lambda=p["rl"],
        n_jobs=threads, verbose=-1, random_state=42)
    Xtr, ytr, wtr, gtr = by_day(Xtr, ytr, wtr, dtr)
    if n_estimators:
        m.fit(Xtr, ytr, group=gtr, sample_weight=wtr)
        return m, n_estimators
    Xva, yva, _, gva = by_day(Xva, yva, None, dva)
    # NDCG over the whole day (cut-off = the largest day): the closest ranking metric to AUC
    m.fit(Xtr, ytr, group=gtr, sample_weight=wtr, eval_set=[(Xva, yva)], eval_group=[gva],
          eval_metric="ndcg", eval_at=[int(gva.max())], callbacks=[lgb.early_stopping(100, verbose=False)])
    return m, int(m.best_iteration_ or m.n_estimators)


# --reg strong: bounds that replace the default search range, (low, high); None keeps the
# default side. Narrows toward shallower, better-regularised trees -- the train-CV gap in the
# overfit check is the reason. ss = bagging_fraction, cs = feature_fraction, rl = lambda_l2.
REG_STRONG = {
    "lgb": {"leaves": (None, 63), "mcs": (200, None), "cs": (None, 0.7), "ss": (None, 0.8), "rl": (1, None)},
    "xgb": {"depth": (None, 6), "mcw": (20, None), "ss": (None, 0.8), "cs": (None, 0.7), "rl": (1, None)},
    "cat": {"depth": (None, 6), "l2": (5, None)},
}
REG_STRONG["lgbrank"] = REG_STRONG["lgb"]  # same trees, same search space


def space(trial, name, reg="none"):
    clamp = REG_STRONG.get(name, {}) if reg == "strong" else {}

    def b(key, lo, hi):
        c_lo, c_hi = clamp.get(key, (None, None))
        return (lo if c_lo is None else max(lo, c_lo)), (hi if c_hi is None else min(hi, c_hi))

    if name in ("lgb", "lgbrank"):
        return {"lr": trial.suggest_float("lr", 0.02, 0.1, log=True),
                "leaves": trial.suggest_int("leaves", *b("leaves", 15, 255), log=True),
                "mcs": trial.suggest_int("mcs", *b("mcs", 20, 2000), log=True),
                "ss": trial.suggest_float("ss", *b("ss", 0.5, 1.0)),
                "cs": trial.suggest_float("cs", *b("cs", 0.3, 1.0)),
                "ra": trial.suggest_float("ra", 1e-3, 10, log=True),
                "rl": trial.suggest_float("rl", *b("rl", 1e-3, 10), log=True)}
    if name == "xgb":
        return {"lr": trial.suggest_float("lr", 0.02, 0.1, log=True),
                "depth": trial.suggest_int("depth", *b("depth", 3, 10)),
                "mcw": trial.suggest_float("mcw", *b("mcw", 1, 200), log=True),
                "ss": trial.suggest_float("ss", *b("ss", 0.5, 1.0)),
                "cs": trial.suggest_float("cs", *b("cs", 0.3, 1.0)),
                "ra": trial.suggest_float("ra", 1e-3, 10, log=True),
                "rl": trial.suggest_float("rl", *b("rl", 1e-3, 10), log=True)}
    return {"lr": trial.suggest_float("lr", 0.03, 0.15, log=True),
            "depth": trial.suggest_int("depth", *b("depth", 4, 8)),
            "l2": trial.suggest_float("l2", *b("l2", 1, 30), log=True)}


FIT = {"lgb": fit_lgb, "xgb": fit_xgb, "cat": fit_cat, "lgbrank": fit_lgbrank}


def proba(m, X):
    if not hasattr(m, "predict_proba"):  # the ranker: raw score, ordered within a day
        return m.predict(X)
    return m.predict_proba(X)[:, 1]


def tune(name, X, y, folds, trials, threads, budget_min, W=None, reg="none", D=None):
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial):
        p, aucs = space(trial, name, reg), []
        for k, (tr, va, _, _) in enumerate(folds):
            m, _ = FIT[name](p, X[tr], y[tr], X[va], y[va], threads,
                             wtr=None if W is None else W[tr],
                             dtr=None if D is None else D[tr], dva=None if D is None else D[va])
            aucs.append(roc_auc_score(y[va], proba(m, X[va])))
            trial.report(float(np.mean(aucs)), k)
            if trial.should_prune():
                raise optuna.TrialPruned()
        return float(np.mean(aucs))

    study = optuna.create_study(direction="maximize",
                                sampler=optuna.samplers.TPESampler(seed=42),
                                pruner=optuna.pruners.MedianPruner(n_startup_trials=4))
    study.optimize(objective, n_trials=trials, timeout=budget_min * 60)
    done = [t for t in study.trials if t.state.name == "COMPLETE"]
    log(f"{name}: {len(done)} complete / {len(study.trials)} trials, best CV AUC {study.best_value:.4f}")
    return study.best_params


def oof(name, p, X, y, folds, threads, W=None, D=None):
    """Refit the chosen parameters on every fold; return per-fold scores and best iterations."""
    out, iters = [], []
    for tr, va, _, _ in folds:
        m, it = FIT[name](p, X[tr], y[tr], X[va], y[va], threads,
                          wtr=None if W is None else W[tr],
                          dtr=None if D is None else D[tr], dva=None if D is None else D[va])
        out.append(proba(m, X[va]))
        iters.append(it)
    return out, iters


def rank_mean(arrs):
    return np.mean([rankdata(a) / len(a) for a in arrs], axis=0)


def pct_ranks(arrs):
    """Each score array as a percentile rank within its own set, one column per model."""
    return np.column_stack([rankdata(a) / len(a) for a in arrs])


def fit_stack(Z, y):
    from sklearn.linear_model import LogisticRegression

    return LogisticRegression(C=1.0, solver="lbfgs").fit(Z, y)


def stack_score(meta, arrs):
    """--stack: the meta-model on percentile ranks, built as the ensemble builds its ranks."""
    return meta.predict_proba(pct_ranks(arrs))[:, 1]


def add_relative(frame, cols, keys):
    """rel_<f>: the row's percentile rank of f among the rows sharing its day (average ties,
    NaN kept). Other devices' same-day values are known at scoring time; no label is read."""
    r = frame.groupby(keys)[cols].rank(pct=True)
    return {"rel_" + c: r[c].astype("float32") for c in cols}


# --------------------------------------------------------------------------- metrics
def report(label, y, s, dates, lines):
    auc, ap = roc_auc_score(y, s), average_precision_score(y, s)
    order, n, base = np.argsort(-s), len(s), y.mean()
    parts = []
    for f in (0.01, 0.05, 0.10):
        k = max(1, int(n * f))
        prec = y[order[:k]].mean()
        parts.append(f"P@{int(f*100)}% {prec:.3f} ({prec/base:.2f}x)")
    mon, suffix = {}, ""
    if dates is not None:
        d = pd.DataFrame({"m": dates.dt.to_period("M").astype(str), "day": dates.to_numpy(), "y": y, "s": s})
        mon = {m: roc_auc_score(g.y, g.s) for m, g in d.groupby("m") if 0 < g.y.sum() < len(g)}
        ma = [v for m, v in mon.items() if m >= "2026-03"]
        # the operational rule: each day, flag the top 5% of that day's devices
        r = d.groupby("day")["s"].rank(ascending=False, pct=True)
        daily = d.loc[r <= 0.05, "y"].mean()
        suffix = f"  | daily-top5% P {daily:.3f}" + (f"  | Mar-Aug monthly mean {np.mean(ma):.4f}" if ma else "")
    lines.append(f"{label:26s} AUC {auc:.4f}  AP {ap:.4f}  " + "  ".join(parts) + suffix)
    return {"auc": auc, "ap": ap, "monthly": mon}


def operating_points(y_cal, s_cal, y_test, s_test, targets=(0.8, 0.7, 0.6, 0.5)):
    """Lowering recall raises precision and accuracy and lowers FPR; it never moves AUC.
    Recall is not taken below 0.50 (PK, 25-Sep): the 50% row is the operating point.
    For each recall target, the share of devices to flag is set on the last walk-forward
    fold and the same share is flagged on test -- a daily-budget rule, which transfers
    across a shifting base rate where a raw probability threshold does not. Test recall
    therefore lands near, not exactly on, the target."""
    o = np.argsort(-s_cal)
    cum = np.cumsum(y_cal[o]) / max(1, y_cal.sum())
    ot, n = np.argsort(-s_test), len(s_test)
    P = int(y_test.sum())
    N = n - P
    rows = []
    for r in targets:
        frac = (int(np.searchsorted(cum, r)) + 1) / len(s_cal)
        k = max(1, int(round(frac * n)))
        tp = int(y_test[ot[:k]].sum())
        fp = k - tp
        prec, rec = tp / k, (tp / P if P else 0.0)
        acc, fpr = (tp + (N - fp)) / n, (fp / N if N else 0.0)
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        rows.append({"target_recall": r, "flag_share": frac, "recall": rec, "precision": prec,
                     "accuracy": acc, "fpr": fpr, "f1": f1})
    return rows


DAILY_BUDGETS = (0.01, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30)
BUDGET_GRID = tuple(round(0.005 * i, 3) for i in range(1, 201))  # 0.5% .. 100%, 0.5% steps (F1 can peak high)
CONSTRAINTS = (("precision >= 0.90", "precision", 0.90), ("precision >= 0.80", "precision", 0.80),
               ("FPR <= 0.10", "fpr", 0.10), ("max F1", "f1", None))


def daily_rank(s, days):
    """Within-day rank share of each row's score: 1/n_day for the day's highest. Ties go
    by row order (method='first'), so a budget q flags floor(q * n_day) devices every day
    instead of dropping or admitting a whole tied block at the cut."""
    return (pd.Series(np.asarray(s)).groupby(pd.Series(np.asarray(days)))
            .rank(ascending=False, method="first", pct=True).to_numpy())


def budget_metrics(y, r, budgets):
    """Confusion-matrix metrics when each day's top q share is flagged, for each q. The
    flagged sets are nested in q, so one sort by within-day rank serves every budget."""
    o = np.argsort(r, kind="stable")
    rs, cy = r[o], np.concatenate([[0], np.cumsum(y[o])])
    n, P = len(y), int(y.sum())
    N = n - P
    rows = []
    for q in budgets:
        k = int(np.searchsorted(rs, q + 1e-9, side="right"))  # r = rank/n_day; guard float ties at q
        tp = int(cy[k])
        fp = k - tp
        prec, rec = (tp / k if k else None), (tp / P if P else 0.0)  # nothing flagged: undefined
        acc, fpr = (tp + (N - fp)) / n, (fp / N if N else 0.0)
        f1 = 2 * prec * rec / (prec + rec) if prec and prec + rec else 0.0
        rows.append({"budget": float(q), "flag_share": k / n, "recall": rec, "precision": prec,
                     "accuracy": acc, "fpr": fpr, "f1": f1})
    return rows


def constrained_ops(y_cal, s_cal, days_cal, y_test, s_test, days_test):
    """Recall-optimised operating points picked WITHOUT test: on the last CV fold, the largest
    daily budget that still meets each contract clause (or the F1-maximising one), then that
    same budget flagged on test. A larger budget can only add flags, so the largest feasible
    budget is the most recall that clause allows."""
    cal = budget_metrics(y_cal, daily_rank(s_cal, days_cal), BUDGET_GRID)
    r_test = daily_rank(s_test, days_test)
    out = []
    for label, key, bound in CONSTRAINTS:
        if bound is None:
            best_f1 = max(c["f1"] for c in cal)
            ok = [c for c in cal if c["f1"] == best_f1 and best_f1 > 0]  # ties: the larger budget
        elif key == "fpr":
            ok = [c for c in cal if c["fpr"] <= bound and c["flag_share"] > 0]
        else:
            ok = [c for c in cal if c[key] is not None and c[key] >= bound]
        if not ok:
            out.append({"constraint": label, "reachable": False, "budget": None})
            continue
        # budgets that flag the same rows on the fold are one choice (floor(q * n_day) per day);
        # take the smallest of them so a day-size shift on test cannot add flags the fold never saw
        pick = next(c for c in cal if c["flag_share"] == ok[-1]["flag_share"])
        out.append({"constraint": label, "reachable": True, "budget": pick["budget"],
                    "fold": {k: pick[k] for k in ("flag_share", "recall", "precision", "accuracy", "fpr", "f1")},
                    "test": budget_metrics(y_test, r_test, [pick["budget"]])[0]})
    return out


def diagnostics(dev, test, feats, Xd, yd, Xt, folds, threads, seed=0, n=50_000):
    """Methodology checks. Trees assume neither normality nor independent features, so
    collinearity and skew are REPORTED, not fixed; duplicates, leakage and drift are the
    ones that can invalidate a number."""
    import lightgbm as lgb
    from scipy.stats import kurtosis, skew

    rng = np.random.default_rng(seed)
    out = {}
    key = [c for c in ("DEVICE_ID", "event_date") if c in dev.columns]
    if len(key) == 2:
        out["grain_dups_dev"] = int(dev.duplicated(subset=key).sum())
        out["grain_dups_test"] = int(test.duplicated(subset=key).sum())
    else:
        out["grain_dups_dev"] = out["grain_dups_test"] = -1  # key columns not in the frame

    S = Xd[rng.choice(len(Xd), min(n, len(Xd)), replace=False)].astype(np.float64)
    miss = np.isnan(S).mean(axis=0)
    med = np.nanmedian(np.where(np.isnan(S).all(axis=0), 0.0, S), axis=0)
    Sf = np.where(np.isnan(S), med, S)
    top_share = np.array([np.unique(Sf[:, j], return_counts=True)[1].max() / len(Sf) for j in range(Sf.shape[1])])
    out["missing_gt_50pct"] = [feats[j] for j in np.where(miss > 0.5)[0]]
    out["near_constant"] = [feats[j] for j in np.where(top_share > 0.99)[0]]

    # duplicate columns, Spearman correlation, VIF (diagonal of the inverse correlation matrix)
    live = [j for j in range(Sf.shape[1]) if Sf[:, j].std() > 0]
    R = np.column_stack([rankdata(Sf[:, j]) for j in live])
    C = np.corrcoef(R, rowvar=False)
    dup, hi = [], []
    for a_ in range(len(live)):
        for b_ in range(a_ + 1, len(live)):
            r = C[a_, b_]
            if abs(r) > 0.999 and np.array_equal(Sf[:, live[a_]], Sf[:, live[b_]]):
                dup.append((feats[live[a_]], feats[live[b_]]))
            elif abs(r) >= 0.95:
                hi.append((round(float(r), 3), feats[live[a_]], feats[live[b_]]))
    out["duplicate_columns"] = dup
    out["corr_ge_0_95"] = sorted(hi, key=lambda t: -abs(t[0]))
    P = np.corrcoef(Sf[:, live], rowvar=False)
    vif = np.diag(np.linalg.pinv(P))
    out["vif_gt_10"] = sorted(((round(float(v), 1), feats[live[i]]) for i, v in enumerate(vif) if v > 10),
                              reverse=True)

    sk = np.array([skew(Sf[:, j]) for j in live])
    ku = np.array([kurtosis(Sf[:, j]) for j in live])
    out["skew_gt_2"] = int((np.abs(sk) > 2).sum())
    out["kurtosis_gt_7"] = int((ku > 7).sum())
    out["n_live"] = len(live)

    # population stability, dev deciles
    T = Xt[rng.choice(len(Xt), min(n, len(Xt)), replace=False)].astype(np.float64)
    psi = []
    for j in range(len(feats)):
        d, t = S[:, j], T[:, j]
        d, t = d[~np.isnan(d)], t[~np.isnan(t)]
        if len(d) < 100 or len(t) < 100 or d.std() == 0:
            continue
        edges = np.unique(np.quantile(d, np.linspace(0, 1, 11)))
        if len(edges) < 3:
            continue
        pd_ = np.histogram(np.clip(d, edges[0], edges[-1]), edges)[0] / len(d) + 1e-4
        pt_ = np.histogram(np.clip(t, edges[0], edges[-1]), edges)[0] / len(t) + 1e-4
        psi.append((round(float(np.sum((pt_ - pd_) * np.log(pt_ / pd_))), 3), feats[j]))
    out["psi_gt_0_25"] = sorted([x for x in psi if x[0] > 0.25], reverse=True)

    # shuffled-label control: must land near 0.50
    yp = rng.permutation(yd)
    aucs = []
    for tr, va, _, _ in folds:
        m = lgb.LGBMClassifier(n_estimators=150, learning_rate=0.1, num_leaves=31, n_jobs=threads,
                               verbose=-1, random_state=seed)
        m.fit(Xd[tr], yp[tr])
        aucs.append(roc_auc_score(yp[va], m.predict_proba(Xd[va])[:, 1]))
    out["shuffled_label_cv_auc"] = round(float(np.mean(aucs)), 4)

    print("\nDIAGNOSTICS")
    print(f"  grain duplicates (DEVICE_ID, date): dev {out['grain_dups_dev']}, test {out['grain_dups_test']}"
          + ("   (key columns absent)" if out['grain_dups_dev'] < 0
             else "   <-- INSPECT" if out['grain_dups_dev'] or out['grain_dups_test'] else "   OK"))
    print(f"  duplicate feature columns: {len(dup)}" + (f"  {dup[:5]}" if dup else "   OK"))
    print(f"  |Spearman| >= 0.95 pairs: {len(hi)}" + (f"  e.g. {out['corr_ge_0_95'][:5]}" if hi else ""))
    print(f"  VIF > 10: {len(out['vif_gt_10'])} of {len(live)} live features"
          + (f"  top {out['vif_gt_10'][:5]}" if out['vif_gt_10'] else "")
          + "  (trees are unbiased under collinearity; it spreads importance)")
    print(f"  near-constant (>99% one value): {len(out['near_constant'])}  missing >50%: {len(out['missing_gt_50pct'])}")
    print(f"  |skew| > 2: {out['skew_gt_2']} / kurtosis > 7: {out['kurtosis_gt_7']} of {len(live)} "
          "(no normality assumption in tree models; relevant to linear baselines only)")
    print(f"  PSI dev->test > 0.25: {len(out['psi_gt_0_25'])}" + (f"  top {out['psi_gt_0_25'][:6]}" if out['psi_gt_0_25'] else ""))
    print(f"  shuffled-label CV AUC: {out['shuffled_label_cv_auc']:.4f}  "
          + ("OK (expect ~0.50)" if out['shuffled_label_cv_auc'] < 0.55 else "<-- ALARM: pipeline leakage"))
    return out


def block_bootstrap_auc(y, s, days, n_boot=200, seed=0):
    """95% interval for AUC, resampling whole DAYS so within-day correlation is respected."""
    rng = np.random.default_rng(seed)
    ud = np.unique(days)
    idx_by_day = {d: np.where(days == d)[0] for d in ud}
    vals = []
    for _ in range(n_boot):
        pick = rng.choice(ud, len(ud), replace=True)
        ii = np.concatenate([idx_by_day[d] for d in pick])
        if 0 < y[ii].sum() < len(ii):
            vals.append(roc_auc_score(y[ii], s[ii]))
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def block_bootstrap_auc_delta(y, s1, s2, days, n_boot=200, seed=0):
    """95% interval for AUC(s1) - AUC(s2) over the same day resamples. Two overlapping marginal
    intervals say little about a difference between scores of the same rows; this pairs them."""
    rng = np.random.default_rng(seed)
    ud = np.unique(days)
    idx_by_day = {d: np.where(days == d)[0] for d in ud}
    vals = []
    for _ in range(n_boot):
        pick = rng.choice(ud, len(ud), replace=True)
        ii = np.concatenate([idx_by_day[d] for d in pick])
        if 0 < y[ii].sum() < len(ii):
            vals.append(roc_auc_score(y[ii], s1[ii]) - roc_auc_score(y[ii], s2[ii]))
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


# --------------------------------------------------------------------------- data-quality masks
def parse_date_ranges(spec):
    """'2026-04-02:2026-04-10,2025-03-05' -> the listed days, sorted."""
    days = set()
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        lo, _, hi = part.partition(":")
        lo = pd.Timestamp(lo)
        hi = pd.Timestamp(hi) if hi else lo
        if hi < lo:
            raise SystemExit(f"--exclude-dates range {part!r} ends before it starts")
        days.update(pd.date_range(lo, hi, freq="D"))
    return sorted(days)


def thin_days(dates, floor, window=31):
    """Days whose checkpoint row count is below `floor` x the centred rolling median: the
    ingestion-outage signature, where a whole fleet's rows vanish together. A day with no rows
    inside the span counts as thin; the ETL's interior mask may already have removed it."""
    cnt = pd.Series(1, index=pd.DatetimeIndex(dates).normalize()).groupby(level=0).size()
    full = cnt.reindex(pd.date_range(cnt.index.min(), cnt.index.max(), freq="D"), fill_value=0)
    med = full.rolling(window, center=True, min_periods=7).median()
    return sorted(full.index[(full < floor * med).to_numpy()]), full, med


def mask_rows(event_dates, days, pre, post):
    """True for rows dated in [d - pre, d + post] of a masked day d: the day itself, the rows whose
    `pre`-day lookahead crosses it, and the rows whose prior windows it silenced."""
    if not days:
        return np.zeros(len(event_dates), bool)
    ext = set()
    for d in days:
        ext.update(pd.date_range(d - pd.Timedelta(days=pre), d + pd.Timedelta(days=post), freq="D"))
    return pd.DatetimeIndex(event_dates).normalize().isin(pd.DatetimeIndex(sorted(ext)))


def ranges_text(days):
    """Consecutive days as 'a..b (nd)' ranges."""
    days = sorted(pd.Timestamp(d) for d in days)
    out, i = [], 0
    while i < len(days):
        j = i
        while j + 1 < len(days) and (days[j + 1] - days[j]).days == 1:
            j += 1
        out.append(f"{days[i]:%Y-%m-%d}" + (f"..{days[j]:%Y-%m-%d} ({j - i + 1}d)" if j > i else ""))
        i = j + 1
    return ", ".join(out) or "none"


# --------------------------------------------------------------------------- threshold policy
# Annexure 1 Table 13; Table 14 lowers accuracy to 0.85 when data quality is below 95%.
CONTRACT = (("accuracy", ">=", 0.90), ("precision", ">=", 0.90), ("recall", ">=", 0.80),
            ("f1", ">=", 0.80), ("fpr", "<=", 0.10))


def policy_score(scores, names):
    """A probability on a fixed scale for thresholding: the chosen model's own probability, or the
    mean of the members' probabilities for a blend. The ensemble and stack scores are ranks within
    the scored batch, so a cut on them would not carry from one month to the next. LambdaRank
    emits unscaled scores and joins the mean only when it is the sole member."""
    use = [n for n in names if n != "lgbrank"] or list(names)
    return np.mean([scores[n] for n in use], axis=0)


def confusion(y, flag):
    n, P = len(y), int(y.sum())
    N, k = n - P, int(flag.sum())
    tp = int(y[flag].sum())
    fp = k - tp
    prec = tp / k if k else None
    rec = tp / P if P else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec and prec + rec else 0.0
    return {"flag_share": k / n if n else 0.0, "recall": rec, "precision": prec,
            "accuracy": (tp + N - fp) / n if n else None, "fpr": fp / N if N else 0.0, "f1": f1,
            "flagged": k, "tp": tp}


def _cut_for_precision(y, p, target, min_flags=30):
    """Lowest probability cut whose flagged rows reach `target` precision, with at least
    `min_flags` rows flagged. None when no cut does."""
    o = np.argsort(-p, kind="stable")
    k = np.arange(1, len(o) + 1)
    ok = np.flatnonzero((np.cumsum(y[o]) / k >= target) & (k >= min_flags))
    return float(p[o][ok[-1]]) if len(ok) else None


def _cut_for_recall(y, p, target):
    """Highest probability cut whose flagged rows reach `target` recall."""
    o = np.argsort(-p, kind="stable")
    P = y.sum()
    if P == 0:
        return None
    i = min(int(np.searchsorted(np.cumsum(y[o]) / P, target)), len(o) - 1)
    return float(p[o][i])


def episode_ids(dev_ids, dates, y):
    """Approximate failure episodes among positive rows: a device's positive rows on consecutive
    days form one run, the lead days of one failure. -1 on negative rows. Two failures whose
    lead windows touch merge into one run, so the count is a lower bound."""
    d = pd.DataFrame({"dev": np.asarray(dev_ids), "t": pd.DatetimeIndex(dates).normalize(),
                      "y": np.asarray(y), "i": np.arange(len(y))})
    pos = d[d["y"] == 1].sort_values(["dev", "t"])
    new = (pos["dev"] != pos["dev"].shift()) | ((pos["t"] - pos["t"].shift()).dt.days != 1)
    ep = np.full(len(d), -1)
    ep[pos["i"].to_numpy()] = new.cumsum().to_numpy()
    return ep


def episode_recall(ep, flag):
    """Share of failure episodes with at least one flagged lead day."""
    m = ep >= 0
    if not m.any():
        return None, 0
    caught = pd.Series(flag[m]).groupby(ep[m]).any()
    return float(caught.mean()), int(len(caught))


def threshold_policy(y, p, dates, horizon, p_high=0.90, r_med=0.50, cal_days=30, min_cal=500):
    """Calibrated fixed cuts on the model's probability, recalibrated every month. Before each
    month, both cuts are set on the latest `cal_days` of rows whose labels had matured by then
    (dated <= month start - (horizon + 1)), scored as they were at the time, and applied unchanged
    to the whole month; no row's own label ever sets its cut.
      High   = at or above the lowest cut whose calibration precision reached p_high
      Medium = down to the cut whose calibration recall reached r_med
      Low    = the rest
    A month with too few matured rows before it (the first test month) is the warm-up and is
    left unscored: tier -1."""
    dates = pd.DatetimeIndex(dates).normalize()
    mon = np.asarray(dates.to_period("M").astype(str))
    tier = np.full(len(y), -1)
    rows = []
    for m in sorted(set(mon)):
        cut_end = pd.Timestamp(f"{m}-01") - pd.Timedelta(days=horizon + 1)
        cal = np.asarray((dates <= cut_end) & (dates > cut_end - pd.Timedelta(days=cal_days))) & ~np.isnan(p)
        idx = np.flatnonzero((mon == m) & ~np.isnan(p))
        if cal.sum() < min_cal or not 0 < y[cal].sum() < cal.sum() or not len(idx):
            rows.append({"month": m, "scored": False, "cal_rows": int(cal.sum())})
            continue
        th = _cut_for_precision(y[cal], p[cal], p_high)
        tm = _cut_for_recall(y[cal], p[cal], r_med)
        if th is not None and tm is not None and tm > th:  # High alone already reaches the recall target
            tm = th
        t = np.zeros(len(idx), int)
        if tm is not None:
            t[p[idx] >= tm] = 1
        if th is not None:
            t[p[idx] >= th] = 2
        tier[idx] = t
        rows.append({"month": m, "scored": True, "cal_rows": int(cal.sum()), "cut_high": th, "cut_med": tm,
                     "high": confusion(y[idx], t == 2), "high_med": confusion(y[idx], t >= 1)})
    return tier, rows


def print_policy(title, y, tier, rows, ep, p_high, r_med, horizon, cal_days):
    """The threshold-policy tables: month by month, the tiers, both policies and the contract."""
    def _f(v, w=6, d=3):
        return f"{v:>{w}.{d}f}" if v is not None else f"{'-':>{w}s}"

    print(f"\n{title}")
    print(f"  cuts set before each month on the latest {cal_days} days of matured rows (dated <= month start - "
          f"{horizon + 1}d), then fixed for the month; High: precision >= {p_high:.2f} on those rows, "
          f"Medium: down to recall {r_med:.2f}")
    print("  month     cal rows  cut High  cut Med |  High: flag    P      R  |  High+Med: flag    P      R     FPR    acc")
    for r in rows:
        if not r["scored"]:
            print(f"  {r['month']}  {r['cal_rows']:>8,}   warm-up: too few matured rows before this month")
            continue
        h, hm = r["high"], r["high_med"]
        print(f"  {r['month']}  {r['cal_rows']:>8,}  {_f(r['cut_high'], 8)}  {_f(r['cut_med'], 7)} |"
              f"  {h['flag_share']:>10.1%}  {_f(h['precision'])} {_f(h['recall'])} |"
              f"  {hm['flag_share']:>14.1%}  {_f(hm['precision'])} {_f(hm['recall'])}  {_f(hm['fpr'])}  {_f(hm['accuracy'])}")
    sc = tier >= 0
    if not sc.any():
        print("  no month could be scored")
        return {"months": rows}
    ys, ts = y[sc], tier[sc]
    P = max(1, int(ys.sum()))
    print(f"  RISK TIERS over the scored months ({int(sc.sum()):,} rows, base rate {ys.mean():.3f})")
    print("  tier      share of rows   observed failure rate   share of all failures")
    tiers = {}
    for lvl, name in ((2, "High"), (1, "Medium"), (0, "Low")):
        mk = ts == lvl
        rate = float(ys[mk].mean()) if mk.any() else None
        tiers[name] = {"share": float(mk.mean()), "failure_rate": rate, "share_of_failures": float(ys[mk].sum() / P)}
        print(f"  {name:8s}  {mk.mean():>13.1%}   {_f(rate, 21)}   {ys[mk].sum() / P:>21.1%}")
    out = {"months": rows, "tiers": tiers, "policies": {}}
    print("  policy               flagged   recall  episode-recall  precision  accuracy    FPR      F1")
    for key, name, flag in (("high", "flag High", ts == 2), ("high_med", "flag High+Medium", ts >= 1)):
        c = confusion(ys, flag)
        er, n_ep = episode_recall(ep[sc], flag)
        c["episode_recall"], c["episodes"] = er, n_ep
        out["policies"][key] = c
        print(f"  {name:18s}  {c['flag_share']:>7.1%}   {c['recall']:>6.3f}  {_f(er, 14)}  {_f(c['precision'], 9)}"
              f"  {_f(c['accuracy'], 8)}  {c['fpr']:>6.3f}  {c['f1']:>6.3f}")
        verdict = []
        for k, op, bound in CONTRACT:
            v = c[k]
            ok = v is not None and (v >= bound if op == ">=" else v <= bound)
            verdict.append(f"{k} {'-' if v is None else f'{v:.3f}'} {'MET' if ok else 'not met'}")
        acc_ok = c["accuracy"] is not None and c["accuracy"] >= 0.85
        print(f"    contract (Annexure 1): " + "; ".join(verdict)
              + f"; accuracy >= 0.85 (Table 14) {'MET' if acc_ok else 'not met'}"
              + f"; recall >= 0.50 floor {'MET' if c['recall'] >= 0.50 else 'not met'}")
    print(f"  episode-recall = share of failures ({n_ep:,} approximate episodes) with at least one flagged lead day; "
          "a metric definition to agree with Cubic, reported beside row recall, never instead of it")
    return out


def refit_monthly(df, feats, target, dev, Xd, yd, test, Xt, a, names, params, rounds, combine=None):
    """Production retraining, simulated: each test month is scored by models refitted -- same tuned
    params, same rounds as the final fit, nothing re-tuned -- on every row whose label had matured
    when the month opened. Day D's label covers D+1..D+horizon, so a retrain on day S may use rows
    dated <= S - (horizon + 1); earlier test months join the training data as they would in
    production. Returns scores aligned to `test` (NaN for a month that could not be fitted), with
    the ensemble ranked within each month, and one log row per month. `combine` replaces the
    rank-mean blend of several models (--stack passes the fixed meta-model)."""
    keep = df["split"] == "test"  # the untrimmed test split: --test-start limits scoring, not training
    if a.warmup_end:
        keep &= df["event_date"] >= pd.Timestamp(a.warmup_end)
    if a.train_start:
        keep &= df["event_date"] >= pd.Timestamp(a.train_start)
    late = df.loc[keep, feats + [target, "event_date"]].sort_values("event_date", kind="stable")
    # dev is date-sorted and ends before test, so the pool is one sorted matrix and every month's
    # training set is a prefix of it -- a view, not a per-month copy. Dev rows keep Xd's order,
    # so a month whose cutoff admits exactly the dev rows reproduces the final fit.
    Xp = np.concatenate([Xd, late[feats].to_numpy(np.float32)])
    yp = np.concatenate([yd, late[target].to_numpy()])
    dp = np.concatenate([dev["event_date"].to_numpy(), late["event_date"].to_numpy()])
    del late
    if (np.diff(dp) < np.timedelta64(0, "D")).any():  # splits overlap in time: sort once
        o = np.argsort(dp, kind="stable")
        Xp, yp, dp = Xp[o], yp[o], dp[o]
    mon = test["event_date"].dt.to_period("M").astype(str).to_numpy()
    s = np.full(len(test), np.nan)
    pm = np.full(len(test), np.nan)  # mean member probability, for the --policy thresholds
    rows = []
    for m in sorted(set(mon)):
        idx = np.flatnonzero(mon == m)
        cut = (pd.Timestamp(f"{m}-01") - pd.Timedelta(days=a.horizon + 1)).to_datetime64()
        k = int(np.searchsorted(dp, cut, side="right"))
        pos = int(yp[:k].sum())
        if not 0 < pos < k:
            log(f"refit {m}: {k:,} matured rows, {pos:,} positive -- cannot fit, month left unscored")
            rows.append({"month": m, "cutoff": str(pd.Timestamp(cut).date()), "train_rows": k,
                         "positives": pos, "scored": 0, "fit_sec": 0.0})
            continue
        W = None
        if a.recency_halflife > 0:  # age from this refit's last training day, as the final fit ages from dev's
            W = np.power(0.5, ((dp[k - 1] - dp[:k]) // np.timedelta64(1, "D")) / a.recency_halflife)
        t1, sc = time.time(), {}
        for n in names:
            mdl, _ = FIT[n](params[n], Xp[:k], yp[:k], None, None, a.threads, n_estimators=rounds[n], wtr=W,
                            dtr=dp[:k])
            sc[n] = proba(mdl, Xt[idx])
        s[idx] = (combine or rank_mean)([sc[n] for n in names])
        pm[idx] = policy_score({n: sc[n] for n in names}, names)
        sec = time.time() - t1
        log(f"refit {m}: trained on {k:,} rows to {pd.Timestamp(cut).date()} ({pos:,} positive), "
            f"scored {len(idx):,}, fit {sec:.0f}s")
        rows.append({"month": m, "cutoff": str(pd.Timestamp(cut).date()), "train_rows": k,
                     "positives": pos, "scored": int(len(idx)), "fit_sec": round(sec, 1)})
    return s, rows, pm


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet", required=True, choices=["GATE", "TVM", "VALIDATOR"])
    ap.add_argument("--ckpt", default="checkpoints", help="checkpoint folder under the fleet's outputs")
    ap.add_argument("--models", default="lgb,xgb,cat",
                    help="any of lgb, xgb, cat, lgbrank (LambdaRank, one query per day)")
    ap.add_argument("--trials", default="lgb=25,xgb=15,cat=6")
    ap.add_argument("--budget-min", type=int, default=20, help="per-model tuning time cap")
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--recent-share", type=float, default=0.4)
    ap.add_argument("--embargo-days", type=int, default=14, help="matches CELL 9")
    ap.add_argument("--train-start", default="",
                    help="drop dev rows before this date (e.g. 2024-09-01); test is unchanged")
    ap.add_argument("--recency-halflife", type=float, default=0.0,
                    help="weight dev rows by 0.5 ** (age_days / N), age from the last dev day; 0 = off")
    ap.add_argument("--test-start", default="",
                    help="score only test rows on/after this date (e.g. 2026-03-01 skips TVM's "
                         "label-degenerate Jan/Feb 2026); training and CV are unchanged")
    ap.add_argument("--warmup-end", default="2023-08-01",
                    help="drop training rows before this date (LEFT-CENSORED warm-up); '' keeps them")
    ap.add_argument("--tune-frac", type=float, default=1.0, help="device sample used for tuning only")
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--drop", default="", help="comma list of features to withhold; name* = prefix")
    ap.add_argument("--drop-preset", default="", choices=["", "honest"])
    ap.add_argument("--device-prior", action="store_true",
                    help="add dev_prior_pos_rate: the device's own label rate over closed windows only")
    ap.add_argument("--horizon", type=int, default=7,
                    help="label horizon, for the device-prior lag and the --refit-monthly cutoff")
    ap.add_argument("--no-scan", action="store_true", help="skip the leak and drift checks")
    ap.add_argument("--no-diag", action="store_true",
                    help="skip the methodology diagnostics (duplicates, collinearity, PSI, shuffled-label)")
    ap.add_argument("--reg", default="none", choices=["none", "strong"],
                    help="strong narrows the Optuna ranges toward regularised trees (REG_STRONG)")
    ap.add_argument("--refit-monthly", action="store_true",
                    help="also score each test month with models refitted on the rows matured before it "
                         "(tuned params and rounds unchanged); costs about one final fit per test month")
    ap.add_argument("--stack", action="store_true",
                    help="add 'stack': a logistic regression on the models' OOF percentile ranks; its CV is "
                         "forward-chained (fold k fits on earlier folds only), and it takes the QUOTE from the "
                         "ensemble only by 0.005 or more")
    ap.add_argument("--relative-features", type=int, default=0,
                    help="add rel_<f>, f's within-day percentile rank, for the N features with the highest "
                         "solo AUC on dev rows; 0 = off")
    ap.add_argument("--target", default="",
                    help="score a label variant written by PS1_LABEL_VARIANTS (lblv_*) instead of the headline "
                         "label; same rows and features, only the failure days that count differ")
    ap.add_argument("--exclude-dates", default="",
                    help="data-quality mask 'YYYY-MM-DD:YYYY-MM-DD,...': the days, the rows whose --horizon "
                         "lookahead crosses them and --mask-post days after leave training and test")
    ap.add_argument("--mask-thin", type=float, default=0.0,
                    help="also mask days whose checkpoint row count is below this share of the 31-day rolling "
                         "median within their split (0.5 matches the notebook's completeness guard); 0 = off")
    ap.add_argument("--mask-post", type=int, default=0,
                    help="with a mask: also drop N days after each masked day, whose prior windows it silenced")
    ap.add_argument("--policy", action="store_true",
                    help="calibrated fixed thresholds recalibrated monthly, High/Medium/Low risk tiers, and both "
                         "policies scored against Annexure 1")
    ap.add_argument("--policy-precision", type=float, default=0.90, help="the High tier's calibration precision")
    ap.add_argument("--policy-recall", type=float, default=0.50, help="the Medium tier's calibration recall")
    ap.add_argument("--policy-cal-days", type=int, default=30, help="matured days each monthly cut is set on")
    a = ap.parse_args()
    t0 = time.time()

    base, meta, feats, target, df = load_checkpoint(a.fleet, a.ckpt, a.target)
    if a.target:
        log(f"target: label variant {target} (headline label {meta['TARGET']} not used)")
    _sig = meta.get("PS1_LABEL_SIGNATURE") or {}
    if "PS1_LABEL_HORIZON_DAYS" in _sig:  # '' is the notebook default of 3
        _ckpt_h = int(_sig.get("PS1_LABEL_HORIZON_DAYS") or 3)
        if (a.refit_monthly or a.device_prior) and a.horizon < _ckpt_h:
            raise SystemExit(f"--horizon {a.horizon} is shorter than the checkpoint's {_ckpt_h}-day label: "
                             f"lagged labels would not have matured; pass --horizon {_ckpt_h}")
    pats = [x.strip() for x in a.drop.split(",") if x.strip()] + DROP_PRESETS.get(a.drop_preset, [])
    dropped = [f for f in feats if any(f == p or (p.endswith("*") and f.startswith(p[:-1])) for p in pats)]
    if dropped:
        feats = [f for f in feats if f not in dropped]
        log(f"withheld {len(dropped)} features ({a.drop_preset or 'custom'}): {dropped}")
    log(f"{a.fleet}: {len(df):,} rows, {len(feats)} features, splits "
        + str(df["split"].value_counts().to_dict()))
    mask_days = parse_date_ranges(a.exclude_dates) if a.exclude_dates else []
    if a.mask_thin > 0:
        # per split, so the embargo gaps between splits never read as an outage
        thin = sorted(set().union(*[thin_days(df.loc[df["split"] == sp, "event_date"], a.mask_thin)[0]
                                    for sp in df["split"].unique()]))
        log(f"thin days (< {a.mask_thin:.0%} of the 31-day median row count): {ranges_text(thin)}")
        mask_days = sorted(set(mask_days) | set(thin))
    if mask_days:
        mk = mask_rows(df["event_date"], mask_days, a.horizon, a.mask_post)
        log(f"data-quality mask: {ranges_text(mask_days)}; extended {a.horizon}d before and {a.mask_post}d after "
            f"each day; removed {int(mk.sum()):,} rows " + str(df.loc[mk, "split"].value_counts().to_dict()))
        df = df.loc[~mk].reset_index(drop=True)
    if a.device_prior:
        if "DEVICE_ID" not in df.columns:
            raise SystemExit("--device-prior needs DEVICE_ID in the checkpoint")
        p0 = add_device_prior(df, target, a.horizon)
        feats = feats + ["dev_prior_pos_rate"]
        log(f"device prior added (lag {a.horizon + 1}d, shrunk toward train rate {p0:.3%})")
    dev = df[df["split"].isin(["train", "val"])]
    if a.warmup_end:
        n0 = len(dev)
        dev = dev[dev["event_date"] >= pd.Timestamp(a.warmup_end)]
        log(f"warm-up rows before {a.warmup_end} dropped from training: {n0 - len(dev):,}")
    test = df[df["split"] == "test"]
    if a.test_start:
        n0 = len(test)
        test = test[test["event_date"] >= pd.Timestamp(a.test_start)]
        log(f"test-start {a.test_start}: scoring {len(test):,} of {n0:,} test rows")
    if a.train_start:
        n0 = len(dev)
        dev = dev[dev["event_date"] >= pd.Timestamp(a.train_start)]
        log(f"train-start {a.train_start}: dropped {n0 - len(dev):,} earlier dev rows")
    dev = dev.sort_values("event_date").reset_index(drop=True)
    rel = []
    if a.relative_features > 0:
        # picked on dev rows only; ranked within each day of dev, of test, and of df's test split
        # (the --refit-monthly pool) separately -- a day never spans dev and test
        # chosen on dev rows before the first CV validation block (minus the embargo), so the choice
        # of rel_ columns never sees a validation label
        _u = np.sort(dev["event_date"].unique())
        _v0 = pd.Timestamp(_u[int(len(_u) * (1 - a.recent_share))]) - pd.Timedelta(days=a.embargo_days)
        _sel = dev[dev["event_date"] < _v0]
        # a feature equal for every device on a day ranks as a tie and would encode the day's size
        _vary = [f for f in feats if (_sel.groupby("event_date")[f].nunique(dropna=True) > 1).mean() > 0.5]
        top = leak_scan(_sel[_vary].to_numpy(np.float32), _sel[target].to_numpy(), _vary)[:a.relative_features]
        log(f"relative features chosen on dev rows before {_v0:%Y-%m-%d} ({len(_sel):,} rows, "
            f"{len(_vary)} features vary within a day)")
        rel = [f for _, f, _ in top]
        dev = dev.assign(**add_relative(dev, rel, "event_date"))
        test = test.assign(**add_relative(test, rel, "event_date"))
        for c, v in add_relative(df, rel, [df["split"].eq("test"), "event_date"]).items():
            df[c] = v
        feats = feats + ["rel_" + f for f in rel]
        log(f"relative features: within-day percentile rank of the top {len(rel)} by solo dev AUC -- "
            + ", ".join(f"{f} {auc_:.3f}" for auc_, f, _ in top))
    Xd, yd = dev[feats].to_numpy(np.float32), dev[target].to_numpy()
    Wd = None
    if a.recency_halflife > 0:
        _age = (dev["event_date"].max() - dev["event_date"]).dt.days.to_numpy()
        Wd = np.power(0.5, _age / a.recency_halflife).astype(np.float64)
        log(f"recency weights: half-life {a.recency_halflife:g} days; oldest row weighs {Wd.min():.3f}")
    Xt, yt = test[feats].to_numpy(np.float32), test[target].to_numpy()
    log(f"dev {len(dev):,} rows ({yd.mean():.3%} positive) | test {len(test):,} rows ({yt.mean():.3%} positive)")

    if not a.no_scan:
        scan = leak_scan(Xd, yd, feats)
        print("\nLEAK SCAN -- solo AUC per feature on the dev period (alarm above 0.85)")
        for auc_, f, cov in scan[:8]:
            print(f"  {f:40s} {auc_:.3f}   coverage {cov:.0%}" + ("   <-- INSPECT" if auc_ > 0.85 else ""))
        adv_auc, adv_imp = adversarial(Xd, Xt, feats, a.threads)
        print(f"DRIFT -- a model separating dev from test scores AUC {adv_auc:.3f} "
              f"(0.5 = alike); features that changed most:")
        print("  " + ", ".join(f for _, f in adv_imp))
        print()

    folds = walk_forward_folds(dev["event_date"], a.folds, a.embargo_days, a.recent_share)
    for tr, va, s, e in folds:
        log(f"  fold: train {len(tr):,} rows | {a.embargo_days}d embargo | validate {s}..{e} "
            f"{len(va):,} rows ({yd[va].mean():.2%} positive)")

    diag = {}
    if not a.no_diag:
        diag = diagnostics(dev, test, feats, Xd, yd, Xt, folds, a.threads)

    tune_folds = folds
    if a.tune_frac < 1.0 and "DEVICE_ID" in dev.columns:
        keep = (pd.util.hash_pandas_object(dev["DEVICE_ID"], index=False) % 1000
                < a.tune_frac * 1000).to_numpy()
        tune_folds = [(tr[keep[tr]], va[keep[va]], s, e) for tr, va, s, e in folds]
        log(f"tuning on a {a.tune_frac:.0%} device sample; OOF and final fits use every device")

    trials = dict(kv.split("=") for kv in a.trials.split(","))
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    best, oofs, iters = {}, {}, {}
    if a.reg == "strong":
        def _bound(k, lo, hi):
            return f"{k}>={lo}" if hi is None else f"{k}<={hi}" if lo is None else f"{k} {lo}..{hi}"
        log("regularised search (--reg strong): " + "; ".join(
            f"{m} " + " ".join(_bound(k, lo, hi) for k, (lo, hi) in REG_STRONG[m].items())
            for m in models if m in REG_STRONG))
    Dd = dev["event_date"].to_numpy()  # the ranker's query key
    for name in models:
        log(f"tuning {name} ...")
        best[name] = tune(name, Xd, yd, tune_folds, int(trials.get(name, 10)), a.threads, a.budget_min, W=Wd,
                          reg=a.reg, D=Dd)
        oofs[name], iters[name] = oof(name, best[name], Xd, yd, folds, a.threads, W=Wd, D=Dd)

    # ---- choose on CV only --------------------------------------------------
    cv = {n: [roc_auc_score(yd[va], o) for (tr, va, _, _), o in zip(folds, oofs[n])] for n in models}
    if len(models) > 1:
        cv["ensemble"] = [roc_auc_score(yd[va], rank_mean([oofs[n][k] for n in models]))
                          for k, (tr, va, _, _) in enumerate(folds)]
    meta = None
    if a.stack and (len(models) < 2 or len(folds) < 2):
        log("--stack needs two or more models and two or more folds; skipped")
    elif a.stack:
        # forward-chained like the folds themselves: fold k's meta-model is fitted on the OOF rows
        # of folds before k only, so its weights never encode later periods; fold 0 has no earlier
        # fold and keeps the ensemble's equal-weight order
        Z = [pct_ranks([oofs[n][k] for n in models]) for k in range(len(folds))]
        yz = [yd[va] for _, va, _, _ in folds]
        oofs["stack"] = [rank_mean([oofs[n][0] for n in models])]
        for k in range(1, len(Z)):
            m_k = fit_stack(np.vstack(Z[:k]), np.concatenate(yz[:k]))
            oofs["stack"].append(m_k.predict_proba(Z[k])[:, 1])
        cv["stack"] = [roc_auc_score(yz[k], s) for k, s in enumerate(oofs["stack"])]
        meta = fit_stack(np.vstack(Z), np.concatenate(yz))  # the test meta-model: every OOF row
    print("\nWALK-FORWARD CV (train+val period only; test untouched)")
    for n, v in cv.items():
        print(f"  {n:10s} " + "  ".join(f"{x:.4f}" for x in v) + f"   mean {np.mean(v):.4f} +/- {np.std(v):.4f}")
    if meta is not None:
        print("  stack = logistic regression on within-fold percentile ranks; coefficients (all OOF rows): "
              + "  ".join(f"{n} {c:+.3f}" for n, c in zip(models, meta.coef_[0]))
              + f"  intercept {meta.intercept_[0]:+.3f}")
    chosen = max(cv, key=lambda n: np.mean(cv[n]))
    if chosen == "stack" and "ensemble" in cv and np.mean(cv["stack"]) - np.mean(cv["ensemble"]) < 0.005:
        # the stack adds fitted weights over the ensemble; it has to earn them by the usual margin
        chosen = "ensemble"

    # ---- final fits on the whole dev period, scored once on test -----------
    test_scores, train_scores, rounds = {}, {}, {}
    for name in models:
        n_est = rounds[name] = int(np.mean(iters[name]) * 1.1) + 1
        log(f"final {name}: {n_est} rounds on {len(dev):,} rows")
        m, _ = FIT[name](best[name], Xd, yd, None, None, a.threads, n_estimators=n_est, wtr=Wd, dtr=Dd)
        test_scores[name] = proba(m, Xt)
        train_scores[name] = proba(m, Xd)
        if name == "lgb":
            imp = pd.Series(m.booster_.feature_importance("gain"), index=feats).sort_values(ascending=False)
            top_imp = imp.head(15)
    if len(models) > 1:
        test_scores["ensemble"] = rank_mean([test_scores[n] for n in models])
        train_scores["ensemble"] = rank_mean([train_scores[n] for n in models])
    if meta is not None:  # members ranked within test (within dev for the overfit check), as the ensemble
        test_scores["stack"] = stack_score(meta, [test_scores[n] for n in models])
        train_scores["stack"] = stack_score(meta, [train_scores[n] for n in models])

    lines, res = [], {}
    for n, s in test_scores.items():
        tag = " <- CV-SELECTED" if n == chosen else ""
        res[n] = report(n + tag, yt, s, test["event_date"].reset_index(drop=True), lines)

    spark_eval = os.path.join(base, "spark_eval", "test_pdf.parquet")
    if os.path.exists(spark_eval):
        sp = pd.read_parquet(spark_eval)
        if "score" in sp.columns and target in sp.columns:
            res["spark_champion"] = report("spark champion (CELL 13)", sp[target].astype(int).to_numpy(),
                                           sp["score"].astype(float).to_numpy(),
                                           pd.to_datetime(sp["event_date"]) if "event_date" in sp else None,
                                           lines)

    print(f"\nTEST {test['event_date'].min().date()}..{test['event_date'].max().date()}  "
          f"base rate {yt.mean():.4f}  (scored once)")
    for ln in lines:
        print("  " + ln)
    print(f"\nQUOTE: {chosen}  test AUC {res[chosen]['auc']:.4f}  (chosen on CV, not on test)")
    _lo, _hi = block_bootstrap_auc(yt, test_scores[chosen], test["event_date"].to_numpy())
    _mon = res[chosen].get("monthly") or {}
    if _mon:
        print("  monthly test AUC: " + "  ".join(f"{m} {v:.3f}" for m, v in sorted(_mon.items())))
    _tr_auc = roc_auc_score(yd, train_scores[chosen])
    _cv = float(np.mean(cv[chosen]))
    print(f"  95% CI (day-block bootstrap, 200 resamples): {_lo:.4f} - {_hi:.4f}")
    print(f"  overfit check: train AUC {_tr_auc:.4f} vs CV {_cv:.4f} vs test {res[chosen]['auc']:.4f}"
          + ("   <-- large train-CV gap" if _tr_auc - _cv > 0.08 else "   OK"))
    diag.update({"test_auc_ci95": [_lo, _hi], "train_auc": _tr_auc})

    va_last = folds[-1][1]
    s_cal = (rank_mean([oofs[n][-1] for n in models]) if chosen == "ensemble" else oofs[chosen][-1])
    ops = operating_points(yd[va_last], s_cal, yt, test_scores[chosen])
    print(f"\nOPERATING POINTS -- {chosen}; share to flag set on the last CV fold, applied to test")
    print("  target recall   flagged   recall  precision  accuracy    FPR      F1")
    for r in ops:
        print(f"  {r['target_recall']:>12.0%}   {r['flag_share']:>7.1%}   {r['recall']:>6.3f}  {r['precision']:>9.3f}"
              f"  {r['accuracy']:>8.3f}  {r['fpr']:>6.3f}  {r['f1']:>6.3f}"
              + ("   <- operating point" if r["target_recall"] == 0.5 else ""))
    print(f"  accuracy if nothing is flagged: {1 - yt.mean():.3f}   contract: precision >= 0.90, "
          f"recall >= 0.80, F1 >= 0.80, FPR <= 0.10, accuracy >= 0.90 (0.85)")

    days_t = test["event_date"].to_numpy()
    budgets = budget_metrics(yt, daily_rank(test_scores[chosen], days_t), DAILY_BUDGETS)
    for r in budgets:
        r["meets_fpr_acc"] = bool(r["fpr"] <= 0.10 and r["accuracy"] >= 0.85)
    print(f"\nDAILY BUDGET -- {chosen} on test; each day, flag the top q% of that day's devices by score")
    print("  a daily dispatch budget is a policy choice, not tuned on test; the rows describe each choice")
    print("  budget   flagged   recall  precision  accuracy    FPR      F1")
    def _prec(r):  # nothing flagged: precision is undefined, not zero
        return f"{r['precision']:>9.3f}" if r["precision"] is not None else f"{'-':>9s}"

    for r in budgets:
        print(f"  {r['budget']:>6.0%}   {r['flag_share']:>7.1%}   {r['recall']:>6.3f}  {_prec(r)}"
              f"  {r['accuracy']:>8.3f}  {r['fpr']:>6.3f}  {r['f1']:>6.3f}"
              + ("   <- FPR <= 0.10 and accuracy >= 0.85" if r["meets_fpr_acc"] else ""))

    fs_, fe_ = folds[-1][2], folds[-1][3]
    cops = constrained_ops(yd[va_last], s_cal, dev["event_date"].to_numpy()[va_last], yt, test_scores[chosen], days_t)
    print(f"\nCONSTRAINED OPERATING POINTS -- {chosen}; largest daily budget meeting each clause on the last "
          f"CV fold ({fs_}..{fe_}), applied to test")
    print("  the most recall available under each clause; the budget is never chosen on test")
    print("  clause              budget  fold P  fold R |  flagged   recall  precision  accuracy    FPR      F1")
    for c in cops:
        if not c["reachable"]:
            print(f"  {c['constraint']:18s}  not reachable on the last fold")
            continue
        f_, t_ = c["fold"], c["test"]
        print(f"  {c['constraint']:18s}  {c['budget']:>6.1%}  {f_['precision']:>6.3f}  {f_['recall']:>6.3f} |"
              f"  {t_['flag_share']:>7.1%}   {t_['recall']:>6.3f}  {_prec(t_)}  {t_['accuracy']:>8.3f}"
              f"  {t_['fpr']:>6.3f}  {t_['f1']:>6.3f}")

    blend_names = models if chosen in ("ensemble", "stack") else [chosen]
    ep_t = (episode_ids(test["DEVICE_ID"].to_numpy(), days_t, yt) if "DEVICE_ID" in test.columns
            else np.full(len(yt), -1))
    policy = {}
    if a.policy:
        p_static = policy_score(test_scores, blend_names)
        tier, prow = threshold_policy(yt, p_static, days_t, a.horizon, a.policy_precision, a.policy_recall,
                                      a.policy_cal_days)
        policy["static"] = print_policy(
            f"THRESHOLD POLICY -- {chosen} (static model), probability "
            + ("= mean of member probabilities" if len(blend_names) > 1 else "of the model"),
            yt, tier, prow, ep_t, a.policy_precision, a.policy_recall, a.horizon, a.policy_cal_days)
        if a.fleet == "TVM":
            print("  TVM is scored on all days, so a device's positive rows can run through an open outage; "
                  "episode-recall is looser there than on the in-service fleets")

    refit, s_rf, p_rf = None, None, None
    if a.refit_monthly:
        try:
            # the static figures above score all of test with one model frozen at the end of dev; this
            # measures what monthly retraining recovers of the drift between the two periods
            blend = chosen in ("ensemble", "stack")
            names = models if blend else [chosen]
            # the stack keeps its meta-model fixed (fitted on the OOF rows); only the members are refitted
            combine = (lambda arrs: stack_score(meta, arrs)) if chosen == "stack" else rank_mean
            log(f"refit monthly: {', '.join(names)}, {a.horizon}-day label lag, rounds "
                + ", ".join(f"{n} {rounds[n]}" for n in names))
            s_rf, rf_rows, p_rf = refit_monthly(df, feats, target, dev, Xd, yd, test, Xt, a, names, best, rounds,
                                                combine=combine)
            st = test_scores[chosen]
            # rank the static side within each month exactly as the refit side is ranked; ranks over all
            # of test are a different scale, and the difference would mix that into the retraining effect
            mon_t, st = test["event_date"].dt.to_period("M").astype(str).to_numpy(), np.empty(len(test))
            for m in np.unique(mon_t):
                i = np.flatnonzero(mon_t == m)
                st[i] = combine([test_scores[n][i] for n in names])
            cov = ~np.isnan(s_rf)
            y_c, s_c, st_c = yt[cov], s_rf[cov], st[cov]
            print(f"\nREFIT MONTHLY -- each test month scored by models retrained on data available before it "
                  f"(labels need {a.horizon} days to mature)")
            print(f"  {int(cov.sum()):,} of {len(cov):,} test rows scored; static = the final model above, same rows"
                  + "; refit and static scores both ranked within each month, so the pooled AUC is not the "
                  "QUOTE figure -- read the paired delta and the monthly lines")
            refit = {"model": chosen, "fitted": names, "rounds": {n: rounds[n] for n in names},
                     "rows_scored": int(cov.sum()), "months": rf_rows}
            if cov.sum() == 0:
                print("  no month could be refitted (see the refit log lines) -- nothing to measure")
            elif not 0 < y_c.sum() < len(y_c):
                print("  no scored month holds both classes -- nothing to measure")
            else:
                days_c = days_t[cov]
                d_c = test["event_date"].reset_index(drop=True)[cov].reset_index(drop=True)
                rl = []
                r_rf = report(f"refit {chosen}", y_c, s_c, d_c, rl)
                r_st = report(f"static {chosen}", y_c, st_c, d_c, rl)
                rlo, rhi = block_bootstrap_auc(y_c, s_c, days_c)
                dlo, dhi = block_bootstrap_auc_delta(y_c, s_c, st_c, days_c)
                rb = budget_metrics(y_c, daily_rank(s_c, days_c), DAILY_BUDGETS)
                for r in rb:
                    r["meets_fpr_acc"] = bool(r["fpr"] <= 0.10 and r["accuracy"] >= 0.85)
                for ln in rl:
                    print("  " + ln)
                print(f"  95% CI (day-block bootstrap, 200 resamples): {rlo:.4f} - {rhi:.4f}   refit - static AUC "
                      f"{r_rf['auc'] - r_st['auc']:+.4f} (paired 95% CI {dlo:+.4f} .. {dhi:+.4f})")
                for tag, rr in (("refit ", r_rf), ("static", r_st)):
                    if rr["monthly"]:
                        print(f"  monthly AUC {tag}: "
                              + "  ".join(f"{m} {v:.3f}" for m, v in sorted(rr["monthly"].items())))
                print(f"  daily budget -- {chosen} refit; each day, flag the top q% of that day's devices by score")
                print("  budget   flagged   recall  precision  accuracy    FPR      F1")
                for r in rb:
                    print(f"  {r['budget']:>6.0%}   {r['flag_share']:>7.1%}   {r['recall']:>6.3f}  {_prec(r)}"
                          f"  {r['accuracy']:>8.3f}  {r['fpr']:>6.3f}  {r['f1']:>6.3f}"
                          + ("   <- FPR <= 0.10 and accuracy >= 0.85" if r["meets_fpr_acc"] else ""))
                refit.update({"auc": r_rf["auc"], "ap": r_rf["ap"], "ci95": [rlo, rhi], "monthly": r_rf["monthly"],
                              "static_auc": r_st["auc"], "static_ap": r_st["ap"], "static_monthly": r_st["monthly"],
                              "delta_auc_ci95": [dlo, dhi], "budgets": rb})
                if a.policy:
                    # each month's cuts come from the rows scored in earlier months, as the monthly
                    # retrain scored them at the time
                    tier_r, prow_r = threshold_policy(yt, p_rf, days_t, a.horizon, a.policy_precision,
                                                      a.policy_recall, a.policy_cal_days)
                    policy["refit"] = print_policy(
                        f"THRESHOLD POLICY -- {chosen} refitted monthly (production cadence)",
                        yt, tier_r, prow_r, ep_t, a.policy_precision, a.policy_recall, a.horizon,
                        a.policy_cal_days)
        except Exception as exc:  # the static results above must still be written
            print(f"\nREFIT MONTHLY failed ({type(exc).__name__}: {exc}); the static results are unaffected")
            refit = {"error": f"{type(exc).__name__}: {exc}"}
    if "lgb" in models:
        print("\nLightGBM top-15 gain:")
        for f, v in top_imp.items():
            print(f"  {f:40s} {v:12.0f}")

    out = os.path.join(base, "..", "native_lab", f"{_dt.datetime.now():%Y%m%d_%H%M%S}")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "summary.json"), "w") as fh:
        json.dump({"fleet": a.fleet, "args": vars(a), "cv": cv, "chosen": chosen, "withheld": dropped, "best_params": best,
                   "test": {k: {"auc": v["auc"], "ap": v["ap"], "monthly": v["monthly"]} for k, v in res.items()},
                   "operating_points": ops, "daily_budgets": budgets, "constrained_ops": cops,
                   "diagnostics": diag, **({"refit_monthly": refit} if refit else {}),
                   **({"policy": policy} if policy else {}),
                   **({"target": target} if a.target else {}),
                   **({"masked_days": [str(pd.Timestamp(d).date()) for d in mask_days]} if mask_days else {}),
                   **({"relative_from": rel} if rel else {}),
                   **({"stack_coef": {**dict(zip(models, meta.coef_[0].tolist())),
                                      "intercept": float(meta.intercept_[0])}} if meta is not None else {})},
                  fh, indent=1, default=float)
    pd.DataFrame({"event_date": test["event_date"].to_numpy(), "y": yt, **test_scores,
                  **({f"refit_{chosen}": s_rf} if refit else {})}).to_parquet(
        os.path.join(out, "test_scores.parquet"), index=False)
    log(f"written {os.path.normpath(out)}  ({(time.time() - t0) / 60:.1f} min)")


if __name__ == "__main__":
    main()
