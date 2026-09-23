#!/usr/bin/env python3
# =============================================================================
# load_transform_output_to_rds.py -- load PS2 pipeline output into RDS.
#
# Two sources, same script:
#  1. Batch-Transform-scored output (markov/hmm/recurrence, device+serial) from
#     s3://.../chicago/ps2/scored/<family>/<grain>/asof=.../  -- upserted into
#     new per-family tables (idempotent on (city_id, device_id[, serial], as_of_date)).
#  2. Population-level descriptive output computed directly by
#     refresh_device_state.py (no model) from
#     s3://.../chicago/ps2/population/<name>/asof=.../  -- upserted into the
#     EXISTING dashboard tables where they already exist (ps2_subsystem_hub_edges,
#     ps2_subsystem_hub_summary, ps2_subsystem_associations, ps2_hmm_regimes,
#     ps2_facility_contagion_summary) plus one genuinely new table
#     (ps2_conditional_prob).
#
# Schema target = migration 07 (sql/migrations or dashboard/backfill, matching
# migration 06's idiom -- additive, idempotent, CREATE TABLE IF NOT EXISTS +
# ON CONFLICT). Aurora is the APP-TIER db only.
#
# Usage:
#   python load_transform_output_to_rds.py \
#     --scored s3://.../chicago/ps2/scored --population s3://.../chicago/ps2/population \
#     --asof 2026-07-24 --city CHI --secret cubic/rds/dashboard [--dry-run]
# =============================================================================
import argparse, io, json, math, uuid

import pandas as pd


def _f(v):
    try:
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
    except Exception:
        return None


def _read_s3_prefix(bucket, prefix):
    import boto3
    s3 = boto3.client("s3"); frames = []
    tok = None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if tok:
            kw["ContinuationToken"] = tok
        resp = s3.list_objects_v2(**kw)
        for o in resp.get("Contents", []):
            if o["Key"].endswith("/"):
                continue
            body = s3.get_object(Bucket=bucket, Key=o["Key"])["Body"].read()
            try:
                frames.append(pd.read_parquet(io.BytesIO(body)))
            except Exception:
                frames.append(pd.read_csv(io.BytesIO(body)))
        if not resp.get("IsTruncated"):
            break
        tok = resp.get("NextContinuationToken")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _split_s3(uri):
    p = uri.replace("s3://", "").split("/", 1)
    return p[0], (p[1] if len(p) > 1 else "")


MARKOV_DEV_SQL = """INSERT INTO ps2_markov_device_scores
  (city_id, run_id, device_id, mars_device_category, current_subsystem, predicted_next_subsystem,
   predicted_next_prob, escalation_subsystem, escalation_prob, self_loop_prob, as_of_date,
   event_definition, event_def_version, scored_at)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
  ON CONFLICT (city_id, device_id, as_of_date) DO UPDATE SET
   run_id=EXCLUDED.run_id, current_subsystem=EXCLUDED.current_subsystem,
   predicted_next_subsystem=EXCLUDED.predicted_next_subsystem, predicted_next_prob=EXCLUDED.predicted_next_prob,
   escalation_subsystem=EXCLUDED.escalation_subsystem, escalation_prob=EXCLUDED.escalation_prob,
   self_loop_prob=EXCLUDED.self_loop_prob, scored_at=now()"""

MARKOV_SER_SQL = """INSERT INTO ps2_markov_serial_scores
  (city_id, run_id, device_id, component_serial_nbr, component_description, mars_device_category,
   current_subsystem, predicted_next_subsystem, predicted_next_prob, escalation_subsystem,
   escalation_prob, as_of_date, event_definition, event_def_version, scored_at)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
  ON CONFLICT (city_id, device_id, component_serial_nbr, as_of_date) DO UPDATE SET
   run_id=EXCLUDED.run_id, predicted_next_subsystem=EXCLUDED.predicted_next_subsystem,
   predicted_next_prob=EXCLUDED.predicted_next_prob, escalation_subsystem=EXCLUDED.escalation_subsystem,
   escalation_prob=EXCLUDED.escalation_prob, scored_at=now()"""

