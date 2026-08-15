# =============================================================================
# Generate the _PS3V25 route table from the REAL V26 schema dump.
#
# The PS2 route family was hand-typed and the pending-task list already carries
# the consequence: "explicit column lists mean new columns are invisible to the
# API until added to the entry". Two things follow from that, and both are
# decisions rather than conveniences:
#
# 1. THE COLUMN LISTS ARE GENERATED FROM THE DUMP, not typed. 271 columns typed
#    by hand is 271 chances to produce a name that 500s at runtime instead of
#    failing here.
#
# 2. THE 18 ALL-NULL COLUMNS ARE INCLUDED, not filtered out. They are null
#    because PS3_GOLD_INCIDENT_LABEL_EXPORT and the taxonomy export are not
#    configured yet -- not because they are dead. Excluding them would make the
#    dashboard silently blind to root cause on the day those exports land,
#    which is exactly the failure mode PS2 hit. They cost one null per row now
#    and need no code change later.
#
# EVERY IDENTIFIER IS QUOTED. "column" is a reserved word in PostgreSQL and
# "rows" is a keyword in window frames; both appear in these tables. This
# project already lost a load to an unquoted "window" after eight tables were
# staged, so the whole class goes rather than the instances that happen to bite.
# =============================================================================
import json

DUMP = json.load(open("/tmp/ps3_schema_real.json"))["tables"]

# Columns that never belong in an API response.
#   _episode_row_id  -- internal scratch, already excluded from the DDL
#   city_id          -- the caller supplied it; echoing it in every row is noise
#   computed_date    -- reported once by /ps3/status, not per row
HIDE = {"_episode_row_id", "city_id", "computed_date"}

# metric slug -> (source table, ORDER BY, default limit, max limit, date column)
#
# CAPS EXIST BECAUSE API GATEWAY KILLS THE INTEGRATION AT 30s.
# device_day and device_episode_fact hold 54,239 rows each. Both are browse
# windows and neither is ever a denominator -- the rollups are. A caller who
# wants a device's whole history pages it with ?device=&limit=&offset=.
ROUTES = {
    # -- the grain ------------------------------------------------------
    "episodes":      ("ps3_device_episode_fact", '"episode_start" DESC', 200, 1500, "episode_start"),
    "device-day":    ("ps3_device_day",          '"event_date" DESC, "device_id"', 500, 5000, "event_date"),
    # -- device and asset rollups ---------------------------------------
    "device-summary":     ("ps3_device_summary",      '"oos_episode_count" DESC', 500, 5000, None),
    "device-reliability": ("ps3_device_reliability",  '"oos_episode_count" DESC', 500, 5000, None),
    "serial-reliability": ("ps3_serial_reliability",  '"oos_episode_count" DESC', 500, 5000, None),
    "facility-rollup":    ("ps3_facility_rollup",     '"oos_episodes" DESC', 500, 2000, None),
    # -- component and root cause ---------------------------------------
    "component-summary":  ("ps3_component_summary",   '"oos_episode_count" DESC', 200, 2000, None),
    "repeat-interval":    ("ps3_repeat_interval",     '"attributed_episodes" DESC', 200, 2000, None),
    "commanded-split":    ("ps3_commanded_split",     '"mars_device_category"', 50, 200, None),
    "label-maturity":     ("ps3_label_maturity",      '"mars_device_category"', 50, 200, None),
    # -- causal ----------------------------------------------------------
    "causal-effects":     ("ps3_causal_effects",      '"treatment_component", "outcome"', 200, 1000, None),
    "causal-balance":     ("ps3_causal_balance",      '"treatment_component", "covariate"', 500, 2000, None),
    # -- models ----------------------------------------------------------
    "model-scorecard":    ("ps3_model_scorecard",     '"target", "candidate_model"', 100, 500, None),
    "feature-importance": ("ps3_model_feature_importance", '"target", "importance" DESC', 200, 1000, None),
    "explainability":     ("ps3_prediction_explainability", '"target", "abs_shap_value" DESC', 200, 1000, None),
    # -- provenance and audit --------------------------------------------
    "run-status":         ("ps3_run_status",          '"table_name"', 100, 500, None),
    "run-stage-audit":    ("ps3_run_stage_audit",     '"at_utc"', 100, 500, None),
    "source-audit":       ("ps3_oos_source_audit",    '"source"', 50, 200, None),
    "evidence-audit":     ("ps3_root_cause_evidence_audit", '"source"', 50, 200, None),
    "column-profile":     ("ps3_source_column_profile", '"column"', 50, 200, None),
}

