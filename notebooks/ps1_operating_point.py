"""PS1 operating point: calibration, threshold selection, risk tiers, frontier.

Kept out of the notebooks so the same rules apply to every fleet, and so it can be
run against scores that already exist -- no retraining required.

Why this module exists
----------------------
The notebooks pick their operating threshold by maximising F2 on validation. F2
weights recall four times precision, which was defensible against the old state
label (GATE base rate 73%) and is wrong against an onset label (GATE 11.6%): it
drives the cut down until nearly everything is flagged. On the 17-Sep GATE onset
run it chose a point with accuracy 55.7% and precision 0.17, while the SAME model
at the top 5% of its own ranking gives accuracy 88.3% and precision 0.49.

Nothing here changes a model. It changes where the line is drawn, how the scores
are calibrated before drawing it, and what the risk tiers mean.

Everything is opt-in via environment variables, so an unchanged run behaves
exactly as it did before:

    PS1_CALIBRATION          none | isotonic | sigmoid       (default none)
    PS1_THRESHOLD_POLICY     f2 | f1 | budget | precision_floor | youden
                                                             (default f2, legacy)
    PS1_DISPATCH_BUDGET_PCT  percent of rows flagged          (default 5.0)
    PS1_PRECISION_FLOOR      float in (0,1)                   (default 0.40)
    PS1_TIER_POLICY          absolute | budget                (default absolute)
"""
from __future__ import annotations

import os

import numpy as np

__all__ = [
    "expected_calibration_error",
    "fit_calibrator",
    "frontier",
    "select_threshold",
    "tier_cuts",
    "assign_tiers",
    "tier_report",
    "operating_point_report",
    "format_report",
]


# --------------------------------------------------------------------------- #
# calibration
# --------------------------------------------------------------------------- #

def expected_calibration_error(y_true, p, n_bins: int = 10) -> float:
    """Binned |confidence - observed rate|, weighted by bin population.

    Empty bins contribute nothing rather than counting as perfect, which keeps
    ECE comparable across models whose scores occupy different ranges.
    """
    y_true = np.asarray(y_true, dtype=float)
    p = np.asarray(p, dtype=float)
    if y_true.size == 0:
        return float("nan")
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    ece = 0.0
    for b in range(n_bins):
        m = idx == b
        n = int(m.sum())
        if n == 0:
            continue
        ece += (n / y_true.size) * abs(p[m].mean() - y_true[m].mean())
    return float(ece)


def fit_calibrator(y_val, s_val, method: str | None = None):
    """Fit a probability calibrator on VALIDATION scores.

    Must be validation, never test: fitting on test and then reporting test
    calibration measures nothing. Returns a callable mapping score -> [0,1].

    isotonic  non-parametric, monotonic. Preferred when validation is large
              enough -- it can fix the severe over-confidence on the GATE onset
              run (ECE 0.41).
    sigmoid   Platt scaling, two parameters. Safer on small or sparse validation
              sets because it cannot chase noise the way isotonic can.
    """
    method = (method or os.environ.get("PS1_CALIBRATION", "none")).strip().lower()
    if method in ("none", "", "off"):
        return lambda s: np.asarray(s, dtype=float)

    y_val = np.asarray(y_val, dtype=int)
    s_val = np.asarray(s_val, dtype=float)
    n_pos = int(y_val.sum())
    if n_pos < 10 or y_val.size < 50:
        raise ValueError(
            "refusing to calibrate on %d rows / %d positives -- too few to fit a "
            "reliable mapping; use PS1_CALIBRATION=none" % (y_val.size, n_pos)
        )

    if method == "isotonic":
        from sklearn.isotonic import IsotonicRegression

        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(s_val, y_val)
        return lambda s: np.clip(iso.predict(np.asarray(s, dtype=float)), 0.0, 1.0)

    if method == "sigmoid":
        from sklearn.linear_model import LogisticRegression

        lr = LogisticRegression(C=1e10, solver="lbfgs")
        lr.fit(s_val.reshape(-1, 1), y_val)
        return lambda s: lr.predict_proba(
            np.asarray(s, dtype=float).reshape(-1, 1))[:, 1]

    raise ValueError("unknown PS1_CALIBRATION=%r; expected none|isotonic|sigmoid" % method)


# --------------------------------------------------------------------------- #
# the achievable frontier
# --------------------------------------------------------------------------- #

