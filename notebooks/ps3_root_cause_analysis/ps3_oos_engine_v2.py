# =============================================================================
# CUBIC MARS Chicago -- PS3 engine (severity + root-cause/component, two heads)
# Runnable module form. This same code is sliced into the SageMaker notebook.
# Runs here on SYNTHETIC data to validate the logic end-to-end before shipping.
# =============================================================================
import os, sys, json, math, warnings, time
from pathlib import Path
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------- CONFIG
CONFIG = {
    "CITY_ID": "CHI",

    # ---------------- v2 isolation - nothing here can reach the live v1 endpoint ----
    "V2_SUFFIX": "oos_v2",
    "LIVE_ENDPOINT_DO_NOT_TOUCH": "chicago-ps3-rootcause-v1",

    # ---------------- SageMaker deploy / ECR (parity with the PS1 notebooks) -------
    # ANSWER TO "does it register model endpoints in ECR like PS1?":
    #   With DEPLOY_ENDPOINT = False (the default) this notebook does NOT touch
    #   SageMaker at all -- no ECR image, no Model, no EndpointConfig, no Endpoint.
    #   It trains, scores, writes CSV/Parquet + manifest.json, and stops. That is
    #   deliberate: it is what makes the run provably unable to disturb the live
    #   v1 severity endpoint while it is serving.
    #
    #   Set DEPLOY_ENDPOINT = True to get PS1 parity. Section 16 then packages the
    #   trained head bundles into model.tar.gz, resolves the AWS-managed sklearn
    #   inference image from ECR via sagemaker.image_uris.retrieve() (the same
    #   mechanism the PS1 notebooks use -- no custom image is built or pushed), and
    #   creates a Model + EndpointConfig + Endpoint under V2_ENDPOINT_NAME.
    #
    #   V2_ENDPOINT_NAME is a NEW name. Section 16 asserts it differs from
    #   LIVE_ENDPOINT_DO_NOT_TOUCH and refuses to run if it does not, so the v1
    #   endpoint cannot be updated or deleted even by a typo.
    "DEPLOY_ENDPOINT": False,
    "V2_ENDPOINT_NAME": "chicago-ps3-oos-v2",
    "SM_ROLE_ARN": "",                 # blank -> sagemaker.get_execution_role()
    "SM_INSTANCE_TYPE": "ml.m5.large",
    "SM_INSTANCE_COUNT": 1,
    "SM_FRAMEWORK": "sklearn",         # managed container family for the joblib bundles
    "SM_FRAMEWORK_VERSION": "1.2-1",
    "SM_MODEL_S3_PREFIX": "s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/ps3_v2/model",
    "REGISTER_MLFLOW": False,
    "MLFLOW_EXPERIMENT": "chicago-ps3-oos-v2",

    # ---------------- OOS spine, never chargeable ---------------------------------
    "SPINE": "oos",
    "ASSERT_OOS_SPINE": True,
    "FORBID_CHARGEABLE_FILTER": True,

    # ---------------- serial fan-out (the v1 gap) ---------------------------------
    "SERIAL_FANOUT": True,
    "SERIAL_MIN_INCIDENTS": 1,

    # ---------------- bridge ------------------------------------------------------
    "EMIT_MANIFEST": True,
    "RDS_TABLE_PREFIX": "ps3v2_",
    "S3_OUT_PREFIX": "s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/ps3_v2",

    "DEVICE_SCOPE": ["TVM", "GATE", "VALIDATOR"],   # engine loops these; VALIDATOR -> stub+proxy
    "SEVERITY_TARGET": "failure_level_label",         # multi-class severity head target
    # -> RDS predicted_label (dashboard MAJOR/CRITICAL). FIXED 21-Jul-2026: the 18-Jul live run found
    # this collapsed to 100% MAJOR because it was keyed on human-readable labels while the real
    # failure_level_label values on gold.device_ps3_incident are CODES. Keyed to match
    # ps3_collapse_fix.py exactly (verified against the live TVM/GATE feeds) -- unknown/other code
    # v2: unmapped/OTHER codes now resolve to UNKNOWN at collapse time and are excluded
    # from pct_critical denominators - they no longer default to MAJOR.
    "SEVERITY_COLLAPSE": {
        "PURCHASE_CARD": "MAJOR", "PURCHASE_PRODUCT": "MAJOR", "NONPAYMENT": "MAJOR",
        "ALL_PURCHASE": "CRITICAL", "ALL_FUNCTIONS": "CRITICAL",
        "BUS_READER": "CRITICAL", "BUS_READER_ASSEMBLY": "CRITICAL",
    },
    "ROOTCAUSE_TARGET": "derived_component_type",      # component/subsystem root-cause head target
    "SEVERITY_F1_FLOOR": 0.55,                         # provisional macro-F1 gate (honest v3 = 0.74)
    "ROOTCAUSE_F1_FLOOR": 0.45,                        # provisional macro-F1 gate (honest v3 = 0.54)
    "MIN_CLASS_COUNT": 30,                             # collapse classes rarer than this into OTHER
    "MIN_INCIDENTS_TO_MODEL": 200,                     # below this -> report-only (sparse device type)

    # ---- data source (SageMaker Studio path = S3 parquet; Spark auto-detected on Databricks) ----
    "DATA_SOURCE": "auto",
    "GOLD_TABLE":   "mars_dev.gold.device_ps3_incident",
    "GOLD_PARQUET": "s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/gold/device_ps3_incident",
    "S3_REGION":    "us-east-1",
    "DBX_HOST": "", "DBX_HTTP_PATH": "", "DBX_TOKEN_ENV": "DATABRICKS_TOKEN",

    # ---- enrichment silvers (leakage-safe, prior-window; each guarded -> skip if export missing) ----
    # NOTE (21-Jul-2026): ENRICH_USAGE/ENRICH_STATION were declared in the 18-Jul delivery but never
    # actually wired to a join -- dead config. This revision implements all four as real merge_asof
    # (backward, i.e. "most recent silver row strictly before AE_START_DTM's date") joins so no
    # post-incident information leaks in. Every new column lands with an `enr_` prefix, which
    # build_feature_list() already auto-includes and leakage_scan()/NZV-filter already auto-covers --
    # no changes needed there. Columns below are grounded against the chicago-data-catalog skill's
    # silver_schemas.md (usage_lifecycle_daily/station_network_daily/metric_daily) and the PS1
    # AUC-boost patch's confirmed device_uptime_intervals columns (ps1_auc_boost_completion, 21-Jul).
    "ENRICH_SOURCES": True,
    "SILVER_PREFIX": "s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/silver",
    "ENRICH_USAGE":     True,   # usage_lifecycle_daily -- failure_count_30d, days_since_last_*, wear
    "ENRICH_STATION":   True,   # station_network_daily -- prior-day station co-failure stress
    "ENRICH_METRIC":    True,   # metric_daily (M401) -- pre-incident tap-timing/comms degradation
    "ENRICH_UPTIME":    True,   # device_uptime_intervals -- silence/heartbeat precursor signal
    "HWCONFIG_TABLE":   "mars_dev.silver.hw_config_current",
    # v1 declared this and never used it - that is exactly why the serial grain collapsed.
    "HWCONFIG_PARQUET": "s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/silver/hw_config_current",
    "USAGE_LIFECYCLE_TABLE":  "mars_dev.silver.usage_lifecycle_daily",
    "STATION_NETWORK_TABLE":  "mars_dev.silver.station_network_daily",
    "METRIC_DAILY_TABLE":     "mars_dev.silver.metric_daily",
    "UPTIME_INTERVALS_TABLE": "mars_dev.silver.device_uptime_intervals",
    "ENRICH_ASOF_TOLERANCE_DAYS": 10,   # merge_asof tolerance -- don't attach a silver row >10d stale

    # ---- ServiceNow conformance probe (diagnostic only -- does NOT feed features) ----
    # 21-Jul-2026: bronze cta_servicenow_incident + servicenow_incident are deprecated as silver join
    # sources; silver.incident_root_cause (S17, upstream of PS3's root-cause label) is a repoint
    # target for the new silver.servicenow_incident_conformed. The 18-Jul live verdict found the
    # *true* 9-class ServiceNow root cause blocked because SVN_STAGE was 0 rows -- incident_root_cause
    # carries `from_svn_stage`/`from_cta_sn_mirror` boolean columns that tell us the moment that
    # unblocks. This is read-only and print-only; it never changes what the two heads train on.
    "PROBE_SERVICENOW_CONFORMANCE": True,
    "INCIDENT_ROOT_CAUSE_TABLE": "mars_dev.silver.incident_root_cause",
    "INCIDENT_ROOT_CAUSE_PARQUET": "s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/silver/incident_root_cause",
    # candidate columns that would appear on gold.device_ps3_incident IF/when it starts drawing
    # richer conformed-ServiceNow fields -- never hardcoded as features; auto-discovered + leakage-
    # gated like MONITORED_FEATURES if they ever actually show up (see run_device_type).
    "CANDIDATE_CONFORMANCE_FEATURES": ["sn_category", "sn_priority", "sn_chargeable_level",
                                       "from_svn_stage", "from_cta_sn_mirror"],

    # ---- feature policy (PS3 content-leakage is the #1 risk: free text NAMES the answer) ----
    # HARD leak list -- excluded from BOTH heads (fault text + text-derived flags + targets).
    "EXPLICIT_LEAK": [
        "AE_FAULT_DESCRIPTION", "AE_SYMPTOM", "AE_PROBLEM", "AE_RESOLUTION", "combined_text",
        "affected_component", "matched_component", "sn_event_code_name",
        "desc_printer_flag", "desc_card_reader_flag", "desc_bill_handler_flag", "desc_coin_flag",
        "desc_comms_flag", "desc_timeout_flag", "desc_replacement_flag",
        "failure_level", "failure_level_label", "is_chargeable", "is_device_fault",
        "derived_component_type", "root_cause_category", "kpi_rule_id", "kpi_category_name",
        "AE_FAULT_STATE", "wot_state", "request_type",
    ],
    # CONCURRENT / post-incident cols -- known only after the incident; ablation only, not champion.
    "CONCURRENT_FEATURES": ["incident_duration_min"],
    "USE_CONCURRENT_FEATURES": False,
    # MONITORED -- kept but flagged (the ServiceNow fault code dominates; leakage-scanned).
    "MONITORED_FEATURES": ["sn_event_code_id", "sn_priority"],
    "LEAKAGE_AUC_THRESHOLD": 0.95,
    "NZV_UNIQUE_MIN": 2,
    "RUN_OPTUNA": True,          # add an Optuna-tuned XGBoost challenger (matches v3's xgb_optuna)
    "N_OPTUNA_TRIALS": 20,
    "TUNE_SAMPLE_ROWS": 60000,   # cap tuning rows for speed (None = all)

    # ---- split / CV ----
    "TEST_FRACTION": 0.20, "EMBARGO_DAYS": 2, "CV_SPLITS": 4,
    "MIN_TRAIN_DATE": "2024-01-01", "RANDOM_STATE": 42,

    # ---- outputs ----
    "OUT_ROOT": "PS3_v2_outputs",   # v2 writes beside v1, never over it
    "SUBFOLDER": {"TVM": "tvm", "GATE": "gates", "VALIDATOR": "validators"},

    # ---- MLflow (guarded no-op if blank) ----
    "MLFLOW_TRACKING_URI": "", "MLFLOW_EXPERIMENT": "chicago-ps3-rootcause-severity", "AWS_REGION": "us-east-1",
}
RS = CONFIG["RANDOM_STATE"]

# ----------------------------------------------------------------------------- imports (graceful)
def _try(name):
    try: return __import__(name)
    except Exception as e:
        print(f"[optional] {name} unavailable ({type(e).__name__}); paths using it will be skipped"); return None
xgb = _try("xgboost"); lgb = _try("lightgbm"); catb = _try("catboost"); shap_mod = _try("shap")
optuna_mod = _try("optuna")
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler, LabelEncoder, label_binarize
from sklearn.metrics import (f1_score, roc_auc_score, accuracy_score, confusion_matrix,
                             classification_report, precision_score, recall_score,
                             average_precision_score, balanced_accuracy_score, cohen_kappa_score,
                             matthews_corrcoef, precision_recall_curve, roc_curve, log_loss)
np.random.seed(RS)

PASTEL = ["#9DC3E6", "#A9D18E", "#F4B183", "#C9A0DC", "#FFD966", "#F28E8E", "#8FD4C8", "#BFBFBF"]

# ----------------------------------------------------------------------------- serial grain (v2)
# Restores component granularity WITHOUT touching gold. See ps3_serial_grain.py for the
# attribution-vs-exposure contract and the serial-uniqueness caveat.
# FIX 26-Jul-2026: this resolved SG once, at import time, and in the NOTEBOOK the
# grain module is pasted into an earlier cell -- so `import ps3_serial_grain` fails
# AND sys.modules has no such entry, even though the four functions are sitting in
# the notebook's own globals. SG stayed None, SERIAL_FANOUT was switched off, and
# the 26-Jul run emitted "no serial-grain output" for both TVM and GATE with
# hw_config_current never even loaded. Resolution is now LAZY (at call time) and
# falls back to the notebook namespace, so cell execution order cannot break it.
_SG_FUNCS = ("load_hw_config", "serial_uniqueness_report", "expand_serial_grain", "serial_rollup")
_SG_CACHE = None

def get_serial_grain():
    """Resolve the serial-grain module: real import -> sys.modules -> the functions
    inlined into the notebook namespace. Returns None only if it is genuinely absent."""
    global _SG_CACHE
    if _SG_CACHE is not None:
        return _SG_CACHE
    mod = sys.modules.get("ps3_serial_grain")
    if mod is None:
        try:
            import ps3_serial_grain as mod           # noqa: PLC0415
        except Exception:
            mod = None
    if mod is not None and all(callable(getattr(mod, n, None)) for n in _SG_FUNCS):
        _SG_CACHE = mod
        return _SG_CACHE
    g = globals()
    if all(callable(g.get(n)) for n in _SG_FUNCS):
        import types as _types
        _SG_CACHE = _types.SimpleNamespace(**{n: g[n] for n in _SG_FUNCS})
        print("[serial] using the ps3_serial_grain functions inlined in this notebook")
        return _SG_CACHE
    return None

SG = None   # resolved lazily by get_serial_grain(); kept for backwards reference


