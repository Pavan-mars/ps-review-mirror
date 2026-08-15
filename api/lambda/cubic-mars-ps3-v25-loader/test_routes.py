"""Executes every PS3 v2.5 route against sql/45 on a real PostgreSQL 16.

The point is not that the python parses -- it is that all 21 SQL statements the
family can emit actually run. A route table with 269 generated column names has
269 ways to produce a statement that only fails when a user clicks the tab.

Each route is exercised three ways: bare, with a filter, and with paging. The
filtered call is the one that catches a _PS3V25_FILTERS entry naming a column
that is not in that table's select list.
"""
import datetime as dt
import json
import sys

sys.path.insert(0, "/tmp/scratch/ps3/loader")
sys.path.insert(0, "/tmp/scratch/ps3/routes")

DUMP = json.load(open("/tmp/ps3_schema_real.json"))["tables"]
CITY = "CHI"

VAL = {
    "str":       lambda n, i: f"{n}_{i}",
    "int":       lambda n, i: 1000 + i,
    "int64":     lambda n, i: 1000 + i,
    "float":     lambda n, i: 0.5 + i,
    "bool":      lambda n, i: bool(i % 2),
    "date":      lambda n, i: dt.date(2026, 4, 11 - i),
    "Timestamp": lambda n, i: dt.datetime(2026, 4, 11, 17, 51, 34) + dt.timedelta(days=i),
    "list":      lambda n, i: json.dumps([{"model": f"m{i}"}]),
}

conn_obj = __import__("pg8000.native", fromlist=["native"]).Connection(
    user="postgres", unix_sock="/tmp/.s.PGSQL.5442", database="appdb")
def reset_ps3_v25(c):
    """Drop every ps3_v25_ object. Called BEFORE and AFTER, deliberately.

    The first version of this harness wrapped everything in one BEGIN and ended
    with ROLLBACK, and printed "the scratch database is unchanged". That was
    false: handler.lambda_handler() issues its own COMMIT, which committed the
    OUTER transaction -- DDL and all -- and the closing ROLLBACK was a no-op
    against an already-committed transaction. The run itself was valid; the
    cleanup claim was not. An explicit DROP cannot be defeated that way.
    """
    c.run("DROP VIEW IF EXISTS v_ps3_v25_status")
    for t in ("causal_balance","causal_effects","commanded_split","component_summary",
              "device_day","device_episode_fact","device_reliability","device_summary",
              "facility_rollup","label_maturity","model_feature_importance",
              "model_scorecard","oos_source_audit","prediction_explainability",
              "repeat_interval","root_cause_evidence_audit","run_stage_audit",
              "run_status","serial_reliability","source_column_profile"):
        c.run(f"DROP TABLE IF EXISTS ps3_v25_{t} CASCADE")

reset_ps3_v25(conn_obj)
for stmt in [s.strip() for s in open("/tmp/scratch/ps3/45_ps3_v25.sql").read().split(";\n") if s.strip()]:
    if stmt.lstrip().startswith("--") and "CREATE" not in stmt:
        continue
    conn_obj.run(stmt)
conn_obj.run("INSERT INTO cities (id,name) VALUES ('CHI','Chicago') ON CONFLICT DO NOTHING")

# two rows per table, real column names, real value types
for src, spec in DUMP.items():
    tgt = "ps3_v25_" + src[len("ps3_"):]
    names = [c["name"] for c in spec["columns"] if c["name"] not in ("_episode_row_id", "computed_date")]
    cols = ['"city_id"', '"computed_date"'] + [f'"{n}"' for n in names]
    for i in range(2):
        vals, kw = [], {"p_city": CITY, "p_cd": dt.date(2026, 4, 11)}
        vals += [":p_city", ":p_cd"]
        for j, c in enumerate([c for c in spec["columns"] if c["name"] in names]):
            t = c["python_types"]
            if c["null_rate"] >= 1.0 or not t:
                v = None
            elif c["name"] == "mars_device_category":
                # The real column only ever holds GATE / TVM / VALIDATOR, and
                # the route upper-cases ?category to match. A lowercase fixture
                # made a correct filter look broken.
                v = ("GATE", "TVM")[i]
            else:
                v = VAL[t[0]](c["name"], i)
            kw[f"v{j}"] = v
            vals.append(f":v{j}")
        conn_obj.run(f'INSERT INTO {tgt} ({", ".join(cols)}) VALUES ({", ".join(vals)})', **kw)

# ---- the handler's real helpers, reproduced exactly -----------------------
def _json_default(o):
    return o.isoformat() if hasattr(o, "isoformat") else str(o)

def rows(sql, **kw):
    res = conn_obj.run(sql, **kw)
    cols = [d["name"] for d in conn_obj.columns]
    return [dict(zip(cols, r)) for r in res]

def ok(payload):
    return {"statusCode": 200, "body": json.dumps(payload, default=_json_default)}

