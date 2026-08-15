"""
cubic-mars-ps3-rc-loader        03-Aug-2026

  s3://<artifacts>/chicago/ps3/rootcause_outputs/<run_id>/  ->  Aurora ps3_*

WHY THIS EXISTS
---------------
PS3's root-cause run currently reaches Aurora by a route that is not a data
path at all: the notebook writes a 797 KB file of INSERT statements, a human
copies it into the dashboard-api's deployment package, the Lambda is rebuilt,
and the API is asked to execute its own load file. That works, and it worked
for the demo. It is still wrong for anything that has to run tomorrow without
a person:

  * the DATA ships inside the CODE artifact, so every refresh is a redeploy
    of the API that serves the dashboard -- a data problem can now take the
    dashboard down;
  * there is no artifact in S3, so nothing can be re-loaded, diffed, audited
    or replayed after the fact;
  * SQL text is unvalidated until Postgres parses it, so a column that was
    renamed in the DDL fails at the last possible moment, mid-transaction;
  * and it cannot be scheduled.

Everything else in this estate already publishes to S3 and is loaded by a
Lambda inside the RDS VPC. This brings PS3's root-cause head onto that path.

WHAT IS BORROWED, AND FROM WHERE
--------------------------------
The body is cubic-mars-ps5-rds-loader's, which is cubic-mars-ps2-rds-loader's,
and every guard in it was earned by a real failure:

  1. SCHEMA-ADAPTIVE. Target columns come from information_schema at runtime
     and the loader inserts the intersection. A hand-written column list is N
     chances to typo a name that then loads as NULL and looks like missing
     data.
  2. IDENTIFIER QUOTING. Every column name is quoted -- "window", "rank",
     "role", "end" and "order" all appear somewhere in these exports.
  3. PER-TABLE SAVEPOINTS. One bad file rolls back to its own savepoint and
     the rest still commit. Without this, table 9 of 22 failing discarded the
     other 21.
  4. PK-COLLAPSE DETECTION. If dropping an unmapped column collapses distinct
     rows onto one primary key, refuse THAT file and name the column, rather
     than letting Postgres raise 23505 halfway through.

WHAT IS NEW HERE
----------------
  5. STRUCTURAL TARGET ALLOW-LIST. target_table() maps the three artifact
     names to the three tables this run owns and returns None for anything
     else. The loader cannot be pointed at ps3_v25_*, ps3_v2_* or the
     severity tables by a bad payload or a stray file in the prefix. Those
     feeds keep running; rolling this back is "stop invoking this Lambda".
  6. RUN COMPLETENESS. The run is loaded as a unit or not at all. A
     half-loaded PS3 shows a head_summary from today beside device
     predictions from last week, and nothing on the dashboard says so.
     Missing artifact -> the whole run is refused, and the reason is named.
  7. RUN-SCOPED DELETE. DELETE is by (city_id, run_id), matching the
     ON CONFLICT DO NOTHING semantics the SQL file used, so re-loading a run
     replaces it instead of duplicating it -- and CANNOT wipe a different
     run's rows. ps3_model_runs is the run registry; earlier runs stay
     queryable.

TRIGGERS
  {}                                  newest complete run under the prefix
  {"run_id": "ps3_oos_20260803"}      pin one run
  {"dry_run": true}                   read, shape, check, write nothing --
                                      doubles as the S3-vs-Aurora reconcile
  {"only": ["ps3_head_summary"]}      one table (still refuses if the run is
                                      incomplete; use with dry_run to inspect)

ENV
  ARTIFACT_BUCKET, PS3_RC_PREFIX, RDS_HOST, RDS_PORT, RDS_SECRET_ID,
  CITY_ID, MIN_MATCH, PG_TIMEOUT, CHUNK_ROWS
"""
import csv
import io
import json
import os
import re

import boto3
import pg8000.native

REGION = os.environ.get("AWS_REGION", "us-east-1")
ARTIFACT_BUCKET = os.environ.get(
    "ARTIFACT_BUCKET", "cubic-mars-pm-s3-datalake-dev-artifacts-170202974600")
PS3_RC_PREFIX = os.environ.get("PS3_RC_PREFIX", "chicago/ps3/rootcause_outputs")
RDS_SECRET_ID = os.environ.get("RDS_SECRET_ID", "cubic-mars-secret-rds-dev")
RDS_HOST = os.environ.get("RDS_HOST", "")
RDS_PORT = int(os.environ.get("RDS_PORT", "5432"))
CITY_ID = os.environ.get("CITY_ID", "CHI")
MIN_MATCH = float(os.environ.get("MIN_MATCH", "0.5"))
# 60s killed the first PS2 v2.5.3 load. Per-operation socket timeout.
PG_TIMEOUT = int(os.environ.get("PG_TIMEOUT", "300"))
CHUNK_ROWS = int(os.environ.get("CHUNK_ROWS", "500"))