# hmm/recurrence serial-grain tables -- added 2026-07-24. run_batch_transform.py
# has always scored all three families at BOTH grains (device, serial); only
# markov's serial table+load path was originally wired. This closes that gap so
# ps2_hmm_serial_regime / ps2_serial_recurrence_scores get populated the same
# way markov's does (migration 07).
HMM_SER_SQL = """INSERT INTO ps2_hmm_serial_regime
  (city_id, run_id, device_id, component_serial_nbr, component_description, mars_device_category,
   current_regime, regime_posterior, prob_escalate_to_critical, dwell_days_expected, as_of_date,
   event_definition, event_def_version, scored_at)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
  ON CONFLICT (city_id, device_id, component_serial_nbr, as_of_date) DO UPDATE SET
   run_id=EXCLUDED.run_id, current_regime=EXCLUDED.current_regime, regime_posterior=EXCLUDED.regime_posterior,
   prob_escalate_to_critical=EXCLUDED.prob_escalate_to_critical, dwell_days_expected=EXCLUDED.dwell_days_expected,
   scored_at=now()"""

RECURRENCE_SER_SQL = """INSERT INTO ps2_serial_recurrence_scores
  (city_id, run_id, device_id, component_serial_nbr, component_description, mars_device_category,
   cascade_days_total, cascade_rate, recurrence_class, recurrence_hazard_score, as_of_date,
   event_definition, event_def_version, scored_at)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
  ON CONFLICT (city_id, device_id, component_serial_nbr, as_of_date) DO UPDATE SET
   run_id=EXCLUDED.run_id, cascade_days_total=EXCLUDED.cascade_days_total, cascade_rate=EXCLUDED.cascade_rate,
   recurrence_class=EXCLUDED.recurrence_class, recurrence_hazard_score=EXCLUDED.recurrence_hazard_score,
   scored_at=now()"""

FAMILY_SER_SQL = {"markov": MARKOV_SER_SQL, "hmm": HMM_SER_SQL, "recurrence": RECURRENCE_SER_SQL}

HMM_DEV_SQL = """INSERT INTO ps2_hmm_device_regime
  (city_id, run_id, device_id, mars_device_category, current_regime, regime_posterior,
   prob_escalate_to_critical, dwell_days_expected, as_of_date, event_definition, event_def_version, scored_at)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
  ON CONFLICT (city_id, device_id, as_of_date) DO UPDATE SET
   run_id=EXCLUDED.run_id, current_regime=EXCLUDED.current_regime, regime_posterior=EXCLUDED.regime_posterior,
   prob_escalate_to_critical=EXCLUDED.prob_escalate_to_critical, dwell_days_expected=EXCLUDED.dwell_days_expected,
   scored_at=now()"""

RECURRENCE_DEV_SQL = """INSERT INTO ps2_device_recurrence_scores
  (city_id, run_id, device_id, mars_device_category, cascade_days_total, cascade_rate,
   recurrence_class, recurrence_hazard_score, as_of_date, event_definition, event_def_version, scored_at)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
  ON CONFLICT (city_id, device_id, as_of_date) DO UPDATE SET
   run_id=EXCLUDED.run_id, cascade_days_total=EXCLUDED.cascade_days_total, cascade_rate=EXCLUDED.cascade_rate,
   recurrence_class=EXCLUDED.recurrence_class, recurrence_hazard_score=EXCLUDED.recurrence_hazard_score,
   scored_at=now()"""

FAMILY_DEV_SQL = {"markov": MARKOV_DEV_SQL, "hmm": HMM_DEV_SQL, "recurrence": RECURRENCE_DEV_SQL}


