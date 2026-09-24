# ── Where does each source chain actually end?  bronze -> silver -> gold ─────────
# Read-only. Databricks, Unity Catalog. Distinguishes a FEED gap (bronze stops too)
# from a REBUILD gap (bronze holds newer data that silver has not picked up).
from pyspark.sql import functions as F

CAT = "mars_dev"
CHAINS = [
    ("PS1 label spine", [("bronze", "edw_device_event"), ("bronze", "device_event"),
                         ("silver", "device_event_enriched"), ("gold", "device_ps1_daily")]),
    ("metric",          [("bronze", "edw_device_metric"),
                         ("silver", "metric_daily"), ("silver", "usage_lifecycle_daily")]),
    ("availability",    [("bronze", "edw_availability_events"), ("silver", "kpi_avail_enriched")]),
    ("taps",            [("bronze", "edw_abp_tap"), ("bronze", "abp_tap"),
                         ("silver", "tap_event_daily")]),
    ("ServiceNow",      [("bronze", "servicenow_incident_conformed"),
                         ("silver", "servicenow_incident_conformed"),
                         ("bronze", "servicenow_incident"),
                         ("silver", "incident_history"),
                         ("silver", "device_incident_features_daily")]),
]
# first match wins, case-insensitive. TRANSIT_DAY_KEY is yyyyMMdd.
DATE_CANDIDATES = ["transit_day", "TRANSIT_DAY_KEY", "EVENT_DTM", "incident_date",
                   "opened_at", "eod_date", "failure_date", "AE_START_DTM", "event_date",
                   "TRANSACTION_DTM"]   # edw_abp_tap has no TRANSIT_DAY_KEY


def _day(col):
    if col.upper() == "TRANSIT_DAY_KEY":
        return F.to_date(F.col(col).cast("string"), "yyyyMMdd")
    return F.to_date(F.col(col))


def measure(layer, tbl):
    # Touch .columns INSIDE the try. Under Spark Connect spark.table() is lazy: a missing
    # table raises only when its schema is first resolved, which would otherwise happen
    # outside the handler and abort the whole run on the first absent alternative.
    try:
        df = spark.table(f"{CAT}.{layer}.{tbl}")
        cols = df.columns
    except Exception:
        return None                                   # table absent -- skipped, not an error
    # Anything else that goes wrong is reported against THIS table and the run continues.
    try:
        lc = {c.casefold(): c for c in cols}
        col = next((lc[c.casefold()] for c in DATE_CANDIDATES if c.casefold() in lc), None)
        if col is None:
            return {"col": None, "max": None, "fut": 0, "rows": df.count(), "parsed": 0}
        d = _day(col)
        r = df.agg(F.count(F.lit(1)).alias("rows"),
                   F.count(d).alias("parsed"),
                   # future-dated keys exist in this estate; never let one set the max
                   F.max(F.when(d <= F.current_date(), d)).alias("max"),
                   F.sum(F.when(d > F.current_date(), 1).otherwise(0)).alias("fut")).collect()[0]
        return {"col": col, "max": r["max"], "fut": r["fut"] or 0,
                "rows": r["rows"], "parsed": r["parsed"]}
    except Exception as exc:
        return {"col": None, "max": None, "fut": 0, "rows": 0, "parsed": 0,
                "err": f"{type(exc).__name__}: {str(exc).splitlines()[0][:90]}"}


print("=" * 104)
print("SOURCE CHAINS  bronze -> silver -> gold      (max = latest date on or before today)")
print("=" * 104)
SUMMARY = []
for chain, members in CHAINS:
    print(f"\n{chain}")
    got = []
    for layer, tbl in members:
        m = measure(layer, tbl)
        if m is None:
            print(f"   {layer:6s} {tbl:32s} (absent)")
            continue
        note = ""
        if m.get("err"):
            note = "ERR " + m["err"]
        elif m["col"] is None:
            note = "no recognised date column"
        elif m["rows"] and not m["parsed"]:
            note = f"DATES UNPARSEABLE in {m['col']} -- check the format"
        elif m["fut"]:
            note = f"{m['fut']:,} future-dated rows ignored"
        print(f"   {layer:6s} {tbl:32s} {str(m['max']):>12s}  via {str(m['col']):16s} "
              f"{m['rows']:>14,} rows  {note}")
        got.append((layer, tbl, m["max"]))
    b = [x for l, _, x in got if l == "bronze" and x]
    s = [(t, x) for l, t, x in got if l == "silver" and x]
    if b and s:
        bmax, (stbl, smax) = max(b), s[0]
        lag = (bmax - smax).days
        v = (f"SILVER LAGS BRONZE by {lag}d -> a rebuild of {stbl} extends this chain to {bmax}"
             if lag > 0 else "silver in step with bronze -> any gap is upstream, in the feed itself")
        print(f"   => {v}")
        SUMMARY.append((chain, bmax, stbl, smax, lag))

print("\n8<" + "-" * 102)
print("[CHAINS] chain | bronze_max | first_silver | silver_max | lag_days")
for row in SUMMARY:
    print("  " + " | ".join(str(x) for x in row))
print("-" * 104 + ">8")
