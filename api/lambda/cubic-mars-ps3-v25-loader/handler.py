"""
cubic-mars-ps3-v25-loader
=========================
S3 -> this Lambda (inside the RDS VPC) -> Aurora ps3_v25_* tables -> the dashboard.

  s3://cubic-mars-pm-s3-datalake-dev-artifacts-170202974600/
      ps3_outputs/<table>/computed_date=<date>/run_id=<uuid>/part-00000.parquet
      ps3_run_control/computed_date=<date>/run_id=<uuid>/manifest.json

WHY A NEW LAMBDA RATHER THAN A CHANGE TO AN EXISTING ONE
--------------------------------------------------------
cubic-mars-ps3-v2-loader and cubic-mars-ps3-rds-push keep filling
ps3_v2_* / ps3_incident_predictions / ps3_device_predictions, which is what the
deployed PS3 screens read today. Nothing here touches them. This Lambda writes
only to tables named ps3_v25_*, and refuses -- structurally, in
target_table() -- to write to any table whose name does not start with that
prefix. Rolling back is "stop invoking this Lambda"; the plan-B feed never
stopped running.

WHAT IS BORROWED VERBATIM FROM cubic-mars-ps2-rds-loader
--------------------------------------------------------
The schema-adaptive body: ask information_schema for each target's real column
list, insert the intersection, refuse on low coverage, refuse on a missing
identity column, and detect a primary-key collapse in Python before the INSERT
so one bad table cannot roll back the other nineteen. Every one of those guards
exists because it caught a real failure on PS2, and PS3 publishes twenty tables
into a schema written by the same hands, so it has the same failure modes.

THE ONE GUARD PS2 DOES NOT HAVE, ADDED HERE
--------------------------------------------
PS2's loader picks the newest partition PER TABLE independently. If one table
fails to publish, the loader silently pairs nineteen tables from today's run
with one from last week's, and every cross-table join in the dashboard is then
quietly wrong. PS3 runs as one transaction in the notebook and stamps one
run_id across all twenty tables, so this loader picks ONE (computed_date,
run_id) globally and REFUSES the whole load if any expected table is missing
from it. A half-loaded dashboard is worse than a stale one, because stale is
visible on /ps3/status and half-loaded is not.

TRIGGERS
  1. S3 ObjectCreated on ps3_outputs/ps3_run_control/*/manifest.json
  2. EventBridge schedule / {}      -> newest complete run
  3. {"dry_run": true}              -> read, shape, check, write nothing
     {"run_id": "...", "computed_date": "2026-04-11"}  -> pin one run
     {"only": ["ps3_device_day"]}   -> one table

Runtime deps: pg8000 (bundled) + pyarrow (AWS SDK for pandas managed layer).
Env: ARTIFACT_BUCKET, PS3_PREFIX, RDS_HOST, RDS_PORT, RDS_SECRET_ID, CITY_ID,
     MIN_MATCH, PG_TIMEOUT, CHUNK_ROWS, COPY_MIN_ROWS, MAX_BIND_PARAMS

HOW ROWS ACTUALLY GET IN
------------------------
COPY above COPY_MIN_ROWS, multi-row INSERT below it, and INSERT again as the
fallback if COPY fails. Measured on the widest table (54,239 rows x 79 columns)
against a local PostgreSQL 16: INSERT had not finished after eight minutes;
COPY took 28.9s. The 900s Lambda ceiling has to cover all twenty tables, so
that is the difference between comfortable and a coin toss.
"""
import io
import json
import logging
import os
import re

import boto3
import pg8000.native

log = logging.getLogger()
log.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-east-1")
ARTIFACT_BUCKET = os.environ.get(
    "ARTIFACT_BUCKET", "cubic-mars-pm-s3-datalake-dev-artifacts-170202974600")
# ps3_replay_outputs is what a REPLAY run writes. Production runs write
# ps3_outputs. The notebook enforces that the prefix tail is one of exactly
# those two, so pointing this at anything else is a configuration error.
PS3_PREFIX = os.environ.get("PS3_PREFIX", "ps3_outputs")
RDS_SECRET_ID = os.environ.get("RDS_SECRET_ID", "cubic-mars-secret-rds-dev")
RDS_HOST = os.environ.get("RDS_HOST", "")
RDS_PORT = int(os.environ.get("RDS_PORT", "5432"))
CITY_ID = os.environ.get("CITY_ID", "CHI")

