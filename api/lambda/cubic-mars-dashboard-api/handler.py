"""
cubic-mars-dashboard-api  â€”  VPC Lambda for the CUBIC MARS Chicago dashboard.
Jobs: 1. action=migrate -> applies sql/01..08 to Aurora (missing files skipped)
      2. HTTP API -> read routes for PS1/PS2/PS3/PS5 (+device-level) + PS4 alerts
pg8000 pure-python driver (no native build).
Phase-1e adds 3 PS2 analytics routes: /ps2/paths, /ps2/ignition, /ps2/impact.
"""
import os, json, re, boto3, pg8000.native
from decimal import Decimal

def _json_default(o):
    if isinstance(o, Decimal): return float(o)
    return str(o)

_SECRETS = boto3.client("secretsmanager")
_conn = None

# 29-Jul-2026. The socket timeout is now a variable, not a literal.
# 15s is right for a read route -- API Gateway gives up at 30s anyway, so a
# longer route timeout only holds the container. It is WRONG for DDL: one
# statement in sql/11 runs past 15s, pg8000 raises "The read operation timed
# out", and the socket is left unusable. migrate() raises this for its own
# run and puts it back before returning.
_CONN_TIMEOUT = 60

def _creds():
    s = json.loads(_SECRETS.get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"])
    return {
        "user": s.get("username") or s.get("user") or "postgres",
        "password": s.get("password"),
        "host": s.get("host") or os.environ["RDS_HOST"],
        "port": int(s.get("port") or os.environ.get("RDS_PORT", 5432)),
        "database": s.get("dbname") or s.get("database") or os.environ.get("DB_NAME", "postgres"),
    }

def conn():
    global _conn
    # 28-Jul-2026. A CACHED CONNECTION CAN BE DEAD.
    #
    # Lambda keeps a warm container between invocations. The socket to Aurora
    # closes while that container is idle, and every later request on it then
    # fails with
    #     {"error": "cannot read from timed out object"}
    # until Lambda happens to recycle the container. That is why a route can
    # serve 600 rows, 500 for several minutes, then recover on its own -- the
    # behaviour tracks container lifetime, not the query.
    #
    # Ping before use and rebuild on failure. The round trip is sub-millisecond
    # in-VPC and turns an intermittent 500 into a deterministic reconnect.
    if _conn is not None:
        try:
            _conn.run("SELECT 1")
            return _conn
        except Exception:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
    c = _creds()
    _conn = pg8000.native.Connection(
        user=c["user"], password=c["password"], host=c["host"],
        port=c["port"], database=c["database"], ssl_context=True,
        timeout=_CONN_TIMEOUT)
    return _conn

def split_sql(sql):
    stmts, buf, i, n = [], [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "-" and sql[i:i+2] == "--":
            j = sql.find("\n", i); j = n if j < 0 else j; buf.append(sql[i:j]); i = j; continue
        if ch == "/" and sql[i:i+2] == "/*":
            j = sql.find("*/", i); j = n if j < 0 else j+2; buf.append(sql[i:j]); i = j; continue
        if ch == "'":
            j = i+1
            while j < n:
                if sql[j] == "'" and sql[j:j+2] == "''": j += 2; continue
                if sql[j] == "'": break
                j += 1
            buf.append(sql[i:j+1]); i = j+1; continue
        if ch == "$":
            m = re.match(r"\$[A-Za-z0-9_]*\$", sql[i:])
            if m:
                tag = m.group(0); k = sql.find(tag, i+len(tag))
                k = n if k < 0 else k+len(tag); buf.append(sql[i:k]); i = k; continue
        if ch == ";":
            s = "".join(buf).strip()
            if s: stmts.append(s)
            buf = []; i += 1; continue
        buf.append(ch); i += 1
    tail = "".join(buf).strip()
    if tail: stmts.append(tail)
    return stmts

_OK = ("already exists", "is not available", "does not exist, skipping")

# 29-Jul-2026. ONE DEAD SOCKET USED TO KILL THE ENTIRE MIGRATION.
#
# migrate() took a connection once and reused it for every statement in every
# file. When a statement in sql/11 exceeded the socket timeout, pg8000 left
# that socket unreadable, and all 26 files after it failed with
#     cannot read from timed out object
# -- 27 files reported as broken by one slow CREATE. The conn() ping added on
# 28-Jul could not help: migrate never called conn() a second time.
#
# A dead socket is not a failed statement. This tells the two apart, rebuilds
# the connection, and retries ONCE. A statement that fails again on a fresh
# connection is a real error and is reported as one.
_DEAD = ("timed out", "cannot read from", "connection is closed",
         "broken pipe", "network error", "server socket", "connection reset")

def _migrate_run(st):
    """Run one statement. Returns None on success, else the exception."""
    global _conn
    try:
        conn().run(st); return None
    except Exception as e:
        if not any(t in str(e).lower() for t in _DEAD):
            return e
        try: _conn.close()
        except Exception: pass
        _conn = None
        try:
            conn().run(st); return None
        except Exception as e2:
            return e2
def migrate(_evt):
    here = os.path.dirname(__file__); results = {}
    # Longer socket timeout for DDL, and a FRESH connection so the new value
    # actually takes effect -- a cached socket keeps the timeout it was built
    # with. Restored just before the return.
    global _CONN_TIMEOUT, _conn
    _prev_timeout = _CONN_TIMEOUT
    _CONN_TIMEOUT = 180
    try: _conn.close()
    except Exception: pass
    _conn = None
    conn()
    for fn in ("sql/01_schema_core.sql", "sql/02_phase1_ps2_ps5_backfill.sql",
               "sql/03_phase1b_ps3_severity.sql", "sql/04_phase1c_ps1_failure.sql",
               "sql/05_phase1d_ps2_device.sql", "sql/06_phase1d_ps3_device.sql",
               "sql/07_phase1e_ps2_new.sql", "sql/08_ps2_run_backfill.sql",
               "sql/09_phase1f_ps2_rich.sql", "sql/10_ps2_run_backfill_2.sql",
               "sql/11_phase1g_ps1_serving.sql", "sql/12_ps1_serving_backfill.sql",
               "sql/13_phase2_device360.sql", "sql/14_phase3_dim_station.sql",
               "sql/15_phase2a_ps3_two_head.sql",
               "sql/16_phase2b_ps1_batch_lineage.sql",
               "sql/17_phase2c_ps3_two_head_views.sql",
               "sql/18_purge_ps3_bridge_test_rows.sql",
               "sql/19_ps3_severity_collapse_fix.sql",
               "sql/20_ps4_anomaly.sql",
               "sql/21_fleet_event_baseline.sql",
               "sql/22_dim_device_serial.sql",
               # 27-Jul-2026. Must come AFTER sql/22 -- v_ps3_category_coverage
               # reads v_device_serial, which sql/22 creates.
               "sql/23_ps3_coverage.sql",
               # 27-Jul-2026. Puts all three device types into the PS3 risk
               # views. AFTER sql/23 -- it reads ps3_category_coverage to decide
               # whether an unscored device is out of feed or merely unscored.
               "sql/24_ps3_all_devices.sql",
               # 27-Jul-2026. PS4 tables shaped to the notebooks' REAL export
               # layout (artifacts bucket, chicago/ps4/scored/asof=<date>).
               # sql/20's assumed shape is left in place but stays empty.
               "sql/25_ps4_scored.sql",
               # 27-Jul-2026. Adds scope to ps2_network_centrality and rebuilds
               # its primary key. Without it the PS2 load dies on a duplicate
               # key for (CHI, PRINTER, 2026-07-26): the export carries the same
               # subsystem once per scope (ALL/TVM/GATE/VALIDATOR) and the old
               # key had room for one. Must be applied BEFORE the ps2 loader.
               "sql/26_ps2_scope.sql",
               # 27-Jul-2026. The three device-grain PS2 tables the loader
               # reported as no_target, plus v_ps2_device_cascade. AFTER sql/26
               # only by ordering convention -- no dependency between them.
               "sql/27_ps2_device_grain.sql",
               # 27-Jul-2026. Rebuilds dim_device_serial from dim_device_component
               # + dim_device_station. MUST come after sql/22 (which creates the
               # table and its views) and after load_run has filled the two
               # source tables -- migrate runs first on a deploy, so on a cold
               # database this inserts nothing and the next deploy fills it.
               "sql/28_dim_serial_backfill.sql",
               # 27-Jul-2026. PS5 notebook-output tables, shaped to the real CSV
               # headers. Independent of everything above it.
               "sql/29_ps5_outputs.sql",
               # 27-Jul-2026. ps5_serial_rul -- the export's own table. The
               # legacy ps5_serial_reliability rejects it on two NOT NULL
               # columns the notebook does not produce.
               "sql/30_ps5_serial_rul.sql",
               # 27-Jul-2026. Drops sql/30's unique index -- (device_id,
               # component_serial_nbr) is NOT the grain for TVM or validators --
               # and adds v_ps5_serial_dupes to measure the real grain from the
               # loaded rows instead of guessing at it.
               "sql/31_ps5_serial_rul_grain.sql",
               # 27-Jul-2026. Deduplicates v_ps5_serial_rul. The grain audit
               # proved validators' duplicate rows are identical on every
               # measured column -- a roster fan-out, up to 25 copies of one
               # component -- so DISTINCT is safe and the raw table keeps the
               # evidence. Adds v_ps5_serial_fanout to keep the defect visible.
               "sql/32_ps5_serial_dedup.sql",
               # 28-Jul-2026. PS4 device_daily + lifetime rollup, and the views
               # that reproduce the anomalies / outliers feeds without storing
               # 6.4M redundant rows.
               "sql/33_ps4_device_daily.sql",
               "sql/34_ps1_cross_wired.sql",
               "sql/35_ps1_label_onset.sql",
               "sql/36_ps1_state_framing.sql",
               "sql/37_ps1_predictions_xw.sql",
               "sql/38_ps4_weekly_v3.sql",
               "sql/39_ps3_v2.sql",
               "sql/40_ps3_v2_rootcause.sql",
               "sql/41_dim_device_bus.sql",
               "sql/42_ps2_serial_grain.sql",
               # 02-Aug-2026. The 20 tables published by PS2 v2.5.2/2.5.3.
               # Purely additive; the 27 legacy PS2 tables are untouched.
               "sql/44_ps2_v25.sql"):
        path = os.path.join(here, fn)
        if not os.path.exists(path):
            results[fn] = {"skipped": "file not present"}; continue
        applied = tolerated = failed = 0; errs = []
        reconnects = 0
        for st in split_sql(open(path).read()):
            _e = _migrate_run(st)
            if _e is None:
                applied += 1
            else:
                msg = str(_e).lower()
                if any(t in msg for t in _OK): tolerated += 1
                else:
                    failed += 1
                    if any(t in msg for t in _DEAD): reconnects += 1
                    if len(errs) < 8: errs.append(str(_e)[:180])
        results[fn] = {"applied": applied, "tolerated": tolerated, "failed": failed, "errors": errs}
        if reconnects:
            results[fn]["dead_socket_after_retry"] = reconnects
    try:
        results["dim_station_seed.csv"] = _seed_dim_station_csv(conn())
    except Exception as e:
        results["dim_station_seed.csv"] = {"error": str(e)[:180]}
    _CONN_TIMEOUT = _prev_timeout
    return {"statusCode": 200, "body": json.dumps({"migrate": results}, default=str)}

# ---------------------------------------------------------------------------
# load_run  (added 2026-07-26)
#
# Applies the real-run data files in sql/load/. These are DELIBERATELY NOT in
# migrate()'s file list. migrate() runs on every single deploy, so a data INSERT
# living there refills the tables behind your back after any purge -- which is
# precisely what the 8 relocated seed blocks were doing and why every wipe kept
# "undoing itself". This action is explicit, idempotent (each file DELETEs its
# own run_id/as_of_date first), and reports per-file counts.
#
#   aws lambda invoke ... --payload '{"action":"load_run"}'            # all
#   aws lambda invoke ... --payload '{"action":"load_run","only":["ps3"]}'
# ---------------------------------------------------------------------------
LOAD_FILES = [
    # 29-Jul-2026. Bus assignment for 4,218 validators. Data, not schema --
    # deliberately here and not in migrate(), which runs on every deploy.
    ("bus_map", "sql/load/bus_map_20260729.sql"),
    ("ps1",           "sql/load/ps1_run_20260726.sql"),
    # 26-Jul-2026: the sklearn v3 bake-off SUPERSEDES the Spark metrics for
    # display (PK's selection). It runs straight after "ps1" so its DELETE of the
    # 26-Jul as_of_date removes the Spark leaderboard before inserting its own.
    ("ps1_sklearn",   "sql/load/ps1_sklearn_20260726.sql"),
    # Must follow "ps1": it UPDATEs the ps1_inference_runs row that file inserts.
    ("ps1_predictions", "sql/load/ps1_predictions_20260726.sql"),
    ("ps3",           "sql/load/ps3_run_20260726.sql"),
    ("ps3_incidents", "sql/load/ps3_incidents_20260726.sql"),
    # 27-Jul-2026. Which device types PS3 covers and why VALIDATOR is empty.
    # Follows "ps3" because its run_id must already exist in v_ps3_latest_run for
    # v_ps3_category_coverage to select it.
    ("ps3_coverage",  "sql/load/ps3_coverage_20260726.sql"),
    # LAST on purpose: it UPDATEs the PS1 prediction rows with facility_id and
    # component identity, and builds ps1_station_summary from the joined result,
    # so both PS1 files must already be committed when it runs.
    ("station_dim",   "sql/load/dim_device_station_20260726.sql"),
]

def load_run(evt):
    here = os.path.dirname(__file__)
    only = evt.get("only")
    if only is not None and not isinstance(only, list):
        return err(400, "only must be a list of keys, e.g. [\"ps3\",\"ps3_incidents\"]")
    picked = [(k, f) for k, f in LOAD_FILES if only is None or k in only]
    if not picked:
        return err(400, f"nothing matched; valid keys are {[k for k, _ in LOAD_FILES]}")

    c = conn(); results = {}
    for key, fn in picked:
        path = os.path.join(here, fn)
        if not os.path.exists(path):
            results[key] = {"skipped": "file not present in the deployed package"}; continue
        applied = tolerated = failed = 0; errs = []
        try:
            c.run("BEGIN")
            for st in split_sql(open(path).read()):
                # split_sql hands back comment-only chunks (the file headers). psql
                # ignores those; pg8000 raises on an empty query, and since this file
                # is one transaction that error would roll back the whole load. Skip
                # them here rather than relying on the _OK tolerance list.
                if not [ln for ln in st.splitlines()
                        if ln.strip() and not ln.strip().startswith("--")]:
                    continue
                try:
                    c.run(st); applied += 1
                except Exception as e:
                    if any(t in str(e).lower() for t in _OK): tolerated += 1
                    else:
                        failed += 1
                        if len(errs) < 6: errs.append(str(e)[:200])
            if failed:
                c.run("ROLLBACK")
                results[key] = {"status": "rolled_back", "applied": 0, "failed": failed,
                                "errors": errs,
                                "note": "one file = one transaction; nothing from this file landed"}
                continue
            c.run("COMMIT")
        except Exception as e:
            try: c.run("ROLLBACK")
            except Exception: pass
            results[key] = {"status": "rolled_back", "error": str(e)[:300]}; continue
        results[key] = {"status": "committed", "statements": applied, "tolerated": tolerated,
                        "failed": 0}

    # Row counts after the load, so the response itself is the verification.
    tally = {}
    for tb in ("ps1_model_performance", "ps1_leaderboard", "ps1_confusion",
               "ps1_feature_importance", "ps1_threshold_sweep", "ps1_risk_bands",
               "ps1_risk_trend", "ps1_inference_runs", "ps1_failure_predictions", "ps1_serial_predictions",
               "ps3_head_summary", "ps3_head_leaderboard", "ps3_head_class_metrics",
               "ps3_leakage_scan", "ps3_device_predictions", "ps3_serial_predictions",
               "ps3_incident_predictions", "ps3_model_runs", "ps1_station_summary",
               "dim_device_station", "dim_device_component"):
        try:
            tally[tb] = int(c.run(f"SELECT COUNT(*) FROM {tb} WHERE city_id=:c",
                                  c=str(evt.get("city") or CITY).upper())[0][0])
        except Exception as e:
            tally[tb] = f"count failed: {str(e)[:80]}"
    return ok({"action": "load_run", "files": results, "rows_after": tally,
               "note": "PS1 device/serial rows are VALIDATOR only. All three v3 notebooks write to the "
                       "same unpartitioned gold path device_ps1_cross_wired_daily, so VALIDATOR "
                       "(last to run) overwrote the 184,386 TVM and 107,110 GATE rows their logs "
                       "report writing. ps1_explainability stays empty on purpose: the run emits "
                       "fleet-average SHAP broadcast to every row, not per-row contributions."})

def rows(sql, **kw):
    c = conn(); res = c.run(sql, **kw); cols = [d["name"] for d in c.columns]
    return [dict(zip(cols, r)) for r in res]

# Query-string integers reach here as strings and may be absent, blank, or
# hostile. Clamped rather than trusted: an unbounded LIMIT from the URL is a
# denial-of-service on a 34,612-row table, and a negative OFFSET is a 500.
def _clamp_int(v, default, lo, hi):
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))

def ok(payload): return {"statusCode": 200, "headers": {"content-type": "application/json",
    "access-control-allow-origin": "*"}, "body": json.dumps(payload, default=_json_default)}
def err(code, m): return {"statusCode": code, "headers": {"access-control-allow-origin": "*"},
    "body": json.dumps({"error": m})}

# ---------------------------------------------------------------------------
# Model verdict (added 2026-07-26)
#
# A high accuracy is not evidence when one class dominates. The PS3 GATE
# severity head is the worked example: on a test split of 335 ALL_FUNCTIONS + 3
# OTHER it reports accuracy 0.99112, macro-F1 0.49777, weighted-F1 0.98671,
# balanced accuracy 0.5000, Cohen kappa 0.0 and MCC 0.0 -- which are, to every
# digit, the exact scores of a model that outputs ALL_FUNCTIONS unconditionally.
# Five different algorithms (RandomForest / HistGBM / LightGBM / XGBoost /
# CatBoost) reported identical figures to five decimal places, which independent
# learners cannot do unless they are all emitting the same constant.
#
# So instead of filtering on an accuracy threshold -- which would keep exactly
# these models and discard the ones that work -- classify each row:
#
#   degenerate   : provably no better than a constant predictor.
#                  * multi-class: macro-F1 <= 1/n_classes. A constant predictor
#                    with majority share p scores (1/k)*2p/(1+p), whose supremum
#                    as p->1 is 1/k. Scoring at or under 1/k therefore means the
#                    model carries no class-discriminating information.
#                  * binary: recall == 0 (never fires) or AUC <= 0.5 (at or
#                    below chance ranking).
#   below_floor  : real model, missed its promotion gate.
#   ok           : cleared its gate.
#
# `evidence` is a short human string so the dashboard can show WHY, not just a
# colour. Nothing is deleted server-side; the caller opts in with
# ?exclude_degenerate=true.
# ---------------------------------------------------------------------------
def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None

def model_verdict(*, f1=None, auc=None, recall=None, n_classes=None,
                  floor=None, gate_pass=None):
    f1, auc, recall = _num(f1), _num(auc), _num(recall)
    floor, k = _num(floor), _num(n_classes)
    if k and k >= 2 and f1 is not None and f1 <= (1.0 / k) + 1e-9:
        # Distinguish "carries no signal at all" from "ranks well but the decision
        # threshold collapses onto the majority class". The GATE severity champion
        # is the first case (AUC 0.4358, below chance); GATE LogReg is arguably the
        # second (AUC 0.6358) -- same macro-F1 region, very different remedy.
        if auc is not None and auc > 0.55:
            return "below_floor", (f"macro-F1 {f1:.4f} <= 1/{int(k)} = {1.0/k:.4f} at the chosen "
                                   f"threshold, but AUC {auc:.4f} shows the ranking does carry "
                                   f"signal -- this is a threshold/class-imbalance problem, not a dead model")
        return "degenerate", (f"macro-F1 {f1:.4f} <= 1/{int(k)} = {1.0/k:.4f} -- "
                              f"no better than always predicting the majority class")
    if recall is not None and recall == 0:
        return "degenerate", "recall 0.0000 -- the model never predicts the positive class"
    if auc is not None and auc <= 0.5:
        return "degenerate", f"AUC {auc:.4f} <= 0.50 -- ranking is at or below chance"
    if gate_pass is False:
        return "below_floor", (f"macro-F1 {f1:.4f} below floor {floor:.2f}"
                               if f1 is not None and floor is not None else "missed its promotion gate")
    if floor is not None and f1 is not None and f1 < floor:
        return "below_floor", f"F1 {f1:.4f} below floor {floor:.2f}"
    return "ok", "cleared its promotion gate"

def _truthy(v): return str(v).strip().lower() in ("1", "true", "yes", "y")

CITY = "CHI"
def q(city):
    return city if city in ("CHI", "BOS", "LAX", "TOC") else "CHI"


# ============ Device-360 : cross-PS aggregation for one device (grounded, honest) ============
_PS5_TYPE = {"TVM": "tvms", "GATE": "gates", "VALIDATOR": "validators"}

def _seed_dim_station_csv(c):
    """Full-coverage loader: if sql/dim_station_seed.csv is present (Databricks export of
    silver.dim_facility / dim_device DISTINCT facility_id,facility_name[,operator]), batch-upsert it
    into dim_station. Flexible header mapping. Batched (200/stmt) for speed. Returns a status dict."""
    import csv as _csv
    p = os.path.join(os.path.dirname(__file__), "sql", "dim_station_seed.csv")
    if not os.path.exists(p):
        return {"skipped": "no sql/dim_station_seed.csv (dim_station keeps its seeded rows)"}
    def pick(row, *names):
        for k in row:
            if k and k.strip().lower() in names:
                return row[k]
        return None
    data = []
    with open(p, newline="", encoding="utf-8-sig") as f:
        for row in _csv.DictReader(f):
            fid = pick(row, "facility_id", "facid", "facility")
            name = pick(row, "station_name", "facility_name", "facility_short_name")
            if not fid or not str(fid).strip() or not name or not str(name).strip():
                continue
            op = pick(row, "operator", "operator_name")
            op = op.strip() if op else None
            if op and op.lower() in ("null", "none", ""):
                op = None
            data.append((str(fid).strip()[:20], str(name).strip()[:120], (op[:80] if op else None)))
    # dedupe by facility_id (export may key one facility with several operators) so no intra-batch conflict
    _seen = {}
    for fid, name, op in data:
        if fid not in _seen:
            _seen[fid] = (fid, name, op)
    data = list(_seen.values())
    n = 0
    B = 200
    for i in range(0, len(data), B):
        chunk = data[i:i+B]; vals = []; params = {}
        for j, (fid, name, op) in enumerate(chunk):
            vals.append(f"('CHI',:f{j},:n{j},:o{j},'databricks')")
            params[f"f{j}"] = fid; params[f"n{j}"] = name; params[f"o{j}"] = op
        sql = ("INSERT INTO dim_station(city_id,facility_id,station_name,operator,source) VALUES "
               + ",".join(vals)
               + " ON CONFLICT (city_id,facility_id) DO UPDATE SET station_name=EXCLUDED.station_name, operator=EXCLUDED.operator, source='databricks', loaded_at=NOW()")
        try:
            c.run(sql, **params); n += len(chunk)
        except Exception:
            pass
    return {"loaded": n}

