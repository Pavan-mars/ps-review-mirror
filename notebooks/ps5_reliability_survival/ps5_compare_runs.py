"""Compare two PS5 runs from their local outputs -- no S3, no database.

    python ps5_compare_runs.py snapshot baseline.json      # summarise the current outputs
    python ps5_compare_runs.py compare  baseline.json      # summarise again and print side by side

Run `snapshot` BEFORE the comparison run (the notebook overwrites its local output folder), then
`compare` after it. Reads <fleet>_device_rul_estimates.csv and <fleet>_cindex_leaderboard_v5.csv.
"""
import csv
import json
import statistics
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "PS5_reliability_v5_outputs"
FLEETS = {"GATE": "gates", "TVM": "tvm", "VALIDATOR": "validators"}
THRESHOLDS = {"GATE": 0.49, "TVM": 0.66, "VALIDATOR": 0.51}   # sql/72


def _q(vals, q):
    vals = sorted(vals)
    return round(vals[min(int(q * len(vals)), len(vals) - 1)], 4) if vals else None


def summarise():
    res = {}
    for fleet, sub in FLEETS.items():
        rows = list(csv.DictReader(open(OUT / sub / f"{sub}_device_rul_estimates.csv")))
        p1 = [float(r["p_oos_1d"]) for r in rows if r.get("p_oos_1d") not in (None, "")]
        rul = [float(r["rul_standard_days"]) for r in rows if r.get("rul_standard_days") not in (None, "")]
        lb = list(csv.DictReader(open(OUT / sub / f"{sub}_cindex_leaderboard_v5.csv")))
        best = max((float(r["oot_cindex"]) for r in lb if r.get("oot_cindex") not in (None, "", "nan")), default=None)
        res[fleet] = {
            "devices": len(rows),
            "overdue_share": round(sum(r["is_overdue"] in ("True", "true", "1") for r in rows) / max(len(rows), 1), 3),
            "median_rul_days": round(statistics.median(rul), 2) if rul else None,
            "p1_p10": _q(p1, 0.10), "p1_p50": _q(p1, 0.50), "p1_p90": _q(p1, 0.90),
            "p1_spread_p90_p10": round(_q(p1, 0.90) - _q(p1, 0.10), 4) if p1 else None,
            "act_now_at_current_threshold": sum(v >= THRESHOLDS[fleet] for v in p1),
            "best_cindex": round(best, 4) if best is not None else None,
        }
    return res


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("snapshot", "compare"):
        sys.exit(__doc__)
    now = summarise()
    if sys.argv[1] == "snapshot":
        json.dump(now, open(sys.argv[2], "w"), indent=2)
        print(json.dumps(now, indent=2))
        return
    base = json.load(open(sys.argv[2]))
    keys = list(next(iter(now.values())).keys())
    for fleet in FLEETS:
        print(f"\n{fleet:10s} {'baseline':>12s} {'this run':>12s}")
        for k in keys:
            print(f"  {k:30s} {str(base[fleet].get(k)):>12s} {str(now[fleet].get(k)):>12s}")


if __name__ == "__main__":
    main()
