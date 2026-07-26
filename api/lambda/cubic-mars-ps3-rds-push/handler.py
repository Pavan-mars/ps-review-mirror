"""
cubic-mars-ps3-rds-push
========================
S3-event-triggered Lambda that loads PS3 DEEP-DIVE outputs (Parquet + manifest.json,
written either by `notebooks/ps3/ps3_deepdive_engine.py`'s `write_output()` helper
or by the sibling `cubic-mars-ps3-inference` Lambda's on-demand-scoring writer)
into Aurora PostgreSQL, auto-evolving the RDS schema as new columns/tables appear.

This is a direct fork of `cubic-mars-ps2-rds-push` (built 21-Jul-2026 for the PS2
serial-grain delivery) -- the loader logic is entity/PS-agnostic by design (it reads
whatever columns are in the incoming Parquet schema), so the only real changes here
are: PS3-specific defaults (S3 prefix, audit table name, secret id) and the S3 event
suffix filter. See that Lambda's handler.py docstring for the full design rationale;
duplicated (not imported) here so this Lambda has zero cross-Lambda dependency at
deploy time -- if PK later wants ONE shared loader for every PS, that refactor is a
follow-up, not a blocker to shipping PS3 today.

Design:
  - Trigger: S3 ObjectCreated, suffix filter "manifest.json" only.
  - Safety rule: new columns get `ADD COLUMN IF NOT EXISTS` automatically. An
    existing column whose incoming type disagrees with the stored type is NEVER
    auto-`ALTER TYPE` -- it is skipped and logged to `ps3_load_audit`.
  - Idempotency: rows are keyed by (grain, computed_date, run_id) -- a re-delivered
    S3 event (or a manual re-run of the same manifest) DELETEs that exact slice
    first, then re-INSERTs -- safe to retry.
  - Runs inside the same 3 private subnets as `cubic-mars-dashboard-api` (RDS SG
    only accepts 5432 from the VPC CIDR). Own least-privilege execution role:
    S3 read on the PS3 deep-dive export prefix + secretsmanager:GetSecretValue
    for `cubic-mars-secret-rds-dev` only.
  - Also loads `cubic-mars-ps3-inference`'s on-demand scoring output
    (`ps3_ondemand_inference_log`, grain="ondemand") through the identical path --
    "score new data" (item 3) and "push to RDS with schema updates" (item 4) meet
    here, without this Lambda needing to know which producer wrote the manifest.

Environment variables expected at deploy time:
  RDS_HOST, RDS_PORT (default 5432), RDS_DATABASE, RDS_SECRET_ID
    (defaults to "cubic-mars-secret-rds-dev"), AWS_REGION (default us-east-1)
"""
import io
import json
import logging
import os
import re

import boto3
import pg8000.native as pg8000
import pyarrow.parquet as pq

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-east-1")
RDS_SECRET_ID = os.environ.get("RDS_SECRET_ID", "cubic-mars-secret-rds-dev")
RDS_DATABASE = os.environ.get("RDS_DATABASE", "cubic_mars_dev")
AUDIT_TABLE = "ps3_load_audit"

_s3 = boto3.client("s3", region_name=REGION)
_secrets = boto3.client("secretsmanager", region_name=REGION)

_CACHED_CREDS = None
_CACHED_CONN = None

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

_ARROW_TO_PG = {
    "int8": "smallint", "int16": "smallint", "int32": "integer", "int64": "bigint",
    "uint8": "smallint", "uint16": "integer", "uint32": "bigint", "uint64": "bigint",
    "float": "double precision", "float32": "real", "float64": "double precision",
    "double": "double precision", "bool": "boolean",
    "date32[day]": "date", "date64[ms]": "date",
    "timestamp[us]": "timestamptz", "timestamp[ms]": "timestamptz",
    "timestamp[ns]": "timestamptz", "timestamp[s]": "timestamptz",
}


class SchemaConflict(Exception):
    """Raised (and caught, never fatal to the whole load) when an existing column's
    incoming type disagrees with the stored type. Column is skipped, not auto-altered."""


def _get_rds_creds():
    global _CACHED_CREDS
    if _CACHED_CREDS is None:
        raw = _secrets.get_secret_value(SecretId=RDS_SECRET_ID)["SecretString"]
        _CACHED_CREDS = json.loads(raw)
        logger.info("Loaded RDS credentials from Secrets Manager (%s)", RDS_SECRET_ID)
    return _CACHED_CREDS