# ----------------------------------------------------------------------------- tee logger
class _Tee:
    def __init__(self, *streams): self.streams = streams
    def write(self, d):
        for s in self.streams:
            try: s.write(d); s.flush()
            except Exception: pass
    def flush(self):
        for s in self.streams:
            try: s.flush()
            except Exception: pass

def start_run_log(out_root):
    Path(out_root).mkdir(parents=True, exist_ok=True)
    if "_ORIG_STDOUT" not in globals():
        globals()["_ORIG_STDOUT"], globals()["_ORIG_STDERR"] = sys.stdout, sys.stderr
    lg = open(Path(out_root) / "run_console_log.txt", "w", encoding="utf-8")
    sys.stdout = _Tee(globals()["_ORIG_STDOUT"], lg); sys.stderr = _Tee(globals()["_ORIG_STDERR"], lg)
    print(f"[log] capturing all cell output to {Path(out_root)/'run_console_log.txt'}")

# ----------------------------------------------------------------------------- loader (Spark->SQL->parquet)
def _active_spark():
    g = globals().get("spark", None)
    if g is not None: return g
    try:
        from pyspark.sql import SparkSession
        return SparkSession.getActiveSession()
    except Exception: return None

def load_table(table, parquet_path, category_filter=None, required=True):
    src = CONFIG["DATA_SOURCE"]; sp = _active_spark()
    try:
        if src in ("auto", "databricks_spark") and sp is not None:
            sdf = sp.table(table)
            if category_filter: sdf = sdf.filter(f"mars_device_category = '{category_filter}'")
            print(f"[load] Spark -> {table}"); return sdf.toPandas()
        if src in ("auto", "parquet") and parquet_path:
            rk = {}
            if str(parquet_path).startswith("s3"):
                rk["storage_options"] = {"client_kwargs": {"region_name": CONFIG.get("S3_REGION", "us-east-1")}}
            print(f"[load] parquet -> {parquet_path}")
            if category_filter:
                try: return pd.read_parquet(parquet_path, filters=[("mars_device_category","==",category_filter)], **rk)
                except Exception: pass
            return pd.read_parquet(parquet_path, **rk)
    except Exception as e:
        if required: raise
        print(f"[load] optional source {table} unavailable ({type(e).__name__}: {str(e)[:80]}); skipping"); return None
    if required:
        raise RuntimeError(f"No usable data source for {table}. On SageMaker Studio run "
                           "export_gold_to_s3.py + export_silver_to_s3.py first, or run on a Databricks cluster.")
    return None

def check_availability_event_dedup(df):
    """
PS3 v2 - OOS spine, device AND serial grain. DERIVED from ps3_engine_reference.py
by make_ps3_v2_engine.py - do not hand-edit; edit the base engine or the patch script.

v2 vs v1, in full:
  * SERIAL GRANULARITY. v1 read matched_serial_nbr from gold.device_ps3_incident, whose
    hw_best_match CTE ends `WHERE rn = 1` (FIX 7) - exactly one component per incident. So
    v1's serial rollup, though correctly written, could only emit one row per device: the
    19-Jul run produced 927 device rows and 927 serial rows, identical on every measure.
    v2 joins silver.hw_config_current in the notebook (no gold change) and keys on
    (device_id, COMPONENT_SERIAL_NBR) because serials are NOT unique across devices.
  * OOS SPINE. is_chargeable is a contract flag applied after the physical event and is a
    strict subset of hardware OOS. v2 asserts no chargeable filter is ever applied.
  * UNKNOWN, not MAJOR, for unmapped severity codes.
  * TOTAL ISOLATION from chicago-ps3-rootcause-v1: own output dir, own MLflow names, own
    RDS table prefix, endpoint deployment hard-off.
  * MANIFEST BRIDGE so cubic-mars-ps3-rds-push loads the outputs unchanged, and
    score_new_data() so daily inference runs the same code as training.
"""

    if "availability_event_id" not in df.columns:
        return df
    dup_mask = df["availability_event_id"].duplicated(keep=False)
    n_dup = int(dup_mask.sum())
    if n_dup:
        print("!" * 74)
        print(f"[GUARDRAIL] {n_dup:,} rows share a duplicated availability_event_id "
              f"({df['availability_event_id'].duplicated().sum():,} extra rows beyond first-seen). "
              "Expected 0 -- gold is supposed to arrive pre-deduped. This may mean the 21-Jul "
              "ServiceNow-conformance repoint changed incident_root_cause's duplicate profile. "
              "Deterministically deduping (keep first) so the run continues, but this should be "
              "investigated before trusting the resulting metrics.")
        print("!" * 74)
        df = df.drop_duplicates(subset=["availability_event_id"], keep="first")
    else:
        print(f"[guardrail] availability_event_id dedup check: clean (0 duplicates over {len(df):,} rows)")
    return df

def print_kpi_rule_fill(df):
    """Visibility-only (open item since the 17-Jul catalog validation): kpi_rule_id was 0% filled.
    Not used as a model feature today, so this doesn't block a run -- it's a debt flag so a future
    fix isn't silently missed."""
    for c in ("kpi_rule_id", "kpi_category_name"):
        if c in df.columns:
            fill = float(df[c].notna().mean())
            print(f"[data-quality] {c} fill rate: {fill:.4%}"
                  + ("  <-- still 0%, open item since 17-Jul catalog validation" if fill == 0 else ""))

def coerce_decimals(df):
    # parquet returns Spark DECIMAL as python Decimal (object dtype) -> float (else TypeErrors downstream)
    from decimal import Decimal
    for c in df.columns:
        if df[c].dtype == object:
            s = df[c].dropna()
            if len(s) and isinstance(s.iloc[0], Decimal):
                df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

# ----------------------------------------------------------------------------- OOS spine guard (v2)
SERIAL_UNIQUENESS = {}


def assert_oos_spine(df):
    """PS3 v2 trains on hardware OOS. is_chargeable is a CONTRACT classification applied
    after the physical event and is a strict SUBSET of OOS - filtering on it teaches the
    model the contract, not the device. This fails the run rather than training quietly on
    the wrong population."""
    if not CONFIG.get("ASSERT_OOS_SPINE", True):
        return df
    if "is_chargeable" in df.columns:
        ch = df["is_chargeable"].fillna(False).astype(bool)
        print(f"  [spine] OOS rows {len(df):,} | chargeable subset {int(ch.sum()):,} "
              f"({100*ch.mean():.1f}%) - NOT filtered, kept for reference only")
        if CONFIG.get("FORBID_CHARGEABLE_FILTER", True) and ch.all() and len(df) > 0:
            raise AssertionError(
                "every row is is_chargeable=True - the frame has already been filtered to the "
                "chargeable subset upstream. PS3 v2 must train on the full OOS population.")
    else:
        print("  [spine] is_chargeable absent - nothing to guard against")
    if "is_device_fault" in df.columns:
        print(f"  [spine] is_device_fault true on {int(df['is_device_fault'].fillna(False).sum()):,}")
    return df


# ----------------------------------------------------------------------------- feature engineering
def add_temporal(df):
    dt = pd.to_datetime(df["AE_START_DTM"], errors="coerce")
    df["ae_hour"] = dt.dt.hour.fillna(12).astype("float64")
    df["ae_dow"] = dt.dt.dayofweek.fillna(0).astype("float64")
    df["ae_month"] = dt.dt.month.fillna(1).astype("float64")
    return df

def add_facility_freq(df):
    if "FACILITY_ID" in df.columns:
        fc = df["FACILITY_ID"].astype(str).map(df["FACILITY_ID"].astype(str).value_counts())
        df["facility_incident_freq"] = fc.fillna(0).astype("float64")
    return df

# ----------------------------------------------------------------------------- enrichment joins (NEW 21-Jul-2026)
# Leakage-safe pattern (mirrors the PS5 v2 as-of enrichment lesson): every join attaches the most
# recent silver row STRICTLY BEFORE the incident's own transit_day, via pd.merge_asof(direction=
# "backward"). This guarantees nothing from the incident day itself (let alone after it) leaks in.
# Each source is independently CONFIG-gated and skips silently (prints + returns df unchanged) if the
# export isn't available yet -- same convention as the existing device_failures optional load.

def _norm_join_key(s):
    """Canonical string form of a join key, so float 1704.0 and text '1704' match."""
    out = s.astype(str).str.strip()
    out = out.str.replace(r"\.0$", "", regex=True)
    return out.str.upper()

def _resolve_date_col(src, candidates, label):
    """Pick the first present date column. FIX 26-Jul-2026: device_uptime_intervals
    has no `transit_day`, so the join died with KeyError: ['transit_day'] not in
    index and the run silently lost every uptime feature."""
    for c in candidates:
        if c in src.columns:
            return c
    print(f"  [enrich:{label}] no date column among {list(candidates)} "
          f"-- columns present: {sorted(map(str, src.columns))[:14]} -- skipping")
    return None

# `date_key` added 26-Jul-2026: that is what silver.device_uptime_intervals actually
# carries. The 26-Jul run listed its columns and no candidate matched, so every uptime
# feature was silently skipped.
_DATE_CANDIDATES = ("transit_day", "TRANSIT_DAY", "interval_date", "INTERVAL_DATE",
                    "event_date", "EVENT_DATE", "metric_date", "METRIC_DATE",
                    "date_key", "DATE_KEY", "business_date", "day", "DAY",
                    "dt", "DT", "date", "DATE")

def _asof_join(df, key_left_id, key_left_date, silver_df, key_right_id, key_right_date,
               cols, prefix, by_extra_left=None, by_extra_right=None):
    """Backward merge_asof of `cols` from silver_df onto df, one day-lag guaranteed by construction
    (silver_df's own date col is shifted +1 day before the asof match, so a same-day silver row
    can never attach to an incident on that same day)."""
    left = df[[key_left_id, key_left_date] + (by_extra_left or [])].copy()
    left["_asof_key"] = pd.to_datetime(left[key_left_date], errors="coerce")
    right = silver_df[[key_right_id, key_right_date] + cols + (by_extra_right or [])].copy()
    right["_asof_key"] = pd.to_datetime(right[key_right_date], errors="coerce") + pd.Timedelta(days=1)
    left = left.dropna(subset=["_asof_key"]).sort_values("_asof_key")
    right = right.dropna(subset=["_asof_key"]).sort_values("_asof_key")
    by_l = [key_left_id] + (by_extra_left or [])
    by_r = [key_right_id] + (by_extra_right or [])
    if len(by_l) != len(by_r):
        raise ValueError("_asof_join: left/right `by` key count mismatch")
    right = right.rename(columns=dict(zip(by_r, by_l)))
    # FIX 26-Jul-2026: the 26-Jul run lost station_network_daily to
    #   MergeError: incompatible merge keys [0] dtype('float64') and dtype('O')
    # FACILITY_ID arrives as float on one side (1704.0) and text on the other
    # ('1704'). merge_asof requires identical `by` dtypes, so normalise both to a
    # canonical string -- trailing '.0' stripped so 1704.0 and '1704' match.
    # FIX 26-Jul-2026 (round 2). Normalising only `left`/`right` fixed merge_asof but
    # left the FINAL df.merge below comparing df's original float64 FACILITY_ID against
    # the normalised string -- which is why the 26-Jul run swapped one error for
    # another ("You are trying to merge on float64 and object columns for key
    # 'FACILITY_ID'"). The normalised key now lives in its own column that BOTH sides
    # carry, so the original dtypes are never compared and never mutated.
    for _k in by_l:
        left[_k] = _norm_join_key(left[_k])
        right[_k] = _norm_join_key(right[_k])
    merged = pd.merge_asof(left, right, on="_asof_key", by=by_l, direction="backward",
                            tolerance=pd.Timedelta(days=CONFIG["ENRICH_ASOF_TOLERANCE_DAYS"]))
    ren = {c: f"{prefix}{c}" for c in cols}
    merged = merged.rename(columns=ren)
    merged["_jk"] = _norm_join_key(merged[key_left_id])
    merged["_jd"] = pd.to_datetime(merged[key_left_date], errors="coerce")
    out = df.copy()
    out["_jk"] = _norm_join_key(out[key_left_id])
    out["_jd"] = pd.to_datetime(out[key_left_date], errors="coerce")
    keep = ["_jk", "_jd"] + list(ren.values())
    out = out.merge(merged[keep].drop_duplicates(subset=["_jk", "_jd"]),
                    on=["_jk", "_jd"], how="left")
    return out.drop(columns=["_jk", "_jd"])

def enrich_usage_lifecycle(df):
    """silver.usage_lifecycle_daily -- wear/recency covariates (item B3 in PS3_Enrichment_Recommendations.md).
    A device that's failed 4x in the last 30 days and hasn't seen maintenance in 90 is a different
    severity risk than one on day 3 of service -- these are honest, real trailing-state features."""
    if not CONFIG.get("ENRICH_USAGE"): return df
    cols = ["days_since_last_failure", "days_since_last_maintenance", "failure_count_30d",
            "cumulative_failure_count", "cumulative_outage_min", "days_in_service"]
    src = load_table(CONFIG["USAGE_LIFECYCLE_TABLE"], f"{CONFIG['SILVER_PREFIX']}/usage_lifecycle_daily",
                     required=False)
    if src is None or "DEVICE_ID" not in src.columns:
        print("  [enrich:usage_lifecycle_daily] unavailable -- skipping (silent, per ENRICH_USAGE guard)")
        return df
    src = src.rename(columns={"DEVICE_ID": "device_id"})
    have = [c for c in cols if c in src.columns]
    if not have:
        print("  [enrich:usage_lifecycle_daily] expected columns not found -- skipping"); return df
    d2 = _asof_join(df, "device_id", "_ae_date", src, "device_id", "transit_day", have, "enr_")
    print(f"  [enrich:usage_lifecycle_daily] joined {len(have)} cols (as-of, 1-day lag)")
    return d2

