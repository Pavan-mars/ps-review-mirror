"""PS4 local checks -- no AWS, no database.  Run:  python -m pytest -q tests/ps4

1. A run with a dataset missing from S3 is rolled back, not registered as current.
2. A manifest without silhouettes never stores another run's silhouettes as this run's.
"""
import sys
import types
from pathlib import Path

import pytest

LOADER = Path(__file__).resolve().parents[2] / "api/lambda/cubic-mars-ps4-v3-loader"


class _Conn:
    def __init__(self):
        self.sql = []
        self.columns = []

    def run(self, q, **kw):
        self.sql.append(q.split()[0].upper())
        return []


def _loader(monkeypatch, missing=(), manifest_extra=None):
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda *a, **k: types.SimpleNamespace()))
    monkeypatch.setitem(sys.modules, "pg8000", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pg8000.native", types.SimpleNamespace(Connection=object))
    monkeypatch.syspath_prepend(str(LOADER))
    sys.modules.pop("handler", None)
    import handler as h  # noqa: E402

    c = _Conn()
    inserted = {}
    man = {"run_id": "ps4-TEST", "pipeline_version": "v3", "asof_date": "2026-09-20", **(manifest_extra or {})}
    monkeypatch.setattr(h, "connect", lambda: c)
    monkeypatch.setattr(h, "load_manifest", lambda e: (man, {"manifest_key": "k"}))
    monkeypatch.setattr(h, "list_parquet", lambda b, p: [] if any(p.endswith(m) for m in missing) else [("x/part-0.parquet", 1)])
    monkeypatch.setattr(h, "read_parts", lambda b, parts: [{"device_id": "D1"}])
    monkeypatch.setattr(h, "hive_partition_cols", lambda k: {})

    def insert(conn, table, cols, rows, dry):
        inserted[table] = rows
        return len(rows)

    monkeypatch.setattr(h, "insert", insert)
    return h, c, inserted


def test_missing_dataset_rolls_back(monkeypatch):
    h, c, inserted = _loader(monkeypatch, missing=("weekly_alerts",))
    out = h.lambda_handler({"action": "load"}, None)
    assert out["ok"] is False and "weekly_alerts" in out["error"]
    assert "ROLLBACK" in c.sql and "COMMIT" not in c.sql
    assert "ps4_v3_runs" not in inserted


def test_complete_run_commits_and_nulls_foreign_silhouette(monkeypatch):
    h, c, inserted = _loader(monkeypatch)
    out = h.lambda_handler({"action": "load"}, None)
    assert out.get("ok") is not False and "COMMIT" in c.sql
    q = inserted["ps4_cluster_quality"]
    assert q and all(r["silhouette"] is None and r["quality_source"] == "not_published" for r in q)
