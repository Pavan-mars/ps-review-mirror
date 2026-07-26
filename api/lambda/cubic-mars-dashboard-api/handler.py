"""
cubic-mars-dashboard-api  —  VPC Lambda for the CUBIC MARS Chicago dashboard.
Jobs: 1. action=migrate -> applies sql/01..08 to Aurora (missing files skipped)
      2. HTTP API -> read routes for PS1/PS2/PS3/PS5 (+device-level) + PS4 alerts
pg8000 pure-python driver (no native build).
Phase-1e adds 3 PS2 analytics routes: /ps2/paths, /ps2/ignition, /ps2/impact.
"""
import os, json, re, boto3, pg8000.native
from decimal import Decimal

def _json_default(o):
    if isinstance(o, Decimal): return float(o)
    return str(o)

_SECRETS = boto3.client("secretsmanager")
_conn = None

def _creds():
    s = json.loads(_SECRETS.get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"])
    return {
        "user": s.get("username") or s.get("user") or "postgres",
        "password": s.get("password"),
        "host": s.get("host") or os.environ["RDS_HOST"],
        "port": int(s.get("port") or os.environ.get("RDS_PORT", 5432)),
        "database": s.get("dbname") or s.get("database") or os.environ.get("DB_NAME", "postgres"),
    }

def conn():
    global _conn
    if _conn is None:
        c = _creds()
        _conn = pg8000.native.Connection(
            user=c["user"], password=c["password"], host=c["host"],
            port=c["port"], database=c["database"], ssl_context=True, timeout=15)
    return _conn

def split_sql(sql):
    stmts, buf, i, n = [], [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "-" and sql[i:i+2] == "--":
            j = sql.find("\n", i); j = n if j < 0 else j; buf.append(sql[i:j]); i = j; continue
        if ch == "/" and sql[i:i+2] == "/*":
            j = sql.find("*/", i); j = n if j < 0 else j+2; buf.append(sql[i:j]); i = j; continue
        if ch == "'":
            j = i+1
            while j < n:
                if sql[j] == "'" and sql[j:j+2] == "''": j += 2; continue
                if sql[j] == "'": break
                j += 1
            buf.append(sql[i:j+1]); i = j+1; continue
        if ch == "$":
            m = re.match(r"\$[A-Za-z0-9_]*\$", sql[i:])
            if m:
                tag = m.group(0); k = sql.find(tag, i+len(tag))
                k = n if k < 0 else k+len(tag); buf.append(sql[i:k]); i = k; continue
        if ch == ";":
            s = "".join(buf).strip()
            if s: stmts.append(s)
            buf = []; i += 1; continue
        buf.append(ch); i += 1
    tail = "".join(buf).strip()
    if tail: stmts.append(tail)
    return stmts

_OK = ("already exists", "is not available", "does not exist, skipping")
def migrate(_evt):
    here = os.path.dirname(__file__); results = {}
    c = conn()
    for fn in ("sql/01_schema_core.sql", "sql/02_phase1_ps2_ps5_backfill.sql",
               "sql/03_phase1b_ps3_severity.sql", "sql/04_phase1c_ps1_failure.sql",
               "sql/05_phase1d_ps2_device.sql", "sql/06_phase1d_ps3_device.sql",
               "sql/07_phase1e_ps2_new.sql", "sql/08_ps2_run_backfill.sql",
               "sql/09_phase1f_ps2_rich.sql", "sql/10_ps2_run_backfill_2.sql",
               "sql/11_phase1g_ps1_serving.sql", "sql/12_ps1_serving_backfill.sql",
               "sql/13_phase2_device360.sql", "sql/14_phase3_dim_station.sql",
               "sql/15_phase2a_ps3_two_head.sql",
               "sql/16_phase2b_ps1_batch_lineage.sql"):
        path = os.path.join(here, fn)
        if not os.path.exists(path):
            results[fn] = {"skipped": "file not present"}; continue
        applied = tolerated = failed = 0; errs = []
        for st in split_sql(open(path).read()):
            try:
                c.run(st); applied += 1
            except Exception as e:
                msg = str(e).lower()
                if any(t in msg for t in _OK): tolerated += 1
                else:
                    failed += 1
                    if len(errs) < 8: errs.append(str(e)[:180])
        results[fn] = {"applied": applied, "tolerated": tolerated, "failed": failed, "errors": errs}
    try:
        results["dim_station_seed.csv"] = _seed_dim_station_csv(c)
    except Exception as e:
        results["dim_station_seed.csv"] = {"error": str(e)[:180]}
    return {"statusCode": 200, "body": json.dumps({"migrate": results}, default=str)}

def rows(sql, **kw):
    c = conn(); res = c.run(sql, **kw); cols = [d["name"] for d in c.columns]
    return [dict(zip(cols, r)) for r in res]

def ok(payload): return {"statusCode": 200, "headers": {"content-type": "application/json",
    "access-control-allow-origin": "*"}, "body": json.dumps(payload, default=_json_default)}
def err(code, m): return {"statusCode": code, "headers": {"access-control-allow-origin": "*"},
    "body": json.dumps({"error": m})}

CITY = "CHI"
def q(city):
    return city if city in ("CHI", "BOS", "LAX", "TOC") else "CHI"


# ============ Device-360 : cross-PS aggregation for one device (grounded, honest) ============
_PS5_TYPE = {"TVM": "tvms", "GATE": "gates", "VALIDATOR": "validators"}

def _seed_dim_station_csv(c):
    """Full-coverage loader: if sql/dim_station_seed.csv is present (Databricks export of
    silver.dim_facility / dim_device DISTINCT facility_id,facility_name[,operator]), batch-upsert it
    into dim_station. Flexible header mapping. Batched (200/stmt) for speed. Returns a status dict."""
    import csv as _csv
    p = os.path.join(os.path.dirname(__file__), "sql", "dim_station_seed.csv")
    if not os.path.exists(p):
        return {"skipped": "no sql/dim_station_seed.csv (dim_station keeps its seeded rows)"}
    def pick(row, *names):
        for k in row:
            if k and k.strip().lower() in names:
                return row[k]
        return None
    data = []
    with open(p, newline="", encoding="utf-8-sig") as f:
        for row in _csv.DictReader(f):
            fid = pick(row, "facility_id", "facid", "facility")
            name = pick(row, "station_name", "facility_name", "facility_short_name")
            if not fid or not str(fid).strip() or not name or not str(name).strip():
                continue
            op = pick(row, "operator", "operator_name")
            op = op.strip() if op else None
            if op and op.lower() in ("null", "none", ""):
                op = None
            data.append((str(fid).strip()[:20], str(name).strip()[:120], (op[:80] if op else None)))
    # dedupe by facility_id (export may key one facility with several operators) so no intra-batch conflict
    _seen = {}
    for fid, name, op in data:
        if fid not in _seen:
            _seen[fid] = (fid, name, op)
    data = list(_seen.values())
    n = 0
    B = 200
    for i in range(0, len(data), B):
        chunk = data[i:i+B]; vals = []; params = {}
        for j, (fid, name, op) in enumerate(chunk):
            vals.append(f"('CHI',:f{j},:n{j},:o{j},'databricks')")
            params[f"f{j}"] = fid; params[f"n{j}"] = name; params[f"o{j}"] = op
        sql = ("INSERT INTO dim_station(city_id,facility_id,station_name,operator,source) VALUES "
               + ",".join(vals)
               + " ON CONFLICT (city_id,facility_id) DO UPDATE SET station_name=EXCLUDED.station_name, operator=EXCLUDED.operator, source='databricks', loaded_at=NOW()")
        try:
            c.run(sql, **params); n += len(chunk)
        except Exception:
            pass
    return {"loaded": n}

def _device_360(city, dev):
    out = {"device_id": dev, "city": "Chicago"}
    cat = None; pid = None; prob = None; thr = None
    # ---- PS1 : device-level prediction + SHAP drivers ----
    p = rows("SELECT prediction_id,device_category,facility_id,failure_probability,predicted_label,decision_threshold,prediction_date FROM ps1_failure_predictions WHERE city_id=:c AND device_id=:d AND computed_date=(SELECT MAX(computed_date) FROM ps1_failure_predictions WHERE city_id=:c) ORDER BY failure_probability DESC LIMIT 1", c=city, d=dev)
    if p:
        r = p[0]; cat = r["device_category"]; pid = r["prediction_id"]
        prob = float(r["failure_probability"] or 0); thr = float(r["decision_threshold"] or 0)
        band = "Critical" if prob >= max(0.5, thr*2) else "High" if prob >= thr else "Medium" if prob >= thr*0.5 else "Low"
        drivers = rows("SELECT feature_name,shap_value,feature_value FROM ps1_explainability WHERE city_id=:c AND prediction_id=:p AND computed_date=(SELECT MAX(computed_date) FROM ps1_explainability WHERE city_id=:c) ORDER BY ABS(shap_value) DESC LIMIT 8", c=city, p=pid)
        if not drivers and cat:
            drivers = [{"feature_name": x["feature_name"], "shap_value": x["avg_shap"], "feature_value": None}
                       for x in rows("SELECT feature_name,avg_shap FROM ps1_feature_importance WHERE city_id=:c AND device_category=:d AND computed_date=(SELECT MAX(computed_date) FROM ps1_feature_importance WHERE city_id=:c) ORDER BY feat_rank LIMIT 8", c=city, d=cat)]
        out["ps1"] = {"level": "device", "found": True, "device_category": cat, "facility_id": r["facility_id"],
                      "failure_probability": prob, "predicted_label": r["predicted_label"], "decision_threshold": thr,
                      "risk_band": band, "prediction_date": str(r["prediction_date"]), "drivers": drivers,
                      "note": "PS1 is REVIEW-ONLY: model has not passed its quality gate (both QGs failed)."}
    else:
        out["ps1"] = {"level": "device", "found": False, "note": "No PS1 prediction for this device in the latest run."}
    # ---- PS2 : device-level catalog + cascade rank + recent event-code chains ----
    cat2 = rows("SELECT device_name,serial,category,control_group,facility,operator,cascade_days,avg_chain_len,max_chain_len,dom_subsystem,dom_error_code,worst_cascade_path,worst_window FROM ps2_device_catalog WHERE city_id=:c AND device_id=:d AND computed_date=(SELECT MAX(computed_date) FROM ps2_device_catalog WHERE city_id=:c) LIMIT 1", c=city, d=dev)
    topd = rows("SELECT cascade_days,dev_rank,w0_5,w5_15,w15_30,w30_60,w60plus FROM ps2_top_devices WHERE city_id=:c AND device_id=:d AND computed_date=(SELECT MAX(computed_date) FROM ps2_top_devices WHERE city_id=:c) LIMIT 1", c=city, d=dev)
    chains = rows("SELECT transit_day,event_code_chain,subsystem_chain,chain_length,chain_span_min,first_subsystem,last_subsystem FROM ps2_device_cascades WHERE city_id=:c AND device_id=:d AND computed_date=(SELECT MAX(computed_date) FROM ps2_device_cascades WHERE city_id=:c) ORDER BY transit_day DESC LIMIT 5", c=city, d=dev)
    ps2 = {"level": "device", "in_catalog": bool(cat2), "in_top_devices": bool(topd)}
    if cat2: ps2.update(cat2[0])
    if topd:
        ps2["cascade_rank"] = topd[0]["dev_rank"]
        ps2["windows"] = {k: topd[0][k] for k in ("w0_5", "w5_15", "w15_30", "w30_60", "w60plus")}
    ps2["recent_cascades"] = chains
    if cat is None and cat2: cat = cat2[0]["category"]
    out["ps2"] = ps2
    # ---- PS3 : category-level SEVERITY model (not per-device root cause) ----
    if cat:
        sev = rows("SELECT device,f1_macro,accuracy,n_incidents FROM ps3_device_metrics WHERE city_id=:c AND UPPER(device)=UPPER(:d) AND split='test' LIMIT 1", c=city, d=cat)
        out["ps3"] = {"level": "category", "category": cat, "metrics": (sev[0] if sev else None),
                      "note": "PS3 = failure SEVERITY (category-level), not per-device root cause. True 9-class root cause blocked on SVN_STAGE."}
    else:
        out["ps3"] = {"level": "category", "note": "device category unknown"}
    # ---- PS4 : device-level anomaly alerts ----
    al = rows("SELECT detected_at,triggering_signal,anomaly_score,severity,status,description FROM ps4_anomaly_alerts WHERE city_id=:c AND device_id=:d ORDER BY detected_at DESC LIMIT 5", c=city, d=dev)
    out["ps4"] = {"level": "device", "alert_count": len(al), "alerts": al,
                  "note": "PS4 anomaly ensemble (unsupervised); thresholds under recalibration."}
    # ---- PS5 : category-level reliability / RUL ----
    if cat:
        p5 = _PS5_TYPE.get(str(cat).upper(), str(cat).lower())
        _allrel = rows("SELECT device_type,concordance_index,registry_status,dashboard_ready,blockers FROM ps5_reliability_status WHERE city_id=:c", c=city)
        rel = [r for r in _allrel if str(r.get("device_type")).lower() == p5]  # filter in python: no enum::text cast (pg8000.native-safe)
        out["ps5"] = {"level": "category", "category": cat, "reliability": (rel[0] if rel else None),
                      "note": "PS5 = reliability/RUL (category-level); gate closed pending fixes."}
    else:
        out["ps5"] = {"level": "category", "note": "device category unknown"}
    out["recommendation"] = _reco(out, prob, thr)
    out["servicenow_payload"] = _sn_payload(dev, cat, out)
    return out

def _reco(o, prob, thr):
    ps1 = o.get("ps1", {}); ps2 = o.get("ps2", {}); ps4 = o.get("ps4", {})
    if not ps1.get("found"):
        base = "No PS1 prediction in the latest run for this device."
    elif prob is not None and thr is not None and prob >= thr:
        base = "PS1 flags elevated 3-day failure risk (%.1f%% vs threshold %.1f%%). REVIEW-ONLY: PS1 has not passed its quality gate - treat as a watch signal, not an auto-dispatch." % (prob*100, thr*100)
    else:
        base = "PS1 below threshold (%.1f%% vs %.1f%%). No PS1 action." % ((prob or 0)*100, (thr or 0)*100)
    corr = []
    if ps2.get("in_top_devices"): corr.append("PS2 cascade-active (rank %s, %s days)" % (ps2.get("cascade_rank"), ps2.get("cascade_days")))
    elif ps2.get("in_catalog") and ps2.get("cascade_days"): corr.append("PS2 cascade history (%s days)" % ps2.get("cascade_days"))
    if ps4.get("alert_count"): corr.append("PS4 anomaly alerts x%s" % ps4.get("alert_count"))
    dom = ps2.get("dom_error_code")
    if ps1.get("found") and prob is not None and thr is not None and prob >= thr and (ps2.get("in_top_devices") or ps4.get("alert_count")):
        return base + " Corroborated by %s. Suggest inspection of %s%s and scheduling preventive maintenance." % (
            ", ".join(corr), ps2.get("dom_subsystem") or "dominant subsystem", (" (error code %s)" % dom) if dom else "")
    if corr:
        return base + " Cross-PS context: " + ", ".join(corr) + "."
    return base

def _sn_payload(dev, cat, o):
    ps1 = o.get("ps1", {}); ps2 = o.get("ps2", {})
    prob = ps1.get("failure_probability"); band = ps1.get("risk_band", "")
    urg = {"Critical": "1", "High": "2", "Medium": "3", "Low": "3"}.get(band, "3")
    return {
        "short_description": "Scheduled maintenance - %s (%s) PS1 3-day failure risk %s%%" % (dev, cat or "device", round((prob or 0)*100, 1)),
        "cmdb_ci": dev, "u_device_category": cat, "u_facility": ps2.get("facility"), "u_serial": ps2.get("serial"),
        "urgency": urg, "impact": urg, "category": "Hardware", "subcategory": "Predictive Maintenance",
        "u_predicted_probability": prob, "u_decision_threshold": ps1.get("decision_threshold"),
        "u_dominant_error_code": ps2.get("dom_error_code"), "u_dominant_subsystem": ps2.get("dom_subsystem"),
        "work_notes": "MARS PS1 predictive signal (REVIEW-ONLY; model not promoted, both quality gates failed). Risk band %s. Cross-PS: PS2 cascade_days=%s in_top=%s, PS4 alerts=%s." % (
            band, ps2.get("cascade_days"), ps2.get("in_top_devices"), o.get("ps4", {}).get("alert_count")),
        "u_source": "MARS-predictive", "u_state": "staged", "caller_id": "mars.integration"}


def route(method, path, params, body):
    city = q((params or {}).get("city", CITY))
    if path == "/ps2/windows":
        return ok(rows("SELECT window_bucket,cascade_days,pct,total_cascade_days,slow_fast_fault_mult,slow_fast_duration_mult FROM ps2_cascade_window_summary WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_cascade_window_summary WHERE city_id=:c) ORDER BY cascade_days DESC", c=city))
    if path == "/ps2/windowdetail":
        return ok(rows("SELECT window_bucket,cascade_days,chain_len_mean,chain_len_median,chain_len_max,span_min_mean,span_min_median,velocity_min_per_fault FROM ps2_window_detail WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_window_detail WHERE city_id=:c) ORDER BY span_min_mean", c=city))
    if path == "/ps2/topdevices":
        return ok(rows("SELECT device_id,category,cascade_days,w0_5,w5_15,w15_30,w30_60,w60plus,dev_rank FROM ps2_top_devices WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_top_devices WHERE city_id=:c) ORDER BY dev_rank", c=city))
    if path == "/ps2/hub":
        return ok({"nodes": rows("SELECT node_id,freq,is_hub FROM ps2_subsystem_hub_summary WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_subsystem_hub_summary WHERE city_id=:c) ORDER BY freq DESC", c=city),
                   "edges": rows("SELECT source_sub,target_sub,phi FROM ps2_subsystem_hub_edges WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_subsystem_hub_edges WHERE city_id=:c)", c=city)})
    if path == "/ps2/facility":
        r = rows("SELECT * FROM ps2_facility_contagion_summary WHERE city_id=:c ORDER BY computed_date DESC LIMIT 1", c=city)
        return ok(r[0] if r else {})
    if path == "/ps2/associations":
        return ok(rows("SELECT antecedent_subsystem,consequent_subsystem,support,confidence,lift,conviction FROM ps2_subsystem_associations WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_subsystem_associations WHERE city_id=:c) ORDER BY lift DESC", c=city))
    if path == "/ps2/hmm":
        return ok(rows("SELECT regime,pct,dwell_days_min,dwell_days_max FROM ps2_hmm_regimes WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_hmm_regimes WHERE city_id=:c)", c=city))
    # ---- Phase-1e NEW PS2 analytics (return newest computed_date only) ----
    if path == "/ps2/paths":
        return ok(rows("SELECT path_rank,cascade_path,path_len,first_subsystem,last_subsystem,occurrences,pct_of_chains FROM ps2_cascade_paths WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_cascade_paths WHERE city_id=:c) ORDER BY path_rank", c=city))
    if path == "/ps2/ignition":
        return ok(rows("SELECT subsystem,rank,ignition_days,termination_days,ignition_pct,termination_pct,net_role FROM ps2_ignition_termination WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_ignition_termination WHERE city_id=:c) ORDER BY rank", c=city))
    if path == "/ps2/impact":
        return ok(rows("SELECT device_id,category,total_impact,cascade_days,avg_impact,max_impact,impact_rank FROM ps2_business_impact WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_business_impact WHERE city_id=:c) ORDER BY impact_rank", c=city))
    # ---- Phase-1f RICH PS2 (correlation/markov/network/error-codes/device drill-down) ----
    if path == "/ps2/devices":
        return ok(rows("SELECT device_id,device_name,serial,category,control_group,facility,operator,cascade_days,avg_chain_len,max_chain_len,dom_subsystem,dom_error_code,worst_cascade_path,worst_window FROM ps2_device_catalog WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_device_catalog WHERE city_id=:c) ORDER BY cascade_days DESC LIMIT 200", c=city))
    if path == "/ps2/devicecascades":
        dev = (params or {}).get("device", "")
        return ok(rows("SELECT device_id,transit_day,subsystem_chain,event_code_chain,severity_chain,chain_length,chain_span_min,first_subsystem,last_subsystem FROM ps2_device_cascades WHERE city_id=:c AND device_id=:d AND computed_date=(SELECT MAX(computed_date) FROM ps2_device_cascades WHERE city_id=:c) ORDER BY transit_day DESC LIMIT 100", c=city, d=dev))
    if path == "/ps2/errorcodes":
        return ok({"codes": rows("SELECT error_code,occurrences,top_subsystem,pct FROM ps2_error_codes WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_error_codes WHERE city_id=:c) ORDER BY occurrences DESC", c=city),
                   "transitions": rows("SELECT from_code,to_code,occurrences FROM ps2_error_code_transitions WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_error_code_transitions WHERE city_id=:c) ORDER BY occurrences DESC", c=city)})
    if path == "/ps2/phi":
        return ok(rows("SELECT sub_a,sub_b,phi FROM ps2_phi_matrix WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_phi_matrix WHERE city_id=:c)", c=city))
    if path == "/ps2/markov":
        return ok(rows("SELECT from_sub,to_sub,prob FROM ps2_markov_transitions WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_markov_transitions WHERE city_id=:c) ORDER BY prob DESC", c=city))
    if path == "/ps2/network":
        return ok(rows("SELECT node_id,betweenness,pagerank,in_degree,out_degree,role FROM ps2_network_centrality WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_network_centrality WHERE city_id=:c) ORDER BY betweenness DESC", c=city))
    if path == "/ps2/conditional":
        return ok(rows("SELECT sub_a,sub_b,window_bucket,p_b_given_a FROM ps2_conditional_prob WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps2_conditional_prob WHERE city_id=:c) ORDER BY p_b_given_a DESC", c=city))
    # ---- PS1 failure prediction (run 20260713_0905; NOT promoted) ----
    if path == "/ps1/summary":
        return ok(rows("SELECT device,champion_model,test_auc,test_ap,test_accuracy,test_f1,test_precision,test_recall,op_threshold,op_fleet_pct,op_precision,op_recall,op_f2,recall_floor,quality_gate,promoted,brier_raw,brier_cal,auc_cal,prec_at_k,rec_at_k,lift_at_k,map_score,mlflow_version,sm_registered,endpoint_name,overfit_flag,n_train,n_test,n_test_pos,base_rate_pct,target,run_id FROM ps1_failure_summary WHERE city_id=:c ORDER BY device", c=city))
    if path == "/ps1/leaderboard":
        return ok(rows("SELECT device,model,auc,ap,f1,prec,rec,lb_rank,is_champion,note FROM ps1_leaderboard WHERE city_id=:c ORDER BY device,lb_rank", c=city))
    if path == "/ps1/features":
        return ok(rows("SELECT device,feature,mean_abs_shap,pct_total,feat_rank FROM ps1_features WHERE city_id=:c ORDER BY device,feat_rank", c=city))
    # ---- Phase-1g PS1 SERVING (backs the rich teammates' PS1 tab) ----
    if path == "/ps1/predictions":
        # Phase-3: enrich with real station name + operator + dominant error code from the PS2 device catalog (join on device_id; NULL where not catalogued)
        return ok(rows("""SELECT p.prediction_id, p.device_category, p.device_id, p.facility_id,
                   p.failure_probability, p.predicted_label, p.decision_threshold, p.prediction_date, p.inference_ts,
                   COALESCE(ds.station_name, c.facility) AS station_name,
                   COALESCE(ds.operator, c.operator) AS operator,
                   c.dom_error_code, c.dom_subsystem
            FROM ps1_failure_predictions p
            LEFT JOIN dim_station ds ON ds.city_id = p.city_id AND ds.facility_id = p.facility_id
            LEFT JOIN ps2_device_catalog c
              ON c.city_id = p.city_id AND c.device_id = p.device_id
             AND c.computed_date = (SELECT MAX(computed_date) FROM ps2_device_catalog WHERE city_id = p.city_id)
            WHERE p.city_id = :c
              AND p.computed_date = (SELECT MAX(computed_date) FROM ps1_failure_predictions WHERE city_id = :c)
            ORDER BY p.failure_probability DESC LIMIT 500""", c=city))
    if path == "/ps1/model-performance":
        mp = rows("SELECT device_category,model_name,algorithm,decision_threshold,mlflow_version,endpoint_name,n_features,quality_gate,promoted,computed_date,train_auc,train_ap,train_f1,val_auc,val_ap,val_f1,test_auc,test_ap,test_f1,test_prec,test_rec FROM ps1_model_performance WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps1_model_performance WHERE city_id=:c)", c=city)
        out = []
        for r in mp:
            _acc = {"TVM": 0.7033, "GATE": 0.9950}.get(str(r["device_category"]).upper())
            out.append({"device_category": r["device_category"], "category": r["device_category"], "city": "Chicago",
                        "model_name": r["model_name"], "algorithm": r["algorithm"], "prediction_head": r["device_category"],
                        "test_auc": r["test_auc"], "test_pr_auc": r["test_ap"], "accuracy": _acc,
                        "decision_threshold": r["decision_threshold"], "mlflow_version": r["mlflow_version"],
                        "model_version": r["mlflow_version"], "model_registry_id": r["endpoint_name"], "registry_alias": "champion",
                        "endpoint_name": r["endpoint_name"], "n_features": r["n_features"],
                        "status": ("promoted" if r["promoted"] else "not promoted"), "deployed_at": str(r["computed_date"]),
                        "quality_gate": r["quality_gate"], "promoted": r["promoted"],
                        "s3_metrics": {"train_auc": r["train_auc"], "train_ap": r["train_ap"], "train_f1": r["train_f1"],
                                       "val_auc": r["val_auc"], "val_ap": r["val_ap"], "val_f1": r["val_f1"],
                                       "test_auc": r["test_auc"], "test_ap": r["test_ap"], "test_f1": r["test_f1"],
                                       "test_prec": r["test_prec"], "test_rec": r["test_rec"], "test_acc": _acc}})
        return ok(out)
    if path == "/ps1/risk-trend":
        return ok(rows("SELECT trend_date AS date,device_category,avg_prob_pct,failures,total FROM ps1_risk_trend WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps1_risk_trend WHERE city_id=:c) ORDER BY trend_date", c=city))
    if path == "/ps1/feature-importance":
        dc = (params or {}).get("device_category", "TVM")
        return ok(rows("SELECT feature_name,avg_importance,avg_shap FROM ps1_feature_importance WHERE city_id=:c AND device_category=:d AND computed_date=(SELECT MAX(computed_date) FROM ps1_feature_importance WHERE city_id=:c) ORDER BY feat_rank", c=city, d=dc))
    if path == "/ps1/station-summary":
        return ok(rows("SELECT facility_id,total_devices,predicted_failures,avg_risk_pct,critical_count,high_count,medium_count,last_inference_date FROM ps1_station_summary WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps1_station_summary WHERE city_id=:c) ORDER BY avg_risk_pct DESC", c=city))
    if path == "/ps1/explainability":
        pid = (params or {}).get("prediction_id", "")
        return ok(rows("SELECT feature_name,shap_value,feature_value FROM ps1_explainability WHERE city_id=:c AND prediction_id=:p AND computed_date=(SELECT MAX(computed_date) FROM ps1_explainability WHERE city_id=:c) ORDER BY ABS(shap_value) DESC", c=city, p=pid))
    if path == "/ps1/risk-bands":
        return ok(rows("SELECT device_category,band,device_count,pct FROM ps1_risk_bands WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps1_risk_bands WHERE city_id=:c)", c=city))
    if path == "/ps1/threshold-sweep":
        return ok(rows("SELECT device_category,threshold,precision,recall,alert_rate,f1 FROM ps1_threshold_sweep WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps1_threshold_sweep WHERE city_id=:c) ORDER BY device_category,threshold", c=city))
    if path == "/ps1/calibration":
        return ok(rows("SELECT device_category,bin_lo,bin_hi,pred_mean,actual_rate,n FROM ps1_calibration WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps1_calibration WHERE city_id=:c) ORDER BY device_category,bin_lo", c=city))
    if path == "/ps1/confusion":
        return ok(rows("SELECT device_category,tp,fp,tn,fn FROM ps1_confusion WHERE city_id=:c AND computed_date=(SELECT MAX(computed_date) FROM ps1_confusion WHERE city_id=:c)", c=city))
    # ---- PS3 failure-SEVERITY ----
    if path == "/ps3/summary":
        r = rows("SELECT * FROM ps3_severity_summary WHERE city_id=:c ORDER BY as_of_date DESC LIMIT 1", c=city)
        return ok(r[0] if r else {})
    if path == "/ps3/drivers":
        return ok(rows("SELECT feature,shap_importance,solo_auc,driver_rank FROM ps3_severity_drivers WHERE city_id=:c ORDER BY driver_rank ASC", c=city))
    if path == "/ps3/devices":
        return ok(rows("SELECT device,split,n_incidents,f1_macro,accuracy FROM ps3_device_metrics WHERE city_id=:c ORDER BY device,CASE split WHEN 'train' THEN 1 WHEN 'val' THEN 2 ELSE 3 END", c=city))
    if path == "/ps3/predictions":
        return ok(rows("SELECT device_id,mars_device_category,incident_id,incident_dtm,predicted_label,proba_critical,actual_label FROM ps3_severity_predictions WHERE city_id=:c ORDER BY incident_dtm DESC LIMIT 200", c=city))
    if path == "/ps5/status":
        return ok(rows("SELECT device_type,concordance_index,registry_status,dashboard_ready,blockers FROM ps5_reliability_status WHERE city_id=:c", c=city))
    if path == "/ps4/alerts":
        st = (params or {}).get("status")
        if st: return ok(rows("SELECT * FROM ps4_anomaly_alerts WHERE city_id=:c AND status=:s ORDER BY detected_at DESC LIMIT 200", c=city, s=st))
        return ok(rows("SELECT * FROM ps4_anomaly_alerts WHERE city_id=:c ORDER BY detected_at DESC LIMIT 200", c=city))
    if path.startswith("/ps4/alerts/") and method == "PATCH":
        aid = path.rsplit("/", 1)[-1]; new = (body or {}).get("status")
        if new not in ("active", "investigating", "acknowledged", "resolved"): return err(400, "bad status")
        conn().run("UPDATE ps4_anomaly_alerts SET status=:s, acknowledged_at=CASE WHEN :s='acknowledged' THEN NOW() ELSE acknowledged_at END, resolved_at=CASE WHEN :s='resolved' THEN NOW() ELSE resolved_at END WHERE id=CAST(:i AS uuid)", s=new, i=aid)
        return ok({"id": aid, "status": new})
    if path == "/overview/summary":
        return err(501, "v_executive_summary deferred to Phase 2 (needs PS1/PS3/PS4 tables)")
    if path == "/ps1/device-360":
        dev = (params or {}).get("device_id", "")
        if not dev: return err(400, "device_id required")
        return ok(_device_360(city, dev))
    if path == "/ps1/servicenow-stage" and method == "POST":
        d = body or {}; dev = d.get("device_id", "")
        if not dev: return err(400, "device_id required")
        import uuid as _uuid
        sid = str(_uuid.uuid4()); pl = d.get("payload", {})
        conn().run("INSERT INTO servicenow_staging(id,city_id,device_id,device_category,short_description,payload_json,status,created_at) VALUES(CAST(:i AS uuid),:c,:d,:cat,:sd,:p,'staged',NOW())",
                   i=sid, c=city, d=dev, cat=d.get("device_category"), sd=(d.get("short_description") or "")[:238], p=json.dumps(pl, default=str))
        return ok({"staged_id": sid, "status": "staged", "note": "Incident STAGED only (no live post). Wire Robin's ServiceNow endpoint to submit."})
    if path == "/ps1/servicenow-staged":
        return ok(rows("SELECT id,device_id,device_category,short_description,status,created_at FROM servicenow_staging WHERE city_id=:c ORDER BY created_at DESC LIMIT 100", c=city))
    return err(404, f"no route {method} {path}")

def lambda_handler(event, context):
    if isinstance(event, dict) and event.get("action") == "migrate":
        return migrate(event)
    rc = (event or {}).get("requestContext", {}).get("http", {})
    method = rc.get("method", "GET"); path = event.get("rawPath", "/")
    params = event.get("queryStringParameters") or {}
    body = {}
    if event.get("body"):
        try: body = json.loads(event["body"])
        except Exception: body = {}
    try:
        return route(method, path, params, body)
    except Exception as e:
        return err(500, str(e)[:300])
