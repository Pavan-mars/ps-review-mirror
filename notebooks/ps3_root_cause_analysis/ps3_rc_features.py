"""
ps3_rc_features.py -- the PS3 root-cause feature contract, in ONE place.

WHY THIS FILE EXISTS
--------------------
Training built its features inside the notebook. If daily scoring rebuilds
them from a second copy of that code, the two drift -- and training/serving
skew does not announce itself. It shows up as a model that scored 0.91 in the
notebook and quietly predicts the majority class in production, which is
indistinguishable from "the model was always weak" unless someone happens to
diff two files that were never meant to be compared.

So both sides import this module. The notebook trains with it, the daily
scorer scores with it, and the packaged artifact records the exact feature
ORDER it was fitted on. If this file changes, every artifact fitted before
the change fails its own contract check at load time rather than silently
scoring on shifted columns.

THE LEAKAGE RULE, RESTATED WHERE IT IS ENFORCED
-----------------------------------------------
component_subsystem -- the target -- is supplied by silver.dim_event_type
joined on EVENT_TYPE_ID. So event_type_id, event_type_name, event_type_desc
and component_type_name are deterministic functions of the target. Anything
in LEAKY is barred from the feature list, and assert_no_leakage() is called
on both paths, not just the training one.

Every history feature is shift(1)-ed after sorting on (device_id,
transit_day), so a row can never see its own label. That is the whole reason
the feature builder is backward-looking by construction rather than by
convention.
"""
import numpy as np
import pandas as pd

FEATURE_CONTRACT_VERSION = "ps3_rc_features.2026-08-03.v1"

# Columns that ARE the target, or reconstruct it. Restated here so nobody
# re-adds one later in a notebook cell that never sees the training code.
LEAKY = [
    "component_subsystem", "component_type_name", "event_type_name",
    "event_type_desc", "event_type_id", "root_cause", "root_cause_state",
    "sn_root_cause", "sn_severity", "severity_target", "sn_agrees",
    "sn_fault_desc", "sn_label_state", "component_serial_nbr",
]

# The non-history features, in fixed order. Anything absent from the frame is
# dropped by the caller and RECORDED in the artifact, so a scoring frame that
# is missing a column the model was fitted on is caught rather than filled
# with a plausible zero.
BASE_NUM = [
    "prior_oos_count", "days_since_prior_oos", "device_lifetime_days",
    "prior_oos_rate", "hour_of_day", "day_of_week",
    "events_in_day", "informative_in_day", "component_position",
]
BASE_CAT = ["control_group", "transit_mode", "device_type_name", "prior_root_cause"]

TARGET = "root_cause"
KEYS = ["device_id", "transit_day"]


def prior_share_col(cls):
    return "prior_share_%s" % cls


def build_history_features(d, classes=None):
    """Device failure history as of each device-day, strictly backward-looking.

    `classes` pins WHICH prior_share_* columns are produced and in what order.
    At training time it is None and the classes are read off the data. At
    scoring time it MUST be the trained class list -- otherwise a fleet that
    happened not to see a class in the scoring window produces a narrower
    matrix, every column after the gap shifts by one, and the model scores
    confidently on the wrong numbers. That failure is silent, which is why
    the parameter is not optional in the scorer.
    """
    d = d.sort_values(KEYS).copy()
    g = d.groupby("device_id", sort=False)

    d["prior_oos_count"] = g.cumcount()
    d["days_since_prior_oos"] = (d["transit_day"] - g["transit_day"].shift(1)).dt.days
    d["device_lifetime_days"] = (
        d["transit_day"] - g["transit_day"].transform("min")).dt.days
    d["prior_oos_rate"] = d["prior_oos_count"] / d["device_lifetime_days"].replace(0, np.nan)

    # the device's PREVIOUS root cause ...
    d["prior_root_cause"] = g[TARGET].shift(1)

    # ... and how often each cause has occurred for it BEFORE this row.
    # shift(1) then expanding().mean() is the whole guarantee: the row's own
    # label is excluded from its own feature.
    if classes is None:
        classes = sorted(d[TARGET].dropna().unique())
    for cls in classes:
        hit = (d[TARGET] == cls).astype(float)
        d[prior_share_col(cls)] = (
            hit.groupby(d["device_id"])
               .apply(lambda s: s.shift(1).expanding().mean())
               .reset_index(level=0, drop=True))
    return d


def feature_lists(d, classes):
    """The (numeric, categorical) feature names, in the order the model sees.

    Order is part of the contract. numpy has no column names, so a reordered
    list is not an error -- it is a wrong answer.
    """
    feat_num = [c for c in BASE_NUM if c in d.columns]
    feat_num += [prior_share_col(c) for c in classes if prior_share_col(c) in d.columns]
    feat_cat = [c for c in BASE_CAT if c in d.columns]
    assert_no_leakage(feat_num + feat_cat)
    return feat_num, feat_cat


def assert_no_leakage(features):
    leaked = [c for c in features if c in LEAKY]
    if leaked:
        raise AssertionError(
            "LEAKAGE: %s would reconstruct the target. component_subsystem comes "
            "from dim_event_type joined on EVENT_TYPE_ID, so any event-type column "
            "IS the label wearing a different name." % leaked)


def design_matrix(d, feat_num, feat_cat, encoder, fit=False):
    """Assemble X in the contracted order, or say exactly what is missing.

    A missing column is NOT filled with a zero here. A zero in
    prior_oos_count means "this device has never failed before", which is a
    real and confident statement; inventing it because a column did not
    arrive turns a pipeline fault into a prediction.
    """
    missing = [c for c in feat_num + feat_cat if c not in d.columns]
    if missing:
        raise KeyError(
            "scoring frame is missing %d contracted feature(s): %s. The model was "
            "fitted on them; scoring without them would silently shift every "
            "column after the gap." % (len(missing), missing))

    num = d[feat_num].to_numpy(dtype=float)
    if not feat_cat:
        return num
    cat_raw = d[feat_cat].astype(str)
    cat = encoder.fit_transform(cat_raw) if fit else encoder.transform(cat_raw)
    return np.hstack([num, cat])


def prepare_for_scoring(spine, score_dates, classes):
    """History from labelled days, rows to score from the requested days.

    THE SUBTLETY THAT MATTERS. Training filtered to root_cause_state ==
    'LABELLED' and built history over exactly those rows. Scoring must build
    history the same way -- but the days being scored are, by definition, the
    ones whose cause is not yet known, so they cannot be inside that filter.

    So: take every LABELLED day strictly before the scoring window as
    history, append the scoring days regardless of their label state, sort,
    build features over the union, then return only the scoring rows. Because
    every feature is shift(1)-ed, a scoring row reads its device's past and
    never itself.
    """
    spine = spine.copy()
    spine["transit_day"] = pd.to_datetime(spine["transit_day"])
    want = pd.to_datetime(pd.Series(list(score_dates))).dt.normalize()
    lo = want.min()

    hist = spine[(spine["root_cause_state"] == "LABELLED") & (spine["transit_day"] < lo)]
    todo = spine[spine["transit_day"].dt.normalize().isin(set(want))]
    if todo.empty:
        return todo, todo

    both = pd.concat([hist, todo], ignore_index=True)
    feats = build_history_features(both, classes=classes)
    mask = feats["transit_day"].dt.normalize().isin(set(want))
    return feats[mask].copy(), feats