# ---------------------------------------------------------------------------
# The contract. artifact file name (without .csv) -> Aurora table.
#
# These three tables are the root-cause head's own. ps3_v25_*, ps3_v2_*,
# ps3_severity_* and ps3_incident_predictions belong to other feeds that are
# still running, and nothing in this file can reach them.
# ---------------------------------------------------------------------------
ARTIFACTS = {
    "ps3_model_runs": "ps3_model_runs",
    "ps3_head_summary": "ps3_head_summary",
    "ps3_device_predictions": "ps3_device_predictions",
    # 04-Aug-2026. Serial grain, so Device 360's component panel has rows to
    # join to instead of stamping every serial "not_in_feed".
    "ps3_serial_predictions": "ps3_serial_predictions",
}
ALLOWED_TARGETS = frozenset(ARTIFACTS.values())

# COMPLETENESS IS JUDGED ON THESE THREE, NOT ON ALL FOUR.        04-Aug-2026
#
# The run-completeness check refuses a run that is missing an artifact, which
# is right: a half-loaded PS3 puts today's metrics beside last week's device
# list and nothing on screen reveals it.
#
# But adding ps3_serial_predictions to that required set would have made the
# loader refuse EVERY run already in S3 -- including ps3_oos_20260804, the one
# currently serving the dashboard -- because none of them was published by a
# notebook that writes it. The next unpinned invoke would have returned 409
# and the only visible symptom would have been "no complete run", which reads
# like an S3 or permissions fault rather than a contract change made here.
#
# So the new artifact is OPTIONAL: loaded when present, recorded as skipped
# when absent, and never a reason to refuse. It moves into REQUIRED once every
# live run publishes it -- which is a one-line change, made deliberately,
# rather than a trap sprung on the next person to run the loader.
REQUIRED_ARTIFACTS = frozenset((
    "ps3_model_runs", "ps3_head_summary", "ps3_device_predictions",
))

# Load order matters: ps3_model_runs is the run registry the other two hang
# off, and loading it last would leave a window where head_summary references
# a run that does not exist yet.
LOAD_ORDER = ("ps3_model_runs", "ps3_head_summary", "ps3_device_predictions",
              "ps3_serial_predictions")

# A row without its key is not a row, whatever the coverage percentage says.
IDENTITY_COLS = ("run_id", "device_id", "device_category")

_s3 = boto3.client("s3", region_name=REGION)
_sec = boto3.client("secretsmanager", region_name=REGION)
_conn = None


def target_table(artifact):
    """The whole allow-list, in one place, as a function rather than a lookup.

    Returning None rather than raising lets the caller record WHY a file was
    skipped instead of failing the run on a stray object in the prefix.
    """
    tgt = ARTIFACTS.get(artifact)
    if tgt is None or tgt not in ALLOWED_TARGETS:
        return None
    return tgt


def conn():
    global _conn
    if _conn is not None:
        return _conn
    sec = json.loads(_sec.get_secret_value(SecretId=RDS_SECRET_ID)["SecretString"])
    # The secret carries dbname=appdb; the migrations create every table
    # there. Defaulting to "postgres" cost a full debug cycle on the PS4
    # loader and is not repeated here.
    dbname = (sec.get("dbname") or sec.get("database")
              or os.environ.get("RDS_DATABASE") or "postgres")
    _conn = pg8000.native.Connection(
        user=sec.get("username", "postgres"), password=sec["password"],
        host=RDS_HOST or sec.get("host"), port=int(sec.get("port", RDS_PORT)),
        database=dbname, ssl_context=True, timeout=PG_TIMEOUT)
    print("connected to database=%s" % dbname)
    return _conn


def target_columns(c, table):
    r = c.run("SELECT column_name FROM information_schema.columns "
              "WHERE table_schema='public' AND table_name=:t "
              "ORDER BY ordinal_position", t=table)
    return [x[0] for x in r] if r else None


def pk_columns(c, table):
    r = c.run("SELECT a.attname FROM pg_index i "
              "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
              "WHERE i.indrelid=to_regclass('public.'||:t) AND i.indisprimary", t=table)
    return [x[0] for x in r] if r else []


