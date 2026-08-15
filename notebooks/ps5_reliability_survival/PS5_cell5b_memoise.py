# -- Cell 5b -- MEMOISE THE EVENT READ ---------------------------------------
# Insert as a NEW cell between Cell 5 and Cell 6. Do not edit Cell 3.
# Then run Cell 6 unchanged.
#
# load_hw_oos_failures(cat) reads silver.device_event_enriched -- the largest
# table in the estate -- and is called THREE times per fleet:
#     build_frame()       -> survival intervals
#     score_device_rul()  -> as-of-run recency features
#     run_serial_grain()  -> component attribution
# Nothing caches it, so three fleets issue NINE full reads. That is the wall
# clock, and it is the crash: in run_serial_grain the event frame is alive at
# the same time as the component roster and their merge, and the validator
# roster is the big one. The kernel dying instead of raising is what an
# out-of-memory kill looks like from the notebook side.
#
# Safe to share one frame: all three callers, plus _asof_recency, only READ it
# -- no assignment into it, no .drop/.fillna on it, no inplace=True.
#
# Single-slot on purpose: a new fleet evicts the previous fleet's frame, so
# peak memory is ONE event frame, and main() does not have to change. That
# matters -- main() writes ps5_rds_load_manifest.json from one invocation, so
# splitting fleets across separate main() calls would leave a manifest holding
# only the last fleet and the loader would report the other two as missing.
#
# Reversible: rebinds one global. Re-run Cell 3 to restore the original.
import gc

if "_HWOOS_RAW" not in globals():
    _HWOOS_RAW = load_hw_oos_failures          # capture ONCE, so re-running
                                               # this cell cannot wrap a wrapper
_HWOOS_CACHE = {}


def load_hw_oos_failures(cat):
    k = str(cat).upper()
    if k not in _HWOOS_CACHE:
        if _HWOOS_CACHE:                       # new fleet -> free the old one
            _HWOOS_CACHE.clear()
            gc.collect()
        _HWOOS_CACHE[k] = _HWOOS_RAW(cat)
        try:
            _mb = _HWOOS_CACHE[k].memory_usage(deep=True).sum() / 1e6
            print(f"   [hw-oos] {k}: {len(_HWOOS_CACHE[k]):,} rows, {_mb:,.0f} MB "
                  f"-- read once, reused for intervals + recency + serial")
        except Exception:
            pass
    return _HWOOS_CACHE[k]


def drop_hwoos_cache():
    _HWOOS_CACHE.clear()
    gc.collect()


print("[patched] load_hw_oos_failures memoised, single-slot.")
print("          reads per run: 9 -> 3   |   peak resident event frames: 3 -> 1")