def enrich_station_network(df):
    """silver.station_network_daily -- prior-day station co-failure stress (item B4). A device
    failing during a station-wide meltdown is a different severity story than an isolated failure."""
    if not CONFIG.get("ENRICH_STATION"): return df
    cols = ["devices_failed", "total_failure_events", "avg_downtime_minutes",
            "is_coordinated_failure", "is_major_station_event"]
    src = load_table(CONFIG["STATION_NETWORK_TABLE"], f"{CONFIG['SILVER_PREFIX']}/station_network_daily",
                     required=False)
    if src is None or "FACILITY_ID" not in src.columns:
        print("  [enrich:station_network_daily] unavailable -- skipping"); return df
    have = [c for c in cols if c in src.columns]
    if not have or "device_category" not in src.columns or "mars_device_category" not in df.columns:
        print("  [enrich:station_network_daily] expected columns not found -- skipping"); return df
    src = src.rename(columns={"device_category": "mars_device_category"})
    _dc = _resolve_date_col(src, _DATE_CANDIDATES, "station_network_daily")
    if _dc is None: return df
    d2 = _asof_join(df, "FACILITY_ID", "_ae_date", src, "FACILITY_ID", _dc, have, "enr_station_",
                    by_extra_left=["mars_device_category"], by_extra_right=["mars_device_category"])
    for c in [f"enr_station_{x}" for x in ("is_coordinated_failure", "is_major_station_event") if f"enr_station_{x}" in d2.columns]:
        d2[c] = d2[c].astype("float64")
    print(f"  [enrich:station_network_daily] joined {len(have)} cols (as-of, 1-day lag, by facility+category)")
    return d2

def enrich_metric_daily(df):
    """silver.metric_daily (M401) -- pre-incident tap-timing + comms degradation (item B5, the
    doc's own words: 'early functional-severity signal for TVM/GATE'). Slow taps + comms-lost
    spikes in the days before a failure are a genuine functional precursor, not a leak (M401
    aggregates end at the PRIOR day, never the incident day)."""
    if not CONFIG.get("ENRICH_METRIC"): return df
    cols = ["m401_avg_txn_time_ms", "m401_p95_txn_time_ms", "m401_p99_txn_time_ms",
            "m401_slow_tap_pct", "m401_rolling_7d_avg_ms", "m401_z_score_vs_28d",
            "volume_drop_flag", "comms_total_count", "comms_event_flag"]
    src = load_table(CONFIG["METRIC_DAILY_TABLE"], f"{CONFIG['SILVER_PREFIX']}/metric_daily", required=False)
    if src is None or "DEVICE_ID" not in src.columns:
        print("  [enrich:metric_daily] unavailable -- skipping"); return df
    src = src.rename(columns={"DEVICE_ID": "device_id"})
    have = [c for c in cols if c in src.columns]
    if not have:
        print("  [enrich:metric_daily] expected columns not found -- skipping"); return df
    d2 = _asof_join(df, "device_id", "_ae_date", src, "device_id", "transit_day", have, "enr_")
    for c in ("enr_volume_drop_flag", "enr_comms_event_flag"):
        if c in d2.columns: d2[c] = d2[c].astype("float64")
    print(f"  [enrich:metric_daily] joined {len(have)} cols (as-of, 1-day lag)")
    return d2

def enrich_uptime_intervals(df):
    """silver.device_uptime_intervals -- silence/heartbeat precursor (item new-21Jul; same table +
    same confirmed column names the PS1 AUC-boost patch validated in production). A device going
    quiet (is_silent_device, rising hours_since_last_hb) ahead of a hard failure is exactly the kind
    of leading indicator PS1 found lift from -- worth testing on PS3 severity too."""
    if not CONFIG.get("ENRICH_UPTIME"): return df
    cols = ["uptime_pct", "downtime_hours", "hours_since_last_hb", "is_silent_device",
            "distinct_message_types", "total_msg_count"]
    src = load_table(CONFIG["UPTIME_INTERVALS_TABLE"], f"{CONFIG['SILVER_PREFIX']}/device_uptime_intervals",
                     required=False)
    if src is None:
        print("  [enrich:device_uptime_intervals] unavailable -- skipping"); return df
    if "DEVICE_ID" in src.columns:
        src = src.rename(columns={"DEVICE_ID": "device_id"})
    if "device_id" not in src.columns:
        print("  [enrich:device_uptime_intervals] no device_id column -- skipping"); return df
    # FIX 26-Jul-2026: the expected column names never existed on this table. The real
    # schema is UPTIME_SECONDS / DOWNTIME_SECONDS / TOTAL_SECONDS / LAST_HEART_BEAT_DTM /
    # COMPLETE_FLAG, so `have` came back empty and the source was skipped every run.
    # Derive the intended quantities from what is actually there.
    src = src.copy()
    if "uptime_pct" not in src.columns and {"UPTIME_SECONDS", "TOTAL_SECONDS"} <= set(src.columns):
        tot = pd.to_numeric(src["TOTAL_SECONDS"], errors="coerce")
        up = pd.to_numeric(src["UPTIME_SECONDS"], errors="coerce")
        src["uptime_pct"] = (up / tot.where(tot > 0)).clip(0, 1)
    if "downtime_hours" not in src.columns and "DOWNTIME_SECONDS" in src.columns:
        src["downtime_hours"] = pd.to_numeric(src["DOWNTIME_SECONDS"], errors="coerce") / 3600.0
    if "is_silent_device" not in src.columns and "COMPLETE_FLAG" in src.columns:
        # COMPLETE_FLAG false == the interval has no closing heartbeat, i.e. the device
        # went quiet. That is the precursor PS1 found lift from.
        cf = src["COMPLETE_FLAG"]
        src["is_silent_device"] = (~cf.astype(str).str.strip().str.lower()
                                   .isin(["true", "t", "1", "1.0", "y", "yes"])).astype(float)
    have = [c for c in cols if c in src.columns]
    if not have:
        print("  [enrich:device_uptime_intervals] expected columns not found -- skipping"); return df
    _dc = _resolve_date_col(src, _DATE_CANDIDATES, "device_uptime_intervals")
    if _dc is None: return df
    d2 = _asof_join(df, "device_id", "_ae_date", src, "device_id", _dc, have, "enr_")
    if "enr_is_silent_device" in d2.columns: d2["enr_is_silent_device"] = d2["enr_is_silent_device"].astype("float64")
    print(f"  [enrich:device_uptime_intervals] joined {len(have)} cols (as-of, 1-day lag)")
    return d2

def enrich_all(df):
    """Run every CONFIG-gated enrichment. Each is independently guarded; a missing/broken source
    degrades to 'skip that one source' rather than failing the whole run."""
    if not CONFIG.get("ENRICH_SOURCES"): return df
    print("[enrich] joining leakage-safe as-of features (1-day lag, per-source guarded)...")
    for fn in (enrich_usage_lifecycle, enrich_station_network, enrich_metric_daily, enrich_uptime_intervals):
        try:
            df = fn(df)
        except Exception as e:
            print(f"  [enrich:{fn.__name__}] FAILED ({type(e).__name__}: {str(e)[:80]}) -- continuing without it")
    return df

def probe_servicenow_conformance():
    """Diagnostic-only (CONFIG['PROBE_SERVICENOW_CONFORMANCE']): reports whether the 21-Jul-2026
    servicenow_incident_conformed repoint has reached silver.incident_root_cause yet, using its
    own from_svn_stage/from_cta_sn_mirror boolean columns. Never touches training -- this exists so
    the moment the true 9-class ServiceNow root cause unblocks (18-Jul verdict: SVN_STAGE was 0 rows)
    is visible in every run's console log instead of silently missed."""
    if not CONFIG.get("PROBE_SERVICENOW_CONFORMANCE"): return
    try:
        irc = load_table(CONFIG["INCIDENT_ROOT_CAUSE_TABLE"], CONFIG["INCIDENT_ROOT_CAUSE_PARQUET"], required=False)
        if irc is None or not len(irc):
            print("[probe:servicenow] silver.incident_root_cause not reachable from this environment -- skip"); return
        n = len(irc)
        for col in ("from_svn_stage", "from_cta_sn_mirror"):
            if col in irc.columns:
                rate = float(pd.to_numeric(irc[col], errors="coerce").fillna(0).mean())
                print(f"[probe:servicenow] {col} fill/true-rate = {rate:.4f} over {n:,} rows"
                      + ("  <-- was 0 at the 18-Jul verdict; a true ServiceNow root-cause head may now be viable"
                         if col == "from_svn_stage" and rate > 0 else ""))
        dup = irc["availability_event_id"].duplicated().sum() if "availability_event_id" in irc.columns else None
        if dup is not None:
            print(f"[probe:servicenow] incident_root_cause duplicate availability_event_id rows: {dup:,} "
                  f"(18-Jul catalog snapshot flagged 670 -- PS3 gold's own load-time assertion below is the real gate)")
    except Exception as e:
        print(f"[probe:servicenow] skipped ({type(e).__name__}: {str(e)[:80]})")

BASE_STRUCT = ["events_24h_prior", "oos_onsets_24h", "bhu_events_24h", "chu_events_24h",
               "printer_events_24h", "gate_mech_events_24h", "csc_reader_events_24h",
               "comms_events_24h", "oos_onsets_7d_prior", "events_7d_prior", "component_age_days",
               "ae_hour", "ae_dow", "ae_month", "facility_incident_freq"]

def build_feature_list(df):
    feats = [c for c in BASE_STRUCT if c in df.columns]
    for m in CONFIG["MONITORED_FEATURES"]:
        if m in df.columns: feats.append(m)
    # auto-discovered ServiceNow-conformance candidates (NEW 21-Jul-2026): only added if the
    # 21-Jul conformance repoint has actually reached gold.device_ps3_incident -- never assumed.
    # 26-Jul-2026: this line used to claim "numeric coercion happens downstream in
    # train_head's .astype('float64')". It does not -- .astype() raises on a
    # non-numeric string, and sn_category holds 32-char hashes, which killed the
    # 26-Jul run. Coercion is now real and explicit in coerce_feature_matrix().
    for c in CONFIG.get("CANDIDATE_CONFORMANCE_FEATURES", []):
        if c in df.columns and c not in feats:
            feats.append(c)
            print(f"  [feature:auto-discovered] {c} present on gold -- routing through the leakage gate")
    if CONFIG["USE_CONCURRENT_FEATURES"]:
        feats += [c for c in CONFIG["CONCURRENT_FEATURES"] if c in df.columns]
    # enrichment cols added by joins (prefix enr_)
    feats += [c for c in df.columns if c.startswith("enr_")]
    # never allow a leak/target column in features
    feats = [c for c in feats if c not in set(CONFIG["EXPLICIT_LEAK"])]
    return list(dict.fromkeys(feats))    # de-dup, keep order

# ----------------------------------------------------------------------------- leakage scan
def leakage_scan(df, target_col, feats, split_date):
    tr = df[df["_ae_date"] <= split_date]; va = df[df["_ae_date"] > split_date]
    if va[target_col].nunique() < 2 or len(va) < 20:
        print("[leak] holdout too small for solo-AUC scan; skipping"); return pd.Series(dtype=float), []
    top = va[target_col].value_counts().index[0]
    ybin = (va[target_col] == top).astype(int)
    def solo(col):
        a = pd.to_numeric(tr[col], errors="coerce"); b = pd.to_numeric(va[col], errors="coerce")
        med = a.median()
        a = a.fillna(med); b = b.fillna(med)
        if a.nunique() < 2: return np.nan
        try:
            auc = roc_auc_score(ybin, b); return max(auc, 1 - auc)
        except Exception: return np.nan
    scan = pd.Series({c: solo(c) for c in feats}).dropna().sort_values(ascending=False)
    leaky = scan[scan > CONFIG["LEAKAGE_AUC_THRESHOLD"]].index.tolist()
    print("top solo-AUC features (one-vs-rest on majority class):")
    print(scan.head(10).round(3).to_string())
    print(f"leakage gate {CONFIG['LEAKAGE_AUC_THRESHOLD']} -> {'CLEAN' if not leaky else 'DROP '+str(leaky)}")
    return scan, leaky

# ----------------------------------------------------------------------------- target prep
def prep_target(df, target_col):
    y = df[target_col].astype(str).fillna("UNKNOWN")
    vc = y.value_counts()
    rare = vc[vc < CONFIG["MIN_CLASS_COUNT"]].index.tolist()
    if rare:
        y = y.where(~y.isin(rare), "OTHER")
        print(f"[target:{target_col}] collapsed {len(rare)} rare class(es) (<{CONFIG['MIN_CLASS_COUNT']}) -> OTHER: {rare}")
    return y

# ----------------------------------------------------------------------------- model factory
def make_prep(feats):
    return ColumnTransformer([("num", Pipeline([("imp", SimpleImputer(strategy="median")),
                                                ("sc", StandardScaler())]), feats)], remainder="drop")

def builders(n_classes, spw=None):
    b = {}
    b["LogReg"] = lambda: LogisticRegression(max_iter=2000, class_weight="balanced")
    b["RandomForest"] = lambda: RandomForestClassifier(n_estimators=300, class_weight="balanced_subsample",
                                                       n_jobs=-1, random_state=RS)
    b["HistGBM"] = lambda: HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06, random_state=RS)
    if xgb is not None:
        b["XGBoost"] = lambda: xgb.XGBClassifier(
            n_estimators=350, max_depth=6, learning_rate=0.06, subsample=0.85, colsample_bytree=0.8,
            objective="multi:softprob", num_class=n_classes, eval_metric="mlogloss",
            tree_method="hist", random_state=RS, n_jobs=-1)
    if lgb is not None:
        b["LightGBM"] = lambda: lgb.LGBMClassifier(
            n_estimators=400, num_leaves=48, learning_rate=0.05, subsample=0.85, colsample_bytree=0.8,
            objective="multiclass", num_class=n_classes, class_weight="balanced", random_state=RS, n_jobs=-1, verbose=-1)
    if catb is not None:
        b["CatBoost"] = lambda: catb.CatBoostClassifier(iterations=400, depth=6, learning_rate=0.06,
                                                        loss_function="MultiClass", verbose=0, random_seed=RS)
    return b

def sample_weight(y):
    vc = pd.Series(y).value_counts()
    w = {k: len(y) / (len(vc) * v) for k, v in vc.items()}
    return pd.Series(y).map(w).values

def fit_one(model, prep, X, y):
    Xt = prep.transform(X)
    try:
        model.fit(Xt, y, sample_weight=sample_weight(y))
    except TypeError:
        model.fit(Xt, y)
    return model

def _xgb_task(n_cls):
    """XGBoost task params for the class count. Binary must NOT use multi:softprob
    with num_class=2 -- the sklearn wrapper then returns a 2-column indicator from
    .predict(), which f1_score rejects."""
    if int(n_cls) <= 2:
        return {"objective": "binary:logistic", "eval_metric": "logloss"}
    return {"objective": "multi:softprob", "num_class": int(n_cls), "eval_metric": "mlogloss"}

