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
# ---------------------------------------------------------------------------
# apply_sql  (added 2026-08-10)
#
# Apply ONE named file from sql/, and nothing else.
#
# WHY THIS EXISTS. migrate() applies a hardcoded list of sql/01 - sql/25 on
# every deploy. The repo carries 46 files. Everything from 26 upward -- which
# includes sql/34 (the PS1 cross-wired tables and views), sql/35 (the label
# onset views), sql/36 and sql/37 (v_ps1_predictions_xw, which /ps1/predictions
# reads) -- is NOT in that list. There has been no way to apply a NEW migration
# without also re-running 25 old ones, and two of those write data:
#
#   sql/08_ps2_run_backfill.sql       DELETEs and re-INSERTs hardcoded PS2 rows
#                                     for computed_date 2026-07-14  (retired 27-Sep-2026)
#   sql/18_purge_ps3_bridge_test_rows DELETEs ps3_severity_predictions rows
#
# Adding a PS1 index should not put PS2 data at risk. This action makes the unit
# of application one reviewed file.
#
# WHAT IT WILL NOT DO. It takes a FILE NAME, never SQL. The name must match the
# sql/ convention -- digits, underscore, word characters, .sql -- so there is no
# path traversal, no glob, and nothing from the event body reaches the database.
# The only thing it can run is a file that was reviewed and packaged into the
# deployment artifact.
#
#   aws lambda invoke ... --payload '{"action":"apply_sql","file":"49_ps1_xw_unique_index.sql","dry_run":true}'
#   aws lambda invoke ... --payload '{"action":"apply_sql","file":"49_ps1_xw_unique_index.sql"}'
#
# dry_run returns the statements that WOULD run, split exactly as execution
# would split them, and touches nothing.
#
# Per-statement behaviour, error tolerance and dead-socket handling are migrate's
# -- it reuses split_sql and _migrate_run rather than reimplementing them, so the
# two cannot drift apart.
# ---------------------------------------------------------------------------
def apply_sql(evt):
    global _CONN_TIMEOUT, _conn
    evt = evt or {}
    name = str(evt.get("file") or "").strip()
    if not re.fullmatch(r"[0-9]{2,3}_[A-Za-z0-9_]+\.sql", name):
        return {"statusCode": 400, "body": json.dumps({
            "error": "file must be a bare sql/ filename such as "
                     "49_ps1_xw_unique_index.sql -- no paths, no SQL",
            "got": name[:120]})}
    here = os.path.dirname(__file__)
    path = os.path.join(here, "sql", name)
    if not os.path.exists(path):
        return {"statusCode": 404, "body": json.dumps({
            "error": "not packaged in this deployment", "file": name,
            "available": sorted(os.listdir(os.path.join(here, "sql")))[:60]})}

    text  = open(path).read()
    stmts = split_sql(text)
    head  = [s.strip().splitlines()[0][:100] if s.strip() else "" for s in stmts]

    if evt.get("dry_run"):
        return {"statusCode": 200, "body": json.dumps({
            "apply_sql": {"file": name, "dry_run": True,
                          "statements": len(stmts), "first_lines": head}}, default=str)}

    # DDL needs the longer socket timeout, and a FRESH connection so the new
    # value actually takes effect -- a cached socket keeps the timeout it was
    # built with. Restored before returning. Same reasoning as migrate().
    _prev = _CONN_TIMEOUT
    _CONN_TIMEOUT = 180
    try: _conn.close()
    except Exception: pass
    _conn = None
    conn()

    applied = tolerated = failed = 0
    errs, reconnects = [], 0
    for st in stmts:
        _e = _migrate_run(st)
        if _e is None:
            applied += 1
        else:
            msg = str(_e).lower()
            if any(t in msg for t in _OK):
                tolerated += 1
            else:
                failed += 1
                if any(t in msg for t in _DEAD):
                    reconnects += 1
                if len(errs) < 8:
                    errs.append(str(_e)[:300])
    _CONN_TIMEOUT = _prev
    out = {"file": name, "statements": len(stmts), "applied": applied,
           "tolerated": tolerated, "failed": failed, "errors": errs,
           "first_lines": head}
    if reconnects:
        out["dead_socket_after_retry"] = reconnects
    return {"statusCode": 200 if failed == 0 else 500,
            "body": json.dumps({"apply_sql": out}, default=str)}


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
    # 27-Sep-2026: sql/02 and sql/08 (hand-seeded PS2 rows for 11/14-Jul, re-inserted on EVERY deploy) retired
    # to _retired/ps2_seed_backfills; sql/10 never existed in the repo. The PS2 loader owns all ps2_* data.
    for fn in ("sql/01_schema_core.sql",
               "sql/03_phase1b_ps3_severity.sql", "sql/04_phase1c_ps1_failure.sql",
               "sql/06_phase1d_ps3_device.sql",   # sql/05, sql/07: retired 27-Sep-2026 (only created + seeded retired PS2 tables)
               "sql/09_phase1f_ps2_rich.sql",
               "sql/11_phase1g_ps1_serving.sql", "sql/12_ps1_serving_backfill.sql",
               "sql/13_phase2_device360.sql", "sql/14_phase3_dim_station.sql",
               "sql/15_phase2a_ps3_two_head.sql",
               "sql/16_phase2b_ps1_batch_lineage.sql",
               "sql/17_phase2c_ps3_two_head_views.sql",
               "sql/18_purge_ps3_bridge_test_rows.sql",
               "sql/19_ps3_severity_collapse_fix.sql",
               # sql/20_ps4_anomaly.sql retired 26-Sep-2026 (sql/70 drops its objects)
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
               # sql/25_ps4_scored.sql retired 26-Sep-2026 (sql/70 drops its objects)
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
               # sql/33_ps4_device_daily.sql retired 26-Sep-2026 (sql/70 drops its objects)
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
               "sql/44_ps2_v25.sql",
               # 03-Aug-2026. The 20 tables published by PS3 V26, built
               # from that run's own schema dump rather than a fixture.
               # Purely additive and every name is prefixed ps3_v25_, so
               # ps3_incident_predictions / ps3_device_predictions /
               # ps3_v2_* -- what the deployed PS3 screens read today --
               # are untouched and remain the plan-B set.
               "sql/45_ps3_v25.sql",
               # 03-Aug-2026. PS2 v2.5.4 union-minute columns. ADD COLUMN
               # IF NOT EXISTS on two existing tables; no DROP, no ALTER
               # TYPE, and hardware_oos_minutes is left exactly as it was
               # so the published component-burden measure still resolves.
               "sql/46_ps2_v254_union_minutes.sql",
               "sql/47_ps3_latest_run_widen.sql",
               # 04-Aug-2026. MUST run AFTER sql/36 -- it CREATE OR REPLACEs
               # v_ps1_xw_device_state, which sql/36 creates, so that the state
               # is read on the last EVALUABLE day rather than the last scored
               # one. Appended at the end, which gives that ordering for free.
               #
               # This list is a TUPLE, not a glob. A new sql/NN file that is not
               # named here is silently skipped and the deploy still reports
               # success, which is how migration 47 nearly shipped as a no-op.
               "sql/48_ps1_state_evaluable_day.sql"):
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
               "note": "All three fleets load. The cross-wired export writes one object per fleet "
                       "at chicago/device_ps1_cross_wired_daily/{gate|tvm|validator} -- the fleet is "
                       "the object key, not a directory or a write partition, so listing the prefix "
                       "shows three keys and no partition= segment. Verified live 09-Aug-2026: GATE, "
                       "TVM and VALIDATOR all return rows. An earlier version of this note claimed "
                       "VALIDATOR overwrote the other two; that collision was real once and is fixed. "
                       "ps1_explainability stays empty on purpose: the run emits fleet-average SHAP "
                       "broadcast to every row, not per-row contributions."})

def rows(sql, **kw):
    c = conn(); res = c.run(sql, **kw); cols = [d["name"] for d in c.columns]
    return [dict(zip(cols, r)) for r in res]


# ===========================================================================
# WRITE GUARD  (added 2026-08-10)
#
# WHAT THIS API ACTUALLY EXPOSES. Both HTTP API Gateways in this account carry
# AuthorizationType NONE on their $default route. Of the 117 routes here, three
# are not read-only, and all three are reachable by anyone who has the URL:
#
#   POST  /ps1/servicenow-stage    INSERT INTO servicenow_staging
#   POST  /ps3/servicenow-stage    INSERT INTO servicenow_staging
#   PATCH /ps4/alerts/{id}         UPDATE ps4_anomaly_alerts SET status=...
#
# The reads are a data-disclosure question. These three are different in kind:
#
#   * servicenow_staging is a QUEUE THAT BECOMES REAL WORK ORDERS. Its own
#     response says "Wire Robin's ServiceNow endpoint to submit." An open write
#     path into it means anonymous callers can author incidents attributed to
#     any device_id, and the day that queue is wired to live ServiceNow those
#     become real tickets dispatched to real technicians.
#   * payload_json is TEXT with no cap. One caller in a loop is an unbounded
#     write into Aurora -- a disk-fill denial of service that costs the
#     attacker nothing.
#   * PATCH /ps4/alerts/{id} lets an anonymous caller mark any anomaly alert
#     'resolved'. Silencing an alert is worse than reading one.
#
# WHAT THIS GUARD IS AND IS NOT. It is defence in depth, NOT the fix. The fix
# is authorization at the gateway -- a JWT authorizer, IAM auth, or a WAF --
# and that is an architecture decision plus an AWS change, not a code change.
# This bounds the blast radius in the meantime, and it is deliberately built so
# that turning it on cannot break the running dashboard:
#
#   MUTATION_TOKEN unset  -> tokens NOT required. Behaviour unchanged. Caps and
#                            quotas still apply. This is today's state.
#   MUTATION_TOKEN set    -> mutating routes additionally require a matching
#                            x-cubic-token header. Reads are never affected.
#
# A token shipped inside a public React bundle is not a secret, so this is a
# speed bump against casual and automated abuse, not a defence against a
# determined attacker who has read the JavaScript. Said plainly here so nobody
# reads this block and concludes the API is secured.
# ===========================================================================
MUTATION_TOKEN     = os.environ.get("MUTATION_TOKEN", "").strip()
MAX_PAYLOAD_BYTES  = int(os.environ.get("MAX_PAYLOAD_BYTES", "16384"))
STAGE_QUOTA_PER_HR = int(os.environ.get("STAGE_QUOTA_PER_HR", "200"))
_DEVICE_ID_RE      = re.compile(r"^[A-Za-z0-9_.:-]{1,30}$")


def _hdr(evt_headers, name):
    """HTTP header lookup, case-insensitively -- API Gateway lowercases, curl may not."""
    if not evt_headers:
        return ""
    low = {str(k).lower(): v for k, v in evt_headers.items()}
    return str(low.get(name.lower(), "") or "")


def write_guard(headers, *, payload=None, device_id=None, city=None, quota_check=False):
    """Returns None when the write may proceed, else a ready-to-return error."""
    if MUTATION_TOKEN:
        supplied = _hdr(headers, "x-cubic-token")
        # Constant-time compare: a length-or-prefix leak here would let a caller
        # discover the token a character at a time.
        import hmac
        if not supplied or not hmac.compare_digest(supplied, MUTATION_TOKEN):
            return err(401, "this endpoint requires a valid x-cubic-token header")

    if device_id is not None and not _DEVICE_ID_RE.match(str(device_id)):
        return err(400, "device_id must be 1-30 chars of letters, digits, _ . : or -")

    if payload is not None:
        try:
            size = len(json.dumps(payload, default=str).encode("utf-8"))
        except Exception:
            return err(400, "payload is not JSON-serialisable")
        if size > MAX_PAYLOAD_BYTES:
            return err(413, f"payload is {size} bytes; the limit is {MAX_PAYLOAD_BYTES}. "
                            f"servicenow_staging.payload_json is unbounded TEXT and an "
                            f"unauthenticated caller must not be able to fill it")

    if quota_check:
        try:
            n = conn().run("SELECT COUNT(*) FROM servicenow_staging "
                           "WHERE city_id = :c AND created_at > NOW() - INTERVAL '1 hour'",
                           c=city)[0][0]
            if int(n) >= STAGE_QUOTA_PER_HR:
                return err(429, f"{n} incidents already staged for this city in the last hour; "
                                f"the ceiling is {STAGE_QUOTA_PER_HR}. Nothing was written. "
                                f"Raise STAGE_QUOTA_PER_HR if this is legitimate volume")
        except Exception as e:
            # Never fail a legitimate write because the quota probe broke.
            print(f"[warn] staging quota check failed, allowing the write: {type(e).__name__}: {e}")
    return None

