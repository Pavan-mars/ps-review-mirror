# ============ PS2 v2.5 label-aligned generation (sql/44) ====================
# ONE route family over the 20 tables sql/44 creates, plus /ps2/status.
#
# WHY A FAMILY AND NOT 20 ROUTES
# The tables share one shape: city-scoped, one snapshot per computed_date,
# replaced wholesale by the daily loader. Twenty near-identical route blocks is
# twenty chances to forget the city filter or the latest-date subquery. The
# metric name is validated against this dict, so an unknown one 404s rather
# than reaching SQL.
#
# EVERY QUERY IS SCOPED TO THAT TABLE'S OWN LATEST computed_date.
# The loader commits all 20 in one transaction so they normally agree. Scoping
# each independently means a partially refreshed database degrades to "one
# panel is stale" instead of "one panel is empty".
#
# id IS NEVER SELECTED. It is BIGSERIAL and the daily DELETE+INSERT
# regenerates it, so it is not a stable reference for a link, a saved view or
# a ServiceNow payload. The business key is.
#
# "precision" IS QUOTED. It is a column name in two of these tables and a
# PostgreSQL keyword. This project already lost a load to an unquoted
# "window" after eight tables were staged.
#
# CAPS. API Gateway kills the integration at 30s. cofailure_clusters holds
# 83,184 rows and repair_effectiveness 38,395, so both default to a browse
# window and are never a denominator -- the rollup routes are.
#
# (table, select list, order by, default limit, max limit, date column or None)
_PS2V25 = {
    # -- label and governance (ps2_v25_*) --------------------------------
    "label-daily": ("ps2_v25_failure_label_daily",
        'label_date,device_category,eligible_device_days,positive_device_days,eligible_devices,'
        'positive_devices,future_hardware_oos_set_events,median_hours_to_next_oos,'
        'p90_hours_to_next_oos,negative_device_days,label_positive_rate',
        "label_date, device_category", 2000, 5000, "label_date"),
    "label-summary": ("ps2_v25_failure_label_summary",
        'device_category,eligible_device_days,positive_device_days,eligible_devices,positive_devices,'
        'future_hardware_oos_set_events,mean_hours_to_next_oos,median_hours_to_next_oos,'
        'p90_hours_to_next_oos,positive_days_with_commanded_oos,negative_device_days,'
        'label_positive_rate,positive_device_share,label_horizon_days,label_cutoff_date,label_definition',
        "device_category", 100, 100, None),
    "label-horizon": ("ps2_v25_failure_horizon_profile",
        'device_category,lead_day,positive_device_days_at_lead,eligible_device_days,'
        'positive_rate_at_lead,label_definition',
        "device_category, lead_day", 100, 100, None),
    "label-parity": ("ps2_v25_ps1_label_parity",
        'device_category,eligible_device_days,comparable_device_days,matching_device_days,'
        'mismatching_device_days,source_label_positive_rate,rebuilt_label_positive_rate,'
        'legacy_sla_positive_rate,parity_rate,parity_status,rebuilt_target',
        "device_category", 100, 100, None),
    "definition-alignment": ("ps2_v25_failure_definition_alignment",
        'device_category,silver_ps1_failure_device_days,governed_oos_episode_device_days,'
        'overlap_device_days,silver_only_device_days,governed_only_device_days,'
        'silver_to_governed_overlap_rate,governed_to_silver_overlap_rate,definition_jaccard',
        "device_category", 100, 100, None),
    "model-performance": ("ps2_v25_ps1_model_performance",
        'device_category,evaluated_device_days,eligible_device_days,prediction_coverage,'
        'true_positive,false_positive,true_negative,false_negative,actual_positive_rate,'
        'predicted_positive_rate,"precision",recall,specificity,f1_score,balanced_accuracy,'
        'brier_score,roc_auc,pr_auc,evaluation_status',
        "device_category", 100, 100, None),
    "category-profile": ("ps2_v25_category_profile",
        'device_category_raw,device_category,event_count,device_count,first_event_ts,'
        'last_event_ts,is_mapped,is_target_scope',
        "event_count DESC", 200, 500, None),
    "run-quality": ("ps2_v25_run_quality",
        'check_name,passed,observed_value,threshold,severity,metric_context,'
        'quality_status,run_mode,run_disposition',
        "severity, passed, check_name", 200, 500, None),

    # -- governed OOS, patterns, components (ps2_v2_*) -------------------
    "oos-trend": ("ps2_v2_daily_oos_trend",
        'event_date,device_category,hardware_oos_onsets,affected_devices,hardware_oos_minutes,'
        'validated_failure_onsets,chargeable_oos_onsets',
        "event_date, device_category", 2000, 5000, "event_date"),
    "exposure": ("ps2_v2_customer_exposure",
        'event_date,device_category,hardware_oos_onsets,hardware_oos_minutes,'
        'transactions_exposed,revenue_cents_exposed',
        "event_date, device_category", 2000, 5000, "event_date"),
    "governance": ("ps2_v2_oos_governance",
        'device_category,oos_evidence_class,failure_evidence_class,event_count,device_count,outage_minutes',
        "device_category, event_count DESC", 200, 500, None),
    "precursors": ("ps2_v2_precursor_patterns",
        'device_category,component_subsystem,next_subsystem,pattern_key,edge_support,'
        'pre_oos_edge_count,validated_failure_edge_count,median_edge_lag_seconds,p95_edge_lag_seconds,'
        'baseline_pre_oos_rate,pre_oos_rate,pre_oos_wilson_lower_95,pre_oos_lift_vs_category,'
        'validated_failure_rate,evidence_tier,priority_score',
        "priority_score DESC NULLS LAST", 500, 2000, None),
    "leadlag": ("ps2_v2_leadlag_timing",
        'device_category,component_subsystem,next_subsystem,pattern_key,edge_support,'
        'median_edge_lag_seconds,p95_edge_lag_seconds,pre_oos_rate,pre_oos_lift_vs_category,evidence_tier',
        "edge_support DESC", 500, 2000, None),
    "topology": ("ps2_v2_topology_nodes",
        'device_category,subsystem,outgoing_edge_volume,out_degree,outgoing_pre_oos_rate,'
        'incoming_edge_volume,in_degree,flow_centrality_score',
        "flow_centrality_score DESC NULLS LAST", 200, 500, None),
    "drift": ("ps2_v2_pattern_drift",
        'device_category,component_subsystem,next_subsystem,baseline_count,recent_count,'
        'baseline_pre_oos_count,recent_pre_oos_count,baseline_pre_oos_rate,recent_pre_oos_rate,'
        'rate_change,drift_flag',
        "rate_change DESC NULLS LAST", 500, 2000, None),
    "serials": ("ps2_v2_component_serial_patterns",
        'device_category,component_subsystem,component_serial_id,hardware_oos_episode_count,'
        'validated_failure_count,hardware_oos_minutes,observed_oos_days,last_oos_ts,'
        'serial_evidence_tier,component_priority_score,current_component_age_days,'
        'hardware_component_description,hardware_source,current_config_device_count,'
        'hardware_age_enrichment',
        "component_priority_score DESC NULLS LAST", 1000, 5000, None),
    "deterioration": ("ps2_v2_device_deterioration",
        'device_id,device_category,event_date,hardware_oos_onsets,hardware_oos_minutes,'
        'validated_failure_onsets,baseline_mean_28d,baseline_std_28d,oos_zscore_28d,alert_reason',
        "oos_zscore_28d DESC NULLS LAST", 1000, 5000, "event_date"),
    "clusters": ("ps2_v2_cofailure_clusters",
        'event_date,cluster_scope,cluster_id,device_category,facility_id,cofailing_devices,'
        'hardware_oos_onsets,observed_group_devices,cofailure_share,coordinated_station_flag,'
        'major_station_flag',
        "event_date DESC, cofailing_devices DESC", 1000, 5000, "event_date"),
    "repairs": ("ps2_v2_repair_effectiveness",
        'repair_id,maintenance_component_subsystem,ledger_type,maintenance_date,'
        'pre_30d_oos_onsets,post_30d_oos_onsets,post_vs_pre_change,interpretation_note',
        "maintenance_date DESC", 1000, 5000, "maintenance_date"),
    "cross-ps": ("ps2_v2_cross_ps_alignment",
        'source,truth_positive_count,signal_positive_count,matched_positive_count,'
        '"precision",recall,population_unit,alignment_status',
        "source", 100, 100, None),
}

