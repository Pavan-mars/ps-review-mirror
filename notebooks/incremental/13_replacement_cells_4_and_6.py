# PASTE-IN REPLACEMENTS for the running NB13 (keeps cell 3's query results in memory)
# Replace the FAILED analysis cell with REPLACEMENT CELL 4, run it, then run the DQ cell (5)
# unchanged, then replace the last cell with REPLACEMENT CELL 6 and run it.

# ============ REPLACEMENT CELL 4 ============
# ============================== CELL 4 : GAPS, VERDICTS, CSVs, SUMMARY ==============================
W0 = _dt.date.fromisoformat(WINDOW_START)

def day_range(a, b):
    d, out = a, []
    while d <= b: out.append(d); d += _dt.timedelta(days=1)
    return out

def compress(days):  # [date,...] -> "12-18 Apr, 03 May" style ranges
    if not days: return ""
    runs, s, p = [], days[0], days[0]
    for d in days[1:]:
        if (d - p).days > 1: runs.append((s, p)); s = d
        p = d
    runs.append((s, p))
    return "; ".join(a.isoformat() if a == b else f"{a.isoformat()}..{b.isoformat()}" for a, b in runs)

SUMMARY = []
for key, sd in sorted(SERIES.items()):
    label, sname = key.split("::")
    pts = sd["points"]
    if not pts:
        SUMMARY.append((label, sname, sd["grain"], None, None, 0, 0, "", "NO_DATA_IN_WINDOW")); continue
    first, last = _dt.date.fromisoformat(pts[0][0]), _dt.date.fromisoformat(pts[-1][0])
    total = sum(n for _, n in pts)
    if sd["grain"] == "monthly":
        verdict = "FROZEN" if (TODAY - last).days > 45 else "PRESENT (monthly grain)"
        SUMMARY.append((label, sname, "monthly", pts[0][0], pts[-1][0], total, len(pts), "", verdict)); continue
    have = {p[0] for p in pts}
    missing = [d for d in day_range(max(W0, first), last) if d.isoformat() not in have]
    stale = (TODAY - last).days
    avg = total / max(len(pts), 1)
    if stale > 14:                      verdict = f"FROZEN (last {last.isoformat()}, {stale}d stale)"
    elif avg < 50 and missing:          verdict = f"SPARSE (low-volume table; {len(missing)} empty days)"
    elif len(missing) == 0:             verdict = "CONTINUOUS"
    else:                               verdict = f"GAPS ({len(missing)} missing days)"
    SUMMARY.append((label, sname, "daily", pts[0][0], pts[-1][0], total, len(missing), compress(missing), verdict))
for key, err in ERRORS.items():
    label, sname = key.split("::")
    SUMMARY.append((label, sname, "-", None, None, None, None, "", f"UNKNOWN ({err[:60]})"))

S3_AUDIT = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra/_audit/continuity_" + TS
SAVED = {}
def save_csv(name, text):
    """Write a CSV where this cluster allows: DBFS FUSE -> dbutils dbfs -> S3 _audit prefix."""
    import os as _os
    errs = []
    try:
        d = f"/dbfs/FileStore/chicago_continuity/{TS}"
        _os.makedirs(d, exist_ok=True)
        with open(f"{d}/{name}", "w") as f: f.write(text)
        SAVED[name] = f"/FileStore/chicago_continuity/{TS}/{name}"
        print(f"saved {name} -> {SAVED[name]}  (download: <workspace-url>/files/chicago_continuity/{TS}/{name})"); return
    except Exception as e: errs.append(f"fuse: {str(e).splitlines()[0][:50]}")
    try:
        p = f"dbfs:/FileStore/chicago_continuity/{TS}/{name}"
        dbutils.fs.put(p, text, True)
        SAVED[name] = p
        print(f"saved {name} -> {p}  (download: <workspace-url>/files/chicago_continuity/{TS}/{name})"); return
    except Exception as e: errs.append(f"dbutils: {str(e).splitlines()[0][:50]}")
    try:
        p = f"{S3_AUDIT}/{name}"
        dbutils.fs.put(p, text, True)
        SAVED[name] = p
        print(f"saved {name} -> {p}  (fetch in CloudShell: aws s3 cp '{p}' .)"); return
    except Exception as e: errs.append(f"s3: {str(e).splitlines()[0][:50]}")
    print(f"COULD NOT SAVE {name} ({'; '.join(errs)}) — content follows:"); print(text)

buf = ["table,series,grain,first_day,last_day,rows_in_window,missing_days,missing_ranges,verdict"]
for r in SUMMARY:
    buf.append(",".join('"' + str(x if x is not None else "") + '"' for x in r))
save_csv("summary.csv", "\n".join(buf) + "\n")
buf = ["table,series,day,rows"]
for key, sd in sorted(SERIES.items()):
    label, sname = key.split("::")
    for d, n in sd["points"]: buf.append(f"{label},{sname},{d},{n}")
save_csv("daily_detail.csv", "\n".join(buf) + "\n")
buf = ["table,rows,max_dtm,days_stale"]
for label, v in sorted(FRESH.items()):
    stale = (TODAY - _dt.date.fromisoformat(v["max_dtm"][:10])).days if v["max_dtm"] else ""
    buf.append(f"{label},{v['rows']},{v['max_dtm']},{stale}")
save_csv("freshness.csv", "\n".join(buf) + "\n")

