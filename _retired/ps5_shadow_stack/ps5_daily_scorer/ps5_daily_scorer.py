# =============================================================================
# cubic-mars-ps5-daily-scorer  --  PS5 RELIABILITY daily RUL scorer (Lambda)
# -----------------------------------------------------------------------------
# The SERVE half of the train/score split (event = hardware-OOS-Set, v5.1):
#   * TRAIN (SageMaker/Databricks, weekly): the v5.1 notebook fits Weibull + Cox on
#     the OOS-Set intervals and persists SLIM params + a device STATE feed to S3.
#   * SERVE (this Lambda, daily): load those params + the latest state, recompute
#     RUL **params-only with numpy** (NO scikit-survival at serve time), and upsert
#     per-device + per-serial RUL into RDS (migration 06 tables).
#
# Why this runs in Lambda and the notebook does not: this does zero model fitting and
# no cross-validation -- it is a vector of coefficients dotted into a feature matrix,
# then a Weibull residual-life integral. Milliseconds, a few MB, deps = numpy + pandas
# (+ boto3/psycopg2). The training notebook's purged-CV over RSF/GBSA/Coxnet on full
# history is what needs SageMaker/Databricks.
#
# Deps: numpy, pandas, boto3 (in the runtime), pyarrow (parquet state) OR csv, psycopg2
#       (layer). NOT sksurv/sklearn/scipy -- that's the whole point of persisting params.
#
# Env (all optional except the bucket + secret in real use):
#   PS5_PARAMS_BUCKET   S3 bucket holding params + state       (e.g. cubic-mars-...-gold-...)
#   PS5_PARAMS_PREFIX   prefix for <type>_device_survival_params.json  (default chicago/ps5/params)
#   PS5_STATE_PREFIX    prefix for <type>_device_state.parquet/.csv    (default chicago/ps5/state)
#   PS5_CITY            city id                                  (default CHI)
#   PS5_DEVICE_TYPES    csv of subfolders                        (default tvm,gates,validators)
#   PS5_RDS_SECRET      Secrets Manager id for RDS creds         (e.g. cubic/rds/dashboard)
#   PS5_WRITE_RDS       "true" to upsert to RDS; "false" to dry-run (default true in Lambda)
# =============================================================================
import os, io, json, math, datetime as _dt

import numpy as np
import pandas as pd

_TRAPZ = getattr(np, "trapezoid", None) or np.trapz   # numpy>=2 renamed trapz -> trapezoid
EVENT_DEF_FALLBACK = ("hw_oos_set", "2026-07-23.v1")
SUB_TO_DEVICE_TYPE = {"tvm": "tvms", "gates": "gates", "validators": "validators"}  # RDS device_type domain


# ---------------------------------------------------------------------------
# numpy-only survival math (mirrors the engine's weibull_survival / conditional_mrl)
# ---------------------------------------------------------------------------
def weibull_survival(t, shape, scale):
    shape = max(float(shape), 1e-6); scale = max(float(scale), 1e-6)
    return np.exp(-np.power(np.clip(np.asarray(t, float), 0.0, None) / scale, shape))


def weibull_mean(shape, scale):
    return float(scale) * math.gamma(1.0 + 1.0 / max(float(shape), 1e-6))


def conditional_mrl(age, shape, scale, horizon_mult=6.0, n=400, cap=None):
    """Expected residual life E[T-a | T>a] for Weibull(shape,scale) -- identical to the training engine."""
    age = max(float(age), 0.0)
    Sa = float(weibull_survival(age, shape, scale))
    if Sa < 1e-6:
        val = float(max(weibull_mean(shape, scale) * 0.05, 1.0))
    else:
        hi = age + horizon_mult * float(scale)
        xs = np.linspace(age, hi, n)
        val = float(max(_TRAPZ(weibull_survival(xs, shape, scale), xs) / Sa, 0.0))
    return float(min(val, cap)) if cap else val


