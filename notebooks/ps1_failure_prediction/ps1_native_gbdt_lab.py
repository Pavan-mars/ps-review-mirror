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
               "met_comms_*", "evq_dur_*"],
}


def log(msg):
    print(f"[{_dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


# --------------------------------------------------------------------------- data
def load_checkpoint(fleet, ckpt_subdir):
    import pyarrow.dataset as ds

    base = os.path.join(HERE, f"ps1_{fleet.lower()}_oos_outputs", ckpt_subdir)
    meta = joblib.load(os.path.join(base, "spark_ckpt_meta.joblib"))
    feats, target = list(meta["FEATURE_COLS"]), meta["TARGET"]
    dset = ds.dataset(os.path.join(base, "spark_splits"), format="parquet")
    names = set(dset.schema.names)
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


# --------------------------------------------------------------------------- models
def fit_lgb(p, Xtr, ytr, Xva, yva, threads, n_estimators=None):
    import lightgbm as lgb

    m = lgb.LGBMClassifier(
        n_estimators=n_estimators or 2000, learning_rate=p["lr"], num_leaves=p["leaves"],
        min_child_samples=p["mcs"], subsample=p["ss"], subsample_freq=1,
        colsample_bytree=p["cs"], reg_alpha=p["ra"], reg_lambda=p["rl"],
        n_jobs=threads, verbose=-1, random_state=42)
    if n_estimators:
        m.fit(Xtr, ytr)
        return m, n_estimators
    m.fit(Xtr, ytr, eval_set=[(Xva, yva)], eval_metric="auc",
          callbacks=[lgb.early_stopping(100, verbose=False)])
    return m, int(m.best_iteration_ or m.n_estimators)


def fit_xgb(p, Xtr, ytr, Xva, yva, threads, n_estimators=None):
    import xgboost as xgb

    kw = dict(n_estimators=n_estimators or 2000, learning_rate=p["lr"], max_depth=p["depth"],
              min_child_weight=p["mcw"], subsample=p["ss"], colsample_bytree=p["cs"],
              reg_alpha=p["ra"], reg_lambda=p["rl"], tree_method="hist",
              eval_metric="auc", n_jobs=threads, random_state=42)
    if n_estimators:
        m = xgb.XGBClassifier(**kw)
        m.fit(Xtr, ytr, verbose=False)
        return m, n_estimators
    m = xgb.XGBClassifier(early_stopping_rounds=100, **kw)
    m.fit(Xtr, ytr, eval_set=[(Xva, yva)], verbose=False)
    return m, int(m.best_iteration + 1)


def fit_cat(p, Xtr, ytr, Xva, yva, threads, n_estimators=None):
    from catboost import CatBoostClassifier

    kw = dict(iterations=n_estimators or 2000, learning_rate=p["lr"], depth=p["depth"],
              l2_leaf_reg=p["l2"], eval_metric="AUC", thread_count=threads,
              verbose=False, allow_writing_files=False, random_seed=42)
    if n_estimators:
        m = CatBoostClassifier(**kw)
        m.fit(Xtr, ytr)
        return m, n_estimators
    m = CatBoostClassifier(od_type="Iter", od_wait=100, **kw)
    m.fit(Xtr, ytr, eval_set=(Xva, yva), use_best_model=True)
    return m, int(m.get_best_iteration() + 1)


def space(trial, name):
    if name == "lgb":
        return {"lr": trial.suggest_float("lr", 0.02, 0.1, log=True),
                "leaves": trial.suggest_int("leaves", 15, 255, log=True),
                "mcs": trial.suggest_int("mcs", 20, 2000, log=True),
                "ss": trial.suggest_float("ss", 0.5, 1.0),
                "cs": trial.suggest_float("cs", 0.3, 1.0),
                "ra": trial.suggest_float("ra", 1e-3, 10, log=True),
                "rl": trial.suggest_float("rl", 1e-3, 10, log=True)}
    if name == "xgb":
        return {"lr": trial.suggest_float("lr", 0.02, 0.1, log=True),
                "depth": trial.suggest_int("depth", 3, 10),
                "mcw": trial.suggest_float("mcw", 1, 200, log=True),
                "ss": trial.suggest_float("ss", 0.5, 1.0),
                "cs": trial.suggest_float("cs", 0.3, 1.0),
                "ra": trial.suggest_float("ra", 1e-3, 10, log=True),
                "rl": trial.suggest_float("rl", 1e-3, 10, log=True)}
    return {"lr": trial.suggest_float("lr", 0.03, 0.15, log=True),
            "depth": trial.suggest_int("depth", 4, 8),
            "l2": trial.suggest_float("l2", 1, 30, log=True)}


FIT = {"lgb": fit_lgb, "xgb": fit_xgb, "cat": fit_cat}


def proba(m, X):
    return m.predict_proba(X)[:, 1]


def tune(name, X, y, folds, trials, threads, budget_min):
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial):
        p, aucs = space(trial, name), []
        for k, (tr, va, _, _) in enumerate(folds):
            m, _ = FIT[name](p, X[tr], y[tr], X[va], y[va], threads)
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


