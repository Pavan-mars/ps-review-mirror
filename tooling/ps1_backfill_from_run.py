#!/usr/bin/env python3
"""
CUBIC MARS Chicago - PS1 device + serial backfill loader.

Loads the PS1 notebooks' cross-wired output
    s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/gold/device_ps1_cross_wired_daily
(written by CELL 24 of PS1_3d_{GATE,TVM,VALIDATOR}_SageMaker_MLflow_FeatureStore.ipynb)
into the Aurora tables created by sql/04_phase1c_ps1_failure.sql,
sql/11_phase1g_ps1_serving.sql and sql/16_phase2b_ps1_batch_lineage.sql.

WHY THIS EXISTS
---------------
The PS1 notebooks write a device x component x day parquet and a model bundle, but nothing
RDS-shaped and no manifest.json - so `cubic-mars-ps3-rds-push` (which is PS-agnostic but
triggers on manifest.json) can never fire for PS1. This script is the one-off path: it maps the
parquet that already exists onto the tables that already exist, so PS1 reaches the dashboard
without re-running a notebook. The durable daily path is an export cell + a forked rds-push
Lambda; this does not replace that.

GRAIN
-----
The source is device x component x day. Two targets are derived:
  ps1_failure_predictions  - DEVICE grain. Deduped on (device_id, prediction_date); the
                             component columns are dropped. Device probability is identical
                             across a device's component rows, so dedup is lossless.
  ps1_serial_predictions   - SERIAL grain, one row per (device, component serial, day).

ATTRIBUTION WEIGHT - READ THIS
------------------------------
`ps1_serial_predictions.attribution_weight` is meant to be the share of device risk carried by
one component. The PS1 notebooks do not compute one. This loader defaults to an EQUAL SPLIT
(1 / n_components on that device-day) and records `attribution_method` in the audit row. That
is a placeholder, not a model. Do not present serial_risk_score as a learned per-component risk
until a real attribution exists - equal split only re-expresses device risk at finer grain.

Usage:
  python ps1_backfill_from_run.py --parquet ./device_ps1_cross_wired_daily --dry-run
  python ps1_backfill_from_run.py --parquet <dir-or-file> --city CHI \
      --run-id ps1_20260726 --threshold-json ./threshold_final_gate.json \
      --dsn postgresql://user:pw@host:5432/db
"""
import argparse, datetime, glob, hashlib, json, os, sys

import pandas as pd

# ---------------------------------------------------------------- column aliases
# The notebook's frame is assembled from several joins, so casing and names drift
# between categories. Map defensively rather than assume one spelling.
ALIASES = {
    "device_id":            ["DEVICE_KEY", "DEVICE_ID", "device_id", "device_key"],
    "device_category":      ["device_category", "mars_device_category", "DEVICE_CATEGORY"],
    "failure_probability":  ["ps1_fail_prob", "failure_probability", "probability", "score"],
    "predicted_label":      ["ps1_predicted", "predicted_label", "prediction"],
    "decision_threshold":   ["threshold_used", "decision_threshold", "active_threshold"],
    "prediction_date":      ["transit_day", "event_date", "prediction_date", "scoring_date"],
    "facility_id":          ["FACILITY_ID", "facility_id", "FACID"],
    "risk_band":            ["ps1_risk_tier", "risk_band", "risk_tier"],
    "matched_serial_nbr":   ["COMPONENT_SERIAL_NBR", "matched_serial_nbr", "component_serial_nbr"],
    "component_type":       ["COMPONENT_TYPE", "component_type"],
    "component_age_days":   ["component_age_days", "COMPONENT_AGE_DAYS"],
}

RISK_BANDS = [(0.70, "CRITICAL"), (0.50, "HIGH"), (0.30, "MEDIUM")]

