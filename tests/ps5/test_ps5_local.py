"""PS5 local checks -- no AWS, no database.  Run:  python -m pytest -q tests/ps5

1. p_event_within (the act_now driver) is a valid conditional probability, lifted straight
   from the notebook source so the test exercises the code that ships.
2. The RDS loader reports a load with a missing artifact as committed_partial / 500,
   never as a clean commit.
"""
import ast
import json
import math
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NB = ROOT / "notebooks/ps5_reliability_survival/PS5_Reliability_Survival_v5_6.ipynb"
LOADER = ROOT / "api/lambda/cubic-mars-ps5-rds-loader"


def _engine_src():
    """The engine cell, found by content: an operator cell added above it (e.g. PS5_PUBLISH) shifts its index."""
    for c in json.loads(NB.read_text())["cells"]:
        src = "".join(c["source"])
        if c["cell_type"] == "code" and "def build_intervals_from_failures" in src:
            return src
    raise AssertionError("PS5 engine cell not found in the notebook")


def _notebook_fn(name):
    src = "".join(_engine_src())
    tree = ast.parse("\n".join(l for l in src.splitlines() if not l.lstrip().startswith(("%", "!"))))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {"weibull_survival": lambda t, k, lam: math.exp(-((max(t, 0.0) / lam) ** k)), "_expm1_fn": math.expm1}
    exec(compile(ast.Module([node], []), str(NB), "exec"), ns)
    return ns[name]


def test_p_event_within_bounds_and_monotonic():
    p = _notebook_fn("p_event_within")
    for shape in (0.5, 1.0, 2.0):
        vals = [p(age, shape, 1.5, 7.0) for age in (0, 1, 5, 30, 200)]
        assert all(0.0 <= v <= 1.0 for v in vals)
        if shape < 1:      # decreasing hazard: a long quiet run lowers the 7-day risk
            assert vals == sorted(vals, reverse=True)
        if shape > 1:      # increasing hazard: wear-out raises it
            assert vals == sorted(vals)
    assert p(0, 1.0, 1.5, 7.0) == pytest.approx(1 - math.exp(-7 / 1.5))   # memoryless check
    assert p(0, 1.0, 1.5, 0.0) == pytest.approx(0.0)


def test_long_quiet_device_is_not_scored_as_certain():
    """26-Sep-2026 run: 51 gates / 298 validators got p_oos_1d = 1.0 because S(age) underflowed. With shape < 1 a
    long quiet run LOWERS the hazard, so these must score below a recently faulted device, never 1.0."""
    p = _notebook_fn("p_event_within")
    long_quiet = p(400.0, 0.93, 1.0, 1.0)          # S(400) ~ 1e-163 under the old ratio form
    assert 0.0 < long_quiet < 0.5
    assert long_quiet < p(0.5, 0.93, 1.0, 1.0)


