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


def _notebook_fn(name):
    src = "".join(json.loads(NB.read_text())["cells"][3]["source"])
    tree = ast.parse("\n".join(l for l in src.splitlines() if not l.lstrip().startswith(("%", "!"))))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {"weibull_survival": lambda t, k, lam: math.exp(-((max(t, 0.0) / lam) ** k))}
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
    src = "".join(json.loads(NB.read_text())["cells"][3]["source"])
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
    src = "".join(json.loads(NB.read_text())["cells"][3]["source"])
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