MIN_MATCH = float(os.environ.get("MIN_MATCH", "0.5"))

# 60s killed the first PS2 v2.5.3 load. This is a per-operation socket timeout,
# not a total budget -- and with COPY the widest table is a SINGLE operation, so
# the whole 54,239-row transfer has to fit inside it. 60s would not have.
PG_TIMEOUT = int(os.environ.get("PG_TIMEOUT", "300"))

# Only the INSERT path uses these. 500 rows x 79 columns = 39,500 bound
# parameters per statement, against PostgreSQL's wire-protocol cap of 65,535 --
# inside the limit, but not by a margin worth trusting to a schema change. The
# chunk is sized per table below rather than fixed.
MAX_BIND_PARAMS = int(os.environ.get("MAX_BIND_PARAMS", "30000"))
CHUNK_ROWS = int(os.environ.get("CHUNK_ROWS", "500"))

# Above this row count a table is loaded with COPY instead of multi-row INSERT.
# Below it the INSERT path's per-chunk error reporting is worth more than the
# speed, and the difference is under a second either way.
COPY_MIN_ROWS = int(os.environ.get("COPY_MIN_ROWS", "5000"))

# The twenty tables V26 publishes. Named, not discovered, because the point of
# the completeness gate is to notice a table that ISN'T there -- and a gate
# built from whatever happens to be in the bucket can never do that.
EXPECTED_TABLES = (
    "ps3_causal_balance", "ps3_causal_effects", "ps3_commanded_split",
    "ps3_component_summary", "ps3_device_day", "ps3_device_episode_fact",
    "ps3_device_reliability", "ps3_device_summary", "ps3_facility_rollup",
    "ps3_label_maturity", "ps3_model_feature_importance", "ps3_model_scorecard",
    "ps3_oos_source_audit", "ps3_prediction_explainability",
    "ps3_repeat_interval", "ps3_root_cause_evidence_audit",
    "ps3_run_stage_audit", "ps3_run_status", "ps3_serial_reliability",
    "ps3_source_column_profile",
)

# ps3_run_control holds the run manifest, not a dashboard table. It lives under
# the same prefix and must never be treated as a load target.
NON_TABLE_PREFIXES = {"ps3_run_control"}

# Columns that identify the row in the PS3 schema. If a target declares one and
# the source cannot fill it, the load is refused however good the coverage looks
# -- PS2's ps2_business_impact cleared the 50% bar with device_id NULL on every
# row, which is non-empty and useless.
IDENTITY_COLS = ("device_id", "facility_id", "oos_episode_id",
                 "component_serial_nbr", "mars_device_category",
                 "component_attribution", "treatment_component")

# No column renames. Unlike PS2 -- where 27 tables were built by different hands
# at different times -- every PS3 v25 target column was generated FROM this
# run's own schema dump, so the names match by construction. An empty map here
# is a statement, not an omission: if a rename ever becomes necessary it means
# the DDL and the notebook have drifted, and the fix belongs at that seam.
COL_ALIASES = {}

_s3 = boto3.client("s3", region_name=REGION)
_sec = boto3.client("secretsmanager", region_name=REGION)
_conn = None


def conn():
    global _conn
    if _conn is not None:
        return _conn
    sec = json.loads(_sec.get_secret_value(SecretId=RDS_SECRET_ID)["SecretString"])
    host = RDS_HOST or sec.get("host")
    if not host:
        raise RuntimeError("RDS_HOST unset and the secret carries no host")
    # The SECRET decides the database, in the same order as the dashboard-api,
    # so the two cannot drift. The PS4 loader hardcoded "postgres" from an env
    # var while the secret carries dbname="appdb" and every write failed with
    # relation-does-not-exist against the right cluster.
    dbname = (sec.get("dbname") or sec.get("database")
              or os.environ.get("RDS_DATABASE") or "postgres")
    log.info("connecting to database=%s on %s (timeout=%ss)", dbname, host, PG_TIMEOUT)
    _conn = pg8000.native.Connection(
        user=sec.get("username", "postgres"), password=sec["password"], host=host,
        port=int(sec.get("port", RDS_PORT)), database=dbname,
        ssl_context=True, timeout=PG_TIMEOUT)
    return _conn