def _safe_rows(sql, **kw):
    """rows() that degrades to [] instead of taking the whole response with it.

    _device_360 stitches together five problem statements plus causation. Before
    this, ONE query against a table whose schema had drifted returned 42703 and
    the entire modal came back as {"error": ...} -- every panel blank, with no
    indication which of the twelve queries was at fault.

    A cross-PS view is a partial-evidence document by nature: PS4 having nothing
    to say about a device is a normal outcome, not a failure. So a section that
    cannot be read is now an absent section, and the reason is logged rather
    than raised.
    """
    try:
        return rows(sql, **kw)
    except Exception as e:
        print("device_360 sub-query failed, section skipped: %s", str(e)[:200])
        return []


def _device_360(city, dev):
    out = {"device_id": dev, "city": "Chicago"}
    cat = None; pid = None; prob = None; thr = None
    # ---- PS1 : device-level prediction + SHAP drivers ----
    p = _safe_rows("SELECT prediction_id,device_category,facility_id,failure_probability,predicted_label,decision_threshold,prediction_date FROM ps1_failure_predictions WHERE city_id=:c AND device_id=:d AND computed_date=(SELECT MAX(computed_date) FROM ps1_failure_predictions WHERE city_id=:c) ORDER BY failure_probability DESC LIMIT 1", c=city, d=dev)
    if p:
        r = p[0]; cat = r["device_category"]; pid = r["prediction_id"]
        prob = float(r["failure_probability"] or 0); thr = float(r["decision_threshold"] or 0)
        band = "Critical" if prob >= max(0.5, thr*2) else "High" if prob >= thr else "Medium" if prob >= thr*0.5 else "Low"
        drivers = _safe_rows("SELECT feature_name,shap_value,feature_value FROM ps1_explainability WHERE city_id=:c AND prediction_id=:p AND computed_date=(SELECT MAX(computed_date) FROM ps1_explainability WHERE city_id=:c) ORDER BY ABS(shap_value) DESC LIMIT 8", c=city, p=pid)
        if not drivers and cat:
            drivers = [{"feature_name": x["feature_name"], "shap_value": x["avg_shap"], "feature_value": None}
                       for x in _safe_rows("SELECT feature_name,avg_shap FROM ps1_feature_importance WHERE city_id=:c AND device_category=:d AND computed_date=(SELECT MAX(computed_date) FROM ps1_feature_importance WHERE city_id=:c) ORDER BY feat_rank LIMIT 8", c=city, d=cat)]
        out["ps1"] = {"level": "device", "found": True, "device_category": cat, "facility_id": r["facility_id"],
                      "failure_probability": prob, "predicted_label": r["predicted_label"], "decision_threshold": thr,
                      "risk_band": band, "prediction_date": str(r["prediction_date"]), "drivers": drivers}
        # 27-Jul-2026. The REVIEW-ONLY warning here was hard-coded from an
        # earlier run and contradicted the data actually loaded: the 26-Jul run
        # writes quality_gate='PASS' and promoted=TRUE for all three categories.
        # A note that disagrees with the scorecard on the same screen is worse
        # than no note, so it is now read from the row rather than asserted.
        perf = _safe_rows("SELECT quality_gate, promoted, test_auc, test_ap, test_prec, test_rec, "
                    "decision_threshold, algorithm, mlflow_version "
                    "FROM ps1_model_performance WHERE city_id=:c AND device_category=:k "
                    "ORDER BY computed_date DESC LIMIT 1", c=city, k=cat) if cat else []
        if perf:
            out["ps1"].update({k2: perf[0][k2] for k2 in perf[0]})
            out["ps1"]["note"] = (
                f"Champion {perf[0].get('algorithm')} â€” quality gate "
                f"{perf[0].get('quality_gate')}, promoted={perf[0].get('promoted')}. "
                f"Held-out AUC {perf[0].get('test_auc')}, AP {perf[0].get('test_ap')}, "
                f"precision {perf[0].get('test_prec')}, recall {perf[0].get('test_rec')}.")
        else:
            out["ps1"]["note"] = "No PS1 model-performance row for this category."
    else:
        out["ps1"] = {"level": "device", "found": False, "note": "No PS1 prediction for this device in the latest run."}
    # ---- PS1 cross-wired fallback (28-Jul-2026) -------------------------
    # MEASURED by the RDS audit, not inferred: ps1_explainability,
    # ps1_feature_importance and ps1_model_performance all hold ZERO rows, and
    # ps1_failure_predictions covers 1,922 rows against a 4,217-device fleet.
    # So the block above returns found=False for most devices and, when it does
    # find one, drivers comes back empty from two empty tables. That -- not a
    # broken route -- is why the Analyse button has been blank.
    #
    # ps1_cross_wired_daily holds 786,525 rows covering every scored device with
    # its SHAP triple, threshold, tier, facility and spell state. Use it whenever
    # the legacy path came up short.
    #
    # Keyed on device_id, NEVER device_key: device_key is the SCD2 surrogate and
    # one device accumulates many versions of it, so joining on it multiplies
    # rows -- the defect that inflated the PS5 validator roster 25x.
    xw = _safe_rows("SELECT device_type, facility_id, last_scored_day, ps1_fail_prob, "
                    "ps1_risk_tier, threshold_used, device_state, state_note, "
                    "current_spell_day, n_spells, total_oos_days, days_since_spell_end "
                    "FROM v_ps1_xw_device_state WHERE city_id=:c AND device_id=:d",
                    c=city, d=dev)
    if xw:
        s = dict(xw[0])
        # Always attached, even when the legacy block found a row: the state is
        # what tells a reader whether a CRITICAL badge is a live problem or a
        # description of an outage that has already been repaired.
        out["ps1_state"] = s
        if not out["ps1"].get("found"):
            _d = _safe_rows("SELECT shap_feat1, shap_val1, shap_feat2, shap_val2, "
                            "shap_feat3, shap_val3, component_type "
                            "FROM v_ps1_device_drivers WHERE city_id=:c AND device_id=:d "
                            "LIMIT 1", c=city, d=dev)
            _drv = []
            if _d:
                _g = _d[0]
                for _i in (1, 2, 3):
                    _f = _g.get("shap_feat%d" % _i)
                    if _f is not None:
                        _drv.append({"feature_name": _f,
                                     "shap_value": _g.get("shap_val%d" % _i),
                                     "feature_value": None})
            _p = float(s.get("ps1_fail_prob") or 0)
            _t = float(s.get("threshold_used") or 0)
            cat = cat or s.get("device_type")
            prob = _p
            thr = _t
            out["ps1"] = {
                "level": "device", "found": True,
                "source": "ps1_cross_wired_daily",
                "device_category": s.get("device_type"),
                "facility_id": s.get("facility_id"),
                "failure_probability": _p,
                "predicted_label": 1 if (_t and _p >= _t) else 0,
                "decision_threshold": _t,
                "risk_band": s.get("ps1_risk_tier"),
                "prediction_date": str(s.get("last_scored_day")),
                "drivers": _drv,
                # The framing belongs on the device panel too. A probability
                # shown without it reads as a failure forecast, which it is not:
                # 95-98% of positive label days merely follow another positive
                # day, so the score is an out-of-service STATE signal.
                "note": ("This score reflects out-of-service STATE, not a "
                         "forecast. " + str(s.get("state_note") or "")),
            }
    # ---- PS2 : device-level catalog + cascade rank + recent event-code chains ----
    cat2 = _safe_rows("SELECT device_name,serial,category,control_group,facility,operator,cascade_days,avg_chain_len,max_chain_len,dom_subsystem,dom_error_code,worst_cascade_path,worst_window FROM ps2_device_catalog WHERE city_id=:c AND device_id=:d AND computed_date=(SELECT MAX(computed_date) FROM ps2_device_catalog WHERE city_id=:c) LIMIT 1", c=city, d=dev)
    # 27-Jul-2026. Was ps2_top_devices, which the S3 pipeline never loads -- it
    # held ONE seed row, so every device except BMV01005 came back with no
    # cascade rank at all. v_ps2_device_cascade is built from the 4,673 devices
    # the 27-Jul load actually put in Aurora, and carries the recurrence join.
    #
    # The w0_5..w60plus window split is NOT selected here because the PS2 export
    # does not produce one at device grain. The modal's cascade-timing panel is
    # gated on cascade_window_total, so it simply does not render -- which is the
    # correct outcome, rather than five zeroed bars.
    topd = _safe_rows("SELECT impact_rank AS dev_rank, impact_cascade_days AS cascade_days,"
                " total_impact, avg_impact, recurrence_cascade_days, chronic,"
                " impact_rank_in_category"
                " FROM v_ps2_device_cascade WHERE city_id=:c AND device_id=:d LIMIT 1",
                c=city, d=dev)
    chains = _safe_rows("SELECT transit_day,event_code_chain,subsystem_chain,chain_length,chain_span_min,first_subsystem,last_subsystem FROM ps2_device_cascades WHERE city_id=:c AND device_id=:d AND computed_date=(SELECT MAX(computed_date) FROM ps2_device_cascades WHERE city_id=:c) ORDER BY transit_day DESC LIMIT 5", c=city, d=dev)
    ps2 = {"level": "device", "in_catalog": bool(cat2), "in_top_devices": bool(topd)}
    if cat2: ps2.update(cat2[0])
    if topd:
        t0 = topd[0]
        ps2["cascade_rank"] = t0["dev_rank"]
        ps2["cascade_rank_in_category"] = t0.get("impact_rank_in_category")
        ps2["total_impact"] = t0.get("total_impact")
        ps2["avg_impact"] = t0.get("avg_impact")
        # chronic comes through a LEFT JOIN on ps2_recurrence, so a missing
        # recurrence row means UNKNOWN, not "not chronic". Only assert the flag
        # when the row that carries it actually exists.
        if t0.get("recurrence_cascade_days") is not None:
            ps2["recurrence_cascade_days"] = t0.get("recurrence_cascade_days")
            ps2["chronic"] = bool(t0.get("chronic"))
    ps2["recent_cascades"] = chains
    # Chain shape, from the catalog columns that were already loaded. A long mean
    # chain means one fault reliably drags others with it; that is what makes a
    # device worth pre-empting rather than repairing.
    if cat2:
        ps2["chain_profile"] = {
            "avg_chain_len": cat2[0].get("avg_chain_len"),
            "max_chain_len": cat2[0].get("max_chain_len"),
            "worst_window": cat2[0].get("worst_window"),
            "worst_cascade_path": cat2[0].get("worst_cascade_path"),
        }
    # Which subsystem the cascades START at, counted over the recent chains.
    # dom_subsystem in the catalog is the most FREQUENT subsystem overall; the
    # first link is a different and more useful question for maintenance -- it is
    # where the intervention goes.
    if chains:
        _first = {}
        for _c in chains:
            _k = _c.get("first_subsystem")
            if _k: _first[_k] = _first.get(_k, 0) + 1
        if _first:
            ps2["cascade_entry_subsystem"] = max(_first, key=_first.get)
            ps2["cascade_entry_counts"] = _first
    if cat is None and cat2: cat = cat2[0]["category"]
    out["ps2"] = ps2
    # ---- PS3 : category-level SEVERITY model (not per-device root cause) ----
    # 27-Jul-2026. This block read ps3_device_metrics -- a CATEGORY-level table --
    # and carried a note claiming per-device root cause was "blocked on
    # SVN_STAGE". Both are out of date: the 26-Jul two-head run writes
    # ps3_device_predictions at device grain, and v_ps3_device_360 serves it
    # gate-aware. The modal now gets this device's own incident count, severity
    # and attributed subsystem instead of its category's test metrics.
    d3 = _safe_rows("SELECT mars_device_category,n_incidents,pct_critical_pred,dominant_pred_severity,"
              "dominant_pred_component,avg_component_age_days,last_incident_dtm,n_serials,"
              "severity_shippable,rootcause_shippable "
              "FROM v_ps3_device_360 WHERE city_id=:c AND device_id=:d", c=city, d=dev)
    if d3:
        r3 = dict(d3[0])
        # The components actually fitted to this device, by serial. Different
        # taxonomy from dominant_pred_component (the attributed SUBSYSTEM), and
        # the pair read together is the useful thing.
        r3["components"] = _safe_rows(
            "SELECT matched_serial_nbr, component_description, component_age_days, "
            " n_incidents, coverage_status "
            "FROM v_ps3_serial_all WHERE city_id=:c AND device_id=:d "
            "ORDER BY n_incidents DESC NULLS LAST, component_description LIMIT 12",
            c=city, d=dev)
        r3["level"] = "device"; r3["found"] = True
        out["ps3"] = r3
    else:
        # No PS3 row at all. Distinguish "this device type has no PS3 feed"
        # (validators) from "modelled type, no incidents in this run".
        cov = _safe_rows("SELECT modeled, exclusion_reason, alternative_coverage "
                   "FROM v_ps3_category_coverage WHERE city_id=:c AND device_category=:k",
                   c=city, k=cat) if cat else []
        if cov and not cov[0]["modeled"]:
            out["ps3"] = {"level": "device", "found": False, "category": cat,
                          "not_in_feed": True,
                          "note": cov[0]["exclusion_reason"],
                          "alternative_coverage": cov[0]["alternative_coverage"]}
        else:
            out["ps3"] = {"level": "device", "found": False, "category": cat,
                          "note": "No PS3 incident attributed to this device in the latest run."}
    # ---- CAUSATION (added 2026-07-27) ----------------------------------------
    # Everything above tells you WHAT happened on this device. This answers "and
    # then what" -- given the subsystem the cascades on this device start at,
    # which subsystem follows, how reliably, and how long you have before it does.
    #
    # Four independent methods on the same anchor, all from the 27-Jul S3 load:
    #   association rules   lift  -- co-occurrence above chance
    #   markov transitions  prob  -- next-state probability
    #   conditional prob    P(B|A) within a time window
    #   lead/lag timing     median minutes from A to B
    # They are deliberately NOT merged into one score. Lift and probability
    # answer different questions, and a single blended number would hide the case
    # that matters most: a pair that is highly probable but barely lifted is just
    # a common subsystem, not a causal link.
    #
    # And the standing caveat, stated in the payload rather than left implied:
    # these are sequential associations over event chains. Ordering plus lift is
    # evidence for causation, not proof of it.
    # Anchor, in falling order of directness. This block sits AFTER the PS3 one
    # so the third fallback is available: a device PS2 has never recorded a chain
    # on can still be traced forward from the subsystem PS3 ATTRIBUTED its
    # failures to. Without that fallback the whole section vanished for every
    # device outside the PS2 catalogue -- which is most validators, and is why it
    # was not showing on the PS1 and PS3 tabs.
    _p3 = out.get("ps3") or {}
    anchor = (ps2.get("cascade_entry_subsystem")
              or (cat2[0].get("dom_subsystem") if cat2 else None)
              or _p3.get("dominant_pred_component"))
    if anchor:
        _lat = ("AND computed_date=(SELECT MAX(computed_date) FROM {t} WHERE city_id=:c)")
        causation = {
            "anchor_subsystem": anchor,
            "anchor_source": ("cascade entry (first link of this device's recent chains)"
                              if ps2.get("cascade_entry_subsystem")
                              else "dominant subsystem in this device's PS2 catalogue row"
                              if (cat2 and cat2[0].get("dom_subsystem"))
                              else "subsystem PS3 attributes this device's failures to"),
            "rules": _safe_rows(
                "SELECT consequent_subsystem AS to_sub, support, confidence, lift, conviction "
                "FROM ps2_subsystem_associations WHERE city_id=:c AND antecedent_subsystem=:a "
                + _lat.format(t="ps2_subsystem_associations") +
                " ORDER BY lift DESC NULLS LAST LIMIT 6", c=city, a=anchor),
            "transitions": _safe_rows(
                "SELECT to_sub, prob FROM ps2_markov_transitions "
                "WHERE city_id=:c AND from_sub=:a "
                + _lat.format(t="ps2_markov_transitions") +
                " ORDER BY prob DESC NULLS LAST LIMIT 6", c=city, a=anchor),
            "conditional": _safe_rows(
                "SELECT sub_b AS to_sub, window_bucket, p_b_given_a FROM ps2_conditional_prob "
                "WHERE city_id=:c AND sub_a=:a "
                + _lat.format(t="ps2_conditional_prob") +
                " ORDER BY p_b_given_a DESC NULLS LAST LIMIT 8", c=city, a=anchor),
            "timing": _safe_rows(
                "SELECT sub_b AS to_sub, n_events, mean, median, p25, p75 "
                "FROM ps2_leadlag_timing WHERE city_id=:c AND sub_a=:a "
                + _lat.format(t="ps2_leadlag_timing") +
                " ORDER BY n_events DESC NULLS LAST LIMIT 6", c=city, a=anchor),
            "caveat": ("Sequential association over event chains: A is observed before B "
                       "more often than chance. Ordering plus lift is evidence for "
                       "causation, not proof of it."),
        }
        # The headline: the strongest LIFTED consequent that also has a timing
        # row, because a link you cannot put a clock on is not actionable.
        _t_by = {r["to_sub"]: r for r in causation["timing"]}
        for _r in causation["rules"]:
            if _r["to_sub"] in _t_by:
                causation["lead_link"] = {
                    "from_sub": anchor, "to_sub": _r["to_sub"],
                    "lift": _r.get("lift"), "confidence": _r.get("confidence"),
                    "median_minutes": _t_by[_r["to_sub"]].get("median"),
                    "n_observations": _t_by[_r["to_sub"]].get("n_events"),
                }
                break
        out["causation"] = causation
    else:
        # Still returned, with the reason. A section that silently disappears
        # reads as "this feature is broken"; one that says why it is empty is
        # information. The reason is specific -- which of the three possible
        # anchors was missing -- rather than a generic "no data".
        # 28-Jul-2026. Falling back to FLEET-level cascade paths rather than
        # returning an empty section.
        #
        # ps2_device_catalog holds 200 devices and ps2_device_cascades 1,000
        # rows, both stamped 14-Jul -- so for most of the fleet there is no
        # device-specific anchor and this branch was all anyone ever saw. The
        # subsystem-level tables ARE current (ps2_markov_transitions 36 rows,
        # ps2_conditional_prob 450, ps2_leadlag_timing 80, all 26-Jul), so the
        # fleet's strongest observed transitions can still be shown.
        #
        # scope is set to "fleet" and said plainly in the note. Showing a
        # fleet-level chain while implying it is device-specific would be worse
        # than showing nothing -- the whole point of this panel is what follows
        # THIS device.
        _fleet = _safe_rows(
            "SELECT from_sub, to_sub, transition_prob AS confidence, n_events "
            "FROM ps2_markov_transitions WHERE city_id=:c "
            "ORDER BY n_events DESC NULLS LAST LIMIT 6", c=city)
        _ftime = _safe_rows(
            "SELECT from_sub, to_sub, median_minutes AS median, n_events "
            "FROM ps2_leadlag_timing WHERE city_id=:c "
            "ORDER BY n_events DESC NULLS LAST LIMIT 6", c=city)
        out["causation"] = {
            "anchor_subsystem": None,
            "scope": "fleet",
            "rules": _fleet,
            "timing": _ftime,
            "note": ("No cascade chain or catalogue row exists for this device, so "
                     "no device-specific starting link can be traced. Showing the "
                     "FLEET's most frequently observed subsystem transitions "
                     "instead -- these describe Chicago as a whole, NOT this "
                     "device."),
            "caveat": ("Fleet-level sequential association: A is observed before B "
                       "more often than chance across all devices. Ordering plus "
                       "frequency is evidence toward causation, not proof of it, "
                       "and none of it is specific to this device."),
        }
    # ---- PS4 : device-level anomaly alerts ----
    al = _safe_rows("SELECT detected_at,triggering_signal,anomaly_score,severity,status,description FROM ps4_anomaly_alerts WHERE city_id=:c AND device_id=:d ORDER BY detected_at DESC LIMIT 5", c=city, d=dev)
    out["ps4"] = {"level": "device", "alert_count": len(al), "alerts": al,
                  "note": "PS4 anomaly ensemble (unsupervised); thresholds under recalibration."}
    # ---- PS5 : category-level reliability / RUL ----
    # 27-Jul-2026. ps5_reliability_estimates is DEVICE grain and carries the two
    # RUL figures; the modal was only ever showing the category's concordance
    # index and gate state, which says nothing about the device in front of you.
    # Device row first, category status kept underneath as context.
    # 27-Jul-2026. device_type REMOVED from this select and the call made safe.
    #
    # ps5_reliability_estimates declares device_type in sql/01, but the column
    # does not exist in Aurora -- sql/01 reports `failed: 1` with exactly
    # `column "device_type" does not exist` on every single deploy, so the
    # CREATE never fully applied. Selecting it raised 42703, and because nothing
    # here caught it the WHOLE /ps1/device-360 response collapsed to
    # {"error": ...}. That is why every panel in the Analyse modal rendered
    # blank: not five empty models, one unhandled exception.
    #
    # ps5["category"] already carries the device type from the PS1 lookup, so
    # nothing is lost by dropping the column.
    d5 = _safe_rows("SELECT as_of_date, concordance_index, rul_standard_days, "
                    "rul_conservative_days, reader_fault_count_30d, data_quality_gate_passed "
                    "FROM ps5_reliability_estimates WHERE city_id=:c AND device_id=:d "
                    "ORDER BY as_of_date DESC LIMIT 1", c=city, d=dev)
    ps5 = {"level": "device" if d5 else "category", "category": cat}
    if d5:
        ps5.update(d5[0])
        ps5["found"] = True
        _std = d5[0].get("rul_standard_days")
        _con = d5[0].get("rul_conservative_days")
        # The spread between the two RUL estimates is the model's own uncertainty.
        # A wide spread means "we do not really know" and should temper any
        # scheduling decision taken off the standard figure alone.
        if _std is not None and _con is not None:
            try:
                ps5["rul_spread_days"] = round(float(_std) - float(_con), 1)
                ps5["rul_uncertainty"] = ("wide" if float(_std) and
                                          (float(_std) - float(_con)) / float(_std) > 0.5
                                          else "narrow")
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        ps5["note"] = ("Device-level RUL from ps5_reliability_estimates. "
                       + ("Data-quality gate PASSED." if d5[0].get("data_quality_gate_passed")
                          else "Data-quality gate NOT passed â€” treat the RUL as indicative only."))
    else:
        ps5["found"] = False
    if cat:
        p5 = _PS5_TYPE.get(str(cat).upper(), str(cat).lower())
        _allrel = _safe_rows("SELECT device_type,concordance_index,registry_status,dashboard_ready,blockers FROM ps5_reliability_status WHERE city_id=:c", c=city)
        rel = [r for r in _allrel if str(r.get("device_type")).lower() == p5]  # filter in python: no enum::text cast (pg8000.native-safe)
        ps5["reliability"] = rel[0] if rel else None
        if not d5:
            ps5["note"] = "No device-level RUL row for this device; showing category reliability status only."
    out["ps5"] = ps5
    # ---- v2/v3 ENRICHMENT (29-Jul-2026) -------------------------------------
    # The Analyse modal previously stopped at the first-generation feeds. The
    # PS3 hardened run and the PS4 v3 weekly run both carry per-device detail
    # that was reachable only by leaving the modal and searching another tab.
    #
    # Every block is wrapped in _safe_rows-style tolerance: a missing v2/v3
    # table returns an empty section, never a 500. The modal was killed once by
    # exactly this class of failure and must not be again.
    def _q(sql, **kw):
        try:
            return rows(sql, **kw)
        except Exception as e:
            print("device360 v2/v3 section skipped:", type(e).__name__, str(e)[:160])
            return []

    # Bus identity. Operations dispatch to a VEHICLE, not a device id, so the
    # modal leads with it. Missing bus is rendered as "not assigned" by the view
    # rather than left blank, because a blank cell reads as a broken join.
    try:
        _bus = rows("SELECT bus_id, bus_label, on_vehicle, serial_number,"
                    " component_serial_nbr, component_type, facility_name,"
                    " operator_name, transit_mode_name FROM v_device_bus"
                    " WHERE city_id=:c AND device_id=:d LIMIT 1", c=city, d=dev)
        out["bus_identity"] = _bus[0] if _bus else {"bus_label": "no bus mapping for this device"}
    except Exception as _e:
        print("bus identity skipped:", str(_e)[:140])
        out["bus_identity"] = {"bus_label": "bus mapping table not loaded"}

    ps3v2 = {}
    # Root cause: which component this device's incidents actually came from.
    # OBSERVED attribution, not a prediction - the run publishes no per-incident
    # predicted component, and the wording must not imply one.
    ps3v2["rootcause"] = _q(
        "SELECT component_label, component_label_semantics, serial_number,"
        " incident_count, critical_rate, recurrence_30d, latest_incident_at,"
        " taxonomy_note FROM v_ps3_v2_rootcause WHERE city_id=:c AND device_id=:d"
        " ORDER BY critical_weighted DESC NULLS LAST LIMIT 10", c=city, d=dev)
    ps3v2["concentration"] = _q(
        "SELECT serial_number, total_incidents, distinct_components, concentration,"
        " concentration_verdict FROM v_ps3_v2_rootcause_concentration"
        " WHERE city_id=:c AND device_id=:d ORDER BY concentration DESC LIMIT 5", c=city, d=dev)
    # Incidents: the open severity queue for this device.
    ps3v2["incidents"] = _q(
        "SELECT source_event_timestamp, predicted_severity, action_band,"
        " critical_probability, prediction_confidence, confidence_band,"
        " action_priority_score, prior_incidents_30d, recurrence_note, model_status"
        " FROM v_ps3_v2_queue WHERE city_id=:c AND device_id=:d"
        " ORDER BY action_priority_score DESC NULLS LAST LIMIT 10", c=city, d=dev)
    # Per-incident SHAP - the plain-English why behind the severity call.
    ps3v2["drivers"] = _q(
        "SELECT feature, feature_value, shap_value, predicted_label, event_timestamp"
        " FROM v_ps3_v2_shap WHERE city_id=:c AND device_id=:d"
        " ORDER BY event_timestamp DESC, feature_rank LIMIT 8", c=city, d=dev)
    ps3v2["serial_risk"] = _q(
        "SELECT serial_number, incident_count, critical_incidents, critical_rate,"
        " max_prior_incidents_30d, latest_incident_at FROM ps3_v2_device_serial"
        " WHERE city_id=:c AND device_id=:d ORDER BY critical_rate DESC NULLS LAST LIMIT 5",
        c=city, d=dev)
    ps3v2["found"] = any(bool(ps3v2[k]) for k in ("rootcause", "incidents", "drivers", "serial_risk"))
    ps3v2["basis"] = ("Root cause is OBSERVED attribution from incident history, not a model "
                      "prediction. Severity IS a model output, with its confidence band shown.")
    out["ps3_v2_rootcause_360"] = ps3v2

    # PS4 v3: the weekly anomaly picture and which behaviour cluster this device
    # sits in. cluster_distance_ratio_max > 1 means it sat further from its own
    # cluster centre than 99% of the training population.
    ps4v3 = {}
    ps4v3["weeks"] = _q(
        "SELECT week_start, week_end, severity, anomaly_types, is_actionable_week,"
        " actionable_days, candidate_days, observed_days, anomaly_score_max,"
        " anomaly_score_p95, max_abs_z, cluster_distance_ratio_max,"
        " dominant_cluster_id, partial_week FROM v_ps4_weekly_device"
        " WHERE city_id=:c AND device_id=:d ORDER BY week_start DESC LIMIT 12", c=city, d=dev)
    if ps4v3["weeks"]:
        cid = ps4v3["weeks"][0].get("dominant_cluster_id")
        dt = None
        try:
            dt = (out.get("ps1") or {}).get("device_category")
        except Exception:
            dt = None
        if cid is not None:
            ps4v3["cluster"] = _q(
                "SELECT device_type, cluster_id, scored_device_days, candidate_rate,"
                " actionable_rate, mean_cluster_distance, train_cluster_distance_p99,"
                " train_cluster_share, silhouette, quality_source"
                " FROM v_ps4_v3_cluster_profile WHERE city_id=:c AND cluster_id=:k"
                + (" AND device_type=:t" if dt else "") + " LIMIT 3",
                **({"c": city, "k": cid, "t": dt} if dt else {"c": city, "k": cid}))
        ps4v3["persistent"] = _q(
            "SELECT actionable_weeks, first_week, last_week, worst_distance_ratio,"
            " worst_score, severities FROM v_ps4_weekly_persistent"
            " WHERE city_id=:c AND device_id=:d LIMIT 1", c=city, d=dev)
    ps4v3["found"] = bool(ps4v3.get("weeks"))
    ps4v3["basis"] = ("PS4 is unsupervised: it reports that this device behaved unlike its "
                      "peers. That is not a probability of failure.")
    out["ps4_v3_360"] = ps4v3

    # ---- Cross-PS corroboration (added 2026-07-27) ----
    # The modal previously stacked five independent panels and left the reader to
    # spot the connection. This computes it: PS2 knows which SUBSYSTEM the
    # cascades on this device start at, PS3 knows which subsystem it attributes
    # the failures to. When they name the same one that is two independent
    # methods agreeing, and it is the strongest evidence the platform can offer
    # about where to send an engineer. When they disagree it is worth saying so
    # rather than presenting both as equally confident.
    # 27-Jul-2026. The three synthesis steps are guarded individually.
    #
    # They run LAST and read everything above them, so any one of them raising
    # discarded all five problem statements plus causation and returned a bare
    # {"error": ...} -- which is precisely what made every panel in the Analyse
    # modal render blank. A derived summary failing must never cost the reader
    # the evidence it was derived from.
    for _key, _fn in (("cross_ps", lambda: _cross_ps(out)),
                      ("recommendation", lambda: _reco(out, prob, thr)),
                      ("servicenow_payload", lambda: _sn_payload(dev, cat, out))):
        try:
            out[_key] = _fn()
        except Exception as _e:
            print("device_360 %s failed: %s", _key, str(_e)[:200])
            out[_key] = None
            out.setdefault("degraded", []).append(_key)
    return out