def _sweep(y, s):
    """Every distinct cut in one pass, aligned by cut index."""
    y = np.asarray(y, dtype=int)
    s = np.asarray(s, dtype=float)
    order = np.argsort(-s, kind="mergesort")
    ys = y[order]
    tp = np.cumsum(ys)
    k = np.arange(1, y.size + 1)
    fp = k - tp
    P = int(y.sum())
    N = y.size
    fn = P - tp
    tn = (N - P) - fp
    with np.errstate(divide="ignore", invalid="ignore"):
        prec = np.where(k > 0, tp / k, 0.0)
        rec = np.where(P > 0, tp / P, 0.0)
        f1 = np.where(prec + rec > 0, 2 * prec * rec / (prec + rec), 0.0)
    acc = (tp + tn) / N
    return dict(k=k, thr=s[order], tp=tp, fp=fp, fn=fn, tn=tn,
                prec=prec, rec=rec, f1=f1, acc=acc, P=P, N=N)


def frontier(y, s, ks=(0.01, 0.02, 0.05, 0.10, 0.25)):
    """What this data supports, before anyone commits to a number.

    Precision/recall/F1/accuracy at each top-K cut, the true maxima found by
    sweeping every threshold, and the two trivial baselines. The always-negative
    accuracy is the figure any accuracy target must beat: at an 11.6% base rate
    it is already 88.4%, so a 90% target asks for 1.6 points over a model that
    predicts nothing at all.
    """
    sw = _sweep(y, s)
    P, N = sw["P"], sw["N"]
    base = P / N if N else float("nan")
    rows = []
    for kf in ks:
        i = max(0, min(N - 1, int(round(kf * N)) - 1))
        rows.append(dict(
            cut="top %.0f%%" % (kf * 100), n_flagged=int(sw["k"][i]),
            threshold=float(sw["thr"][i]), precision=float(sw["prec"][i]),
            recall=float(sw["rec"][i]), f1=float(sw["f1"][i]),
            accuracy=float(sw["acc"][i]), lift=float(sw["prec"][i] / base)))
    ia, jf = int(np.argmax(sw["acc"])), int(np.argmax(sw["f1"]))
    for lbl, i in (("max accuracy", ia), ("max F1", jf)):
        rows.append(dict(
            cut=lbl, n_flagged=int(sw["k"][i]), threshold=float(sw["thr"][i]),
            precision=float(sw["prec"][i]), recall=float(sw["rec"][i]),
            f1=float(sw["f1"][i]), accuracy=float(sw["acc"][i]),
            lift=float(sw["prec"][i] / base)))
    rows.append(dict(cut="always negative", n_flagged=0, threshold=float("inf"),
                     precision=float("nan"), recall=0.0, f1=0.0,
                     accuracy=float((N - P) / N), lift=float("nan")))
    rows.append(dict(cut="always positive", n_flagged=N, threshold=float("-inf"),
                     precision=base, recall=1.0, f1=float(2 * base / (1 + base)),
                     accuracy=base, lift=1.0))
    return dict(base_rate=base, n=N, positives=P, rows=rows,
                max_accuracy=float(sw["acc"][ia]), max_f1=float(sw["f1"][jf]),
                always_negative_accuracy=float((N - P) / N))


# --------------------------------------------------------------------------- #
# threshold selection
# --------------------------------------------------------------------------- #