def target_table(src_table):
    """ps3_<x> -> ps3_v25_<x>, and nothing else is reachable.

    Derived rather than hand-listed: twenty hand-written mappings is twenty
    chances to typo one into an existing plan-B table name. Returns None for
    anything that does not fit the pattern, so an unexpected prefix in the
    bucket is reported rather than routed somewhere.
    """
    if not src_table.startswith("ps3_") or src_table in NON_TABLE_PREFIXES:
        return None
    tgt = "ps3_v25_" + src_table[len("ps3_"):]
    # Belt and braces. If the line above is ever edited, this is what stops the
    # loader writing into the deployed PS3 tables.
    return tgt if tgt.startswith("ps3_v25_") else None


def norm(v):
    if v is None:
        return None
    if isinstance(v, float) and v != v:
        return None
    if isinstance(v, str):
        s = v.strip()
        return None if s == "" or s.lower() in ("nan", "none", "null", "<na>") else s
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def csv_cell(v):
    """One CSV field for COPY, with NULL and empty-string kept distinct.

    The csv module cannot express this: it quotes only when a value contains a
    delimiter, so an empty string and a NULL both come out as an empty field.
    Here None is the ONLY thing written unquoted-empty, and every other value is
    always quoted -- so '' round-trips as an empty string and None as NULL.
    PostgreSQL accepts a quoted numeric in CSV, so quoting everything costs
    nothing but removes the whole class of ambiguity.
    """
    if v is None:
        return ""
    if v is True:
        return '"true"'
    if v is False:
        return '"false"'
    return '"' + str(v).replace('"', '""') + '"'


def copy_rows(c, table, cols, rows):
    """COPY ... FROM STDIN. Returns the row count, or raises.

    Multi-row INSERT costs one round trip per chunk and one parse per statement.
    On the widest table -- 54,239 rows over 77 columns, 141 statements -- that
    was measured at minutes, against a 900s Lambda ceiling that also has to
    cover nineteen other tables. COPY is one round trip.

    The trade is error granularity: COPY reports the first bad row and aborts,
    where INSERT reports the failing chunk. That is why the caller falls back to
    the INSERT path on any COPY failure -- the fast path is for speed, the slow
    path is for diagnosis, and a table only ever pays for the slow path when
    something is actually wrong with it.
    """
    buf = io.StringIO()
    for r in rows:
        buf.write(",".join(csv_cell(norm(r.get(col))) for col in cols))
        buf.write("\n")
    buf.seek(0)
    quoted = ", ".join('"' + x + '"' for x in cols)
    c.run(f"COPY {table} ({quoted}) FROM STDIN WITH (FORMAT csv)", stream=buf)
    return len(rows)


def target_columns(c, table):
    rows = c.run(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position",
        t=table)
    return [r[0] for r in rows] if rows else None


def json_columns(c, table):
    """Targets typed jsonb. pg8000 sends a python list as an array literal,
    which Postgres rejects for a jsonb column, so those values are dumped to a
    JSON string at the boundary. ps3_run_stage_audit.model_runs is the only one
    today; asked from the catalog so a second one needs no code change."""
    rows = c.run(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t AND data_type='jsonb'",
        t=table)
    return {r[0] for r in rows} if rows else set()


def pk_columns(c, table):
    rows = c.run(
        "SELECT a.attname FROM pg_index i "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid "
        "                   AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = to_regclass('public.' || :t) AND i.indisprimary",
        t=table)
    return [r[0] for r in rows] if rows else []