# Normalises the two subsystem vocabularies enough to compare them. PS2 names a
# subsystem from the event-code chain; PS3 attributes one from the ServiceNow
# root-cause label. They overlap but are not written identically, so compare on a
# folded key rather than on raw equality -- and when a name is not in the map,
# say "not comparable" instead of guessing a match.
_SUBSYS_FOLD = {
    "BHU": "BILL", "BILLACCEPTOR": "BILL", "BILL_HANDLING": "BILL",
    "CHU": "COIN", "COINACCEPTOR": "COIN", "COIN_HANDLING": "COIN",
    "CSC_READER": "READER", "READER": "READER", "BUS_READER": "READER",
    "COMMS": "COMMS", "COMMUNICATIONS": "COMMS", "NETWORK": "COMMS",
    "GATE_MECH": "GATE_MECH", "MECH": "GATE_MECH",
    "PRINTER": "PRINTER", "SCRST": "SCREEN", "SYSTEM": "SYSTEM",
}
def _fold(v):
    if not v: return None
    return _SUBSYS_FOLD.get(str(v).strip().upper().replace(" ", "_"))

def _cross_ps(o):
    ps1, ps2, ps3, ps4 = (o.get(k) or {} for k in ("ps1", "ps2", "ps3", "ps4"))
    # PS2's entry subsystem is more actionable than its most-frequent one, but
    # fall back to the frequent one when there are no recent chains.
    p2raw = ps2.get("cascade_entry_subsystem") or ps2.get("dom_subsystem")
    p3raw = ps3.get("dominant_pred_component") if ps3.get("rootcause_shippable") else None
    a, b = _fold(p2raw), _fold(p3raw)
    if a and b:
        agree = a == b
        verdict = "agree" if agree else "disagree"
        detail = (f"PS2 cascades start at {p2raw} and PS3 attributes failures to {p3raw} â€” "
                  + ("the same subsystem, reached by two independent methods."
                     if agree else
                     "two different subsystems. Neither is confirmed; inspect both."))
    elif p2raw or p3raw:
        verdict = "single_source"
        detail = (f"Only {'PS2' if p2raw else 'PS3'} names a subsystem "
                  f"({p2raw or p3raw}). No second method to corroborate it.")
    else:
        verdict = "no_signal"
        detail = "Neither PS2 nor PS3 names a subsystem for this device."
    # A device that is cascade-active AND anomalous AND above the PS1 threshold
    # is being flagged by three unrelated models. Count that rather than leaving
    # it implicit across three panels.
    signals = []
    if ps1.get("found") and ps1.get("predicted_label"): signals.append("PS1 above threshold")
    if ps2.get("in_top_devices"): signals.append(f"PS2 cascade rank #{ps2.get('cascade_rank')}")
    elif ps2.get("in_catalog"): signals.append("PS2 cascade history")
    if ps3.get("found") and ps3.get("n_incidents"): signals.append(f"PS3 {ps3['n_incidents']} OOS incident(s)")
    if ps4.get("alert_count"): signals.append(f"PS4 {ps4['alert_count']} anomaly alert(s)")
    return {"subsystem_verdict": verdict, "subsystem_detail": detail,
            "ps2_subsystem": p2raw, "ps3_subsystem": p3raw,
            "signal_count": len(signals), "signals": signals,
            "propagation_speed": ps2.get("propagation_speed"),
            "pct_cascades_under_15min": ps2.get("pct_cascades_under_15min")}

def _reco(o, prob, thr):
    ps1 = o.get("ps1", {}); ps2 = o.get("ps2", {}); ps4 = o.get("ps4", {})
    if not ps1.get("found"):
        base = "No PS1 prediction in the latest run for this device."
    elif prob is not None and thr is not None and prob >= thr:
        _qg = (ps1.get("quality_gate") or "").upper()
        _tail = ("" if _qg == "PASS"
                 else " REVIEW-ONLY: this model has not passed its quality gate â€” treat as a watch signal, not an auto-dispatch.")
        base = ("PS1 flags elevated 3-day failure risk (%.1f%% vs threshold %.1f%%).%s"
                % (prob*100, thr*100, _tail))
    else:
        base = "PS1 below threshold (%.1f%% vs %.1f%%). No PS1 action." % ((prob or 0)*100, (thr or 0)*100)
    corr = []
    if ps2.get("in_top_devices"): corr.append("PS2 cascade-active (rank %s, %s days)" % (ps2.get("cascade_rank"), ps2.get("cascade_days")))
    elif ps2.get("in_catalog") and ps2.get("cascade_days"): corr.append("PS2 cascade history (%s days)" % ps2.get("cascade_days"))
    if ps4.get("alert_count"): corr.append("PS4 anomaly alerts x%s" % ps4.get("alert_count"))
    dom = ps2.get("dom_error_code")
    if ps1.get("found") and prob is not None and thr is not None and prob >= thr and (ps2.get("in_top_devices") or ps4.get("alert_count")):
        return base + " Corroborated by %s. Suggest inspection of %s%s and scheduling preventive maintenance." % (
            ", ".join(corr), ps2.get("dom_subsystem") or "dominant subsystem", (" (error code %s)" % dom) if dom else "")
    x = o.get("cross_ps") or {}
    if x.get("subsystem_verdict") == "agree":
        corr.append("PS2 and PS3 both point at %s" % x.get("ps2_subsystem"))
    if x.get("propagation_speed") == "fast":
        corr.append("cascades propagate fast (%s under 15 min)"
                    % (f"{x['pct_cascades_under_15min']*100:.0f}%"
                       if x.get("pct_cascades_under_15min") is not None else "most"))
    if corr:
        return base + " Cross-PS context: " + ", ".join(corr) + "."
    return base

def _sn_payload(dev, cat, o):
    ps1 = o.get("ps1", {}); ps2 = o.get("ps2", {})
    prob = ps1.get("failure_probability"); band = ps1.get("risk_band", "")
    urg = {"Critical": "1", "High": "2", "Medium": "3", "Low": "3"}.get(band, "3")
    return {
        "short_description": "Scheduled maintenance - %s (%s) PS1 3-day failure risk %s%%" % (dev, cat or "device", round((prob or 0)*100, 1)),
        "cmdb_ci": dev, "u_device_category": cat, "u_facility": ps2.get("facility"), "u_serial": ps2.get("serial"),
        "urgency": urg, "impact": urg, "category": "Hardware", "subcategory": "Predictive Maintenance",
        "u_predicted_probability": prob, "u_decision_threshold": ps1.get("decision_threshold"),
        "u_dominant_error_code": ps2.get("dom_error_code"), "u_dominant_subsystem": ps2.get("dom_subsystem"),
        "work_notes": "MARS PS1 predictive signal (quality gate %s). Risk band %s. Cross-PS: PS2 cascade_days=%s in_top=%s, PS4 alerts=%s." % (
            ps1.get("quality_gate") or "unknown",
            band, ps2.get("cascade_days"), ps2.get("in_top_devices"), o.get("ps4", {}).get("alert_count")),
        "u_source": "MARS-predictive", "u_state": "staged", "caller_id": "mars.integration"}


# ============ PS2 v2.5 label-aligned generation (sql/44) ====================
# ONE route family over the 20 tables sql/44 creates, plus /ps2/status.
#
# WHY A FAMILY AND NOT 20 ROUTES
# The tables share one shape: city-scoped, one snapshot per computed_date,
# replaced wholesale by the daily loader. Twenty near-identical route blocks is
# twenty chances to forget the city filter or the latest-date subquery. The
# metric name is validated against this dict, so an unknown one 404s rather
# than reaching SQL.
#
# EVERY QUERY IS SCOPED TO THAT TABLE'S OWN LATEST computed_date.
# The loader commits all 20 in one transaction so they normally agree. Scoping
# each independently means a partially refreshed database degrades to "one
# panel is stale" instead of "one panel is empty".
#
# id IS NEVER SELECTED. It is BIGSERIAL and the daily DELETE+INSERT
# regenerates it, so it is not a stable reference for a link, a saved view or
# a ServiceNow payload. The business key is.
#
# "precision" IS QUOTED. It is a column name in two of these tables and a
# PostgreSQL keyword. This project already lost a load to an unquoted
# "window" after eight tables were staged.
#
# CAPS. API Gateway kills the integration at 30s. cofailure_clusters holds
# 83,184 rows and repair_effectiveness 38,395, so both default to a browse
# window and are never a denominator -- the rollup routes are.
#
# (table, select list, order by, default limit, max limit, date column or None)
_PS2V25 = {
    # -- label and governance (ps2_v25_*) --------------------------------
    "label-daily": ("ps2_v25_failure_label_daily",
        'label_date,device_category,eligible_device_days,positive_device_days,eligible_devices,'
        'positive_devices,future_hardware_oos_set_events,median_hours_to_next_oos,'
        'p90_hours_to_next_oos,negative_device_days,label_positive_rate',
        "label_date, device_category", 2000, 5000, "label_date"),
    "label-summary": ("ps2_v25_failure_label_summary",
        'device_category,eligible_device_days,positive_device_days,eligible_devices,positive_devices,'
        'future_hardware_oos_set_events,mean_hours_to_next_oos,median_hours_to_next_oos,'
        'p90_hours_to_next_oos,positive_days_with_commanded_oos,negative_device_days,'
        'label_positive_rate,positive_device_share,label_horizon_days,label_cutoff_date,label_definition',
        "device_category", 100, 100, None),
    "label-horizon": ("ps2_v25_failure_horizon_profile",
        'device_category,lead_day,positive_device_days_at_lead,eligible_device_days,'
        'positive_rate_at_lead,label_definition',
        "device_category, lead_day", 100, 100, None),
    "label-parity": ("ps2_v25_ps1_label_parity",
        'device_category,eligible_device_days,comparable_device_days,matching_device_days,'
        'mismatching_device_days,source_label_positive_rate,rebuilt_label_positive_rate,'
        'legacy_sla_positive_rate,parity_rate,parity_status,rebuilt_target',
        "device_category", 100, 100, None),
    "definition-alignment": ("ps2_v25_failure_definition_alignment",
        'device_category,silver_ps1_failure_device_days,governed_oos_episode_device_days,'
        'overlap_device_days,silver_only_device_days,governed_only_device_days,'
        'silver_to_governed_overlap_rate,governed_to_silver_overlap_rate,definition_jaccard',
        "device_category", 100, 100, None),
    "model-performance": ("ps2_v25_ps1_model_performance",
        'device_category,evaluated_device_days,eligible_device_days,prediction_coverage,'
        'true_positive,false_positive,true_negative,false_negative,actual_positive_rate,'
        'predicted_positive_rate,"precision",recall,specificity,f1_score,balanced_accuracy,'
        'brier_score,roc_auc,pr_auc,evaluation_status',
        "device_category", 100, 100, None),
    "category-profile": ("ps2_v25_category_profile",
        'device_category_raw,device_category,event_count,device_count,first_event_ts,'
        'last_event_ts,is_mapped,is_target_scope',
        "event_count DESC", 200, 500, None),
    "run-quality": ("ps2_v25_run_quality",
        'check_name,passed,observed_value,threshold,severity,metric_context,'
        'quality_status,run_mode,run_disposition',
        "severity, passed, check_name", 200, 500, None),

    # -- governed OOS, patterns, components (ps2_v2_*) -------------------
    "oos-trend": ("ps2_v2_daily_oos_trend",
        'event_date,device_category,hardware_oos_onsets,affected_devices,hardware_oos_minutes,'
        'validated_failure_onsets,chargeable_oos_onsets',
        "event_date, device_category", 2000, 5000, "event_date"),
    "exposure": ("ps2_v2_customer_exposure",
        'event_date,device_category,hardware_oos_onsets,hardware_oos_minutes,'
        'transactions_exposed,revenue_cents_exposed',
        "event_date, device_category", 2000, 5000, "event_date"),
    "governance": ("ps2_v2_oos_governance",
        'device_category,oos_evidence_class,failure_evidence_class,event_count,device_count,outage_minutes',
        "device_category, event_count DESC", 200, 500, None),
    "precursors": ("ps2_v2_precursor_patterns",
        'device_category,component_subsystem,next_subsystem,pattern_key,edge_support,'
        'pre_oos_edge_count,validated_failure_edge_count,median_edge_lag_seconds,p95_edge_lag_seconds,'
        'baseline_pre_oos_rate,pre_oos_rate,pre_oos_wilson_lower_95,pre_oos_lift_vs_category,'
        'validated_failure_rate,evidence_tier,priority_score',
        "priority_score DESC NULLS LAST", 500, 2000, None),
    "leadlag": ("ps2_v2_leadlag_timing",
        'device_category,component_subsystem,next_subsystem,pattern_key,edge_support,'
        'median_edge_lag_seconds,p95_edge_lag_seconds,pre_oos_rate,pre_oos_lift_vs_category,evidence_tier',
        "edge_support DESC", 500, 2000, None),
    "topology": ("ps2_v2_topology_nodes",
        'device_category,subsystem,outgoing_edge_volume,out_degree,outgoing_pre_oos_rate,'
        'incoming_edge_volume,in_degree,flow_centrality_score',
        "flow_centrality_score DESC NULLS LAST", 200, 500, None),
    "drift": ("ps2_v2_pattern_drift",
        'device_category,component_subsystem,next_subsystem,baseline_count,recent_count,'
        'baseline_pre_oos_count,recent_pre_oos_count,baseline_pre_oos_rate,recent_pre_oos_rate,'
        'rate_change,drift_flag',
        "rate_change DESC NULLS LAST", 500, 2000, None),
    "serials": ("ps2_v2_component_serial_patterns",
        'device_category,component_subsystem,component_serial_id,hardware_oos_episode_count,'
        'validated_failure_count,hardware_oos_minutes,observed_oos_days,last_oos_ts,'
        'serial_evidence_tier,component_priority_score,current_component_age_days,'
        'hardware_component_description,hardware_source,current_config_device_count,'
        'hardware_age_enrichment',
        "component_priority_score DESC NULLS LAST", 1000, 5000, None),
    "deterioration": ("ps2_v2_device_deterioration",
        'device_id,device_category,event_date,hardware_oos_onsets,hardware_oos_minutes,'
        'validated_failure_onsets,baseline_mean_28d,baseline_std_28d,oos_zscore_28d,alert_reason',
        "oos_zscore_28d DESC NULLS LAST", 1000, 5000, "event_date"),
    "clusters": ("ps2_v2_cofailure_clusters",
        'event_date,cluster_scope,cluster_id,device_category,facility_id,cofailing_devices,'
        'hardware_oos_onsets,observed_group_devices,cofailure_share,coordinated_station_flag,'
        'major_station_flag',
        "event_date DESC, cofailing_devices DESC", 1000, 5000, "event_date"),
    "repairs": ("ps2_v2_repair_effectiveness",
        'repair_id,maintenance_component_subsystem,ledger_type,maintenance_date,'
        'pre_30d_oos_onsets,post_30d_oos_onsets,post_vs_pre_change,interpretation_note',
        "maintenance_date DESC", 1000, 5000, "maintenance_date"),
    "cross-ps": ("ps2_v2_cross_ps_alignment",
        'source,truth_positive_count,signal_positive_count,matched_positive_count,'
        '"precision",recall,population_unit,alignment_status',
        "source", 100, 100, None),
}

# Which optional filters each table can honour. Asked of the SELECT list rather
# than hardcoded per table, so a column that is not there can never be filtered
# on -- that would be a 42703 at request time instead of an ignored parameter.
_PS2V25_FILTERS = (
    ("category", "device_category"),
    ("device",   "device_id"),
    ("serial",   "component_serial_id"),
    ("facility", "facility_id"),
    ("subsystem", "component_subsystem"),
)


def _ps2_v25_route(path, params, city):
    """Returns a response for /ps2/v25/* and /ps2/status, else None."""
    params = params or {}

    if path == "/ps2/status":
        # One row per table. A coherent load shows ONE distinct run_id across
        # all 20. More than one means a partial load -- the failure that
        # otherwise shows up as a tab where 18 panels are today and 2 are last
        # week. The view does the UNION so this route stays a one-liner.
        data = rows(
            "SELECT table_name, run_id, computed_date, notebook_version, as_of_ts, row_count "
            "FROM v_ps2_v25_status WHERE city_id=:c ORDER BY table_name", c=city)
        run_ids = sorted({r["run_id"] for r in data if r.get("run_id")})
        return ok({
            "city": city,
            "tables": len(data),
            "expected_tables": len(_PS2V25),
            "run_ids": run_ids,
            "coherent": len(run_ids) == 1 and len(data) == len(_PS2V25),
            "computed_date": (data[0]["computed_date"] if data else None),
            "as_of_ts": (data[0]["as_of_ts"] if data else None),
            "rows": data,
        })

    if not path.startswith("/ps2/v25/"):
        return None

    metric = path[len("/ps2/v25/"):].strip("/")
    if metric == "":
        return ok({"metrics": sorted(_PS2V25), "usage": "/ps2/v25/<metric>?city=CHI"})
    spec = _PS2V25.get(metric)
    if spec is None:
        return err(404, "unknown PS2 v2.5 metric %r. Known: %s" % (metric, ", ".join(sorted(_PS2V25))))

    table, cols, order, dflt, hard, datecol = spec
    where = ["city_id=:c",
             "computed_date=(SELECT MAX(computed_date) FROM %s WHERE city_id=:c)" % table]
    kw = {"c": city}

    have = cols.replace('"', '')
    for pname, col in _PS2V25_FILTERS:
        v = (params.get(pname) or "").strip()
        if v and col in have.split(","):
            where.append("%s=:%s" % (col, pname))
            kw[pname] = v.upper() if col == "device_category" else v

    if datecol:
        for pname, op in (("from", ">="), ("to", "<=")):
            v = (params.get(pname) or "").strip()
            if v:
                where.append("%s %s :%s" % (datecol, op, pname))
                kw[pname] = v

    limit = _clamp_int(params.get("limit"), dflt, 1, hard)
    offset = _clamp_int(params.get("offset"), 0, 0, 1000000)
    sql = ("SELECT %s FROM %s WHERE %s ORDER BY %s LIMIT %d OFFSET %d"
           % (cols, table, " AND ".join(where), order, limit, offset))
    return ok(rows(sql, **kw))


