"""Offline tests for cubic-mars-ps1-rds-push. No AWS, no DB.  python -m pytest test_handler.py"""
import datetime, sys, types
import pyarrow as pa

sys.modules.setdefault("boto3", types.SimpleNamespace(client=lambda *a, **k: None))
_pg = types.ModuleType("pg8000"); _pgn = types.ModuleType("pg8000.native")
_pgn.Connection = object; _pg.native = _pgn
sys.modules["pg8000"] = _pg; sys.modules["pg8000.native"] = _pgn
import handler as H

CD = datetime.date(2026, 7, 26)


def _tbl(**over):
    base = dict(
        DEVICE_KEY=["D1", "D1", "D2"], device_category=["TVM"] * 3,
        ps1_fail_prob=[0.81, 0.81, 0.12], ps1_predicted=[1, 1, 0],
        threshold_used=[0.4] * 3, transit_day=["2026-07-25"] * 3,
        FACILITY_ID=["F1", "F1", "F2"], COMPONENT_SERIAL_NBR=["S1", "S2", "S3"],
        COMPONENT_TYPE=["BHU", "CHU", "BHU"], component_age_days=[10.0, 20.0, 30.0],
        ps1_risk_tier=["CRITICAL", "CRITICAL", "LOW"])
    base.update(over)
    return pa.table(base)


def test_grains_and_dedup():
    rows, _ = H.build_rows(_tbl(), "CHI", "r1", CD, "s3://b/k")
    assert len(rows["ps1_failure_predictions"]) == 2      # D1 deduped
    assert len(rows["ps1_serial_predictions"]) == 3
    assert len(rows["ps1_inference_runs"]) == 1


def test_attribution_sums_to_one():
    rows, _ = H.build_rows(_tbl(), "CHI", "r1", CD, "s3://b/k")
    d1 = [r for r in rows["ps1_serial_predictions"] if r["device_id"] == "D1"]
    assert abs(sum(r["attribution_weight"] for r in d1) - 1.0) < 1e-4
    assert all(abs(r["serial_risk_score"] - 0.81 * r["attribution_weight"]) < 1e-4 for r in d1)


def test_label_semantics_written():
    rows, warn = H.build_rows(_tbl(), "CHI", "r1", CD, "s3://b/k")
    assert all(r["target_col"] == "will_hardware_oos_3d" for r in rows["ps1_failure_predictions"])
    assert any("not chargeable" in w for w in warn)


def test_single_category_warning():
    _, warn = H.build_rows(_tbl(), "CHI", "r1", CD, "s3://b/k")
    assert any("ONLY ONE device_category" in w for w in warn)


def test_risk_band_derived_when_absent():
    t = _tbl(); t = t.drop_columns(["ps1_risk_tier"])
    rows, warn = H.build_rows(t, "CHI", "r1", CD, "s3://b/k")
    bands = {r["device_id"]: r["risk_band"] for r in rows["ps1_failure_predictions"]}
    assert bands["D1"] == "CRITICAL" and bands["D2"] == "LOW"
    assert any("risk_band derived" in w for w in warn)


def test_missing_required_column_raises():
    t = _tbl().drop_columns(["ps1_fail_prob"])
    try:
        H.build_rows(t, "CHI", "r1", CD, "s3://b/k")
    except ValueError as e:
        assert "failure_probability" in str(e); return
    raise AssertionError("expected ValueError")


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = 0
    for f in fns:
        try:
            f(); print(f"  PASS  {f.__name__}"); ok += 1
        except Exception:
            print(f"  FAIL  {f.__name__}"); traceback.print_exc()
    print(f"{ok}/{len(fns)} passed")