def _dev_row(family, city, run_id, r, asof):
    edv = str(r.get("event_def_version", "2026-07-24.v1")); edn = str(r.get("event_definition", "cascade_chain"))
    if family == "markov":
        return (city, run_id, r["DEVICE_ID"], r.get("mars_device_category"), r.get("current_subsystem"),
                r.get("predicted_next_subsystem"), _f(r.get("predicted_next_prob")), r.get("escalation_subsystem"),
                _f(r.get("escalation_prob")), _f(r.get("self_loop_prob")), asof, edn, edv)
    if family == "hmm":
        return (city, run_id, r["DEVICE_ID"], r.get("mars_device_category"), r.get("current_regime"),
                r.get("regime_posterior"), _f(r.get("prob_escalate_to_critical")), _f(r.get("dwell_days_expected")),
                asof, edn, edv)
    return (city, run_id, r["DEVICE_ID"], r.get("mars_device_category"), _f(r.get("cascade_days_total")),
            _f(r.get("cascade_rate")), r.get("recurrence_class"), _f(r.get("recurrence_hazard_score")),
            asof, edn, edv)


def _ser_row(family, city, run_id, r, asof):
    """Same shape as _dev_row but with the serial-identity columns spliced in
    right after device_id, matching each *_SER_SQL's column order above."""
    edv = str(r.get("event_def_version", "2026-07-24.v1")); edn = str(r.get("event_definition", "cascade_chain"))
    ser, desc = r["COMPONENT_SERIAL_NBR"], r.get("COMPONENT_DESCRIPTION")
    if family == "markov":
        return (city, run_id, r["DEVICE_ID"], ser, desc, r.get("mars_device_category"),
                r.get("current_subsystem"), r.get("predicted_next_subsystem"), _f(r.get("predicted_next_prob")),
                r.get("escalation_subsystem"), _f(r.get("escalation_prob")), asof, edn, edv)
    if family == "hmm":
        return (city, run_id, r["DEVICE_ID"], ser, desc, r.get("mars_device_category"),
                r.get("current_regime"), r.get("regime_posterior"), _f(r.get("prob_escalate_to_critical")),
                _f(r.get("dwell_days_expected")), asof, edn, edv)
    return (city, run_id, r["DEVICE_ID"], ser, desc, r.get("mars_device_category"),
            _f(r.get("cascade_days_total")), _f(r.get("cascade_rate")), r.get("recurrence_class"),
            _f(r.get("recurrence_hazard_score")), asof, edn, edv)