def route(method, path, params, body):
    city = q((params or {}).get("city", CITY))
    _v25 = _ps2_v25_route(path, params, city)
    if _v25 is not None: return _v25
    if path == "/ps2/windows":
        return ok(rows("SELECT window_bucket,cascade_days,pct,total_cascade_days,slow_fast_fault_mult,slow_fast_duration_mult FROM ps2_cascade_window_summary WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_cascade_window_summary WHERE city_id=:c) ORDER BY cascade_days DESC", c=city))
    if path == "/ps2/windowdetail":
        return ok(rows("SELECT window_bucket,cascade_days,chain_len_mean,chain_len_median,chain_len_max,span_min_mean,span_min_median,velocity_min_per_fault FROM ps2_window_detail WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_window_detail WHERE city_id=:c) ORDER BY span_min_mean", c=city))
    if path == "/ps2/topdevices":
        # 27-Jul-2026. Repointed from ps2_top_devices to v_ps2_device_cascade.
        #
        # ps2_top_devices is NOT part of the PS2 S3 export. It was never loaded
        # by the pipeline and held exactly one row -- BMV01005, a seed left over
        # from an early backfill -- so this route has been reporting a fleet of
        # one while 4,673 real devices sat in ps2_business_impact.
        #
        # dev_rank is kept as an alias of impact_rank so a caller ordering by it
        # still works. The w0_5..w60plus time-window columns are GONE and are not
        # faked: the export carries no per-device window split (the only velocity
        # breakdown, ps2_cascade_velocity, is fleet-level). Returning zeros there
        # would read as "this device never cascaded in 0-5 min", which is a claim
        # the data does not support.
        #
        # limit defaults to 20 to match the route's original intent; ?limit=0
        # returns all 4,673.
        try:
            lim = int((params or {}).get("limit", 20))
        except (TypeError, ValueError):
            lim = 20
        sql = ("SELECT device_id,category,total_impact,avg_impact,"
               "impact_cascade_days AS cascade_days,recurrence_cascade_days,"
               "chronic,impact_rank,impact_rank_in_category,"
               "impact_rank AS dev_rank "
               "FROM v_ps2_device_cascade WHERE city_id=:c ORDER BY impact_rank")
        if lim > 0:
            sql += f" LIMIT {int(lim)}"
        return ok(rows(sql, c=city))
    # ---- PS2 SERIAL-GRAIN  (added 01-Aug-2026) ----------------------------
    #
    # ONE route, seventeen metrics: GET /ps2/serial/{metric}?city=CHI
    #
    # The front end for this has existed since 26-Jul and was already correct.
    # PS2SerialGrainAnalytics.jsx, PS2RichAnalytics.jsx and useSerialDeviceMap
    # all call apiPS2SerialMetric(), which fetches /ps2/serial/{metric} --
    # a path this handler had no branch for. api.js swallows a non-2xx and
    # returns [], so all thirteen panels rendered their "no data yet" message
    # instead of an error. Nothing was broken; the server end was never built.
    # The 01-Aug PS2 load put ~158,000 serial-grain rows into Aurora. This is
    # the only thing standing between those rows and the screen.
    #
    # Column names below are NOT chosen -- each one is what the JSX reads off
    # the row object. Verified panel by panel before writing this:
    #   suppression family/min_support_floor/cells_reported/cells_suppressed
    #   chronic     serial_id/device_category/reference_period_days/
    #               reference_period_source/n_cascades/recurrence_rate_per_day/
    #               peer_pct_rank/chronicity_flag
    #   recurrence  serial_id/cascade_days/chronic
    #   leadlag     serial_id/sub_a/sub_b/mean/median/p25/p75/n
    #   assoc       serial_id/antecedents/consequents/support/confidence/
    #               lift/conviction
    #   impact      entity_id (NOT serial_id)/cascade_days/total_impact/avg_impact
    #   velocity    age_bucket/n/mean_velocity_min_per_fault
    #   crossps     entity_grain/n_ps2_ignition_entities/n_matched_in_cross_ps/
    #               match_rate/ps1_*/ps4_*/note
    #   sankey      subsystem_from/subsystem_to/cascade_count/
    #               total_business_impact/avg_severity
    #   network     subsystem (the table column is node_id -- aliased)/scope/
    #               betweenness/pagerank/in_degree/out_degree
    #   ignition    subsystem/ignition_count/termination_count
    #   facility    facility_id/cascade_days/distinct_devices/contagion_rate
    #   phi         serial_id/sub_a/sub_b/phi
    #   condprob    serial_id/sub_a/sub_b/window/window_bucket/n_a/n_ab/
    #               p_b_given_a   ("window" is a RESERVED KEYWORD -- quoted,
    #               and emitted twice because the JSX reads both spellings)
    #   hmm         serial_id/n_obs/pct_time_critical/mean_chain_length/
    #               converged/n_iter_run
    #   markov      serial_id/n_chains/self_transition_rate  (roster)
    #   cmdb        serial_id/device_id  (drives every Analyse button on this
    #               tab -- an empty map disables all of them)
    #
    # Every metric returns the NEWEST computed_date for the city only, the
    # same rule the device-grain PS2 routes use.
    if path.startswith("/ps2/serial/"):
        metric = path[len("/ps2/serial/"):].strip("/").lower()
        serial = ((params or {}).get("serial_id") or "").strip()
        # ps2_conditional_prob_serial is 89,990 rows and ps2_phi_matrix_serial
        # is 30,430. Both are always requested WITH a serial_id by the JSX;
        # the cap is the guard for when they are not.
        lim = _clamp_int((params or {}).get("limit"), 500, 1, 5000)

        def _latest(tbl, cols, order="", serial_col=None):
            w = ""
            if serial_col and serial:
                w = f" AND {serial_col}=:s"
            return (f"SELECT {cols} FROM {tbl} WHERE city_id=:c"
                    f" AND computed_date=(SELECT MAX(computed_date) FROM {tbl}"
                    f" WHERE city_id=:c){w} {order} LIMIT {lim}")

        M = {
            "suppression": _latest(
                "ps2_suppression_summary_serial",
                'family, grain, min_support_floor, cells_suppressed, '
                'cells_reported, run_id, computed_date',
                "ORDER BY family"),
            "chronic": _latest(
                "ps2_chronic_recurrence_serial",
                'serial_id, device_category, reference_period_days, '
                'reference_period_source, n_cascades, recurrence_rate_per_day, '
                'peer_pct_rank, chronicity_flag',
                "ORDER BY recurrence_rate_per_day DESC NULLS LAST",
                "serial_id"),
            "recurrence": _latest(
                "ps2_recurrence_serial",
                'serial_id, cascade_days, chronic',
                "ORDER BY cascade_days DESC NULLS LAST", "serial_id"),
            "leadlag": _latest(
                "ps2_leadlag_timing_serial",
                'serial_id, sub_a, sub_b, "mean", median, p25, p75, n',
                "ORDER BY serial_id, sub_a, sub_b", "serial_id"),
            "assoc": _latest(
                "ps2_association_rules_serial",
                'serial_id, antecedents, consequents, support, confidence, '
                'lift, conviction',
                "ORDER BY lift DESC NULLS LAST", "serial_id"),
            "impact": _latest(
                "ps2_business_impact_serial",
                'entity_id, cascade_days, total_impact, avg_impact',
                "ORDER BY total_impact DESC NULLS LAST"),
            "velocity": _latest(
                "ps2_cascade_velocity_by_age_serial",
                'age_bucket, n, mean_velocity_min_per_fault',
                "ORDER BY age_bucket"),
            "crossps": _latest(
                "ps2_cross_ps_attribution_serial",
                'entity_grain, n_ps2_ignition_entities, n_matched_in_cross_ps, '
                'match_rate, ps1_high_risk_co_occur_n, '
                'ps1_high_risk_co_occur_rate, ps4_anomaly_co_occur_n, '
                'ps4_anomaly_co_occur_rate, note',
                "ORDER BY entity_grain"),
            "sankey": _latest(
                "ps2_cascade_sankey_subsystem",
                'subsystem_from, subsystem_to, cascade_count, '
                'total_business_impact, avg_severity',
                "ORDER BY cascade_count DESC NULLS LAST"),
            "network": _latest(
                "ps2_network_centrality",
                'node_id AS subsystem, scope, betweenness, pagerank, '
                'in_degree, out_degree',
                "ORDER BY pagerank DESC NULLS LAST"),
            "ignition": _latest(
                "ps2_ignition_termination_subsystem",
                'subsystem, ignition_count, termination_count',
                "ORDER BY ignition_count DESC NULLS LAST"),
            "facility": _latest(
                "ps2_facility_contagion_facility",
                'facility_id, cascade_days, distinct_devices, contagion_rate',
                "ORDER BY cascade_days DESC NULLS LAST"),
            "phi": _latest(
                "ps2_phi_matrix_serial",
                'serial_id, sub_a, sub_b, phi',
                "ORDER BY phi DESC NULLS LAST", "serial_id"),
            "condprob": _latest(
                "ps2_conditional_prob_serial",
                'serial_id, sub_a, sub_b, "window", "window" AS window_bucket, '
                'n_a, n_ab, p_b_given_a',
                "ORDER BY p_b_given_a DESC NULLS LAST", "serial_id"),
            "hmm": _latest(
                "ps2_hmm_regimes_serial",
                'serial_id, n_obs, pct_time_critical, mean_chain_length, '
                'converged, n_iter_run',
                "ORDER BY pct_time_critical DESC NULLS LAST", "serial_id"),
            "markov": _latest(
                "ps2_markov_self_transition_serial",
                'serial_id, n_chains, self_transition_rate',
                "ORDER BY n_chains DESC NULLS LAST", "serial_id"),
            # The serial -> device map. ps2_device_catalog is the only place
            # the two identifiers sit on one row. Without this every Analyse
            # button on the serial tab stays disabled.
            "cmdb": ("SELECT DISTINCT serial AS serial_id, device_id"
                     " FROM ps2_device_catalog WHERE city_id=:c"
                     " AND serial IS NOT NULL AND device_id IS NOT NULL"
                     " AND computed_date=(SELECT MAX(computed_date)"
                     " FROM ps2_device_catalog WHERE city_id=:c) LIMIT 20000"),
        }
        if metric not in M:
            return err(404, "unknown ps2 serial metric '%s' -- known: %s"
                       % (metric, ", ".join(sorted(M))))
        # One metric failing must not take the tab down. _safe_rows returns []
        # and logs rather than raising, so a table that has not been loaded
        # yet degrades to that panel's own "no data yet" message.
        if serial and metric in ("chronic", "recurrence", "leadlag", "assoc",
                                 "phi", "condprob", "hmm", "markov"):
            return ok(_safe_rows(M[metric], c=city, s=serial))
        return ok(_safe_rows(M[metric], c=city))

    if path == "/ps2/hub":
        return ok({"nodes": rows("SELECT node_id,freq,is_hub FROM ps2_subsystem_hub_summary WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_subsystem_hub_summary WHERE city_id=:c) ORDER BY freq DESC", c=city),
                   "edges": rows("SELECT source_sub,target_sub,phi FROM ps2_subsystem_hub_edges WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_subsystem_hub_edges WHERE city_id=:c)", c=city)})
    if path == "/ps2/facility":
        r = rows("SELECT * FROM ps2_facility_contagion_summary WHERE city_id=:c ORDER BY computed_date DESC LIMIT 1", c=city)
        return ok(r[0] if r else {})
    if path == "/ps2/associations":
        return ok(rows("SELECT antecedent_subsystem,consequent_subsystem,support,confidence,lift,conviction FROM ps2_subsystem_associations WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_subsystem_associations WHERE city_id=:c) ORDER BY lift DESC", c=city))
    if path == "/ps2/hmm":
        return ok(rows("SELECT regime,pct,dwell_days_min,dwell_days_max FROM ps2_hmm_regimes WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_hmm_regimes WHERE city_id=:c)", c=city))
    # ---- Phase-1e NEW PS2 analytics (return newest computed_date only) ----
    if path == "/ps2/paths":
        return ok(rows("SELECT path_rank,cascade_path,path_len,first_subsystem,last_subsystem,occurrences,pct_of_chains FROM ps2_cascade_paths WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_cascade_paths WHERE city_id=:c) ORDER BY path_rank", c=city))
    if path == "/ps2/ignition":
        return ok(rows("SELECT subsystem,rank,ignition_days,termination_days,ignition_pct,termination_pct,net_role FROM ps2_ignition_termination WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_ignition_termination WHERE city_id=:c) ORDER BY rank", c=city))
    if path == "/ps2/impact":
        return ok(rows("SELECT device_id,category,total_impact,cascade_days,avg_impact,max_impact,impact_rank FROM ps2_business_impact WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_business_impact WHERE city_id=:c) ORDER BY impact_rank", c=city))
    # ---- Phase-1f RICH PS2 (correlation/markov/network/error-codes/device drill-down) ----
    if path == "/ps2/devices":
        return ok(rows("SELECT device_id,device_name,serial,category,control_group,facility,operator,cascade_days,avg_chain_len,max_chain_len,dom_subsystem,dom_error_code,worst_cascade_path,worst_window FROM ps2_device_catalog WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_device_catalog WHERE city_id=:c) ORDER BY cascade_days DESC LIMIT 200", c=city))
    if path == "/ps2/devicecascades":
        dev = (params or {}).get("device", "")
        return ok(rows("SELECT device_id,transit_day,subsystem_chain,event_code_chain,severity_chain,chain_length,chain_span_min,first_subsystem,last_subsystem FROM ps2_device_cascades WHERE city_id=:c AND device_id=:d AND computed_date=(SELECT MAX(computed_date) FROM ps2_device_cascades WHERE city_id=:c) ORDER BY transit_day DESC LIMIT 100", c=city, d=dev))
    if path == "/ps2/errorcodes":
        return ok({"codes": rows("SELECT error_code,occurrences,top_subsystem,pct FROM ps2_error_codes WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_error_codes WHERE city_id=:c) ORDER BY occurrences DESC", c=city),
                   "transitions": rows("SELECT from_code,to_code,occurrences FROM ps2_error_code_transitions WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_error_code_transitions WHERE city_id=:c) ORDER BY occurrences DESC", c=city)})
    if path == "/ps2/phi":
        return ok(rows("SELECT sub_a,sub_b,phi FROM ps2_phi_matrix WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_phi_matrix WHERE city_id=:c)", c=city))
    if path == "/ps2/markov":
        return ok(rows("SELECT from_sub,to_sub,prob FROM ps2_markov_transitions WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_markov_transitions WHERE city_id=:c) ORDER BY prob DESC", c=city))
    if path == "/ps2/network":
        # 27-Jul-2026. The table is now keyed on scope as well, because the PS2
        # export carries each subsystem once per scope: ALL(10) + TVM(8) +
        # GATE(5) + VALIDATOR(4) = 27 rows. The previous query had no scope
        # predicate, so after the load it would have returned PRINTER four times
        # and every centrality bar chart would have shown duplicate nodes.
        #
        # Default is ALL -- the fleet-wide graph, which is what this route
        # returned before scope existed, so existing panels keep their meaning.
        # ?scope=TVM|GATE|VALIDATOR gives the per-device-type graph, and
        # ?scope=* returns every row WITH the scope column so a caller that
        # knows to facet can do so. `role` is dropped: it is NULL on every row
        # (it described a node's graph role and the export does not supply one),
        # and a column of nulls in a payload reads as missing data rather than
        # as a column that was never populated.
        sc = str((params or {}).get("scope") or "ALL").upper().strip()
        if sc == "*":
            return ok(rows(
                "SELECT scope,is_fleet,node_id,betweenness,pagerank,in_degree,"
                "out_degree,total_degree,pagerank_rank,betweenness_rank,"
                "n_nodes_in_scope FROM v_ps2_network_centrality "
                "WHERE city_id=:c AND computed_date="
                "(SELECT MAX(computed_date) FROM ps2_network_centrality WHERE city_id=:c) "
                "ORDER BY scope, betweenness DESC", c=city))
        return ok(rows(
            "SELECT scope,is_fleet,node_id,betweenness,pagerank,in_degree,"
            "out_degree,total_degree,pagerank_rank,betweenness_rank,"
            "n_nodes_in_scope FROM v_ps2_network_centrality "
            "WHERE city_id=:c AND scope=:s AND computed_date="
            "(SELECT MAX(computed_date) FROM ps2_network_centrality WHERE city_id=:c) "
            "ORDER BY betweenness DESC", c=city, s=sc))
    if path == "/ps2/conditional":
        return ok(rows("SELECT sub_a,sub_b,window_bucket,p_b_given_a FROM ps2_conditional_prob WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_conditional_prob WHERE city_id=:c) ORDER BY p_b_given_a DESC", c=city))
    # ---- PS1 failure prediction (run 20260713_0905; NOT promoted) ----
    if path == "/ps1/summary":
        return ok(rows("SELECT device,champion_model,test_auc,test_ap,test_accuracy,test_f1,test_precision,test_recall,op_threshold,op_fleet_pct,op_precision,op_recall,op_f2,recall_floor,quality_gate,promoted,brier_raw,brier_cal,auc_cal,prec_at_k,rec_at_k,lift_at_k,map_score,mlflow_version,sm_registered,endpoint_name,overfit_flag,n_train,n_test,n_test_pos,base_rate_pct,target,run_id FROM ps1_failure_summary WHERE city_id=:c "
                       # champion-first: promoted models, then strongest test AUC.
                       "ORDER BY promoted DESC, test_auc DESC NULLS LAST, device", c=city))
    if path == "/ps1/leaderboard":
        # 2026-07-26 -- was ORDER BY device,lb_rank, which put the *non*-champion
        # top-AUC row first and buried the deployed champion (Gates: CatBoost at
        # rank 1, LightGBM (Optuna) champion at rank 4). The dashboard reads this
        # top-down, so the deployed model now leads each device block and the
        # rest follow best-AUC-first.
        lb = rows("SELECT device,model,auc,ap,f1,prec,rec,lb_rank,is_champion,note "
                  "FROM ps1_leaderboard WHERE city_id=:c "
                  "ORDER BY device, is_champion DESC, auc DESC NULLS LAST, ap DESC NULLS LAST, lb_rank",
                  c=city)
        # PS1's promotion gate is a RECALL floor, not an F1 floor, and it lives on
        # ps1_failure_summary (per-device champion). Carry it onto every row of that
        # device's leaderboard so each model is judged against the bar it must clear,
        # and carry base_rate_pct too -- that is the number that makes a high
        # accuracy readable (Gates base rate 0.54% => 99.46% is the do-nothing score).
        try:
            floors = {str(x["device"]): x for x in rows(
                "SELECT device,recall_floor,base_rate_pct,quality_gate FROM ps1_failure_summary "
                "WHERE city_id=:c", c=city)}
        except Exception:
            floors = {}
        for r in lb:
            f = floors.get(str(r.get("device")), {})
            rf, rec = _num(f.get("recall_floor")), _num(r.get("rec"))
            v, why = model_verdict(f1=r.get("f1"), auc=r.get("auc"), recall=r.get("rec"))
            if v == "ok" and rf is not None and rec is not None and rec < rf:
                v, why = "below_floor", f"recall {rec:.4f} below the {rf:.2f} recall floor"
            r["verdict"], r["verdict_evidence"] = v, why
            r["recall_floor"] = rf
            r["base_rate_pct"] = _num(f.get("base_rate_pct"))
        if _truthy((params or {}).get("exclude_degenerate")):
            lb = [r for r in lb if r["verdict"] != "degenerate"]
        return ok(lb)
    if path == "/ps1/features":
        return ok(rows("SELECT device,feature,mean_abs_shap,pct_total,feat_rank FROM ps1_features WHERE city_id=:c ORDER BY device,feat_rank", c=city))
    # ---- Phase-1g PS1 SERVING (backs the rich teammates' PS1 tab) ----
    if path == "/ps1/predictions":
        # 2026-07-26 -- BUG FIX. This route was
        #     ORDER BY p.failure_probability DESC LIMIT 500
        # across the whole fleet. VALIDATOR probabilities sit at ~0.99997, so all
        # 500 returned rows were VALIDATOR and *neither TVM nor GATE appeared at
        # all* -- the dashboard's device filter had nothing to filter. Probed
        # 2026-07-26: 500/500 rows VALIDATOR.
        # Now ranked within each device_category so every modelled type gets its
        # own top-N slice. device_category= still narrows to one type.
        cat = (params or {}).get("device_category")
        try:
            per_cat = max(1, min(500, int((params or {}).get("limit") or 200)))
        except (TypeError, ValueError):
            per_cat = 200
        # 28-Jul-2026. Sourced from v_ps1_predictions_xw (sql/37) instead of
        # ps1_failure_predictions. MEASURED: the legacy table returns 200 rows,
        # all VALIDATOR -- it has no GATE or TVM rows at its latest
        # computed_date, so the per-category ranking below had nothing to rank.
        # The view is one row per device from ps1_cross_wired_daily and carries
        # all three types. No computed_date filter: the view is already
        # DISTINCT ON device, newest day first.
        where = "p.city_id = :c"
        kw = {"c": city, "k": per_cat}
        if cat:
            where += " AND p.device_category = :d"; kw["d"] = cat
        return ok(rows(f"""WITH ranked AS (
              SELECT p.*, ROW_NUMBER() OVER (
                       PARTITION BY p.device_category
                       ORDER BY p.failure_probability DESC NULLS LAST, p.device_id
                     ) AS cat_rank
              FROM v_ps1_predictions_xw p
              WHERE {where}
            )
            SELECT r.prediction_id, r.device_category, r.device_id, r.facility_id,
                   r.failure_probability, r.predicted_label, r.decision_threshold,
                   r.prediction_date, r.inference_ts, r.cat_rank,
                   r.ps1_risk_tier,
                   COALESCE(ds.station_name, c2.facility, r.facility_id) AS station_name,
                   COALESCE(ds.operator, c2.operator, r.operator_id) AS operator,
                   c2.dom_error_code, c2.dom_subsystem
            FROM ranked r
            LEFT JOIN dim_station ds ON ds.city_id = :c AND ds.facility_id = r.facility_id
            LEFT JOIN ps2_device_catalog c2
              ON c2.city_id = :c AND c2.device_id = r.device_id
             AND c2.computed_date = (SELECT MAX(computed_date) FROM ps2_device_catalog WHERE city_id = :c)
            WHERE r.cat_rank <= :k
            ORDER BY r.device_category, r.failure_probability DESC NULLS LAST""", **kw))
    # ---- coverage map (added 2026-07-26) ----
    # Which device categories actually have rows in each PS1 table. Probed
    # 2026-07-26: VALIDATOR has 494,932 prediction rows but NO row in
    # ps1_failure_summary, ps1_leaderboard, ps1_model_performance or
    # ps1_feature_importance -- so the dashboard could show validator
    # predictions with no model behind them. This route makes that gap explicit
    # instead of leaving a silently empty panel.
    if path == "/ps1/coverage":
        out = []
        for tbl, col in (("ps1_failure_predictions", "device_category"),
                         ("ps1_failure_summary", "device"),
                         ("ps1_leaderboard", "device"),
                         ("ps1_model_performance", "device_category"),
                         ("ps1_feature_importance", "device_category"),
                         ("ps1_serial_predictions", "device_category"),
                         ("ps1_risk_bands", "device_category"),
                         ("ps1_threshold_sweep", "device_category")):
            try:
                r = rows(f"SELECT {col} AS category, COUNT(*) AS n FROM {tbl} "
                         f"WHERE city_id=:c GROUP BY {col} ORDER BY {col}", c=city)
                out.append({"table": tbl, "categories": r})
            except Exception as e:
                out.append({"table": tbl, "error": str(e)[:160]})
        return ok(out)
    if path == "/ps1/model-performance":
        mp = rows("SELECT device_category,model_name,algorithm,decision_threshold,mlflow_version,endpoint_name,n_features,quality_gate,promoted,computed_date,test_auc,test_ap,test_f1,test_prec,test_rec FROM ps1_model_performance WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps1_model_performance WHERE city_id=:c)", c=city)
        # 2026-07-26 -- accuracy is now COMPUTED from the real confusion matrix
        # rather than hardcoded (the old {"TVM": 0.7033, "GATE": 0.9950} literal
        # was removed) or left NULL. ps1_confusion holds the run's actual
        # TP/FP/TN/FN, so accuracy = (TP+TN)/N is derived, not asserted.
        #
        # The baseline shipped alongside it is the MAJORITY-CLASS accuracy,
        # max(prevalence, 1 - prevalence) -- NOT the positive prevalence.
        #
        # 2026-07-26 CORRECTION. This first used positive prevalence, which is
        # only the right baseline when positives are the majority. On VALIDATOR
        # they are not: 38.91% positive, so the accuracy you get for free is
        # "always predict negative" = 61.09%. The old formula reported a lift of
        # +59.8 points against 38.91 when the true gain is +37.6 -- and the
        # notebook agrees with the corrected figure (gain=+0.3758). Overstating a
        # model's edge is the same failure as the 99.11% severity head, just
        # pointing the other way.
        #
        # Both numbers are returned: positive_prevalence_pct describes the data,
        # majority_accuracy_pct is what accuracy must beat.
        conf = {r["device_category"]: r for r in rows(
            "SELECT device_category,tp,fp,tn,fn FROM ps1_confusion WHERE city_id=:c "
            "AND computed_date=(SELECT MAX(computed_date) FROM ps1_confusion WHERE city_id=:c)",
            c=city)}
        out = []
        for r in mp:
            _acc = _base = _n = _prev = None
            cm = conf.get(r["device_category"])
            if cm and all(cm.get(k) is not None for k in ("tp", "fp", "tn", "fn")):
                tp, fp, tn, fn = (int(cm["tp"]), int(cm["fp"]), int(cm["tn"]), int(cm["fn"]))
                _n = tp + fp + tn + fn
                if _n:
                    _acc  = round((tp + tn) / _n, 4)
                    _prev = round((tp + fn) * 100.0 / _n, 3)          # positives in the test set
                    _base = round(max(tp + fn, tn + fp) * 100.0 / _n, 3)  # majority-class accuracy
            out.append({"device_category": r["device_category"], "category": r["device_category"], "city": "Chicago",
                        "model_name": r["model_name"], "algorithm": r["algorithm"], "prediction_head": r["device_category"],
                        "test_auc": r["test_auc"], "test_pr_auc": r["test_ap"], "accuracy": _acc,
                        "test_accuracy": _acc, "base_rate_pct": _base, "n_test": _n,
                        "majority_accuracy_pct": _base, "positive_prevalence_pct": _prev,
                        "accuracy_lift_over_base": (round(_acc * 100 - _base, 3)
                                                    if _acc is not None and _base is not None else None),
                        "decision_threshold": r["decision_threshold"], "mlflow_version": r["mlflow_version"],
                        "model_version": r["mlflow_version"], "model_registry_id": r["endpoint_name"], "registry_alias": "champion",
                        "endpoint_name": r["endpoint_name"], "n_features": r["n_features"],
                        "status": ("promoted" if r["promoted"] else "not promoted"), "deployed_at": str(r["computed_date"]),
                        "quality_gate": r["quality_gate"], "promoted": r["promoted"],
                        # 2026-07-26 -- train_*/val_* were dropped from this payload.
                        # They are in-sample fit statistics (GATE reported train AUC
                        # 1.0000 / val AUC 1.0000) and drawing them next to the
                        # held-out numbers on a client dashboard reads as model
                        # quality when it is memorisation. Held-out test metrics only.
                        # The overfit signal is still served, as the boolean
                        # ps1_failure_summary.overfit_flag on /ps1/summary.
                        "s3_metrics": {"test_auc": r["test_auc"], "test_ap": r["test_ap"], "test_f1": r["test_f1"],
                                       "test_prec": r["test_prec"], "test_rec": r["test_rec"]}})
        # champion-first ordering for the model cards / registry table
        out.sort(key=lambda m: (not m["promoted"], -(m["test_auc"] or 0)))
        return ok(out)
    # ---- PS1 SERIAL grain + run lineage (added 2026-07-26) ----
    # ps1_serial_predictions is keyed (city_id, run_id, device_id,
    # matched_serial_nbr) -- one row per component on a device, per run. The
    # dashboard previously had device grain only, so a serial-level view was
    # impossible even though the table was being written.
    if path == "/ps1/serial-predictions":
        cat = (params or {}).get("device_category")
        dev = (params or {}).get("device_id")
        where = ("s.city_id=:c AND s.run_id=(SELECT run_id FROM ps1_inference_runs "
                 "WHERE city_id=:c AND status='success' ORDER BY run_ts DESC LIMIT 1)")
        kw = {"c": city}
        if cat:
            where += " AND s.device_category=:d"; kw["d"] = cat
        if dev:
            where += " AND s.device_id=:dv"; kw["dv"] = dev
        return ok(rows(
            "SELECT s.device_id,s.matched_serial_nbr,s.device_category,s.component_type,"
            "s.component_age_days,s.device_failure_probability,s.attribution_weight,"
            "s.serial_risk_score,s.risk_band,s.prediction_date,s.run_id "
            f"FROM ps1_serial_predictions s WHERE {where} "
            "ORDER BY s.serial_risk_score DESC NULLS LAST LIMIT 500", **kw))
    if path == "/ps1/runs":
        return ok(rows(
            "SELECT run_id,run_ts,run_kind,device_category,scoring_date,endpoint_name,"
            "model_version,mlflow_version,target_col,decision_threshold,"
            "n_devices_scored,n_flagged,gold_snapshot_s3,status "
            "FROM ps1_inference_runs WHERE city_id=:c "
            "ORDER BY run_ts DESC LIMIT 50", c=city))
    if path == "/ps1/load-audit":
        return ok(rows(
            "SELECT ps_id,run_id,target_table,s3_source,rows_read,rows_loaded,"
            "columns_added,columns_skipped,status,error_text,loaded_at "
            "FROM ml_batch_load_audit WHERE city_id=:c "
            "ORDER BY loaded_at DESC LIMIT 50", c=city))
    # PS1 cross-tab over the device grain of the latest batch. Same whitelist
    # discipline as /ps3/crosstab -- dimensions are identifiers, never user text.
    if path == "/ps1/crosstab":
        DIMS = {"device_category": "p.device_category", "risk_band": "p.risk_band",
                "facility": "p.facility_id", "predicted_label": "p.predicted_label",
                "prediction_date": "p.prediction_date", "device": "p.device_id"}
        r_key = (params or {}).get("rows") or "device_category"
        c_key = (params or {}).get("cols") or "risk_band"
        if r_key not in DIMS or c_key not in DIMS:
            return err(400, f"rows/cols must be one of {sorted(DIMS)}")
        if r_key == c_key:
            return err(400, "rows and cols must differ")
        rexp, cexp = DIMS[r_key], DIMS[c_key]
        try:
            return ok(rows(
                f"SELECT {rexp} AS row_key, {cexp} AS col_key, COUNT(*) AS n, "
                " ROUND(AVG(p.failure_probability)::numeric,5) AS avg_prob "
                "FROM ps1_failure_predictions p WHERE p.city_id=:c "
                " AND p.computed_date=(SELECT MAX(computed_date) FROM ps1_failure_predictions WHERE city_id=:c) "
                f"GROUP BY {rexp}, {cexp} ORDER BY COUNT(*) DESC LIMIT 2000", c=city))
        except Exception as e:
            # risk_band was added by migration 16; tolerate its absence rather
            # than 500 the whole pivot panel.
            return err(400, f"crosstab unavailable: {str(e)[:160]}")
    if path == "/ps1/risk-trend":
        return ok(rows("SELECT trend_date AS date,device_category,avg_prob_pct,failures,total FROM ps1_risk_trend WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps1_risk_trend WHERE city_id=:c) ORDER BY trend_date", c=city))
    # ---- PS1 cross-wired reads (sql/34, sql/35, sql/36) ----------------
    # Source: ps1_cross_wired_daily, 786,525 device x component x day rows
    # loaded by cubic-mars-ps1-xw-loader from the three PS1 notebook exports.
    #
    # The Model Scorecard routes above are NOT touched. Scorecard reports
    # held-out registry metrics; these report what the model did to the fleet,
    # and what state the fleet is actually in.
    def _xw(sql, **kw):
        """Non-fatal read: a view that does not exist yet returns [] rather
        than 500-ing the whole sub-tab."""
        try:
            return rows(sql, **kw)
        except Exception as e:
            print("[ps1-xw]", type(e).__name__, str(e)[:200])
            return []
    if path == "/ps1/xw-summary":
        return ok(_xw("SELECT * FROM v_ps1_xw_summary WHERE city_id=:c "
                      "ORDER BY device_type", c=city))
    if path == "/ps1/xw-performance":
        return ok(_xw("SELECT * FROM v_ps1_xw_performance WHERE city_id=:c "
                      "ORDER BY device_type", c=city))
    if path == "/ps1/xw-tiers":
        return ok(_xw("SELECT * FROM v_ps1_xw_tier_calibration WHERE city_id=:c "
                      "ORDER BY device_type, positive_rate DESC", c=city))
    if path == "/ps1/xw-drivers":
        top = int((params or {}).get("top", 10))
        return ok(_xw("SELECT * FROM v_ps1_shap_importance WHERE city_id=:c "
                      "AND importance_rank <= :t "
                      "ORDER BY device_type, importance_rank", c=city, t=top))
    if path == "/ps1/xw-causation":
        return ok(_xw("SELECT * FROM v_ps1_xw_causation WHERE city_id=:c "
                      "ORDER BY critical_lift DESC", c=city))
    if path == "/ps1/xw-facility":
        top = int((params or {}).get("top", 20))
        return ok(_xw("SELECT * FROM v_ps1_xw_facility WHERE city_id=:c "
                      "ORDER BY n_critical DESC LIMIT :t", c=city, t=top))
    if path == "/ps1/xw-device-drivers":
        dev = (params or {}).get("device_id")
        if not dev:
            return err(400, "device_id required")
        return ok(_xw("SELECT * FROM v_ps1_device_drivers "
                      "WHERE city_id=:c AND device_id=:d", c=city, d=dev))
    # ---- sql/35: the corrected onset label -----------------------------
    if path == "/ps1/xw-base-rate":
        return ok(_xw("SELECT * FROM v_ps1_xw_base_rate WHERE city_id=:c "
                      "ORDER BY device_type", c=city))
    if path == "/ps1/xw-performance-onset":
        return ok(_xw("SELECT * FROM v_ps1_xw_performance_onset WHERE city_id=:c "
                      "ORDER BY device_type", c=city))
    if path == "/ps1/xw-chronic":
        top = int((params or {}).get("top", 20))
        return ok(_xw("SELECT * FROM v_ps1_xw_chronic_devices WHERE city_id=:c "
                      "ORDER BY total_days_out DESC LIMIT :t", c=city, t=top))
    # ---- sql/36: state framing -----------------------------------------
    if path == "/ps1/xw-flag-reason":
        return ok(_xw("SELECT * FROM v_ps1_xw_flag_reason WHERE city_id=:c "
                      "ORDER BY device_type", c=city))
    if path == "/ps1/xw-state-mix":
        return ok(_xw("SELECT * FROM v_ps1_xw_state_mix WHERE city_id=:c "
                      "ORDER BY device_type, ps1_risk_tier, device_state", c=city))
    if path == "/ps1/xw-act-now":
        top = int((params or {}).get("top", 100))
        # IN_SPELL first: a device down now outranks one whose window opened
        # today, and both outrank anything already back in service.
        return ok(_xw("SELECT * FROM v_ps1_xw_act_now WHERE city_id=:c "
                      "ORDER BY (device_state = 'IN_SPELL') DESC, "
                      "ps1_fail_prob DESC LIMIT :t", c=city, t=top))
    if path == "/ps1/table-status":
        return ok(_xw("SELECT * FROM v_ps1_table_status ORDER BY table_name"))
    if path == "/ps1/feature-importance":
        dc = (params or {}).get("device_category", "TVM")
        return ok(rows("SELECT feature_name,avg_importance,avg_shap FROM ps1_feature_importance WHERE city_id=:c AND device_category=:d AND computed_date=(SELECT MAX(computed_date) FROM ps1_feature_importance WHERE city_id=:c) ORDER BY feat_rank", c=city, d=dc))
    # ---- shared fleet base statistic (added 2026-07-27) ----------------
    # One route, read by the PS1, PS2, PS3 and PS5 tabs alike, so the programme
    # states its headline event counts once. OOS is the denominator and
    # chargeable the subset -- never the reverse: a chargeable event is a
    # contract classification applied AFTER the physical outage, so it cannot be
    # the baseline the fleet is measured against.
    #
    # Returns an empty list, not zeros, when nothing is loaded. A zero here would
    # read as "no failures in Chicago", which is a very different claim from
    # "we have not loaded this yet".
    # ---- top-level device <-> serial <-> component map (added 2026-07-27) --
    # Refreshed daily by cubic-mars-dim-loader. One dimension for every tab, so a
    # serial resolves to the same device, component and age everywhere.
    if path == "/fleet/device-serials":
        cat = (params or {}).get("device_category")
        dev = (params or {}).get("device_id")
        w, kw = "city_id=:c", {"c": city}
        if cat: w += " AND mars_device_category=:k"; kw["k"] = cat
        if dev: w += " AND device_id=:d"; kw["d"] = dev
        return ok(rows(
            "SELECT device_id, serial_id, mars_device_category, component_description, "
            " component_age_days, age_is_negative, as_of_date "
            f"FROM v_device_serial WHERE {w} ORDER BY device_id, component_description "
            "LIMIT 5000", **kw))
    # Component inventory + the fan-out factor. Age stats exclude negative ages
    # and report their count separately -- a component fitted after the event is
    # a repair replacement, and averaging it in distorts the age story.
    if path == "/fleet/component-inventory":
        return ok(rows(
            "SELECT mars_device_category AS device_category, component_description, "
            " n_components, n_devices, avg_age_days, min_age_days, max_age_days, "
            " n_replaced_after_event, as_of_date "
            "FROM v_component_inventory WHERE city_id=:c "
            "ORDER BY n_components DESC", c=city))
    if path == "/fleet/serial-coverage":
        return ok(rows(
            "SELECT mars_device_category AS device_category, n_devices, n_serial_rows, "
            " n_distinct_serials, serials_per_device, as_of_date "
            "FROM v_device_serial_counts WHERE city_id=:c ORDER BY n_devices DESC", c=city))

    if path == "/fleet/baseline":
        scope = (params or {}).get("scope")
        cat = (params or {}).get("device_category")
        w, kw = "city_id=:c", {"c": city}
        if scope:
            w += " AND scope=:s"; kw["s"] = scope
        if cat:
            w += " AND device_category=:d"; kw["d"] = cat
        return ok(rows(
            "SELECT scope, device_category, period_start, period_end, "
            " total_oos_events, chargeable_events, chargeable_pct, n_devices, "
            " source_table, as_of_date "
            f"FROM v_fleet_event_baseline WHERE {w} "
            "ORDER BY scope, CASE device_category WHEN 'ALL' THEN 0 ELSE 1 END, "
            " total_oos_events DESC", **kw))

    if path == "/ps1/station-summary":
        # 2026-07-26: LEFT JOIN dim_device_station for the real garage name.
        # The summary table stores facility_id only, and "45" on a screen means
        # nothing to an operations person -- "North Park" does. LEFT JOIN, not
        # INNER, so a facility missing from the dimension still shows its numbers
        # instead of vanishing from the tab.
        # Ordered by predicted_failures: a 1-device depot at 99% risk is not the
        # headline; a 236-device garage with 106 devices flagged is.
        return ok(rows(
            "SELECT s.facility_id, f.facility_name, f.operator_name, "
            " s.total_devices, s.predicted_failures, s.avg_risk_pct, "
            " s.critical_count, s.high_count, s.medium_count, s.last_inference_date "
            "FROM ps1_station_summary s "
            "LEFT JOIN (SELECT DISTINCT city_id, facility_id, facility_name, operator_name "
            "             FROM dim_device_station) f "
            "  ON f.city_id = s.city_id AND f.facility_id = s.facility_id "
            "WHERE s.city_id=:c AND s.computed_date=("
            "  SELECT MAX(computed_date) FROM ps1_station_summary WHERE city_id=:c) "
            "ORDER BY s.predicted_failures DESC, s.avg_risk_pct DESC", c=city))
    if path == "/ps1/explainability":
        pid = (params or {}).get("prediction_id", "")
        return ok(rows("SELECT feature_name,shap_value,feature_value FROM ps1_explainability WHERE city_id=:c AND prediction_id=:p AND computed_date=(SELECT MAX(computed_date) FROM ps1_explainability WHERE city_id=:c) ORDER BY ABS(shap_value) DESC", c=city, p=pid))
    if path == "/ps1/risk-bands":
        return ok(rows("SELECT device_category,band,device_count,pct FROM ps1_risk_bands WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps1_risk_bands WHERE city_id=:c)", c=city))
    if path == "/ps1/threshold-sweep":
        return ok(rows("SELECT device_category,threshold,precision,recall,alert_rate,f1 FROM ps1_threshold_sweep WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps1_threshold_sweep WHERE city_id=:c) ORDER BY device_category,threshold", c=city))
    if path == "/ps1/calibration":
        return ok(rows("SELECT device_category,bin_lo,bin_hi,pred_mean,actual_rate,n FROM ps1_calibration WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps1_calibration WHERE city_id=:c) ORDER BY device_category,bin_lo", c=city))
    if path == "/ps1/confusion":
        return ok(rows("SELECT device_category,tp,fp,tn,fn FROM ps1_confusion WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps1_confusion WHERE city_id=:c)", c=city))
    # ---- PS3 failure-SEVERITY ----
    if path == "/ps3/summary":
        r = rows("SELECT * FROM ps3_severity_summary WHERE city_id=:c ORDER BY as_of_date DESC LIMIT 1", c=city)
        return ok(r[0] if r else {})
    if path == "/ps3/drivers":
        return ok(rows("SELECT feature,shap_importance,solo_auc,driver_rank FROM ps3_severity_drivers WHERE city_id=:c ORDER BY driver_rank ASC", c=city))
    if path == "/ps3/devices":
        # 2026-07-26 -- held-out split only. The train/val rows were in-sample fit
        # statistics; serving them let the dashboard draw TVM train f1_macro 0.920
        # beside its test 0.898 as if both were evidence of generalisation.
        # Ordered best-first so the strongest per-device head leads.
        # 27-Jul-2026. Repointed from ps3_device_metrics to v_ps3_device_all.
        #
        # ps3_device_metrics is CATEGORY-level model metrics -- three rows of
        # f1_macro, not devices -- and it has no split='test' rows, so this
        # route returned [] while ps3_device_predictions held 927 real devices.
        # The name says devices; it now returns devices.
        #
        # v_ps3_device_all covers all THREE categories: a validator appears with
        # its components and n_incidents NULL (never 0 -- zero would assert it
        # had no failures, which PS3 cannot know for a type outside its feed).
        return ok(rows(
            "SELECT device_id, mars_device_category AS device_category, coverage_status,"
            " n_incidents, pct_critical_pred, dominant_pred_severity,"
            " dominant_pred_component, avg_component_age_days, n_serials,"
            " n_component_types, last_incident_dtm, computed_date "
            "FROM v_ps3_device_all WHERE city_id=:c "
            "ORDER BY n_incidents DESC NULLS LAST, device_id LIMIT 2000", c=city))
    if path == "/ps3/predictions":
        # 2026-07-26 -- the 2026-07-13 PS1->PS3 bridge smoke test left synthetic
        # rows in ps3_severity_predictions keyed INC-PS1-0001..n (actual_label
        # NULL, invented device ids). They are excluded here so they cannot reach
        # the dashboard even before the DELETE in
        # sql/18_purge_ps3_bridge_test_rows.sql is applied.
        return ok(rows("SELECT device_id,mars_device_category,incident_id,incident_dtm,"
                       "predicted_label,proba_critical,actual_label "
                       "FROM ps3_severity_predictions "
                       "WHERE city_id=:c AND incident_id NOT LIKE 'INC-PS1-%' "
                       "ORDER BY incident_dtm DESC LIMIT 200", c=city))
    if path == "/ps5/status":
        return ok(rows("SELECT device_type,concordance_index,registry_status,dashboard_ready,blockers FROM ps5_reliability_status WHERE city_id=:c", c=city))
    # ==== PS5 v5 NOTEBOOK OUTPUTS (sql/29 + sql/30, loaded 27-Jul: 29,441 rows) ====
    # These serve the REAL survival run. /ps5/status above is the older
    # category-level registry state and is kept as-is; it answers "is the model
    # shippable", not "what does it say about this device".
    if path == "/ps5/device-rul":
        # act_now (overdue AND <=30 days RUL) leads, then shortest RUL. Ranks in
        # the view are WITHIN device type -- TVM and validator survival models
        # are fitted separately, so their day counts are not on one scale.
        dt = (params or {}).get("device_type")
        w = "city_id=:c"; kw = {"c": city}
        if dt:
            w += " AND device_type=:d"; kw["d"] = str(dt).upper().strip()
        return ok(rows(
            "SELECT device_type, device_id, facility_id, risk_band, is_overdue,"
            " rul_standard_days, predicted_median_survival_days, hazard_score,"
            " current_healthy_age_days, days_since_hw_oos, roll_fail_30d,"
            " n_prior_oos, rul_rank_in_type, n_devices_in_type, act_now,"
            " feature_asof_date "
            f"FROM v_ps5_device_rul WHERE {w} "
            "ORDER BY act_now DESC, rul_standard_days ASC NULLS LAST LIMIT 3000", **kw))
    if path == "/ps5/serial-rul":
        dt = (params or {}).get("device_type")
        w = "city_id=:c"; kw = {"c": city}
        if dt:
            w += " AND device_type=:d"; kw["d"] = str(dt).upper().strip()
        return ok(rows(
            "SELECT device_type, device_id, component_serial_nbr, has_serial,"
            " component_type_name, component_age_days, risk_tier, risk_score,"
            " expected_component_rul_days, predicted_median_survival_days,"
            " is_overdue, device_oos_failures_total, act_now, serial_source,"
            " feature_asof_date "
            f"FROM v_ps5_serial_rul WHERE {w} "
            "ORDER BY act_now DESC, expected_component_rul_days ASC NULLS LAST "
            "LIMIT 3000", **kw))
    if path == "/ps5/leaderboard":
        # sd travels WITH oot_cindex on purpose: a high concordance with a wide
        # fold-to-fold spread is not better than a steadier lower one, and the
        # pair has to be read together.
        return ok(rows(
            "SELECT device_type, model, feats, oot_cindex, sd, window_label "
            "FROM ps5_cindex_leaderboard WHERE city_id=:c "
            "ORDER BY device_type, oot_cindex DESC NULLS LAST", c=city))
    if path == "/ps5/importance":
        # cindex_drop is the FALL in concordance when the feature is shuffled, so
        # LARGER means more important -- the opposite reading from a SHAP
        # magnitude. Ordered descending so the chart cannot invert the story.
        return ok(rows(
            "SELECT device_type, feature_name, cindex_drop, is_enriched "
            "FROM ps5_permutation_importance WHERE city_id=:c "
            "ORDER BY device_type, cindex_drop DESC NULLS LAST", c=city))
    if path == "/ps5/coverage":
        return ok(rows(
            "SELECT device_type, source_name, table_name, status, pct_matched,"
            " tel_min, tel_max, n_feats "
            "FROM ps5_enrich_coverage WHERE city_id=:c "
            "ORDER BY device_type, pct_matched ASC NULLS FIRST", c=city))
    if path == "/ps5/serial-grain":
        # Grain audit, not a dashboard panel. sql/30 asserted (device_id,
        # component_serial_nbr) was unique; gates satisfied it, TVM and
        # validators did not. This measures what actually varies within a
        # duplicated pair instead of guessing a wider key.
        return ok(rows(
            "SELECT device_type, COUNT(*) AS n_dup_keys, SUM(n_rows) AS n_rows,"
            " MAX(n_rows) AS worst_dup,"
            " MAX(n_component_types) AS max_component_types,"
            " MAX(n_asof_dates) AS max_asof_dates,"
            " MAX(n_serial_sources) AS max_serial_sources,"
            " MAX(n_risk_tiers) AS max_risk_tiers,"
            " MAX(n_rul_values) AS max_rul_values "
            "FROM v_ps5_serial_dupes WHERE city_id=:c GROUP BY device_type", c=city))
    # ------------------------------------------------------------------
    # PS4 v3 WEEKLY ANOMALY / CLUSTERING            29-Jul-2026
    #
    # Serves the v3 pipeline (sql/38). Every route reads a v_ps4_* view, and
    # every view resolves the pipeline_version through v_ps4_v3_current, so the
    # dashboard follows whichever version was loaded last without a code change.
    #
    # The OLD /ps4/alerts route below is untouched and still serves
    # ps4_anomaly_alerts. Both feeds are live at once on purpose: v3 is the new
    # story, the old one is the fallback if v3 does not land.
    #
    # _v3() never raises. A missing table returns an empty list with the reason
    # attached instead of a 500 -- the PS1 device-360 outage was one unguarded
    # helper turning a missing column into a dead tab.
    # ------------------------------------------------------------------
    if path.startswith("/ps4/weekly") or path.startswith("/ps4/cluster-") \
            or path == "/ps4/v3-status":
        def _v3(sql, **kw):
            try:
                return rows(sql, **kw)
            except Exception as e:
                print("PS4V3 route failed:", path, type(e).__name__, str(e)[:300])
                return [{"_unavailable": f"{type(e).__name__}: {str(e)[:200]}"}]

        p = params or {}
        dt = (p.get("device_type") or "").upper()
        wk = p.get("week") or ""
        try:
            lim = min(int(p.get("limit") or 500), 2000)
        except Exception:
            lim = 500

        if path == "/ps4/v3-status":
            return ok({
                "tables": _v3("SELECT * FROM v_ps4_v3_table_status"),
                "run": _v3("SELECT * FROM v_ps4_v3_current WHERE city_id=:c", c=city),
                "reconcile": _v3("SELECT * FROM v_ps4_weekly_alert_reconcile "
                                 "WHERE city_id=:c ORDER BY device_type", c=city),
                "quality": _v3("SELECT * FROM v_ps4_cluster_quality WHERE city_id=:c "
                               "ORDER BY device_type", c=city),
            })

        if path == "/ps4/cluster-quality":
            return ok(_v3("SELECT * FROM v_ps4_cluster_quality WHERE city_id=:c "
                          "ORDER BY device_type", c=city))

        if path == "/ps4/cluster-profile":
            if dt:
                return ok(_v3("SELECT * FROM v_ps4_v3_cluster_profile WHERE city_id=:c "
                              "AND device_type=:d ORDER BY device_type, cluster_id",
                              c=city, d=dt))
            return ok(_v3("SELECT * FROM v_ps4_v3_cluster_profile WHERE city_id=:c "
                          "ORDER BY device_type, cluster_id", c=city))

        if path == "/ps4/weekly-timeline":
            return ok(_v3("SELECT * FROM v_ps4_weekly_timeline WHERE city_id=:c "
                          "ORDER BY week_start, device_type", c=city))

        if path == "/ps4/weekly-alerts":
            # Ordered by cluster_distance_ratio_max, not by the raw anomaly
            # score: the ratio is normalised by each cluster's own training p99,
            # so it is comparable across device types. Sorting a mixed list by
            # the raw score ranks device types, not devices.
            if dt and wk:
                return ok(_v3("SELECT * FROM v_ps4_weekly_alerts WHERE city_id=:c "
                              "AND device_type=:d AND week_start=CAST(:w AS date) "
                              "ORDER BY cluster_distance_ratio_max DESC NULLS LAST "
                              "LIMIT :l", c=city, d=dt, w=wk, l=lim))
            if dt:
                return ok(_v3("SELECT * FROM v_ps4_weekly_alerts WHERE city_id=:c "
                              "AND device_type=:d ORDER BY week_start DESC, "
                              "cluster_distance_ratio_max DESC NULLS LAST LIMIT :l",
                              c=city, d=dt, l=lim))
            if wk:
                return ok(_v3("SELECT * FROM v_ps4_weekly_alerts WHERE city_id=:c "
                              "AND week_start=CAST(:w AS date) ORDER BY "
                              "cluster_distance_ratio_max DESC NULLS LAST LIMIT :l",
                              c=city, w=wk, l=lim))
            return ok(_v3("SELECT * FROM v_ps4_weekly_alerts WHERE city_id=:c "
                          "ORDER BY week_start DESC, cluster_distance_ratio_max "
                          "DESC NULLS LAST LIMIT :l", c=city, l=lim))

        if path == "/ps4/weekly-persistent":
            return ok(_v3("SELECT * FROM v_ps4_weekly_persistent WHERE city_id=:c "
                          "ORDER BY actionable_weeks DESC, worst_distance_ratio "
                          "DESC NULLS LAST LIMIT :l", c=city, l=lim))

        if path == "/ps4/weekly-facility":
            return ok(_v3("SELECT * FROM v_ps4_weekly_facility WHERE city_id=:c "
                          "ORDER BY actionable_devices DESC, devices DESC LIMIT :l",
                          c=city, l=lim))

        if path == "/ps4/weekly-device":
            dev = p.get("device_id") or ""
            if not dev:
                return err(400, "device_id required")
            return ok(_v3("SELECT * FROM v_ps4_weekly_device WHERE city_id=:c "
                          "AND device_id=:d ORDER BY week_start DESC LIMIT 60",
                          c=city, d=dev))

        if path == "/ps4/weekly":
            if dt and wk:
                return ok(_v3("SELECT * FROM v_ps4_weekly_device WHERE city_id=:c "
                              "AND device_type=:d AND week_start=CAST(:w AS date) "
                              "ORDER BY cluster_distance_ratio_max DESC NULLS LAST "
                              "LIMIT :l", c=city, d=dt, w=wk, l=lim))
            if dt:
                return ok(_v3("SELECT * FROM v_ps4_weekly_device WHERE city_id=:c "
                              "AND device_type=:d ORDER BY week_start DESC, "
                              "cluster_distance_ratio_max DESC NULLS LAST LIMIT :l",
                              c=city, d=dt, l=lim))
            return ok(_v3("SELECT * FROM v_ps4_weekly_device WHERE city_id=:c "
                          "ORDER BY week_start DESC, cluster_distance_ratio_max "
                          "DESC NULLS LAST LIMIT :l", c=city, l=lim))

    # ------------------------------------------------------------------
    # PS3 v2 - HARDENED REMEDIATION RUN                29-Jul-2026
    #
    # Serves the ps3_v2_* tables (sql/39). The existing /ps3/* routes below are
    # untouched and still serve the previous feed - both are live at once so v2
    # can be demonstrated with the old one intact as a fallback.
    #
    # _v2() never raises: a missing table returns the reason in the payload
    # rather than a 500 that kills the whole tab.
    # ------------------------------------------------------------------
    if path.startswith("/ps3/v2"):
        def _v2(sql, **kw):
            try:
                return rows(sql, **kw)
            except Exception as e:
                print("PS3V2 route failed:", path, type(e).__name__, str(e)[:300])
                return [{"_unavailable": f"{type(e).__name__}: {str(e)[:200]}"}]
        p = params or {}
        cat = (p.get("device_category") or "").upper()
        try: lim = min(int(p.get("limit") or 500), 5000)
        except Exception: lim = 500

        if path == "/ps3/v2-status":
            return ok({
                "tables": _v2("SELECT * FROM v_ps3_v2_table_status"),
                "run":    _v2("SELECT * FROM v_ps3_v2_current WHERE city_id=:c", c=city),
                "policy": _v2("SELECT * FROM v_ps3_v2_policy WHERE city_id=:c", c=city),
                "readiness": _v2("SELECT * FROM ps3_v2_readiness WHERE city_id=:c "
                                 "ORDER BY device_category", c=city),
                "promotion": _v2("SELECT * FROM ps3_v2_promotion_status WHERE city_id=:c "
                                 "ORDER BY device_category, head", c=city),
                "freshness": _v2("SELECT * FROM ps3_v2_data_freshness WHERE city_id=:c", c=city),
            })
        if path == "/ps3/v2-scorecard":
            return ok(_v2("SELECT * FROM v_ps3_v2_scorecard WHERE city_id=:c "
                          "ORDER BY device_category, head", c=city))
        if path == "/ps3/v2-queue":
            if cat:
                return ok(_v2("SELECT * FROM v_ps3_v2_queue WHERE city_id=:c AND device_category=:d "
                              "ORDER BY action_priority_score DESC NULLS LAST LIMIT :l",
                              c=city, d=cat, l=lim))
            return ok(_v2("SELECT * FROM v_ps3_v2_queue WHERE city_id=:c "
                          "ORDER BY action_priority_score DESC NULLS LAST LIMIT :l", c=city, l=lim))
        if path == "/ps3/v2-shap":
            ev = p.get("event_id") or ""
            dev = p.get("device_id") or ""
            if ev:
                return ok(_v2("SELECT * FROM v_ps3_v2_shap WHERE city_id=:c AND availability_event_id=:e "
                              "ORDER BY feature_rank LIMIT 40", c=city, e=ev))
            if dev:
                return ok(_v2("SELECT * FROM v_ps3_v2_shap WHERE city_id=:c AND device_id=:d "
                              "ORDER BY event_timestamp DESC, feature_rank LIMIT 60", c=city, d=dev))
            return ok(_v2("SELECT * FROM v_ps3_v2_shap WHERE city_id=:c "
                          "ORDER BY abs_shap_value DESC NULLS LAST LIMIT :l", c=city, l=lim))
        if path == "/ps3/v2-shap-global":
            return ok(_v2("SELECT * FROM ps3_v2_shap_global WHERE city_id=:c "
                          "ORDER BY device_category, head, feature_rank LIMIT 200", c=city))
        if path == "/ps3/v2-drivers":
            return ok(_v2("SELECT * FROM ps3_v2_driver_importance WHERE city_id=:c "
                          "ORDER BY mean_macro_f1_drop DESC NULLS LAST LIMIT 100", c=city))
        if path == "/ps3/v2-devices":
            return ok(_v2("SELECT * FROM ps3_v2_device_reliability WHERE city_id=:c "
                          "ORDER BY critical_rate DESC NULLS LAST, incident_count DESC LIMIT :l",
                          c=city, l=lim))
        if path == "/ps3/v2-components":
            return ok(_v2("SELECT * FROM ps3_v2_component_reliability WHERE city_id=:c "
                          "ORDER BY portfolio_priority_score DESC NULLS LAST LIMIT 100", c=city))
        if path == "/ps3/v2-facilities":
            return ok(_v2("SELECT * FROM ps3_v2_facility_hotspots WHERE city_id=:c "
                          "ORDER BY incident_count DESC NULLS LAST LIMIT :l", c=city, l=lim))
        if path == "/ps3/v2-models":
            return ok(_v2("SELECT * FROM ps3_v2_model_comparison WHERE city_id=:c "
                          "ORDER BY device_category, head, validation_f1_macro DESC NULLS LAST", c=city))
        if path == "/ps3/v2-rootcause":
            # Observed attribution, not a prediction. The run publishes no
            # per-incident predicted component, so this is incident history
            # rolled to device x serial x component -- labelled as such in the
            # view rather than dressed up as a model output.
            if cat:
                return ok(_v2("SELECT * FROM v_ps3_v2_rootcause WHERE city_id=:c "
                              "AND device_category=:d ORDER BY critical_weighted DESC "
                              "NULLS LAST LIMIT :l", c=city, d=cat, l=lim))
            return ok(_v2("SELECT * FROM v_ps3_v2_rootcause WHERE city_id=:c "
                          "ORDER BY critical_weighted DESC NULLS LAST LIMIT :l", c=city, l=lim))
        if path == "/ps3/v2-rootcause-rollup":
            return ok(_v2("SELECT * FROM v_ps3_v2_rootcause_rollup WHERE city_id=:c "
                          "ORDER BY critical_weighted DESC NULLS LAST", c=city))
        if path == "/ps3/v2-rootcause-concentration":
            return ok(_v2("SELECT * FROM v_ps3_v2_rootcause_concentration WHERE city_id=:c "
                          "AND total_incidents >= 2 ORDER BY concentration DESC NULLS LAST, "
                          "total_incidents DESC LIMIT :l", c=city, l=lim))
        if path == "/ps3/v2-causal":
            return ok(_v2("SELECT * FROM v_ps3_v2_causal WHERE city_id=:c "
                          "ORDER BY device_category, treatment_id", c=city))

    if path == "/ps4/alerts":
        st = (params or {}).get("status")
        if st: return ok(rows("SELECT * FROM ps4_anomaly_alerts WHERE city_id=:c AND status=:s ORDER BY detected_at DESC LIMIT 200", c=city, s=st))
        return ok(rows("SELECT * FROM ps4_anomaly_alerts WHERE city_id=:c ORDER BY detected_at DESC LIMIT 200", c=city))
    if path.startswith("/ps4/alerts/") and method == "PATCH":
        aid = path.rsplit("/", 1)[-1]; new = (body or {}).get("status")
        if new not in ("active", "investigating", "acknowledged", "resolved"): return err(400, "bad status")
        conn().run("UPDATE ps4_anomaly_alerts SET status=:s, acknowledged_at=CASE WHEN :s='acknowledged' THEN NOW() ELSE acknowledged_at END, resolved_at=CASE WHEN :s='resolved' THEN NOW() ELSE resolved_at END WHERE id=CAST(:i AS uuid)", s=new, i=aid)
        return ok({"id": aid, "status": new})
    if path == "/overview/summary":
        return err(501, "v_executive_summary deferred to Phase 2 (needs PS1/PS3/PS4 tables)")
    if path == "/ps1/device-360":
        dev = (params or {}).get("device_id", "")
        if not dev: return err(400, "device_id required")
        return ok(_device_360(city, dev))
    if path == "/ps1/servicenow-stage" and method == "POST":
        d = body or {}; dev = d.get("device_id", "")
        if not dev: return err(400, "device_id required")
        import uuid as _uuid
        sid = str(_uuid.uuid4()); pl = d.get("payload", {})
        conn().run("INSERT INTO servicenow_staging(id,city_id,device_id,device_category,short_description,payload_json,status,created_at) VALUES(CAST(:i AS uuid),:c,:d,:cat,:sd,:p,'staged',NOW())",
                   i=sid, c=city, d=dev, cat=d.get("device_category"), sd=(d.get("short_description") or "")[:238], p=json.dumps(pl, default=str))
        return ok({"staged_id": sid, "status": "staged", "note": "Incident STAGED only (no live post). Wire Robin's ServiceNow endpoint to submit."})
    if path == "/ps1/servicenow-staged":
        return ok(rows("SELECT id,device_id,device_category,short_description,status,created_at FROM servicenow_staging WHERE city_id=:c ORDER BY created_at DESC LIMIT 100", c=city))

    # ==== PS3 TWO-HEAD ROUTES (migrations 15 + 17) ====
    # Rewritten 2026-07-26 from PS3_delivery_pavan_updated_21Jul2026/dashboard/
    # ps3_api_routes_patch.py, whose SQL targeted the SUPERSEDED bundle rds/04
    # schema (prediction_head / predicted_class / predicted_collapsed /
    # incident_dtm / as_of_date / model_version). None of those columns exist in
    # migration 15. Severity fields come from the gate-aware views in 17, so a
    # head that failed its macro-F1 gate returns NULL + severity_shippable=false
    # instead of a number the dashboard would draw.
    if path in ("/ps3/rootcause/summary", "/ps3/severity/summary"):
        head = "root_cause" if path.endswith("rootcause/summary") else "severity"
        # 2026-07-26: accuracy, weighted-F1 and PR-AUC come from ps3_head_summary
        # via a LEFT JOIN rather than from the view, which does not select them.
        # Joining is cheaper and safer than replacing v_ps3_two_head_scorecard,
        # which sql/17's gate-aware views depend on.
        #
        # ACCURACY IS DELIBERATELY NOT THE HEADLINE. Read GATE severity below:
        # 99.11% accurate, macro-F1 0.4978. With 335 ALL_FUNCTIONS against 3
        # OTHER, always guessing the majority class scores exactly that. Accuracy
        # is served because it is asked for and because the contrast with macro-F1
        # is itself the evidence -- not because it decides anything.
        sc = rows(
            "SELECT v.device_category,v.head,v.modeled,v.champion,v.target_col,v.n_classes,"
            "v.class_labels,v.test_f1_macro,v.test_auc_macro_ovr,v.macro_f1_floor,v.gate_pass,"
            "v.n_train,v.n_test,v.n_features,v.run_id,v.run_ts,"
            "h.test_accuracy,h.test_f1_weighted,"
            "COALESCE(v.test_pr_auc_macro, h.test_pr_auc_macro) AS test_pr_auc_macro,"
            # the majority-class score for k classes: what accuracy a model gets
            # for free. Shown next to accuracy so 99.11% cannot pass unchallenged.
            "CASE WHEN v.n_classes > 0 THEN ROUND(1.0/v.n_classes, 4) END AS macro_f1_ceiling_if_constant "
            "FROM v_ps3_two_head_scorecard v "
            "LEFT JOIN ps3_head_summary h "
            "  ON h.city_id=v.city_id AND h.run_id=v.run_id "
            " AND h.device_category=v.device_category AND h.head=v.head "
            "WHERE v.city_id=:c AND v.head=:h "
            # champion-first: shippable heads lead, then strongest macro-F1.
            "ORDER BY v.gate_pass DESC NULLS LAST, v.test_f1_macro DESC NULLS LAST, v.device_category",
            c=city, h=head)
        for r in sc:
            v, why = model_verdict(f1=r.get("test_f1_macro"), auc=r.get("test_auc_macro_ovr"),
                                   n_classes=r.get("n_classes"), floor=r.get("macro_f1_floor"),
                                   gate_pass=r.get("gate_pass"))
            r["verdict"], r["verdict_evidence"] = v, why
        if _truthy((params or {}).get("exclude_degenerate")):
            sc = [r for r in sc if r["verdict"] != "degenerate"]
        return ok(sc)
    # (the two former single-head blocks were merged into the verdict-aware handler above)
    if path in ("/ps3/rootcause/drivers", "/ps3/severity/drivers", "/ps3/drivers/head"):
        cat = (params or {}).get("device_category")
        head = (params or {}).get("head") or ("severity" if path == "/ps3/severity/drivers" else "root_cause")
        base = ("SELECT f.device_category,f.head,f.feature_rank,f.feature_name,f.shap_importance "
                "FROM ps3_head_feature_importance f "
                "JOIN v_ps3_latest_run l ON l.city_id=f.city_id AND l.run_id=f.run_id "
                "WHERE f.city_id=:c AND f.head=:h")
        if cat:
            return ok(rows(base + " AND f.device_category=:d ORDER BY f.feature_rank",
                           c=city, h=head, d=cat))
        return ok(rows(base + " ORDER BY f.device_category,f.feature_rank", c=city, h=head))
    # ---- PS3 macro rollup (added 2026-07-26) ----
    # One row per device category: the top-level view, computed in SQL so the
    # dashboard does not have to pull the 300-row device page to build a fleet
    # summary (and cannot accidentally summarise only the first page).
    # Counts come from the gate-aware views, so a head below its macro-F1 floor
    # contributes devices and incidents but no severity number.
    # Collapse health: the standing check that pct_critical_pred cannot silently
    # go flat again. If one label holds collapse_share 1.0000 for a category, the
    # collapse has regressed to a constant -- which is the 21-Jul failure mode
    # (MAJOR on 100% of 34,612 incidents => pct_critical_pred 0.0 everywhere).
    if path == "/ps3/collapse-health":
        return ok(rows(
            "SELECT mars_device_category AS device_category, pred_severity_collapsed, n, collapse_share "
            "FROM v_ps3_collapse_health WHERE city_id=:c "
            "AND run_id=(SELECT run_id FROM v_ps3_latest_run WHERE city_id=:c) "
            "ORDER BY mars_device_category, n DESC", c=city))
    # 27-Jul-2026. Fleet Overview now rolls up ALL THREE device types. It used to
    # read v_ps3_device_risk, which holds only what PS3 modelled, so the page
    # rendered two cards and gave no hint a third device type existed at all.
    # v_ps3_rollup_all unions the hardware dimension in and returns n_incidents
    # as NULL -- not 0 -- for a category with no availability-event feed.
    if path == "/ps3/rollup":
        return ok(rows(
            "SELECT device_category, n_devices, n_devices_scored, n_incidents,"
            " n_scored, n_gated, avg_pct_critical, avg_component_age_days,"
            " last_incident_dtm, computed_date, n_serials, n_distinct_serials,"
            " modelled "
            "FROM v_ps3_rollup_all WHERE city_id=:c "
            "ORDER BY modelled DESC, n_incidents DESC NULLS LAST", c=city))
    if path == "/ps3/rollup-scored-only":
        return ok(rows(
            "SELECT d.mars_device_category AS device_category,"
            " COUNT(*) AS n_devices,"
            " SUM(d.n_incidents) AS n_incidents,"
            " COUNT(*) FILTER (WHERE d.pct_critical_pred IS NOT NULL) AS n_scored,"
            " COUNT(*) FILTER (WHERE d.severity_shippable IS FALSE) AS n_gated,"
            " ROUND(AVG(d.pct_critical_pred)::numeric, 4) AS avg_pct_critical,"
            " ROUND(AVG(d.avg_component_age_days)::numeric, 1) AS avg_component_age_days,"
            " MAX(d.last_incident_dtm) AS last_incident_dtm,"
            " MAX(d.computed_date) AS computed_date,"
            " (SELECT COUNT(*) FROM v_ps3_serial_risk s"
            "    WHERE s.city_id=d.city_id AND s.mars_device_category=d.mars_device_category)"
            "   AS n_serials,"
            " (SELECT COUNT(DISTINCT s2.matched_serial_nbr) FROM v_ps3_serial_risk s2"
            "    WHERE s2.city_id=d.city_id AND s2.mars_device_category=d.mars_device_category)"
            "   AS n_distinct_serials "
            "FROM v_ps3_device_risk d WHERE d.city_id=:c "
            "GROUP BY d.city_id, d.mars_device_category "
            "ORDER BY SUM(d.n_incidents) DESC", c=city))
    # Predicted severity-class mix at the incident grain, per category. This is
    # the 4-class label the model actually emits (ALL_FUNCTIONS / ALL_PURCHASE /
    # PURCHASE_CARD / PURCHASE_PRODUCT for TVM), not the 2-class collapse.
    if path == "/ps3/severity-mix":
        return ok(rows(
            "SELECT p.mars_device_category AS device_category, p.pred_severity, "
            " COUNT(*) AS n, "
            " ROUND(AVG(p.pred_severity_conf)::numeric, 4) AS avg_conf, "
            " COUNT(*) FILTER (WHERE p.actual_severity = p.pred_severity) AS n_agree, "
            " COUNT(*) FILTER (WHERE p.actual_severity IS NOT NULL) AS n_labelled "
            "FROM ps3_incident_predictions p "
            "WHERE p.city_id=:c AND p.run_id=(SELECT run_id FROM v_ps3_latest_run WHERE city_id=:c) "
            "GROUP BY p.mars_device_category, p.pred_severity "
            "ORDER BY p.mars_device_category, COUNT(*) DESC", c=city))
    # Predicted component mix at the incident grain -- the real Pareto source.
    if path == "/ps3/component-mix":
        return ok(rows(
            "SELECT p.mars_device_category AS device_category, p.pred_component, "
            " COUNT(*) AS n, "
            " ROUND(AVG(p.pred_component_conf)::numeric, 4) AS avg_conf, "
            " COUNT(*) FILTER (WHERE p.actual_component = p.pred_component) AS n_agree, "
            " COUNT(*) FILTER (WHERE p.actual_component IS NOT NULL) AS n_labelled "
            "FROM ps3_incident_predictions p "
            "WHERE p.city_id=:c AND p.run_id=(SELECT run_id FROM v_ps3_latest_run WHERE city_id=:c) "
            "GROUP BY p.mars_device_category, p.pred_component "
            "ORDER BY p.mars_device_category, COUNT(*) DESC", c=city))
    # Component inventory at the serial grain (added 2026-07-26 with the v2 run).
    #
    # /ps3/component-mix answers "which component did the model blame", and its
    # top row is the literal class None on ~96% of incidents -- that is the
    # model's honest answer, most OOS events are not attributed to one component.
    # This route answers the different and more actionable question: what
    # components are actually INSTALLED across the fleet, how old are they, and
    # how many incidents was each one exposed to versus attributed. The age
    # spread is where the maintenance conversation lives.
    if path == "/ps3/component-inventory":
        cat = (params or {}).get("device_category")
        w = ("s.city_id=:c AND s.run_id=(SELECT run_id FROM v_ps3_latest_run WHERE city_id=:c) "
             "AND s.component_description IS NOT NULL")
        kw = {"c": city}
        if cat:
            w += " AND s.mars_device_category=:cat"; kw["cat"] = cat
        return ok(rows(
            "SELECT s.mars_device_category AS device_category, s.component_description, "
            " COUNT(*) AS n_components, "
            " COUNT(DISTINCT s.device_id) AS n_devices, "
            # Age stats exclude negative ages. 6 of 2,515 serial rows (and 559 of
            # 34,612 incidents) carry a component whose install date is AFTER the
            # incident -- hw_config holds only the CURRENT configuration, so those
            # are components fitted as part of the repair, not the part that failed.
            # Averaging them in drags tvmsbc from ~3.6k days to a number that is
            # simply wrong. They are counted separately instead of hidden.
            " ROUND(AVG(s.component_age_days) FILTER (WHERE s.component_age_days >= 0)::numeric, 0) AS avg_age_days, "
            " ROUND(MIN(s.component_age_days) FILTER (WHERE s.component_age_days >= 0)::numeric, 0) AS min_age_days, "
            " ROUND(MAX(s.component_age_days)::numeric, 0) AS max_age_days, "
            " COUNT(*) FILTER (WHERE s.component_age_days < 0) AS n_replaced_after_incident, "
            " SUM(s.n_incidents_exposed) AS n_incidents_exposed, "
            " SUM(s.n_incidents_attributed) AS n_incidents_attributed, "
            " ROUND(AVG(s.pct_critical_exposed)::numeric, 4) AS avg_pct_critical_exposed "
            f"FROM ps3_serial_predictions s WHERE {w} "
            "GROUP BY s.mars_device_category, s.component_description "
            "ORDER BY COUNT(*) DESC, AVG(s.component_age_days) DESC", **kw))
    # ---- PS3 coverage (added 2026-07-27) ------------------------------------
    # The answer to "why does the PS3 tab show nothing for Validators".
    #
    # Returns one row per device category in the run's scope, INCLUDING the ones
    # with zero incidents, each carrying the reason and the fleet size from
    # dim_device_serial. That is what lets the tab render "3,329 validators in
    # the fleet, 0 in the PS3 availability-event feed, here is why" rather than
    # an empty panel that reads as a broken dashboard.
    if path == "/ps3/coverage":
        return ok(rows(
            "SELECT device_category, modeled, n_ps3_incidents, n_devices_scored,"
            " n_serials_scored, source_table, exclusion_reason, alternative_coverage,"
            " remediation, n_fleet_devices, n_fleet_serials, n_fleet_components,"
            " device_coverage_ratio, run_id, as_of_date "
            "FROM v_ps3_category_coverage WHERE city_id=:c "
            "ORDER BY modeled DESC, n_ps3_incidents DESC", c=city))
    # Fleet inventory straight off the daily device<->serial dimension. Covers
    # ALL THREE categories, so it is the one component-level view a Validator
    # actually appears in. Deliberately independent of ps3_serial_predictions:
    # that table only holds categories PS3 modelled.
    if path == "/ps3/fleet-inventory":
        cat = (params or {}).get("device_category")
        w = "city_id=:c"; kw = {"c": city}
        if cat:
            w += " AND mars_device_category=:cat"; kw["cat"] = cat
        return ok(rows(
            "SELECT mars_device_category AS device_category, component_description,"
            " n_components, n_devices, avg_age_days, min_age_days, max_age_days,"
            " n_replaced_after_event, as_of_date "
            f"FROM v_component_inventory WHERE {w} "
            "ORDER BY n_components DESC", **kw))
    # Device-grain fleet list for a category, again from the dimension rather
    # than from PS3 output, with the PS3 incident count LEFT JOINed on. A
    # validator therefore appears with its components and a null incident count,
    # which is the truth, instead of not appearing at all.
    if path == "/ps3/fleet-devices":
        cat = (params or {}).get("device_category")
        qtxt = (params or {}).get("q")
        lim = _clamp_int((params or {}).get("limit"), 200, 1, 2000)
        off = _clamp_int((params or {}).get("offset"), 0, 0, 500000)
        w = "v.city_id=:c"; kw = {"c": city, "lim": lim, "off": off}
        if cat:
            w += " AND v.mars_device_category=:cat"; kw["cat"] = cat
        if qtxt:
            w += " AND (v.device_id ILIKE :q OR v.serial_id ILIKE :q"\
                 " OR v.component_description ILIKE :q)"
            kw["q"] = f"%{qtxt}%"
        return ok(rows(
            "SELECT v.device_id, v.mars_device_category AS device_category,"
            " COUNT(DISTINCT v.serial_id) AS n_serials,"
            " COUNT(DISTINCT v.component_description) AS n_component_types,"
            " ROUND(AVG(v.component_age_days) FILTER (WHERE NOT v.age_is_negative)::numeric, 0)"
            "   AS avg_component_age_days,"
            " STRING_AGG(DISTINCT v.component_description, ', ') AS components,"
            " MAX(d.n_incidents) AS ps3_n_incidents,"
            " MAX(d.dominant_pred_component) AS ps3_dominant_component,"
            " MAX(d.dominant_pred_severity) AS ps3_dominant_severity "
            "FROM v_device_serial v "
            "LEFT JOIN v_ps3_device_risk d"
            "  ON d.city_id=v.city_id AND d.device_id=v.device_id "
            f"WHERE {w} "
            "GROUP BY v.device_id, v.mars_device_category "
            "ORDER BY MAX(d.n_incidents) DESC NULLS LAST, v.device_id "
            "LIMIT :lim OFFSET :off", **kw))
    # Monthly incident volume by category and collapsed severity. Feeds the
    # trend line and the stacked area -- both were absent because nothing served
    # a time series, even though ae_start_dtm has been on the table all along.
    if path == "/ps3/timeline":
        return ok(rows(
            "SELECT DATE_TRUNC('month', p.ae_start_dtm)::date AS period,"
            " p.mars_device_category AS device_category,"
            " p.pred_severity_collapsed,"
            " COUNT(*) AS n,"
            " COUNT(DISTINCT p.device_id) AS n_devices,"
            " ROUND(AVG(p.pred_severity_conf)::numeric, 4) AS avg_conf "
            "FROM ps3_incident_predictions p "
            "WHERE p.city_id=:c AND p.ae_start_dtm IS NOT NULL "
            "AND p.run_id=(SELECT run_id FROM v_ps3_latest_run WHERE city_id=:c) "
            "GROUP BY 1, 2, 3 ORDER BY 1", c=city))
    # Station / facility rollup. facility_name is on every incident row and was
    # only ever used as a filter value -- never aggregated, so "where is the
    # trouble" had no answer on this tab.
    if path == "/ps3/facility-rollup":
        return ok(rows(
            "SELECT COALESCE(p.facility_name, 'Unmapped') AS facility_name,"
            " p.facility_id,"
            " p.mars_device_category AS device_category,"
            " COUNT(*) AS n_incidents,"
            " COUNT(DISTINCT p.device_id) AS n_devices,"
            " COUNT(*) FILTER (WHERE p.pred_severity_collapsed='CRITICAL') AS n_critical,"
            " MODE() WITHIN GROUP (ORDER BY p.pred_component) AS top_component,"
            " MAX(p.ae_start_dtm) AS last_incident_dtm "
            "FROM ps3_incident_predictions p "
            "WHERE p.city_id=:c "
            "AND p.run_id=(SELECT run_id FROM v_ps3_latest_run WHERE city_id=:c) "
            "GROUP BY 1, 2, 3 ORDER BY COUNT(*) DESC LIMIT 500", c=city))
    # Does an older component fail more? Bucketed server-side so the scatter
    # sees the WHOLE run rather than the 300-row page the serial route returns.
    #
    # COUNTED AT THE INCIDENT GRAIN, NOT THE SERIAL GRAIN. ps3_serial_predictions
    # .n_incidents is the DEVICE's incident count repeated on each of its serial
    # rows -- summing it gives 132,500 TVM incidents against a true 32,842 (4.03x)
    # and 2,407 GATE against 1,770 (1.36x). n_incidents_exposed is the same
    # figure under an honest name, and n_incidents_attributed is 0 on every row
    # because the root-cause head returns the literal class None for ~96% of
    # incidents. So neither serial-grain column can carry this measure.
    #
    # ps3_incident_predictions carries component_age_days on the incident row
    # itself, one row per availability event. Bucketing that is a true count with
    # no join and therefore no fan-out.
    if path == "/ps3/age-risk":
        return ok(rows(
            "SELECT p.mars_device_category AS device_category,"
            " WIDTH_BUCKET(p.component_age_days, 0, 4000, 8) AS age_bucket,"
            " MIN(p.component_age_days) AS bucket_min_days,"
            " MAX(p.component_age_days) AS bucket_max_days,"
            " COUNT(*) AS n_incidents,"
            " COUNT(DISTINCT p.device_id) AS n_devices,"
            " COUNT(DISTINCT p.matched_serial_nbr) AS n_serials,"
            # Surfaced because it is a finding, not decoration. TVM incidents
            # carry only 15 distinct component_age_days values (median 103, max
            # 303) against GATE's 346 spanning 103..3950, so the whole TVM series
            # lands in one bucket. A reader seeing a single TVM bar needs to know
            # that is the feature being flat, not the chart being broken.
            " COUNT(DISTINCT p.component_age_days) AS n_distinct_ages,"
            " ROUND(COUNT(*)::numeric"
            "       / NULLIF(COUNT(DISTINCT p.device_id), 0), 2) AS incidents_per_device,"
            # Critical share is a property of the severity head, so it is NULLed
            # for any category whose severity head missed its macro-F1 floor
            # (GATE: 0.4978 against a 0.55 floor). Re-deriving it from the raw
            # column here would resurrect a number the rest of the tab suppresses.
            " CASE WHEN BOOL_OR(g.severity_shippable) THEN"
            "   ROUND(COUNT(*) FILTER (WHERE p.pred_severity_collapsed='CRITICAL')::numeric"
            "         / NULLIF(COUNT(*), 0), 4) END AS pct_critical "
            "FROM ps3_incident_predictions p "
            "LEFT JOIN v_ps3_head_gates g"
            "  ON g.city_id=p.city_id AND g.run_id=p.run_id"
            " AND g.device_category=p.mars_device_category "
            "WHERE p.city_id=:c AND p.component_age_days >= 0 "
            "AND p.run_id=(SELECT run_id FROM v_ps3_latest_run WHERE city_id=:c) "
            "GROUP BY 1, 2 ORDER BY 1, 2", c=city))
    if path == "/ps3/incident-predictions":
        cat = (params or {}).get("device_category")
        head = (params or {}).get("head")
        cols = ("availability_event_id,device_id,mars_device_category,matched_serial_nbr,"
                "component_age_days,ae_start_dtm,facility_id,facility_name,")
        if head == "severity":
            cols += "pred_severity,pred_severity_conf,pred_severity_collapsed,actual_severity"
        elif head == "root_cause":
            cols += "pred_component,pred_component_conf,actual_component"
        else:
            cols += ("pred_severity,pred_severity_conf,pred_severity_collapsed,actual_severity,"
                     "pred_component,pred_component_conf,actual_component")
        where = ("p.city_id=:c AND p.run_id=(SELECT run_id FROM v_ps3_latest_run "
                 "WHERE city_id=:c)")
        # 27-Jul-2026. Server-side paging and search. The tab used to fetch a
        # fixed 300 rows of 34,612 and filter them in the browser, so a search
        # for a device outside that first page returned "no rows" -- which read
        # as missing data rather than as an unpaged query.
        lim = _clamp_int((params or {}).get("limit"), 300, 1, 2000)
        off = _clamp_int((params or {}).get("offset"), 0, 0, 500000)
        kw = {"c": city, "lim": lim, "off": off}
        if cat:
            where += " AND p.mars_device_category=:d"; kw["d"] = cat
        for key, col in (("facility_id", "p.facility_id::text"),
                         ("device_id", "p.device_id"),
                         ("serial", "p.matched_serial_nbr"),
                         ("component", "p.pred_component"),
                         ("severity", "p.pred_severity")):
            v = (params or {}).get(key)
            if v:
                where += f" AND {col}=:{key}"; kw[key] = v
        qtxt = (params or {}).get("q")
        if qtxt:
            where += (" AND (p.device_id ILIKE :q OR p.matched_serial_nbr ILIKE :q"
                      " OR p.facility_name ILIKE :q OR p.pred_component ILIKE :q"
                      " OR p.availability_event_id::text ILIKE :q)")
            kw["q"] = f"%{qtxt}%"
        sel = ",".join("p." + x for x in cols.split(","))
        total = rows(f"SELECT COUNT(*) AS n FROM ps3_incident_predictions p WHERE {where}",
                     **{k: v for k, v in kw.items() if k not in ("lim", "off")})
        data = rows(f"SELECT {sel} FROM ps3_incident_predictions p WHERE {where} "
                    "ORDER BY p.ae_start_dtm DESC NULLS LAST LIMIT :lim OFFSET :off", **kw)
        # Shape kept backwards compatible: callers that expect a bare array still
        # work, and the count rides along in a header-style envelope only when
        # the caller opts in with paged=1.
        if (params or {}).get("paged"):
            return ok({"total": (total[0]["n"] if total else 0), "limit": lim,
                       "offset": off, "rows": data})
        return ok(data)
    if path == "/ps3/device-predictions":
        cat = (params or {}).get("device_category")
        where = "city_id=:c"
        lim = _clamp_int((params or {}).get("limit"), 300, 1, 2000)
        off = _clamp_int((params or {}).get("offset"), 0, 0, 500000)
        kw = {"c": city, "lim": lim, "off": off}
        if cat:
            where += " AND mars_device_category=:d"; kw["d"] = cat
        qtxt = (params or {}).get("q")
        if qtxt:
            where += (" AND (device_id ILIKE :q OR dominant_pred_component ILIKE :q"
                      " OR dominant_pred_severity ILIKE :q)")
            kw["q"] = f"%{qtxt}%"
        # 27-Jul-2026. v_ps3_device_all, not v_ps3_device_risk. The risk view
        # holds only the categories PS3 modelled, so 3,329 validators had no row
        # in Device Risk at all. The all-devices view unions the hardware
        # dimension in and tags each row with coverage_status; the model columns
        # come back NULL (never 0) for anything PS3 did not score, and the front
        # end renders that as a word rather than a number.
        #
        # scored_only=1 restores the old behaviour for any caller that wants it.
        if (params or {}).get("scored_only"):
            where += " AND coverage_status='scored'"
        return ok(rows(
            "SELECT device_id,mars_device_category,coverage_status,n_incidents,"
            "pct_critical_pred,dominant_pred_severity,dominant_pred_component,"
            "avg_component_age_days,last_incident_dtm,computed_date,"
            "severity_shippable,rootcause_shippable,severity_f1_macro,severity_floor,"
            "rootcause_f1_macro,rootcause_floor,n_serials,n_component_types,components "
            f"FROM v_ps3_device_all WHERE {where} "
            # Scored rows first, then risk, then incident volume. A NULL-scored
            # validator therefore sorts below every scored device instead of
            # jumping the queue on a NULLS-FIRST accident.
            "ORDER BY (coverage_status='scored') DESC, pct_critical_pred DESC NULLS LAST,"
            " n_incidents DESC NULLS LAST, device_id "
            "LIMIT :lim OFFSET :off", **kw))
    if path == "/ps3/serial-predictions":
        dev = (params or {}).get("device_id")
        cat = (params or {}).get("device_category")
        where = "city_id=:c"
        lim = _clamp_int((params or {}).get("limit"), 300, 1, 2000)
        off = _clamp_int((params or {}).get("offset"), 0, 0, 500000)
        kw = {"c": city, "lim": lim, "off": off}
        if dev:
            where += " AND device_id=:dv"; kw["dv"] = dev
        if cat:
            where += " AND mars_device_category=:d"; kw["d"] = cat
        qtxt = (params or {}).get("q")
        if qtxt:
            where += (" AND (device_id ILIKE :q OR matched_serial_nbr ILIKE :q"
                      " OR dominant_pred_component ILIKE :q"
                      # component_description is the only searchable identity an
                      # unscored validator row has -- omit it and search returns
                      # nothing for the 7,143 serials this change added.
                      " OR component_description ILIKE :q)")
            kw["q"] = f"%{qtxt}%"
        # Same change at the serial grain, and it gains component_description --
        # the installed component's real name, which ps3_serial_predictions only
        # ever carried for scored rows. That column is what makes a validator
        # component row worth putting on screen.
        if (params or {}).get("scored_only"):
            where += " AND coverage_status='scored'"
        return ok(rows(
            "SELECT device_id,matched_serial_nbr,mars_device_category,coverage_status,"
            "component_age_days,component_description,age_is_negative,n_incidents,"
            "dominant_pred_component,pct_critical_pred,last_incident_dtm,"
            "computed_date,severity_shippable,rootcause_shippable "
            f"FROM v_ps3_serial_all WHERE {where} "
            "ORDER BY (coverage_status='scored') DESC, pct_critical_pred DESC NULLS LAST,"
            " n_incidents DESC NULLS LAST, device_id, matched_serial_nbr "
            "LIMIT :lim OFFSET :off", **kw))
    # ---- PS3 ServiceNow staging (added 2026-07-26, mirrors /ps1/servicenow-*) ----
    # api.js already referenced /ps3/servicenow/status and
    # /ps3/servicenow/create-incident, but no server route existed -- every call
    # 404'd. Same staging semantics as PS1: nothing is posted to ServiceNow, the
    # payload is recorded in servicenow_staging for review. Naming the button
    # "post" while silently doing nothing would be worse than not having it.
    if path == "/ps3/servicenow-stage" and method == "POST":
        d = body or {}
        dev = d.get("device_id", "")
        if not dev:
            return err(400, "device_id required")
        import uuid as _uuid
        sid = str(_uuid.uuid4())
        conn().run(
            "INSERT INTO servicenow_staging(id,city_id,device_id,device_category,"
            "short_description,payload_json,status,created_at) "
            "VALUES(CAST(:i AS uuid),:c,:d,:cat,:sd,:p,'staged',NOW())",
            i=sid, c=city, d=dev, cat=d.get("device_category"),
            sd=(d.get("short_description") or "")[:238],
            p=json.dumps(d.get("payload", {}), default=str))
        return ok({"staged_id": sid, "status": "staged", "ps_id": "PS3",
                   "note": "Incident STAGED only (no live post). Wire the ServiceNow "
                           "endpoint to submit."})
    if path == "/ps3/servicenow-staged":
        return ok(rows("SELECT id,device_id,device_category,short_description,status,created_at "
                       "FROM servicenow_staging WHERE city_id=:c "
                       "ORDER BY created_at DESC LIMIT 100", c=city))
    # ---- Cross-tab / pivot (added 2026-07-26) ----
    # Server-side pivot over the incident grain. Dimensions come from a fixed
    # whitelist -- the values are interpolated into SQL identifiers, so anything
    # outside the whitelist is rejected rather than escaped.
    if path == "/ps3/crosstab":
        DIMS = {"device_category": "p.mars_device_category", "severity": "p.pred_severity",
                "component": "p.pred_component", "facility": "p.facility_name",
                "actual_severity": "p.actual_severity", "actual_component": "p.actual_component",
                "device": "p.device_id", "serial": "p.matched_serial_nbr"}
        r_key = (params or {}).get("rows") or "device_category"
        c_key = (params or {}).get("cols") or "severity"
        if r_key not in DIMS or c_key not in DIMS:
            return err(400, f"rows/cols must be one of {sorted(DIMS)}")
        if r_key == c_key:
            return err(400, "rows and cols must differ")
        rexp, cexp = DIMS[r_key], DIMS[c_key]
        # 27-Jul-2026: optional category filter, so a pivot of severity against
        # actual_severity can be read one device type at a time. Without it the
        # TVM confusion matrix (32,842 rows) buries the GATE one (1,770).
        w = ("p.city_id=:c AND p.run_id=(SELECT run_id FROM v_ps3_latest_run "
             "WHERE city_id=:c)")
        kw = {"c": city}
        cat = (params or {}).get("device_category")
        if cat:
            w += " AND p.mars_device_category=:cat"; kw["cat"] = cat
        return ok(rows(
            f"SELECT {rexp} AS row_key, {cexp} AS col_key, COUNT(*) AS n, "
            " ROUND(AVG(p.pred_severity_conf)::numeric,4) AS avg_conf "
            f"FROM ps3_incident_predictions p WHERE {w} "
            f"GROUP BY {rexp}, {cexp} "
            "ORDER BY COUNT(*) DESC LIMIT 2000", **kw))
    if path == "/ps3/device-360":
        dev = (params or {}).get("device_id")
        if not dev:
            return err(400, "device_id required")
        d = rows("SELECT * FROM v_ps3_device_360 WHERE city_id=:c AND device_id=:dv",
                 c=city, dv=dev)
        serials = rows(
            "SELECT matched_serial_nbr,component_age_days,n_incidents,dominant_pred_component,"
            "pct_critical_pred,last_incident_dtm FROM v_ps3_serial_risk "
            "WHERE city_id=:c AND device_id=:dv ORDER BY n_incidents DESC", c=city, dv=dev)
        incidents = rows(
            "SELECT p.availability_event_id,p.ae_start_dtm,p.matched_serial_nbr,"
            "p.pred_severity,p.pred_severity_collapsed,p.pred_severity_conf,"
            "p.pred_component,p.pred_component_conf "
            "FROM ps3_incident_predictions p "
            "WHERE p.city_id=:c AND p.device_id=:dv "
            "AND p.run_id=(SELECT run_id FROM v_ps3_latest_run WHERE city_id=:c) "
            "ORDER BY p.ae_start_dtm DESC NULLS LAST LIMIT 50", c=city, dv=dev)
        return ok({"device": (d[0] if d else {}), "serials": serials, "incidents": incidents})
    return err(404, f"no route {method} {path}")

