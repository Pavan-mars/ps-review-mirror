"""The widest table at real volume. 54,239 rows x 78 columns is where a chunk
size that is merely 'probably fine' stops being fine: PostgreSQL caps a single
statement at 65,535 bound parameters, and pg8000 reports that as a protocol
error mid-transaction, after other tables are staged."""
import datetime as dt, json, resource, sys, time, types
_f = types.ModuleType("boto3"); _f.client = lambda *a, **k: object()
sys.modules["boto3"] = _f
sys.path.insert(0, "/tmp/ps3_scratch/loader")
import handler
sys.path.insert(0, "/tmp/ps3_scratch/loader")
from simulate_load import synth, VAL, reset_ps3_v25   # noqa

DUMP = json.load(open("/tmp/ps3_schema_real.json"))
SPEC = DUMP["tables"]["ps3_device_episode_fact"]
N = SPEC["rows"]

conn = __import__("pg8000.native", fromlist=["native"]).Connection(
    user="postgres", unix_sock="/tmp/.s.PGSQL.5442", database="appdb")
reset_ps3_v25(conn)
ddl = open("/tmp/ps3_scratch/45_ps3_v25.sql").read()
for stmt in [s.strip() for s in ddl.split(";\n") if s.strip()]:
    if stmt.lstrip().startswith("--") and "CREATE" not in stmt:
        continue
    conn.run(stmt)
conn.run("INSERT INTO cities (id,name) VALUES ('CHI','Chicago') ON CONFLICT DO NOTHING")

t0 = time.time()
rows = synth(SPEC, nrows=N)
for i, r in enumerate(rows):                 # unique PK per row, as in the real run
    r["oos_episode_id"] = "%064x" % i
build = time.time() - t0

store = {"ps3_device_episode_fact": rows}
handler.survey = lambda b, p: {("2026-04-11", "6a7002b0-a0fc-41f8-bb0f-1ad5a5358edf"):
                               set(handler.EXPECTED_TABLES)}
handler.read_parts = lambda b, kp: (store[kp.split("/")[1]], 1)
handler.conn = lambda: conn

t0 = time.time()
out = json.loads(handler.lambda_handler(
    {"city": "CHI", "only": ["ps3_device_episode_fact"]}, None)["body"])
load = time.time() - t0

n = conn.run("SELECT count(*) FROM ps3_v25_device_episode_fact")[0][0]
d = out["loaded"].get("ps3_device_episode_fact", {})
print("status            ", out["status"])
print("rows synthesised  ", f"{N:,}   ({build:.1f}s)")
print("columns inserted  ", d.get("columns_used"))
print("method            ", d.get("method"))
cr = d.get("chunk_rows")
if cr:
    print("chunk rows        ", cr, f"= {cr * d.get('columns_used',0):,} bound params/statement")
    print("statements        ", -(-N // cr))
else:
    print("chunk rows        ", "n/a -- COPY is one statement")
print("rows in postgres  ", f"{n:,}")
print("load wall clock   ", f"{load:.1f}s")
print("peak rss          ", f"{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024:.0f} MB")
print("dropped           ", d.get("columns_dropped"))
reset_ps3_v25(conn)
ok = out["status"] == "committed" and n == N
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