def find_pk_collapse(shaped, use, pk):
    """Report a PK collision among `shaped` once trimmed to `use`, else None.

    separating_columns are the source columns that DO tell the colliding rows
    apart -- the ones dropped that should not have been. That turns a cryptic
    23505 into a specific instruction about which column the schema is missing.
    """
    keys = [k for k in pk if k in use]
    if not keys or not shaped:
        return None
    seen, dup = {}, None
    for r in shaped:
        k = tuple(norm(r.get(x)) for x in keys)
        if k in seen and dup is None:
            a = seen[k]
            dup = {
                "pk": keys,
                "example_key": [str(x) for x in k],
                "separating_columns": sorted(
                    col for col in set(a) | set(r)
                    if col not in keys and norm(a.get(col)) != norm(r.get(col))),
            }
        seen[k] = r
    if dup:
        dup["rows_in"] = len(shaped)
        dup["distinct_keys"] = len(seen)
        dup["rows_lost_if_forced"] = len(shaped) - len(seen)
    return dup


def _list(bucket, prefix, delimiter=None):
    token = None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if delimiter:
            kw["Delimiter"] = delimiter
        if token:
            kw["ContinuationToken"] = token
        r = _s3.list_objects_v2(**kw)
        yield r
        if not r.get("IsTruncated"):
            return
        token = r.get("NextContinuationToken")


_PART_RE = re.compile(r"/([^/]+)/computed_date=([\d-]+)/run_id=([0-9a-f\-]+)/")


def survey(bucket, prefix):
    """Every (table, computed_date, run_id) present under the prefix.

    One pass over the whole prefix rather than one listing per table: twenty
    tables is twenty round trips, and they have to be reconciled against each
    other anyway to answer "is this run complete".
    """
    root = prefix.rstrip("/") + "/"
    found = {}
    for page in _list(bucket, root):
        for o in page.get("Contents", []):
            m = _PART_RE.search(o["Key"][len(root) - 1:] if o["Key"].startswith(root) else o["Key"])
            if not m:
                continue
            table, cdate, rid = m.group(1), m.group(2), m.group(3)
            found.setdefault((cdate, rid), set()).add(table)
    return found


def choose_run(found, want_date=None, want_run=None):
    """The newest run that carries every expected table, plus why others lost.

    The run id's first eight hex characters are epoch seconds, so within one
    computed_date the lexical max is the chronological max. Sorting on
    (computed_date, run_id) is therefore a real ordering, not an alphabetical
    accident.
    """
    report = []
    for (cdate, rid) in sorted(found, reverse=True):
        tables = found[(cdate, rid)]
        missing = sorted(set(EXPECTED_TABLES) - tables)
        extra = sorted(tables - set(EXPECTED_TABLES) - NON_TABLE_PREFIXES)
        entry = {"computed_date": cdate, "run_id": rid,
                 "tables_present": len(tables & set(EXPECTED_TABLES)),
                 "missing": missing, "unexpected": extra}
        if want_date and cdate != want_date:
            entry["skipped"] = "computed_date not requested"
        elif want_run and rid != want_run:
            entry["skipped"] = "run_id not requested"
        elif missing:
            entry["skipped"] = f"incomplete -- {len(missing)} expected table(s) absent"
        else:
            report.append(entry)
            return (cdate, rid), report
        report.append(entry)
    return None, report


def read_parts(bucket, key_prefix):
    import pyarrow.parquet as pq
    rows, keys = [], []
    for page in _list(bucket, key_prefix):
        keys += [o["Key"] for o in page.get("Contents", [])
                 if o["Key"].endswith(".parquet")]
    for k in sorted(keys):
        body = _s3.get_object(Bucket=bucket, Key=k)["Body"].read()
        tbl = pq.read_table(io.BytesIO(body))
        cols = tbl.column_names
        cells = {c: tbl.column(c).to_pylist() for c in cols}
        rows.extend({c: cells[c][i] for c in cols} for i in range(tbl.num_rows))
        del tbl, cells, body
    return rows, len(keys)


