"""The guards are the reason this loader exists rather than a 40-line INSERT
script. A guard that has never been made to fire is a comment. Each test below
constructs the exact failure it claims to prevent and asserts the loader
refuses it."""
import datetime as dt
import json
import sys
import types

_fake = types.ModuleType("boto3")
_fake.client = lambda *a, **k: object()
sys.modules["boto3"] = _fake
sys.path.insert(0, "/tmp/ps3_scratch/loader")
import handler  # noqa: E402

DUMP = json.load(open("/tmp/ps3_schema_real.json"))
CDATE, RUN = "2026-04-11", "6a7002b0-a0fc-41f8-bb0f-1ad5a5358edf"
OLDER = "5f000000-0000-4000-8000-000000000000"
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  -- ' + detail) if detail else ''}")
    if not cond:
        fails.append(name)


print("1. survey() regex against real S3 key shapes")
real_keys = [
    f"ps3_outputs/ps3_device_day/computed_date={CDATE}/run_id={RUN}/part-00000.parquet",
    f"ps3_outputs/ps3_device_episode_fact/computed_date={CDATE}/run_id={RUN}/part-00000.parquet",
    f"ps3_outputs/ps3_run_control/computed_date={CDATE}/run_id={RUN}/manifest.json",
    "ps3_outputs/ps3_device_day/_SUCCESS",
]
root = "ps3_outputs/"
parsed = []
for k in real_keys:
    m = handler._PART_RE.search(k[len(root) - 1:] if k.startswith(root) else k)
    if m:
        parsed.append((m.group(1), m.group(2), m.group(3)))
check("parses the three partitioned keys, ignores _SUCCESS", len(parsed) == 3, str(parsed[:1]))
check("table name extracted, not the prefix",
      {p[0] for p in parsed} == {"ps3_device_day", "ps3_device_episode_fact", "ps3_run_control"})
check("uuid run_id matches the [0-9a-f-] class", all(p[2] == RUN for p in parsed))

print("\n2. choose_run() refuses an incomplete run and falls back to a complete older one")
complete = set(handler.EXPECTED_TABLES)
partial = complete - {"ps3_causal_effects"}
found = {(CDATE, RUN): partial, (CDATE, OLDER): complete}
chosen, report = handler.choose_run(found)
check("newest run rejected because one table is absent",
      chosen == (CDATE, OLDER), f"chose {chosen}")
check("the rejection names the missing table",
      report[0]["missing"] == ["ps3_causal_effects"], str(report[0]["missing"]))

chosen2, _ = handler.choose_run({(CDATE, RUN): partial})
check("no complete run at all -> chooses nothing (load will be refused)", chosen2 is None)

print("\n3. target_table() cannot reach a plan-B table")
check("ps3_device_day -> ps3_v25_device_day",
      handler.target_table("ps3_device_day") == "ps3_v25_device_day")
check("every expected table maps under the ps3_v25_ prefix",
      all(str(handler.target_table(t)).startswith("ps3_v25_") for t in handler.EXPECTED_TABLES))
for hostile in ("ps3_v2_rootcause", "ps3_incident_predictions", "ps3_device_predictions",
                "ps3_serial_risk"):
    t = handler.target_table(hostile)
    check(f"{hostile} cannot be written as itself", t != hostile, f"-> {t}")
check("ps3_run_control is not a load target", handler.target_table("ps3_run_control") is None)
check("a non-ps3 prefix is not a load target", handler.target_table("ps2_phi_matrix") is None)

print("\n4. find_pk_collapse() catches the grain loss it was written for")
rows = [{"device_id": "D1", "mars_device_category": "TVM", "scope": "ALL", "v": 1},
        {"device_id": "D1", "mars_device_category": "TVM", "scope": "GATE", "v": 2}]
use = ["device_id", "mars_device_category", "v"]          # scope dropped by the target
col = handler.find_pk_collapse(rows, use, ["device_id", "mars_device_category"])
check("collision detected before the INSERT", col is not None)
check("names the column that separated them",
      col and col["separating_columns"] == ["scope", "v"], str(col and col["separating_columns"]))
check("reports the row loss", col and col["rows_lost_if_forced"] == 1)
check("no false positive when the grain survives",
      handler.find_pk_collapse(rows, use + ["scope"],
                               ["device_id", "mars_device_category", "scope"]) is None)

print("\n5. norm() -- the string sentinels pandas writes into parquet")
for raw, want in [("nan", None), ("NaN", None), ("<NA>", None), ("None", None),
                  ("null", None), ("  ", None), (float("nan"), None),
                  ("  DEV  ", "DEV"), (0.0, 0.0), (False, False), (0, 0)]:
    got = handler.norm(raw)
    check(f"norm({raw!r}) == {want!r}", got == want and type(got) is type(want), f"got {got!r}")
check("a timestamp becomes an ISO string",
      handler.norm(dt.datetime(2026, 4, 11, 5, 31, 5)) == "2026-04-11T05:31:05")

print("\n6. chunk sizing stays inside PostgreSQL's 65,535 bound-parameter cap")
widest = max(len(v["columns"]) for v in DUMP["tables"].values())
chunk = max(1, min(handler.CHUNK_ROWS, handler.MAX_BIND_PARAMS // widest))
check(f"widest table is {widest} columns -> chunk {chunk} rows "
      f"= {chunk * widest:,} params", chunk * widest < 65535)

print("\n7. the jsonb boundary")
check("only ps3_run_stage_audit.model_runs is non-scalar in the real run",
      [f"{t}.{c['name']}" for t, v in DUMP["tables"].items() for c in v["columns"]
       if not c["is_scalar"]] == ["ps3_run_stage_audit.model_runs"])

print()
print("FAILURES:", fails or "none")
sys.exit(1 if fails else 0)
