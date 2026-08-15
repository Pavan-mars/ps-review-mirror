# =============================================================================
# PS5 READ PATCH -- paste AFTER cell 3 (engine), BEFORE the run cell.
#
# THE PROBLEM, from your own trace:
#     [read] device_event_enriched  29,396,298 rows  360.9s (parquet)
#
# Three compounding costs, none of them necessary:
#
#   1. ALL 76 COLUMNS. load_hw_oos_failures() calls load_table() without the
#      `columns` argument, so every column comes back. We need eight.
#   2. NO PREDICATE PUSHDOWN. is_hardware_oos_event is applied in pandas AFTER
#      29.4M rows are already in memory. Pushed into the parquet read it prunes
#      row groups and never materialises most of them.
#   3. coerce() THEN WALKS ALL OF IT. A per-column Decimal conversion over
#      29.4M x 76. This is where the last hour went -- the read was only 361s.
#
# The patch changes ONLY the read. Every filter, the dedup, and the contract
# guard downstream are untouched: same predicate, same episodes, same result.
#
# Expected: 360s + hours  ->  well under a minute per fleet.
# =============================================================================
import pandas as pd, time

_orig_load_table = load_table

# Exactly what load_hw_oos_failures() touches. Anything absent is skipped
# gracefully by the reader, so a schema change degrades rather than crashes.
_FAILURE_COLS = [
    "DEVICE_ID", "DEVICE_KEY", "mars_device_category",
    "transit_day", "EVENT_DTM",
    "EVENT_STATE_TYPE_NAME",
    "is_hardware_oos_event", "is_commanded_oos_event",
    "is_device_fault", "failure_level",
    "duration_to_clear_min",
    "COMPONENT_SERIAL_NBR",
]

def load_table(table, cat=None, catcol="mars_device_category", required=True, columns=None):
    """Column-pruned, predicate-pushed read for the failure table only.

    Everything else -- the nine ENRICH_SOURCES included -- falls through to the
    original implementation unchanged.
    """
    if columns is None and "device_event_enriched" in table:
        t0 = time.time()
        path = _puri(table)
        flt = [("is_hardware_oos_event", "==", True)]
        if cat:
            flt.append((catcol, "==", cat))
        rk = {"storage_options": {"client_kwargs": {"region_name": CONFIG["S3_REGION"]}}} \
             if path.startswith("s3") else {}
        for attempt in ({"filters": flt, "columns": _FAILURE_COLS},
                        {"filters": flt},
                        {"columns": _FAILURE_COLS},
                        {}):
            try:
                df = pd.read_parquet(path, **attempt, **rk)
                if cat and catcol in df.columns:
                    df = df[df[catcol] == cat]
                print(f"      [read-fast] {table.rsplit('.',1)[-1]:30s} {len(df):>10,} rows "
                      f"{time.time()-t0:6.1f}s  cols={len(df.columns)} "
                      f"pushdown={'filters' in attempt}", flush=True)
                return df
            except Exception as e:
                last = e
                continue
        print(f"      [read-fast] pushdown failed ({type(last).__name__}); "
              f"falling back to the full read")
    return _orig_load_table(table, cat=cat, catcol=catcol,
                            required=required, columns=columns)

print("read patch active:")
print(f"  failure table  -> {len(_FAILURE_COLS)} columns (was 76) "
      f"+ is_hardware_oos_event pushed into the parquet read")
print("  enrich sources -> unchanged")
print("\nSanity check before the long run -- reads TVM and reports the shape:")
_t = time.time()
_probe = load_table(CONFIG["FAILURE_TABLE"], cat="TVM")
print(f"  TVM failure rows: {len(_probe):,} in {time.time()-_t:.1f}s")
print(f"  columns: {list(_probe.columns)}")
_have = [c for c in ("is_hardware_oos_event", "EVENT_STATE_TYPE_NAME") if c in _probe.columns]
print(f"  contract columns present: {_have}")
assert "is_hardware_oos_event" in _probe.columns, \
    "is_hardware_oos_event missing -- the contract guard would raise. Do not run."
assert "EVENT_STATE_TYPE_NAME" in _probe.columns, \
    "EVENT_STATE_TYPE_NAME missing -- the state gate would never fire. Do not run."
print("  OK -- both contract columns survived the pruning. Safe to run.")
del _probe
