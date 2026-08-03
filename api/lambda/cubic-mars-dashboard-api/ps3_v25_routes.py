# ============ PS3 v2.5 source-first generation (sql/45) =====================
# ONE route family over the 20 tables sql/45 creates, plus /ps3/status.
#
# THESE ARE NEW ROUTES, NOT REPLACEMENTS. /ps3/rootcause, /ps3/incidents and
# every other existing PS3 path still reads ps3_v2_* / ps3_incident_predictions
# and is untouched. This family lives under /ps3/v25/ and can be removed by
# deleting this block.
#
# THE COLUMN LISTS ARE GENERATED FROM THE V26 SCHEMA DUMP, not typed. The PS2
# family was hand-typed and its lesson is already in the backlog: a column the
# export starts carrying stays invisible to the API until someone remembers to
# add it here. So the 22 columns that are all-null today -- the linked_* and
# confirmed_root_cause_* family, which wait on PS3_GOLD_INCIDENT_LABEL_EXPORT
# and the taxonomy export -- are LISTED anyway. They return null now and
# populate themselves the day those exports are configured.
#
# EVERY IDENTIFIER IS QUOTED. "column" is reserved in PostgreSQL and "rows" is
# a window-frame keyword; both are real column names in these tables. This
# project already lost a load to an unquoted "window" after eight tables had
# been staged, so the class goes rather than the instances.
#
# EVERY QUERY IS SCOPED TO THAT TABLE'S OWN LATEST computed_date, matching the
# PS2 family. The v25 loader refuses to publish a partial run at all, so they
# should never disagree -- but scoping independently means a half-refreshed
# database degrades to "one panel is stale" rather than "one panel is empty".
#
# CAPS. API Gateway kills the integration at 30s. device_episode_fact and
# device_day hold 54,239 rows each; both are browse windows, never a
# denominator. The rollups are the denominators.
#
# (table, select list, order by, default limit, max limit, date column or None)
_PS3V25 = {
    # ps3_v25_causal_balance: 54 rows, 4 columns
    "causal-balance": ("ps3_v25_causal_balance",
        '"treatment_component","covariate","standardised_mean_difference","balance_status"',
        '"treatment_component", "covariate"', 500, 2000, None),
    # ps3_v25_causal_effects: 6 rows, 22 columns
    "causal-effects": ("ps3_v25_causal_effects",
        '"treatment_component","outcome","estimator","average_treatment_effect",'
        '"standard_error","ci_low_95","ci_high_95","significant_95","episodes_used",'
        '"treated_episodes","control_episodes","overlap_share","treated_prevalence",'
        '"propensity_p01","propensity_p99","models_converged","status","interpretation",'
        '"p_value_two_sided","p_value_holm","significant_95_holm","multiplicity_note"',
        '"treatment_component", "outcome"', 200, 1000, None),
    # ps3_v25_source_column_profile: 7 rows, 9 columns
    "column-profile": ("ps3_v25_source_column_profile",
        '"column","status","rows","non_null","null_rate","distinct_values",'
        '"deterministic_given_event_type","usable_as_observed_label","note"',
        '"column"', 50, 200, None),
    # ps3_v25_commanded_split: 3 rows, 5 columns
    "commanded-split": ("ps3_v25_commanded_split",
        '"mars_device_category","oos_episodes","commanded_signal_episodes",'
        '"failure_only_episodes","basis"',
        '"mars_device_category"', 50, 200, None),
    # ps3_v25_component_summary: 14 rows, 7 columns
    "component-summary": ("ps3_v25_component_summary",
        '"mars_device_category","component_attribution","dashboard_root_cause_domain",'
        '"dashboard_severity","oos_episode_count","device_count","confirmed_root_cause_count"',
        '"oos_episode_count" DESC', 200, 2000, None),
    # ps3_v25_device_day: 54,239 rows, 11 columns
    "device-day": ("ps3_v25_device_day",
        '"device_id","mars_device_category","event_date","oos_episode_starts","oos_set_events",'
        '"set_signal_span_minutes","commanded_signal_episodes","observed_severity_episodes",'
        '"confirmed_root_cause_episodes","failure_only_episode_starts","grain"',
        '"event_date" DESC, "device_id"', 500, 5000, 'event_date'),
    # ps3_v25_device_reliability: 2,806 rows, 16 columns
    "device-reliability": ("ps3_v25_device_reliability",
        '"device_id","mars_device_category","oos_episode_count","oos_set_event_count",'
        '"critical_episodes","first_episode_at","latest_episode_at","mean_interval_hours",'
        '"median_interval_hours","confirmed_root_cause_episodes","observed_severity_episodes",'
        '"commanded_signal_episodes","critical_rate","failure_only_episode_count",'
        '"reliability_risk_band","band_basis"',
        '"oos_episode_count" DESC', 500, 5000, None),
    # ps3_v25_device_summary: 2,806 rows, 9 columns
    "device-summary": ("ps3_v25_device_summary",
        '"device_id","mars_device_category","oos_episode_count","first_oos_episode_start",'
        '"latest_oos_episode_start","latest_dashboard_severity",'
        '"latest_dashboard_root_cause_domain","confirmed_root_cause_episode_count",'
        '"observed_severity_episode_count"',
        '"oos_episode_count" DESC', 500, 5000, None),
    # ps3_v25_device_episode_fact: 54,239 rows, 77 columns
    "episodes": ("ps3_v25_device_episode_fact",
        '"oos_episode_id","device_id","mars_device_category","episode_start",'
        '"episode_last_signal","oos_set_event_count","first_oos_event_id",'
        '"contains_commanded_oos_signal","observed_event_component","component_subsystem",'
        '"component_position","facility_id","facility_name","bus_id","component_serial_nbr",'
        '"event_type_name","event_type_id","observed_event_severity","event_type_severity",'
        '"event_priority","requires_service_call","any_automatic_clear",'
        '"distinct_serials_in_episode","distinct_components_in_episode",'
        '"set_signal_span_minutes","oos_fact_definition","oos_minutes_union",'
        '"oos_minutes_naive_sum","events_with_clear","events_clear_clamped",'
        '"episode_scope_status","episode_scope_start","linked_severity","linked_component",'
        '"linked_root_cause","linked_root_cause_domain","linked_confidence","linked_source",'
        '"link_method","observed_severity_label","severity_status","component_attribution",'
        '"component_attribution_status","confirmed_root_cause_label",'
        '"confirmed_root_cause_domain","root_cause_confidence","root_cause_evidence_source",'
        '"root_cause_link_method","root_cause_status","candidate_root_cause_raw",'
        '"evidence_conflict_status","event_month","event_day_of_week","event_hour",'
        '"log_oos_set_event_count","log_set_signal_span_minutes","prior_episodes_7d",'
        '"prior_episodes_30d","prior_episodes_90d","days_since_prior_episode",'
        '"device_oos_recency_status","predicted_component","predicted_component_confidence",'
        '"component_model_status","run_id","computed_at_utc","data_as_of_date",'
        '"data_freshness_days","is_current_operational_score","freshness_status",'
        '"right_censored_tail_days","chargeability_policy","shap_interpretation",'
        '"dashboard_root_cause_domain","dashboard_root_cause_status","dashboard_severity",'
        '"dashboard_severity_status"',
        '"episode_start" DESC', 200, 2000, 'episode_start'),
    # ps3_v25_root_cause_evidence_audit: 7 rows, 5 columns
    "evidence-audit": ("ps3_v25_root_cause_evidence_audit",
        '"source","status","reference","rows","detail"',
        '"source"', 50, 200, None),
    # ps3_v25_prediction_explainability: 3 rows, 12 columns
    "explainability": ("ps3_v25_prediction_explainability",
        '"target","model_output","oos_episode_id","device_id","mars_device_category",'
        '"predicted_label","feature","shap_value","abs_shap_value","explanation_status",'
        '"explanation_note","model_scope"',
        '"target", "abs_shap_value" DESC', 200, 1000, None),
    # ps3_v25_facility_rollup: 366 rows, 11 columns
    "facility-rollup": ("ps3_v25_facility_rollup",
        '"facility_id","mars_device_category","devices","oos_episodes","first_episode",'
        '"latest_episode","active_days","commanded_signal_episodes",'
        '"confirmed_root_cause_episodes","episodes_per_device","failure_only_episodes"',
        '"oos_episodes" DESC', 500, 2000, None),
    # ps3_v25_model_feature_importance: 12 rows, 5 columns
    "feature-importance": ("ps3_v25_model_feature_importance",
        '"target","model","feature","importance","model_scope"',
        '"target", "importance" DESC', 200, 1000, None),
    # ps3_v25_label_maturity: 3 rows, 8 columns
    "label-maturity": ("ps3_v25_label_maturity",
        '"mars_device_category","oos_episode_count","observed_severity_count",'
        '"confirmed_root_cause_count","candidate_root_cause_count",'
        '"component_attribution_count","severity_coverage","confirmed_root_cause_coverage"',
        '"mars_device_category"', 50, 200, None),
    # ps3_v25_model_scorecard: 8 rows, 13 columns
    "model-scorecard": ("ps3_v25_model_scorecard",
        '"target","candidate_model","f1_macro","f1_weighted","balanced_accuracy","accuracy",'
        '"mcc","majority_f1_macro","macro_f1_lift","label_coverage","quality_gate","detail",'
        '"model_scope"',
        '"target", "candidate_model"', 100, 500, None),
    # ps3_v25_repeat_interval: 14 rows, 11 columns
    "repeat-interval": ("ps3_v25_repeat_interval",
        '"component_attribution","mars_device_category","attributed_episodes","devices",'
        '"episodes_with_a_next","median_days_to_next","p25_days_to_next","mean_days_to_next",'
        '"repeat_rate_within_horizon","horizon_days","basis"',
        '"attributed_episodes" DESC', 200, 2000, None),
    # ps3_v25_run_stage_audit: 8 rows, 9 columns
    "run-stage-audit": ("ps3_v25_run_stage_audit",
        '"stage","status","detail","at_utc","rows","mode","evidence_sources","taxonomy_rows",'
        '"model_runs"',
        '"at_utc"', 100, 500, None),
    # ps3_v25_run_status: 19 rows, 13 columns
    "run-status": ("ps3_v25_run_status",
        '"run_id","revision","table_name","publish_status","rows","path","run_mode",'
        '"data_as_of_date","is_current_operational_score","computed_at_utc","run_is_coherent",'
        '"tables_published","tables_total"',
        '"table_name"', 100, 500, None),
    # ps3_v25_serial_reliability: 2,762 rows, 10 columns
    "serial-reliability": ("ps3_v25_serial_reliability",
        '"component_serial_nbr","device_id","mars_device_category","oos_episode_count",'
        '"first_episode_at","latest_episode_at","component_attributions",'
        '"confirmed_root_cause_episodes","observed_span_days","episodes_per_100_observed_days"',
        '"oos_episode_count" DESC', 500, 5000, None),
    # ps3_v25_oos_source_audit: 1 rows, 12 columns
    "source-audit": ("ps3_v25_oos_source_audit",
        '"source","status","reference","detail","engine","pushed_down","scanned_rows",'
        '"window_start","window_end","current_device_filter","rows_removed_by_current_filter",'
        '"selected_rows"',
        '"source"', 50, 200, None),
}