def _get_conn():
    global _CACHED_CONN
    if _CACHED_CONN is not None:
        try:
            _CACHED_CONN.run("SELECT 1")
            return _CACHED_CONN
        except Exception:
            logger.info("Cached RDS connection went stale, reconnecting.")
            _CACHED_CONN = None
    creds = _get_rds_creds()
    _CACHED_CONN = pg8000.Connection(
        user=creds["username"], password=creds["password"],
        host=creds.get("host", os.environ.get("RDS_HOST")),
        port=int(creds.get("port", os.environ.get("RDS_PORT", 5432))),
        database=creds.get("dbname", RDS_DATABASE),
        timeout=15,
    )
    return _CACHED_CONN


def _pg_type_for_arrow(arrow_type_str, column_name):
    key = str(arrow_type_str).lower()
    if key in _ARROW_TO_PG:
        return _ARROW_TO_PG[key]
    if key.startswith("timestamp"):
        return "timestamptz"
    if key.startswith("string") or key.startswith("large_string") or key.startswith("utf8"):
        return "text"
    logger.warning("[type-infer] unmapped arrow type %r for column %r -> falling back to text",
                    arrow_type_str, column_name)
    return "text"


def _validate_identifier(name, kind="column"):
    if not _IDENT_RE.match(name):
        raise ValueError(f"Refusing unsafe {kind} name from PS3 output: {name!r} "
                          f"(must be lowercase snake_case, <=63 chars)")
    return name


def _existing_columns(conn, table_name):
    rows = conn.run(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :t",
        t=table_name,
    )
    return {r[0]: r[1] for r in rows}


def _table_exists(conn, table_name):
    rows = conn.run(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name = :t",
        t=table_name,
    )
    return len(rows) > 0


def _create_table(conn, table_name, columns_with_types):
    _validate_identifier(table_name, "table")
    col_defs = ", ".join(f'"{_validate_identifier(c)}" {t}' for c, t in columns_with_types)
    ddl = f'CREATE TABLE "{table_name}" ({col_defs})'
    logger.info("[ddl] CREATE TABLE %s (%d columns)", table_name, len(columns_with_types))
    conn.run(ddl)
    try:
        conn.run(f'CREATE INDEX "idx_{table_name}_grain_date" ON "{table_name}" (grain, computed_date)')
    except Exception as e:
        logger.warning("[ddl] index create skipped for %s: %s", table_name, e)


def _add_missing_columns(conn, table_name, incoming_cols_with_types, existing_cols):
    added, conflicts = [], []
    for col, pg_type in incoming_cols_with_types:
        _validate_identifier(col)
        if col not in existing_cols:
            ddl = f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS "{col}" {pg_type}'
            logger.info("[ddl] %s", ddl)
            conn.run(ddl)
            added.append(col)
        else:
            stored = existing_cols[col].lower()
            numeric_stored = any(k in stored for k in ("int", "numeric", "double", "real", "float"))
            numeric_incoming = any(k in pg_type for k in ("int", "numeric", "double", "real", "float"))
            if numeric_stored != numeric_incoming and stored not in ("text", "character varying"):
                conflicts.append({"column": col, "stored_type": stored, "incoming_type": pg_type})
    return added, conflicts


def _load_rows(conn, table_name, columns, arrow_table, grain, computed_date, run_id):
    _validate_identifier(table_name, "table")
    for c in columns:
        _validate_identifier(c)

    conn.run(
        f'DELETE FROM "{table_name}" WHERE grain = :g AND computed_date = :d AND run_id = :r',
        g=grain, d=computed_date, r=run_id,
    )

    pydict = arrow_table.to_pylist()
    if not pydict:
        return 0

    placeholders = ", ".join(f":{c}" for c in columns)
    col_list = ", ".join(f'"{c}"' for c in columns)
    insert_sql = f'INSERT INTO "{table_name}" ({col_list}) VALUES ({placeholders})'

    BATCH = 500
    n_loaded = 0
    for i in range(0, len(pydict), BATCH):
        batch = pydict[i:i + BATCH]
        for row in batch:
            params = {c: row.get(c) for c in columns}
            conn.run(insert_sql, **params)
        n_loaded += len(batch)
    return n_loaded


