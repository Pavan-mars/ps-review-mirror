# ===========================================================================================
# NB16 DIAGNOSTIC — paste as a new cell AFTER cell 1 (needs ora(), _col()).  Read-only, ~1 min.
#
# Answers the two questions the A_FAST run raised:
#   1. Which bronze column of edw_read_transaction is absent from EDW.READ_TRANSACTION?
#      (that missing column became a NullType and Parquet refused it)
#   2. Which audit batch column does each bronze table actually carry -
#      `_batch_id` (ours) or `_raw_batch_id` (the original loader's)?
# ===========================================================================================
print("=" * 110)
print("1. SCHEMA DIFF — bronze vs Oracle, for the CDC tables")
print("=" * 110)
for t, src in [("edw_read_transaction", "EDW.READ_TRANSACTION"),
               ("ncs_stage_sale_transaction", "NCS_STAGE.SALE_TRANSACTION"),
               ("edw_device_event", "EDW.DEVICE_EVENT"),
               ("edw_abp_tap", "EDW.ABP_TAP")]:
    try:
        bdf = spark.table(f"mars_dev.bronze.`{t}`")
        # WHERE 1=0 returns the column list with zero rows - instant, no scan
        ocols = [c.upper() for c in ora(f"(SELECT * FROM {src} WHERE 1=0) q", 60).columns]
        bcols = [c for c in bdf.columns
                 if not c.startswith("_") and c.lower() not in ("year", "month", "day")]
        missing_in_ora = [c for c in bcols if c.upper() not in ocols]
        extra_in_ora = [c for c in ocols if c not in [b.upper() for b in bcols]]
        btypes = dict(bdf.dtypes)
        print(f"\n{t}  ({src})")
        print(f"  bronze business cols: {len(bcols)}   oracle cols: {len(ocols)}")
        if missing_in_ora:
            print(f"  ** BRONZE COLUMNS ABSENT FROM ORACLE (these become NullType -> Parquet fails):")
            for c in missing_in_ora:
                print(f"       {c}  (bronze type: {btypes.get(c)})")
        else:
            print("  bronze columns all present in Oracle - no NullType risk")
        if extra_in_ora:
            print(f"  oracle columns not in bronze (ignored by align_to): {extra_in_ora}")
    except Exception as e:
        print(f"\n{t}: ERROR {str(e).splitlines()[0][:80]}")

print("\n" + "=" * 110)
print("2. AUDIT BATCH COLUMN — which name does each bronze table carry?")
print("=" * 110)
rows = []
for c in ALLC:
    t = c["table"]
    try:
        cols = spark.table(f"mars_dev.bronze.`{t}`").columns
        has_new = "_batch_id" in cols
        has_old = "_raw_batch_id" in cols
        which = "_batch_id" if has_new else ("_raw_batch_id" if has_old else "NONE")
        rows.append((t, which, has_new, has_old))
    except Exception:
        rows.append((t, "no table", False, False))
print(f"{'table':42s} {'batch column':16s}")
for t, which, _, _ in sorted(rows, key=lambda x: (x[1], x[0])):
    flag = "  <-- engine writes _batch_id; mismatch" if which == "_raw_batch_id" else ""
    print(f"{t:42s} {which:16s}{flag}")
n_old = sum(1 for r in rows if r[1] == "_raw_batch_id")
n_new = sum(1 for r in rows if r[1] == "_batch_id")
print(f"\n_batch_id: {n_new}   _raw_batch_id: {n_old}   none/missing: {len(rows) - n_new - n_old}")

print("\n" + "=" * 110)
print("3. THIS RUN'S APPENDED ROWS — did the batch stamp land on sale_transaction?")
print("=" * 110)
try:
    sdf = spark.table("mars_dev.bronze.`ncs_stage_sale_transaction`")
    bc = "_batch_id" if "_batch_id" in sdf.columns else "_raw_batch_id"
    from pyspark.sql import functions as _F
    tot = sdf.count()
    nulls = sdf.where(_F.col(bc).isNull()).count()
    post = sdf.where(_F.col("DW_INSERTED_DAY") > 20260411).count()
    post_null = sdf.where((_F.col("DW_INSERTED_DAY") > 20260411) & _F.col(bc).isNull()).count()
    print(f"  batch column in use: {bc}")
    print(f"  total rows           {tot:,}")
    print(f"  NULL {bc:16s} {nulls:,}")
    print(f"  rows post-11-Apr     {post:,}   of which NULL batch: {post_null:,}")
    if post_null:
        print(f"  -> the {post_null:,} rows this run appended have no lineage stamp (data is correct;")
        print(f"     only the audit column is blank). Backfill statement, if wanted:")
        print(f"     UPDATE mars_dev.bronze.`ncs_stage_sale_transaction` SET {bc} = '<BATCH_ID>'")
        print(f"     WHERE {bc} IS NULL AND DW_INSERTED_DAY > 20260411;")
except Exception as e:
    print(f"  ERROR {str(e).splitlines()[0][:80]}")