def select_threshold(y, s, policy: str | None = None, *, budget_pct=None,
                     precision_floor=None, recall_floor=None):
    """Choose the cut by an explicit, stated policy rather than a metric maximum.

    budget           flag the top PS1_DISPATCH_BUDGET_PCT of rows. The most
                     defensible default for maintenance: the list is as long as
                     the team can actually visit, and precision follows from that
                     rather than being traded away by a metric.
    precision_floor  the most permissive cut whose precision still clears the
                     floor -- maximise coverage subject to being right often
                     enough to be worth dispatching on.
    f1 / f2          maximise that metric. f2 is the legacy behaviour, kept so
                     existing runs reproduce; it collapses toward flagging
                     everything whenever the base rate is low.
    youden           maximise sensitivity + specificity - 1. Prevalence-free, so
                     it is the fairest single number when base rates differ
                     between fleets.
    """
    policy = (policy or os.environ.get("PS1_THRESHOLD_POLICY", "f2")).strip().lower()
    sw = _sweep(y, s)
    N = sw["N"]

    if policy == "budget":
        pct = budget_pct if budget_pct is not None else float(
            os.environ.get("PS1_DISPATCH_BUDGET_PCT", "5.0"))
        i = max(0, min(N - 1, int(round(pct / 100.0 * N)) - 1))
    elif policy == "precision_floor":
        floor = precision_floor if precision_floor is not None else float(
            os.environ.get("PS1_PRECISION_FLOOR", "0.40"))
        ok = np.where(sw["prec"] >= floor)[0]
        if ok.size == 0:
            raise ValueError(
                "no cut reaches precision %.2f; the best available is %.4f. Lower "
                "PS1_PRECISION_FLOOR, or accept that this label does not support it."
                % (floor, float(sw["prec"].max())))
        i = int(ok[-1])
    elif policy in ("f1", "f2"):
        beta2 = 1.0 if policy == "f1" else 4.0
        with np.errstate(divide="ignore", invalid="ignore"):
            fb = np.where(sw["prec"] + sw["rec"] > 0,
                          (1 + beta2) * sw["prec"] * sw["rec"]
                          / (beta2 * sw["prec"] + sw["rec"]), 0.0)
        i = int(np.argmax(fb))
    elif policy == "youden":
        spec = sw["tn"] / np.maximum(sw["tn"] + sw["fp"], 1)
        i = int(np.argmax(sw["rec"] + spec - 1.0))
    else:
        raise ValueError("unknown PS1_THRESHOLD_POLICY=%r" % policy)

    if recall_floor is not None and sw["rec"][i] < recall_floor:
        ok = np.where(sw["rec"] >= recall_floor)[0]
        if ok.size:
            i = int(ok[0])

    base = sw["P"] / sw["N"]
    return dict(policy=policy, threshold=float(sw["thr"][i]), n_flagged=int(sw["k"][i]),
                flagged_pct=float(sw["k"][i] / N), precision=float(sw["prec"][i]),
                recall=float(sw["rec"][i]), f1=float(sw["f1"][i]),
                accuracy=float(sw["acc"][i]), tp=int(sw["tp"][i]), fp=int(sw["fp"][i]),
                fn=int(sw["fn"][i]), tn=int(sw["tn"][i]),
                lift=float(sw["prec"][i] / base) if base else float("nan"))


# --------------------------------------------------------------------------- #
# risk tiers
# --------------------------------------------------------------------------- #

DEFAULT_TIER_CUTS = {"CRITICAL": 0.50, "HIGH": 0.25, "MEDIUM": 0.10}


def tier_cuts(p=None, policy: str | None = None, cuts: dict | None = None):
    """Boundaries for CRITICAL / HIGH / MEDIUM / LOW.

    absolute (default) fixed probability bands, which mean something only once
        the scores are calibrated: CRITICAL = more likely than not to fail within
        the horizon; HIGH = at least one in four; MEDIUM = around or above the
        fleet base rate; LOW = below it. A tier then carries a claim a technician
        can check, rather than being a quantile of whatever the model emitted.
    budget  quantile bands (top 1 / 5 / 20 percent). Use when the scores are not
        calibrated, or when tier sizes must stay fixed regardless of fleet health.
    """
    policy = (policy or os.environ.get("PS1_TIER_POLICY", "absolute")).strip().lower()
    if cuts:
        return dict(cuts)
    if policy == "absolute":
        return dict(DEFAULT_TIER_CUTS)
    if policy == "budget":
        if p is None:
            raise ValueError("budget tiering needs the score array")
        p = np.asarray(p, dtype=float)
        return {"CRITICAL": float(np.quantile(p, 0.99)),
                "HIGH": float(np.quantile(p, 0.95)),
                "MEDIUM": float(np.quantile(p, 0.80))}
    raise ValueError("unknown PS1_TIER_POLICY=%r" % policy)


def assign_tiers(p, cuts: dict | None = None):
    p = np.asarray(p, dtype=float)
    c = cuts or tier_cuts(p)
    out = np.full(p.shape, "LOW", dtype=object)
    out[p >= c["MEDIUM"]] = "MEDIUM"
    out[p >= c["HIGH"]] = "HIGH"
    out[p >= c["CRITICAL"]] = "CRITICAL"
    return out


def tier_report(y, p, cuts: dict | None = None):
    """Population and OBSERVED failure rate per tier -- the honesty check.

    If CRITICAL does not empirically contain mostly failures, the tier is lying
    whatever the cut says.
    """
    y = np.asarray(y, dtype=int)
    t = assign_tiers(p, cuts)
    rows = []
    for name in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        m = t == name
        n = int(m.sum())
        rows.append(dict(tier=name, n=n,
                         share=float(n / y.size) if y.size else 0.0,
                         observed_rate=float(y[m].mean()) if n else float("nan")))
    return rows


# --------------------------------------------------------------------------- #
# one call that does all of it
# --------------------------------------------------------------------------- #