# ---------------------------------------------------------------------------
# PURE scoring core (unit-testable, no I/O). Reproduces the trained RUL from
# params + a state frame. This is the function the self-test verifies.
# ---------------------------------------------------------------------------
def score_devices(params, state_df, feature_asof_date=None):
    cat = params.get("category", "")
    shape = float(params["weibull"]["shape"]); scale = float(params["weibull"]["scale"])
    cap = float(params.get("rul_cap_days", 3650.0))
    bands = params.get("hazard_pctl_bands", {"CRITICAL": 0.90, "HIGH": 0.70, "MEDIUM": 0.40})
    df = state_df.copy()
    ages = pd.to_numeric(df["current_healthy_age_days"], errors="coerce").fillna(0.0).clip(lower=0).values

    cox = params.get("cox")
    if cox and cox.get("features"):
        feats = cox["features"]
        coef = np.asarray(cox["coef"], float)
        mean = np.asarray(cox["feature_means"], float)
        std = np.asarray(cox["feature_stds"], float); std[std == 0] = 1.0
        lp_med = float(cox.get("lp_median", 0.0))
        # build the feature matrix in the SAME order the params were persisted; missing -> mean (=> 0 after standardize)
        X = np.column_stack([
            pd.to_numeric(df[f], errors="coerce").values if f in df.columns else np.full(len(df), mean[j])
            for j, f in enumerate(feats)
        ]).astype(float)
        X = np.where(np.isnan(X), mean, X)
        lp = ((X - mean) / std) @ coef                     # == sksurv/lifelines CoxPH linear predictor
        lpc = np.clip(lp - lp_med, -3.0, 3.0)
        scale_i = scale * np.exp(-lpc / max(shape, 0.3))
        hz = pd.Series(lpc).rank(pct=True).values
    else:
        scale_i = np.full(len(df), scale); hz = np.full(len(df), 0.5)

    rul = np.array([conditional_mrl(a, shape, s, cap=cap) for a, s in zip(ages, scale_i)])
    med_i = np.minimum(scale_i * (math.log(2.0) ** (1.0 / max(shape, 0.3))), cap)
    band = np.where(hz >= bands["CRITICAL"], "CRITICAL",
                    np.where(hz >= bands["HIGH"], "HIGH",
                             np.where(hz >= bands["MEDIUM"], "MEDIUM", "LOW")))
    asof = feature_asof_date or params.get("run_date") or _dt.date.today().isoformat()
    # display recency: days-since-HW-OOS defaults to the current age; roll_fail_30d comes from the state feature col
    dsh = (pd.to_numeric(df["days_since_hw_oos"], errors="coerce").fillna(0.0).values
           if "days_since_hw_oos" in df.columns else ages)
    rf30 = (pd.to_numeric(df["roll_fail_30d"], errors="coerce").fillna(0).astype(int).values
            if "roll_fail_30d" in df.columns else np.zeros(len(df), dtype=int))
    fac = (df["facility_id"].values if "facility_id" in df.columns else np.array([None] * len(df), dtype=object))
    out = pd.DataFrame({
        "device_id": df["DEVICE_ID"].values, "mars_device_category": cat,
        "current_healthy_age_days": np.round(ages, 1),
        "rul_standard_days": np.round(rul, 1),
        "predicted_median_survival_days": np.round(med_i, 1),
        "hazard_score": np.round(hz, 4), "risk_band": band, "is_overdue": (ages > med_i),
        "n_prior_oos": (pd.to_numeric(df["failure_seq"], errors="coerce").fillna(0).astype(int).values
                        if "failure_seq" in df.columns else np.zeros(len(df), dtype=int)),
        "days_since_hw_oos": np.round(dsh, 1), "roll_fail_30d": rf30, "facility_id": fac,
        "feature_asof_date": asof,
        "event_definition": params.get("event_definition", EVENT_DEF_FALLBACK[0]),
        "event_def_version": params.get("event_def_version", EVENT_DEF_FALLBACK[1]),
        "cv_cindex": params.get("cv_cindex"), "gate_pass": bool(params.get("gate_pass", False)),
    }).sort_values("rul_standard_days")
    return out