def _write_load_audit(conn, manifest, added_cols, conflicts, n_loaded, status, error=None):
    try:
        if not _table_exists(conn, AUDIT_TABLE):
            conn.run(f"""
                CREATE TABLE {AUDIT_TABLE} (
                    id BIGSERIAL PRIMARY KEY,
                    table_name TEXT NOT NULL,
                    grain TEXT, computed_date TEXT, run_id TEXT,
                    rows_loaded INTEGER, columns_added TEXT, columns_conflicted TEXT,
                    status TEXT, error_detail TEXT,
                    loaded_at TIMESTAMPTZ DEFAULT now()
                )
            """)
        conn.run(
            f"INSERT INTO {AUDIT_TABLE} "
            "(table_name, grain, computed_date, run_id, rows_loaded, columns_added, "
            " columns_conflicted, status, error_detail) VALUES "
            "(:tn, :g, :cd, :rid, :n, :added, :conf, :st, :err)",
            tn=manifest.get("table_name"), g=manifest.get("grain"),
            cd=manifest.get("computed_date"), rid=manifest.get("run_id"),
            n=n_loaded, added=json.dumps(added_cols), conf=json.dumps(conflicts),
            st=status, err=str(error)[:2000] if error else None,
        )
    except Exception as e:
        logger.error("[audit] failed to write %s row: %s", AUDIT_TABLE, e)


def process_manifest(bucket, manifest_key):
    """Core loader — pulled out of the handler so it's independently unit-testable
    (see test_handler.py) without an S3-event envelope."""
    conn = _get_conn()

    manifest_obj = _s3.get_object(Bucket=bucket, Key=manifest_key)
    manifest = json.loads(manifest_obj["Body"].read())
    table_name = _validate_identifier(manifest["table_name"], "table")
    grain = manifest["grain"]
    computed_date = manifest["computed_date"]
    run_id = manifest["run_id"]
    expected_rows = manifest.get("row_count", -1)
    data_key = manifest["s3_data_key"]

    logger.info("[manifest] %s grain=%s computed_date=%s run_id=%s expected_rows=%s",
                table_name, grain, computed_date, run_id, expected_rows)

    data_obj = _s3.get_object(Bucket=bucket, Key=data_key)
    arrow_table = pq.read_table(io.BytesIO(data_obj["Body"].read()))

    incoming_cols_with_types = []
    for field in arrow_table.schema:
        col = field.name.lower()
        pg_type = _pg_type_for_arrow(field.type, col)
        incoming_cols_with_types.append((col, pg_type))
    incoming_col_names = [c for c, _ in incoming_cols_with_types]

    added_cols, conflicts = [], []
    try:
        if not _table_exists(conn, table_name):
            _create_table(conn, table_name, incoming_cols_with_types)
            added_cols = incoming_col_names
        else:
            existing = _existing_columns(conn, table_name)
            added_cols, conflicts = _add_missing_columns(conn, table_name, incoming_cols_with_types, existing)
            if conflicts:
                logger.warning("[schema-conflict] %s: %s — these columns are SKIPPED on load, "
                                "not auto-widened. Review %s.", table_name, conflicts, AUDIT_TABLE)
                incoming_col_names = [c for c in incoming_col_names
                                       if c not in {x["column"] for x in conflicts}]

        n_loaded = _load_rows(conn, table_name, incoming_col_names, arrow_table, grain, computed_date, run_id)

        if expected_rows >= 0 and n_loaded != expected_rows:
            logger.warning("[reconcile] %s expected %d rows from manifest, loaded %d",
                            table_name, expected_rows, n_loaded)

        _write_load_audit(conn, manifest, added_cols, conflicts, n_loaded,
                           status="ok" if not conflicts else "ok_with_skipped_columns")
        logger.info("[done] %s: %d rows loaded, %d columns added, %d columns skipped (conflict)",
                    table_name, n_loaded, len(added_cols), len(conflicts))
        return {"table_name": table_name, "rows_loaded": n_loaded, "columns_added": added_cols,
                "columns_conflicted": conflicts, "status": "ok"}
    except Exception as e:
        logger.exception("[error] failed to load %s", table_name)
        _write_load_audit(conn, manifest, added_cols, conflicts, 0, status="failed", error=e)
        raise


def handler(event, context):
    """S3 ObjectCreated entry point. Configure the trigger's suffix filter to
    'manifest.json' only in the bucket notification config — this handler does not
    re-check the suffix itself so infra and code both need to agree on the contract."""
    results = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        if not key.endswith("manifest.json"):
            logger.info("[skip] %s is not a manifest.json key (trigger suffix filter should "
                        "already prevent this — check the S3 notification config)", key)
            continue
        try:
            results.append(process_manifest(bucket, key))
        except Exception as e:
            results.append({"key": key, "status": "failed", "error": str(e)})
    return {"processed": len(results), "results": results}
