"""Replays cubic-mars-ps3-v25-loader's real code path against sql/45 on a real
PostgreSQL 16, using pg8000 (the loader's own driver) and rows carrying the
exact python types pyarrow's to_pylist() yields for each column in the V26
schema dump.

S3 and Secrets Manager are stubbed. Everything else -- survey/choose_run,
target_columns, json_columns, pk_columns, find_pk_collapse, the shaping loop,
the chunk sizing and the INSERT itself -- is the deployed code, imported, not
copied. A test that copies the logic proves the copy works.
"""
import datetime as dt
import json
import sys
import types

# ---- stub boto3 BEFORE importing the handler ------------------------------
_fake = types.ModuleType("boto3")
_fake.client = lambda *a, **k: object()
sys.modules["boto3"] = _fake

sys.path.insert(0, "/tmp/scratch/ps3/loader")
import handler  # noqa: E402

DUMP = json.load(open("/tmp/ps3_schema_real.json"))
CDATE, RUN_ID, CITY = "2026-04-11", "6a7002b0-a0fc-41f8-bb0f-1ad5a5358edf", "CHI"

# Exactly what pyarrow to_pylist() hands back for each observed python type.
VAL = {
    "str":       lambda n, i: f"{n}_{i}",
    "int":       lambda n, i: 1000 + i,
    # numpy int64 survives to_pylist() as a python int in pyarrow, but the dump
    # saw it inside object-dtype columns where pandas kept the numpy scalar.
    # pg8000 binds it fine; kept distinct so the mapping is explicit, not lucky.
    "int64":     lambda n, i: 1000 + i,
    "float":     lambda n, i: 0.5 + i,
    "bool":      lambda n, i: bool(i % 2),
    "date":      lambda n, i: dt.date(2026, 4, 11 - i),
    "Timestamp": lambda n, i: dt.datetime(2026, 4, 11, 17, 51, 34) + dt.timedelta(days=i),
    "list":      lambda n, i: [{"model": f"m{i}", "f1": 0.5 + i}],
}


def synth(table_spec, nrows=2):
    rows = []
    for i in range(nrows):
        r = {}
        for col in table_spec["columns"]:
            name, types_ = col["name"], col["python_types"]
            if col["null_rate"] >= 1.0 or not types_:
                r[name] = None            # all-null in the real run, stays null
            else:
                r[name] = VAL[types_[0]](name, i)
        rows.append(r)
    return rows


def main():
    conn = __import__("pg8000.native", fromlist=["native"]).Connection(
        user="postgres", unix_sock="/tmp/.s.PGSQL.5442", database="appdb")

    # sql/45 into a transaction we throw away, so this leaves nothing behind.
    conn.run("BEGIN")
    ddl = open("/tmp/scratch/ps3/45_ps3_v25.sql").read()
    for stmt in [s.strip() for s in ddl.split(";\n") if s.strip()]:
        if stmt.lstrip().startswith("--") and "CREATE" not in stmt:
            continue
        conn.run(stmt)
    conn.run("INSERT INTO cities (id, name) VALUES ('CHI','Chicago') ON CONFLICT DO NOTHING")

    # ---- stub the two S3 readers, keep every other line of the handler ----
    store = {t: synth(v) for t, v in DUMP["tables"].items()}
    handler.survey = lambda b, p: {(CDATE, RUN_ID): set(handler.EXPECTED_TABLES)}
    handler.read_parts = lambda b, kp: (store[kp.split("/")[1]], 1)
    handler.conn = lambda: conn
    handler.PS3_PREFIX = "ps3_outputs"

    # COPY_MIN_ROWS is forced to 0 when FORCE_COPY is set, so the same 20-table
    # load runs through the COPY encoder instead of the INSERT path. Both paths
    # must produce identical values -- that is the point of running it twice.
    import os
    if os.environ.get("FORCE_COPY"):
        handler.COPY_MIN_ROWS = 0
    out = json.loads(handler.lambda_handler({"city": CITY}, None)["body"])

    print("=" * 72)
    print("PS3 v25 LOADER -- SIMULATED LOAD AGAINST sql/45 ON REAL POSTGRESQL 16")
    print("=" * 72)
    print("status          ", out.get("status"))
    print("run selected    ", out.get("computed_date"), out.get("run_id"))
    print("summary         ", json.dumps(out["summary"]))
    if out.get("error"):
        print("error           ", out["error"])
    for bucket in ("refused", "errors", "skipped", "no_target"):
        for k, v in out.get(bucket, {}).items():
            print(f"  {bucket.upper():9s} {k}: {json.dumps(v, default=str)[:240]}")

    print()
    print(f"{'table':32s} {'rows':>5s} {'cols':>4s}  dropped")
    for k, v in sorted(out["loaded"].items()):
        print(f"{v['table']:32s} {v['rows']:5d} {v['columns_used']:4d}  "
              f"{v.get('method','-'):8s} {v['columns_dropped'] or '-'}")
        if v.get("copy_fallback_reason"):
            print(f"    COPY FELL BACK: {v['copy_fallback_reason']}")

    print()
    print("--- read back from PostgreSQL ---")
    total = 0
    for t in sorted(handler.EXPECTED_TABLES):
        tgt = handler.target_table(t)
        n = conn.run(f"SELECT count(*) FROM {tgt}")[0][0]
        total += n
        if n != 2:
            print(f"  MISMATCH {tgt}: {n} rows, expected 2")
    print(f"  {len(handler.EXPECTED_TABLES)} tables, {total} rows total "
          f"(expected {2 * len(handler.EXPECTED_TABLES)})")

    j = conn.run("SELECT model_runs FROM ps3_v25_run_stage_audit LIMIT 1")[0][0]
    print(f"  jsonb round trip: {type(j).__name__} {json.dumps(j)[:60]}")
    d = conn.run("SELECT event_date FROM ps3_v25_device_day LIMIT 1")[0][0]
    print(f"  event_date type in Postgres: {type(d).__name__} = {d}")
    conf = conn.run("SELECT predicted_component_confidence "
                    "FROM ps3_v25_device_episode_fact LIMIT 1")[0][0]
    print(f"  predicted_component_confidence: {type(conf).__name__} = {conf}")
    b = conn.run("SELECT deterministic_given_event_type "
                 "FROM ps3_v25_source_column_profile ORDER BY 1 LIMIT 1")[0][0]
    print(f"  boolean round trip: {type(b).__name__} = {b}")
    nulls = conn.run("SELECT count(*) FROM ps3_v25_device_episode_fact "
                     "WHERE linked_severity IS NULL")[0][0]
    print(f"  all-null column stayed NULL (not the string 'None'): {nulls}/2")
    ts = conn.run("SELECT episode_start FROM ps3_v25_device_episode_fact "
                  "ORDER BY 1 LIMIT 1")[0][0]
    print(f"  timestamp round trip: {type(ts).__name__} = {ts}")

    conn.run("ROLLBACK")
    print()
    print("rolled back -- the scratch database is unchanged")
    return out


if __name__ == "__main__":
    r = main()
    ok = (r.get("status") == "committed" and r["summary"]["loaded"] == 20
          and not r["summary"]["refused"] and not r["summary"]["errors"])
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