# ---------------------------------------------------------------- label semantics
# PS1 here is trained on the HARDWARE-OOS label, NOT the chargeable-SLA label.
#   will_hardware_oos_3d  -> device goes hardware-out-of-service within 3 days
#   will_fail_3d          -> chargeable SLA event (kept in the notebooks only as
#                            SLA_TARGET_COL, a cross-check; NOT what the model predicts)
# These populations differ, so an OOS-trained model must never be presented against the
# PSA/SLA acceptance thresholds, which are measured on chargeable events. The target and
# label revision are written to every row and to ps1_inference_runs so the provenance
# travels with the data instead of living in a slide.
TARGET_COL_DEFAULT = "will_hardware_oos_3d"
LABEL_REVISION_DEFAULT = "R7-1"
LABEL_BANNER = (
    "LABEL SEMANTICS: target={target} ({semantics}), label_revision={rev}. "
    "This is NOT the chargeable-SLA label (will_fail_3d). Do not compare these metrics "
    "against PSA/SLA acceptance thresholds, and label the dashboard 'hardware OOS risk', "
    "not 'failure' or 'SLA'."
)


def band(p):
    if p is None or pd.isna(p):
        return "UNKNOWN"
    for cut, name in RISK_BANDS:
        if p >= cut:
            return name
    return "LOW"


def resolve(df, canonical):
    """Return the actual column name in df for a canonical field, or None."""
    for cand in ALIASES[canonical]:
        if cand in df.columns:
            return cand
    return None


def read_parquet_any(path):
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "**", "*.parquet"), recursive=True))
        if not files:
            raise SystemExit(f"no .parquet files under {path}")
        return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True), len(files)
    return pd.read_parquet(path), 1


def short_id(*parts):
    """Deterministic <=48-char prediction_id (the column is VARCHAR(48))."""
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:32]


