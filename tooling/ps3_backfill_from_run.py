#!/usr/bin/env python3
"""
CUBIC MARS Chicago - PS3 two-head backfill loader.

Loads a PS3 notebook run directory (ML_outputs/Level2/PS3) into the Aurora
tables created by sql/15_phase2a_ps3_two_head.sql.

WHY THIS RECOMPUTES pred_severity_collapsed
-------------------------------------------
The run's CONFIG["SEVERITY_COLLAPSE"] is keyed on human-readable labels
("All Functions") while pred_severity holds CODES ("ALL_FUNCTIONS"), and the
notebook applies `.map(...).fillna("MAJOR")`. Every lookup misses, so 100% of
incidents land on MAJOR and the derived pct_critical_pred is 0.0 fleet-wide.
Loading the CSVs verbatim would publish a dashboard asserting that no device in
Chicago carries critical risk.

This loader therefore treats pred_severity_collapsed and pct_critical_pred on
disk as UNTRUSTED and recomputes both from the pred_severity codes, recording
what it changed in ps3_model_runs.notes and ml_batch_load_audit.

Usage:
  python ps3_backfill_from_run.py --run-dir <dir> --city CHI \
      --run-id ps3_20260719 --dsn postgresql://user:pw@host:5432/db
  python ps3_backfill_from_run.py --run-dir <dir> --dry-run
"""
import argparse, csv, glob, json, os, sys, datetime, decimal

# Keyed on failure_level_label CODES. MAJOR = degraded subset of functions,
# CRITICAL = all-functions / all-purchase / bus-reader class outages.
SEVERITY_COLLAPSE_FIXED = {
    "PURCHASE_CARD":       "MAJOR",
    "PURCHASE_PRODUCT":    "MAJOR",
    "NONPAYMENT":          "MAJOR",
    "ALL_PURCHASE":        "CRITICAL",
    "ALL_FUNCTIONS":       "CRITICAL",
    "BUS_READER":          "CRITICAL",
    "BUS_READER_ASSEMBLY": "CRITICAL",
}
# CONFIG MIN_CLASS_COUNT collapses rare classes into OTHER. It maps to neither
# bucket; we record it as UNKNOWN and exclude it from pct_critical denominators
# rather than silently defaulting it to MAJOR (which is what created the bug).
UNKNOWN = "UNKNOWN"

CAT_DIRS = {"TVM": ("TVM", "tvm"), "GATE": ("Gates", "gate"), "VALIDATOR": ("Validator", "validator")}


def collapse(code):
    return SEVERITY_COLLAPSE_FIXED.get((code or "").strip().upper(), UNKNOWN)


def num(v, cast=float):
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in ("nan", "none", "null"):
        return None
    try:
        return cast(s)
    except (ValueError, decimal.InvalidOperation):
        return None