# Which optional filters each table can honour. Asked of the SELECT list rather
# than hardcoded per table, so a column that is not there can never be filtered
# on -- that would be a 42703 at request time instead of an ignored parameter.
_PS2V25_FILTERS = (
    ("category", "device_category"),
    ("device",   "device_id"),
    ("serial",   "component_serial_id"),
    ("facility", "facility_id"),
    ("subsystem", "component_subsystem"),
)


def _ps2_v25_route(path, params, city):
    """Returns a response for /ps2/v25/* and /ps2/status, else None."""
    params = params or {}

    if path == "/ps2/status":
        # One row per table. A coherent load shows ONE distinct run_id across
        # all 20. More than one means a partial load -- the failure that
        # otherwise shows up as a tab where 18 panels are today and 2 are last
        # week. The view does the UNION so this route stays a one-liner.
        data = rows(
            "SELECT table_name, run_id, computed_date, notebook_version, as_of_ts, row_count "
            "FROM v_ps2_v25_status WHERE city_id=:c ORDER BY table_name", c=city)
        run_ids = sorted({r["run_id"] for r in data if r.get("run_id")})
        return ok({
            "city": city,
            "tables": len(data),
            "expected_tables": len(_PS2V25),
            "run_ids": run_ids,
            "coherent": len(run_ids) == 1 and len(data) == len(_PS2V25),
            "computed_date": (data[0]["computed_date"] if data else None),
            "as_of_ts": (data[0]["as_of_ts"] if data else None),
            "rows": data,
        })

    if not path.startswith("/ps2/v25/"):
        return None

    metric = path[len("/ps2/v25/"):].strip("/")
    if metric == "":
        return ok({"metrics": sorted(_PS2V25), "usage": "/ps2/v25/<metric>?city=CHI"})
    spec = _PS2V25.get(metric)
    if spec is None:
        return err(404, "unknown PS2 v2.5 metric %r. Known: %s" % (metric, ", ".join(sorted(_PS2V25))))

    table, cols, order, dflt, hard, datecol = spec
    where = ["city_id=:c",
             "computed_date=(SELECT MAX(computed_date) FROM %s WHERE city_id=:c)" % table]
    kw = {"c": city}

    have = cols.replace('"', '')
    for pname, col in _PS2V25_FILTERS:
        v = (params.get(pname) or "").strip()
        if v and col in have.split(","):
            where.append("%s=:%s" % (col, pname))
            kw[pname] = v.upper() if col == "device_category" else v

    if datecol:
        for pname, op in (("from", ">="), ("to", "<=")):
            v = (params.get(pname) or "").strip()
            if v:
                where.append("%s %s :%s" % (datecol, op, pname))
                kw[pname] = v

    limit = _clamp_int(params.get("limit"), dflt, 1, hard)
    offset = _clamp_int(params.get("offset"), 0, 0, 1000000)
    sql = ("SELECT %s FROM %s WHERE %s ORDER BY %s LIMIT %d OFFSET %d"
           % (cols, table, " AND ".join(where), order, limit, offset))
    return ok(rows(sql, **kw))