def _load_loader(monkeypatch):
    boto3 = types.SimpleNamespace(client=lambda *a, **k: types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setitem(sys.modules, "pg8000", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pg8000.native", types.SimpleNamespace(Connection=object))
    monkeypatch.syspath_prepend(str(LOADER))
    sys.modules.pop("handler", None)
    import handler  # noqa: E402
    return handler


class _Conn:
    def __init__(self):
        self.sql = []

    def run(self, q, **kw):
        self.sql.append(q)
        return []


@pytest.mark.parametrize("missing", [set(), {"validators/serial_reliability"}])
def test_loader_flags_partial_load(monkeypatch, missing):
    h = _load_loader(monkeypatch)
    c = _Conn()
    monkeypatch.setattr(h, "conn", lambda: c)
    monkeypatch.setattr(h, "target_columns", lambda _c, t: ["city_id", "device_type", "device_id",
                                                              "component_serial_nbr", "model", "feature_name"])
    monkeypatch.setattr(h, "pk_columns", lambda _c, t: [])
    monkeypatch.setattr(h, "rds_count", lambda _c, t: 0)

    def read_csv(bucket, key):
        tag = "/".join(key.split("/")[-2:]).replace(".csv", "")
        folder, fname = tag.split("/")
        if "%s/%s" % (folder, fname[len(folder) + 1:]) in missing:
            raise FileNotFoundError(key)
        return [{"device_id": "D1", "component_serial_nbr": "S1", "model": "m", "feature_name": "f"}]

    monkeypatch.setattr(h, "read_csv", read_csv)
    monkeypatch.setattr(h, "read_json", lambda b, k: {"weibull": {"shape": 0.9, "scale": 1.5}, "cv_cindex": 0.7,
                                                      "gate_pass": True, "cindex_floor": 0.65, "run_date": "2026-08-29"})
    out = h.lambda_handler({}, None)
    body = json.loads(out["body"])
    if missing:
        assert body["status"] == "committed_partial" and out["statusCode"] == 500
        assert "validators/serial_reliability" in body["incomplete"]
    else:
        assert body["status"] == "committed" and out["statusCode"] == 200


def test_device_weighting_stops_chronic_device_dominating():
    """P5-4. Needs numpy + scipy (present in Studio). Uses the notebook's own weibull_fit."""
    np = pytest.importorskip("numpy")
    sciopt = pytest.importorskip("scipy.optimize")
    src = "".join(_engine_src())
    tree = ast.parse("\n".join(l for l in src.splitlines() if not l.lstrip().startswith(("%", "!"))))
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("weibull_fit", "weibull_nll")]
    ns = {"np": np, "math": math, "_sciopt": sciopt, "_HAS_LIFELINES": False}
    exec(compile(ast.Module(fns, []), str(NB), "exec"), ns)
    t = np.array([1.0] * 100 + [30.0] * 10)          # one chronic device, ten quiet ones
    e = np.ones_like(t, dtype=int)
    n_per = np.array([100] * 100 + [1] * 10, float)
    w = (1 / n_per) * (len(n_per) / np.sum(1 / n_per))
    med = lambda k, lam: lam * math.log(2) ** (1 / k)
    unweighted = med(*ns["weibull_fit"](t, e))
    weighted = med(*ns["weibull_fit"](t, e, w))
    assert unweighted < 3 and weighted > 5 * unweighted


def test_device_whose_last_fault_is_on_the_cutoff_day_still_gets_an_open_interval():
    """26-Sep-2026: devices faulting ON EVENT_END_DATE had no open interval and were never scored."""
    pd = pytest.importorskip("pandas")
    src = "".join(_engine_src())
    tree = ast.parse("\n".join(l for l in src.splitlines() if not l.lstrip().startswith(("%", "!"))))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_intervals_from_failures")
    ns = {"pd": pd, "CONFIG": {"EVENT_END_DATE": "2026-08-29"}}
    exec(compile(ast.Module([fn], []), str(NB), "exec"), ns)
    f = pd.DataFrame({"DEVICE_ID": ["A", "A", "B", "B"], "DEVICE_KEY": ["A", "A", "B", "B"],
                      "failure_date": pd.to_datetime(["2026-08-20", "2026-08-29", "2026-08-10", "2026-08-25"])})
    iv = ns["build_intervals_from_failures"](f, "GATE")
    open_iv = iv[iv["is_ongoing"]].set_index("DEVICE_ID")["interval_days"]
    assert set(open_iv.index) == {"A", "B"}          # A faulted on the cut-off day and is still scored
    assert open_iv["A"] == 1 and open_iv["B"] == 5


def test_loader_writes_fleet_survival_and_flags_missing_params(monkeypatch):
    """sql/74: one ps5_fleet_survival row per fleet; a missing params json is a partial load, never silent."""
    h = _load_loader(monkeypatch)
    c = _Conn()
    monkeypatch.setattr(h, "conn", lambda: c)
    monkeypatch.setattr(h, "target_columns", lambda _c, t: ["city_id", "device_type", "device_id"])
    monkeypatch.setattr(h, "pk_columns", lambda _c, t: [])
    monkeypatch.setattr(h, "rds_count", lambda _c, t: 0)
    monkeypatch.setattr(h, "read_csv", lambda b, k: [{"device_id": "D1"}])

    def read_json(bucket, key):
        if "validators" in key:
            raise FileNotFoundError(key)
        return {"weibull": {"shape": 0.94, "scale": 2.0}, "cv_cindex": 0.67, "gate_pass": True}

    monkeypatch.setattr(h, "read_json", read_json)
    body = json.loads(h.lambda_handler({}, None)["body"])
    assert body["loaded"]["gates/device_survival_params"]["shape"] == 0.94
    assert "validators/device_survival_params" in body["incomplete"] and body["status"] == "committed_partial"
    assert sum("INSERT INTO ps5_fleet_survival" in q for q in c.sql) == 2


def test_overdue_uses_device_own_history_with_fleet_fallback():
    """P5-4 (27-Sep-2026). Runs the notebook's own overdue block: a chronic device with a tight rhythm is judged
    against its own median gap; a device with too little recent history falls back to the fleet value."""
    pd = pytest.importorskip("pandas")
    np = pytest.importorskip("numpy")
    src = _engine_src()
    start = src.index("    # P5-4 (27-Sep-2026): overdue against the device's OWN")
    block = "\n".join(l[4:] for l in src[start:src.index("    dev = pd.DataFrame({", start)].splitlines())
    run = pd.Timestamp("2026-08-29")
    rows = [("CHRONIC", run - pd.Timedelta(days=d), 0.5, 1) for d in range(1, 20)]      # 19 half-day gaps
    rows += [("QUIET", run - pd.Timedelta(days=60), 30.0, 1)]                             # 1 gap: too few
    Xiv = pd.DataFrame(rows, columns=["DEVICE_ID", "interval_start_date", "interval_days", "event_observed"])
    ns = {"pd": pd, "np": np, "run": run, "Xiv": Xiv,
          "CONFIG": {"OVERDUE_LOOKBACK_DAYS": 90, "OVERDUE_MIN_INTERVALS": 5},
          "dids": np.array(["CHRONIC", "QUIET"]), "ages": np.array([2.0, 2.0]), "med_i": np.array([1.5, 1.5])}
    exec(block, ns)
    assert list(ns["od_basis"]) == ["device", "fleet"]
    assert ns["od_ref"][0] == pytest.approx(0.5) and ns["od_ref"][1] == pytest.approx(1.5)
    assert list(ns["ages"] > ns["od_ref"]) == [True, True]
