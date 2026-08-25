#!/usr/bin/env python3
# =============================================================================
# ps5_api -- Lambda Function URL serving /ps5/devices and /ps5/serials for the
# CUBIC MARS dashboard, reading the migration-06 latest-per-row views live from
# Aurora. Runs IN the VPC (only in-VPC compute reaches the private writer); the
# Function URL is the public front door the dashboard calls.
#
# DB driver = pg8000 (pure python, no native build). Password comes ONLY from
# Secrets Manager (PS5_RDS_SECRET); never logged, written, or embedded.
#
# Response shape == apiPS5DeviceRUL() / apiPS5SerialHealth() so the dashboard
# flips from SAMPLE to live with no data-shape change.
# =============================================================================
import json, math, os, ssl


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except Exception:
        return None


def _weibull_q(median, shape, p):
    """Weibull quantile from fitted median + shape (median = scale*(ln2)^(1/k))."""
    median = _num(median); shape = _num(shape)
    if not median or not shape or shape <= 0:
        return None
    scale = median / (math.log(2.0) ** (1.0 / shape))
    return round(scale * ((-math.log(1.0 - p)) ** (1.0 / shape)), 1)


# Read base tables directly (the migration-06 view omits facility_id); replicate its
# latest-per-device + event-definition join here so the API is self-contained.
DEV_QUERY = """
SELECT e.device_id, e.mars_device_category, e.facility_id, e.current_healthy_age_days, e.rul_standard_days,
       e.predicted_median_survival_days, e.hazard_score, e.risk_band, e.is_overdue, e.days_since_hw_oos, e.roll_fail_30d,
       e.concordance_index, e.weibull_shape, e.data_quality_gate_passed,
       e.event_definition, e.event_def_version, d.label AS event_label, d.window_mode, d.telemetry_start,
       d.cindex_floor, e.as_of_date
FROM ps5_reliability_estimates e
JOIN (SELECT city_id, device_id, MAX(as_of_date) AS mx
        FROM ps5_reliability_estimates GROUP BY city_id, device_id) l
  ON l.city_id = e.city_id AND l.device_id = e.device_id AND l.mx = e.as_of_date
LEFT JOIN ps5_event_definition d ON d.event_def_version = e.event_def_version
WHERE e.city_id = %s
ORDER BY e.rul_standard_days ASC
"""

SER_QUERY = """
SELECT device_id, component_serial_nbr, component_type, mars_device_category, component_age_days,
       device_oos_failures_total, risk_score, risk_tier, expected_component_rul_days,
       predicted_median_survival_days, is_overdue, event_def_version, as_of_date
FROM v_ps5_serial_oos_latest
WHERE city_id = %s
ORDER BY risk_score DESC
"""


def _rows(conn, q, city):
    cur = conn.cursor()
    try:
        cur.execute(q, (city,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        cur.close()


def ps5_devices(conn, city):
    rows = _rows(conn, DEV_QUERY, city)
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


def ps5_serials(conn, city):
    rows = _rows(conn, SER_QUERY, city)
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


def _connect():
    import pg8000.dbapi as pg, boto3
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    s = json.loads(boto3.client("secretsmanager").get_secret_value(
        SecretId=os.environ["PS5_RDS_SECRET"])["SecretString"])
    return pg.connect(user=s["username"], password=s["password"], host=s["host"],
                      port=int(s.get("port", 5432)), database=s.get("dbname", "appdb"),
                      ssl_context=ctx, timeout=30, tcp_keepalive=True)


_CORS = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*",
         "Access-Control-Allow-Methods": "GET, OPTIONS", "Access-Control-Allow-Headers": "*"}

def _resp(body, code=None):
    if code is None:
        code = 200 if body is not None else 404
    return {"statusCode": code, "headers": _CORS,
            "body": json.dumps(body if body is not None else {"error": "no rows"}, default=str)}


def lambda_handler(event, context=None):
    event = event or {}
    method = (((event.get("requestContext") or {}).get("http") or {}).get("method")
              or event.get("httpMethod") or "GET").upper()
    if method == "OPTIONS":                       # CORS preflight
        return {"statusCode": 204, "headers": _CORS, "body": ""}
    path = (event.get("rawPath") or event.get("path") or "").rstrip("/")
    qs = event.get("queryStringParameters") or {}
    city = (qs.get("city") or os.environ.get("PS5_CITY", "CHI"))
    try:
        conn = _connect()
    except Exception as e:
        return _resp({"error": "db connect failed", "detail": str(e)}, 500)
    try:
        if path.endswith("/ps5/devices"):
            return _resp(ps5_devices(conn, city))
        if path.endswith("/ps5/serials"):
            return _resp(ps5_serials(conn, city))
        if path.endswith("/health") or path in ("", "/"):
            return _resp({"status": "ok", "routes": ["/ps5/devices", "/ps5/serials"]})
        return _resp({"error": f"unknown route {path}"}, 404)
    except Exception as e:
        return _resp({"error": "query failed", "detail": str(e)}, 500)
    finally:
        conn.close()