class Loader:
    def __init__(self, df, city, run_id, computed_date, attribution="equal_split",
                 target_col=TARGET_COL_DEFAULT, label_revision=LABEL_REVISION_DEFAULT,
                 label_semantics="hardware OOS, not chargeable"):
        self.src, self.city, self.run_id = df, city, run_id
        self.computed_date, self.attribution = computed_date, attribution
        self.target_col, self.label_revision = target_col, label_revision
        self.source_rows = len(df)
        self.gold_s3 = None
        self.label_semantics = label_semantics
        self.rows, self.warnings, self.stats = {}, [], {}

    # ------------------------------------------------------------ normalisation
    def normalise(self):
        df = self.src
        missing_required = []
        col = {}
        for canonical in ("device_id", "failure_probability"):
            c = resolve(df, canonical)
            if c is None:
                missing_required.append(canonical)
            col[canonical] = c
        if missing_required:
            raise SystemExit(
                f"source parquet is missing required field(s) {missing_required}; "
                f"columns present: {list(df.columns)[:40]}")
        for canonical in ALIASES:
            col.setdefault(canonical, resolve(df, canonical))

        out = pd.DataFrame()
        out["device_id"] = df[col["device_id"]].astype(str)
        out["failure_probability"] = pd.to_numeric(df[col["failure_probability"]], errors="coerce")

        out["device_category"] = (df[col["device_category"]].astype(str)
                                  if col["device_category"] else None)
        if col["device_category"] is None:
            self.warnings.append("no device_category column - loaded as NULL")

        if col["decision_threshold"]:
            out["decision_threshold"] = pd.to_numeric(df[col["decision_threshold"]], errors="coerce")
        else:
            out["decision_threshold"] = None
            self.warnings.append("no threshold column - decision_threshold NULL")

        if col["predicted_label"]:
            out["predicted_label"] = pd.to_numeric(df[col["predicted_label"]], errors="coerce")
        elif col["decision_threshold"]:
            out["predicted_label"] = (out["failure_probability"] >= out["decision_threshold"]).astype(int)
            self.warnings.append("predicted_label derived from probability >= threshold")
        else:
            out["predicted_label"] = None

        if col["prediction_date"]:
            out["prediction_date"] = pd.to_datetime(df[col["prediction_date"]], errors="coerce").dt.date
        else:
            out["prediction_date"] = self.computed_date
            self.warnings.append(f"no date column - prediction_date set to {self.computed_date}")

        out["facility_id"] = df[col["facility_id"]].astype(str) if col["facility_id"] else None

        if col["risk_band"]:
            out["risk_band"] = df[col["risk_band"]].astype(str)
        else:
            out["risk_band"] = out["failure_probability"].map(band)
            self.warnings.append("risk_band derived from probability (0.70/0.50/0.30 cuts)")

        for c in ("matched_serial_nbr", "component_type", "component_age_days"):
            out[c] = df[col[c]] if col[c] else None
        if col["matched_serial_nbr"] is None:
            self.warnings.append("no component serial column - ps1_serial_predictions will be EMPTY")

        self.norm = out
        return self

    # ------------------------------------------------------------ device grain
    def build_device(self):
        d = self.norm.drop_duplicates(subset=["device_id", "prediction_date"], keep="first")
        n_src, n_dev = len(self.norm), len(d)
        rows = []
        for r in d.itertuples(index=False):
            rows.append(dict(
                city_id=self.city,
                prediction_id=short_id(self.run_id, r.device_id, r.prediction_date),
                device_category=r.device_category, device_id=r.device_id,
                facility_id=r.facility_id,
                failure_probability=None if pd.isna(r.failure_probability) else round(float(r.failure_probability), 5),
                predicted_label=None if r.predicted_label is None or pd.isna(r.predicted_label) else int(r.predicted_label),
                decision_threshold=None if r.decision_threshold is None or pd.isna(r.decision_threshold) else round(float(r.decision_threshold), 5),
                prediction_date=r.prediction_date,
                inference_ts=None,
                computed_date=self.computed_date,
                run_id=self.run_id, model_version=None,
                target_col=self.target_col, risk_band=r.risk_band,
                matched_serial_nbr=None,
            ))
        self.rows["ps1_failure_predictions"] = rows
        self.warnings.append(f"device grain: {n_src:,} source rows -> {n_dev:,} device-day rows")
        return self

    # ------------------------------------------------------------ serial grain
    def build_serial(self):
        s = self.norm[self.norm["matched_serial_nbr"].notna()].copy()
        if s.empty:
            self.rows["ps1_serial_predictions"] = []
            return self
        s = s.drop_duplicates(subset=["device_id", "matched_serial_nbr", "prediction_date"])
        counts = s.groupby(["device_id", "prediction_date"])["matched_serial_nbr"].transform("size")
        s["attribution_weight"] = (1.0 / counts).round(5)
        s["serial_risk_score"] = (s["failure_probability"] * s["attribution_weight"]).round(5)
        rows = []
        for r in s.itertuples(index=False):
            rows.append(dict(
                city_id=self.city, run_id=self.run_id,
                device_id=r.device_id, matched_serial_nbr=str(r.matched_serial_nbr),
                device_category=r.device_category,
                component_type=None if r.component_type is None or pd.isna(r.component_type) else str(r.component_type),
                component_age_days=None if r.component_age_days is None or pd.isna(r.component_age_days) else round(float(r.component_age_days), 2),
                device_failure_probability=None if pd.isna(r.failure_probability) else round(float(r.failure_probability), 5),
                attribution_weight=float(r.attribution_weight),
                serial_risk_score=None if pd.isna(r.serial_risk_score) else float(r.serial_risk_score),
                risk_band=r.risk_band,
                prediction_date=r.prediction_date, computed_date=self.computed_date,
            ))
        self.rows["ps1_serial_predictions"] = rows
        self.warnings.append(
            f"attribution_weight = EQUAL SPLIT ({self.attribution}) - a placeholder, not a "
            "learned per-component risk")
        return self

    # ------------------------------------------------------------ run registry
    def build_runs(self, gold_s3, thresholds):
        self.gold_s3 = gold_s3
        rows = []
        cats = ([c for c in self.norm["device_category"].dropna().unique()]
                if self.norm["device_category"].notna().any() else ["UNKNOWN"])
        for cat in cats:
            sub = self.norm[self.norm["device_category"] == cat] if cat != "UNKNOWN" else self.norm
            th = thresholds.get(str(cat).upper(), {})
            rows.append(dict(
                city_id=self.city, run_id=self.run_id,
                run_ts=datetime.datetime.combine(self.computed_date, datetime.time()),
                run_kind="batch_score", device_category=str(cat),
                scoring_date=self.computed_date,
                endpoint_name=None, serving_image=None, model_version=None,
                mlflow_version=str(th.get("mlflow_version")) if th.get("mlflow_version") else None,
                target_col=self.target_col,
                decision_threshold=(round(float(th["active_threshold"]), 5)
                                    if th.get("active_threshold") is not None else None),
                n_devices_scored=int(sub["device_id"].nunique()),
                n_flagged=int(pd.to_numeric(sub["predicted_label"], errors="coerce").fillna(0).sum()),
                gold_snapshot_s3=gold_s3, status="success", error_text=None, duration_s=None,
                computed_date=self.computed_date,
            ))
        self.rows["ps1_inference_runs"] = rows
        return self

    def build_performance(self, thresholds):
        rows = []
        for cat, th in thresholds.items():
            rows.append(dict(
                city_id=self.city, device_category=cat,
                model_name=th.get("mlflow_model_name"), algorithm=th.get("champion_name"),
                train_auc=None, train_ap=None, train_f1=None,
                val_auc=None, val_ap=None, val_f1=None,
                test_auc=None,
                test_ap=(round(float(th["test_ap"]), 4) if th.get("test_ap") is not None else None),
                test_f1=None, test_prec=None, test_rec=None,
                decision_threshold=(round(float(th["active_threshold"]), 5)
                                    if th.get("active_threshold") is not None else None),
                mlflow_version=str(th.get("mlflow_version")) if th.get("mlflow_version") else None,
                endpoint_name=None, n_features=None,
                quality_gate=("PASS" if th.get("quality_gate_pass") else "FAIL"),
                promoted=bool(th.get("quality_gate_pass")),
                computed_date=self.computed_date,
                target_col=self.target_col, label_revision=self.label_revision,
                recall_floor=(round(float(th["recall_floor"]), 4)
                              if th.get("recall_floor") is not None else None),
                base_rate_pct=None, run_id=self.run_id,
            ))
            if not th.get("quality_gate_pass"):
                self.warnings.append(
                    f"{cat}: quality_gate FAIL (recall floor {th.get('recall_floor')}) - "
                    "loaded with quality_gate='FAIL'; the dashboard must mask it")
        self.rows["ps1_model_performance"] = rows
        return self

    def run(self, gold_s3, thresholds):
        self.warnings.append(LABEL_BANNER.format(
            target=self.target_col, semantics=self.label_semantics, rev=self.label_revision))
        self.normalise().build_device().build_serial()
        self.build_runs(gold_s3, thresholds)
        if thresholds:
            self.build_performance(thresholds)
        else:
            self.rows["ps1_model_performance"] = []
            self.warnings.append(
                "no --threshold-json given -> ps1_model_performance EMPTY; /ps1/model-performance "
                "will return no rows (export threshold_final_{cat}.json from MLflow to fill it)")
        for t, r in self.rows.items():
            self.stats[t] = len(r)
        return self