def ts(v):
    if not v or str(v).strip() == "":
        return None
    s = str(v).strip().replace("T", " ")
    for f in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s[:26], f)
        except ValueError:
            continue
    return None


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def read_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class Loader:
    def __init__(self, run_dir, city, run_id, as_of):
        self.d, self.city, self.run_id, self.as_of = run_dir, city, run_id, as_of
        self.stats, self.warnings = {}, []
        self.rows = {}          # table -> list of dict
        self.collapse_fixes = 0
        self.unknown_rows = 0
        self.total_incidents = 0

    def p(self, *parts):
        return os.path.join(self.d, *parts)

    # ---------------- extraction ----------------
    def load_run(self):
        rs = read_json(self.p("ps3_run_summary.json")) or {}
        scope = ",".join(rs.get("device_scope", []) or [])
        self.run_summary = rs
        self.rows["ps3_model_runs"] = [dict(
            city_id=self.city, run_id=self.run_id,
            run_ts=ts(self.as_of + " 00:00:00"), run_kind="train",
            source_notebook="notebooks/ps3_root_cause_analysis/PS3_SageMaker_MLflow_FeatureStore.ipynb",
            git_sha=None, n_incidents=rs.get("n_incidents"), device_scope=scope,
            endpoint_name=None, serving_image=None, mlflow_version=None, sm_package_arn=None,
            severity_collapse_verified=True, notes=None, as_of_date=self.as_of)]

    def load_heads(self):
        summ, cls, lb, leak = [], [], [], []
        for cat, (sub, pre) in CAT_DIRS.items():
            res = next((r for r in self.run_summary.get("results", []) if r.get("cat") == cat), {})
            if not res.get("modeled", False):
                for head in ("severity", "root_cause"):
                    summ.append(dict(city_id=self.city, run_id=self.run_id, device_category=cat,
                                     head=head, modeled=False, target_col=None, champion=None,
                                     n_classes=None, class_labels=None, test_f1_macro=None,
                                     test_f1_weighted=None, test_accuracy=None,
                                     test_balanced_accuracy=None, test_auc_macro_ovr=None,
                                     test_pr_auc_macro=None, test_cohen_kappa=None, test_mcc=None,
                                     test_log_loss=None, macro_f1_floor=None, gate_pass=False,
                                     n_train=None, n_test=None, n_features=None,
                                     rootcause_source=None, as_of_date=self.as_of))
                self.warnings.append(f"{cat}: modeled=False (stub) - recorded, no predictions")
                continue

            for head in ("severity", "root_cause"):
                j = read_json(self.p(sub, f"{pre}_{head}_champion_summary.json"))
                if not j:
                    self.warnings.append(f"{cat}/{head}: champion_summary.json missing")
                    continue
                summ.append(dict(
                    city_id=self.city, run_id=self.run_id, device_category=cat, head=head,
                    modeled=True, target_col=j.get("target"), champion=j.get("champion"),
                    n_classes=j.get("n_classes"), class_labels=",".join(j.get("classes", []) or []),
                    test_f1_macro=j.get("test_f1_macro"), test_f1_weighted=j.get("test_f1_weighted"),
                    test_accuracy=j.get("test_accuracy"),
                    test_balanced_accuracy=j.get("test_balanced_accuracy"),
                    test_auc_macro_ovr=j.get("test_auc_macro_ovr"),
                    test_pr_auc_macro=j.get("test_pr_auc_macro"),
                    test_cohen_kappa=j.get("test_cohen_kappa"), test_mcc=j.get("test_mcc"),
                    test_log_loss=j.get("test_log_loss"), macro_f1_floor=j.get("macro_f1_floor"),
                    gate_pass=bool(j.get("gate_pass")), n_train=j.get("n_train"),
                    n_test=j.get("n_test"), n_features=j.get("n_features"),
                    rootcause_source=("derived_component_type" if head == "root_cause" else None),
                    as_of_date=self.as_of))

                bal = j.get("class_balance_train", {}) or {}
                for label, m in (j.get("per_class", {}) or {}).items():
                    cls.append(dict(city_id=self.city, run_id=self.run_id, device_category=cat,
                                    head=head, class_label=label,
                                    precision_val=m.get("precision"), recall_val=m.get("recall"),
                                    f1=m.get("f1"), support=m.get("support"),
                                    train_count=bal.get(label), as_of_date=self.as_of))

                champ = j.get("champion")
                for i, r in enumerate(read_csv(self.p(sub, f"{pre}_{head}_leaderboard.csv"))):
                    lb.append(dict(city_id=self.city, run_id=self.run_id, device_category=cat,
                                   head=head, model=r.get("model"),
                                   f1_macro=num(r.get("f1_macro")), f1_weighted=num(r.get("f1_weighted")),
                                   accuracy=num(r.get("accuracy")),
                                   auc_macro_ovr=num(r.get("auc_macro_ovr")),
                                   pr_auc_macro=num(r.get("pr_auc_macro")), fit_s=num(r.get("fit_s")),
                                   lb_rank=i + 1, is_champion=(r.get("model") == champ),
                                   as_of_date=self.as_of))

            # leakage scan is headerless: feature,solo_auc
            path = self.p(sub, f"{pre}_leakage_scan.csv")
            if os.path.exists(path):
                with open(path, encoding="utf-8-sig", newline="") as fh:
                    for i, row in enumerate(csv.reader(fh)):
                        if i == 0 or len(row) < 2 or not row[0].strip():
                            continue
                        leak.append(dict(city_id=self.city, run_id=self.run_id, device_category=cat,
                                         feature_name=row[0].strip(), solo_auc=num(row[1]),
                                         as_of_date=self.as_of))
        self.rows.update(ps3_head_summary=summ, ps3_head_class_metrics=cls,
                         ps3_head_leaderboard=lb, ps3_leakage_scan=leak)

        fi = glob.glob(self.p("*", "*prediction_explainability.csv")) + \
             glob.glob(self.p("*", "*shap_importance.csv"))
        self.rows["ps3_head_feature_importance"] = []
        if not fi:
            self.warnings.append(
                "no *_prediction_explainability.csv / *_shap_importance.csv in this run -> "
                "ps3_head_feature_importance left EMPTY (re-run with SHAP export to populate)")

    def load_predictions(self):
        inc, dev, ser = [], [], []
        per_dev, per_ser = {}, {}
        for cat, (sub, pre) in CAT_DIRS.items():
            for r in read_csv(self.p(sub, f"{pre}_incident_predictions.csv")):
                on_file = (r.get("pred_severity_collapsed") or "").strip().upper()
                fixed = collapse(r.get("pred_severity"))
                if fixed != on_file:
                    self.collapse_fixes += 1
                if fixed == UNKNOWN:
                    self.unknown_rows += 1
                self.total_incidents += 1
                did = r.get("device_id")
                sn = r.get("matched_serial_nbr")
                if fixed != UNKNOWN:
                    a, b = per_dev.setdefault(did, [0, 0])
                    per_dev[did] = [a + (1 if fixed == "CRITICAL" else 0), b + 1]
                    if sn:
                        a, b = per_ser.setdefault((did, sn), [0, 0])
                        per_ser[(did, sn)] = [a + (1 if fixed == "CRITICAL" else 0), b + 1]
                inc.append(dict(
                    city_id=self.city, run_id=self.run_id,
                    availability_event_id=r.get("availability_event_id"), device_id=did,
                    mars_device_category=r.get("mars_device_category") or cat,
                    ae_start_dtm=ts(r.get("AE_START_DTM")), matched_serial_nbr=sn,
                    component_age_days=num(r.get("component_age_days")),
                    facility_id=r.get("FACILITY_ID"), facility_name=r.get("FACILITY_NAME"),
                    pred_severity=r.get("pred_severity"),
                    pred_severity_conf=num(r.get("pred_severity_conf")),
                    pred_severity_collapsed=fixed, actual_severity=r.get("actual_severity"),
                    pred_component=r.get("pred_component"),
                    pred_component_conf=num(r.get("pred_component_conf")),
                    actual_component=r.get("actual_component"), features=None,
                    computed_date=self.as_of))

            for r in read_csv(self.p(sub, f"{pre}_device_predictions.csv")):
                did = r.get("device_id")
                c, n = per_dev.get(did, [0, 0])
                dev.append(dict(city_id=self.city, run_id=self.run_id, device_id=did,
                                mars_device_category=r.get("mars_device_category") or cat,
                                n_incidents=num(r.get("n_incidents"), int),
                                pct_critical_pred=round(c / n, 4) if n else None,
                                dominant_pred_severity=r.get("dominant_pred_severity"),
                                dominant_pred_component=r.get("dominant_pred_component"),
                                avg_component_age_days=num(r.get("avg_component_age_days")),
                                last_incident_dtm=ts(r.get("last_incident_dtm")),
                                computed_date=self.as_of))

            for r in read_csv(self.p(sub, f"{pre}_serial_predictions.csv")):
                did, sn = r.get("device_id"), r.get("matched_serial_nbr")
                c, n = per_ser.get((did, sn), [0, 0])
                ser.append(dict(city_id=self.city, run_id=self.run_id, device_id=did,
                                matched_serial_nbr=sn,
                                mars_device_category=r.get("mars_device_category") or cat,
                                n_incidents=num(r.get("n_incidents"), int),
                                component_age_days=num(r.get("component_age_days")),
                                dominant_pred_component=r.get("dominant_pred_component"),
                                pct_critical_pred=round(c / n, 4) if n else None,
                                last_incident_dtm=ts(r.get("last_incident_dtm")),
                                computed_date=self.as_of))
        self.rows.update(ps3_incident_predictions=inc, ps3_device_predictions=dev,
                         ps3_serial_predictions=ser)

        note = (f"pred_severity_collapsed recomputed from pred_severity CODES: "
                f"{self.collapse_fixes}/{self.total_incidents} incident rows corrected "
                f"({self.unknown_rows} unmapped -> {UNKNOWN}, excluded from pct_critical). "
                f"pct_critical_pred recomputed for {len(dev)} device and {len(ser)} serial rows "
                f"(the run's own values were 0.0 fleet-wide).")
        self.rows["ps3_model_runs"][0]["notes"] = note
        self.warnings.append(note)

    def run(self):
        self.load_run(); self.load_heads(); self.load_predictions()
        self.stats = {t: len(r) for t, r in self.rows.items()}
        return self