# ---------------------------------------------------------------------------
# PURGE (added 2026-07-26)
#
# Wipes the PS1 and/or PS3 serving tables for ONE city so a fresh notebook run
# lands on clean tables, with no residue from the backfills, the bridge smoke
# test, or the hand-seeded rows.
#
# Deliberately NOT part of migrate(). migrate() runs on every deploy; a DELETE
# living inside it would silently wipe production data every time someone
# shipped a schema change. This is a separate action that must be asked for by
# name, with a matching confirmation token.
#
# Four safety properties, each there for a reason:
#   1. CONFIRM TOKEN. The caller must send confirm="WIPE-<SCOPE>-<CITY>", e.g.
#      "WIPE-BOTH-CHI". A mistyped or absent token is a 400, not a wipe.
#   2. CITY-SCOPED. Every PS1/PS3 table carries city_id, so this is
#      DELETE ... WHERE city_id = :c, never TRUNCATE. Boston/LA/TOC rows in the
#      same tables are untouched.
#   3. EXISTENCE-CHECKED. to_regclass() is consulted first, so a table that this
#      deployment does not have is reported "absent" instead of aborting the
#      transaction and leaving a half-purged database.
#   4. TRANSACTIONAL. BEGIN / COMMIT with ROLLBACK on any error: either every
#      listed table is cleared or none is.
#
# dry_run=true (the default) counts what WOULD be deleted and changes nothing.
# The caller must pass dry_run=false explicitly to actually delete.
#
# Not touched unless explicitly requested:
#   servicenow_staging  - operator-created records, not model output
#                         (include_servicenow=true)
#   ml_batch_load_audit - the load audit trail; wiping it destroys the evidence
#                         of what the previous loads did (include_audit=true)
#   dim_station, cities, ps2_*, ps4_*, ps5_*  - never in scope here
# ---------------------------------------------------------------------------
PURGE_TABLES = {
    "ps1": [
        # serving / prediction grain
        "ps1_failure_predictions", "ps1_serial_predictions", "ps1_inference_runs",
        "ps1_explainability", "ps1_station_summary", "ps1_risk_trend", "ps1_risk_bands",
        # model metadata / scorecard
        "ps1_failure_summary", "ps1_leaderboard", "ps1_features",
        "ps1_model_performance", "ps1_feature_importance",
        "ps1_threshold_sweep", "ps1_calibration", "ps1_confusion",
        # 26-Jul-2026: orphan table. It exists in the deployed database, has no DDL
        # anywhere in this checkout, and is read by no route -- so the 26-Jul CHI
        # purge left it holding stale rows. Its schema is unknown here, so the delete
        # loop checks for city_id and SKIPS it rather than issuing an unscoped DELETE
        # that would cross tenants.
        "ps1_prediction_explainability",
    ],
    "ps3": [
        # two-head run (migration 15)
        "ps3_incident_predictions", "ps3_device_predictions", "ps3_serial_predictions",
        "ps3_head_summary", "ps3_head_leaderboard", "ps3_head_class_metrics",
        "ps3_head_feature_importance", "ps3_leakage_scan", "ps3_model_runs",
        # the earlier severity model's tables (migration 03/06) -- present on the
        # deployed package, absent from some checkouts, so existence-checked
        "ps3_severity_predictions", "ps3_severity_summary",
        "ps3_severity_drivers", "ps3_device_metrics",
        # 26-Jul-2026: same orphan situation as ps1_prediction_explainability.
        "ps3_prediction_explainability",
    ],
}
_OPTIONAL_TABLES = {"servicenow_staging": "include_servicenow",
                    "ml_batch_load_audit": "include_audit"}