def err(code, m):
    return {"statusCode": code, "body": json.dumps({"error": m})}

def _clamp_int(v, default, lo, hi):
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))

ns = {"rows": rows, "ok": ok, "err": err, "_clamp_int": _clamp_int, "json": json}
exec(open("/tmp/scratch/ps3/routes/ps3_v25_routes.py").read(), ns)
route = ns["_ps3_v25_route"]
METRICS = ns["_PS3V25"]
FILTERS = ns["_PS3V25_FILTERS"]

fails = []
print("=" * 78)
print("PS3 v2.5 ROUTES -- EVERY STATEMENT EXECUTED AGAINST sql/45 ON POSTGRESQL 16")
print("=" * 78)

r = route("/ps3/status", {}, CITY)
st = json.loads(r["body"])
print(f'  /ps3/status  tables={st["tables"]}/{st["expected_tables"]}  '
      f'coherent={st["coherent"]}  rows={st["total_rows"]}  run_id={st["run_id"]}')
if st["tables"] != 20 or not st["coherent"] or st["total_rows"] != 40:
    fails.append(f"/ps3/status: {json.dumps(st)[:300]}")
if st["empty_tables"]:
    fails.append(f"/ps3/status empty_tables: {st['empty_tables']}")

idx = json.loads(route("/ps3/v25/", {}, CITY)["body"])
if len(idx["metrics"]) != 20:
    fails.append(f"index lists {len(idx['metrics'])} metrics, expected 20")

nf = route("/ps3/v25/does-not-exist", {}, CITY)
if nf["statusCode"] != 404:
    fails.append("unknown metric did not 404")

print()
print(f'  {"metric":22s} {"bare":>9s} {"cols":>8s}  filters exercised (name=rows returned)')
for slug in sorted(METRICS):
    table, cols, order, dflt, hard, datecol = METRICS[slug]
    have = cols.replace('"', "").split(",")
    line = [slug]
    got = None
    # EVERY applicable filter, one call each -- not just the first that matches.
    # Breaking on the first meant "category" shadowed device, facility, serial
    # and component on every table that has them, so five of the seven filters
    # were never actually sent to PostgreSQL.
    cases = [("bare", {}), ("paged", {"limit": "1", "offset": "1"})]
    allnull = {c["name"] for c in DUMP[
        "ps3_" + table[len("ps3_v25_"):]]["columns"] if c["null_rate"] >= 1.0}
    for pname, col in FILTERS:
        if col in have and col not in allnull:
            # the value the synthesiser actually wrote, so a working filter
            # returns rows -- a filter that binds to the wrong column returns 0
            # and that is the failure this is looking for
            want = "GATE" if col == "mars_device_category" else f"{col}_0"
            cases.append((f"?{pname}", {pname: want}))
    skipped = sorted(col for _, col in FILTERS if col in have and col in allnull)
    if datecol:
        cases.append(("?from/to", {"from": "2020-01-01", "to": "2030-01-01"}))
    matched = []
    for label, params in cases:
        try:
            resp = route(f"/ps3/v25/{slug}", params, CITY)
            body = json.loads(resp["body"])
            if resp["statusCode"] != 200:
                fails.append(f"{slug} [{label}]: HTTP {resp['statusCode']} {body}")
                continue
            if label == "bare":
                got = body
            elif label.startswith("?") and label != "?from/to":
                if len(body) == 0:
                    fails.append(f"{slug} [{label}]: filter bound but matched 0 of 2 rows "
                                 f"-- it is filtering the wrong column")
                matched.append(f"{label}={len(body)}")
            elif label == "?from/to":
                matched.append(f"{label}={len(body)}")
        except Exception as e:
            fails.append(f"{slug} [{label}]: {type(e).__name__}: {str(e)[:220]}")
    ncols = len(got[0]) if got else 0
    print(f"  {slug:22s} rows={len(got) if got else 0}  cols={ncols:>3}  "
          f"{' '.join(matched) or '(no applicable filter)'}"
          + (f"  [skipped, all-null: {','.join(skipped)}]" if skipped else ""))
    if got and ncols != len(have):
        fails.append(f"{slug}: returned {ncols} columns, select list has {len(have)}")

# The two guards worth stating outright.
epi = json.loads(route("/ps3/v25/episodes", {}, CITY)["body"])
if epi and "_episode_row_id" in epi[0]:
    fails.append("episodes route leaks _episode_row_id")
if epi and "linked_root_cause" not in epi[0]:
    fails.append("episodes route dropped the all-null root-cause columns; "
                 "they must be present so they populate without a code change")

reset_ps3_v25(conn_obj)
print()
print("FAILURES:", len(fails))
for f in fails:
    print("  !", f)
print("RESULT:", "PASS" if not fails else "FAIL")
sys.exit(1 if fails else 0)