def score_serials(params, roster_df, feature_asof_date=None):
    """Per-serial RUL from the device-type Weibull + component age (params-only)."""
    cat = params.get("category", "")
    shape = float(params["weibull"]["shape"]); scale = float(params["weibull"]["scale"])
    cap = float(params.get("rul_cap_days", 3650.0))
    tiers = params.get("risk_tiers", [[0.02, "CRITICAL"], [0.008, "HIGH"], [0.002, "MEDIUM"], [-1.0, "LOW"]])

    def tier(rs):
        for thr, name in tiers:
            if rs > thr:
                return name
        return "LOW"

    df = roster_df.copy()
    age = pd.to_numeric(df["component_age_days"], errors="coerce").fillna(0.0).clip(lower=0).values
    fails = pd.to_numeric(df.get("device_oos_failures_total", 0), errors="coerce").fillna(0).values
    rs = np.round(fails / np.clip(age, 1, None), 6)
    med = round(min(scale * (math.log(2.0) ** (1.0 / max(shape, 0.3))), cap), 1)
    out = pd.DataFrame({
        "device_id": df["DEVICE_ID"].values,
        "component_serial_nbr": df["COMPONENT_SERIAL_NBR"].values,
        "component_type_name": df.get("COMPONENT_TYPE_NAME", pd.Series(["OTHER"] * len(df))).values,
        "mars_device_category": cat, "component_age_days": np.round(age, 1),
        "device_oos_failures_total": fails.astype(int),
        "risk_score": rs, "risk_tier": [tier(x) for x in rs],
        "expected_component_rul_days": np.round([conditional_mrl(a, shape, scale, cap=cap) for a in age], 1),
        "predicted_median_survival_days": med, "is_overdue": (age > med),
        "feature_asof_date": feature_asof_date or params.get("run_date") or _dt.date.today().isoformat(),
        "event_definition": params.get("event_definition", EVENT_DEF_FALLBACK[0]),
        "event_def_version": params.get("event_def_version", EVENT_DEF_FALLBACK[1]),
    }).sort_values("risk_score", ascending=False)
    return out


