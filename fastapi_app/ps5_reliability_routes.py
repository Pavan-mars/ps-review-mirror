# =============================================================================
# ps5_reliability_routes.py -- /ps5/devices and /ps5/serials for the dashboard-api
# Lambda. Reads the migration-06 latest-per-row views and returns the exact JSON
# shapes apiPS5DeviceRUL() / apiPS5SerialHealth() consume, so the dashboard flips
# from SAMPLE to live with no front-end change.
#
# Wire into your existing router: add
#     GET /ps5/devices  -> ps5_devices(city)
#     GET /ps5/serials  -> ps5_serials(city)
# reusing however the api already gets its psycopg2 connection. A self-contained
# Secrets-Manager connection + a generic lambda_handler are included as a fallback.
# =============================================================================
import json, math, os


# ---- helpers ----------------------------------------------------------------
def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except Exception:
        return None


def _weibull_q(median, shape, p):
    """Weibull quantile from the fitted median + shape (median = scale*(ln2)^(1/k))."""
    median = _num(median); shape = _num(shape)
    if not median or not shape or shape <= 0:
        return None
    scale = median / (math.log(2.0) ** (1.0 / shape))
    return round(scale * ((-math.log(1.0 - p)) ** (1.0 / shape)), 1)


# ---- /ps5/devices -----------------------------------------------------------
DEV_QUERY = """
SELECT device_id, mars_device_category, facility_id, current_healthy_age_days, rul_standard_days,
       predicted_median_survival_days, hazard_score, risk_band, is_overdue, days_since_hw_oos, roll_fail_30d,
       concordance_index, weibull_shape, data_quality_gate_passed,
       event_definition, event_def_version, event_label, window_mode, telemetry_start, cindex_floor, as_of_date
FROM v_ps5_reliability_oos_latest
WHERE city_id = %s
ORDER BY rul_standard_days ASC
"""


def ps5_devices(conn, city):
    with conn.cursor() as cur:
        cur.execute(DEV_QUERY, (city,))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    if not rows:
        return None
    r0 = rows[0]
    devices = [{
        "device_id": r["device_id"], "mars_device_category": r["mars_device_category"],
        "facility_id": r["facility_id"],
        "current_healthy_age_days": _num(r["current_healthy_age_days"]),
        "rul_standard_days": _num(r["rul_standard_days"]),
        "rul_p10_days": _weibull_q(r["predicted_median_survival_days"], r["weibull_shape"], 0.10),
        "rul_p90_days": _weibull_q(r["predicted_median_survival_days"], r["weibull_shape"], 0.90),
        "hazard_score": _num(r["hazard_score"]), "risk_band": r["risk_band"],
        "is_overdue": bool(r["is_overdue"]),
        "days_since_hw_oos": _num(r["days_since_hw_oos"]),
        "roll_fail_30d": int(r["roll_fail_30d"]) if r["roll_fail_30d"] is not None else None,
        "concordance_index": _num(r["concordance_index"]),
        "data_quality_gate_passed": bool(r["data_quality_gate_passed"]),
    } for r in rows]
    return {
        "event_definition": r0.get("event_label") or "hardware OOS (Set)",
        "event_def_version": r0.get("event_def_version"),
        "window": (f"telemetry-era ({r0['telemetry_start']}+)" if r0.get("telemetry_start") else r0.get("window_mode")),
        "floor": _num(r0.get("cindex_floor")) or 0.65,
        "as_of_date": str(r0.get("as_of_date")),
        "devices": devices,
        "note": "Per-device RUL on the hardware-OOS-Set event (live from RDS). P10/P90 are Weibull quantiles from the fitted median + shape.",
    }


# ---- /ps5/serials -----------------------------------------------------------
SER_QUERY = """
SELECT device_id, component_serial_nbr, component_type, mars_device_category, component_age_days,
       device_oos_failures_total, risk_score, risk_tier, expected_component_rul_days,
       predicted_median_survival_days, is_overdue, event_def_version, as_of_date
FROM v_ps5_serial_oos_latest
WHERE city_id = %s
ORDER BY risk_score DESC
"""


def ps5_serials(conn, city):
    with conn.cursor() as cur:
        cur.execute(SER_QUERY, (city,))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    if not rows:
        return None
    serials = [{
        "device_id": r["device_id"], "component_serial_nbr": r["component_serial_nbr"],
        "component_type": r["component_type"], "mars_device_category": r["mars_device_category"],
        "component_age_days": _num(r["component_age_days"]),
        "device_oos_failures_total": int(r["device_oos_failures_total"]) if r["device_oos_failures_total"] is not None else None,
        "risk_score": _num(r["risk_score"]), "risk_tier": r["risk_tier"],
        "expected_component_rul_days": _num(r["expected_component_rul_days"]),
        "is_overdue": bool(r["is_overdue"]),
    } for r in rows]
    return {
        "event_def_version": rows[0].get("event_def_version"),
        "serials": serials,
        "note": "Per-serial component reliability on the hardware-OOS-Set event (live from RDS).",
    }


# ---- self-contained connection + generic Lambda handler (fallback) ----------
def _connect():
    import boto3, psycopg2
    sec = json.loads(boto3.client("secretsmanager").get_secret_value(
        SecretId=os.environ["PS5_RDS_SECRET"])["SecretString"])
    return psycopg2.connect(host=sec["host"], port=int(sec.get("port", 5432)), dbname=sec["dbname"],
                            user=sec["username"], password=sec["password"], connect_timeout=8)


def _resp(body):
    return {"statusCode": 200 if body is not None else 404,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps(body if body is not None else {"error": "no rows"})}


def lambda_handler(event, context=None):
    path = (event.get("rawPath") or event.get("path") or "").rstrip("/")
    qs = event.get("queryStringParameters") or {}
    city = qs.get("city", "CHI")
    conn = _connect()
    try:
        if path.endswith("/ps5/devices"):
            return _resp(ps5_devices(conn, city))
        if path.endswith("/ps5/serials"):
            return _resp(ps5_serials(conn, city))
        return {"statusCode": 404, "body": json.dumps({"error": f"unknown route {path}"})}
    finally:
        conn.close()
