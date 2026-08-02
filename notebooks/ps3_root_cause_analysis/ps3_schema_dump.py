# =============================================================================
# PS3 SCHEMA DUMP -- run this AFTER the V25 run completes, in the same kernel.
#
# The PS2 dashboard schema (sql/44) was written against real manifests rather
# than guessed, which is why it applied 28/28 first time. This does the same for
# PS3: it prints the exact column name, pandas dtype, null rate and a sample
# value for every published table, which is what the SQL DDL has to be built
# from. Read-only; it touches nothing but the in-memory result.
# =============================================================================
import json
import pandas as pd

if "results" not in dir() or "tables" not in results:
    raise RuntimeError("Run the V25 notebook first; `results` is not in this session.")

spec = {"run_id": results["run_id"],
        "revision": PS3V21_CONFIG["PS3_REVISION"],
        "computed_date": PS3V21_CONFIG["DATA_AS_OF_DATE"],
        "tables": {}}

for name, frame in sorted(results["tables"].items()):
    columns = []
    for column in frame.columns:
        series = frame[column]
        non_null = series.dropna()
        sample = None
        if len(non_null):
            sample = non_null.iloc[0]
            sample = sample.isoformat() if hasattr(sample, "isoformat") else str(sample)[:60]
        columns.append({
            "name": column,
            "pandas_dtype": str(series.dtype),
            "null_rate": round(float(series.isna().mean()), 4),
            "distinct": int(series.nunique(dropna=True)) if series.nunique(dropna=True) < 10_000 else ">10000",
            "sample": sample,
        })
    spec["tables"][name] = {"rows": int(len(frame)), "columns": columns}

print(json.dumps(spec, indent=1, default=str))
print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
summary = pd.DataFrame([{"table": t, "rows": v["rows"], "columns": len(v["columns"])}
                        for t, v in sorted(spec["tables"].items())])
print(summary.to_string(index=False))
print(f"\ntotal columns across {len(spec['tables'])} tables: {int(summary['columns'].sum())}")