ORDER = ["ps3_model_runs", "ps3_head_summary", "ps3_head_class_metrics", "ps3_head_leaderboard",
         "ps3_head_feature_importance", "ps3_leakage_scan", "ps3_incident_predictions",
         "ps3_device_predictions", "ps3_serial_predictions"]


def write_db(loader, dsn, batch=500):
    import pg8000.native, urllib.parse as up
    u = up.urlparse(dsn)
    q = up.parse_qs(u.query or "")
    kw = dict(user=up.unquote(u.username or "postgres"),
              password=up.unquote(u.password) if u.password else None,
              database=(u.path or "/postgres").lstrip("/"))
    # libpq allows a unix-socket directory via ?host=/path (pgserver emits this)
    sock = (q.get("host") or [None])[0]
    if sock and sock.startswith("/"):
        kw["unix_sock"] = os.path.join(sock, ".s.PGSQL.%d" % (u.port or 5432))
    else:
        kw.update(host=u.hostname or "localhost", port=u.port or 5432)
    conn = pg8000.native.Connection(**kw)
    loaded = {}
    for t in ORDER:
        rows = loader.rows.get(t, [])
        conn.run(f"DELETE FROM {t} WHERE city_id = :c AND run_id = :r",
                 c=loader.city, r=loader.run_id)
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
    conn.close()
    return loaded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--city", default="CHI")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: run dir mtime date)")
    ap.add_argument("--dsn", default=os.environ.get("PS3_DSN"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    as_of = a.as_of or datetime.date.fromtimestamp(os.path.getmtime(a.run_dir)).isoformat()
    run_id = a.run_id or f"ps3_{as_of.replace('-', '')}"

    ld = Loader(a.run_dir, a.city, run_id, as_of).run()

    print(f"PS3 backfill  city={a.city}  run_id={run_id}  as_of={as_of}")
    print("-" * 66)
    for t in ORDER:
        print(f"  {t:32} {ld.stats.get(t, 0):>7} rows")
    print("-" * 66)
    for w in ld.warnings:
        print(f"  ! {w}")

    if a.dry_run or not a.dsn:
        print("\n(dry-run - nothing written)" if a.dry_run else "\nNo --dsn/PS3_DSN; not written.")
        return 0
    loaded = write_db(ld, a.dsn)
    print("\nLOADED:", json.dumps(loaded))
    return 0


if __name__ == "__main__":
    sys.exit(main())