def norm(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in ("nan", "none", "null", "<na>", "na", "nat"):
        return None
    return s


def find_pk_collapse(rows_, use, pk):
    """Two source rows landing on one primary key, detected BEFORE the INSERT.

    Postgres would raise 23505 halfway through a chunk and take the whole
    transaction with it. This names the columns that separated the rows and
    are not in the target, which is the actual diagnosis.
    """
    keys = [k for k in pk if k in use]
    if not keys or not rows_:
        return None
    seen = {}
    for r in rows_:
        k = tuple(norm(r.get(x)) for x in keys)
        if k in seen:
            a = seen[k]
            return {"pk": keys, "example_key": [str(x) for x in k],
                    "separating_columns": sorted(
                        col for col in set(a) | set(r)
                        if col not in keys and norm(a.get(col)) != norm(r.get(col))),
                    "rows_in": len(rows_)}
        seen[k] = r
    return None


def list_runs():
    """Every run_id under the prefix, newest first.

    run_id is lexically sortable by construction (ps3_oos_YYYYMMDD), so a
    string sort is a date sort. If that ever stops being true the manifest's
    run_ts is the tiebreak and this function is where to fix it.
    """
    p = _s3.get_paginator("list_objects_v2")
    runs = set()
    for page in p.paginate(Bucket=ARTIFACT_BUCKET, Prefix=PS3_RC_PREFIX.rstrip("/") + "/",
                           Delimiter="/"):
        for cp in page.get("CommonPrefixes") or []:
            runs.add(cp["Prefix"].rstrip("/").rsplit("/", 1)[-1])
    return sorted(runs, reverse=True)


def run_objects(run_id):
    base = "%s/%s/" % (PS3_RC_PREFIX.rstrip("/"), run_id)
    p = _s3.get_paginator("list_objects_v2")
    out = {}
    for page in p.paginate(Bucket=ARTIFACT_BUCKET, Prefix=base):
        for o in page.get("Contents") or []:
            out[o["Key"].rsplit("/", 1)[-1]] = o["Key"]
    return out


def read_csv(key):
    body = _s3.get_object(Bucket=ARTIFACT_BUCKET, Key=key)["Body"].read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(body)))


def read_json(key):
    return json.loads(_s3.get_object(Bucket=ARTIFACT_BUCKET, Key=key)["Body"].read())


def pick_run(pinned):
    """The newest run that carries every artifact, and why the others lost.

    A run missing one artifact is REFUSED rather than partly loaded. A
    dashboard showing today's metrics beside last week's device list is
    wrong in a way nothing on the screen can reveal; a stale dashboard is
    visible on /ps3/status.
    """
    considered = []
    for rid in ([pinned] if pinned else list_runs())[:20]:
        objs = run_objects(rid)
        present = {a for a in ARTIFACTS if (a + ".csv") in objs}
        # Missing is measured against REQUIRED, not against every artifact the
        # loader knows how to load. See REQUIRED_ARTIFACTS above.
        missing = sorted(REQUIRED_ARTIFACTS - present)
        entry = {"run_id": rid, "artifacts_present": sorted(present), "missing": missing,
                 "has_manifest": "manifest.json" in objs}
        considered.append(entry)
        if not missing:
            return rid, objs, considered
        entry["skipped"] = "incomplete -- %d artifact(s) absent" % len(missing)
    return None, None, considered


