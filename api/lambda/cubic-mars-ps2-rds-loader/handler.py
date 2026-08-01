"""
cubic-mars-ps2-rds-loader
=========================
S3 -> this Lambda (inside the RDS VPC) -> Aurora ps2_* tables -> the dashboard.

  s3://cubic-mars-pm-s3-datalake-dev-artifacts-170202974600/
      ps2_outputs/<table>/computed_date=<date>/run_id=<uuid>/
          part-*.parquet
          manifest.json

VERIFIED against the bucket on 27-Jul-2026: 27 tables present, computed_date
2026-07-26, run_id b05f97ab-564c-42df-b3a3-478f08b79dd3. The notebook's own
comment says "push-Lambda watches this bucket's ps2_outputs/ prefix" -- this is
that Lambda, which was designed for and never built. Nothing needs re-running.

NOTE THE PREFIX IS AT THE BUCKET ROOT, not under chicago/. A sweep of chicago/
finds nothing and concludes PS2 never exported, which is wrong.

WHY THIS LOADER IS SCHEMA-ADAPTIVE
----------------------------------
27 source tables against a schema built at different times by different hands.
Hand-mapping 27 column lists would be 27 chances to typo a name that then fails
silently as a NULL column. Instead the loader:

  1. asks information_schema which ps2_* tables actually exist in Aurora
  2. reads the real column list of each target
  3. inserts the INTERSECTION of (parquet columns, target columns)
  4. reports, per table, exactly which columns matched and which were dropped

So a column the dashboard needs and the export does not carry shows up as a
named drop in the response, not as an empty chart three days later.

THE GUARD THAT MAKES THAT SAFE
------------------------------
Adaptive matching would happily load 1 column out of 12 and call it success. So
a table whose intersection covers less than MIN_MATCH of the target's columns is
REFUSED, on the reasoning that a low overlap means the names disagree -- a
mapping problem to fix, not a load to complete. Refusing is recoverable; a
quarter-populated table that looks loaded is not.

TABLES WITH NO AURORA HOME
--------------------------
Roughly half the 27 are serial-grain outputs (ps2_*_serial) with no table in the
schema yet. They are reported under "no_target" with their row counts so the DDL
can be written against real shapes rather than guessed ones. They are not
silently skipped.

TRIGGERS
  1. S3 ObjectCreated on ps2_outputs/*/manifest.json
  2. EventBridge schedule / {}   -> newest computed_date
  3. Manual {"computed_date": "2026-07-26", "dry_run": true}
     {"only": ["ps2_phi_matrix"]} to load one table

Runtime deps: pg8000 (bundled) + pyarrow (AWS SDK for pandas managed layer).
Env: ARTIFACT_BUCKET, PS2_PREFIX, RDS_HOST, RDS_PORT, RDS_SECRET_ID, CITY_ID
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
PS2_PREFIX = os.environ.get("PS2_PREFIX", "ps2_outputs")
RDS_SECRET_ID = os.environ.get("RDS_SECRET_ID", "cubic-mars-secret-rds-dev")
RDS_HOST = os.environ.get("RDS_HOST", "")
RDS_PORT = int(os.environ.get("RDS_PORT", "5432"))
CITY_ID = os.environ.get("CITY_ID", "CHI")

# Fraction of the target's columns the source must supply. Below this the names
# disagree and the load is refused rather than half-completed.
MIN_MATCH = float(os.environ.get("MIN_MATCH", "0.5"))

# Source table -> Aurora table, only where the names genuinely differ. Everything
# else maps to itself; the loader checks information_schema and reports anything
# it cannot place.
ALIASES = {
    "ps2_network_centrality_subsystem": "ps2_network_centrality",
    # 01-Aug-2026: alias removed. ignition_count is not ignition_days.
    # Loads to ps2_ignition_termination_subsystem (sql/42) under its own name.
    # 01-Aug-2026: alias removed. Per-facility detail is not the one-row rollup.
    # Loads to ps2_facility_contagion_facility (sql/42) under its own name.
    "ps2_association_rules_device": "ps2_subsystem_associations",
    "ps2_business_impact_device": "ps2_business_impact",
    "ps2_hmm_regimes_device": "ps2_hmm_regimes",
    "ps2_recurrence_device": "ps2_recurrence",
    "ps2_cascade_velocity_device": "ps2_cascade_velocity",
    "ps2_leadlag_timing_device": "ps2_leadlag_timing",
}

# Column renames, PER SOURCE TABLE. They were global in the first version and
# that was wrong in both directions: antecedents->antecedent_subsystem is right
# for ps2_association_rules_device and WRONG for ps2_association_rules_serial,
# whose target keeps the raw names -- the global rename silently dropped both
# columns there. A rename is only ever valid for a specific (source, target)
# pair, so that is how it is keyed now.
#
# Every entry below was read off the dry run's own columns_dropped /
# columns_missing lists against the real Aurora schema. None is guessed.
COL_ALIASES = {
    "ps2_association_rules_device": {
        "antecedents": "antecedent_subsystem",
        "consequents": "consequent_subsystem",
    },
    "ps2_business_impact_device": {
        # entity_id IS the device id. Without this the load put 4,673 rows into
        # ps2_business_impact with device_id NULL -- non-empty, and useless.
        "entity_id": "device_id",
        "mars_device_category": "category",
    },
    "ps2_hmm_regimes_device": {
        "state": "regime",
        "pct_time": "pct",
    },
    "ps2_network_centrality_subsystem": {
        "subsystem": "node_id",
        # scope is deliberately NOT aliased to role. The first version did that
        # and it is exactly what broke the load with
        #   duplicate key ... (city_id, node_id, computed_date)=(CHI, PRINTER, ...)
        # The source is 27 rows = ALL(10) + TVM(8) + GATE(5) + VALIDATOR(4), so
        # PRINTER exists four times, once per scope. Parking scope in `role` --
        # a non-key column meant for a node's graph role (hub vs bridge) -- kept
        # the VALUE while leaving four rows sharing one primary key.
        #
        # scope is part of the grain, so it belongs in the key. sql/26 adds a
        # real scope column and rebuilds the PK as
        # (city_id, node_id, scope, computed_date). No rename is needed here:
        # the source column is already called scope.
    },
    "ps2_cascade_velocity_by_age_serial": {"n": "n_events"},
    "ps2_leadlag_timing_serial": {"n": "n_events"},
    # 27-Jul-2026. The three tables the 27-Jul run reported as no_target. sql/27
    # creates them; these are the two renames that run needs.
    #
    # `window` -> `window_bucket` is not cosmetic. window is a reserved keyword
    # in PostgreSQL (it is the WINDOW clause), so a column of that name has to be
    # quoted in every statement that touches it forever after. The loader does
    # quote identifiers now, but the dashboard's route SQL is hand-written and
    # would not, so the name is fixed at the boundary instead.
    "ps2_cascade_velocity_device": {"window": "window_bucket", "n": "n_events"},
    "ps2_leadlag_timing_device": {"n": "n_events"},
    # ps2_recurrence_device needs no renames: device_id, cascade_days and
    # chronic already match sql/27.
}

# Columns that identify the row. If a target has one and the source cannot fill
# it, the load is REFUSED however good the overall coverage looks.
#
# This is the check that would have caught ps2_business_impact: 5 of 9 columns
# matched, which cleared the 50% coverage bar, while the one column that makes
# a row mean anything was NULL on every row. Percentage coverage is a proxy for
# correctness and this is the case where the proxy fails.
IDENTITY_COLS = ("device_id", "serial_id", "facility_id", "node_id",
                 "subsystem", "sub_a", "from_sub", "entity_id")

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
    # The SECRET decides the database. The PS4 loader hardcoded "postgres" from
    # an env var while the secret carries dbname="appdb", and every write failed
    # with relation-does-not-exist against the right cluster. Same order as the
    # dashboard-api so the two cannot drift.
    dbname = (sec.get("dbname") or sec.get("database")
              or os.environ.get("RDS_DATABASE") or "postgres")
    log.info("connecting to database=%s on %s", dbname, host)
    _conn = pg8000.native.Connection(
        user=sec.get("username", "postgres"), password=sec["password"], host=host,
        port=int(sec.get("port", RDS_PORT)), database=dbname,
        ssl_context=True, timeout=60)
    return _conn


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


def target_columns(c, table):
    """Real column list from information_schema, or None if the table is absent.

    Asked at runtime rather than hardcoded: 27 hand-written column lists is 27
    chances to typo a name that then loads as NULL and looks like missing data.
    """
    rows = c.run(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position",
        t=table)
    return [r[0] for r in rows] if rows else None


def pk_columns(c, table):
    """The target's primary-key columns. [] if the table has no PK.

    This exists because of a failure mode that costs a whole batch. The loader
    keeps the intersection of source and target columns; any source column the
    target lacks is DROPPED. If that column was part of the source's grain,
    distinct rows collapse onto one primary key and Postgres raises 23505 --
    mid-transaction, after N tables are already staged, so the ROLLBACK discards
    all of them. One unmapped column took down twenty-one healthy tables.

    Knowing the PK lets the loader detect the collapse in Python BEFORE the
    INSERT, refuse that one table with the offending column named, and let
    everything else commit.
    """
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
    apart -- the ones that were dropped and should not have been. That turns a
    cryptic 23505 into a specific instruction about which column the schema is
    missing.
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


def list_tables(bucket, prefix):
    """The <table> names directly under the prefix."""
    out, token = set(), None
    prefix = prefix.rstrip("/") + "/"
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix, "Delimiter": "/"}
        if token:
            kw["ContinuationToken"] = token
        r = _s3.list_objects_v2(**kw)
        for cp in r.get("CommonPrefixes", []):
            out.add(cp["Prefix"].rstrip("/").rsplit("/", 1)[-1])
        if not r.get("IsTruncated"):
            break
        token = r.get("NextContinuationToken")
    return sorted(out)


def latest_partition(bucket, prefix, table):
    """Newest computed_date=<d>/run_id=<r> under one table. Returns (date, key_prefix)."""
    best = None
    token = None
    root = f"{prefix.rstrip('/')}/{table}/"
    while True:
        kw = {"Bucket": bucket, "Prefix": root}
        if token:
            kw["ContinuationToken"] = token
        r = _s3.list_objects_v2(**kw)
        for o in r.get("Contents", []):
            m = re.search(r"computed_date=([\d-]+)/run_id=([0-9a-f\-]+)/", o["Key"])
            if m:
                cand = (m.group(1), m.group(2))
                if best is None or cand > best:
                    best = cand
        if not r.get("IsTruncated"):
            break
        token = r.get("NextContinuationToken")
    if not best:
        return None, None
    return best[0], f"{root}computed_date={best[0]}/run_id={best[1]}/"


def read_parts(bucket, key_prefix):
    import pyarrow.parquet as pq
    rows, token = [], None
    keys = []
    while True:
        kw = {"Bucket": bucket, "Prefix": key_prefix}
        if token:
            kw["ContinuationToken"] = token
        r = _s3.list_objects_v2(**kw)
        keys += [o["Key"] for o in r.get("Contents", [])
                 if o["Key"].endswith(".parquet")]
        if not r.get("IsTruncated"):
            break
        token = r.get("NextContinuationToken")
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
    res = {"dry_run": dry, "city": city, "loaded": {}, "no_target": {},
           "refused": {}, "skipped": {}, "errors": {}}

    c = conn()
    tables = list_tables(ARTIFACT_BUCKET, PS2_PREFIX)
    res["tables_in_s3"] = len(tables)
    if not tables:
        res["status"] = "failed"
        res["error"] = f"nothing under s3://{ARTIFACT_BUCKET}/{PS2_PREFIX}/"
        return {"statusCode": 500, "body": json.dumps(res)}

    if not dry:
        c.run("BEGIN")
    try:
        for src_table in tables:
            if only and src_table not in only:
                continue
            tgt = ALIASES.get(src_table, src_table)
            tcols = target_columns(c, tgt)
            cdate, kp = latest_partition(ARTIFACT_BUCKET, PS2_PREFIX, src_table)

            if tcols is None:
                # No Aurora table. Report the row count so DDL can be written
                # against a real shape instead of a guessed one.
                n = 0
                if kp:
                    rows, _ = read_parts(ARTIFACT_BUCKET, kp)
                    n = len(rows)
                    res["no_target"][src_table] = {
                        "rows_in_s3": n, "computed_date": cdate,
                        "columns": sorted(rows[0].keys()) if rows else []}
                else:
                    res["no_target"][src_table] = {"rows_in_s3": 0}
                continue

            if not kp:
                res["skipped"][src_table] = "no computed_date= partition"
                continue

            rows, nparts = read_parts(ARTIFACT_BUCKET, kp)
            if not rows:
                res["skipped"][src_table] = f"0 rows under {kp}"
                continue

            # rename, then intersect
            aliases = COL_ALIASES.get(src_table, {})
            shaped = []
            for r in rows:
                o = {}
                for k, v in r.items():
                    o[aliases.get(k, k)] = v
                o.setdefault("city_id", city)
                if "computed_date" in tcols:
                    o.setdefault("computed_date", cdate)
                shaped.append(o)

            src_cols = set(shaped[0].keys())
            use = [x for x in tcols if x in src_cols]
            dropped = sorted(src_cols - set(use))
            missing = sorted(set(tcols) - src_cols)

            # id columns are serial/generated -- never insert them
            use = [x for x in use if x != "id"]

            # Identity check first: a row without its key is not a row, and no
            # coverage percentage redeems that.
            id_missing = [x for x in tcols
                          if x in IDENTITY_COLS and x not in use]
            if id_missing:
                res["refused"][src_table] = {
                    "reason": (f"identity column(s) {id_missing} exist on {tgt} but "
                               f"nothing in the source fills them -- every row would "
                               f"carry NULL where the key belongs. Add a COL_ALIASES "
                               f"entry for {src_table}."),
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

            # Grain check. The two checks above ask whether the source can fill
            # the target's columns. This one asks the opposite question: whether
            # trimming to those columns has thrown away what made the rows
            # distinct. ps2_network_centrality is the case -- 27 rows over four
            # scopes, PK without scope, so PRINTER collides with PRINTER.
            #
            # Reported here rather than discovered by Postgres, because Postgres
            # discovers it as a 23505 that aborts the transaction and rolls back
            # every table already staged.
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
                        f"have. This is a schema fix (add the column and put it in "
                        f"the PK), not a mapping fix."),
                    **collapse}
                continue

            if not dry:
                # SAVEPOINT per table. Without it any single table's error aborts
                # the whole transaction and the other twenty-one are rolled back
                # with it. With it, a bad table is reported and skipped and the
                # rest still commit.
                sp = "sp_" + re.sub(r"\W", "_", src_table)[:50]
                c.run(f"SAVEPOINT {sp}")
                try:
                    c.run(f"DELETE FROM {tgt} WHERE city_id=:c", c=city)
                    for i in range(0, len(shaped), 500):
                        chunk = shaped[i:i + 500]
                        ph, args = [], {}
                        for ri, r in enumerate(chunk):
                            names = [f"p{ri}_{ci}" for ci in range(len(use))]
                            ph.append("(" + ", ".join(":" + x for x in names) + ")")
                            for nm, col in zip(names, use):
                                args[nm] = norm(r.get(col))
                        # Quote EVERY column identifier. "window" is a reserved
                        # keyword in PostgreSQL and blew up this INSERT with
                        #     syntax error at or near "window"
                        # after 8 tables had already been staged. It is not alone:
                        # rank, role, note, family, end and order all appear in
                        # these exports and are all reserved. Quoting the whole
                        # list costs nothing and removes the entire class rather
                        # than the one instance that happened to fail first.
                        quoted = ", ".join('"' + x + '"' for x in use)
                        c.run(f"INSERT INTO {tgt} ({quoted}) VALUES "
                              + ", ".join(ph), **args)
                    c.run(f"RELEASE SAVEPOINT {sp}")
                except Exception as te:
                    c.run(f"ROLLBACK TO SAVEPOINT {sp}")
                    res["errors"][src_table] = {
                        "table": tgt, "rows": len(shaped),
                        "columns_used": use, "error": str(te)[:400]}
                    continue

            res["loaded"][src_table] = {
                "table": tgt, "computed_date": cdate, "parts": nparts,
                "rows": len(shaped), "columns_used": use,
                "columns_dropped": dropped, "columns_missing": missing}

        if not dry:
            c.run("COMMIT")
        res["status"] = "dry_run_ok" if dry else "committed"
    except Exception as e:
        if not dry:
            try:
                c.run("ROLLBACK")
            except Exception:
                pass
        log.exception("ps2 load failed")
        res["status"] = "failed"
        res["error"] = str(e)[:600]

    res["summary"] = {
        "loaded": len(res["loaded"]), "no_target": len(res["no_target"]),
        "refused": len(res["refused"]), "skipped": len(res["skipped"]),
        "errors": len(res["errors"]),
        "rows": sum(v.get("rows", 0) for v in res["loaded"].values())}

    # Two log lines, and the order matters. The full dump is truncated at 3000
    # chars, and res["status"] is serialised AFTER the per-table detail -- so on
    # a 22-table run the status was being cut off and the log could not answer
    # "did it commit". The compact line goes FIRST and always survives.
    log.info("PS2 %s %s", res.get("status"), json.dumps(res["summary"]))
    log.info(json.dumps(res, default=str)[:3000])
    return {"statusCode": 200 if res.get("status") != "failed" else 500,
            "body": json.dumps(res, default=str)}