# Per-metric notes emitted INTO the generated block. A rationale that lives
# only in the generator is a rationale the next reader of handler.py never
# sees -- and the max limit below is the kind of number that looks arbitrary
# and gets "tidied up" back to a round 2000.
NOTES = {
    "episodes": [
        "1500, NOT 2000. Measured against the live API on 03-Aug-2026:",
        "  limit=1000 -> 3,033,966 bytes  200 OK",
        "  limit=1800 -> 5,461,360 bytes  200 OK",
        "  limit=2000 -> HTTP 500 in 2.8s (far too fast to be the 30s gateway",
        "                                  timeout -- this is Lambda's 6 MB",
        "                                  synchronous response cap)",
        "3,034 bytes per row over 77 columns; 1500 lands at 4.5 MB.",
        "The headroom is not padding: 22 of those columns are all-null today and",
        "will carry real strings once the label and taxonomy exports are",
        "configured. The row gets WIDER, so a cap set flush against today's",
        "ceiling would start 500ing on the day root cause finally lands.",
    ],
}

assert set(ROUTES[k][0] for k in ROUTES) == set(DUMP), (
    "route table and dump disagree: "
    f"{set(DUMP) ^ set(ROUTES[k][0] for k in ROUTES)}")

lines = []
allnull_included = []
for slug in sorted(ROUTES):
    table, order, dflt, hard, datecol = ROUTES[slug]
    spec = DUMP[table]
    cols = [c for c in spec["columns"] if c["name"] not in HIDE]
    allnull_included += [f"{table}.{c['name']}" for c in cols if c["null_rate"] >= 1.0]
    sel = ",".join(f'"{c["name"]}"' for c in cols)
    tgt = "ps3_v25_" + table[len("ps3_"):]
    lines.append(f'    # {tgt}: {spec["rows"]:,} rows, {len(cols)} columns')
    for n in NOTES.get(slug, []):
        lines.append(f"    # {n}")
    lines.append(f'    "{slug}": ("{tgt}",')
    # wrap the select list so no source line runs past 100 chars
    buf = ""
    chunks = []
    for piece in sel.split(","):
        if len(buf) + len(piece) + 1 > 86:
            chunks.append(buf); buf = piece
        else:
            buf = piece if not buf else buf + "," + piece
    chunks.append(buf)
    for i, ch in enumerate(chunks):
        tail = "," if i == len(chunks) - 1 else ""
        lines.append(f"        '{ch}{'' if i == len(chunks)-1 else ','}'{tail}")
    # repr(), not raw interpolation: the ORDER BY string contains double quotes
    # and pasting it bare turned one tuple element into two.
    lines.append(f"        {order!r}, {dflt}, {hard}, "
                 f"{'None' if datecol is None else repr(datecol)}),")

open("/tmp/scratch/ps3/routes/_ps3v25_table.py", "w").write("\n".join(lines) + "\n")
print(f"metrics: {len(ROUTES)}")
print(f"columns exposed: {sum(len([c for c in DUMP[ROUTES[k][0]]['columns'] if c['name'] not in HIDE]) for k in ROUTES)}")
print(f"all-null columns deliberately included: {len(allnull_included)}")
for a in allnull_included:
    print("   ", a)