ORDER = ["ps1_inference_runs", "ps1_model_performance",
         "ps1_failure_predictions", "ps1_serial_predictions"]

DELETE_KEYS = {
    "ps1_inference_runs":      "city_id = :c AND run_id = :r",
    "ps1_model_performance":   "city_id = :c AND computed_date = :d",
    "ps1_failure_predictions": "city_id = :c AND computed_date = :d",
    "ps1_serial_predictions":  "city_id = :c AND run_id = :r",
}


def write_db(loader, dsn, batch=500):
    import pg8000.native, urllib.parse as up
    u = up.urlparse(dsn)
    kw = dict(user=up.unquote(u.username or "postgres"),
              host=u.hostname or "localhost", port=u.port or 5432,
              database=(u.path or "/postgres").lstrip("/"))
    if u.password:
        kw["password"] = up.unquote(u.password)
    if kw["host"] not in ("localhost", "127.0.0.1"):
        kw["ssl_context"] = True
    conn = pg8000.native.Connection(**kw)
    loaded = {}
    for t in ORDER:
        rows = loader.rows.get(t, [])
        conn.run(f"DELETE FROM {t} WHERE {DELETE_KEYS[t]}",
                 c=loader.city, r=loader.run_id, d=loader.computed_date)
        if not rows:
            loaded[t] = 0
            continue
        cols = list(rows[0].keys())
        collist = ", ".join(cols)
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            tuples, params = [], {}
            for n, r in enumerate(chunk):
                tuples.append("(" + ", ".join(f":p{n}_{c}" for c in cols) + ")")
                for c in cols:
                    params[f"p{n}_{c}"] = r[c]
            conn.run(f"INSERT INTO {t} ({collist}) VALUES " + ", ".join(tuples), **params)
        loaded[t] = len(rows)
    # ml_batch_load_audit: one row per target table (schema per sql/16)
    for t in ORDER:
        try:
            conn.run(
                "INSERT INTO ml_batch_load_audit"
                " (city_id, ps_id, run_id, target_table, s3_source, rows_read, rows_loaded,"
                "  columns_added, columns_skipped, status, error_text)"
                " VALUES (:c, 'PS1', :r, :t, :src, :rr, :rl, NULL, NULL, :st, :note)",
                c=loader.city, r=loader.run_id, t=t, src=loader.gold_s3,
                rr=int(loader.source_rows), rl=int(loaded.get(t, 0)),
                st="ok" if loaded.get(t, 0) else "ok_empty",
                note="; ".join(loader.warnings)[:2000])
        except Exception as e:
            print(f"  [warn] ml_batch_load_audit insert skipped for {t}: {str(e)[:140]}")
            break
    conn.close()
    return loaded