_PS3V25_FILTERS = (
    ("category",  "mars_device_category"),
    ("device",    "device_id"),
    ("serial",    "component_serial_nbr"),
    ("facility",  "facility_id"),
    ("component", "component_attribution"),
    ("episode",   "oos_episode_id"),
    ("target",    "target"),
)


def _ps3_v25_route(path, params, city):
    """Returns a response for /ps3/v25/* and /ps3/status, else None."""
    params = params or {}

    if path == "/ps3/status":
        # Two sources, deliberately. v_ps3_v25_status counts what actually
        # landed in each table; ps3_v25_run_status is what the NOTEBOOK said it
        # published. Reading only the first cannot tell a complete load of a
        # broken run from a broken load of a complete run.
        data = rows(
            'SELECT "table_name", "computed_date", "rows" '
            "FROM v_ps3_v25_status WHERE city_id=:c ORDER BY table_name", c=city)
        try:
            declared = rows(
                'SELECT "run_id","revision","run_mode","computed_date","data_as_of_date",'
                '"run_is_coherent","tables_published","tables_total",'
                '"is_current_operational_score","computed_at_utc" '
                "FROM ps3_v25_run_status WHERE city_id=:c "
                "AND computed_date=(SELECT MAX(computed_date) FROM ps3_v25_run_status "
                "WHERE city_id=:c) LIMIT 1", c=city)
        except Exception as e:
            declared = []
            data.append({"table_name": "_run_status_read_error", "rows": str(e)[:160]})

        head = declared[0] if declared else {}
        dates = sorted({str(r["computed_date"]) for r in data if r.get("computed_date")})
        loaded = [r for r in data if r.get("table_name", "").startswith("ps3_v25_")]
        empty = sorted(r["table_name"] for r in loaded if not r.get("rows"))
        return ok({
            "city": city,
            "generation": "ps3_v25",
            "tables": len(loaded),
            "expected_tables": len(_PS3V25),
            "computed_date": (dates[0] if len(dates) == 1 else dates),
            "run_id": head.get("run_id"),
            "revision": head.get("revision"),
            "run_mode": head.get("run_mode"),
            "data_as_of_date": head.get("data_as_of_date"),
            "computed_at_utc": head.get("computed_at_utc"),
            "is_current_operational_score": head.get("is_current_operational_score"),
            "notebook_tables_published": head.get("tables_published"),
            "notebook_tables_total": head.get("tables_total"),
            "notebook_run_is_coherent": head.get("run_is_coherent"),
            # coherent means three things at once: every expected table is
            # present, they all carry the same computed_date, and none is
            # empty. Any one of those failing is a half-loaded dashboard.
            "coherent": (len(loaded) == len(_PS3V25) and len(dates) == 1 and not empty),
            "empty_tables": empty,
            "total_rows": sum(r.get("rows") or 0 for r in loaded),
            "rows": data,
        })

    if not path.startswith("/ps3/v25/"):
        return None

    metric = path[len("/ps3/v25/"):].strip("/")
    if metric == "":
        return ok({"metrics": sorted(_PS3V25),
                   "filters": [p for p, _ in _PS3V25_FILTERS],
                   "usage": "/ps3/v25/<metric>?city=CHI&category=GATE&limit=200"})
    spec = _PS3V25.get(metric)
    if spec is None:
        return err(404, "unknown PS3 v2.5 metric %r. Known: %s"
                        % (metric, ", ".join(sorted(_PS3V25))))

    table, cols, order, dflt, hard, datecol = spec
    where = ["city_id=:c",
             "computed_date=(SELECT MAX(computed_date) FROM %s WHERE city_id=:c)" % table]
    kw = {"c": city}

    have = cols.replace('"', "").split(",")
    for pname, col in _PS3V25_FILTERS:
        v = (params.get(pname) or "").strip()
        if v and col in have:
            where.append('"%s"=:%s' % (col, pname))
            kw[pname] = v.upper() if col == "mars_device_category" else v

    if datecol:
        for pname, op in (("from", ">="), ("to", "<=")):
            v = (params.get(pname) or "").strip()
            if v:
                where.append('"%s" %s :%s' % (datecol, op, pname))
                kw[pname] = v

    limit = _clamp_int(params.get("limit"), dflt, 1, hard)
    offset = _clamp_int(params.get("offset"), 0, 0, 1000000)
    sql = ('SELECT %s FROM %s WHERE %s ORDER BY %s LIMIT %d OFFSET %d'
           % (cols, table, " AND ".join(where), order, limit, offset))
    return ok(rows(sql, **kw))