# PS1 fleet naming, 2026-08-10. The 13-Jul seed tables (ps1_failure_summary,
# ps1_leaderboard) spell the fleets as display names -- 'TVM', 'Gates'. Every
# table written by a notebook since -- ps1_model_performance, ps1_confusion,
# ps1_feature_importance, ps1_cross_wired_daily -- spells them as codes:
# 'TVM', 'GATE', 'VALIDATOR'. Nothing reconciled the two, which is why the
# coverage note in PS1FailurePredictionTab.jsx resorts to matching on the
# first three characters. One map, used by every route that has to cross that
# boundary, so the reconciliation is stated once instead of guessed at N times.
_PS1_DISPLAY = {"GATE": "Gates", "TVM": "TVM", "VALIDATOR": "Validator"}
_PS1_CATEGORY = {"GATES": "GATE", "GATE": "GATE", "TVM": "TVM",
                 "VALIDATOR": "VALIDATOR", "VALIDATORS": "VALIDATOR"}

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
    # PARALLEL PATH HOOK. When a request is running under
    # _device_360_parallel, results prefetched concurrently are served from
    # here. A MISS IS NOT AN ERROR -- it falls straight through to the
    # sequential query below, which is exactly today's behaviour.
    _st = getattr(_D360_TL, "state", None)
    if _st is not None:
        _k = (sql, tuple(sorted(kw.items())))
        if _k in _st["cache"]:
            return _st["cache"].pop(_k)
        _st["seen"].append((sql, dict(kw)))

    try:
        return rows(sql, **kw)
    except Exception as e:
        print("device_360 sub-query failed, section skipped: %s", str(e)[:200])
        return []


# The concordance a survival model has to clear before it may drive a work
# order. Same figure the front end holds (V4PS5Overview CINDEX_FLOOR); the
# blockers text below is where the API states it, so the two must agree.
_PS5_CINDEX_FLOOR = 0.65


def _ps5_registry_rows(city):
    """Category-level PS5 registry state, DERIVED from ps5_cindex_leaderboard.

    Until 25-Aug-2026 this data came from ps5_reliability_status -- three rows
    hand-seeded by sql/02 on 11-Jul with v1-era C-indexes (gates 0.5906, tvms
    0.5071 'broken_champion_selection', validators 0.5970). No loader ever
    refreshed them, so every deploy's migrate() replay re-asserted numbers the
    v5.6 run had long since beaten and the dashboard understated our own
    models. The leaderboard IS refreshed (daily 07:20 UTC by the PS5 RDS
    loader), so the champion row per fleet -- best oot_cindex -- cannot go
    stale the same way.

    The shape is byte-compatible with the old table so no front-end file
    changes: device_type keeps the lowercase-plural naming this feed always
    used, and dashboard_ready stays FALSE for every fleet. That last part is
    deliberate, not an oversight -- no fleet is signed off in the model
    registry, and this feed is what lets the overview card say the model is a
    prioritisation aid, not a scheduler. Do not flip it here; sign-off is a
    registry event, not a query result.

    Degrades to [] on a missing or empty leaderboard (via _safe_rows) -- the
    old rows() call 500'd the whole response when its table was absent, which
    matters now that a manual DROP TABLE ps5_reliability_status is pending.
    """
    best = _safe_rows(
        "SELECT DISTINCT ON (device_type) device_type, oot_cindex "
        "FROM ps5_cindex_leaderboard WHERE city_id=:c AND oot_cindex IS NOT NULL "
        "ORDER BY device_type, oot_cindex DESC", c=city)
    out = []
    for r in best:
        ci = float(r["oot_cindex"])
        gap = _PS5_CINDEX_FLOOR - ci
        out.append({
            "device_type": _PS5_TYPE.get(str(r["device_type"]).upper(),
                                         str(r["device_type"]).lower()),
            "concordance_index": ci,
            "registry_status": "v5_6_champion",
            "dashboard_ready": False,
            "blockers": ("misses the %.2f C-index floor by %.3f"
                         % (_PS5_CINDEX_FLOOR, gap)) if gap > 0 else "",
        })
    return out


# =====================================================================
# PARALLEL DEVICE-360  (opt-in, reversible)                 06-Aug-2026
#
# THE MEASUREMENT THAT MOTIVATED THIS
#   /ps1/device-360   1 concurrent  5.5s
#                     2 concurrent 10.4s
#                     4 concurrent 21.0s
#   RDS during 6-way load: CPU 57%, ReadLatency 0.0, connections 7->14.
#
# So: the database was NOT saturated, NOT doing disk I/O, and NOT short of
# connections. _device_360 simply issues 25 queries ONE AT A TIME on a single
# cached connection -- 5.5s / 25 = ~220ms each. The wall clock is the sum of a
# queue, not the cost of the work.
#
# HOW THIS WORKS -- _device_360 IS NOT MODIFIED.
#   Rewriting 572 lines of interleaved section-assembly to hoist its queries
#   would be a large diff over code whose failure modes are already documented
#   in comments. Instead this adds a prefetch cache in front of _safe_rows:
#
#   1. LEARN. The first call in a container runs _device_360 exactly as it runs
#      today, recording every (sql, params) it issues. Cost: one normal request.
#   2. PLAN. Keep only the queries whose parameters were EXACTLY {c: city} or
#      {c: city, d: device} -- i.e. those that depend on nothing the function
#      computes mid-flight. 19 of the 23 qualify. The four that bind `pid` or
#      `cat` (drivers, feature-importance, perf, coverage) are excluded and
#      stay sequential, because their values are not known until `p` returns.
#      NOTE the trap this avoids: one query binds d=cat, not d=dev. Matching on
#      parameter NAMES alone would replay it with the wrong value. The plan
#      matches on VALUES, so that query is correctly left out.
#   3. REPLAY. Later calls fire the planned queries concurrently across a small
#      connection pool, then run _device_360 unchanged -- which now finds most
#      of its results already in the cache and returns them without a round trip.
#
# WHY IT IS SAFE
#   - A cache MISS is not an error: _safe_rows falls through to the normal
#     sequential path. Worst case is today's behaviour.
#   - A prefetch query that raises is simply not cached -- same fallthrough.
#   - The pool is separate from conn(); the sequential path is untouched.
#   - Off by default. Enable per-request with ?parallel=1, or per-environment
#     with D360_PARALLEL=1. Revert = unset the variable. No redeploy.
# =====================================================================
import threading, queue as _queue
from concurrent.futures import ThreadPoolExecutor

D360_PARALLEL_DEFAULT = os.environ.get("D360_PARALLEL", "0") == "1"
D360_POOL_SIZE        = int(os.environ.get("D360_POOL_SIZE", "6"))

_D360_PLAN  = None                 # [("cd"|"c", sql)] learned once per container
_D360_LOCK  = threading.Lock()
_D360_POOL  = None
_D360_TL    = threading.local()    # per-request {cache, seen}


def _d360_pool():
    """A small pool, built once per container. Separate from conn() on purpose:
    the sequential path keeps its own connection and its own reconnect logic."""
    global _D360_POOL
    with _D360_LOCK:
        if _D360_POOL is None:
            q = _queue.Queue()
            c = _creds()
            for _ in range(D360_POOL_SIZE):
                q.put(pg8000.native.Connection(
                    user=c["user"], password=c["password"], host=c["host"],
                    port=c["port"], database=c["database"], ssl_context=True,
                    timeout=_CONN_TIMEOUT))
            _D360_POOL = q
    return _D360_POOL


def _pooled_rows(sql, **kw):
    """rows() against a pooled connection. A dead socket is replaced rather than
    returned to the pool -- the same lesson conn() learned on 28-Jul."""
    pool = _d360_pool()
    cn = pool.get()
    try:
        res = cn.run(sql, **kw)
        cols = [d["name"] for d in cn.columns]
        out = [dict(zip(cols, r)) for r in res]
        pool.put(cn)
        return out
    except Exception:
        try: cn.close()
        except Exception: pass
        c = _creds()
        try:
            pool.put(pg8000.native.Connection(
                user=c["user"], password=c["password"], host=c["host"],
                port=c["port"], database=c["database"], ssl_context=True,
                timeout=_CONN_TIMEOUT))
        except Exception:
            pool.put(None)          # keep the pool's size honest
        raise


def _d360_key(sql, kw):
    return (sql, tuple(sorted(kw.items())))