def optuna_challenger(prep, Xtr, ytr, n_cls):
    """Optuna-tuned XGBoost challenger (matches v3's xgb_optuna). Honest temporal tune-split of TRAIN."""
    if optuna_mod is None or xgb is None:
        return None
    Xt = prep.transform(Xtr); y = np.asarray(ytr)
    cap = CONFIG.get("TUNE_SAMPLE_ROWS")
    if cap and len(Xt) > cap:
        Xt, y = Xt[-cap:], y[-cap:]                       # most-recent rows (temporal)
    cut = int(len(Xt) * 0.8)
    Xa, Xb, ya, yb = Xt[:cut], Xt[cut:], y[:cut], y[cut:]
    if len(np.unique(ya)) < 2 or len(np.unique(yb)) < 2:
        return None
    optuna_mod.logging.set_verbosity(optuna_mod.logging.WARNING)

    def objective(t):
        p = dict(n_estimators=t.suggest_int("n_estimators", 200, 500, step=100),
                 max_depth=t.suggest_int("max_depth", 3, 9),
                 learning_rate=t.suggest_float("learning_rate", 0.02, 0.2, log=True),
                 subsample=t.suggest_float("subsample", 0.6, 1.0),
                 colsample_bytree=t.suggest_float("colsample_bytree", 0.6, 1.0))
        # FIX 26-Jul-2026: multi:softprob with num_class=2 made the sklearn wrapper
        # return a 2-column indicator from .predict(), so f1_score raised
        # "Classification metrics can't handle a mix of binary and
        # multilabel-indicator targets" and every GATE severity trial failed.
        m = xgb.XGBClassifier(**_xgb_task(n_cls), tree_method="hist",
                              random_state=RS, n_jobs=-1, **p)
        m.fit(Xa, ya)
        pred = m.predict(Xb)
        pred = np.asarray(pred)
        if pred.ndim > 1:                     # belt and braces across xgboost versions
            pred = pred.argmax(axis=1)
        return f1_score(yb, pred, average="macro", zero_division=0)

    study = optuna_mod.create_study(direction="maximize",
                                    sampler=optuna_mod.samplers.TPESampler(seed=RS))
    study.optimize(objective, n_trials=CONFIG["N_OPTUNA_TRIALS"], show_progress_bar=False)
    best = xgb.XGBClassifier(**_xgb_task(n_cls), tree_method="hist",
                             random_state=RS, n_jobs=-1, **study.best_params)
    best.fit(Xt, y)                                       # refit on the full (capped) train
    return best, float(study.best_value), study.best_params


# ----------------------------------------------------------------------------- feature coercion
# FIX 26-Jul-2026. The 26-Jul live run died here:
#     ValueError: could not convert string to float: '49118fdf47e6855c2ca1a792e36d43b3'
#   at  Xtr = tr[feats].astype("float64").
#
# Cause: build_feature_list() appends CANDIDATE_CONFORMANCE_FEATURES (sn_category,
# from_svn_stage, from_cta_sn_mirror) whenever they appear on gold, on the stated
# assumption that "numeric coercion happens downstream in train_head's
# .astype('float64')". That assumption was wrong -- .astype() does not coerce, it
# raises. sn_category turned out to hold 32-char hashes, so the cast exploded.
#
# It slipped past the leakage gate because leakage_scan()'s solo() does
# pd.to_numeric(..., errors="coerce") -> all-NaN -> nunique() < 2 -> returns NaN ->
# .dropna() removes it from the scan entirely. A column that cannot be scanned was
# therefore treated as a column that passed.
#
# This function makes the coercion real and, crucially, EXPLICIT about what it did:
#   numeric / bool        -> float64
#   object that parses    -> float64  (>=99% of non-null values parse)
#   boolean-like strings  -> {0.0, 1.0}
#   low-cardinality text  -> ordinal codes, categories fit on TRAIN ONLY, unseen -> -1
#   identifier-like text  -> DROPPED (near-unique or very high cardinality: an id is
#                            not a feature, and a near-unique column is a leak risk)
#   all-null / constant   -> DROPPED
# Every decision is printed, so a dropped feature is never silently lost.
# FIX 26-Jul-2026: this was 50, which dropped sn_event_code_id -- 64 distinct values
# over 26,306 TVM rows, i.e. 0.2% unique and plainly a category, not an identifier.
# It was the single strongest driver of the 21-Jul run (mean |SHAP| 0.7916), and
# losing it took TVM severity macro-F1 from 0.7648 down to 0.5383. The uniqueness
# RATIO is the real identifier test; the absolute cap only guards against one-hot
# explosions, so it belongs much higher.
MAX_CATEGORICAL_CARD = 200     # above this, ordinal codes stop being meaningful
ID_LIKE_UNIQUE_RATIO = 0.20    # nunique/n_rows above this => identifier-like

_BOOL_TRUE  = {"true", "t", "yes", "y", "1", "1.0"}
_BOOL_FALSE = {"false", "f", "no", "n", "0", "0.0"}

def coerce_feature_matrix(tr, te, feats, head_name=""):
    """Return (Xtr, Xte, feats_used, report). Never raises on a bad dtype."""
    Xtr, Xte, used, report = {}, {}, [], {"numeric": [], "bool": [], "encoded": {}, "dropped": {}}
    n = max(len(tr), 1)
    for c in feats:
        s_tr, s_te = tr[c], te[c]
        if pd.api.types.is_bool_dtype(s_tr):
            Xtr[c] = s_tr.astype("float64"); Xte[c] = s_te.astype("float64")
            used.append(c); report["bool"].append(c); continue
        if pd.api.types.is_numeric_dtype(s_tr):
            Xtr[c] = s_tr.astype("float64"); Xte[c] = s_te.astype("float64")
            used.append(c); report["numeric"].append(c); continue
        if pd.api.types.is_datetime64_any_dtype(s_tr):
            report["dropped"][c] = "datetime -- not a model feature"; continue

        nn = s_tr.notna().sum()
        if nn == 0:
            report["dropped"][c] = "all-null on train"; continue
        num_tr = pd.to_numeric(s_tr, errors="coerce")
        if num_tr.notna().sum() / nn >= 0.99:
            Xtr[c] = num_tr.astype("float64")
            Xte[c] = pd.to_numeric(s_te, errors="coerce").astype("float64")
            used.append(c); report["numeric"].append(c); continue

        vals = set(s_tr.dropna().astype(str).str.strip().str.lower().unique())
        if len(vals) < 2:
            # constant text (e.g. from_svn_stage == 'false' on every row): carries no
            # information and would otherwise survive the boolean branch below.
            report["dropped"][c] = f"constant on train (single value {next(iter(vals), None)!r})"
            continue
        if vals <= (_BOOL_TRUE | _BOOL_FALSE):
            # Persist the boolean map in `encoders` exactly like a categorical, so
            # apply_feature_coercion() replays it at score time. Storing only a
            # "this was boolean" note made score-time to_numeric() return NaN where
            # fit time had produced 1.0/0.0 -- a silent train/serve skew.
            code = {v: (1.0 if str(v).strip().lower() in _BOOL_TRUE else 0.0)
                    for v in _as_key(s_tr).dropna().unique()}
            Xtr[c] = _as_key(s_tr).map(code).astype("float64")
            Xte[c] = _as_key(s_te).map(code).astype("float64")
            used.append(c); report["bool"].append(c); report["encoded"][c] = code; continue

        card = s_tr.nunique(dropna=True)
        if card < 2:
            report["dropped"][c] = f"constant on train (cardinality {card})"; continue
        if card > MAX_CATEGORICAL_CARD or (card / n) > ID_LIKE_UNIQUE_RATIO:
            ex = str(s_tr.dropna().iloc[0])[:40]
            report["dropped"][c] = (f"identifier-like text: {card:,} distinct over {n:,} rows "
                                    f"({card/n:.1%} unique), e.g. {ex!r}")
            continue
        cats = sorted(_as_key(s_tr).dropna().unique())              # TRAIN ONLY -- no test leakage
        code = {v: float(i) for i, v in enumerate(cats)}
        Xtr[c] = _as_key(s_tr).map(code).fillna(-1.0)
        Xte[c] = _as_key(s_te).map(code).fillna(-1.0)               # unseen at test -> -1
        used.append(c); report["encoded"][c] = code                  # persisted with the bundle

    if report["encoded"]:
        print(f"  [dtype] ordinal-encoded {len(report['encoded'])} categorical feature(s) "
              f"(fit on train only, unseen -> -1): "
              f"{ {k: len(v) for k, v in report['encoded'].items()} }")
    if report["dropped"]:
        print(f"  [dtype] dropped {len(report['dropped'])} non-numeric feature(s):")
        for k, v in report["dropped"].items():
            print(f"            {k}: {v}")
    if not used:
        raise ValueError(f"[{head_name}] no usable features after dtype coercion "
                         f"(started with {len(feats)}). Dropped: {report['dropped']}")
    return (pd.DataFrame(Xtr, index=tr.index)[used],
            pd.DataFrame(Xte, index=te.index)[used], used, report)


def _as_key(s):
    """Canonical string form used for BOTH fit-time encoding and score-time replay.
    Same normalisation on both sides or the codes silently diverge."""
    return s.astype(str).str.strip()

def apply_feature_coercion(df, feats, encoders):
    """Inference-time twin of coerce_feature_matrix. Replays the TRAINING encoding
    (`encoders` = {col: {value: code}} persisted in the head bundle) so a category
    maps to the same integer at score time as it did at fit time. A value unseen in
    training -> -1, exactly as at fit time. Without this, score_new_data() would hit
    the same 'could not convert string to float' the 26-Jul run died on."""
    encoders = encoders or {}
    out = {}
    for c in feats:
        s_ = df[c] if c in df.columns else pd.Series(np.nan, index=df.index)
        if c in encoders:
            out[c] = _as_key(s_).map(encoders[c]).fillna(-1.0).astype("float64")
        else:
            out[c] = pd.to_numeric(s_, errors="coerce").astype("float64")
    return pd.DataFrame(out, index=df.index)[feats]