# ---------------------------------------------------------------------------
# I/O helpers (S3 params/state, RDS upsert). Kept thin so the core stays testable.
# ---------------------------------------------------------------------------
def _read_table(path_or_bytes):
    """Read parquet if possible, else CSV. Accepts a local path or raw bytes."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        buf = io.BytesIO(path_or_bytes)
        try:
            return pd.read_parquet(buf)
        except Exception:
            buf.seek(0); return pd.read_csv(buf)
    if str(path_or_bytes).endswith(".csv"):
        return pd.read_csv(path_or_bytes)
    try:
        return pd.read_parquet(path_or_bytes)
    except Exception:
        return pd.read_csv(path_or_bytes)


def _s3_get(bucket, key):
    import boto3
    return boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()


def _s3_get_first(bucket, prefix, name_base, exts=(".parquet", ".csv")):
    for ext in exts:
        try:
            return _s3_get(bucket, f"{prefix}/{name_base}{ext}")
        except Exception:
            continue
    raise FileNotFoundError(f"s3://{bucket}/{prefix}/{name_base}[{'/'.join(exts)}]")


def _rds_conn(secret_id):
    import boto3, psycopg2
    sec = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)["SecretString"])
    return psycopg2.connect(host=sec["host"], port=int(sec.get("port", 5432)), dbname=sec["dbname"],
                            user=sec["username"], password=sec["password"], connect_timeout=8)


def _fac(v):
    return str(v) if (v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v) != "nan") else None


def _upsert_device(cur, city, asof, run_id, params, row):
    """Append/refresh one device row for this as_of_date (05 schema: key = city_id, device_id, as_of_date)."""
    wb = params.get("weibull") or {}
    cur.execute(
        """INSERT INTO ps5_reliability_estimates
             (city_id, run_id, device_id, mars_device_category, current_healthy_age_days, rul_standard_days,
              predicted_median_survival_days, hazard_score, risk_band, is_overdue, n_prior_failures,
              concordance_index, champion_model, weibull_shape, data_quality_gate_passed, facility_id, as_of_date,
              days_since_hw_oos, roll_fail_30d, event_definition, event_def_version, feature_asof_date, scored_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
           ON CONFLICT (city_id, device_id, as_of_date) DO UPDATE SET
             run_id=EXCLUDED.run_id, mars_device_category=EXCLUDED.mars_device_category,
             current_healthy_age_days=EXCLUDED.current_healthy_age_days, rul_standard_days=EXCLUDED.rul_standard_days,
             predicted_median_survival_days=EXCLUDED.predicted_median_survival_days, hazard_score=EXCLUDED.hazard_score,
             risk_band=EXCLUDED.risk_band, is_overdue=EXCLUDED.is_overdue, n_prior_failures=EXCLUDED.n_prior_failures,
             concordance_index=EXCLUDED.concordance_index, champion_model=EXCLUDED.champion_model,
             weibull_shape=EXCLUDED.weibull_shape, data_quality_gate_passed=EXCLUDED.data_quality_gate_passed,
             facility_id=EXCLUDED.facility_id, days_since_hw_oos=EXCLUDED.days_since_hw_oos,
             roll_fail_30d=EXCLUDED.roll_fail_30d, event_definition=EXCLUDED.event_definition,
             event_def_version=EXCLUDED.event_def_version, feature_asof_date=EXCLUDED.feature_asof_date,
             scored_at=now()""",
        (city, run_id, row["device_id"], row["mars_device_category"], _f(row["current_healthy_age_days"]),
         _f(row["rul_standard_days"]), _f(row["predicted_median_survival_days"]), _f(row["hazard_score"]),
         row["risk_band"], bool(row["is_overdue"]), int(row["n_prior_oos"]),
         _f(params.get("cv_cindex")), (params.get("champion") or "CoxPH (params-only serving)"),
         _f(wb.get("shape")), bool(params.get("gate_pass", False)), _fac(row["facility_id"]), asof,
         _f(row["days_since_hw_oos"]), int(row["roll_fail_30d"]),
         row["event_definition"], row["event_def_version"], row["feature_asof_date"]))


def _upsert_serial(cur, city, asof, run_id, row):
    """Append/refresh one serial row for this as_of_date (05 schema: key = city_id, device_id, serial, as_of_date)."""
    cur.execute(
        """INSERT INTO ps5_serial_reliability
             (city_id, run_id, device_id, component_serial_nbr, component_type, mars_device_category,
              component_age_days, device_oos_failures_total, risk_score, risk_tier, expected_component_rul_days,
              predicted_median_survival_days, is_overdue, as_of_date, event_definition, event_def_version,
              feature_asof_date, scored_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
           ON CONFLICT (city_id, device_id, component_serial_nbr, as_of_date) DO UPDATE SET
             run_id=EXCLUDED.run_id, component_type=EXCLUDED.component_type,
             mars_device_category=EXCLUDED.mars_device_category, component_age_days=EXCLUDED.component_age_days,
             device_oos_failures_total=EXCLUDED.device_oos_failures_total, risk_score=EXCLUDED.risk_score,
             risk_tier=EXCLUDED.risk_tier, expected_component_rul_days=EXCLUDED.expected_component_rul_days,
             predicted_median_survival_days=EXCLUDED.predicted_median_survival_days, is_overdue=EXCLUDED.is_overdue,
             event_definition=EXCLUDED.event_definition, event_def_version=EXCLUDED.event_def_version,
             feature_asof_date=EXCLUDED.feature_asof_date, scored_at=now()""",
        (city, run_id, row["device_id"], row["component_serial_nbr"], row["component_type_name"],
         row["mars_device_category"], _f(row["component_age_days"]), int(row["device_oos_failures_total"]),
         _f(row["risk_score"]), row["risk_tier"], _f(row["expected_component_rul_days"]),
         _f(row["predicted_median_survival_days"]), bool(row["is_overdue"]), asof,
         row["event_definition"], row["event_def_version"], row["feature_asof_date"]))


def _f(v):
    try:
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------
def lambda_handler(event, context=None):
    event = event or {}
    bucket = event.get("params_bucket") or os.environ.get("PS5_PARAMS_BUCKET")
    pfx = event.get("params_prefix") or os.environ.get("PS5_PARAMS_PREFIX", "chicago/ps5/params")
    spfx = event.get("state_prefix") or os.environ.get("PS5_STATE_PREFIX", "chicago/ps5/state")
    city = event.get("city") or os.environ.get("PS5_CITY", "CHI")
    types = (event.get("device_types") or os.environ.get("PS5_DEVICE_TYPES", "tvm,gates,validators")).split(",")
    secret = event.get("rds_secret") or os.environ.get("PS5_RDS_SECRET")
    write_rds = str(event.get("write_rds", os.environ.get("PS5_WRITE_RDS", "true"))).lower() == "true"
    asof = event.get("asof") or _dt.date.today().isoformat()
    if not bucket:
        raise ValueError("PS5_PARAMS_BUCKET (or event.params_bucket) is required")

    summary = {"event_def_version": None, "asof": asof, "city": city, "types": {}, "wrote_rds": False}
    scored = {}; serialed = {}; params_by_sub = {}
    for sub in [t.strip() for t in types if t.strip()]:
        params = json.loads(_s3_get(bucket, f"{pfx}/{sub}_device_survival_params.json"))
        params_by_sub[sub] = params
        summary["event_def_version"] = params.get("event_def_version")
        state = _read_table(_s3_get_first(bucket, spfx, f"{sub}_device_state"))
        dev = score_devices(params, state, feature_asof_date=asof)
        scored[sub] = dev
        summary["types"][sub] = {"n_devices": int(len(dev)),
                                 "median_rul_days": float(np.nanmedian(dev["rul_standard_days"])),
                                 "critical": int((dev["risk_band"] == "CRITICAL").sum()),
                                 "gate_pass": bool(params.get("gate_pass", False))}
        # serials (best-effort): re-score from a serial roster feed (roster + device OOS-Set counts) if present
        try:
            roster = _read_table(_s3_get_first(bucket, spfx, f"{sub}_serial_reliability"))
            roster = roster.rename(columns={"component_serial_nbr": "COMPONENT_SERIAL_NBR",
                                            "component_type_name": "COMPONENT_TYPE_NAME", "device_id": "DEVICE_ID"})
            if {"DEVICE_ID", "COMPONENT_SERIAL_NBR", "component_age_days"}.issubset(roster.columns):
                serialed[sub] = score_serials(params, roster, feature_asof_date=asof)
                summary["types"][sub]["n_serials"] = int(len(serialed[sub]))
        except Exception:
            pass

    if write_rds and secret:
        import uuid
        run_id = str(uuid.uuid4())
        conn = _rds_conn(secret); conn.autocommit = False
        try:
            cur = conn.cursor()
            n_dev = sum(len(d) for d in scored.values()); n_ser = sum(len(s) for s in serialed.values())
            cur.execute(
                """INSERT INTO ps5_scoring_runs
                     (run_id, city_id, run_ts, n_devices_scored, n_serials_scored, model_version, scoring_mode,
                      status, note, event_definition, event_def_version, event_filter, run_date, scorer)
                   VALUES (%s,%s, now(), %s,%s,%s,'LAMBDA','SUCCESS',%s,%s,%s,%s,%s,'ps5_daily_scorer')""",
                (run_id, city, n_dev, n_ser, summary["event_def_version"], f"daily OOS-Set score {asof}",
                 EVENT_DEF_FALLBACK[0], summary["event_def_version"], json.dumps(event.get("event_filter", {})), asof))
            for sub, dev in scored.items():
                for _, r in dev.iterrows():
                    _upsert_device(cur, city, asof, run_id, params_by_sub[sub], r)
            for sub, ser in serialed.items():
                for _, r in ser.iterrows():
                    _upsert_serial(cur, city, asof, run_id, r)
            conn.commit(); summary["wrote_rds"] = True; summary["run_id"] = run_id
        finally:
            conn.close()
    return summary


# ---------------------------------------------------------------------------
# LOCAL SELF-TEST (no AWS): prove the numpy scorer reproduces the trained RUL from
# the persisted params + state feed the engine wrote. Run:
#   python ps5_daily_scorer.py /tmp/ps5_build/PS5_reliability_v5_outputs
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys, glob
    root = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ps5_build/PS5_reliability_v5_outputs"
    ok = True
    for pf in sorted(glob.glob(f"{root}/*/*_device_survival_params.json")):
        sub = pf.split("/")[-2]
        params = json.loads(open(pf).read())
        pq = glob.glob(f"{root}/{sub}/{sub}_device_state.parquet")
        state = _read_table(pq[0] if pq else f"{root}/{sub}/{sub}_device_state.csv")
        got = score_devices(params, state).set_index("device_id")["rul_standard_days"]
        train = pd.read_csv(f"{root}/{sub}/{sub}_device_rul_estimates.csv").set_index("device_id")["rul_standard_days"]
        j = got.to_frame("lambda").join(train.to_frame("train"), how="inner")
        diff = (j["lambda"] - j["train"]).abs()
        mad = float(diff.mean()); worst = float(diff.max()); corr = float(j["lambda"].corr(j["train"]))
        passed = worst <= 1.0 and corr >= 0.999               # numpy serve == sksurv train (within rounding)
        # serial parity: re-score the roster and compare component RUL to the training serial feed
        spath = f"{root}/{sub}/{sub}_serial_reliability.csv"
        s_ok = True; s_worst = 0.0; n_ser = 0
        if glob.glob(spath):
            roster = pd.read_csv(spath)
            sp = json.loads(open(f"{root}/{sub}/{sub}_serial_params.json").read()) if glob.glob(f"{root}/{sub}/{sub}_serial_params.json") else params
            sp = {**params, **{k: sp[k] for k in ("weibull", "rul_cap_days", "risk_tiers") if k in sp}}
            sg = score_serials(sp, roster).set_index("component_serial_nbr")["expected_component_rul_days"]
            st = roster.set_index("COMPONENT_SERIAL_NBR")["expected_component_rul_days"]
            sj = sg.to_frame("l").join(st.to_frame("t"), how="inner"); n_ser = len(sj)
            s_worst = float((sj["l"] - sj["t"]).abs().max()); s_ok = s_worst <= 1.0
        ok &= passed and s_ok
        print(f"[{sub:10s}] DEV n={len(j):3d} worst|Δ|={worst:.3f}d corr={corr:.5f} {'PASS' if passed else 'FAIL'} | "
              f"SER n={n_ser:4d} worst|Δ|={s_worst:.3f}d {'PASS' if s_ok else 'FAIL'} "
              f"(median RUL train={train.median():.0f}d lambda={got.median():.0f}d)")
    print("SELF-TEST:", "PASS -- numpy scorer reproduces trained device+serial RUL" if ok else "FAIL")
    sys.exit(0 if ok else 1)
