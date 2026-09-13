# ===========================================================================================
# LAYER VERIFICATION — paste as a new cell after cell 1 of NB16. Read-only, no Oracle access.
#
# Proves, per table, that BOTH medallion layers hold the data:
#   RAW    = Parquet on the S3 raw bucket   (load_date=... for full, year=/month=/day= for CDC)
#   BRONZE = Delta table in Unity Catalog   (physically S3; DESCRIBE DETAIL shows where)
# ===========================================================================================
print("=" * 132)
print("BRONZE LAYER — format, physical location, files, size, rows")
print("=" * 132)
print(f"{'table':40s} {'format':8s} {'files':>7s} {'size_MB':>9s} {'rows':>14s}  location")
print("-" * 132)
bronze_info = {}
for c in sorted(ALLC, key=lambda x: x["table"]):
    t = c["table"]
    try:
        d = spark.sql(f"DESCRIBE DETAIL {BRONZE_DB}.`{t}`").first()
        n = spark.sql(f"SELECT COUNT(*) n FROM {BRONZE_DB}.`{t}`").first()["n"]
        loc = d["location"] or ""
        bronze_info[t] = {"format": d["format"], "location": loc, "rows": n,
                          "files": d["numFiles"], "bytes": d["sizeInBytes"]}
        flag = "" if d["format"] == "delta" else "   ** NOT DELTA **"
        print(f"{t:40s} {d['format']:8s} {d['numFiles']:>7,} "
              f"{(d['sizeInBytes'] or 0)/1048576:>9,.1f} {n:>14,}  {loc[-58:]}{flag}")
    except Exception as e:
        print(f"{t:40s} -- {str(e).splitlines()[0][:70]}")

print("\n" + "=" * 132)
print("RAW LAYER — S3 Parquet prefixes, newest partitions first")
print("=" * 132)
for c in sorted(ALLC, key=lambda x: x["table"]):
    t = c["table"]
    src = c.get("source") or ""
    if "." not in src:
        continue
    owner, otab = src.split(".", 1)
    raw_dir = f"{RAW_ROOT}/{owner.lower()}/{otab.lower()}/"
    try:
        parts = [f for f in dbutils.fs.ls(raw_dir) if f.name.endswith("/")]
        parts_sorted = sorted([p.name.rstrip("/") for p in parts], reverse=True)
        shown = ", ".join(parts_sorted[:4]) + (f" ... (+{len(parts_sorted)-4} more)"
                                               if len(parts_sorted) > 4 else "")
        print(f"{t:40s} {len(parts_sorted):>4} partition(s)  {shown}")
    except Exception as e:
        msg = str(e).splitlines()[0]
        state = "NO RAW PREFIX YET" if "FileNotFound" in msg or "not exist" in msg else msg[:60]
        print(f"{t:40s}    -- {state}")

print("\n" + "=" * 132)
print("TODAY'S WRITES — which tables got a raw partition dated today?")
print("=" * 132)
today_tag = f"load_date={TODAY.isoformat()}"
hits = []
for c in sorted(ALLC, key=lambda x: x["table"]):
    t, src = c["table"], (c.get("source") or "")
    if "." not in src:
        continue
    owner, otab = src.split(".", 1)
    raw_dir = f"{RAW_ROOT}/{owner.lower()}/{otab.lower()}/"
    try:
        names = [f.name.rstrip("/") for f in dbutils.fs.ls(raw_dir)]
        if today_tag in names:
            hits.append((t, today_tag))
        elif any(n.startswith("year=") for n in names):
            hits.append((t, "year=/month=/day= (CDC append - check the newest month)"))
    except Exception:
        pass
for t, why in hits:
    print(f"  {t:40s} {why}")
print(f"\n{len(hits)} table(s) show raw partitions. Bronze row counts above are the "
      f"authoritative check for the Delta layer.")
print("\nNote: bronze is Unity Catalog managed Delta - it IS on S3, under the location column")
print("above, as Parquet data files plus a _delta_log. Raw is plain Parquet with no log.")