# Only these prefixes may ever be deleted from. This is a belt-and-braces guard on
# top of the hardcoded PURGE_TABLES list: if someone later pastes a ps2_/ps4_/ps5_
# table into that list, the purge refuses to run at all rather than deleting it.
_ALLOWED_PREFIXES = ("ps1_", "ps3_")

def _assert_only_ps1_ps3(targets, opted_in):
    """Refuse the whole run if any target is outside PS1/PS3. Returns nothing; raises."""
    illegal = [t for t in targets
               if not t.startswith(_ALLOWED_PREFIXES) and t not in opted_in]
    if illegal:
        raise ValueError(
            f"REFUSING TO PURGE: {illegal} are outside the PS1/PS3 scope this action "
            f"is allowed to touch (prefixes {list(_ALLOWED_PREFIXES)}, plus the "
            f"explicitly opted-in {sorted(_OPTIONAL_TABLES)}). No statement was executed.")

# The witness scan runs an exact COUNT(*) per table, and this Lambda's timeout is
# 120s. Counting every table in the schema could exceed that on the larger PS2
# tables, so the set is PRIORITISED (the problem statements you actually care
# about first) and capped. Anything not counted is named in the response rather
# than silently dropped -- an unreported gap in a safety check is worse than a
# smaller check.
_WITNESS_PRIORITY = ("ps2_", "ps4_", "ps5_", "ps0_")
_WITNESS_MAX = 40