def lambda_handler(event, context):
    event = event or {}
    dry = bool(event.get("dry_run"))
    city = (event.get("city") or CITY_ID).upper()
    only = set(event.get("only") or [])
    res = {"dry_run": dry, "city": city, "prefix": f"s3://{ARTIFACT_BUCKET}/{PS3_PREFIX}",
           "loaded": {}, "no_target": {}, "refused": {}, "skipped": {}, "errors": {}}

    found = survey(ARTIFACT_BUCKET, PS3_PREFIX)
    if not found:
        res["status"] = "failed"
        res["error"] = f"no computed_date=/run_id= partitions under {res['prefix']}/"
        return {"statusCode": 500, "body": json.dumps(res)}

    chosen, run_report = choose_run(found, event.get("computed_date"), event.get("run_id"))
    res["runs_considered"] = run_report[:8]
    if not chosen:
        # Deliberately fatal. Loading nineteen of twenty tables leaves the
        # dashboard internally inconsistent in a way /ps3/status cannot show,
        # because every table it can see agrees.
        res["status"] = "failed"
        res["error"] = ("no run under the prefix carries all "
                        f"{len(EXPECTED_TABLES)} expected tables; see runs_considered. "
                        "Refusing rather than loading a partial run.")
        return {"statusCode": 500, "body": json.dumps(res, default=str)}

    cdate, run_id = chosen
    res["computed_date"], res["run_id"] = cdate, run_id

    c = conn()
    if not dry:
        c.run("BEGIN")
    try:
        for src_table in EXPECTED_TABLES:
            if only and src_table not in only:
                continue
            tgt = target_table(src_table)
            if tgt is None:
                res["skipped"][src_table] = "does not map to a ps3_v25_ target"
                continue
            tcols = target_columns(c, tgt)
            kp = f"{PS3_PREFIX.rstrip('/')}/{src_table}/computed_date={cdate}/run_id={run_id}/"

            if tcols is None:
                rows, _ = read_parts(ARTIFACT_BUCKET, kp)
                res["no_target"][src_table] = {
                    "expected_table": tgt, "rows_in_s3": len(rows),
                    "columns": sorted(rows[0].keys()) if rows else []}
                continue

            rows, nparts = read_parts(ARTIFACT_BUCKET, kp)
            if not rows:
                res["skipped"][src_table] = f"0 rows under {kp}"
                continue

            aliases = COL_ALIASES.get(src_table, {})
            jsonb = json_columns(c, tgt)
            shaped = []
            for r in rows:
                o = {aliases.get(k, k): v for k, v in r.items()} if aliases else dict(r)
                o.setdefault("city_id", city)
                if "computed_date" in tcols:
                    o["computed_date"] = cdate
                for jc in jsonb:
                    if jc in o and not isinstance(o[jc], (str, type(None))):
                        o[jc] = json.dumps(o[jc], default=str)
                shaped.append(o)
            del rows

            src_cols = set(shaped[0].keys())
            use = [x for x in tcols if x in src_cols]
            dropped = sorted(src_cols - set(use))
            missing = sorted(set(tcols) - src_cols)
            use = [x for x in use if x != "id"]

            id_missing = [x for x in tcols if x in IDENTITY_COLS and x not in use]
            if id_missing:
                res["refused"][src_table] = {
                    "reason": (f"identity column(s) {id_missing} exist on {tgt} but "
                               f"nothing in the source fills them -- every row would "
                               f"carry NULL where the key belongs."),
                    "matched": use, "unused_from_source": dropped}
                continue

            cover = len(use) / max(1, len([x for x in tcols if x != "id"]))
            if cover < MIN_MATCH:
                res["refused"][src_table] = {
                    "reason": (f"only {len(use)}/{len(tcols)} target columns matched "
                               f"({cover:.0%} < {MIN_MATCH:.0%}) -- the names disagree, "
                               f"this is a mapping fix not a load"),
                    "matched": use, "missing_in_source": missing,
                    "unused_from_source": dropped}
                continue

            collapse = find_pk_collapse(shaped, use, pk_columns(c, tgt))
            if collapse:
                res["refused"][src_table] = {
                    "reason": (
                        f"{collapse['rows_in']} source rows collapse to "
                        f"{collapse['distinct_keys']} distinct values of {tgt}'s "
                        f"primary key {collapse['pk']} -- "
                        f"{collapse['rows_lost_if_forced']} rows would be lost or "
                        f"rejected. The column(s) that separate them are "
                        f"{collapse['separating_columns']}, which {tgt} does not "
                        f"have. This is a schema fix, not a mapping fix."),
                    **collapse}
                continue

            # Keep one statement inside PostgreSQL's 65,535 bound-parameter cap
            # with room to spare. 77 columns x 500 rows would be 38,500, which
            # fits -- but the cap is per statement and silent until it isn't.
            chunk_rows = max(1, min(CHUNK_ROWS, MAX_BIND_PARAMS // max(1, len(use))))

            if not dry:
                # SAVEPOINT per table: without it one table's error aborts the
                # whole transaction and the other nineteen roll back with it.
                sp = "sp_" + re.sub(r"\W", "_", src_table)[:50]
                c.run(f"SAVEPOINT {sp}")
                try:
                    c.run(f"DELETE FROM {tgt} WHERE city_id=:c", c=city)
                    quoted = ", ".join('"' + x + '"' for x in use)
                    if len(shaped) >= COPY_MIN_ROWS:
                        try:
                            copy_rows(c, tgt, use, shaped)
                            method = "copy"
                        except Exception as ce:
                            # Undo the partial COPY, then redo the table the slow
                            # way so the error names a chunk instead of the whole
                            # table. If the data is genuinely bad the INSERT will
                            # fail too, with something specific to act on.
                            c.run(f"ROLLBACK TO SAVEPOINT {sp}")
                            c.run(f"DELETE FROM {tgt} WHERE city_id=:c", c=city)
                            copy_error = str(ce)[:300]
                            method = "insert_after_copy_failed"
                        else:
                            copy_error = None
                    else:
                        method, copy_error = "insert", None
                    if method != "copy":
                        for i in range(0, len(shaped), chunk_rows):
                            chunk = shaped[i:i + chunk_rows]
                            ph, args = [], {}
                            for ri, r in enumerate(chunk):
                                names = [f"p{ri}_{ci}" for ci in range(len(use))]
                                ph.append("(" + ", ".join(":" + x for x in names) + ")")
                                for nm, col in zip(names, use):
                                    args[nm] = norm(r.get(col))
                            c.run(f"INSERT INTO {tgt} ({quoted}) VALUES " + ", ".join(ph), **args)
                    c.run(f"RELEASE SAVEPOINT {sp}")
                except Exception as te:
                    c.run(f"ROLLBACK TO SAVEPOINT {sp}")
                    res["errors"][src_table] = {
                        "table": tgt, "rows": len(shaped),
                        "columns_used": len(use), "error": str(te)[:400]}
                    continue

            entry = {"table": tgt, "computed_date": cdate, "parts": nparts,
                     "rows": len(shaped), "columns_used": len(use),
                     "columns_dropped": dropped, "columns_missing": missing}
            if not dry:
                entry["method"] = method
                entry["chunk_rows"] = chunk_rows if method != "copy" else None
                if copy_error:
                    # Never silent. A COPY that fell back still loaded the table,
                    # but the reason it fell back is a real finding about the data.
                    entry["copy_fallback_reason"] = copy_error
            res["loaded"][src_table] = entry
            del shaped

        if not dry:
            c.run("COMMIT")
        res["status"] = "dry_run_ok" if dry else "committed"
    except Exception as e:
        if not dry:
            try:
                c.run("ROLLBACK")
            except Exception:
                pass
        log.exception("ps3 v25 load failed")
        res["status"] = "failed"
        res["error"] = str(e)[:600]

    res["summary"] = {
        "expected": len(EXPECTED_TABLES), "loaded": len(res["loaded"]),
        "no_target": len(res["no_target"]), "refused": len(res["refused"]),
        "skipped": len(res["skipped"]), "errors": len(res["errors"]),
        "rows": sum(v.get("rows", 0) for v in res["loaded"].values())}

    # Compact line FIRST. The full dump is truncated at 3000 chars and on a
    # twenty-table run the status was being cut off, so the log could not
    # answer "did it commit".
    log.info("PS3v25 %s %s", res.get("status"), json.dumps(res["summary"]))
    log.info(json.dumps(res, default=str)[:3000])
    return {"statusCode": 200 if res.get("status") != "failed" else 500,
            "body": json.dumps(res, default=str)}