POP_SQL = {
    "phi_matrix": ("""INSERT INTO ps2_subsystem_hub_edges (city_id, source_sub, target_sub, phi, computed_date)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (city_id, source_sub, target_sub, computed_date) DO UPDATE SET phi=EXCLUDED.phi""",
        lambda city, asof, r: (city, r["source_sub"], r["target_sub"], _f(r["phi"]), asof)),
    "network_centrality": ("""INSERT INTO ps2_subsystem_hub_summary (city_id, node_id, freq, is_hub, computed_date)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (city_id, node_id, computed_date) DO UPDATE SET freq=EXCLUDED.freq, is_hub=EXCLUDED.is_hub""",
        lambda city, asof, r: (city, r["node_id"], int(r["freq"]), bool(r["is_hub"]), asof)),
    "association_rules": ("""INSERT INTO ps2_subsystem_associations
        (city_id, antecedent_subsystem, consequent_subsystem, support, confidence, lift, conviction, computed_date)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (city_id, antecedent_subsystem, consequent_subsystem, computed_date) DO UPDATE SET
        support=EXCLUDED.support, confidence=EXCLUDED.confidence, lift=EXCLUDED.lift, conviction=EXCLUDED.conviction""",
        lambda city, asof, r: (city, r["antecedent_subsystem"], r["consequent_subsystem"], _f(r["support"]),
                               _f(r["confidence"]), _f(r["lift"]), _f(r["conviction"]), asof)),
    "conditional_prob": ("""INSERT INTO ps2_conditional_prob
        (city_id, antecedent_subsystem, consequent_subsystem, conditional_prob, support_count, computed_date)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (city_id, antecedent_subsystem, consequent_subsystem, computed_date) DO UPDATE SET
        conditional_prob=EXCLUDED.conditional_prob, support_count=EXCLUDED.support_count""",
        lambda city, asof, r: (city, r["antecedent"], r["consequent"], _f(r["conditional_prob"]),
                               int(r["support_count"]), asof)),
    "facility_contagion": ("""INSERT INTO ps2_facility_contagion_summary
        (city_id, total_facility_cascade_days, multi_device_contagion_pct, hotspot_facility_id,
         hotspot_facility_name, hotspot_min_devices, hotspot_max_devices, computed_date)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (city_id, computed_date) DO UPDATE SET
        total_facility_cascade_days=EXCLUDED.total_facility_cascade_days,
        multi_device_contagion_pct=EXCLUDED.multi_device_contagion_pct,
        hotspot_facility_id=EXCLUDED.hotspot_facility_id, hotspot_facility_name=EXCLUDED.hotspot_facility_name,
        hotspot_min_devices=EXCLUDED.hotspot_min_devices, hotspot_max_devices=EXCLUDED.hotspot_max_devices""",
        lambda city, asof, r: (city, int(r["total_facility_cascade_days"]), _f(r["multi_device_contagion_pct"]),
                               r.get("hotspot_facility_id"), r.get("hotspot_facility_name"),
                               r.get("hotspot_min_devices"), r.get("hotspot_max_devices"), asof)),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scored", required=True, help="s3://.../chicago/ps2/scored")
    ap.add_argument("--population", required=True, help="s3://.../chicago/ps2/population")
    ap.add_argument("--asof", required=True)
    ap.add_argument("--city", default="CHI")
    ap.add_argument("--secret", required=False)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    scored_b, scored_base = _split_s3(args.scored.rstrip("/"))
    pop_b, pop_base = _split_s3(args.population.rstrip("/"))

    dev_frames, ser_frames = {}, {}
    for fam in ("markov", "hmm", "recurrence"):
        dev_frames[fam] = _read_s3_prefix(scored_b, f"{scored_base}/{fam}/device/asof={args.asof}/")
        ser_frames[fam] = _read_s3_prefix(scored_b, f"{scored_base}/{fam}/serial/asof={args.asof}/")
        print(f"[load] {fam}: device rows={len(dev_frames[fam])} serial rows={len(ser_frames[fam])}")

    pop_frames = {}
    for name in POP_SQL:
        pop_frames[name] = _read_s3_prefix(pop_b, f"{pop_base}/{name}/asof={args.asof}/")
        print(f"[load] population/{name}: {len(pop_frames[name])} rows")

    if args.dry_run or not args.secret:
        print("[load] dry-run (no RDS write). Pass --secret to write."); return

    import boto3, psycopg2
    sec = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=args.secret)["SecretString"])
    conn = psycopg2.connect(host=sec["host"], port=int(sec.get("port", 5432)), dbname=sec["dbname"],
                            user=sec["username"], password=sec["password"], connect_timeout=8)
    conn.autocommit = False
    run_id = str(uuid.uuid4())
    try:
        cur = conn.cursor()
        cur.execute("""INSERT INTO ps2_scoring_runs
            (run_id, city_id, run_ts, n_devices_scored, families, status, note, run_date)
            VALUES (%s,%s, now(), %s,%s,'SUCCESS',%s,%s)""",
            (run_id, args.city, sum(len(d) for d in dev_frames.values()),
             ",".join(k for k, v in dev_frames.items() if len(v)),
             f"batch-transform PS2 cascade {args.asof}", args.asof))

        for fam, dev in dev_frames.items():
            for _, r in dev.iterrows():
                cur.execute(FAMILY_DEV_SQL[fam], _dev_row(fam, args.city, run_id, r, args.asof))
        for fam, ser in ser_frames.items():
            if not len(ser):
                continue
            if "COMPONENT_SERIAL_NBR" not in ser.columns:
                print(f"  [load] WARNING: {fam}/serial has {len(ser)} rows but no COMPONENT_SERIAL_NBR column "
                      f"-- scorer output didn't carry serial identity through, skipping this family/grain")
                continue
            for _, r in ser.iterrows():
                cur.execute(FAMILY_SER_SQL[fam], _ser_row(fam, args.city, run_id, r, args.asof))

        for name, pdf in pop_frames.items():
            if not len(pdf):
                continue
            sql, row_fn = POP_SQL[name]
            for _, r in pdf.iterrows():
                cur.execute(sql, row_fn(args.city, args.asof, r))

        conn.commit()
        print(f"[load] committed run {run_id} for {args.city} @ {args.asof}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