def _witness_tables(c, targets):
    """Non-target tables, PS2/PS4/PS5 first. Counted BEFORE and AFTER so the
    response can prove empirically that they were not touched, rather than asking
    the reader to trust the target list.
    Returns (counted, not_counted)."""
    try:
        rows_ = c.run(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")
        names = [r[0] for r in rows_ if r[0] not in set(targets)]
    except Exception:
        return [], []
    prio = [n for n in names if n.startswith(_WITNESS_PRIORITY)]
    rest = [n for n in names if not n.startswith(_WITNESS_PRIORITY)]
    # 26-Jul-2026 FIX. This was `(prio + rest)[:_WITNESS_MAX]`, so a schema with more
    # than _WITNESS_MAX ps2_ tables consumed every counted slot alphabetically and
    # pushed ps4_/ps5_ into not_counted -- exactly what the 26-Jul CHI purge did
    # (40 counted, all ps2_; PS4 and PS5 never actually measured). The point of the
    # priority tuple is that those tables are ALWAYS counted, so the cap now applies
    # only to the non-priority remainder.
    room = max(0, _WITNESS_MAX - len(prio))
    return prio + rest[:room], rest[room:]

def _count_all(c, tables, city):
    """Exact row counts. Uses the city filter where the table has city_id, whole-table
    otherwise, so a table without a city column is still watched."""
    out = {}
    for tb in tables:
        try:
            has_city = c.run("SELECT COUNT(*) FROM information_schema.columns "
                             "WHERE table_schema='public' AND table_name=:t "
                             "AND column_name='city_id'", t=tb)[0][0]
            if has_city:
                out[tb] = int(c.run(f"SELECT COUNT(*) FROM {tb} WHERE city_id=:c", c=city)[0][0])
            else:
                out[tb] = int(c.run(f"SELECT COUNT(*) FROM {tb}")[0][0])
        except Exception as e:
            out[tb] = f"count failed: {str(e)[:80]}"
    return out

def purge(evt):
    scope = str(evt.get("scope") or "both").lower().strip()
    city = str(evt.get("city") or CITY).upper().strip()
    dry = evt.get("dry_run", True)
    dry = not (dry is False or str(dry).lower() in ("false", "0", "no"))

    if scope not in ("ps1", "ps3", "both"):
        return err(400, "scope must be one of: ps1, ps3, both")
    expected = f"WIPE-{scope.upper()}-{city}"
    if not dry and str(evt.get("confirm") or "") != expected:
        return err(400, f"confirm token mismatch: send confirm=\"{expected}\" to actually delete. "
                        f"Nothing was changed.")

    targets = []
    if scope in ("ps1", "both"): targets += PURGE_TABLES["ps1"]
    if scope in ("ps3", "both"): targets += PURGE_TABLES["ps3"]
    opted_in = set()
    for tb, flag in _OPTIONAL_TABLES.items():
        if evt.get(flag) is True:
            targets.append(tb); opted_in.add(tb)

    # Hard scope guard. Runs before any statement, in dry run and in execution alike.
    try:
        _assert_only_ps1_ps3(targets, opted_in)
    except ValueError as e:
        return err(400, str(e))

    c = conn()
    present, absent, no_city = [], [], []
    for tb in targets:
        try:
            r = c.run("SELECT to_regclass(:t) IS NOT NULL", t=f"public.{tb}")
            if not (r and r[0][0]):
                absent.append(tb); continue
        except Exception:
            absent.append(tb); continue
        # Every DELETE here is `WHERE city_id=:c`. A target without that column
        # cannot be city-scoped, and an unscoped DELETE would take Boston / LA / TOC
        # rows too -- so it is SKIPPED and named in the response, never widened.
        try:
            has_city = int(c.run(
                "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name=:t AND column_name='city_id'", t=tb)[0][0])
        except Exception:
            has_city = 0
        (present if has_city else no_city).append(tb)

    before = {}
    for tb in present:
        try:
            before[tb] = int(c.run(f"SELECT COUNT(*) FROM {tb} WHERE city_id=:c", c=city)[0][0])
        except Exception as e:
            before[tb] = f"count failed: {str(e)[:120]}"

    # Everything else in the database. Counted now, counted again after, and
    # reported both times -- so "PS2/PS4/PS5 were not touched" is a measurement
    # in the response, not a claim in a comment.
    # NOTE: keyed on `present`, not `targets` -- a table skipped for having no
    # city_id is not deleted from, so it belongs in the witness set and gets proven
    # unchanged like any PS2/PS4/PS5 table.
    witnesses, witness_skipped = _witness_tables(c, present)
    witness_before = _count_all(c, witnesses, city)

    result = {"action": "purge", "scope": scope, "city": city, "dry_run": dry,
              "tables_to_delete": sorted(present),
              "tables_present": len(present), "tables_absent": absent,
              "tables_skipped_no_city_column": no_city,
              "rows_before": before,
              "scope_guard": {
                  "allowed_prefixes": list(_ALLOWED_PREFIXES),
                  "opted_in_exceptions": sorted(opted_in),
                  "verified": True,
              },
              "witness_tables_untouched": sorted(witnesses),
              "witness_not_counted": sorted(witness_skipped),
              "witness_rows_before": witness_before}

    if dry:
        total = sum(v for v in before.values() if isinstance(v, int))
        result["would_delete_rows"] = total
        result["note"] = (
            f"DRY RUN - nothing was changed. {len(present)} table(s) would be cleared, all "
            f"matching {list(_ALLOWED_PREFIXES)}. The {len(witnesses)} table(s) in "
            f"witness_tables_untouched are NOT in the delete list; their counts are shown "
            f"so you can compare them after the run. To execute, call again with "
            f"dry_run=false and confirm=\"{expected}\".")
        return ok(result)

    deleted, failed = {}, {}
    try:
        c.run("BEGIN")
        for tb in present:
            try:
                c.run(f"DELETE FROM {tb} WHERE city_id=:c", c=city)
                deleted[tb] = before.get(tb)
            except Exception as e:
                failed[tb] = str(e)[:200]
                raise
        c.run("COMMIT")
    except Exception as e:
        try: c.run("ROLLBACK")
        except Exception: pass
        result["status"] = "rolled_back"
        result["error"] = str(e)[:300]
        result["failed_on"] = failed
        result["note"] = "NOTHING was deleted - the whole purge ran in one transaction and was rolled back."
        return err(500, json.dumps(result, default=str))

    after = {}
    for tb in present:
        try:
            after[tb] = int(c.run(f"SELECT COUNT(*) FROM {tb} WHERE city_id=:c", c=city)[0][0])
        except Exception as e:
            after[tb] = f"count failed: {str(e)[:120]}"
    result["status"] = "committed"
    result["rows_deleted"] = deleted
    result["rows_after"] = after
    result["total_deleted"] = sum(v for v in deleted.values() if isinstance(v, int))
    leftover = {k: v for k, v in after.items() if isinstance(v, int) and v > 0}
    result["non_empty_after"] = leftover

    # Re-count every non-target table and diff. This is the proof: if a single row
    # moved in a PS2/PS4/PS5 table, it shows up here by name.
    witness_after = _count_all(c, witnesses, city)
    drift = {t: {"before": witness_before.get(t), "after": witness_after.get(t)}
             for t in witnesses if witness_before.get(t) != witness_after.get(t)}
    result["witness_rows_after"] = witness_after
    result["witness_drift"] = drift
    result["witness_verified_unchanged"] = (len(drift) == 0)
    result["note"] = (
        f"Purge committed: {result['total_deleted']:,} row(s) deleted from "
        f"{len(deleted)} PS1/PS3 table(s) for city {city}. "
        + (f"All {len(witnesses)} non-target table(s) verified unchanged."
           if not drift else
           f"WARNING: {len(drift)} non-target table(s) CHANGED - see witness_drift. "
           f"Nothing in this action deletes from them, so investigate concurrent writes.")
        + " Other cities in the purged tables were not touched. The dashboard will show "
          "empty states until the next run loads.")
    return ok(result)


# ---------------------------------------------------------------------------
# INSPECT (added 2026-07-26) -- READ ONLY. No DDL, no DML, nothing written.
#
# Added because the 26-Jul migrate reported:
#     sql/15 ... "column \"city_id\" does not exist"   (3 index failures)
#     sql/19 ... "column \"pred_severity\" does not exist"
#                "column p.city_id does not exist"
# CREATE TABLE IF NOT EXISTS is a silent no-op when a table of that name already
# exists with a DIFFERENT shape. So a table can be present, and every migration
# that "succeeded" against it can have done nothing at all. This reports the
# actual live columns so the next step is decided on evidence.
# ---------------------------------------------------------------------------
_EXPECTED = {
    "ps3_incident_predictions": ["city_id","run_id","availability_event_id","device_id",
        "mars_device_category","ae_start_dtm","matched_serial_nbr","component_age_days",
        "facility_id","facility_name","pred_severity","pred_severity_conf",
        "pred_severity_collapsed","actual_severity","pred_component","pred_component_conf",
        "actual_component","computed_date"],
    "ps3_device_predictions": ["city_id","run_id","device_id","mars_device_category",
        "n_incidents","pct_critical_pred","dominant_pred_severity","dominant_pred_component",
        "avg_component_age_days","last_incident_dtm","computed_date"],
    "ps3_serial_predictions": ["city_id","run_id","device_id","matched_serial_nbr",
        "mars_device_category","n_incidents","component_age_days","dominant_pred_component",
        "pct_critical_pred","last_incident_dtm","computed_date"],
    "ps3_head_summary": ["city_id","run_id","device_category","head","modeled","champion",
        "target_col","n_classes","test_f1_macro","macro_f1_floor","gate_pass"],
    "ps3_model_runs": ["city_id","run_id","run_kind","run_ts"],
}

def inspect(evt):
    c = conn()
    want = list(PURGE_TABLES["ps1"]) + list(PURGE_TABLES["ps3"])
    out = {}
    for tb in want:
        try:
            exists = c.run("SELECT to_regclass(:t) IS NOT NULL", t=f"public.{tb}")[0][0]
        except Exception as e:
            out[tb] = {"error": str(e)[:120]}; continue
        if not exists:
            out[tb] = {"exists": False}; continue
        cols = [r[0] for r in c.run(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position", t=tb)]
        rec = {"exists": True, "n_columns": len(cols), "columns": cols,
               "has_city_id": "city_id" in cols}
        try:
            rec["rows_total"] = int(c.run(f"SELECT COUNT(*) FROM {tb}")[0][0])
        except Exception as e:
            rec["rows_total"] = f"count failed: {str(e)[:60]}"
        exp = _EXPECTED.get(tb)
        if exp:
            missing = [x for x in exp if x not in cols]
            rec["missing_expected_columns"] = missing
            rec["schema_matches_migration_15"] = (len(missing) == 0)
        out[tb] = rec
    broken = sorted(t for t, r in out.items()
                    if r.get("exists") and (not r.get("has_city_id")
                                            or r.get("schema_matches_migration_15") is False))
    return ok({"action": "inspect", "read_only": True, "tables": out,
               "tables_with_wrong_schema": broken,
               "note": ("Tables listed in tables_with_wrong_schema exist but do NOT match the "
                        "schema the migrations declare -- CREATE TABLE IF NOT EXISTS skipped "
                        "them silently. They must be dropped and recreated before any load or "
                        "purge can work on them." if broken else
                        "Every table matches the expected schema.")})


# ---------------------------------------------------------------------------
# RECREATE (added 2026-07-26)
#
# Drops tables whose LIVE schema does not match what the migrations declare, so
# that the next migrate() can create them properly.
#
# Why this is needed at all: CREATE TABLE IF NOT EXISTS is a silent no-op when a
# table of that name already exists with a different shape. The 26-Jul inspect
# found ps3_incident_predictions carrying the superseded rds/04 bundle schema --
# 10 columns, no city_id -- while migration 15 declares 18 columns with city_id.
# Every migrate since has "succeeded" against it while doing nothing, which is
# why sql/15's three indexes and all four sql/19 statements failed.
#
# Safety, in the same spirit as purge():
#   * dry_run defaults TRUE and only reports.
#   * The caller must name each table explicitly in `tables`. There is no
#     "drop everything broken" mode -- a DROP has to be typed out.
#   * Every named table is re-checked against _EXPECTED at execution time and
#     REFUSED if its schema is actually fine. You cannot drop a healthy table.
#   * Prefix guard: ps1_/ps3_ only, same as purge().
#   * confirm must equal "RECREATE-<TABLE>" for each table named.
#   * Row counts are reported before dropping so nothing large goes silently.
# ---------------------------------------------------------------------------
def recreate(evt):
    names = evt.get("tables") or []
    if isinstance(names, str):
        names = [names]
    if not names:
        return err(400, 'tables required, e.g. {"action":"recreate","tables":["ps3_incident_predictions"]}')
    dry = evt.get("dry_run", True)
    dry = not (dry is False or str(dry).lower() in ("false", "0", "no"))

    bad = [t for t in names if not t.startswith(_ALLOWED_PREFIXES)]
    if bad:
        return err(400, f"REFUSING: {bad} are outside {list(_ALLOWED_PREFIXES)}. Nothing executed.")

    c = conn()
    plan, refused = [], {}
    for tb in names:
        try:
            exists = c.run("SELECT to_regclass(:t) IS NOT NULL", t="public." + tb)[0][0]
        except Exception as e:
            refused[tb] = f"existence check failed: {str(e)[:100]}"; continue
        if not exists:
            refused[tb] = "does not exist -- migrate() will create it on the next run"; continue
        cols = [r[0] for r in c.run(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=:t", t=tb)]
        exp = _EXPECTED.get(tb)
        missing = [x for x in exp if x not in cols] if exp else []
        healthy = bool(exp) and not missing
        if healthy:
            refused[tb] = ("schema already matches the migration -- REFUSED. This action only "
                           "drops tables that are genuinely the wrong shape.")
            continue
        if not exp:
            refused[tb] = "no expected-schema definition for this table -- refusing to drop blind"
            continue
        n = int(c.run("SELECT COUNT(*) FROM " + tb)[0][0])
        plan.append({"table": tb, "rows": n, "live_columns": len(cols),
                     "expected_columns": len(exp), "missing": missing})

    result = {"action": "recreate", "dry_run": dry, "planned_drops": plan, "refused": refused}
    if not plan:
        result["note"] = "Nothing to drop. See `refused` for why each named table was skipped."
        return ok(result)
    if dry:
        result["note"] = ("DRY RUN - nothing dropped. To execute, call again with dry_run=false and "
                          "confirm=\"RECREATE-<table>\" for the single table named. Then run "
                          "{\"action\":\"migrate\"} so the migration recreates it with the right schema.")
        return ok(result)

    if len(plan) != 1:
        return err(400, "execute one table at a time so the confirm token is unambiguous")
    tb = plan[0]["table"]
    if str(evt.get("confirm") or "") != f"RECREATE-{tb}":
        return err(400, f'confirm token mismatch: send confirm="RECREATE-{tb}". Nothing was dropped.')

    try:
        c.run("BEGIN")
        c.run(f"DROP TABLE {tb} CASCADE")
        c.run("COMMIT")
    except Exception as e:
        try: c.run("ROLLBACK")
        except Exception: pass
        result["status"] = "rolled_back"; result["error"] = str(e)[:300]
        return err(500, json.dumps(result, default=str))

    still = c.run("SELECT to_regclass(:t) IS NOT NULL", t="public." + tb)[0][0]
    result["status"] = "dropped"
    result["table_still_exists"] = bool(still)
    result["note"] = (f"{tb} dropped ({plan[0]['rows']} row(s) removed with it). "
                      "Run {\"action\":\"migrate\"} NOW so migration 15 recreates it with the "
                      "correct schema and its indexes, then sql/19 will apply.")
    return ok(result)


def lambda_handler(event, context):
    if isinstance(event, dict) and event.get("action") == "migrate":
        return migrate(event)
    if isinstance(event, dict) and event.get("action") == "purge":
        return purge(event)
    if isinstance(event, dict) and event.get("action") == "inspect":
        return inspect(event)
    if isinstance(event, dict) and event.get("action") == "recreate":
        return recreate(event)
    if isinstance(event, dict) and event.get("action") == "load_run":
        return load_run(event)
    rc = (event or {}).get("requestContext", {}).get("http", {})
    method = rc.get("method", "GET"); path = event.get("rawPath", "/")
    params = event.get("queryStringParameters") or {}
    body = {}
    if event.get("body"):
        try: body = json.loads(event["body"])
        except Exception: body = {}
    try:
        return route(method, path, params, body)
    except Exception as e:
        return err(500, str(e)[:300])