def operating_point_report(y_val, s_val, y_test, s_test, *, fleet: str = "", **kw):
    """Calibrate on validation, then report frontier, cut and tiers on test."""
    y_val = np.asarray(y_val, dtype=int)
    y_test = np.asarray(y_test, dtype=int)
    cal = fit_calibrator(y_val, s_val, kw.get("calibration"))
    p_val, p_test = cal(s_val), cal(s_test)
    fr = frontier(y_test, p_test)
    sel = select_threshold(y_val, p_val, kw.get("policy"),
                           budget_pct=kw.get("budget_pct"),
                           precision_floor=kw.get("precision_floor"),
                           recall_floor=kw.get("recall_floor"))
    thr = sel["threshold"]
    yhat = (p_test >= thr).astype(int)
    tp = int(((yhat == 1) & (y_test == 1)).sum())
    fp = int(((yhat == 1) & (y_test == 0)).sum())
    fn = int(((yhat == 0) & (y_test == 1)).sum())
    tn = int(((yhat == 0) & (y_test == 0)).sum())
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    f1 = (2 * prec * rec / (prec + rec)) if (prec == prec and rec == rec and prec + rec) else 0.0
    cuts = tier_cuts(p_test, kw.get("tier_policy"))
    return dict(
        fleet=fleet,
        calibrator=(kw.get("calibration") or os.environ.get("PS1_CALIBRATION", "none")),
        ece_before=expected_calibration_error(y_test, s_test),
        ece_after=expected_calibration_error(y_test, p_test),
        frontier=fr, selection=sel, tier_cuts=cuts,
        tiers=tier_report(y_test, p_test, cuts),
        test_at_threshold=dict(
            threshold=thr, tp=tp, fp=fp, fn=fn, tn=tn, precision=prec, recall=rec,
            f1=f1, accuracy=(tp + tn) / y_test.size,
            flagged_pct=float((tp + fp) / y_test.size),
            lift=(prec / fr["base_rate"]) if prec == prec else float("nan")),
    )


def format_report(rep) -> str:
    """Printable form of operating_point_report."""
    fr, sel, at = rep["frontier"], rep["selection"], rep["test_at_threshold"]
    L = []
    L.append("=" * 78)
    L.append("PS1 OPERATING POINT -- %s" % (rep["fleet"] or "fleet"))
    L.append("=" * 78)
    L.append("test rows %d   positives %d   base rate %.4f%%"
             % (fr["n"], fr["positives"], 100 * fr["base_rate"]))
    L.append("calibration %s   ECE %.4f -> %.4f"
             % (rep["calibrator"], rep["ece_before"], rep["ece_after"]))
    L.append("")
    L.append("ACHIEVABLE FRONTIER (what this data supports)")
    L.append("  %-16s %9s %10s %9s %8s %10s %8s"
             % ("cut", "flagged", "precision", "recall", "F1", "accuracy", "lift"))
    for r in fr["rows"]:
        L.append("  %-16s %9d %10.4f %9.4f %8.4f %9.4f%% %7.2fx"
                 % (r["cut"], r["n_flagged"], r["precision"], r["recall"],
                    r["f1"], 100 * r["accuracy"], r["lift"]))
    L.append("")
    L.append("  ceiling: accuracy %.4f%%   F1 %.4f   always-negative accuracy %.4f%%"
             % (100 * fr["max_accuracy"], fr["max_f1"],
                100 * fr["always_negative_accuracy"]))
    L.append("")
    L.append("SELECTED CUT  policy=%s  threshold=%.6f  (chosen on validation)"
             % (sel["policy"], sel["threshold"]))
    L.append("  on test: flagged %.2f%%  precision %.4f  recall %.4f  F1 %.4f  "
             "accuracy %.4f%%  lift %.2fx"
             % (100 * at["flagged_pct"], at["precision"], at["recall"], at["f1"],
                100 * at["accuracy"], at["lift"]))
    L.append("  TN %d  FP %d  FN %d  TP %d" % (at["tn"], at["fp"], at["fn"], at["tp"]))
    L.append("")
    L.append("RISK TIERS  cuts %s" % rep["tier_cuts"])
    L.append("  %-10s %9s %8s %14s" % ("tier", "rows", "share", "observed rate"))
    for t in rep["tiers"]:
        L.append("  %-10s %9d %7.2f%% %13.4f%%"
                 % (t["tier"], t["n"], 100 * t["share"], 100 * t["observed_rate"]))
    L.append("=" * 78)
    return "\n".join(L)