def _device_360_parallel(city, dev):
    global _D360_PLAN

    # ---- 1. LEARN (first call in this container) --------------------
    if _D360_PLAN is None:
        _D360_TL.state = {"cache": {}, "seen": []}
        try:
            out = _device_360(city, dev)
            seen = _D360_TL.state["seen"]
        finally:
            _D360_TL.state = None
        plan = []
        for sql, kw in seen:
            if kw == {"c": city, "d": dev}:  plan.append(("cd", sql))
            elif kw == {"c": city}:          plan.append(("c", sql))
        with _D360_LOCK:
            _D360_PLAN = plan
        print(f"[d360] plan learned: {len(plan)} of {len(seen)} queries are "
              f"prefetchable", flush=True)
        return out

    # ---- 2. REPLAY --------------------------------------------------
    work = [(sql, ({"c": city, "d": dev} if shape == "cd" else {"c": city}))
            for shape, sql in _D360_PLAN]
    cache = {}
    try:
        with ThreadPoolExecutor(max_workers=D360_POOL_SIZE) as ex:
            futs = {ex.submit(_pooled_rows, sql, **kw): (sql, kw) for sql, kw in work}
            for f, (sql, kw) in futs.items():
                try:
                    cache[_d360_key(sql, kw)] = f.result()
                except Exception:
                    pass            # miss -> _safe_rows does it sequentially
    except Exception as e:
        print(f"[d360] prefetch unavailable ({type(e).__name__}); sequential", flush=True)
        cache = {}

    _D360_TL.state = {"cache": cache, "seen": []}
    try:
        return _device_360(city, dev)
    finally:
        _D360_TL.state = None


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
        # THESE TWO QUERIES NAMED COLUMNS THAT DO NOT EXIST.      23-Sep-2026
        # ps2_markov_transitions is (from_sub, to_sub, prob) -- sql/09:48-53 --
        # with no transition_prob and no n_events at all. ps2_leadlag_timing is
        # (sub_a, sub_b, n_events, mean, median, p25, p75) -- sql/27:44-54 --
        # with no from_sub, no to_sub and no median_minutes. Both statements
        # raised 42703 every time, and _safe_rows turns that into [], so the
        # fleet fallback -- the branch that runs when a device has no anchor
        # subsystem -- has been rendering empty rather than erroring. A 200
        # response does not prove this fixed; the arrays have to be non-empty.
        # Also adds the latest-vintage predicate the anchored branch uses, so
        # the fallback cannot serve an older run than the panel above it.
        _fleet = _safe_rows(
            "SELECT from_sub, to_sub, prob AS confidence "
            "FROM ps2_markov_transitions WHERE city_id=:c "
            "AND computed_date=(SELECT MAX(computed_date) FROM ps2_markov_transitions WHERE city_id=:c) "
            "ORDER BY prob DESC NULLS LAST LIMIT 6", c=city)
        _ftime = _safe_rows(
            "SELECT sub_a AS from_sub, sub_b AS to_sub, median, n_events "
            "FROM ps2_leadlag_timing WHERE city_id=:c "
            "AND computed_date=(SELECT MAX(computed_date) FROM ps2_leadlag_timing WHERE city_id=:c) "
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
    # ---- PS4 : actionable weeks of the current v3 run (last 12 weeks) ----
    # 27-Sep-2026: was ps4_anomaly_alerts, which nothing ever wrote (always 0) and sql/70 drops.
    al = _safe_rows("SELECT week_start, week_end, severity, anomaly_types, anomaly_score_max"
                    " FROM v_ps4_weekly_device WHERE city_id=:c AND device_id=:d AND is_actionable_week=1"
                    " AND week_start >= CURRENT_DATE - 84 ORDER BY week_start DESC", c=city, d=dev)
    out["ps4"] = {"level": "device", "alert_count": len(al), "alerts": al,
                  "note": "Actionable weeks in the last 12 (PS4 v3, unsupervised: unlike its peers, not a failure probability)."}
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
    # 03-Aug-2026. THE DEVICE-LEVEL LOOKUP WAS POINTED AT A TABLE THAT IS NOT
    # THE PS5 RUN. ps5_reliability_estimates is the first-generation estimate
    # table -- the same one whose device_type column never applied in Aurora,
    # documented above. The survival run publishes to v_ps5_device_rul (sql/29)
    # and v_ps5_serial_rul (sql/30), which is what /ps5/device-rul serves and
    # what the dashboard's PS5 screen shows.
    #
    # The consequence was not subtle: BMV02633 is rank 1 of 3,235 validators,
    # CRITICAL, act_now, 0.9 days of remaining life -- and its own 360 page said
    # "No device-level RUL row for this device". The most urgent device in the
    # fleet read as unknown on the one screen an engineer opens about it.
    #
    # The live view is tried FIRST and the legacy table is kept as a fallback,
    # so a device that only exists in the old estimates still resolves.
    d5 = _safe_rows(
        "SELECT feature_asof_date AS as_of_date, device_type, facility_id,"
        " risk_band, is_overdue, rul_standard_days, predicted_median_survival_days,"
        " hazard_score, current_healthy_age_days, days_since_hw_oos, roll_fail_30d,"
        " n_prior_oos, rul_rank_in_type, n_devices_in_type, act_now,"
        " p_oos_7d, p_oos_1d, act_now_horizon_days, act_now_threshold "
        "FROM v_ps5_device_rul WHERE city_id=:c AND device_id=:d "
        "ORDER BY feature_asof_date DESC LIMIT 1", c=city, d=dev)
    _ps5_src = "v_ps5_device_rul"
    # 26-Sep-2026: the fallback to ps5_reliability_estimates is gone. That table holds v1-era estimates
    # nothing refreshes; a device the current run did not score now reads as unscored, not as a stale v1 number.
    # Serial grain, deduplicated. v_ps5_serial_dupes measures up to 25 identical
    # rows per (device, serial) on validators, every measured column constant
    # inside the repeat -- a roster fan-out. DISTINCT can only drop rows equal
    # on every selected column, so it cannot lose a reading.
    _s5 = _safe_rows(
        "SELECT DISTINCT component_serial_nbr, component_type_name, component_age_days,"
        " risk_tier, risk_score, expected_component_rul_days, is_overdue, act_now,"
        " has_serial, serial_source "
        "FROM v_ps5_serial_rul WHERE city_id=:c AND device_id=:d "
        "ORDER BY act_now DESC, expected_component_rul_days ASC NULLS LAST LIMIT 20",
        c=city, d=dev)
    ps5 = {"level": "device" if d5 else "category", "category": cat}
    if _s5:
        ps5["components"] = _s5
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
        if _ps5_src == "v_ps5_device_rul":
            # Rank is WITHIN device type. The three survival models are fitted
            # separately on separate populations with separate baseline
            # hazards, so a 3-day TVM and a 3-day validator are not the same
            # claim and the note says so where someone will actually read it.
            ps5["source"] = _ps5_src
            ps5["note"] = (
                "Device-level remaining life from the survival run (v_ps5_device_rul). "
                "Rank %s of %s is WITHIN this device type -- the three fleets are "
                "modelled separately, so day counts are not comparable across them. "
                "No fleet is signed off in the model registry yet, so treat this as "
                "prioritisation rather than a schedule."
                % (d5[0].get("rul_rank_in_type"), d5[0].get("n_devices_in_type")))
    else:
        ps5["found"] = False
    if cat:
        p5 = _PS5_TYPE.get(str(cat).upper(), str(cat).lower())
        # 25-Aug-2026 -- same repoint as /ps5/status: derived from the daily
        # leaderboard, not the stale hand-seeded ps5_reliability_status rows.
        _allrel = _ps5_registry_rows(city)
        rel = [r for r in _allrel if str(r.get("device_type")).lower() == p5]
        ps5["reliability"] = rel[0] if rel else None
        if not d5:
            ps5["note"] = ("No remaining-life row for this device in either the survival "
                           "run or the legacy estimates; showing category reliability "
                           "status only.")
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
    # DISTINCT, and critical_weighted pulled into the select list because
    # SELECT DISTINCT requires every ORDER BY expression to appear there.
    # Without it this block repeated the same (component, serial) pair -- on
    # TVM08212 the same BHU/fc6850 row came back several times, which reads on
    # screen as several separate findings about the same part.
    ps3v2["rootcause"] = _q(
        "SELECT DISTINCT component_label, component_label_semantics, serial_number,"
        " incident_count, critical_rate, recurrence_30d, latest_incident_at,"
        " taxonomy_note, critical_weighted FROM v_ps3_v2_rootcause"
        " WHERE city_id=:c AND device_id=:d"
        " ORDER BY critical_weighted DESC NULLS LAST LIMIT 20", c=city, d=dev)
    # Belt and braces: if two rows differ only in a column the view computes,
    # DISTINCT keeps both. The screen shows one line per part.
    _seen, _rc = set(), []
    for _r in ps3v2["rootcause"]:
        _k = (_r.get("component_label"), _r.get("serial_number"))
        if _k in _seen:
            continue
        _seen.add(_k); _rc.append(_r)
    ps3v2["rootcause"] = _rc[:10]
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

    # ---- Conformed identity + ServiceNow history (sql/56, added 2026-08-27) --
    # dim_device_incident_cmdb is the loader-owned device -> incident -> CMDB CI
    # spine. Read once here: the outbound ServiceNow payload needs the REAL
    # cmdb_ci sys_id, serial and facility (it used to send the device_id string
    # with u_serial/u_facility null -- the error-604 class), and the ticket
    # history is evidence the per-PS sections cannot see: their "incidents" are
    # availability-feed OOS episodes, not ServiceNow tickets.
    _idr = _safe_rows(
        "SELECT device_name, mars_device_category, device_type_name, facility_id,"
        " facility_name, operator_name, serial_number, component_serial_nbr,"
        " component_type, cmdb_ci_sys_id, incident_number, incident_sys_id,"
        " opened_at, closed_at, incident_count, as_of_date"
        " FROM v_device_central WHERE city_id=:c AND device_id=:d LIMIT 1", c=city, d=dev)
    out["identity"] = _idr[0] if _idr else None
    out["servicenow_history"] = ({
        "incident_count": _idr[0].get("incident_count"),
        "latest_incident": _idr[0].get("incident_number"),
        "latest_opened_at": _idr[0].get("opened_at"),
        "latest_closed_at": _idr[0].get("closed_at"),
        "cmdb_ci_sys_id": _idr[0].get("cmdb_ci_sys_id"),
        "note": "ServiceNow tickets linked via CMDB CI / task_ci -- distinct from "
                "the availability-feed OOS counts in the PS sections.",
    } if _idr else None)

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
    # 2026-08-27. Identity now comes from dim_device_incident_cmdb (sql/56):
    # cmdb_ci used to be the device_id STRING, which ServiceNow cannot match to
    # a CI (the error-604 class), and u_serial/u_facility were null because PS2
    # only carries them for its top-200. The dim covers the whole fleet; the
    # old values remain as fallbacks so a missing dim row degrades, not breaks.
    ident = o.get("identity") or {}
    hist = o.get("servicenow_history") or {}
    prob = ps1.get("failure_probability"); band = ps1.get("risk_band", "")
    urg = {"Critical": "1", "High": "2", "Medium": "3", "Low": "3"}.get(band, "3")
    notes = "MARS PS1 predictive signal (quality gate %s). Risk band %s. Cross-PS: PS2 cascade_days=%s in_top=%s, PS4 alerts=%s." % (
        ps1.get("quality_gate") or "unknown",
        band, ps2.get("cascade_days"), ps2.get("in_top_devices"), o.get("ps4", {}).get("alert_count"))
    if hist.get("incident_count"):
        notes += " ServiceNow history: %s ticket(s), latest %s." % (
            hist.get("incident_count"), hist.get("latest_incident") or "n/a")
    return {
        "short_description": "Scheduled maintenance - %s (%s) PS1 3-day failure risk %s%%" % (dev, cat or "device", round((prob or 0)*100, 1)),
        "cmdb_ci": ident.get("cmdb_ci_sys_id") or dev,
        "u_device_category": cat,
        "u_facility": ident.get("facility_name") or ps2.get("facility"),
        "u_serial": ident.get("serial_number") or ps2.get("serial"),
        "urgency": urg, "impact": urg, "category": "Hardware", "subcategory": "Predictive Maintenance",
        "u_predicted_probability": prob, "u_decision_threshold": ps1.get("decision_threshold"),
        "u_dominant_error_code": ps2.get("dom_error_code"), "u_dominant_subsystem": ps2.get("dom_subsystem"),
        "work_notes": notes,
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
        'validated_failure_onsets,chargeable_oos_onsets,'
        # 03-Aug-2026, sql/46. hardware_oos_minutes is the SUM of episode
        # durations and double-counts concurrent episodes, so it is component
        # burden and not device downtime. Availability must read
        # hardware_oos_union_minutes. Both are returned so the difference
        # stays visible rather than being silently swapped.
        'hardware_oos_union_minutes,hardware_oos_onsets_distinct_interval,'
        # The ratio of the two minute columns. sql/46 creates it and comments
        # it; the producer computes it twice; and until now no route selected
        # it, so the one number that shows HOW MUCH the episode-sum column
        # overstates a given day was unreachable. A documented correction that
        # nothing serves is how the next reader concludes it was never made.
        'oos_minutes_overlap_factor',
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
        'validated_failure_onsets,baseline_mean_28d,baseline_std_28d,oos_zscore_28d,alert_reason,'
        'hardware_oos_union_minutes,hardware_oos_onsets_distinct_interval,'
        'oos_minutes_overlap_factor',
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
        # EVERY ps2_ TABLE, NOT THE TWENTY IN A HAND-WRITTEN VIEW. 23-Sep-2026
        # This read v_ps2_v25_status, a static 20-way UNION over the patterns
        # producer's output. The serial-grain producer's 27 tables were absent
        # from it, so two thirds of the PS2 estate was invisible to its own
        # status route: a family that republished a stale vintage, or stopped
        # being written at all, could not be seen from here. That is also why
        # the loader's mixed-vintage guard had nothing to check against.
        #
        # Discovered from the catalog rather than listed, so it cannot drift:
        # a table added by a future producer appears here the first time it is
        # loaded, and one that is dropped stops being counted. Names come from
        # information_schema, never from the caller, and are re-validated
        # against ^ps2_[a-z0-9_]+$ before they are interpolated.
        #
        # COHERENCE IS ONE computed_date, NOT ONE run_id. PS2 has two producer
        # notebooks and each stamps its own RUN_ID, so two run_ids is the
        # healthy state and the old definition would read as permanently
        # broken across 47 tables. The run_ids are reported per producer
        # instead, which is the thing a reader actually wants to see.
        # A SILENT FAILURE HERE READS AS AN EMPTY ESTATE.        23-Sep-2026
        # The first version ran this through _safe_rows, which returns [] on
        # any error -- so when the discovery query failed the route answered
        # "0 tables of 0, not coherent" and looked like a truthful report of an
        # empty database rather than a broken query. That is the same class of
        # defect this route exists to expose, so the error is captured and
        # returned instead.
        #
        # THE CAST IS LOAD-BEARING. information_schema.columns.table_name is
        # type information_schema.sql_identifier, and the regex operator does
        # not resolve against it without a cast -- equality does, which is why
        # the catalog action nearby works while this did not.
        _cat, _discovery_error = [], None
        try:
            _cat = rows(
                "SELECT c.table_name::text AS t, "
                "       bool_or(c.column_name='city_id')          AS has_city, "
                "       bool_or(c.column_name='computed_date')    AS has_date, "
                "       bool_or(c.column_name='run_id')           AS has_run, "
                "       bool_or(c.column_name='notebook_version') AS has_ver, "
                "       bool_or(c.column_name='as_of_ts')         AS has_asof "
                "  FROM information_schema.columns c "
                "  JOIN information_schema.tables t "
                "    ON t.table_schema=c.table_schema AND t.table_name=c.table_name "
                " WHERE c.table_schema='public' AND t.table_type='BASE TABLE' "
                "   AND c.table_name::text LIKE 'ps2#_%' ESCAPE '#' "
                " GROUP BY c.table_name ORDER BY c.table_name")
        except Exception as _e:
            _discovery_error = str(_e)[:400]
        _safe = re.compile(r"^ps2_[a-z0-9_]+$")
        _parts, _skipped = [], []
        for _r in _cat:
            _t = str(_r["t"])
            if not _safe.match(_t):
                _skipped.append(_t)
                continue
            _parts.append(
                "SELECT '{t}'::text AS table_name, "
                "{city} AS city_id, {run} AS run_id, {date} AS computed_date, "
                "{ver} AS notebook_version, {asof} AS as_of_ts, COUNT(*) AS row_count "
                "FROM {t}{where}".format(
                    t=_t,
                    city=("city_id" if _r["has_city"] else "NULL::city_code"),
                    run=("MAX(run_id)" if _r["has_run"] else "NULL::varchar"),
                    date=("MAX(computed_date)" if _r["has_date"] else "NULL::date"),
                    ver=("MAX(notebook_version)" if _r["has_ver"] else "NULL::varchar"),
                    asof=("MAX(as_of_ts)" if _r["has_asof"] else "NULL::timestamp"),
                    where=(" WHERE city_id=:c GROUP BY city_id" if _r["has_city"] else ""),
                ))
        data, _count_error = [], None
        if _parts:
            try:
                data = rows(" UNION ALL ".join(_parts) + " ORDER BY table_name", c=city)
            except Exception as _e:
                _count_error = str(_e)[:400]

        # A TABLE WITH NO run_id WAS NEVER LOADED BY THE LOADER. It is a SQL
        # seed or an artifact of the retired auto-creating push Lambda, and it
        # will not move when a producer runs. Those are separated rather than
        # folded into coherence, which would otherwise read false forever
        # because sql/07 and sql/08 seeded two different July dates.
        managed = [r for r in data if r.get("run_id")]
        unmanaged = sorted(r["table_name"] for r in data if not r.get("run_id"))
        run_ids = sorted({r["run_id"] for r in managed})
        dates = sorted({str(r["computed_date"]) for r in managed if r.get("computed_date")})
        all_dates = sorted({str(r["computed_date"]) for r in data if r.get("computed_date")})
        populated = [r for r in data if (r.get("row_count") or 0) > 0]
        empty = sorted(r["table_name"] for r in data if not (r.get("row_count") or 0))
        # One entry per producer, keyed by the run_id it stamped.
        producers = {}
        for r in managed:
            p = producers.setdefault(str(r["run_id"]), {
                "run_id": str(r["run_id"]), "tables": 0, "rows": 0,
                "computed_date": r.get("computed_date"),
                "notebook_version": r.get("notebook_version")})
            p["tables"] += 1
            p["rows"] += int(r.get("row_count") or 0)
        return ok({
            "city": city,
            "tables": len(populated),
            "tables_total": len(data),
            "tables_managed": len(managed),
            "unmanaged_tables": unmanaged,
            "computed_dates_all": all_dates,
            # Kept so existing callers keep rendering "N/M tables". It is now
            # the DISCOVERED total rather than a constant of 20.
            "expected_tables": len(data),
            "run_ids": run_ids,
            "computed_dates": dates,
            "producers": sorted(producers.values(), key=lambda p: -p["tables"]),
            "empty_tables": empty,
            "unnamed_skipped": _skipped,
            # Present only when something went wrong. Their absence is the
            # signal that "0 tables" would mean an empty database.
            "discovery_error": _discovery_error,
            "count_error": _count_error,
            # One vintage across every LOADER-MANAGED table, none of them
            # empty. Seeded tables are reported but do not decide this.
            "coherent": (not _discovery_error and not _count_error
                         and len(dates) == 1 and bool(managed)
                         and not [t for t in empty if t not in unmanaged]),
            "computed_date": (dates[-1] if dates else None),
            "as_of_ts": next((r["as_of_ts"] for r in data if r.get("as_of_ts")), None),
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



# ============ PS3 v2.5 source-first generation (sql/45) =====================
# ONE route family over the 20 tables sql/45 creates, plus /ps3/status.
#
# THESE ARE NEW ROUTES, NOT REPLACEMENTS. /ps3/rootcause, /ps3/incidents and
# every other existing PS3 path still reads ps3_v2_* / ps3_incident_predictions
# and is untouched. This family lives under /ps3/v25/ and can be removed by
# deleting this block.
#
# THE COLUMN LISTS ARE GENERATED FROM THE V26 SCHEMA DUMP, not typed. The PS2
# family was hand-typed and its lesson is already in the backlog: a column the
# export starts carrying stays invisible to the API until someone remembers to
# add it here. So the 22 columns that are all-null today -- the linked_* and
# confirmed_root_cause_* family, which wait on PS3_GOLD_INCIDENT_LABEL_EXPORT
# and the taxonomy export -- are LISTED anyway. They return null now and
# populate themselves the day those exports are configured.
#
# EVERY IDENTIFIER IS QUOTED. "column" is reserved in PostgreSQL and "rows" is
# a window-frame keyword; both are real column names in these tables. This
# project already lost a load to an unquoted "window" after eight tables had
# been staged, so the class goes rather than the instances.
#
# EVERY QUERY IS SCOPED TO THAT TABLE'S OWN LATEST computed_date, matching the
# PS2 family. The v25 loader refuses to publish a partial run at all, so they
# should never disagree -- but scoping independently means a half-refreshed
# database degrades to "one panel is stale" rather than "one panel is empty".
#
# CAPS. API Gateway kills the integration at 30s. device_episode_fact and
# device_day hold 54,239 rows each; both are browse windows, never a
# denominator. The rollups are the denominators.
#
# (table, select list, order by, default limit, max limit, date column or None)
_PS3V25 = {
    # ps3_v25_causal_balance: 54 rows, 4 columns
    "causal-balance": ("ps3_v25_causal_balance",
        '"treatment_component","covariate","standardised_mean_difference","balance_status"',
        '"treatment_component", "covariate"', 500, 2000, None),
    # ps3_v25_causal_effects: 6 rows, 22 columns
    "causal-effects": ("ps3_v25_causal_effects",
        '"treatment_component","outcome","estimator","average_treatment_effect",'
        '"standard_error","ci_low_95","ci_high_95","significant_95","episodes_used",'
        '"treated_episodes","control_episodes","overlap_share","treated_prevalence",'
        '"propensity_p01","propensity_p99","models_converged","status","interpretation",'
        '"p_value_two_sided","p_value_holm","significant_95_holm","multiplicity_note"',
        '"treatment_component", "outcome"', 200, 1000, None),
    # ps3_v25_source_column_profile: 7 rows, 9 columns
    "column-profile": ("ps3_v25_source_column_profile",
        '"column","status","rows","non_null","null_rate","distinct_values",'
        '"deterministic_given_event_type","usable_as_observed_label","note"',
        '"column"', 50, 200, None),
    # ps3_v25_commanded_split: 3 rows, 5 columns
    "commanded-split": ("ps3_v25_commanded_split",
        '"mars_device_category","oos_episodes","commanded_signal_episodes",'
        '"failure_only_episodes","basis"',
        '"mars_device_category"', 50, 200, None),
    # ps3_v25_component_summary: 14 rows, 7 columns
    "component-summary": ("ps3_v25_component_summary",
        '"mars_device_category","component_attribution","dashboard_root_cause_domain",'
        '"dashboard_severity","oos_episode_count","device_count","confirmed_root_cause_count"',
        '"oos_episode_count" DESC', 200, 2000, None),
    # ps3_v25_device_day: 54,239 rows, 11 columns
    "device-day": ("ps3_v25_device_day",
        '"device_id","mars_device_category","event_date","oos_episode_starts","oos_set_events",'
        '"set_signal_span_minutes","commanded_signal_episodes","observed_severity_episodes",'
        '"confirmed_root_cause_episodes","failure_only_episode_starts","grain"',
        '"event_date" DESC, "device_id"', 500, 5000, 'event_date'),
    # ps3_v25_device_reliability: 2,806 rows, 16 columns
    "device-reliability": ("ps3_v25_device_reliability",
        '"device_id","mars_device_category","oos_episode_count","oos_set_event_count",'
        '"critical_episodes","first_episode_at","latest_episode_at","mean_interval_hours",'
        '"median_interval_hours","confirmed_root_cause_episodes","observed_severity_episodes",'
        '"commanded_signal_episodes","critical_rate","failure_only_episode_count",'
        '"reliability_risk_band","band_basis"',
        '"oos_episode_count" DESC', 500, 5000, None),
    # ps3_v25_device_summary: 2,806 rows, 9 columns
    "device-summary": ("ps3_v25_device_summary",
        '"device_id","mars_device_category","oos_episode_count","first_oos_episode_start",'
        '"latest_oos_episode_start","latest_dashboard_severity",'
        '"latest_dashboard_root_cause_domain","confirmed_root_cause_episode_count",'
        '"observed_severity_episode_count"',
        '"oos_episode_count" DESC', 500, 5000, None),
    # ps3_v25_device_episode_fact: 54,239 rows, 77 columns
    # 1500, NOT 2000. Measured against the live API on 03-Aug-2026:
    #   limit=1000 -> 3,033,966 bytes  200 OK
    #   limit=1800 -> 5,461,360 bytes  200 OK
    #   limit=2000 -> HTTP 500 in 2.8s (far too fast to be the 30s gateway
    #                                   timeout -- this is Lambda's 6 MB
    #                                   synchronous response cap)
    # 3,034 bytes per row over 77 columns; 1500 lands at 4.5 MB.
    # The headroom is not padding: 22 of those columns are all-null today and
    # will carry real strings once the label and taxonomy exports are
    # configured. The row gets WIDER, so a cap set flush against today's
    # ceiling would start 500ing on the day root cause finally lands.
    "episodes": ("ps3_v25_device_episode_fact",
        '"oos_episode_id","device_id","mars_device_category","episode_start",'
        '"episode_last_signal","oos_set_event_count","first_oos_event_id",'
        '"contains_commanded_oos_signal","observed_event_component","component_subsystem",'
        '"component_position","facility_id","facility_name","bus_id","component_serial_nbr",'
        '"event_type_name","event_type_id","observed_event_severity","event_type_severity",'
        '"event_priority","requires_service_call","any_automatic_clear",'
        '"distinct_serials_in_episode","distinct_components_in_episode",'
        '"set_signal_span_minutes","oos_fact_definition","oos_minutes_union",'
        '"oos_minutes_naive_sum","events_with_clear","events_clear_clamped",'
        '"episode_scope_status","episode_scope_start","linked_severity","linked_component",'
        '"linked_root_cause","linked_root_cause_domain","linked_confidence","linked_source",'
        '"link_method","observed_severity_label","severity_status","component_attribution",'
        '"component_attribution_status","confirmed_root_cause_label",'
        '"confirmed_root_cause_domain","root_cause_confidence","root_cause_evidence_source",'
        '"root_cause_link_method","root_cause_status","candidate_root_cause_raw",'
        '"evidence_conflict_status","event_month","event_day_of_week","event_hour",'
        '"log_oos_set_event_count","log_set_signal_span_minutes","prior_episodes_7d",'
        '"prior_episodes_30d","prior_episodes_90d","days_since_prior_episode",'
        '"device_oos_recency_status","predicted_component","predicted_component_confidence",'
        '"component_model_status","run_id","computed_at_utc","data_as_of_date",'
        '"data_freshness_days","is_current_operational_score","freshness_status",'
        '"right_censored_tail_days","chargeability_policy","shap_interpretation",'
        '"dashboard_root_cause_domain","dashboard_root_cause_status","dashboard_severity",'
        '"dashboard_severity_status"',
        '"episode_start" DESC', 200, 1500, 'episode_start'),
    # ps3_v25_root_cause_evidence_audit: 7 rows, 5 columns
    "evidence-audit": ("ps3_v25_root_cause_evidence_audit",
        '"source","status","reference","rows","detail"',
        '"source"', 50, 200, None),
    # ps3_v25_prediction_explainability: 3 rows, 12 columns
    "explainability": ("ps3_v25_prediction_explainability",
        '"target","model_output","oos_episode_id","device_id","mars_device_category",'
        '"predicted_label","feature","shap_value","abs_shap_value","explanation_status",'
        '"explanation_note","model_scope"',
        '"target", "abs_shap_value" DESC', 200, 1000, None),
    # ps3_v25_facility_rollup: 366 rows, 11 columns
    "facility-rollup": ("ps3_v25_facility_rollup",
        '"facility_id","mars_device_category","devices","oos_episodes","first_episode",'
        '"latest_episode","active_days","commanded_signal_episodes",'
        '"confirmed_root_cause_episodes","episodes_per_device","failure_only_episodes"',
        '"oos_episodes" DESC', 500, 2000, None),
    # ps3_v25_model_feature_importance: 12 rows, 5 columns
    "feature-importance": ("ps3_v25_model_feature_importance",
        '"target","model","feature","importance","model_scope"',
        '"target", "importance" DESC', 200, 1000, None),
    # ps3_v25_label_maturity: 3 rows, 8 columns
    "label-maturity": ("ps3_v25_label_maturity",
        '"mars_device_category","oos_episode_count","observed_severity_count",'
        '"confirmed_root_cause_count","candidate_root_cause_count",'
        '"component_attribution_count","severity_coverage","confirmed_root_cause_coverage"',
        '"mars_device_category"', 50, 200, None),
    # ps3_v25_model_scorecard: 8 rows, 13 columns
    "model-scorecard": ("ps3_v25_model_scorecard",
        '"target","candidate_model","f1_macro","f1_weighted","balanced_accuracy","accuracy",'
        '"mcc","majority_f1_macro","macro_f1_lift","label_coverage","quality_gate","detail",'
        '"model_scope"',
        '"target", "candidate_model"', 100, 500, None),
    # ps3_v25_repeat_interval: 14 rows, 11 columns
    "repeat-interval": ("ps3_v25_repeat_interval",
        '"component_attribution","mars_device_category","attributed_episodes","devices",'
        '"episodes_with_a_next","median_days_to_next","p25_days_to_next","mean_days_to_next",'
        '"repeat_rate_within_horizon","horizon_days","basis"',
        '"attributed_episodes" DESC', 200, 2000, None),
    # ps3_v25_run_stage_audit: 8 rows, 9 columns
    "run-stage-audit": ("ps3_v25_run_stage_audit",
        '"stage","status","detail","at_utc","rows","mode","evidence_sources","taxonomy_rows",'
        '"model_runs"',
        '"at_utc"', 100, 500, None),
    # ps3_v25_run_status: 19 rows, 13 columns
    "run-status": ("ps3_v25_run_status",
        '"run_id","revision","table_name","publish_status","rows","path","run_mode",'
        '"data_as_of_date","is_current_operational_score","computed_at_utc","run_is_coherent",'
        '"tables_published","tables_total"',
        '"table_name"', 100, 500, None),
    # ps3_v25_serial_reliability: 2,762 rows, 10 columns
    "serial-reliability": ("ps3_v25_serial_reliability",
        '"component_serial_nbr","device_id","mars_device_category","oos_episode_count",'
        '"first_episode_at","latest_episode_at","component_attributions",'
        '"confirmed_root_cause_episodes","observed_span_days","episodes_per_100_observed_days"',
        '"oos_episode_count" DESC', 500, 5000, None),
    # ps3_v25_oos_source_audit: 1 rows, 12 columns
    "source-audit": ("ps3_v25_oos_source_audit",
        '"source","status","reference","detail","engine","pushed_down","scanned_rows",'
        '"window_start","window_end","current_device_filter","rows_removed_by_current_filter",'
        '"selected_rows"',
        '"source"', 50, 200, None),
}

_PS3V25_FILTERS = (
    ("category",  "mars_device_category"),
    ("device",    "device_id"),
    ("serial",    "component_serial_nbr"),
    ("facility",  "facility_id"),
    ("component", "component_attribution"),
    ("episode",   "oos_episode_id"),
    ("target",    "target"),
)


def _ps3_v25_route(path, params, city):
    """Returns a response for /ps3/v25/* and /ps3/status, else None."""
    params = params or {}

    if path == "/ps3/status":
        # Two sources, deliberately. v_ps3_v25_status counts what actually
        # landed in each table; ps3_v25_run_status is what the NOTEBOOK said it
        # published. Reading only the first cannot tell a complete load of a
        # broken run from a broken load of a complete run.
        data = rows(
            'SELECT "table_name", "computed_date", "rows" '
            "FROM v_ps3_v25_status WHERE city_id=:c ORDER BY table_name", c=city)
        try:
            declared = rows(
                'SELECT "run_id","revision","run_mode","computed_date","data_as_of_date",'
                '"run_is_coherent","tables_published","tables_total",'
                '"is_current_operational_score","computed_at_utc" '
                "FROM ps3_v25_run_status WHERE city_id=:c "
                "AND computed_date=(SELECT MAX(computed_date) FROM ps3_v25_run_status "
                "WHERE city_id=:c) LIMIT 1", c=city)
        except Exception as e:
            declared = []
            data.append({"table_name": "_run_status_read_error", "rows": str(e)[:160]})

        # 24-Sep-2026. The severity provenance and the vintage audit pair live
        # on ps3_v25_device_episode_fact, not on ps3_v25_run_status, so they
        # need their own read. sql/66 added the columns; before that the loader
        # had been dropping all three silently for want of anywhere to put them.
        #
        # GROUPED, not LIMIT 1. severity_definition is NOT constant across the
        # run: it reads servicenow_linked_incident_severity on a linked row and
        # the duration-band text on a native one. Taking the first row would
        # report whichever happened to sort first as though it were the whole
        # story. The grouping is also the more useful answer -- it shows the
        # linked-versus-native split, which is the question anyone asks first
        # when told a severity was measured rather than reported.
        #
        # observed_source_max_date and asof_lead_days ARE constant per run (they
        # describe the run, not the episode), so MAX over the group is just a
        # way to carry them out of the same scan.
        try:
            prov = rows(
                'SELECT "severity_status", "severity_definition", '
                'MAX("observed_source_max_date") AS observed_source_max_date, '
                'MAX("asof_lead_days") AS asof_lead_days, '
                'COUNT(*) AS episodes '
                "FROM ps3_v25_device_episode_fact WHERE city_id=:c "
                "AND computed_date=(SELECT MAX(computed_date) "
                "FROM ps3_v25_device_episode_fact WHERE city_id=:c) "
                "GROUP BY 1, 2 ORDER BY 5 DESC", c=city)
        except Exception as e:
            prov = []
            data.append({"table_name": "_episode_fact_provenance_error",
                         "rows": str(e)[:160]})
        vintage = prov[0] if prov else {}

        head = declared[0] if declared else {}
        dates = sorted({str(r["computed_date"]) for r in data if r.get("computed_date")})
        loaded = [r for r in data if r.get("table_name", "").startswith("ps3_v25_")]
        empty = sorted(r["table_name"] for r in loaded if not r.get("rows"))
        return ok({
            "city": city,
            "generation": "ps3_v25",
            "tables": len(loaded),
            "expected_tables": len(_PS3V25),
            "computed_date": (dates[0] if len(dates) == 1 else dates),
            "run_id": head.get("run_id"),
            "revision": head.get("revision"),
            "run_mode": head.get("run_mode"),
            "data_as_of_date": head.get("data_as_of_date"),
            "computed_at_utc": head.get("computed_at_utc"),
            "is_current_operational_score": head.get("is_current_operational_score"),
            # What the declared vintage was measured against, rather than only
            # asserted as. asof_lead_days is data_as_of_date minus the newest
            # episode actually read; zero means the claim matches the data. The
            # run raises above PS3_ASOF_MAX_LEAD_DAYS rather than publishing, so
            # a value here is always within that bound -- it is served so a
            # reader can confirm that from the data instead of trusting the gate.
            "observed_source_max_date": vintage.get("observed_source_max_date"),
            "asof_lead_days": vintage.get("asof_lead_days"),
            # One row per (tier, definition): how many episodes carry each, and
            # what that tier meant on this run. A tab showing CRITICAL can now
            # say what CRITICAL was.
            "severity_tiers": [
                {"severity_status": r.get("severity_status"),
                 "severity_definition": r.get("severity_definition"),
                 "episodes": r.get("episodes")}
                for r in prov],
            "notebook_tables_published": head.get("tables_published"),
            "notebook_tables_total": head.get("tables_total"),
            "notebook_run_is_coherent": head.get("run_is_coherent"),
            # coherent means three things at once: every expected table is
            # present, they all carry the same computed_date, and none is
            # empty. Any one of those failing is a half-loaded dashboard.
            "coherent": (len(loaded) == len(_PS3V25) and len(dates) == 1 and not empty),
            "empty_tables": empty,
            "total_rows": sum(r.get("rows") or 0 for r in loaded),
            "rows": data,
        })

    if not path.startswith("/ps3/v25/"):
        return None

    metric = path[len("/ps3/v25/"):].strip("/")
    if metric == "":
        return ok({"metrics": sorted(_PS3V25),
                   "filters": [p for p, _ in _PS3V25_FILTERS],
                   "usage": "/ps3/v25/<metric>?city=CHI&category=GATE&limit=200"})
    spec = _PS3V25.get(metric)
    if spec is None:
        return err(404, "unknown PS3 v2.5 metric %r. Known: %s"
                        % (metric, ", ".join(sorted(_PS3V25))))

    table, cols, order, dflt, hard, datecol = spec
    where = ["city_id=:c",
             "computed_date=(SELECT MAX(computed_date) FROM %s WHERE city_id=:c)" % table]
    kw = {"c": city}

    have = cols.replace('"', "").split(",")
    for pname, col in _PS3V25_FILTERS:
        v = (params.get(pname) or "").strip()
        if v and col in have:
            where.append('"%s"=:%s' % (col, pname))
            kw[pname] = v.upper() if col == "mars_device_category" else v

    if datecol:
        for pname, op in (("from", ">="), ("to", "<=")):
            v = (params.get(pname) or "").strip()
            if v:
                where.append('"%s" %s :%s' % (datecol, op, pname))
                kw[pname] = v

    limit = _clamp_int(params.get("limit"), dflt, 1, hard)
    offset = _clamp_int(params.get("offset"), 0, 0, 1000000)
    sql = ('SELECT %s FROM %s WHERE %s ORDER BY %s LIMIT %d OFFSET %d'
           % (cols, table, " AND ".join(where), order, limit, offset))
    return ok(rows(sql, **kw))

def route(method, path, params, body, headers=None):
    city = q((params or {}).get("city", CITY))
    _v25 = _ps2_v25_route(path, params, city)
    if _v25 is not None: return _v25
    _p3v25 = _ps3_v25_route(path, params, city)
    if _p3v25 is not None: return _p3v25
    # ---- PS2 SERIAL-GRAIN  (added 01-Aug, cut back 23-Sep-2026) -----------
    #
    # ONE route, TWO metrics: GET /ps2/serial/{metric}?city=CHI
    #
    # It had seventeen. Fifteen served the pre-V4 page at
    # /dashboard/city/:cityId, which was removed on 23-Sep; sql/62 then
    # dropped the twelve _serial tables underneath them and the serial
    # notebook stopped writing those families in the same commit. The two
    # that remain are the two V4 reads, in V4PS2Overview.jsx:112-113.
    #
    # cmdb went with them. Its comment said it "drives every Analyse button
    # on this tab", and that was true of the tab that no longer exists --
    # nothing in dashboard/src requests it now. It is worth being exact about
    # how that was established, because a route is easy to keep by accident:
    # every API path in dashboard/src was listed, and the front end builds no
    # path by template literal or concatenation, so the list is complete
    # rather than merely long. ps2_device_catalog, which cmdb read, is NOT
    # dropped -- /ps2/devices and _device_360 still read it.
    #
    # Column names are what the JSX reads off the row object, not a choice:
    #   sankey      subsystem_from/subsystem_to/cascade_count/
    #               total_business_impact/avg_severity
    #   ignition    subsystem/ignition_count/termination_count
    #
    # Both return the NEWEST computed_date for the city only, the same rule
    # the device-grain PS2 routes use.
    if path.startswith("/ps2/serial/"):
        metric = path[len("/ps2/serial/"):].strip("/").lower()
        lim = _clamp_int((params or {}).get("limit"), 500, 1, 5000)

        # serial_id is gone with the per-serial metrics: neither surviving
        # metric is keyed on one, and a filter no table can honour is how a
        # parameter becomes a 42703 at request time instead of a no-op.
        def _latest(tbl, cols, order=""):
            return (f"SELECT {cols} FROM {tbl} WHERE city_id=:c"
                    f" AND computed_date=(SELECT MAX(computed_date) FROM {tbl}"
                    f" WHERE city_id=:c) {order} LIMIT {lim}")

        M = {
            "sankey": _latest(
                "ps2_cascade_sankey_subsystem",
                'subsystem_from, subsystem_to, cascade_count, '
                'total_business_impact, avg_severity, computed_date',
                "ORDER BY cascade_count DESC NULLS LAST"),
            "ignition": _latest(
                "ps2_ignition_termination_subsystem",
                'subsystem, ignition_count, termination_count, computed_date',
                "ORDER BY ignition_count DESC NULLS LAST"),
        }
        if metric not in M:
            return err(404, "unknown ps2 serial metric '%s' -- known: %s"
                       % (metric, ", ".join(sorted(M))))
        # One metric failing must not take the tab down. _safe_rows returns []
        # and logs rather than raising, so a table that has not been loaded
        # yet degrades to that panel's own "no data yet" message.
        return ok(_safe_rows(M[metric], c=city))

    # ---- Phase-1e NEW PS2 analytics (return newest computed_date only) ----
    # ---- Phase-1f RICH PS2 (correlation/markov/network/error-codes/device drill-down) ----
    if path == "/ps2/devices":
        return ok(rows("SELECT device_id,device_name,serial,category,control_group,facility,operator,cascade_days,avg_chain_len,max_chain_len,dom_subsystem,dom_error_code,worst_cascade_path,worst_window FROM ps2_device_catalog WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_device_catalog WHERE city_id=:c) ORDER BY cascade_days DESC LIMIT 200", c=city))
    # /ps2/devicecascades REMOVED 23-Sep-2026. It served ps2_device_cascades
    # to the pre-V4 page only; nothing in dashboard/src requests it. The
    # TABLE stays -- _device_360 reads the same rows as `recent_cascades`
    # and V4api.js calls that through /ps1/device-360, so this is one
    # duplicate reader going, not a data source.
    if path == "/ps2/phi":
        return ok(rows("SELECT sub_a,sub_b,phi,computed_date FROM ps2_phi_matrix WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_phi_matrix WHERE city_id=:c)", c=city))
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
                "SELECT computed_date,scope,is_fleet,node_id,betweenness,pagerank,in_degree,"
                "out_degree,total_degree,pagerank_rank,betweenness_rank,"
                "n_nodes_in_scope FROM v_ps2_network_centrality "
                "WHERE city_id=:c AND computed_date="
                "(SELECT MAX(computed_date) FROM ps2_network_centrality WHERE city_id=:c) "
                "ORDER BY scope, betweenness DESC", c=city))
        return ok(rows(
            "SELECT computed_date,scope,is_fleet,node_id,betweenness,pagerank,in_degree,"
            "out_degree,total_degree,pagerank_rank,betweenness_rank,"
            "n_nodes_in_scope FROM v_ps2_network_centrality "
            "WHERE city_id=:c AND scope=:s AND computed_date="
            "(SELECT MAX(computed_date) FROM ps2_network_centrality WHERE city_id=:c) "
            "ORDER BY betweenness DESC", c=city, s=sc))
    # ---- PS1 model scorecard ----
    #
    # 2026-08-10 -- ps1_failure_summary RETIRED as the source of this route.
    #
    # WHAT IT WAS SERVING. Two rows, TVM and Gates, hand-seeded on 13-Jul-2026
    # out of a console log by sql/04 (since moved to sql/manual/). quality_gate
    # FAIL, promoted false, and target = 'will_fail_3d' -- a label name that
    # exists nowhere in this system; the column the models are actually trained
    # on is will_hardware_oos_3d. VALIDATOR was absent entirely. No loader has
    # ever written to that table, so there was no refresh path: every day it
    # aged by one and nothing could correct it. A dashboard panel reading it
    # showed a four-week-old failure verdict as the current state of PS1.
    #
    # WHAT REPLACES IT. ps1_model_performance, written by the 26-Jul sklearn run
    # through sql/load/, carrying all three fleets, quality_gate PASS, promoted
    # true, and the real target column. ps1_confusion supplies accuracy, n_test,
    # n_test_pos, the base rate and the operating point from the run's own
    # TP/FP/TN/FN rather than from a literal typed into a seed file.
    #
    # THE COLUMN CONTRACT IS PRESERVED EXACTLY, because two live consumers read
    # these names: dashboard/src/data/api.js:apiPS1Summary and the legacy
    # PS1FailurePredictionTab. Columns the new source genuinely does not have --
    # Brier, calibrated AUC, top-k, MAP, n_train, SageMaker registration,
    # overfit_flag -- are returned as null and are NOT back-filled from the
    # retired row. A null reads as "not measured". A stale number reads as fact.
    #
    # THE RETIRED TABLE IS NOT DROPPED AND NOT PURGED. If ps1_model_performance
    # holds nothing for a city, this route falls back to it and says so in
    # source_table, so no city that has not been re-run yet goes dark. That
    # fallback is the reversibility of this change: delete the mp branch and the
    # 13-Jul behaviour returns byte for byte.
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
        #
        # 2026-08-10 -- second reader of ps1_failure_summary, repointed with the
        # route above. ps1_model_performance carries recall_floor and
        # base_rate_pct (sql/16 added both), so the floor a model is judged
        # against now comes from the same run that produced the model.
        #
        # The dict is keyed by BOTH spellings -- 'GATE' and 'Gates' -- because
        # ps1_leaderboard.device is a display name and
        # ps1_model_performance.device_category is a code. Keying on one and
        # looking up with the other is what silently returned {} here before,
        # and a missing floor does not raise: it simply means no model is ever
        # judged below floor. A gate that cannot fail is not a gate.
        floors = {}
        try:
            for x in rows("SELECT device_category,recall_floor,base_rate_pct,quality_gate "
                          "FROM ps1_model_performance WHERE city_id=:c AND computed_date="
                          "(SELECT MAX(computed_date) FROM ps1_model_performance WHERE city_id=:c)",
                          c=city):
                cat = str(x.get("device_category") or "")
                floors[cat] = x
                floors[_PS1_DISPLAY.get(cat, cat)] = x
        except Exception:
            floors = {}
        if not floors:
            # Fallback to the retired table, same reversibility rule as /ps1/summary.
            try:
                for x in rows("SELECT device,recall_floor,base_rate_pct,quality_gate "
                              "FROM ps1_failure_summary WHERE city_id=:c", c=city):
                    dev = str(x.get("device") or "")
                    floors[dev] = x
                    cat = _PS1_CATEGORY.get(dev.upper())
                    if cat: floors[cat] = x
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
    # 2026-08-10. sql/50 changed this view from "omit the fleet" to "publish the
    # fleet, withhold the conclusion". GATE was never absent from
    # ps1_cross_wired_daily; it simply had fewer than 30 device-days on one side
    # of the coordinated-station-failure split, and sql/34's WHERE clause turned
    # that into a fleet the panel had never heard of.
    #
    # ORDER BY critical_lift DESC alone would now bury the insufficient fleets
    # among the low-lift ones with no signal that they are different. Sufficient
    # fleets rank first by lift; the rest follow ordered by how close they came,
    # so "GATE, 6 of the 30 device-days needed" is a readable statement of what
    # is missing rather than a blank.
    #
    # ORDER-INDEPENDENT, added 2026-08-10 evening. This route and sql/50 are
    # deployed by two separate mechanisms -- update-function-code for the code,
    # apply_sql for the view -- and there is no way to make those atomic. If the
    # code lands first, sufficient_data and min_cell do not exist yet and the
    # ORDER BY raises 42703, dark-screening the panel for the length of the gap.
    #
    # Rather than depend on an operator getting the order right every time, the
    # route detects which view is installed and adapts. The new ordering when
    # sql/50 is applied; the old one when it is not, plus an explicit marker so
    # the panel can say WHY GATE is missing instead of simply not drawing it.
    # Deploy-then-apply and apply-then-deploy now both work, in either order.
    if path == "/ps1/xw-causation":
        try:
            _has50 = bool(rows(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name='v_ps1_xw_causation' AND column_name='sufficient_data'"))
        except Exception:
            _has50 = False
        if _has50:
            return ok(_xw("SELECT * FROM v_ps1_xw_causation WHERE city_id=:c "
                          "ORDER BY sufficient_data DESC, critical_lift DESC NULLS LAST, "
                          "min_cell DESC, device_type", c=city))
        _legacy = _xw("SELECT * FROM v_ps1_xw_causation WHERE city_id=:c "
                      "ORDER BY critical_lift DESC", c=city)
        for _r in (_legacy if isinstance(_legacy, list) else []):
            _r["sufficient_data"] = None
            _r["min_cell"] = None
            _r["schema_note"] = ("sql/50 is NOT applied to this database. This view still "
                                 "OMITS any fleet with fewer than 30 device-days on either "
                                 "side of the split, so a fleet missing from this list may "
                                 "exist and simply be under-observed. Apply "
                                 "50_ps1_xw_causation_all_fleets.sql to tell the two apart.")
        return ok(_legacy)
    # ---- LOCATION DIMENSION -------------------------------------------
    # 2026-08-06. THE NAMES WERE ALWAYS THERE; NOTHING SERVED THEM.
    #
    # dim_device_station holds 2,183 distinct facility_ids and EVERY ONE has a
    # facility_name. The only route that exposed any of them was
    # /ps1/station-summary, and that INNER-drives off ps1_station_summary, so
    # it can only ever name a facility that PS1 scored. PS2, PS3, PS4 and PS5
    # reference facilities PS1 never saw, which is why they showed bare ids.
    #
    # This route serves the DIMENSION, not a rollup, so every tab can resolve
    # any facility_id it holds. Additive: /ps1/station-summary is untouched and
    # PS1's Depots tab is unaffected. Reversible by deleting this block.
    #
    # UNION, not a join. dim_device_station is the broad source; dim_station is
    # a 17-row hand-seeded table from the PS2 run that occasionally has a name
    # the other lacks. Preferring dim_device_station and falling back keeps one
    # answer per id rather than two competing ones.
    #
    # regexp_replace collapses runs of whitespace: the source holds both
    # "North Park" and "North  Park" (two spaces) for different ids, and
    # untidied they sort apart and read as two different places.
    if path == "/ps1/facilities":
        return ok(rows(
            "WITH d AS ("
            "  SELECT facility_id,"
            "         BTRIM(regexp_replace(facility_name, '\\s+', ' ', 'g')) AS nm,"
            "         BTRIM(COALESCE(operator_name,''))                       AS op,"
            "         COUNT(*)                                                AS n_devices"
            "    FROM dim_device_station"
            "   WHERE city_id=:c AND facility_id IS NOT NULL"
            "     AND BTRIM(COALESCE(facility_name,'')) <> ''"
            "   GROUP BY 1,2,3"
            "), best AS ("
            "  SELECT DISTINCT ON (facility_id) facility_id, nm, op, n_devices"
            "    FROM d ORDER BY facility_id, n_devices DESC, nm"
            "), seeded AS ("
            "  SELECT facility_id,"
            "         BTRIM(regexp_replace(station_name, '\\s+', ' ', 'g')) AS nm,"
            "         BTRIM(COALESCE(operator,''))                           AS op"
            "    FROM dim_station"
            "   WHERE city_id=:c AND BTRIM(COALESCE(station_name,'')) <> ''"
            ") "
            "SELECT COALESCE(b.facility_id, s.facility_id)      AS facility_id,"
            "       COALESCE(b.nm, s.nm)                        AS facility_name,"
            "       NULLIF(COALESCE(b.op, s.op, ''), '')        AS operator_name,"
            "       COALESCE(b.n_devices, 0)                    AS n_devices,"
            "       CASE WHEN b.facility_id IS NOT NULL THEN 'dim_device_station'"
            "            ELSE 'dim_station' END                 AS source "
            "  FROM best b FULL OUTER JOIN seeded s ON s.facility_id = b.facility_id "
            " ORDER BY 4 DESC, 2", c=city))
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
        # 10-Aug-2026 FIX. Same defect as /ps1/xw-act-now above: ranked across
        # the whole fleet with a LIMIT. TVM dominates total_days_out, so all 20
        # rows were TVM. MEASURED 10-Aug: 20/20 rows TVM.
        # `top` now means top-N PER FLEET.
        try:
            per_type = max(1, min(500, int((params or {}).get("top") or 20)))
        except (TypeError, ValueError):
            per_type = 20
        return ok(_xw("""WITH ranked AS (
                  SELECT v.*, ROW_NUMBER() OVER (
                           PARTITION BY v.device_type
                           ORDER BY v.total_days_out DESC NULLS LAST
                         ) AS fleet_rank
                  FROM v_ps1_xw_chronic_devices v
                  WHERE v.city_id = :c
                )
                SELECT * FROM ranked
                WHERE fleet_rank <= :t
                ORDER BY device_type, total_days_out DESC NULLS LAST""",
                c=city, t=per_type))
    # ---- sql/36: state framing -----------------------------------------
    if path == "/ps1/xw-flag-reason":
        return ok(_xw("SELECT * FROM v_ps1_xw_flag_reason WHERE city_id=:c "
                      "ORDER BY device_type", c=city))
    if path == "/ps1/xw-state-mix":
        return ok(_xw("SELECT * FROM v_ps1_xw_state_mix WHERE city_id=:c "
                      "ORDER BY device_type, ps1_risk_tier, device_state", c=city))
    if path == "/ps1/xw-act-now":
        # 10-Aug-2026 FIX. This ranked across the WHOLE fleet with a LIMIT.
        # VALIDATOR probabilities sit near 0.99997, so all 100 returned rows were
        # VALIDATOR and GATE devices needing action were invisible. MEASURED
        # 10-Aug: 100/100 rows VALIDATOR.
        #
        # This is the identical mistake fixed in /ps1/predictions on 26-Jul; the
        # fix was applied there and never generalised. `top` now means top-N
        # PER FLEET, matching how /ps1/predictions treats `limit`.
        #
        # IN_SPELL first WITHIN each fleet: a device down now outranks one whose
        # window opened today, and both outrank anything already back in service.
        try:
            per_type = max(1, min(500, int((params or {}).get("top") or 100)))
        except (TypeError, ValueError):
            per_type = 100
        return ok(_xw("""WITH ranked AS (
                  SELECT v.*, ROW_NUMBER() OVER (
                           PARTITION BY v.device_type
                           ORDER BY (v.device_state = 'IN_SPELL') DESC,
                                    v.ps1_fail_prob DESC NULLS LAST
                         ) AS fleet_rank
                  FROM v_ps1_xw_act_now v
                  WHERE v.city_id = :c
                )
                SELECT * FROM ranked
                WHERE fleet_rank <= :t
                ORDER BY device_type,
                         (device_state = 'IN_SPELL') DESC,
                         ps1_fail_prob DESC NULLS LAST""", c=city, t=per_type))
    if path == "/ps1/table-status":
        return ok(_xw("SELECT * FROM v_ps1_table_status ORDER BY table_name"))
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
    if path == "/ps1/confusion":
        return ok(rows("SELECT device_category,tp,fp,tn,fn FROM ps1_confusion WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps1_confusion WHERE city_id=:c)", c=city))
    # ---- PS3 failure-SEVERITY ----
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
        # 25-Aug-2026 -- REPOINTED off ps5_reliability_status. That table held
        # three rows sql/02 seeded on 11-Jul and nothing ever updated, so this
        # route kept reporting v1-era C-indexes long after the v5.6 champions
        # beat them. Rows now derive from ps5_cindex_leaderboard (refreshed
        # daily), same shape, dashboard_ready still FALSE -- see
        # _ps5_registry_rows for the full reasoning. The sql/02 seed INSERT is
        # deleted; a manual DROP TABLE ps5_reliability_status is pending once
        # this is deployed (nothing reads it after this change).
        return ok(_ps5_registry_rows(city))
    # ==== PS5 v5 NOTEBOOK OUTPUTS (sql/29 + sql/30, loaded 27-Jul: 29,441 rows) ====
    # These serve the REAL survival run. /ps5/status above is category-level
    # registry state derived from the same leaderboard; it answers "is the model
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
            " p_oos_7d, p_oos_1d, act_now_p, act_now_horizon_days, act_now_threshold,"
            " feature_asof_date "
            f"FROM v_ps5_device_rul WHERE {w} "
            "ORDER BY act_now DESC, act_now_p DESC NULLS LAST, rul_standard_days ASC NULLS LAST "
            f"LIMIT {_clamp_int((params or {}).get('limit'), 3000, 1, 12000)}", **kw))
    if path == "/ps5/serial-rul":
        dt = (params or {}).get("device_type")
        w = "city_id=:c"; kw = {"c": city}
        if dt:
            w += " AND device_type=:d"; kw["d"] = str(dt).upper().strip()
        # DISTINCT is not cosmetic. v_ps5_serial_dupes measures 3,823 duplicated
        # (device_id, component_serial_nbr) keys on validators, up to 25 rows
        # each, and reports max_component_types = max_risk_tiers =
        # max_rul_values = 1 within every one of them -- the repetition is a
        # roster fan-out, not a second reading of the part. DISTINCT can only
        # ever drop rows that are equal on every selected column, so it removes
        # the fan-out without being able to lose a real measurement. The grain
        # audit at /ps5/serial-grain stays live so the underlying defect is
        # still visible rather than papered over here.
        return ok(rows(
            "SELECT DISTINCT device_type, device_id, component_serial_nbr, has_serial,"
            " component_type_name, component_age_days, risk_tier, risk_score,"
            " expected_component_rul_days, predicted_median_survival_days,"
            " is_overdue, device_oos_failures_total, act_now, serial_source,"
            " feature_asof_date "
            f"FROM v_ps5_serial_rul WHERE {w} "
            "ORDER BY act_now DESC, expected_component_rul_days ASC NULLS LAST "
            f"LIMIT {_clamp_int((params or {}).get('limit'), 3000, 1, 12000)}", **kw))
    # ---- THE DENOMINATORS -------------------------------------------------
    # /ps5/device-rul and /ps5/serial-rul are ORDERED, CAPPED browse lists.
    # Counting them gives the count of what was returned, not of what exists,
    # and because both are ordered act_now DESC the truncated tail is the
    # healthy end -- so a UI that counts the browse list overstates risk. That
    # is the same trap that once put "600 of 600 devices need a work order" on
    # the PS1 screen.
    #
    # These two routes aggregate in SQL over the WHOLE view. They are the only
    # honest source of a PS5 total.
    if path == "/ps5/summary":
        return ok(rows(
            "SELECT device_type,"
            " COUNT(*) AS n_devices,"
            " COUNT(*) FILTER (WHERE act_now) AS n_act_now,"
            " COUNT(*) FILTER (WHERE is_overdue) AS n_overdue,"
            " COUNT(*) FILTER (WHERE risk_band='CRITICAL') AS n_critical,"
            " COUNT(*) FILTER (WHERE risk_band='HIGH') AS n_high,"
            " COUNT(*) FILTER (WHERE risk_band='MEDIUM') AS n_medium,"
            " COUNT(*) FILTER (WHERE risk_band='LOW') AS n_low,"
            " ROUND(AVG(rul_standard_days)::numeric,1) AS mean_rul_days,"
            " ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP"
            "        (ORDER BY rul_standard_days))::numeric,1) AS median_rul_days,"
            " ROUND(MIN(rul_standard_days)::numeric,1) AS min_rul_days,"
            " MAX(n_devices_in_type) AS n_devices_in_type,"
            " MAX(act_now_threshold) AS act_now_threshold,"
            " MAX(act_now_horizon_days) AS act_now_horizon_days,"
            " ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY act_now_p))::numeric,4) AS p50_act_now_p,"
            " ROUND((PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY act_now_p))::numeric,4) AS p90_act_now_p,"
            " COUNT(p_oos_7d) AS n_with_p_oos_7d,"
            " ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY p_oos_7d))::numeric,4) AS p50_p_oos_7d,"
            " ROUND((PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY p_oos_7d))::numeric,4) AS p90_p_oos_7d,"
            " MAX(feature_asof_date) AS feature_asof_date "
            "FROM v_ps5_device_rul WHERE city_id=:c "
            "GROUP BY device_type ORDER BY device_type", c=city))
    if path == "/ps5/component-summary":
        # COUNT(DISTINCT (device_id, component_serial_nbr)) rather than COUNT(*)
        # for the fan-out reason documented on /ps5/serial-rul above. n_rows is
        # carried alongside it so the size of the fan-out stays measurable from
        # this route alone: n_rows > n_components IS the defect.
        return ok(rows(
            "SELECT device_type,"
            " COUNT(DISTINCT (device_id, component_serial_nbr)) AS n_components,"
            " COUNT(*) AS n_rows,"
            " COUNT(DISTINCT device_id) AS n_devices,"
            " COUNT(DISTINCT component_type_name) AS n_component_types,"
            " COUNT(DISTINCT device_id) FILTER (WHERE act_now) AS n_devices_act_now,"
            " COUNT(DISTINCT (device_id, component_serial_nbr))"
            "   FILTER (WHERE act_now) AS n_components_act_now,"
            " COUNT(DISTINCT (device_id, component_serial_nbr))"
            "   FILTER (WHERE risk_tier='CRITICAL') AS n_components_critical,"
            " COUNT(DISTINCT (device_id, component_serial_nbr))"
            "   FILTER (WHERE has_serial IS NOT TRUE) AS n_components_no_serial,"
            " MAX(feature_asof_date) AS feature_asof_date "
            "FROM v_ps5_serial_rul WHERE city_id=:c "
            "GROUP BY device_type ORDER BY device_type", c=city))
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
    if path == "/ps5/survival":
        # 27-Sep-2026 (sql/74). The fleet's pooled Weibull, turned into what a planner reads: the chance a
        # device that has just come back into service has another OOS within N days, and the curve S(t).
        # Fleet-average -- one device's own risk moves with its recent history (the Cox part of the model).
        import math
        out = []
        for r in rows("SELECT device_type, weibull_shape, weibull_scale, cv_cindex, gate_pass, cindex_floor,"
                      " run_date, event_def_version, loaded_at FROM ps5_fleet_survival WHERE city_id=:c"
                      " ORDER BY device_type", c=city):
            k, lam = float(r["weibull_shape"]), float(r["weibull_scale"])
            surv = lambda t: math.exp(-((t / lam) ** k))
            r["median_days"] = round(lam * math.log(2) ** (1.0 / k), 2)
            r["p_within"] = {str(h): round(1.0 - surv(h), 4) for h in (1, 3, 7, 14)}
            r["hazard_trend"] = "falling" if k < 0.97 else ("rising" if k > 1.03 else "flat")
            r["curve"] = [{"t": round(t / 4.0, 2), "s": round(surv(t / 4.0), 4)} for t in range(0, 57)]
            out.append(r)
        return ok(out)
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
    # NOTE the trailing hyphen. Without it this also matches
    # /ps3/v25/... and swallows the entire PS3 v2.5 family below.
    # Every path inside this block is /ps3/v2-<name>, so the
    # hyphen changes nothing here and removes the collision.
    if path.startswith("/ps3/v2-"):
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

    if path == "/overview/summary":
        return err(501, "v_executive_summary deferred to Phase 2 (needs PS1/PS3/PS4 tables)")
    # ---- Device-central family (sql/56 + sql/57, added 2026-08-27) ----------
    # Reads the conformed device dimension (dim_device_incident_cmdb via
    # v_device_central) and the cross-PS v_device_360 view. Purely additive:
    # no existing route or panel reads these. All four are read-only, and each
    # is one view query -- no per-PS fan-out (the /ps1/xw-state-mix lesson).
    if path == "/device/central":
        # roster=1 -> the Device 360 picker's whole-fleet list: 4 columns, one
        # query, bare array. Band = PS5's where scored, else PS1's, else
        # UNSCORED -- a ROSTER convenience, not a finding (the picker's rule).
        if str((params or {}).get("roster", "")).strip() in ("1", "true", "yes"):
            return ok(_safe_rows(
                "SELECT device_id, mars_device_category AS device_type, facility_id,"
                " COALESCE(ps5_risk_band, ps1_risk_tier, 'UNSCORED') AS risk_band"
                " FROM v_device_360 WHERE city_id = :c ORDER BY device_id", c=city))
        cat = _PS1_CATEGORY.get(str((params or {}).get("category", "")).strip().upper())
        fac = str((params or {}).get("facility", "")).strip()
        # NB: the free-text param is deliberately NOT bound to a local named `q`
        # -- q() is the module-level city sanitizer route() calls on line one,
        # and a local assignment anywhere in this function shadows it for the
        # WHOLE function (UnboundLocalError on every request; bitten 27-Aug).
        qtext = str((params or {}).get("q", "")).strip()
        limit = _clamp_int((params or {}).get("limit"), 200, 1, 1000)
        offset = _clamp_int((params or {}).get("offset"), 0, 0, 100000)
        where, kw = ["city_id = :c"], {"c": city}
        if cat:
            where.append("mars_device_category = :cat"); kw["cat"] = cat
        if fac:
            where.append("facility_id = :f"); kw["f"] = fac
        if qtext:
            where.append("(device_id ILIKE :q OR device_name ILIKE :q OR serial_number ILIKE :q)")
            kw["q"] = "%" + qtext + "%"
        w = " AND ".join(where)
        head = _safe_rows("SELECT COUNT(*) AS n, MAX(as_of_date) AS as_of"
                          " FROM v_device_central WHERE " + w, **kw)
        body_rows = _safe_rows(
            "SELECT device_id, device_key, device_name, mars_device_category,"
            " device_type_name, facility_id, facility_name, operator_name,"
            " serial_number, component_serial_nbr, cmdb_ci_sys_id,"
            " incident_number, opened_at, incident_count"
            " FROM v_device_central WHERE " + w +
            " ORDER BY device_id LIMIT :lim OFFSET :off", lim=limit, off=offset, **kw)
        # as_of stays NULL when nothing matched. str(None) would ship the STRING
        # "None", which is truthy in JS and would render as a date on screen.
        _as_of = head[0]["as_of"] if head else None
        return ok({"rows": body_rows,
                   "total": head[0]["n"] if head else None,
                   "as_of": str(_as_of) if _as_of is not None else None,
                   "limit": limit, "offset": offset})

    if path == "/device/360":
        dev = (params or {}).get("device_id", "")
        if not dev: return err(400, "device_id required")
        if not _DEVICE_ID_RE.match(str(dev)): return err(400, "bad device_id")
        r = _safe_rows("SELECT * FROM v_device_360 WHERE city_id = :c AND device_id = :d LIMIT 1",
                       c=city, d=str(dev).strip().upper())
        return ok(dict(r[0], found=True) if r else {"device_id": dev, "found": False})

    if path == "/device/360/risk":
        cat = _PS1_CATEGORY.get(str((params or {}).get("category", "")).strip().upper())
        limit = _clamp_int((params or {}).get("limit"), 200, 1, 1000)
        w = "city_id = :c" + (" AND mars_device_category = :cat" if cat else "")
        kw = {"c": city}
        if cat:
            kw["cat"] = cat
        # ORDERING, NOT SCORING -- and deliberately NOT led by PS1.
        #
        # This first ordered by ps1_fail_prob DESC. Measured 27-Aug: 398 of
        # 1,000 devices come back at >= 0.999, because the PS1 label marks days
        # a device was ALREADY out of service rather than the day it failed. So
        # the top of the list was one arbitrary slice of a 398-way tie -- which
        # looks like a ranking and is not one.
        #
        # signal_count is a COUNT OF INDEPENDENT ANALYSES that flagged the
        # device, not a risk score and not a weighting: severity queued it, the
        # anomaly run called it something other than Normal, survival says it is
        # past its interval, ServiceNow holds a ticket. PS1 is excluded from the
        # count on purpose -- a model that has not passed its quality gate must
        # not decide what a reader looks at first. Its probability is still
        # returned and shown, just not trusted to sort.
        # PRECOMPUTED FIRST, THE VIEW AS THE FALLBACK.        02-Sep-2026
        # v_device_360 LEFT JOINs seven DISTINCT ON views; even with sql/58's
        # indexes this route measured 10.8s, because the cost is the seven-way
        # join and not the sort alone. device_level_aggregation holds the same
        # columns precomputed, so the same answer comes back from one indexed
        # table. If that table is empty -- never built, or mid-refresh -- fall
        # through to the view, which is always correct and merely slow.
        cols = ("device_id, mars_device_category, facility_name, ps1_risk_tier,"
                " ps1_fail_prob, ps3_action_band, ps3_risk_band, ps4_severity,"
                " ps5_risk_band, ps5_is_overdue, sn_incident_count, signal_count")
        fast = _safe_rows(
            "SELECT " + cols + " FROM device_level_aggregation WHERE " + w +
            " ORDER BY signal_count DESC, sn_incident_count DESC NULLS LAST,"
            " ps1_fail_prob DESC NULLS LAST LIMIT :lim", lim=limit, **kw)
        if fast:
            return ok(fast)
        return ok(_safe_rows(
            "SELECT device_id, mars_device_category, facility_name, ps1_risk_tier,"
            " ps1_fail_prob, ps3_action_band, ps3_risk_band, ps4_severity,"
            " ps5_risk_band, ps5_is_overdue, sn_incident_count,"
            " ((ps3_action_band IS NOT NULL)::int"
            "  + (ps4_severity IS NOT NULL AND ps4_severity <> 'Normal')::int"
            "  + COALESCE(ps5_is_overdue, false)::int"
            "  + (COALESCE(sn_incident_count, 0) > 0)::int) AS signal_count"
            " FROM v_device_360 WHERE " + w +
            " ORDER BY signal_count DESC,"
            " sn_incident_count DESC NULLS LAST,"
            " ps3_action_priority DESC NULLS LAST,"
            " ps1_fail_prob DESC NULLS LAST LIMIT :lim", lim=limit, **kw))

    if path == "/device/aggregate":
        # The precomputed device row (sql/59). One row per device, so the popup
        # renders from a single indexed lookup instead of the ~6s composite.
        # It carries identity, the four W dimensions, the headline measures and
        # the component lists as JSONB. It does NOT carry the causation matrices
        # or the ps3_v2 rootcause family -- those are derived in Python by
        # _device_360, and the front end still falls back to it for them. That
        # fallback is the reason this route may be added without risk.
        dev = (params or {}).get("device_id", "")
        if dev:
            if not _DEVICE_ID_RE.match(str(dev)):
                return err(400, "bad device_id")
            r = _safe_rows("SELECT * FROM device_level_aggregation"
                           " WHERE city_id = :c AND device_id = :d LIMIT 1",
                           c=city, d=str(dev).strip().upper())
            return ok(dict(r[0], found=True) if r else {"device_id": dev, "found": False})
        cat = _PS1_CATEGORY.get(str((params or {}).get("category", "")).strip().upper())
        limit = _clamp_int((params or {}).get("limit"), 200, 1, 2000)
        w2 = "city_id = :c" + (" AND mars_device_category = :cat" if cat else "")
        kw2 = {"c": city}
        if cat:
            kw2["cat"] = cat
        return ok(_safe_rows(
            "SELECT device_id, device_name, mars_device_category, facility_name,"
            " serial_number, cmdb_ci_sys_id, sn_incident_count, signal_count,"
            " ps1_risk_tier, ps3_action_band, ps4_severity, ps5_risk_band,"
            " vintage_spread_days, computed_at"
            " FROM device_level_aggregation WHERE " + w2 +
            " ORDER BY signal_count DESC, sn_incident_count DESC NULLS LAST"
            " LIMIT :lim", lim=limit, **kw2))

    if path == "/device/360/validation":
        return ok(_safe_rows(
            "SELECT check_name, metric, value, computed_at FROM device_360_validation"
            " ORDER BY check_name, metric"))

    if path == "/ps1/device-360":
        dev = (params or {}).get("device_id", "")
        if not dev: return err(400, "device_id required")
        # A/B WITHOUT A REDEPLOY. ?parallel=1 forces the concurrent path,
        # ?parallel=0 forces the sequential one; absent, D360_PARALLEL decides.
        _pv = (params or {}).get("parallel")
        _par = D360_PARALLEL_DEFAULT if _pv is None else str(_pv).strip() in ("1", "true", "yes")
        return ok(_device_360_parallel(city, dev) if _par else _device_360(city, dev))
    if path == "/ps1/servicenow-stage" and method == "POST":
        d = body or {}; dev = d.get("device_id", "")
        if not dev: return err(400, "device_id required")
        _blocked = write_guard(headers, payload=d.get("payload", {}), device_id=dev,
                               city=city, quota_check=True)
        if _blocked: return _blocked
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
        _blocked = write_guard(headers, payload=d.get("payload", {}), device_id=dev,
                               city=city, quota_check=True)
        if _blocked:
            return _blocked
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

# =====================================================================
# catalog -- READ-ONLY live inventory of the whole database.
#
# `inspect` only covers PURGE_TABLES["ps1"] + ["ps3"], which is a small
# slice. This walks information_schema and returns EVERY table and view
# in `public` with its columns, types, nullability, primary key and live
# row count -- i.e. what is actually in Aurora right now, as opposed to
# what the migrations say should be.
#
#   aws lambda invoke --function-name cubic-mars-dashboard-api \
#     --cli-binary-format raw-in-base64-out \
#     --payload '{"action":"catalog"}' catalog.json
#
# Options:
#   {"action":"catalog","counts":false}   skip row counts (much faster)
#   {"action":"catalog","like":"ps5_"}    only names containing this
#
# Row counts are exact COUNT(*) per table and are the slow part. On a
# wide database that can approach the Lambda timeout, so counts are
# wrapped per-table and a failure degrades to a message on that table
# rather than losing the whole response.
# =====================================================================
def catalog(evt):
    c = conn()
    like = (evt or {}).get("like") or ""
    want_counts = (evt or {}).get("counts", True)

    cols_by_table = {}
    for tb, cn, dt, nul, dflt in c.run(
        "SELECT table_name, column_name, "
        "       COALESCE(character_maximum_length::text, "
        "                numeric_precision::text, '') AS extra, "
        "       is_nullable, COALESCE(column_default,'') "
        "FROM information_schema.columns "
        "WHERE table_schema='public' ORDER BY table_name, ordinal_position"):
        cols_by_table.setdefault(tb, []).append(
            {"column": cn, "size": dt, "nullable": nul == "YES", "default": dflt[:60]})

    types_full = {}
    for tb, cn, dt in c.run(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='public'"):
        types_full[(tb, cn)] = dt

    pks = {}
    for tb, cn in c.run(
        "SELECT tc.table_name, kcu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name "
        " AND tc.table_schema = kcu.table_schema "
        "WHERE tc.table_schema='public' AND tc.constraint_type='PRIMARY KEY' "
        "ORDER BY tc.table_name, kcu.ordinal_position"):
        pks.setdefault(tb, []).append(cn)

    kinds = {}
    for tb, kind in c.run(
        "SELECT table_name, table_type FROM information_schema.tables "
        "WHERE table_schema='public'"):
        kinds[tb] = "view" if kind == "VIEW" else "table"

    out = []
    for tb in sorted(kinds):
        if like and like not in tb:
            continue
        rec = {"name": tb, "kind": kinds[tb],
               "n_columns": len(cols_by_table.get(tb, [])),
               "primary_key": pks.get(tb, []),
               "columns": [dict(x, type=types_full.get((tb, x["column"]), "?"))
                           for x in cols_by_table.get(tb, [])]}
        if want_counts and kinds[tb] == "table":
            try:
                rec["rows"] = int(c.run('SELECT COUNT(*) FROM "%s"' % tb)[0][0])
            except Exception as e:
                rec["rows"] = "count failed: " + str(e)[:60]
        out.append(rec)

    tabs = [o for o in out if o["kind"] == "table"]
    empty = sorted(o["name"] for o in tabs if o.get("rows") == 0)
    return ok({"action": "catalog", "read_only": True,
               "n_tables": len(tabs), "n_views": len([o for o in out if o["kind"] == "view"]),
               "empty_tables": empty,
               "objects": out})


def depends(evt):
    """What still depends on a set of tables, before anyone drops them.

    WHY THIS EXISTS. A retirement list built from the repo alone has been
    wrong twice: on 20-Sep it marked seven live tables retirable because the
    PS2 loader RENAMES tables through its ALIASES map, and on 23-Sep
    ps2_network_centrality looked orphaned through /ps2/serial/network while
    V4 read it through /ps2/network. Greps resolve names; only the database
    resolves DEPENDENCIES. A view over a table is invisible to both.

    Read-only. Returns, per table:
      exists, rows, newest computed_date,
      dependent views and matviews (pg_depend, so rules and matviews count),
      inbound foreign keys.

    A table is safe to drop only when dependents and inbound_fks are both
    empty -- or when whatever they name is going in the same change.

        {"action": "depends", "tables": ["ps2_recurrence_serial", ...]}
    """
    c = conn()
    names = [str(t).strip() for t in ((evt or {}).get("tables") or [])]
    names = [t for t in names if re.fullmatch(r"[a-z][a-z0-9_]{0,62}", t)]
    if not names:
        return {"statusCode": 400, "body": json.dumps({
            "error": "pass tables: a list of lower-case identifiers",
            "example": {"action": "depends", "tables": ["ps2_recurrence_serial"]}})}

    out = {}
    for t in names:
        rec = {"exists": False, "rows": None, "computed_date": None,
               "dependents": [], "inbound_fks": []}
        try:
            rec["exists"] = bool(c.run(
                "SELECT to_regclass(:t) IS NOT NULL", t="public." + t)[0][0])
        except Exception as e:
            rec["error"] = str(e)[:200]
            out[t] = rec
            continue
        if not rec["exists"]:
            out[t] = rec
            continue

        # Views, matviews and rules that read it. pg_depend catches what
        # information_schema.view_table_usage does not.
        try:
            rec["dependents"] = [
                {"name": r[0], "kind": r[1]} for r in c.run(
                    "SELECT DISTINCT dep.relname, dep.relkind "
                    "  FROM pg_depend d "
                    "  JOIN pg_rewrite rw ON rw.oid = d.objid "
                    "  JOIN pg_class dep  ON dep.oid = rw.ev_class "
                    "  JOIN pg_class src  ON src.oid = d.refobjid "
                    " WHERE src.relname = :t AND dep.relname <> src.relname "
                    " ORDER BY 1", t=t)]
        except Exception as e:
            rec["dependents"] = "check failed: " + str(e)[:120]

        try:
            rec["inbound_fks"] = [
                {"table": r[0], "column": r[1]} for r in c.run(
                    "SELECT tc.table_name, kcu.column_name "
                    "  FROM information_schema.table_constraints tc "
                    "  JOIN information_schema.key_column_usage kcu "
                    "    ON kcu.constraint_name = tc.constraint_name "
                    "  JOIN information_schema.constraint_column_usage ccu "
                    "    ON ccu.constraint_name = tc.constraint_name "
                    " WHERE tc.constraint_type = 'FOREIGN KEY' "
                    "   AND ccu.table_name = :t", t=t)]
        except Exception as e:
            rec["inbound_fks"] = "check failed: " + str(e)[:120]

        try:
            rec["rows"] = int(c.run('SELECT COUNT(*) FROM "%s"' % t)[0][0])
        except Exception as e:
            rec["rows"] = "count failed: " + str(e)[:80]
        try:
            rec["computed_date"] = str(c.run(
                'SELECT MAX(computed_date) FROM "%s"' % t)[0][0])
        except Exception:
            rec["computed_date"] = None   # no such column; not an error here
        out[t] = rec

    # NECESSARY, NOT SUFFICIENT -- and the name used to say otherwise.
    # This reports the DATABASE's answer: views, matviews, rules and foreign
    # keys. It cannot see application code. On the first real use
    # ps2_leadlag_timing came back under the old key "safe_to_drop" while
    # /device/360's causation panel reads it directly, so a reader trusting
    # the key alone would have dropped a live table. Renamed to say exactly
    # what was checked; a drop still needs a separate code search.
    clear = sorted(t for t, r in out.items()
                   if r.get("exists") and not r.get("dependents")
                   and not r.get("inbound_fks"))
    blocked = sorted(t for t, r in out.items()
                     if r.get("exists") and (r.get("dependents") or r.get("inbound_fks")))
    return ok({"action": "depends", "read_only": True,
               "checked": len(out),
               "no_database_dependents": clear,
               "caveat": ("no_database_dependents means no view, matview, rule or FK "
                          "reads it. It does NOT mean no code reads it -- search the "
                          "handler and the dashboard before dropping."),
               "blocked_by_a_dependent": blocked,
               "absent": sorted(t for t, r in out.items() if not r.get("exists")),
               "detail": out})


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
    if isinstance(event, dict) and event.get("action") == "catalog":
        return catalog(event)
    if isinstance(event, dict) and event.get("action") == "inspect":
        return inspect(event)
    if isinstance(event, dict) and event.get("action") == "depends":
        return depends(event)
    if isinstance(event, dict) and event.get("action") == "recreate":
        return recreate(event)
    if isinstance(event, dict) and event.get("action") == "load_run":
        return load_run(event)
    if isinstance(event, dict) and event.get("action") == "apply_sql":
        return apply_sql(event)
    rc = (event or {}).get("requestContext", {}).get("http", {})
    method = rc.get("method", "GET"); path = event.get("rawPath", "/")
    params = event.get("queryStringParameters") or {}
    body = {}
    if event.get("body"):
        try: body = json.loads(event["body"])
        except Exception: body = {}
    try:
        return route(method, path, params, body, event.get("headers") or {})
    except Exception as e:
        return err(500, str(e)[:300])