print(f"\n{'table':40s} {'series':9s} {'first':10s} {'last':10s} {'rows':>12s} {'gap_days':>8s}  verdict")
print("-" * 110)
for r in sorted(SUMMARY):
    print(f"{r[0]:40s} {r[1]:9s} {str(r[3]):10s} {str(r[4]):10s} "
          f"{(f'{r[5]:,}' if isinstance(r[5], int) else '-'):>12s} {str(r[6]):>8s}  {r[8]}")

payload = json.dumps({"probe": "13_DATA_CONTINUITY", "run_ts": TS, "window_start": WINDOW_START,
                      "series": SERIES, "freshness": FRESH, "errors": ERRORS, "summary": SUMMARY}, default=str)
spark.createDataFrame([("13_DATA_CONTINUITY", TS, payload)], "probe string, run_ts string, result_json string") \
     .write.format("delta").mode("append").saveAsTable("mars_dev.audit.probe_results")
for i in range(0, len(payload), 40000):
    print(f"<<<PART {i//40000 + 1}>>>"); print(payload[i:i+40000])
print("\nCSV locations:"); [print(f"  {k}: {v}") for k, v in SAVED.items()]

# ============ REPLACEMENT CELL 6 ============
# ============================== CELL 6 : ORACLE STATS SNAPSHOT + CONSOLIDATED DIGEST ==============================
EDW_T = ["DEVICE_EVENT","ABP_TAP","READ_TRANSACTION","DEVICE_METRIC","AVAILABILITY_EVENTS","AVAILABILITY_RELIEF",
         "DEVICE_DIMENSION","DEVICE_LAST_STATE","DEVICE_CURRENT_HW_CONFIG","KPI_DETAIL_EVENTS_BY_DAY",
         "KPI_SUMMARY_BY_DAY","KPI_RULES","KPI","KPI_TARGET","EVENT_TYPE_DIMENSION","METRIC_DIMENSION","USE_TRANSACTION"]
NCS_T = ["CASHBOX_TRACKING","SALE_TRANSACTION","DEVICE_END_OF_DAY","DEVICE_END_OF_DAY_MSG_COUNT","DEVICE",
         "DEVICE_TYPE","EVENT","STOP_POINT","TRANSIT_FACILITY"]
CTA_T = ["SERVICENOW_AVAILABILITY_EVENTS","SERVICENOW_DATA_FROM_JUMPBOX","KPI_AGENCY_MAP","KPI_MONTHLY_SUMMARY","SLDC_MONTHLY_SUMMARY"]
STATS = []
try:
    q = ("(SELECT owner, table_name, num_rows, TO_CHAR(last_analyzed,'YYYY-MM-DD') LA FROM all_tables WHERE "
         f"(owner='EDW' AND table_name IN ('" + "','".join(EDW_T) + "')) OR "
         f"(owner='NCS_STAGE' AND table_name IN ('" + "','".join(NCS_T) + "')) OR "
         f"(owner='CTA' AND table_name IN ('" + "','".join(CTA_T) + "'))) q")
    STATS = [r.asDict() for r in ora(q, 120).collect()]
    print(f"dictionary stats: {len(STATS)} tables")
except Exception as e:
    print("stats snapshot failed:", str(e).splitlines()[0][:80])

buf = ["test,table,value,detail,verdict"]
for t in DQ: buf.append(",".join('"' + str(x).replace('"', "'") + '"' for x in t))
save_csv("dq_findings.csv", "\n".join(buf) + "\n")
buf = ["owner,table_name,num_rows,last_analyzed"]
for s in STATS: buf.append(f"{s['OWNER']},{s['TABLE_NAME']},{s['NUM_ROWS']},{s['LA']}")
save_csv("oracle_stats.csv", "\n".join(buf) + "\n")

print("\n" + "=" * 100)
print("CONSOLIDATED ISSUE DIGEST (paste-ready for the mail)")
print("=" * 100)
print("\n[1] CONTINUITY - frozen feeds and gaps:")
for r in sorted(SUMMARY):
    if any(k in r[8] for k in ("FROZEN", "GAPS", "NO_DATA")):
        print(f"  - {r[0]} ({r[1]}): {r[8]}" + (f"  missing: {r[7]}" if r[7] else ""))
print("\n[2] FRESHNESS - tables stale > 14 days:")
for label, v in sorted(FRESH.items()):
    if v["max_dtm"]:
        stale = (TODAY - _dt.date.fromisoformat(v["max_dtm"][:10])).days
        if stale > 14: print(f"  - {label}: last change {v['max_dtm'][:10]} ({stale}d stale)")
print("\n[3] DATA QUALITY - source-side issues:")
for t in DQ:
    if t[4] == "ISSUE": print(f"  - {t[1]}: {t[0]} = {t[2]}  ({t[3]})")
print("\n[4] ERRORS / UNKNOWN (could not measure):")
for k, e in ERRORS.items(): print(f"  - {k}: {e[:80]}")
print("=" * 100)

payload2 = json.dumps({"probe": "13_DQ_TESTS", "run_ts": TS, "dq": DQ, "stats": STATS}, default=str)
spark.createDataFrame([("13_DQ_TESTS", TS, payload2)], "probe string, run_ts string, result_json string") \
     .write.format("delta").mode("append").saveAsTable("mars_dev.audit.probe_results")
for i in range(0, len(payload2), 40000):
    print(f"<<<PART {i//40000 + 1}>>>"); print(payload2[i:i+40000])
print("\nAll CSV locations:"); [print(f"  {k}: {v}") for k, v in SAVED.items()]