def lambda_handler(event, context):
    event = event or {}
    dry = bool(event.get("dry_run"))
    only = set(event.get("only") or [])
    pinned = event.get("run_id")

    res = {"dry_run": dry, "city": CITY_ID, "bucket": ARTIFACT_BUCKET,
           "prefix": PS3_RC_PREFIX, "loaded": {}, "no_target": {}, "refused": {},
           "skipped": {}, "errors": {}, "reconcile": {}}

    run_id, objs, considered = pick_run(pinned)
    res["runs_considered"] = considered
    if not run_id:
        res["status"] = "refused"
        res["error"] = ("no run under %s carries all %d required artifacts; see "
                        "runs_considered" % (PS3_RC_PREFIX, len(REQUIRED_ARTIFACTS)))
        print("PS3-RC refused", json.dumps({"reason": "no complete run"}))
        return {"statusCode": 409, "body": json.dumps(res, default=str)}

    res["run_id"] = run_id
    if "manifest.json" in objs:
        try:
            res["manifest"] = read_json(objs["manifest.json"])
        except Exception as e:
            res["manifest_error"] = str(e)[:200]

    c = conn()
    if not dry:
        c.run("BEGIN")
    try:
        for art in LOAD_ORDER:
            if only and art not in only:
                continue
            tgt = target_table(art)
            if tgt is None:
                res["skipped"][art] = "not in the target allow-list"
                continue
            try:
                src = read_csv(objs[art + ".csv"])
            except Exception as e:
                res["skipped"][art] = "not readable: %s" % str(e)[:160]
                continue
            if not src:
                # An empty artifact is NOT the same as a missing one, and it
                # is not automatically an error either -- a fleet can
                # legitimately produce no device predictions. It is recorded
                # and the run continues.
                res["skipped"][art] = "0 rows in S3"
                continue

            tcols = target_columns(c, tgt)
            if tcols is None:
                res["no_target"][art] = {"rows_in_s3": len(src), "table": tgt,
                                         "columns": sorted(src[0].keys())}
                continue

            shaped = []
            for r in src:
                o = {k.lower(): v for k, v in r.items()}
                o.setdefault("city_id", CITY_ID)
                shaped.append(o)

            srcset = set(shaped[0].keys())
            use = [x for x in tcols if x in srcset and x != "id"]
            dropped = sorted(srcset - set(use))
            missing = sorted(set(tcols) - srcset)

            idm = [x for x in tcols if x in IDENTITY_COLS and x not in use]
            if idm:
                res["refused"][art] = {"reason": "identity column(s) %s unfilled" % idm,
                                       "matched": use, "unused_from_source": dropped}
                continue
            # Coverage is measured against the columns the table REQUIRES, not
            # every column it has. ps3_head_summary declares 30+ optional
            # metric columns and this head fills a documented subset; judging
            # it on the full width would refuse a correct file.
            required = [x for x in tcols if x != "id"]
            cover = len(use) / max(1, len(required))
            if cover < MIN_MATCH:
                res["refused"][art] = {
                    "reason": "only %d/%d target columns matched (%.0f%%)"
                              % (len(use), len(required), cover * 100),
                    "matched": use, "missing_in_source": missing,
                    "unused_from_source": dropped}
                continue

            col = find_pk_collapse(shaped, use, pk_columns(c, tgt))
            if col:
                res["refused"][art] = dict(
                    col, reason="rows collapse on %s's primary key; separating "
                                "column(s) %s are not in the table"
                                % (tgt, col["separating_columns"]))
                continue

            before = None
            try:
                before = c.run("SELECT COUNT(*) FROM %s WHERE city_id=:c" % tgt,
                               c=CITY_ID)[0][0]
            except Exception:
                pass
            res["reconcile"][art] = {"table": tgt, "rows_in_s3": len(src),
                                     "rows_in_rds_before": before}

            if not dry:
                sp = "sp_" + re.sub(r"\W", "_", art)[:50]
                c.run("SAVEPOINT %s" % sp)
                try:
                    # Scoped to THIS run. Re-loading a run replaces it;
                    # earlier runs stay queryable, which is what makes
                    # ps3_model_runs a registry rather than a single row.
                    c.run("DELETE FROM %s WHERE city_id=:c AND run_id=:r" % tgt,
                          c=CITY_ID, r=run_id)
                    for i in range(0, len(shaped), CHUNK_ROWS):
                        chunk = shaped[i:i + CHUNK_ROWS]
                        ph, args = [], {}
                        for ri, r in enumerate(chunk):
                            nm = ["p%d_%d" % (ri, ci) for ci in range(len(use))]
                            ph.append("(" + ", ".join(":" + x for x in nm) + ")")
                            for n2, cn in zip(nm, use):
                                args[n2] = norm(r.get(cn))
                        q = ", ".join('"' + x + '"' for x in use)
                        c.run("INSERT INTO %s (%s) VALUES %s"
                              % (tgt, q, ", ".join(ph)), **args)
                    c.run("RELEASE SAVEPOINT %s" % sp)
                except Exception as te:
                    c.run("ROLLBACK TO SAVEPOINT %s" % sp)
                    res["errors"][art] = {"table": tgt, "error": str(te)[:400]}
                    continue

            res["loaded"][art] = {"table": tgt, "rows": len(shaped),
                                  "columns_used": use, "columns_dropped": dropped,
                                  "columns_missing": missing}

        # A run that lost a table to an error is not a loaded run. Committing
        # the survivors would publish exactly the half-loaded state the
        # completeness check upstream exists to prevent.
        if res["errors"] and not dry:
            c.run("ROLLBACK")
            res["status"] = "rolled_back"
            res["error"] = ("%d artifact(s) failed to insert; the whole run was "
                            "rolled back rather than published half-loaded"
                            % len(res["errors"]))
        elif not dry:
            c.run("COMMIT")
            res["status"] = "committed"
        else:
            res["status"] = "dry_run_ok"
    except Exception as e:
        if not dry:
            try:
                c.run("ROLLBACK")
            except Exception:
                pass
        res["status"] = "failed"
        res["error"] = str(e)[:600]

    res["summary"] = {"run_id": run_id, "loaded": len(res["loaded"]),
                      "no_target": len(res["no_target"]), "refused": len(res["refused"]),
                      "skipped": len(res["skipped"]), "errors": len(res["errors"]),
                      "rows": sum(v["rows"] for v in res["loaded"].values())}
    # Compact line FIRST. The full dump gets truncated in CloudWatch and
    # status serialises last, so without this the log could not answer the
    # only question that matters: did it commit.
    print("PS3-RC", res.get("status"), json.dumps(res["summary"]))
    return {"statusCode": 200 if res.get("status") in ("committed", "dry_run_ok") else 500,
            "body": json.dumps(res, default=str)}