# ----------------------------------------------------------------------------- head trainer
def train_head(df, feats, target_col, head_name, cat, out, split_date):
    print(f"\n----- {head_name} head ({target_col}) -----")
    y_all = prep_target(df, target_col)
    classes = sorted(y_all.unique().tolist())
    n_cls = len(classes)
    if n_cls < 2:
        print(f"[{head_name}] only {n_cls} class present -> cannot model; skipping head"); return None
    le = LabelEncoder().fit(y_all)
    df = df.copy(); df["_y"] = le.transform(y_all)

    tr = df[df["_ae_date"] <= split_date].copy()
    embargo = split_date + pd.Timedelta(days=CONFIG["EMBARGO_DAYS"])
    te = df[df["_ae_date"] > embargo].copy()
    if len(te) < 20 or len(tr) < 50:
        print(f"[{head_name}] split too small (train {len(tr)}, test {len(te)}) -> skipping head"); return None
    ytr, yte = tr["_y"].values, te["_y"].values
    # was: tr[feats].astype("float64") -- raised on any non-numeric feature. See
    # coerce_feature_matrix() above for why that could happen and what it does now.
    Xtr, Xte, feats, _dtype_report = coerce_feature_matrix(tr, te, feats, head_name)
    prep = make_prep(feats).fit(Xtr)

    LBL = np.arange(n_cls)                       # full class index set -> every metric aligns to le.classes_
    Yte_oh = label_binarize(yte, classes=LBL)    # one-vs-rest one-hot for AUC / PR-AUC
    if n_cls == 2 and Yte_oh.shape[1] == 1:
        Yte_oh = np.hstack([1 - Yte_oh, Yte_oh])
    rows = []; fitted = {}; proba_of = {}
    for name, mk in builders(n_cls).items():
        try:
            t0 = time.time(); m = fit_one(mk(), prep, Xtr, ytr)
            proba = m.predict_proba(prep.transform(Xte))
            pred = proba.argmax(axis=1)
            f1m = f1_score(yte, pred, labels=LBL, average="macro", zero_division=0)
            f1w = f1_score(yte, pred, labels=LBL, average="weighted", zero_division=0)
            acc = accuracy_score(yte, pred)
            try:
                auc = (roc_auc_score(yte, proba, average="macro", multi_class="ovr", labels=LBL)
                       if n_cls > 2 else roc_auc_score(yte, proba[:, 1]))
            except Exception: auc = np.nan
            try: prauc = average_precision_score(Yte_oh, proba, average="macro")
            except Exception: prauc = np.nan
            rows.append({"model": name, "f1_macro": f1m, "f1_weighted": f1w, "accuracy": acc,
                         "auc_macro_ovr": auc, "pr_auc_macro": prauc, "fit_s": round(time.time() - t0, 1)})
            fitted[name] = m; proba_of[name] = proba
            print(f"  {name:13s} F1-macro {f1m:.4f} | F1-w {f1w:.4f} | acc {acc:.4f} | "
                  f"AUC {auc if not np.isnan(auc) else float('nan'):.4f} | PR-AUC {prauc if not np.isnan(prauc) else float('nan'):.4f}")
        except Exception as e:
            print(f"  {name:13s} FAILED: {type(e).__name__}: {str(e)[:70]}")
    # Optuna-tuned XGBoost challenger (v3 parity: xgb_optuna) -> flows through all downstream artifacts
    if CONFIG.get("RUN_OPTUNA"):
        try:
            res = optuna_challenger(prep, Xtr, ytr, n_cls)
            if res is not None:
                best, bval, bparams = res
                proba = best.predict_proba(prep.transform(Xte)); pred = proba.argmax(1)
                try: auc = (roc_auc_score(yte, proba, average="macro", multi_class="ovr", labels=LBL)
                            if n_cls > 2 else roc_auc_score(yte, proba[:, 1]))
                except Exception: auc = np.nan
                try: prauc = average_precision_score(Yte_oh, proba, average="macro")
                except Exception: prauc = np.nan
                rows.append({"model": "XGBoost_optuna",
                             "f1_macro": f1_score(yte, pred, labels=LBL, average="macro", zero_division=0),
                             "f1_weighted": f1_score(yte, pred, labels=LBL, average="weighted", zero_division=0),
                             "accuracy": accuracy_score(yte, pred), "auc_macro_ovr": auc,
                             "pr_auc_macro": prauc, "fit_s": np.nan})
                fitted["XGBoost_optuna"] = best; proba_of["XGBoost_optuna"] = proba
                json.dump(bparams, open(out / f"{cat.lower()}_{head_name}_optuna_best_params.json", "w"), indent=2)
                print(f"  XGBoost_optuna F1-macro {rows[-1]['f1_macro']:.4f} | tuned {CONFIG['N_OPTUNA_TRIALS']} trials (val {bval:.4f})")
        except Exception as e:
            print(f"  [optuna] skipped: {type(e).__name__}: {str(e)[:70]}")
    if not rows:
        print(f"[{head_name}] no model trained"); return None
    lb = pd.DataFrame(rows).sort_values("f1_macro", ascending=False).reset_index(drop=True)
    lb.to_csv(out / f"{cat.lower()}_{head_name}_leaderboard.csv", index=False)
    champ = lb.iloc[0]["model"]; champ_f1 = float(lb.iloc[0]["f1_macro"])
    floor = CONFIG["SEVERITY_F1_FLOOR"] if head_name == "severity" else CONFIG["ROOTCAUSE_F1_FLOOR"]
    gate_pass = champ_f1 >= floor
    print(f"  champion = {champ} | macro-F1 {champ_f1:.4f} | gate {floor:.2f} -> {'PASS' if gate_pass else 'FAIL (review)'}")

    LBL = np.arange(n_cls); names0 = [str(x) for x in le.classes_]
    # target-class distribution (v3 'Failure_Level_Distribution')
    try:
        vc = pd.Series(ytr).map(dict(enumerate(names0))).value_counts()
        ax = vc[::-1].plot(kind="barh", figsize=(7, 4), color="#A9D18E")
        ax.set_title(f"PS3 {cat} {head_name} -- class distribution (train)"); plt.tight_layout()
        plt.savefig(out / f"{cat.lower()}_{head_name}_class_distribution.png", dpi=110); plt.close()
    except Exception: plt.close("all")
    # per-model confusion + ROC (matches v3's xgb_mc/lgb_mc/cb_mc set) -- champion-quality artifacts for all
    for mname, mdl in fitted.items():
        try:
            pp = proba_of[mname]; pd_ = pp.argmax(1)
            _plot_confusion(confusion_matrix(yte, pd_, labels=LBL), names0,
                            f"PS3 {cat} {head_name} -- {mname} confusion (test)",
                            out / f"{cat.lower()}_{head_name}_{mname}_confusion.png")
            _plot_roc_pr(yte, pp, names0, LBL, cat, head_name, out, tag=mname)
            fi = getattr(mdl, "feature_importances_", None)
            if fi is not None:
                s = pd.Series(fi, index=feats).sort_values(ascending=False).head(20)
                ax = s[::-1].plot(kind="barh", figsize=(6.5, 5.5), color="#F4B183")
                ax.set_title(f"PS3 {cat} {head_name} -- {mname} feature importance"); plt.tight_layout()
                plt.savefig(out / f"{cat.lower()}_{head_name}_{mname}_featimp.png", dpi=110); plt.close()
        except Exception: plt.close("all")

    # ---- exhaustive champion evaluation (labels pinned to LBL so shapes always align) ----
    model = fitted[champ]; proba = proba_of[champ]; pred = proba.argmax(axis=1)
    names = [str(x) for x in le.classes_]
    cm = confusion_matrix(yte, pred, labels=LBL)                     # FIX: always n_cls x n_cls
    _plot_confusion(cm, names, f"PS3 {cat} {head_name} -- confusion (test)",
                    out / f"{cat.lower()}_{head_name}_confusion.png")
    per_f1 = f1_score(yte, pred, labels=LBL, average=None, zero_division=0)
    per_prec = precision_score(yte, pred, labels=LBL, average=None, zero_division=0)
    per_rec = recall_score(yte, pred, labels=LBL, average=None, zero_division=0)
    support = np.array([(yte == k).sum() for k in LBL])
    _plot_perclass_f1(names, per_f1, f"PS3 {cat} {head_name} -- per-class F1",
                      out / f"{cat.lower()}_{head_name}_perclass_f1.png")
    _plot_roc_pr(yte, proba, names, LBL, cat, head_name, out)        # ROC + PR curves (OVR)
    _plot_calibration(yte, proba, pred, cat, head_name, out)         # reliability curve
    _write_report_txt(yte, pred, names, LBL, cat, head_name, champ, lb, floor, gate_pass, out)

    # extra rigorous metrics (beyond the v3 set)
    def _safe(fn, *a, **k):
        try: return round(float(fn(*a, **k)), 4)
        except Exception: return None
    bal_acc = _safe(balanced_accuracy_score, yte, pred)
    kappa = _safe(cohen_kappa_score, yte, pred)
    mcc = _safe(matthews_corrcoef, yte, pred)
    ll = _safe(log_loss, yte, proba, labels=LBL)

    # SHAP suite always renders on a supported tree model (proxy explainer if the champion is linear)
    _SHAP_OK = ("RandomForest", "XGBoost", "LightGBM", "CatBoost")
    shap_name = champ if champ in _SHAP_OK else next((m for m in lb["model"].tolist() if m in _SHAP_OK), champ)
    shap_imp = _shap_importance(fitted.get(shap_name, model), prep, Xte, feats, cat, head_name, out, names)

    summary = {"device_category": cat, "head": head_name, "target": target_col,
               "champion": champ, "classes": names, "n_classes": int(n_cls),
               "test_f1_macro": round(champ_f1, 4),
               "test_f1_weighted": round(float(lb.iloc[0]["f1_weighted"]), 4),
               "test_accuracy": round(float(lb.iloc[0]["accuracy"]), 4),
               "test_balanced_accuracy": bal_acc,
               "test_auc_macro_ovr": (round(float(lb.iloc[0]["auc_macro_ovr"]), 4)
                                      if not pd.isna(lb.iloc[0]["auc_macro_ovr"]) else None),
               "test_pr_auc_macro": (round(float(lb.iloc[0]["pr_auc_macro"]), 4)
                                     if not pd.isna(lb.iloc[0]["pr_auc_macro"]) else None),
               "test_cohen_kappa": kappa, "test_mcc": mcc, "test_log_loss": ll,
               "macro_f1_floor": floor, "gate_pass": bool(gate_pass),
               # ---- constant-predictor detector (added 26-Jul-2026) -------------
               # A model that outputs the majority class unconditionally scores
               # macro-F1 = (1/k) * 2p/(1+p), whose supremum as p->1 is 1/k. So
               # macro-F1 <= 1/k proves the model carries no class-discriminating
               # information, regardless of how high its accuracy looks. The 21-Jul
               # GATE severity head is the worked case: accuracy 0.99112, macro-F1
               # 0.49777, balanced accuracy 0.5000, kappa 0.0, MCC 0.0 -- and five
               # different algorithms reported identical figures to 5 decimals,
               # which independent learners cannot do unless all are emitting the
               # same constant. `constant_predictor` makes that visible in the run
               # summary and in RDS instead of leaving accuracy to flatter it.
               "constant_predictor": bool(
                   champ_f1 <= (1.0 / float(n_cls)) + 1e-9
                   or (bal_acc is not None and abs(bal_acc - 1.0 / float(n_cls)) < 1e-6)
                   or (kappa is not None and abs(kappa) < 1e-6)
                   or (mcc is not None and abs(mcc) < 1e-6)
                   # FIX 26-Jul-2026: this used to fire on ANY class with recall 0,
                   # which flagged the TVM root-cause head as a constant predictor for
                   # missing two classes with 5 and 2 test supports -- while it scored
                   # macro-F1 0.3272 (2.0x its 1/6 baseline), kappa 0.5105 and MCC
                   # 0.5112. A model that misses a tiny class is not a constant
                   # predictor; a model that predicts ONE class is. Require that all
                   # but one class is never predicted.
                   or (sum(1 for k in LBL if float(per_rec[k]) == 0.0) >= int(n_cls) - 1)),
               "constant_predictor_baseline_macro_f1": round(1.0 / float(n_cls), 4),
               "majority_class_share_test": round(float(max(support) / max(1, sum(support))), 4),
               "n_classes_never_predicted": int(sum(1 for k in LBL if float(per_rec[k]) == 0.0)),
               "n_train": int(len(tr)), "n_test": int(len(te)), "n_features": len(feats),
               "class_balance_train": {names[k]: int((ytr == k).sum()) for k in LBL},
               "per_class": {names[k]: {"precision": round(float(per_prec[k]), 4),
                                        "recall": round(float(per_rec[k]), 4),
                                        "f1": round(float(per_f1[k]), 4),
                                        "support": int(support[k])} for k in LBL}}
    json.dump(summary, open(out / f"{cat.lower()}_{head_name}_champion_summary.json", "w"), indent=2)

    # serving bundle
    import joblib
    joblib.dump({"prep": prep, "model": model, "label_encoder": le, "features": feats,
                 "encoders": _dtype_report.get("encoded", {}),
                 "head": head_name, "category": cat, "target": target_col},
                out / f"{cat.lower()}_{head_name}_bundle.joblib")

    return {"champ": champ, "model": model, "prep": prep, "le": le, "feats": feats,
            "summary": summary, "shap_imp": shap_imp, "gate_pass": gate_pass}

# ----------------------------------------------------------------------------- viz helpers
def _plot_confusion(cm, labels, title, path):
    fig, ax = plt.subplots(figsize=(1.6 + 0.7 * len(labels), 1.4 + 0.6 * len(labels)))
    cmn = cm.astype("float64") / cm.sum(axis=1, keepdims=True).clip(min=1)
    im = ax.imshow(cmn, cmap="Blues", vmin=0, vmax=1)
    ax.set_xticks(range(len(labels))); ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8); ax.set_yticklabels(labels, fontsize=8)
    for i in range(len(labels)):
        for j in range(len(labels)):
            ax.text(j, i, f"{cm[i,j]}", ha="center", va="center",
                    color="white" if cmn[i, j] > 0.5 else "#333", fontsize=7)
    ax.set_xlabel("predicted"); ax.set_ylabel("actual"); ax.set_title(title, fontsize=9)
    plt.tight_layout(); plt.savefig(path, dpi=110); plt.close()

def _plot_perclass_f1(labels, f1s, title, path):
    fig, ax = plt.subplots(figsize=(max(5, 0.7 * len(labels)), 4))
    ax.bar(range(len(labels)), f1s, color=PASTEL[:len(labels)] if len(labels) <= len(PASTEL) else None)
    ax.set_xticks(range(len(labels))); ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_ylim(0, 1); ax.set_ylabel("F1"); ax.set_title(title, fontsize=10)
    for i, v in enumerate(f1s): ax.text(i, v + 0.02, f"{v:.2f}", ha="center", fontsize=8)
    plt.tight_layout(); plt.savefig(path, dpi=110); plt.close()

def _plot_roc_pr(y, proba, names, LBL, cat, head, out, tag=""):
    """One-vs-rest ROC + PR curves (test). Caps at 8 classes by support to stay legible."""
    Yoh = label_binarize(y, classes=LBL)
    if Yoh.shape[1] == 1: Yoh = np.hstack([1 - Yoh, Yoh])
    order = np.argsort([-(y == k).sum() for k in LBL])[:8]
    pre = f"{cat.lower()}_{head}{('_' + tag) if tag else ''}"
    # ROC
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    for k in order:
        try:
            fpr, tpr, _ = roc_curve(Yoh[:, k], proba[:, k]); a = roc_auc_score(Yoh[:, k], proba[:, k])
            ax.plot(fpr, tpr, lw=1.4, color=PASTEL[list(order).index(k) % len(PASTEL)],
                    label=f"{names[k][:22]} (AUC {a:.2f})")
        except Exception: pass
    ax.plot([0, 1], [0, 1], "--", color="#bbb", lw=1)
    ax.set_xlabel("FPR"); ax.set_ylabel("TPR"); ax.set_title(f"PS3 {cat} {head} -- ROC (OVR, test)", fontsize=10)
    ax.legend(fontsize=7, loc="lower right"); plt.tight_layout()
    plt.savefig(out / f"{pre}_roc_ovr.png", dpi=110); plt.close()
    # PR
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    for k in order:
        try:
            prec, rec, _ = precision_recall_curve(Yoh[:, k], proba[:, k])
            ap = average_precision_score(Yoh[:, k], proba[:, k])
            ax.plot(rec, prec, lw=1.4, color=PASTEL[list(order).index(k) % len(PASTEL)],
                    label=f"{names[k][:22]} (AP {ap:.2f})")
        except Exception: pass
    ax.set_xlabel("recall"); ax.set_ylabel("precision"); ax.set_title(f"PS3 {cat} {head} -- PR (OVR, test)", fontsize=10)
    ax.legend(fontsize=7, loc="lower left"); plt.tight_layout()
    plt.savefig(out / f"{pre}_pr_ovr.png", dpi=110); plt.close()

def _plot_calibration(y, proba, pred, cat, head, out):
    """Confidence reliability: bin max-proba vs empirical accuracy of the predicted class."""
    conf = proba.max(axis=1); correct = (pred == np.asarray(y)).astype(float)
    bins = np.linspace(0, 1, 11); idx = np.digitize(conf, bins) - 1
    xs, ys = [], []
    for b in range(10):
        m = idx == b
        if m.sum() >= 5: xs.append(conf[m].mean()); ys.append(correct[m].mean())
    fig, ax = plt.subplots(figsize=(5.5, 5))
    ax.plot([0, 1], [0, 1], "--", color="#bbb", lw=1, label="perfect")
    ax.plot(xs, ys, "o-", color="#9DC3E6", lw=1.6, label="model")
    ax.set_xlabel("mean predicted confidence"); ax.set_ylabel("empirical accuracy")
    ax.set_title(f"PS3 {cat} {head} -- reliability", fontsize=10); ax.legend(fontsize=8)
    plt.tight_layout(); plt.savefig(out / f"{cat.lower()}_{head}_calibration.png", dpi=110); plt.close()

def _write_report_txt(y, pred, names, LBL, cat, head, champ, lb, floor, gate_pass, out):
    rep = classification_report(y, pred, labels=LBL, target_names=names, zero_division=0, digits=4)
    with open(out / f"{cat.lower()}_{head}_report.txt", "w") as f:
        f.write(f"PS3 {cat} -- {head} head\n{'='*60}\n")
        f.write(f"champion         : {champ}\n")
        f.write(f"macro-F1 gate    : {floor:.2f} -> {'PASS' if gate_pass else 'FAIL (review)'}\n\n")
        f.write("model bake-off (sorted by macro-F1):\n")
        f.write(lb.to_string(index=False)); f.write("\n\n")
        f.write("per-class classification report (test):\n"); f.write(rep); f.write("\n")