def oof(name, p, X, y, folds, threads):
    """Refit the chosen parameters on every fold; return per-fold scores and best iterations."""
    out, iters = [], []
    for tr, va, _, _ in folds:
        m, it = FIT[name](p, X[tr], y[tr], X[va], y[va], threads)
        out.append(proba(m, X[va]))
        iters.append(it)
    return out, iters


def rank_mean(arrs):
    return np.mean([rankdata(a) / len(a) for a in arrs], axis=0)


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


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet", required=True, choices=["GATE", "TVM", "VALIDATOR"])
    ap.add_argument("--ckpt", default="checkpoints", help="checkpoint folder under the fleet's outputs")
    ap.add_argument("--models", default="lgb,xgb,cat")
    ap.add_argument("--trials", default="lgb=25,xgb=15,cat=6")
    ap.add_argument("--budget-min", type=int, default=20, help="per-model tuning time cap")
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--recent-share", type=float, default=0.4)
    ap.add_argument("--embargo-days", type=int, default=14, help="matches CELL 9")
    ap.add_argument("--warmup-end", default="2023-08-01",
                    help="drop training rows before this date (LEFT-CENSORED warm-up); '' keeps them")
    ap.add_argument("--tune-frac", type=float, default=1.0, help="device sample used for tuning only")
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--drop", default="", help="comma list of features to withhold; name* = prefix")
    ap.add_argument("--drop-preset", default="", choices=["", "honest"])
    a = ap.parse_args()
    t0 = time.time()

    base, meta, feats, target, df = load_checkpoint(a.fleet, a.ckpt)
    pats = [x.strip() for x in a.drop.split(",") if x.strip()] + DROP_PRESETS.get(a.drop_preset, [])
    dropped = [f for f in feats if any(f == p or (p.endswith("*") and f.startswith(p[:-1])) for p in pats)]
    if dropped:
        feats = [f for f in feats if f not in dropped]
        log(f"withheld {len(dropped)} features ({a.drop_preset or 'custom'}): {dropped}")
    log(f"{a.fleet}: {len(df):,} rows, {len(feats)} features, splits "
        + str(df["split"].value_counts().to_dict()))
    dev = df[df["split"].isin(["train", "val"])]
    if a.warmup_end:
        n0 = len(dev)
        dev = dev[dev["event_date"] >= pd.Timestamp(a.warmup_end)]
        log(f"warm-up rows before {a.warmup_end} dropped from training: {n0 - len(dev):,}")
    test = df[df["split"] == "test"]
    dev = dev.sort_values("event_date").reset_index(drop=True)
    Xd, yd = dev[feats].to_numpy(np.float32), dev[target].to_numpy()
    Xt, yt = test[feats].to_numpy(np.float32), test[target].to_numpy()
    log(f"dev {len(dev):,} rows ({yd.mean():.3%} positive) | test {len(test):,} rows ({yt.mean():.3%} positive)")

    folds = walk_forward_folds(dev["event_date"], a.folds, a.embargo_days, a.recent_share)
    for tr, va, s, e in folds:
        log(f"  fold: train {len(tr):,} rows | {a.embargo_days}d embargo | validate {s}..{e} "
            f"{len(va):,} rows ({yd[va].mean():.2%} positive)")

    tune_folds = folds
    if a.tune_frac < 1.0 and "DEVICE_ID" in dev.columns:
        keep = (pd.util.hash_pandas_object(dev["DEVICE_ID"], index=False) % 1000
                < a.tune_frac * 1000).to_numpy()
        tune_folds = [(tr[keep[tr]], va[keep[va]], s, e) for tr, va, s, e in folds]
        log(f"tuning on a {a.tune_frac:.0%} device sample; OOF and final fits use every device")

    trials = dict(kv.split("=") for kv in a.trials.split(","))
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    best, oofs, iters = {}, {}, {}
    for name in models:
        log(f"tuning {name} ...")
        best[name] = tune(name, Xd, yd, tune_folds, int(trials.get(name, 10)), a.threads, a.budget_min)
        oofs[name], iters[name] = oof(name, best[name], Xd, yd, folds, a.threads)

    # ---- choose on CV only --------------------------------------------------
    cv = {n: [roc_auc_score(yd[va], o) for (tr, va, _, _), o in zip(folds, oofs[n])] for n in models}
    if len(models) > 1:
        cv["ensemble"] = [roc_auc_score(yd[va], rank_mean([oofs[n][k] for n in models]))
                          for k, (tr, va, _, _) in enumerate(folds)]
    print("\nWALK-FORWARD CV (train+val period only; test untouched)")
    for n, v in cv.items():
        print(f"  {n:10s} " + "  ".join(f"{x:.4f}" for x in v) + f"   mean {np.mean(v):.4f} +/- {np.std(v):.4f}")
    chosen = max(cv, key=lambda n: np.mean(cv[n]))

    # ---- final fits on the whole dev period, scored once on test -----------
    test_scores = {}
    for name in models:
        n_est = int(np.mean(iters[name]) * 1.1) + 1
        log(f"final {name}: {n_est} rounds on {len(dev):,} rows")
        m, _ = FIT[name](best[name], Xd, yd, None, None, a.threads, n_estimators=n_est)
        test_scores[name] = proba(m, Xt)
        if name == "lgb":
            imp = pd.Series(m.booster_.feature_importance("gain"), index=feats).sort_values(ascending=False)
            top_imp = imp.head(15)
    if len(models) > 1:
        test_scores["ensemble"] = rank_mean([test_scores[n] for n in models])

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
    if "lgb" in models:
        print("\nLightGBM top-15 gain:")
        for f, v in top_imp.items():
            print(f"  {f:40s} {v:12.0f}")

    out = os.path.join(base, "..", "native_lab", f"{_dt.datetime.now():%Y%m%d_%H%M%S}")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "summary.json"), "w") as fh:
        json.dump({"fleet": a.fleet, "args": vars(a), "cv": cv, "chosen": chosen, "withheld": dropped, "best_params": best,
                   "test": {k: {"auc": v["auc"], "ap": v["ap"], "monthly": v["monthly"]} for k, v in res.items()}},
                  fh, indent=1, default=float)
    pd.DataFrame({"event_date": test["event_date"].to_numpy(), "y": yt, **test_scores}).to_parquet(
        os.path.join(out, "test_scores.parquet"), index=False)
    log(f"written {os.path.normpath(out)}  ({(time.time() - t0) / 60:.1f} min)")


if __name__ == "__main__":
    main()