def load_thresholds(paths):
    out = {}
    for p in paths or []:
        with open(p, encoding="utf-8") as fh:
            j = json.load(fh)
        cat = str(j.get("category") or j.get("device_category") or "").upper()
        if not cat:
            print(f"  [warn] {p} has no 'category' key - skipped")
            continue
        out[cat] = j
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True,
                    help="local dir or file for device_ps1_cross_wired_daily "
                         "(aws s3 cp --recursive it down first)")
    ap.add_argument("--city", default="CHI")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--computed-date", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--threshold-json", action="append",
                    help="threshold_final_{cat}.json; repeatable, one per device category")
    ap.add_argument("--gold-s3",
                    default="s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/"
                            "chicago/gold/device_ps1_cross_wired_daily")
    ap.add_argument("--target-col", default=TARGET_COL_DEFAULT,
                    help="label the model was trained on (default: hardware-OOS, NOT chargeable)")
    ap.add_argument("--label-revision", default=LABEL_REVISION_DEFAULT)
    ap.add_argument("--dsn", default=os.environ.get("PS1_DSN"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cd = (datetime.date.fromisoformat(a.computed_date) if a.computed_date
          else datetime.date.today())
    run_id = a.run_id or f"ps1_{cd.isoformat().replace('-', '')}"

    df, nfiles = read_parquet_any(a.parquet)
    thresholds = load_thresholds(a.threshold_json)
    ld = Loader(df, a.city, run_id, cd, target_col=a.target_col,
                label_revision=a.label_revision).run(a.gold_s3, thresholds)

    print(f"PS1 backfill  city={a.city}  run_id={run_id}  computed_date={cd}")
    print(f"source: {a.parquet}  ({nfiles} parquet file(s), {len(df):,} rows, "
          f"{len(df.columns)} columns)")
    print("-" * 70)
    for t in ORDER:
        print(f"  {t:28} {ld.stats.get(t, 0):>8,} rows")
    print("-" * 70)
    for w in ld.warnings:
        print(f"  ! {w}")

    if a.dry_run or not a.dsn:
        print("\n(dry-run - nothing written)" if a.dry_run else "\nNo --dsn/PS1_DSN; not written.")
        return 0
    print("\nLOADED:", json.dumps(write_db(ld, a.dsn)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