def _norm_shap(sv, n_feat):
    """Normalise TreeExplainer output to a list of K arrays, each (n_samples x n_feat)."""
    if isinstance(sv, list):
        return sv
    if getattr(sv, "ndim", 2) == 3:
        if sv.shape[1] == n_feat:   return [sv[:, :, k] for k in range(sv.shape[2])]   # (n, n_feat, K)
        if sv.shape[2] == n_feat:   return [sv[k] for k in range(sv.shape[0])]         # (K, n, n_feat)
    return [np.asarray(sv)]

def _shap_importance(model, prep, X, feats, cat, head, out, class_names=None):
    """Global mean|SHAP| bar + beeswarm + per-class mean-SHAP + dependence (v3-style suite)."""
    pre = f"{cat.lower()}_{head}"
    try:
        if shap_mod is None: return None
        if not any(k in type(model).__name__ for k in ["Forest", "XGB", "LGBM", "Boosting", "CatBoost"]):
            return None
        Xt = pd.DataFrame(prep.transform(X), columns=feats)
        samp = Xt.sample(min(1500, len(Xt)), random_state=RS)
        expl = shap_mod.TreeExplainer(model)
        sv_list = _norm_shap(expl.shap_values(samp), len(feats))
        absmean = np.mean([np.abs(s) for s in sv_list], axis=0)           # classes-averaged |SHAP|
        imp = pd.Series(absmean.mean(axis=0), index=feats).sort_values(ascending=False)
        imp.to_csv(out / f"{pre}_shap_importance.csv")
        # (1) global bar
        ax = imp.head(20)[::-1].plot(kind="barh", figsize=(7, 6.5), color="#9DC3E6")
        ax.set_title(f"PS3 {cat} {head} -- global SHAP importance (classes averaged)"); plt.tight_layout()
        plt.savefig(out / f"{pre}_shap.png", dpi=110); plt.close()
        names = [str(c) for c in (class_names if class_names is not None else range(len(sv_list)))]
        focus = int(np.argmax([np.abs(s).mean() for s in sv_list]))       # most-influenced class
        # (2) global beeswarm (on the focus class -- signed)
        try:
            shap_mod.summary_plot(sv_list[focus], samp, max_display=20, show=False)
            plt.title(f"PS3 {cat} {head} -- SHAP beeswarm ({names[focus][:22]})", fontsize=9)
            plt.tight_layout(); plt.savefig(out / f"{pre}_shap_beeswarm.png", dpi=110, bbox_inches="tight"); plt.close()
        except Exception as e: print(f"  [shap beeswarm] {type(e).__name__}"); plt.close("all")
        # (3) per-class mean|SHAP| bars (top <=4 classes by influence)
        for k in np.argsort([-np.abs(s).mean() for s in sv_list])[:4]:
            try:
                cimp = pd.Series(np.abs(sv_list[k]).mean(0), index=feats).sort_values(ascending=False)
                ax = cimp.head(15)[::-1].plot(kind="barh", figsize=(6.5, 5.5), color="#C9A0DC")
                ax.set_title(f"PS3 {cat} {head} -- mean|SHAP| for class {names[k][:22]}"); plt.tight_layout()
                safe = names[k].replace(" ", "_").replace("/", "-")[:24]
                plt.savefig(out / f"{pre}_shap_class_{safe}.png", dpi=110); plt.close()
            except Exception: plt.close("all")
        # (4) dependence plots for the top-3 global features on the focus class
        for feat in imp.head(3).index:
            try:
                shap_mod.dependence_plot(feat, sv_list[focus], samp, interaction_index=None, show=False)
                plt.title(f"PS3 {cat} {head} -- SHAP dependence: {feat} ({names[focus][:16]})", fontsize=9)
                plt.tight_layout(); plt.savefig(out / f"{pre}_shap_dep_{feat}.png", dpi=110, bbox_inches="tight"); plt.close()
            except Exception: plt.close("all")
        print(f"  SHAP suite ({head}): global+beeswarm+per-class+dependence | top: {', '.join(imp.head(5).index)}")
        return imp
    except Exception as e:
        print(f"  [shap:{head}] skipped: {type(e).__name__}: {str(e)[:60]}"); plt.close("all"); return None

# ----------------------------------------------------------------------------- scoring -> device + serial + incident
def score_and_aggregate(df, cat, out, sev, rc):
    """Produce incident-grain, device-grain and serial-grain scored feeds (RDS-ready)."""
    inc = df.copy()
    # incident-grain predictions for each trained head
    def predict_head(h):
        if h is None: return None, None
        _X = apply_feature_coercion(inc, h["feats"], h.get("encoders"))
        p = h["model"].predict_proba(h["prep"].transform(_X))
        lab = h["le"].inverse_transform(p.argmax(axis=1))
        conf = p.max(axis=1)
        return lab, conf
    sev_lab, sev_conf = predict_head(sev)
    rc_lab, rc_conf = predict_head(rc)

    base = ["availability_event_id", "device_id", "mars_device_category", "AE_START_DTM",
            "matched_serial_nbr", "component_age_days", "FACILITY_ID", "FACILITY_NAME"]
    # NEW 21-Jul-2026: also carry every raw model feature (both heads' feats, de-duped) onto the
    # incident-grain CSV, not just the fixed `base` identifier columns above. Without this, the
    # deep-dive delivery's entity_shap_drivers() -- which reindexes this same CSV to the bundle's
    # `feats` list for genuine per-instance SHAP -- would silently get NaN (imputed-median) for
    # every feature not in `base`, including all 4 new enrichment sources. That would make
    # "why is THIS device risky" SHAP technically render but be quietly wrong. Real values now,
    # not just component_age_days.
    all_feats = list(dict.fromkeys((sev["feats"] if sev else []) + (rc["feats"] if rc else [])))
    keep = list(dict.fromkeys(base + all_feats))
    inc_out = inc[[c for c in keep if c in inc.columns]].copy()
    if sev is not None:
        inc_out["pred_severity"] = sev_lab
        inc_out["pred_severity_conf"] = np.round(sev_conf, 5)
        # v2: an unmapped code must SURFACE, not silently become MAJOR. MIN_CLASS_COUNT folds
        # rare classes into OTHER, which is in no map; v1's .fillna("MAJOR") swallowed it and
        # was the mechanism behind the 100%-MAJOR fleet on the 18-Jul run.
        _sev_map = {str(k).strip().upper(): v for k, v in CONFIG["SEVERITY_COLLAPSE"].items()}
        inc_out["pred_severity_collapsed"] = (
            pd.Series(sev_lab).astype(str).str.strip().str.upper()
              .map(_sev_map).fillna("UNKNOWN").values)
        _unk = int((inc_out["pred_severity_collapsed"] == "UNKNOWN").sum())
        if _unk:
            print(f"  [severity] {_unk:,} incident(s) carry a code absent from SEVERITY_COLLAPSE "
                  f"-> UNKNOWN (excluded from pct_critical denominators)")
        if CONFIG["SEVERITY_TARGET"] in inc.columns:
            inc_out["actual_severity"] = inc[CONFIG["SEVERITY_TARGET"]].astype(str).values
    if rc is not None:
        inc_out["pred_component"] = rc_lab
        inc_out["pred_component_conf"] = np.round(rc_conf, 5)
        if CONFIG["ROOTCAUSE_TARGET"] in inc.columns:
            inc_out["actual_component"] = inc[CONFIG["ROOTCAUSE_TARGET"]].astype(str).values
    inc_out.to_csv(out / f"{cat.lower()}_incident_predictions.csv", index=False)
    print(f"  incident predictions: {len(inc_out):,} rows -> {cat.lower()}_incident_predictions.csv")

    # device-grain rollup
    g = inc_out.groupby("device_id")
    dev = pd.DataFrame({"n_incidents": g.size()})
    if "pred_severity_collapsed" in inc_out:
        dev["pct_critical_pred"] = g["pred_severity_collapsed"].apply(lambda s: (s == "CRITICAL").mean()).round(4)
        dev["dominant_pred_severity"] = g["pred_severity"].agg(lambda s: s.value_counts().index[0])
    if "pred_component" in inc_out:
        dev["dominant_pred_component"] = g["pred_component"].agg(lambda s: s.value_counts().index[0])
    if "component_age_days" in inc_out:
        dev["avg_component_age_days"] = g["component_age_days"].mean().round(0)
    if "AE_START_DTM" in inc_out:
        dev["last_incident_dtm"] = g["AE_START_DTM"].max()
    dev = dev.reset_index().sort_values("n_incidents", ascending=False)
    dev["mars_device_category"] = cat
    dev.to_csv(out / f"{cat.lower()}_device_predictions.csv", index=False)
    print(f"  device rollup: {len(dev):,} devices -> {cat.lower()}_device_predictions.csv")

    # ---------------- serial-grain rollup, v2 --------------------------------------
    # v1 grouped on the single matched_serial_nbr that gold carries, so it emitted one row
    # per device. v2 explodes to component grain from silver.hw_config_current first.
    ser_out = None
    _sg = get_serial_grain() if CONFIG.get("SERIAL_FANOUT", True) else None
    if _sg is None and CONFIG.get("SERIAL_FANOUT", True):
        print("  [serial] ps3_serial_grain functions not found in this kernel -- "
              "run the 'Component/serial grain module' cell before this one")
    if _sg is not None:
        _hw = _sg.load_hw_config(load_table, CONFIG, category=cat)
        if _hw is not None:
            SERIAL_UNIQUENESS[cat] = _sg.serial_uniqueness_report(_hw)
            _exp = _sg.expand_serial_grain(inc_out, _hw, CONFIG)
            if _exp is not None:
                _exp.to_csv(out / f"{cat.lower()}_incident_component.csv", index=False)
                ser_out = _sg.serial_rollup(_exp, CONFIG, cat)
                if ser_out is not None:
                    ser_out.to_csv(out / f"{cat.lower()}_serial_predictions.csv", index=False)
    if ser_out is None:
        print("  [serial] no serial-grain output for this device type")

    # explainability top-N per incident (from SHAP global as per-incident proxy is heavy; use global top-k)
    expl_rows = []
    for h, hd in [("severity", sev), ("root_cause", rc)]:
        if hd is None or hd.get("shap_imp") is None: continue
        for rank, (feat, val) in enumerate(hd["shap_imp"].head(5).items(), 1):
            expl_rows.append({"head": h, "feature_rank": rank, "feature_name": feat,
                              "shap_importance": round(float(val), 6)})
    if expl_rows:
        pd.DataFrame(expl_rows).to_csv(out / f"{cat.lower()}_prediction_explainability.csv", index=False)
    return inc_out, dev, ser_out

def per_type_viz(df, cat, out):
    # severity mix for top devices (stacked) + component-age vs incident-count scatter
    if CONFIG["SEVERITY_TARGET"] in df.columns:
        top_dev = df["device_id"].value_counts().head(12).index
        sub = df[df["device_id"].isin(top_dev)]
        mix = pd.crosstab(sub["device_id"], sub[CONFIG["SEVERITY_TARGET"]].astype(str))
        mix = mix.loc[top_dev]
        ax = mix.plot(kind="bar", stacked=True, figsize=(9, 5), color=PASTEL[:mix.shape[1]])
        ax.set_title(f"PS3 {cat} -- severity mix, top-12 incident devices"); ax.set_xlabel("")
        ax.legend(fontsize=7, ncol=2); plt.tight_layout()
        plt.savefig(out / f"{cat.lower()}_device_severity_mix.png", dpi=110); plt.close()
    if {"component_age_days", "device_id"}.issubset(df.columns):
        agg = df.groupby("device_id").agg(n=("availability_event_id", "size"),
                                          age=("component_age_days", "mean")).dropna()
        if len(agg):
            fig, ax = plt.subplots(figsize=(7, 5))
            ax.scatter(agg["age"], agg["n"], s=18, alpha=0.5, color="#F4B183")
            ax.set_xlabel("avg component age (days)"); ax.set_ylabel("incident count")
            ax.set_title(f"PS3 {cat} -- component age vs incident volume (device)")
            plt.tight_layout(); plt.savefig(out / f"{cat.lower()}_age_vs_incidents.png", dpi=110); plt.close()

# ----------------------------------------------------------------------------- device-type runner
def run_device_type(df_all, cat, out_root):
    out = Path(out_root) / CONFIG["SUBFOLDER"][cat]; out.mkdir(parents=True, exist_ok=True)
    d = df_all[df_all["mars_device_category"] == cat].copy()
    print("\n" + "=" * 74); print(f" DEVICE TYPE: {cat}   incidents = {len(d):,}"); print("=" * 74)
    if len(d) == 0:
        return handle_no_incidents(cat, out)
    d = add_temporal(add_facility_freq(d))
    d["_ae_date"] = pd.to_datetime(d["AE_START_DTM"], errors="coerce").dt.normalize()
    d = d[d["_ae_date"].notna()].sort_values("_ae_date").reset_index(drop=True)
    d = enrich_all(d)   # NEW 21-Jul-2026: usage_lifecycle_daily / station_network_daily / metric_daily / device_uptime_intervals
    feats = build_feature_list(d)
    split_date = d["_ae_date"].quantile(0.80)

    scan, leaky = leakage_scan(d, CONFIG["SEVERITY_TARGET"], feats, split_date)
    feats = [c for c in feats if c not in set(leaky)]
    nzv = [c for c in feats if d[c].nunique(dropna=True) < CONFIG["NZV_UNIQUE_MIN"]]
    feats = [c for c in feats if c not in set(nzv)]
    if len(scan): scan.round(4).to_csv(out / f"{cat.lower()}_leakage_scan.csv")
    json.dump({"features": feats, "dropped_leaky": leaky, "dropped_nzv": nzv,
               "monitored": [m for m in CONFIG["MONITORED_FEATURES"] if m in feats]},
              open(out / f"{cat.lower()}_feature_manifest.json", "w"), indent=2)
    print(f"final feature count: {len(feats)} | dropped leaky {len(leaky)} nzv {len(nzv)}")

    if len(d) < CONFIG["MIN_INCIDENTS_TO_MODEL"]:
        print(f"[{cat}] only {len(d)} incidents (< {CONFIG['MIN_INCIDENTS_TO_MODEL']}) -> REPORT-ONLY (too sparse to model honestly)")
        _sparse_report(d, cat, out)
        return {"cat": cat, "modeled": False, "n_incidents": len(d)}

    sev = train_head(d, feats, CONFIG["SEVERITY_TARGET"], "severity", cat, out, split_date)
    rc = train_head(d, feats, CONFIG["ROOTCAUSE_TARGET"], "root_cause", cat, out, split_date)
    inc_out, dev, ser = score_and_aggregate(d, cat, out, sev, rc)
    per_type_viz(d, cat, out)
    return {"cat": cat, "modeled": True, "n_incidents": len(d),
            "severity": sev["summary"] if sev else None, "root_cause": rc["summary"] if rc else None,
            "n_devices": int(dev["device_id"].nunique()) if dev is not None else 0,
            "n_serials": int(len(ser)) if ser is not None else 0}

