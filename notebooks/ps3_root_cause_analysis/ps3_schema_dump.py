# =============================================================================
# PS3 SCHEMA DUMP v2 -- run AFTER the V25 run completes, in the same kernel.
#
# The PS2 dashboard schema (sql/44) was written against real manifests rather
# than guessed, which is why it applied 28/28 first time. This does the same for
# PS3: for every published table it reports each column's name, pandas dtype,
# null rate, distinct count and a sample value -- and, added in v2, the actual
# PYTHON TYPE of the values and whether the column is scalar.
#
# v2 FIX: v1 called nunique() on every column and died with
# "TypeError: unhashable type: 'list'". At least one column (ps3_run_stage_audit
# .model_runs) holds a list of dicts. That is not merely a crash to route
# around -- a non-scalar column has to be JSONB in Postgres, not TEXT, so the
# dump now detects and reports it instead of hiding it.
#
# Read-only; touches nothing but the in-memory result.
# =============================================================================
import json
import pandas as pd

if "results" not in dir() or "tables" not in results:
    raise RuntimeError("Run the V25 notebook first; `results` is not in this session.")


def profile_column(series):
    info = {"name": series.name, "pandas_dtype": str(series.dtype)}
    info["null_rate"] = round(float(series.isna().mean()), 4) if len(series) else 1.0
    non_null = series.dropna()

    element_types = sorted({type(v).__name__ for v in non_null.head(500)})
    info["python_types"] = element_types
    non_scalar = any(t in {"list", "dict", "ndarray", "set", "tuple"} for t in element_types)
    info["is_scalar"] = not non_scalar
    # This is the column that decides TEXT vs JSONB in the DDL.
    info["suggested_sql"] = "JSONB" if non_scalar else None

    if non_scalar:
        info["distinct"] = "not_hashable"
    else:
        try:
            n = int(series.nunique(dropna=True))
            info["distinct"] = n if n < 10_000 else ">10000"
        except TypeError:
            info["distinct"] = "not_hashable"
            info["is_scalar"] = False
            info["suggested_sql"] = "JSONB"

    if len(non_null):
        value = non_null.iloc[0]
        info["sample"] = value.isoformat() if hasattr(value, "isoformat") else str(value)[:80]
    else:
        info["sample"] = None

    if pd.api.types.is_numeric_dtype(series) and len(non_null):
        try:
            info["min"] = float(non_null.min())
            info["max"] = float(non_null.max())
        except Exception:
            pass
    return info


spec = {
    "run_id": results["run_id"],
    "revision": PS3V21_CONFIG["PS3_REVISION"],
    "computed_date": PS3V21_CONFIG["DATA_AS_OF_DATE"],
    "run_mode": PS3V21_CONFIG["RUN_MODE"],
    "s3_output_prefix": PS3V21_CONFIG["S3_OUTPUT_PREFIX"],
    "tables": {},
}
problems = []

for name, frame in sorted(results["tables"].items()):
    columns = []
    for column in frame.columns:
        try:
            columns.append(profile_column(frame[column]))
        except Exception as exc:
            columns.append({"name": column, "pandas_dtype": str(frame[column].dtype),
                            "profile_error": f"{type(exc).__name__}: {exc}"})
            problems.append(f"{name}.{column}: {type(exc).__name__}")
    spec["tables"][name] = {"rows": int(len(frame)), "columns": columns}

print(json.dumps(spec, indent=1, default=str))
print()
print("=" * 72)
print("SUMMARY")
print("=" * 72)
summary = pd.DataFrame([
    {"table": t,
     "rows": v["rows"],
     "columns": len(v["columns"]),
     "json_columns": sum(1 for c in v["columns"] if c.get("suggested_sql") == "JSONB")}
    for t, v in sorted(spec["tables"].items())
])
print(summary.to_string(index=False))
print(f"\ntables: {len(spec['tables'])}  columns: {int(summary['columns'].sum())}  "
      f"needing JSONB: {int(summary['json_columns'].sum())}")
if problems:
    print("\ncolumns that could not be profiled (these need a decision, not a guess):")
    for p in problems:
        print("  ", p)