def _sparse_report(d, cat, out):
    rep = {"device_category": cat, "n_incidents": int(len(d)),
           "note": f"{cat} has too few PS3 incidents to train an honest classifier; descriptive only.",
           "severity_distribution": d[CONFIG["SEVERITY_TARGET"]].astype(str).value_counts().to_dict()
                                     if CONFIG["SEVERITY_TARGET"] in d else {},
           "component_distribution": d[CONFIG["ROOTCAUSE_TARGET"]].astype(str).value_counts().to_dict()
                                     if CONFIG["ROOTCAUSE_TARGET"] in d else {}}
    json.dump(rep, open(out / f"{cat.lower()}_sparse_report.json", "w"), indent=2)

def handle_no_incidents(cat, out):
    # VALIDATOR path: 0 availability-event incidents. Honest stub + OOS proxy from device_failures.
    print(f"[{cat}] 0 PS3 availability-event incidents (confirmed by data foundation). Writing honest stub + OOS proxy.")
    stub = {"device_category": cat, "n_ps3_incidents": 0,
            "why": ("PS3 is sourced from silver.incident_root_cause (ServiceNow availability events). "
                    f"{cat}/BMV failures are NOT recorded as availability events -- they surface as "
                    "device_event out-of-service 'Set' events (dim_event_matrix OOS flags). So the "
                    "PS3 availability-event feed has 0 rows for this device type by construction."),
            "recommendation": ("Score validators via the PS4 anomaly head (unsupervised, covers all 3 types) "
                               "and the OOS-proxy severity below; a true PS3 validator model needs Cubic to "
                               "emit validator availability events or fault codes."),
            "proxy_source": "silver.device_failures (S26) OOS 'Set' events, severity via dim_failure_level"}
    json.dump(stub, open(out / f"{cat.lower()}_ps3_stub.json", "w"), indent=2)
    # try the OOS proxy
    df_fail = load_table("mars_dev.silver.device_failures",
                         f"{CONFIG['SILVER_PREFIX']}/device_failures", category_filter=cat, required=False)
    if df_fail is not None and len(df_fail):
        sevcol = next((c for c in ["failure_level", "FAILURE_LEVEL", "failure_level_label"] if c in df_fail.columns), None)
        if sevcol:
            vc = df_fail[sevcol].astype(str).value_counts()
            vc.to_csv(out / f"{cat.lower()}_oos_proxy_severity.csv")
            ax = vc.head(10).plot(kind="bar", figsize=(7, 4), color="#C9A0DC")
            ax.set_title(f"PS3 {cat} -- OOS-proxy severity (device_failures, NOT availability events)")
            plt.tight_layout(); plt.savefig(out / f"{cat.lower()}_oos_proxy_severity.png", dpi=110); plt.close()
            print(f"  OOS proxy: {len(df_fail):,} {cat} OOS events, {df_fail[sevcol].nunique()} severity levels")
    else:
        print(f"  [proxy] device_failures export for {cat} not available; wrote stub only.")
    return {"cat": cat, "modeled": False, "n_incidents": 0, "stub": True}

# ----------------------------------------------------------------------------- overview EDA (root folder)
def overview_eda(df, out_root):
    out = Path(out_root)
    print("\n" + "=" * 74); print(" CROSS-DEVICE OVERVIEW (device_ps3_incident)"); print("=" * 74)
    print(f" incidents {len(df):,} | devices {df['device_id'].nunique():,} | "
          f"{pd.to_datetime(df['AE_START_DTM']).min().date()} -> {pd.to_datetime(df['AE_START_DTM']).max().date()}")
    print(" by device type:"); print(df["mars_device_category"].value_counts().to_string())
    # severity distribution by device type
    if CONFIG["SEVERITY_TARGET"] in df.columns:
        ct = pd.crosstab(df["mars_device_category"], df[CONFIG["SEVERITY_TARGET"]].astype(str))
        ax = ct.plot(kind="bar", stacked=True, figsize=(9, 5), color=PASTEL[:ct.shape[1]])
        ax.set_title("PS3 -- severity distribution by device type"); ax.legend(fontsize=7, ncol=2)
        plt.tight_layout(); plt.savefig(out / "ps3_overview_severity_by_type.png", dpi=110); plt.close()
    # component distribution
    if CONFIG["ROOTCAUSE_TARGET"] in df.columns:
        vc = df[CONFIG["ROOTCAUSE_TARGET"]].astype(str).value_counts().head(12)
        ax = vc[::-1].plot(kind="barh", figsize=(8, 5), color="#A9D18E")
        ax.set_title("PS3 -- root-cause component distribution (derived)"); plt.tight_layout()
        plt.savefig(out / "ps3_overview_component_distribution.png", dpi=110); plt.close()
    # incidents over time by severity
    tmp = df.copy(); tmp["month"] = pd.to_datetime(tmp["AE_START_DTM"]).dt.to_period("M").astype(str)
    piv = tmp.groupby("month").size()
    fig, ax = plt.subplots(figsize=(10, 4)); piv.plot(ax=ax, color="#9DC3E6", marker="o", ms=3)
    ax.set_title("PS3 -- incident volume over time"); ax.set_xlabel(""); plt.xticks(rotation=45, fontsize=7)
    plt.tight_layout(); plt.savefig(out / "ps3_overview_incidents_over_time.png", dpi=110); plt.close()
    # component age by severity (box)
    if {"component_age_days", CONFIG["SEVERITY_TARGET"]}.issubset(df.columns):
        d2 = df.dropna(subset=["component_age_days"])
        cats = d2[CONFIG["SEVERITY_TARGET"]].astype(str).value_counts().head(6).index.tolist()
        data = [d2[d2[CONFIG["SEVERITY_TARGET"]].astype(str) == c]["component_age_days"].values for c in cats]
        if any(len(x) for x in data):
            fig, ax = plt.subplots(figsize=(9, 5)); ax.boxplot(data, labels=cats, showfliers=False)
            ax.set_title("PS3 -- component age by severity"); ax.set_ylabel("component age (days)")
            plt.xticks(rotation=30, ha="right", fontsize=8); plt.tight_layout()
            plt.savefig(out / "ps3_overview_age_by_severity.png", dpi=110); plt.close()
    # subsystem pre-incident intensity by component (honest signal heatmap)
    subs = [c for c in ["bhu_events_24h","chu_events_24h","printer_events_24h","gate_mech_events_24h",
                        "csc_reader_events_24h","comms_events_24h"] if c in df.columns]
    if subs and CONFIG["ROOTCAUSE_TARGET"] in df.columns:
        comp = df[CONFIG["ROOTCAUSE_TARGET"]].astype(str)
        keep = comp.value_counts().head(7).index
        M = df[comp.isin(keep)].groupby(comp)[subs].mean().loc[[c for c in keep if c in comp.unique()]]
        fig, ax = plt.subplots(figsize=(8, 5)); im = ax.imshow(M.values, cmap="YlOrBr", aspect="auto")
        ax.set_xticks(range(len(subs))); ax.set_xticklabels([s.replace("_events_24h","") for s in subs], rotation=40, ha="right", fontsize=8)
        ax.set_yticks(range(len(M))); ax.set_yticklabels(M.index, fontsize=8)
        ax.set_title("PS3 -- mean pre-incident subsystem events (24h) by component"); fig.colorbar(im, fraction=0.046)
        plt.tight_layout(); plt.savefig(out / "ps3_overview_subsystem_by_component.png", dpi=110); plt.close()
    print(" overview figures written to", out.resolve())

# ----------------------------------------------------------------------------- synthetic generator (SMOKE TEST ONLY)
def make_synthetic(n_tvm=6000, n_gate=350, seed=7):
    rng = np.random.default_rng(seed)
    comp_types = ["CSC_READER", "PRINTER", "BHU", "CHU", "GATE_MECH", "COMMS"]
    # CODE-style labels (NEW 21-Jul-2026): the 18-Jul live run confirmed real failure_level_label
    # values on gold.device_ps3_incident are CODES, not human text -- synthetic must match reality
    # so the smoke test actually exercises the fixed CONFIG["SEVERITY_COLLAPSE"] mapping above.
    sev_levels = {2: "PURCHASE_CARD", 3: "PURCHASE_PRODUCT",
                  4: "ALL_PURCHASE", 5: "ALL_FUNCTIONS", 16: "BUS_READER_ASSEMBLY"}
    def gen(cat, n, dev_prefix, comp_probs):
        comp = rng.choice(comp_types, n, p=comp_probs)
        # subsystem event counts correlated with the true component (honest pre-incident signal)
        base = {c: rng.poisson(0.4, n).astype(float) for c in
                ["bhu", "chu", "printer", "gate_mech", "csc_reader", "comms"]}
        keymap = {"CSC_READER": "csc_reader", "PRINTER": "printer", "BHU": "bhu", "CHU": "chu",
                  "GATE_MECH": "gate_mech", "COMMS": "comms"}
        for i, c in enumerate(comp):
            base[keymap[c]][i] += rng.poisson(4)
        # severity partly driven by component + age + event load (honest, imperfect)
        age = rng.integers(20, 3000, n).astype(float)
        load = sum(base.values())
        sev_score = (0.5 * (comp == "GATE_MECH") + 0.4 * (comp == "COMMS") + 0.0006 * age
                     + 0.05 * load + rng.normal(0, 0.6, n))
        lvl = np.where(sev_score > 1.6, 16, np.where(sev_score > 1.1, 5,
              np.where(sev_score > 0.7, 4, np.where(sev_score > 0.3, 3, 2))))
        sn_code = (lvl * 100 + rng.integers(0, 40, n)).astype(int)   # fault code ~ severity (dominant, monitored)
        dt = pd.to_datetime("2024-01-01") + pd.to_timedelta(rng.integers(0, 800, n), unit="D") \
             + pd.to_timedelta(rng.integers(0, 24 * 3600, n), unit="s")
        dev_id = [f"{dev_prefix}{rng.integers(1, max(2, n // 6)):05d}" for _ in range(n)]
        return pd.DataFrame({
            "availability_event_id": [f"AE-{cat}-{i:07d}" for i in range(n)],
            "device_id": dev_id, "mars_device_category": cat,
            "AE_START_DTM": dt, "AE_END_DTM": dt + pd.to_timedelta(rng.integers(5, 300, n), unit="m"),
            "transit_day": dt.normalize(),
            "incident_duration_min": rng.integers(5, 300, n).astype(float),
            "failure_level": lvl, "failure_level_label": [sev_levels[int(x)] for x in lvl],
            "derived_component_type": comp, "root_cause_category": comp,
            "affected_component": comp, "matched_component": comp,
            "AE_FAULT_DESCRIPTION": [f"{c} fault detected" for c in comp],   # leak col (must be dropped)
            "AE_SYMPTOM": ["symptom"] * n, "AE_PROBLEM": ["problem"] * n, "AE_RESOLUTION": ["replaced part"] * n,
            "desc_card_reader_flag": (comp == "CSC_READER").astype(int),      # text-derived leak flags
            "desc_printer_flag": (comp == "PRINTER").astype(int),
            "desc_bill_handler_flag": (comp == "BHU").astype(int),
            "desc_coin_flag": (comp == "CHU").astype(int),
            "desc_comms_flag": (comp == "COMMS").astype(int),
            "desc_timeout_flag": rng.integers(0, 2, n), "desc_replacement_flag": rng.integers(0, 2, n),
            "events_24h_prior": load + rng.poisson(1, n),
            "oos_onsets_24h": rng.poisson(0.6, n).astype(float),
            "bhu_events_24h": base["bhu"], "chu_events_24h": base["chu"], "printer_events_24h": base["printer"],
            "gate_mech_events_24h": base["gate_mech"], "csc_reader_events_24h": base["csc_reader"],
            "comms_events_24h": base["comms"],
            "oos_onsets_7d_prior": rng.poisson(2, n).astype(float), "events_7d_prior": load * 3 + rng.poisson(5, n),
            "component_age_days": age,
            "matched_serial_nbr": [f"SN{cat}{rng.integers(1, max(2, n // 3)):06d}" for _ in range(n)],
            "sn_event_code_id": sn_code, "sn_event_code_name": [f"code_{x}" for x in sn_code],
            "sn_priority": rng.integers(1, 5, n).astype(float),
            "kpi_rule_id": [None] * n, "kpi_category_name": [None] * n,
            "FACILITY_ID": [f"FAC-{rng.integers(1, 60):04d}" for _ in range(n)],
            "FACILITY_NAME": ["Station"] * n, "OPERATOR_ID": [1] * n, "OPERATOR_NAME": ["CTA"] * n,
            "DEVICE_NAME": dev_id, "DEVICE_TYPE_NAME": cat, "DEVICE_SERIAL_NUMBER": dev_id,
            "is_chargeable": [True] * n, "is_device_fault": [True] * n,
            "wot_state": ["Closed"] * n, "request_type": ["Corrective"] * n, "AE_FAULT_STATE": ["Set"] * n,
        })
    tvm = gen("TVM", n_tvm, "TVM", [0.55, 0.18, 0.12, 0.08, 0.0, 0.07])
    gate = gen("GATE", n_gate, "RVG", [0.15, 0.0, 0.0, 0.0, 0.6, 0.25])
    return pd.concat([tvm, gate], ignore_index=True)

def make_synthetic_enrichment_tables(df, seed=13):
    """NEW 21-Jul-2026: synthetic stand-ins for the 4 new silver enrichment sources, so
    --synthetic genuinely exercises enrich_all()'s join code paths (not just skip-silently).
    One row per (device_id, day) spanning the incident date range plus a 30-day lookback, which
    is enough for merge_asof(direction='backward') to always find a prior-day match."""
    rng = np.random.default_rng(seed)
    devices = df["device_id"].unique()
    lo = pd.to_datetime(df["AE_START_DTM"]).min().normalize() - pd.Timedelta(days=30)
    hi = pd.to_datetime(df["AE_START_DTM"]).max().normalize()
    days = pd.date_range(lo, hi, freq="D")
    fac_by_dev = df.drop_duplicates("device_id").set_index("device_id")["FACILITY_ID"].to_dict()
    cat_by_dev = df.drop_duplicates("device_id").set_index("device_id")["mars_device_category"].to_dict()

    idx = pd.MultiIndex.from_product([devices, days], names=["device_id", "transit_day"]).to_frame(index=False)
    n = len(idx)
    # NOTE: real silver schemas use uppercase DEVICE_ID (see silver_schemas.md); each enrich_* fn
    # renames DEVICE_ID -> device_id on load, so the synthetic frames must carry ONLY the uppercase
    # column (mirroring production) -- keeping both would collide on that rename.
    idx = idx.rename(columns={"device_id": "DEVICE_ID"})
    usage = idx.copy()
    usage["days_since_last_failure"] = rng.integers(0, 60, n).astype(float)
    usage["days_since_last_maintenance"] = rng.integers(0, 120, n).astype(float)
    usage["failure_count_30d"] = rng.poisson(1.2, n).astype(float)
    usage["cumulative_failure_count"] = rng.integers(0, 50, n).astype(float)
    usage["cumulative_outage_min"] = rng.integers(0, 5000, n).astype(float)
    usage["days_in_service"] = rng.integers(30, 3000, n).astype(float)

    metric = idx.copy()
    metric["m401_avg_txn_time_ms"] = rng.normal(1800, 300, n).clip(min=200)
    metric["m401_p95_txn_time_ms"] = metric["m401_avg_txn_time_ms"] * rng.uniform(1.3, 1.8, n)
    metric["m401_p99_txn_time_ms"] = metric["m401_p95_txn_time_ms"] * rng.uniform(1.1, 1.4, n)
    metric["m401_slow_tap_pct"] = rng.uniform(0, 0.12, n)
    metric["m401_rolling_7d_avg_ms"] = metric["m401_avg_txn_time_ms"] * rng.uniform(0.9, 1.1, n)
    metric["m401_z_score_vs_28d"] = rng.normal(0, 1, n)
    metric["volume_drop_flag"] = rng.integers(0, 2, n)
    metric["comms_total_count"] = rng.poisson(0.8, n).astype(float)
    metric["comms_event_flag"] = (metric["comms_total_count"] > 2).astype(int)

    uptime = idx.copy()
    uptime["uptime_pct"] = rng.uniform(0.85, 1.0, n)
    uptime["downtime_hours"] = (1 - uptime["uptime_pct"]) * 24
    uptime["hours_since_last_hb"] = rng.exponential(2.0, n)
    uptime["is_silent_device"] = (uptime["hours_since_last_hb"] > 12).astype(int)
    uptime["distinct_message_types"] = rng.integers(1, 15, n).astype(float)
    uptime["total_msg_count"] = rng.poisson(200, n).astype(float)

    # station table is facility-grain, not device-grain
    fac_ids = pd.Series(devices).map(fac_by_dev)
    fac_grid = pd.MultiIndex.from_product([sorted(set(fac_ids.dropna())), days],
                                          names=["FACILITY_ID", "transit_day"]).to_frame(index=False)
    m = len(fac_grid)
    fac_grid["device_category"] = rng.choice(sorted(set(cat_by_dev.values())), m)
    fac_grid["devices_failed"] = rng.poisson(0.5, m).astype(float)
    fac_grid["total_failure_events"] = fac_grid["devices_failed"] * rng.uniform(1.0, 2.5, m)
    fac_grid["avg_downtime_minutes"] = rng.uniform(5, 90, m)
    fac_grid["is_coordinated_failure"] = (fac_grid["devices_failed"] >= 3).astype(int)
    fac_grid["is_major_station_event"] = (fac_grid["devices_failed"] >= 5).astype(int)

    return {"usage_lifecycle_daily": usage, "metric_daily": metric,
            "device_uptime_intervals": uptime, "station_network_daily": fac_grid}

def make_synthetic_validator_failures(n=1500, seed=11):
    rng = np.random.default_rng(seed)
    dt = pd.to_datetime("2024-01-01") + pd.to_timedelta(rng.integers(0, 800, n), unit="D")
    return pd.DataFrame({"DEVICE_ID": [f"BMV{rng.integers(1,300):05d}" for _ in range(n)],
                         "mars_device_category": "VALIDATOR", "ledger_date": dt.normalize(),
                         "failure_level": rng.choice([2, 4, 5, 16], n, p=[0.5, 0.3, 0.1, 0.1]),
                         "fault_description": ["OOS"] * n})

# ----------------------------------------------------------------------------- manifest bridge (v2)
def write_manifests(out_dir, run_id, computed_date, city=None):
    """Emit one manifest.json per output CSV.

    This is the contract `cubic-mars-ps3-rds-push` triggers on (S3 ObjectCreated, suffix
    manifest.json): table_name / grain / computed_date / run_id / s3_data_key / row_count.
    Writing it here means daily inference needs no bespoke loader - the Lambda that already
    serves PS3 deep-dive picks these up unchanged. RDS_TABLE_PREFIX keeps v2 additive so the
    live ps3_* tables are never overwritten.
    """
    import csv as _csv
    out_dir = Path(out_dir)
    pre = CONFIG.get("RDS_TABLE_PREFIX", "ps3v2_")
    s3root = CONFIG.get("S3_OUT_PREFIX", "").rstrip("/")
    grains = {"incident_predictions": "incident", "device_predictions": "device",
              "serial_predictions": "serial", "incident_component": "incident_component"}
    written = []
    for csv_path in sorted(out_dir.rglob("*.csv")):
        stem = csv_path.stem
        grain = next((g for k, g in grains.items() if stem.endswith(k)), None)
        if grain is None:
            continue
        with open(csv_path, encoding="utf-8-sig", newline="") as fh:
            rows = sum(1 for _ in _csv.reader(fh)) - 1
        rel = csv_path.relative_to(out_dir).as_posix()
        man = {
            "table_name": f"{pre}{grain}_predictions" if grain != "incident_component"
                          else f"{pre}incident_component",
            "grain": grain,
            "computed_date": str(computed_date),
            "run_id": run_id,
            "city_id": city or CONFIG.get("CITY_ID", "CHI"),
            "s3_data_key": f"{s3root}/{computed_date}/{rel}" if s3root else rel,
            "local_path": rel,
            "row_count": max(rows, 0),
            "producer": "PS3_v2_OOS_Serial_SageMaker",
            "spine": CONFIG.get("SPINE", "oos"),
            "label_semantics": "hardware OOS - chargeable is a downstream contract subset",
            "serial_uniqueness": SERIAL_UNIQUENESS,
        }
        mpath = csv_path.with_name(csv_path.stem + "_manifest.json")
        mpath.write_text(json.dumps(man, indent=2, default=str))
        written.append(mpath.name)
    print(f"  [bridge] {len(written)} manifest(s) written - "
          f"cubic-mars-ps3-rds-push will load these on S3 upload")
    return written


def score_new_data(new_inc, bundles, out_dir, run_id, computed_date, cat):
    """Daily inference over an arbitrary incident slice using the saved champion bundles.

    Deliberately reuses score_and_aggregate so a daily score cannot drift from what training
    produced - the failure mode where a batch scorer quietly computes a rollup differently
    from the notebook is exactly what put 927 identical serial rows into RDS.
    """
    new_inc = assert_oos_spine(new_inc)
    res = score_and_aggregate(new_inc, bundles.get("severity"), bundles.get("root_cause"),
                              Path(out_dir), cat)
    if CONFIG.get("EMIT_MANIFEST", True):
        write_manifests(out_dir, run_id, computed_date)
    return res


# ----------------------------------------------------------------------------- main
def main(synthetic=False):
    os.environ.setdefault("AWS_REGION", CONFIG["AWS_REGION"])
    start_run_log(CONFIG["OUT_ROOT"])
    print("PS3 engine start | scope:", CONFIG["DEVICE_SCOPE"])
    if synthetic:
        print("[MODE] SYNTHETIC smoke test -- generating fake device_ps3_incident + validator failures")
        df = make_synthetic()
        globals()["_SYNTH_VALIDATOR_FAILS"] = make_synthetic_validator_failures()
        globals()["_SYNTH_ENRICH"] = make_synthetic_enrichment_tables(df)   # NEW 21-Jul: exercise enrich_all()
    else:
        df = load_table(CONFIG["GOLD_TABLE"], CONFIG["GOLD_PARQUET"])
        df = coerce_decimals(df)
    df = df[df["transit_day"].astype(str) >= CONFIG["MIN_TRAIN_DATE"]] if "transit_day" in df.columns else df
    df = check_availability_event_dedup(df)      # NEW 21-Jul guardrail
    print_kpi_rule_fill(df)                       # NEW 21-Jul visibility (open item, not a blocker)
    probe_servicenow_conformance()                 # NEW 21-Jul diagnostic-only (never touches training)
    overview_eda(df, CONFIG["OUT_ROOT"])
    results = []
    for cat in CONFIG["DEVICE_SCOPE"]:
        try:
            results.append(run_device_type(df, cat, CONFIG["OUT_ROOT"]))
        except Exception as e:
            import traceback; traceback.print_exc()
            results.append({"cat": cat, "error": str(e)})
    # run summary + RDS load manifest + model-registry rows
    _finalize(results, df)
    return results

def _finalize(results, df):
    out = Path(CONFIG["OUT_ROOT"])
    summ = {"city_id": CONFIG["CITY_ID"], "n_incidents": int(len(df)),
            "device_scope": CONFIG["DEVICE_SCOPE"], "results": results}
    json.dump(summ, open(out / "ps3_run_summary.json", "w"), indent=2, default=str)
    reg = []
    for r in results:
        if not r or not r.get("modeled"): continue
        for head in ["severity", "root_cause"]:
            s = r.get(head)
            if s:
                reg.append({"ps_id": "PS3", "device_category": r["cat"], "prediction_head": head,
                            "model_name": f"ps3-{head}-{r['cat'].lower()}", "algorithm": s["champion"],
                            "test_f1_macro": s["test_f1_macro"], "test_auc_macro": s.get("test_auc_macro_ovr"),
                            "gate_pass": s["gate_pass"], "n_classes": s["n_classes"]})
    if reg: pd.DataFrame(reg).to_csv(out / "ps3_model_registry_rows.csv", index=False)
    load_manifest = {"rds_targets": {
        "ps3_incident_predictions": [f"{CONFIG['SUBFOLDER'][r['cat']]}/{r['cat'].lower()}_incident_predictions.csv"
                                     for r in results if r and r.get("modeled")],
        "ps3_device_predictions": [f"{CONFIG['SUBFOLDER'][r['cat']]}/{r['cat'].lower()}_device_predictions.csv"
                                   for r in results if r and r.get("modeled")],
        "ps3_serial_predictions": [f"{CONFIG['SUBFOLDER'][r['cat']]}/{r['cat'].lower()}_serial_predictions.csv"
                                   for r in results if r and r.get("modeled") and r.get("n_serials")],
    }}
    json.dump(load_manifest, open(out / "ps3_rds_load_manifest.json", "w"), indent=2)
    _any_err = any(r and r.get("error") for r in results)
    print("\n" + "=" * 74)
    print(" PS3 RUN COMPLETE" if not _any_err else " PS3 RUN FINISHED WITH ERRORS")
    print("=" * 74)
    for r in results:
        if not r: continue
        if r.get("modeled"):
            sv = r.get("severity"); rc = r.get("root_cause")
            print(f" {r['cat']:10s} incidents {r['n_incidents']:>6,} | "
                  f"severity F1 {sv['test_f1_macro'] if sv else 'NA'} ({'PASS' if sv and sv['gate_pass'] else 'review'}) | "
                  f"root-cause F1 {rc['test_f1_macro'] if rc else 'NA'} ({'PASS' if rc and rc['gate_pass'] else 'review'}) | "
                  f"devices {r.get('n_devices',0)} serials {r.get('n_serials',0)}")
        elif r.get("error"):
            # FIX 26-Jul-2026: an errored device type used to print as
            #   "incidents 0 | REPORT-ONLY / stub (sparse)"
            # because main() appends {"cat","error"} with no n_incidents, and this
            # branch defaulted it to 0. On the 26-Jul run that reported TVM and GATE
            # as sparse stubs when they had 32,842 and 1,770 incidents and had
            # actually crashed. A failure must never read as a clean sparse skip.
            print(f" {r['cat']:10s} *** FAILED *** {r['error'][:90]}")
        else:
            print(f" {r['cat']:10s} incidents {r.get('n_incidents',0):>6,} | REPORT-ONLY / stub ({'validator OOS proxy' if r.get('stub') else 'sparse'})")
    _failed = [r['cat'] for r in results if r and r.get("error")]
    if _failed:
        print("\n *** RUN INCOMPLETE: " + ", ".join(_failed) + " failed. "
              "No predictions were written for them and the RDS manifest excludes them. ***")
    print(" artifacts under", out.resolve())

# patch loader so the synthetic validator proxy + the 4 new enrichment sources work offline
_orig_load = load_table
_SYNTH_ENRICH_KEYS = {"usage_lifecycle_daily": "usage_lifecycle_daily", "metric_daily": "metric_daily",
                     "device_uptime_intervals": "device_uptime_intervals",
                     "station_network_daily": "station_network_daily", "incident_root_cause": None}
def load_table(table, parquet_path, category_filter=None, required=True):   # noqa: F811
    if "device_failures" in str(table) and "_SYNTH_VALIDATOR_FAILS" in globals():
        d = globals()["_SYNTH_VALIDATOR_FAILS"]
        return d[d["mars_device_category"] == category_filter].copy() if category_filter else d
    if "incident_root_cause" in str(table) and "_SYNTH_ENRICH" in globals():
        # synthetic/sandbox mode has no live silver access -- the probe already handles None gracefully
        return None
    for key, synth_key in _SYNTH_ENRICH_KEYS.items():
        if synth_key and key in str(table) and "_SYNTH_ENRICH" in globals():
            return globals()["_SYNTH_ENRICH"][synth_key].copy()
    return _orig_load(table, parquet_path, category_filter, required)

if __name__ == "__main__":
    main(synthetic=("--synthetic" in sys.argv))
