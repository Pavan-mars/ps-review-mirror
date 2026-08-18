#!/usr/bin/env python3
"""One-shot builder: extract PS1 notebook CELLS 6-8 into notebooks/ps1_features.py."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NB_DIR = ROOT / "notebooks" / "ps1_failure_prediction"
OUT = ROOT / "notebooks" / "ps1_features.py"

FLEETS = {
    "GATE": "PS1_3d_GATE_SageMaker_MLflow_FeatureStore.ipynb",
    "TVM": "PS1_3d_TVM_SageMaker_MLflow_FeatureStore.ipynb",
    "VALIDATOR": "PS1_3d_VALIDATOR_SageMaker_MLflow_FeatureStore.ipynb",
}

CELL_IDS = {
    "read_spine": "spark6",
    "add_auxiliary": "spark7",
    "join_and_materialise": "spark8",
}


def load_nb(fleet: str) -> dict:
    """Load from git HEAD — patched notebook wrappers would break extraction."""
    rel = (NB_DIR / FLEETS[fleet]).relative_to(ROOT).as_posix()
    try:
        raw = subprocess.check_output(
            ["git", "show", f"HEAD:{rel}"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
        return json.loads(raw.decode("utf-8"))
    except (subprocess.CalledProcessError, FileNotFoundError):
        return json.loads((NB_DIR / FLEETS[fleet]).read_text(encoding="utf-8"))


def find_cell(nb: dict, suffix: str) -> str:
    needle = f"ps1-{fleet.lower()}-{suffix}" if fleet else suffix
    for cell in nb["cells"]:
        cid = cell.get("id", "")
        if cid.endswith(suffix) or suffix in cid:
            src = "".join(cell.get("source", []))
            if "CELL" in src[:120]:
                return src
    raise KeyError(f"cell {suffix} not found")


def extract_etl_body(src: str) -> str:
    """Strip if should_run_etl(): wrapper and checkpoint else branch."""
    lines = src.splitlines()
    # drop header comment line
    while lines and (lines[0].strip().startswith("#") or not lines[0].strip()):
        if "CELL" in lines[0]:
            lines = lines[1:]
            break
        lines = lines[1:]
    # find if should_run_etl():
    start = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("if should_run_etl():"):
            start = i + 1
            break
    else:
        raise ValueError("no should_run_etl block")
    # find else: checkpoint at same indent
    end = len(lines)
    for i in range(len(lines) - 1, start, -1):
        if lines[i].strip() == "else:":
            end = i
            break
    body = lines[start:end]
    # dedent one level (4 spaces)
    dedented = []
    for line in body:
        if line.startswith("    "):
            dedented.append(line[4:])
        elif line.strip() == "":
            dedented.append("")
        else:
            dedented.append(line)
    return "\n".join(dedented).rstrip() + "\n"


def parse_feature_lists(fleet: str) -> dict[str, list[str]]:
    nb = load_nb(fleet)
    lists: dict[str, list[str]] = {}
    for cell in nb["cells"]:
        src = "".join(cell.get("source", []))
        if "FAILURE_FEATURES" not in src or "ALL_CANDIDATE_FEATURES" not in src:
            continue
        for name in (
            "FAILURE_FEATURES", "PS1_TAP_FEATURES", "GATE_TAP_FEATURES", "GATE_GOLD_EXTRA",
            "TVM_TAP_FEATURES", "TVM_GOLD_EXTRA", "VALIDATOR_TAP_FEATURES", "VALIDATOR_GOLD_EXTRA",
            "INCIDENT_FEATURES", "METRIC_FEATURES", "CHAIN_FEATURES", "ANOMALY_FEATURES",
            "LIFETIME_FEATURES", "USAGE_FEATURES", "MTTR_FEATURES", "USAGE_EXT_FEATURES",
            "SPARK_DERIVED_FEATURES", "OOS_LABEL_CONCURRENT_FEATURES",
            "REQUIRED_PS1_FEATURES",
        ):
            m = re.search(rf"^{name}\s*=\s*(\[.*?\])\s*$", src, re.M | re.S)
            if m:
                lists[name] = ast.literal_eval(m.group(1))
        # ALL_CANDIDATE_FEATURES uses a list-comprehension in notebooks — rebuild it.
        tap = lists.get("GATE_TAP_FEATURES") or lists.get("TVM_TAP_FEATURES") or lists.get("VALIDATOR_TAP_FEATURES") or []
        gold = lists.get("GATE_GOLD_EXTRA") or lists.get("TVM_GOLD_EXTRA") or lists.get("VALIDATOR_GOLD_EXTRA") or []
        concurrent = set(lists.get("OOS_LABEL_CONCURRENT_FEATURES", []))
        lists["ALL_CANDIDATE_FEATURES"] = [
            c for c in dict.fromkeys(
                lists.get("FAILURE_FEATURES", [])
                + lists.get("PS1_TAP_FEATURES", [])
                + tap
                + gold
                + lists.get("INCIDENT_FEATURES", [])
                + lists.get("METRIC_FEATURES", [])
                + lists.get("CHAIN_FEATURES", [])
                + lists.get("ANOMALY_FEATURES", [])
                + lists.get("LIFETIME_FEATURES", [])
                + lists.get("USAGE_FEATURES", [])
                + lists.get("MTTR_FEATURES", [])
                + lists.get("USAGE_EXT_FEATURES", [])
                + lists.get("SPARK_DERIVED_FEATURES", [])
            )
            if c not in concurrent
        ]
        break
    return lists


def tap_block(fleet: str) -> str:
    nb = load_nb(fleet)
    src = find_cell_by_id(nb, f"ps1-{fleet.lower()}-spark7")
    body = extract_etl_body(src)
    # isolate tap section between tap signal comment and print lazy plan
    m = re.search(
        r"(# ── .*? tap signal.*?)\n(.*?)(^\s*print\(\".*?tap prior-only rolling plan",
        body,
        re.S | re.M,
    )
    if not m:
        raise ValueError(f"tap block not found for {fleet}")
    return m.group(1) + "\n" + m.group(2)


def find_cell_by_id(nb: dict, cell_id: str) -> str:
    for cell in nb["cells"]:
        if cell.get("id") == cell_id:
            return "".join(cell.get("source", []))
    raise KeyError(cell_id)


def find_spark_cell(nb: dict, cell_num: int) -> str:
    """Locate CELL 6/7/8 by header comment (VALIDATOR notebook reuses tvm cell ids)."""
    markers = {6: "CELL 6", 7: "CELL 7", 8: "CELL 8"}
    needle = markers[cell_num]
    for cell in nb["cells"]:
        src = "".join(cell.get("source", []))
        if src.lstrip().startswith(f"#  {needle}") or src.lstrip().startswith(f"# {needle}"):
            return src
    raise KeyError(f"CELL {cell_num} not found")


def main() -> None:
    global fleet
    gate_nb = load_nb("GATE")
    gate_cell6 = extract_etl_body(find_spark_cell(gate_nb, 6))
    gate_cell7_prefix = extract_etl_body(find_spark_cell(gate_nb, 7))
    gate_cell8 = extract_etl_body(find_spark_cell(gate_nb, 8))

    # tap blocks per fleet
    tap_blocks = {}
    for fl in FLEETS:
        nb = load_nb(fl)
        src7 = extract_etl_body(find_spark_cell(nb, 7))
        # split at tap signal comment
        idx = src7.find("# ──")
        tap_idx = src7.find("tap signal")
        if tap_idx < 0:
            raise ValueError(f"no tap section in {fl}")
        tap_start = src7.rfind("\n", 0, tap_idx) + 1
        end_marker = "prior-only rolling plan created"
        tap_end = src7.find(end_marker, tap_idx)
        if tap_end < 0:
            raise ValueError(f"tap end marker not found in {fl}")
        tap_end = src7.find("\n", tap_end) + 1
        tap_blocks[fl] = src7[tap_start:tap_end]
        if fl == "GATE":
            gate_cell7_prefix = src7[:tap_start]

    cell8_bodies = {
        fl: extract_etl_body(find_spark_cell(load_nb(fl), 8)) for fl in FLEETS
    }

    fleet_configs = {fl: parse_feature_lists(fl) for fl in FLEETS}

    module = HEADER
    module += render_config(fleet_configs)
    module += HELPERS + render_module_helpers(gate_cell6)
    module += render_read_spine(gate_cell6)
    module += render_add_auxiliary(gate_cell7_prefix, tap_blocks)
    module += render_join(cell8_bodies)

    OUT.write_text(module, encoding="utf-8")
    print(f"Wrote {OUT} ({len(module.splitlines())} lines)")


HEADER = '''\
# Shared PS1 feature ETL — extracted verbatim from fleet notebook CELLS 6-8.
# Do not edit by hand; regenerate with tooling/build_ps1_features_module.py
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from pyspark.sql import functions as F
from pyspark.sql.window import Window
from pyspark.storagelevel import StorageLevel

TARGET_COL = "will_hardware_oos_3d"
SLA_TARGET_COL = "will_fail_3d"
LABEL_HORIZON_DAYS = 3
LABEL_DATA_LAG_DAYS = 0
KEY_COLS = ["DEVICE_ID", "transit_day"]
JOIN_KEY_DEVICE = "DEVICE_KEY"
PS5_DATE_COLUMN = None
AUXILIARY_DUPLICATE_POLICY = "skip"
RUN_DEEP_GRAIN_AUDIT = False
IS_LOCAL_SPARK = False
shuffle_partitions = 200


def _notebook_flag(name: str, default: bool = False) -> bool:
    return bool(globals().get(name, default))


def _notebook_val(name: str, default):
    return globals().get(name, default)

STAGE_TIMINGS_SEC: dict[str, float] = {}
SOURCE_COLUMN_MAPPINGS: dict[str, dict] = {}
SOURCE_SKIPS: dict[str, dict] = {}
SOURCE_DERIVATIONS: dict[str, str] = {}
SOURCE_GRAIN_AUDITS: dict[str, dict] = {}


@contextmanager
def timed_stage(name: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        STAGE_TIMINGS_SEC[name] = round(elapsed, 2)
        print(f"[{name}] {elapsed:,.1f} sec")


def _apply_s3a_hadoop_conf(_spark):
    """Override S3A duration defaults like '60s' that break Hadoop 3.3.x parsers."""
    _s3a_numeric = {
        "fs.s3a.connection.timeout": "200000",
        "fs.s3a.connection.establish.timeout": "30000",
        "fs.s3a.connection.request.timeout": "60000",
        "fs.s3a.connection.acquisition.timeout": "60000",
        "fs.s3a.connection.idle.time": "60000",
        "fs.s3a.connection.ttl": "300000",
        "fs.s3a.socket.timeout": "60000",
        "fs.s3a.threads.keepalivetime": "60",
        "fs.s3a.multipart.purge.age": "86400000",
    }
    for key, value in _s3a_numeric.items():
        try:
            _spark.conf.set(f"spark.hadoop.{key}", value)
        except Exception as exc:
            print(f"[S3A] spark.conf {key}: {type(exc).__name__}")
    try:
        _hconf = _spark.sparkContext._jsc.hadoopConfiguration()
        for key, value in _s3a_numeric.items():
            _hconf.set(key, value)
    except Exception as exc:
        print(f"[S3A] hadoopConfiguration override: {type(exc).__name__}")


@dataclass
class _FleetCfg:
    device_cat: str
    failure_features: list[str]
    ps1_tap_features: list[str]
    fleet_tap_features: list[str]
    gold_extra: list[str]
    incident_features: list[str]
    metric_features: list[str]
    chain_features: list[str]
    anomaly_features: list[str]
    lifetime_features: list[str]
    usage_features: list[str]
    mttr_features: list[str]
    usage_ext_features: list[str]
    spark_derived_features: list[str]
    oos_label_concurrent_features: list[str]
    all_candidate_features: list[str]
    required_ps1_features: list[str]
    tap_feature_cols: list[str]  # columns selected from df_read_tap join


def _resolve_end_day_expr(end_day: str | None, for_training: bool):
    if end_day is not None:
        return F.to_date(F.lit(end_day))
    return F.date_sub(
        F.current_date(),
        LABEL_HORIZON_DAYS + LABEL_DATA_LAG_DAYS,
    )


def _reset_source_audit():
    global SOURCE_COLUMN_MAPPINGS, SOURCE_SKIPS, SOURCE_DERIVATIONS, SOURCE_GRAIN_AUDITS
    SOURCE_COLUMN_MAPPINGS = {}
    SOURCE_SKIPS = {}
    SOURCE_DERIVATIONS = {}
    SOURCE_GRAIN_AUDITS = {}


def _fleet_key(fleet: str) -> str:
    key = fleet.strip().upper()
    if key not in FLEET_CONFIG:
        raise ValueError(f"Unknown fleet {fleet!r}; expected one of {sorted(FLEET_CONFIG)}")
    return key


def _paths(spark, fleet: str, s3_gold: str | None, s3_silver: str | None):
    if s3_gold and s3_silver:
        return s3_gold, s3_silver
    g = globals()
    sg = s3_gold or g.get("S3_GOLD_RUNTIME")
    ss = s3_silver or g.get("S3_SILVER_RUNTIME")
    if not sg or not ss:
        raise RuntimeError(
            f"[{fleet}] S3 paths required: pass s3_gold/s3_silver or define "
            "S3_GOLD_RUNTIME / S3_SILVER_RUNTIME in the notebook."
        )
    return sg, ss

'''


HELPERS = "# --- CELL 6 shared helpers (hoisted for CELL 7) ---\n\n"


def render_config(fleet_configs: dict[str, dict[str, list[str]]]) -> str:
    lines = ["FLEET_CONFIG: dict[str, _FleetCfg] = {}\n"]
    tap_col_names = {
        "GATE": "GATE_TAP_FEATURES",
        "TVM": "TVM_TAP_FEATURES",
        "VALIDATOR": "VALIDATOR_TAP_FEATURES",
    }
    gold_extra_names = {
        "GATE": "GATE_GOLD_EXTRA",
        "TVM": "TVM_GOLD_EXTRA",
        "VALIDATOR": "VALIDATOR_GOLD_EXTRA",
    }
    for fl, cfg in fleet_configs.items():
        tap_name = tap_col_names[fl]
        gold_name = gold_extra_names[fl]
        tap_feats = cfg.get(tap_name, [])
        gold_extra = cfg.get(gold_name, [])
        lines.append(f"FLEET_CONFIG[{fl!r}] = _FleetCfg(")
        lines.append(f"    device_cat={fl!r},")
        for field, key in [
            ("failure_features", "FAILURE_FEATURES"),
            ("ps1_tap_features", "PS1_TAP_FEATURES"),
            ("fleet_tap_features", tap_name),
            ("gold_extra", gold_name),
            ("incident_features", "INCIDENT_FEATURES"),
            ("metric_features", "METRIC_FEATURES"),
            ("chain_features", "CHAIN_FEATURES"),
            ("anomaly_features", "ANOMALY_FEATURES"),
            ("lifetime_features", "LIFETIME_FEATURES"),
            ("usage_features", "USAGE_FEATURES"),
            ("mttr_features", "MTTR_FEATURES"),
            ("usage_ext_features", "USAGE_EXT_FEATURES"),
            ("spark_derived_features", "SPARK_DERIVED_FEATURES"),
            ("oos_label_concurrent_features", "OOS_LABEL_CONCURRENT_FEATURES"),
            ("all_candidate_features", "ALL_CANDIDATE_FEATURES"),
            ("required_ps1_features", "REQUIRED_PS1_FEATURES"),
        ]:
            val = cfg.get(key, [])
            lines.append(f"    {field}={val!r},")
        lines.append(f"    tap_feature_cols={tap_feats!r},")
        lines.append(")\n")
    return "\n".join(lines) + "\n"


def split_cell6(cell_body: str) -> tuple[str, str]:
    marker = "PS1_PASS_THROUGH = [JOIN_KEY_DEVICE]"
    idx = cell_body.find(marker)
    if idx < 0:
        raise ValueError("PS1_PASS_THROUGH marker missing from CELL 6")
    helpers = cell_body[:idx]
    spine = cell_body[idx:]
    return helpers, spine


def render_module_helpers(cell6_body: str) -> str:
    helpers, _ = split_cell6(cell6_body)
    helpers = helpers.replace("DEVICE_CAT", "device_cat")
    # Drop per-call SOURCE_* resets; read_spine calls _reset_source_audit().
    helpers = re.sub(
        r"SOURCE_COLUMN_MAPPINGS = \{\}\nSOURCE_SKIPS = \{\}\nSOURCE_DERIVATIONS = \{\}\nSOURCE_GRAIN_AUDITS = \{\}\n\n",
        "",
        helpers,
    )
    helpers = re.sub(
        r"LABEL_CUTOFF_EXPR = F\.date_sub\([\s\S]*?\)\n\n",
        "",
        helpers,
        count=1,
    )
    helpers = helpers.replace(
        "def read_feature_source(\n",
        "def read_feature_source(\n    spark,\n    device_cat,\n",
    )
    return helpers + "\n"


def render_read_spine(cell_body: str) -> str:
    _, spine = split_cell6(cell_body)
    cell_body = spine
    cell_body = cell_body.replace("TRAIN_START", "start_day")
    cell_body = cell_body.replace("read_feature_source(", "read_feature_source(spark, cfg.device_cat, ")
    cell_body = cell_body.replace("DEVICE_CAT", "cfg.device_cat")
    cell_body = cell_body.replace("S3_GOLD_RUNTIME", "s3_gold")
    cell_body = cell_body.replace("S3_SILVER_RUNTIME", "s3_silver")
    cell_body = cell_body.replace(
        "df_ps1 = attach_hardware_oos_label(df_ps1, cfg.device_cat, LABEL_HORIZON_DAYS)\n"
        "df_ps1 = df_ps1.where(F.col(TARGET_COL).isin(0, 1))\n"
        "if SLA_TARGET_COL in df_ps1.columns:\n"
        "    _sla = df_ps1.agg(F.avg(SLA_TARGET_COL).alias(\"r\")).collect()[0][\"r\"]\n"
        "    _oos = df_ps1.agg(F.avg(TARGET_COL).alias(\"r\")).collect()[0][\"r\"]\n"
        "    print(f\"Label rates — {SLA_TARGET_COL}: {100 * float(_sla or 0):.4f}% | {TARGET_COL}: {100 * float(_oos or 0):.4f}%\")\n",
        "if with_label:\n"
        "    df_ps1 = attach_hardware_oos_label(df_ps1, cfg.device_cat, LABEL_HORIZON_DAYS)\n"
        "    df_ps1 = df_ps1.where(F.col(TARGET_COL).isin(0, 1))\n"
        "    if SLA_TARGET_COL in df_ps1.columns:\n"
        "        _sla = df_ps1.agg(F.avg(SLA_TARGET_COL).alias(\"r\")).collect()[0][\"r\"]\n"
        "        _oos = df_ps1.agg(F.avg(TARGET_COL).alias(\"r\")).collect()[0][\"r\"]\n"
        "        print(\n"
        "            f\"Label rates — {SLA_TARGET_COL}: {100 * float(_sla or 0):.4f}% | \"\n"
        "            f\"{TARGET_COL}: {100 * float(_oos or 0):.4f}%\"\n"
        "        )\n",
    )
    header = (
        "def read_spine(\n"
        "    spark,\n"
        "    fleet: str,\n"
        "    start_day: str,\n"
        "    end_day: str | None = None,\n"
        "    *,\n"
        "    with_label: bool = True,\n"
        "    s3_gold: str | None = None,\n"
        "    s3_silver: str | None = None,\n"
        "):\n"
        '    """CELL 6 — gold/silver spine read + optional hardware OOS label."""\n'
        "    _reset_source_audit()\n"
        "    cfg = FLEET_CONFIG[_fleet_key(fleet)]\n"
        "    s3_gold, s3_silver = _paths(spark, fleet, s3_gold, s3_silver)\n"
        "    end_day_expr = _resolve_end_day_expr(end_day, for_training=with_label)\n"
        "    DEVICE_CAT = cfg.device_cat\n"
        "    FAILURE_FEATURES = cfg.failure_features\n"
        "    PS1_TAP_FEATURES = cfg.ps1_tap_features\n"
        "    GATE_GOLD_EXTRA = cfg.gold_extra\n"
        "    INCIDENT_FEATURES = cfg.incident_features\n"
        "    USAGE_FEATURES = cfg.usage_features\n"
        "    REQUIRED_PS1_FEATURES = cfg.required_ps1_features\n"
        "\n"
    )
    return header + textwrap.indent(cell_body, "    ") + "\n    return df_ps1\n\n"


def render_add_auxiliary(prefix: str, tap_blocks: dict[str, str]) -> str:
    prefix = prefix.replace("TRAIN_START", "start_day")
    prefix = prefix.replace("LABEL_CUTOFF_EXPR", "end_day_expr")
    prefix = prefix.replace("DEVICE_CAT", "cfg.device_cat")
    prefix = prefix.replace("S3_GOLD_RUNTIME", "s3_gold")
    prefix = prefix.replace("S3_SILVER_RUNTIME", "s3_silver")
    prefix = prefix.replace("CHAIN_FEATURES", "cfg.chain_features")
    prefix = prefix.replace("ANOMALY_FEATURES", "cfg.anomaly_features")
    prefix = prefix.replace("LIFETIME_FEATURES", "cfg.lifetime_features")
    prefix = prefix.replace("read_feature_source(", "read_feature_source(spark, cfg.device_cat, ")
    prefix = prefix.replace("GATE_TAP_RAW", "TAP_RAW")

    tap_fn = "def _build_tap_features(spark, df_ps1, cfg, start_day, end_day_expr, s3_silver):\n"
    tap_parts = []
    for fl, block in tap_blocks.items():
        b = block.replace("TRAIN_START", "start_day")
        b = b.replace("LABEL_CUTOFF_EXPR", "end_day_expr")
        b = b.replace("DEVICE_CAT", "cfg.device_cat")
        b = b.replace("S3_SILVER_RUNTIME", "s3_silver")
        b = b.replace("GATE_TAP_FEATURES", "cfg.tap_feature_cols")
        b = b.replace("TVM_TAP_FEATURES", "cfg.tap_feature_cols")
        b = b.replace("VALIDATOR_TAP_FEATURES", "cfg.tap_feature_cols")
        b = b.replace("FEATURE_HISTORY_START", "start_day")
        b = b.replace("GATE_TAP_RAW", "TAP_RAW")
        tap_parts.append(
            f"    if cfg.device_cat == {fl!r}:\n"
            + textwrap.indent(b, "        ")
            + "\n        return df_read_tap\n"
        )
    tap_fn += "\n".join(tap_parts)
    tap_fn += '    raise RuntimeError(f"No tap builder for fleet {cfg.device_cat}")\n'

    head = (
        "def add_auxiliary(\n"
        "    spark,\n"
        "    df_ps1,\n"
        "    fleet: str,\n"
        "    start_day: str,\n"
        "    end_day: str | None = None,\n"
        "    *,\n"
        "    s3_gold: str | None = None,\n"
        "    s3_silver: str | None = None,\n"
        "):\n"
        '    """CELL 7 — auxiliary reads + fleet tap rolling features."""\n'
        "    cfg = FLEET_CONFIG[_fleet_key(fleet)]\n"
        "    s3_gold, s3_silver = _paths(spark, fleet, s3_gold, s3_silver)\n"
        "    end_day_expr = _resolve_end_day_expr(end_day, for_training=(end_day is None))\n"
        "    CHAIN_FEATURES = cfg.chain_features\n"
        "    ANOMALY_FEATURES = cfg.anomaly_features\n"
        "    LIFETIME_FEATURES = cfg.lifetime_features\n"
        "\n"
    )
    body = head + textwrap.indent(prefix, "    ") + "\n"
    body += textwrap.indent(tap_fn, "    ") + "\n"
    body += "    df_read_tap = _build_tap_features(spark, df_ps1, cfg, start_day, end_day_expr, s3_silver)\n\n"
    body += (
        "    return {\n"
        "        'df_ps1': df_ps1,\n"
        "        'df_ps2': df_ps2,\n"
        "        'df_ps4': df_ps4,\n"
        "        'df_ps5': df_ps5,\n"
        "        'df_metric': df_metric,\n"
        "        'df_mttr': df_mttr,\n"
        "        'df_usage_ext': df_usage_ext,\n"
        "        'df_read_tap': df_read_tap,\n"
        "    }\n\n"
    )
    return body


def render_join(cell8_bodies: dict[str, str]) -> str:
    head = (
        "def join_and_materialise(\n"
        "    spark,\n"
        "    aux: dict,\n"
        "    fleet: str,\n"
        "    *,\n"
        "    with_label: bool = True,\n"
        "    materialize: bool = True,\n"
        "):\n"
        '    """CELL 8 — joins, grain audit, feature materialization."""\n'
        "    cfg = FLEET_CONFIG[_fleet_key(fleet)]\n"
        "    df_ps1 = aux['df_ps1']\n"
        "    df_ps2 = aux['df_ps2']\n"
        "    df_ps4 = aux['df_ps4']\n"
        "    df_ps5 = aux['df_ps5']\n"
        "    df_metric = aux['df_metric']\n"
        "    df_mttr = aux['df_mttr']\n"
        "    df_usage_ext = aux['df_usage_ext']\n"
        "    df_read_tap = aux['df_read_tap']\n"
        "    FEATURE_COLS: list[str] = []\n"
        "    df_features = None\n"
        "\n"
    )
    branches = []
    for fl, raw in cell8_bodies.items():
        body = raw.replace("ALL_CANDIDATE_FEATURES", "cfg.all_candidate_features")
        body = body.replace("REQUIRED_PS1_FEATURES", "cfg.required_ps1_features")
        body = _patch_join_body(body)
        branches.append(
            f"    if cfg.device_cat == {fl!r}:\n"
            + textwrap.indent(body, "        ")
        )
    branches.append(
        '    else:\n'
        '        raise RuntimeError(f"No CELL 8 join path for fleet {cfg.device_cat}")\n'
    )
    tail = "\n    if df_features is None:\n        raise RuntimeError(\"join_and_materialise did not build df_features\")\n    return df_features, FEATURE_COLS\n\n"
    return head + "\n".join(branches) + tail


def _patch_join_body(body: str) -> str:
    body = _wrap_materialize_tail(body)
    body = body.replace(
        "df_features = df_joined.select(\n"
        "    \"DEVICE_ID\",\n"
        "    *_MLFLOW_ID_COLS,\n"
        "    \"transit_day\",\n"
        "    F.col(TARGET_COL).cast(\"byte\").alias(TARGET_COL),\n"
        "    *[F.col(c).cast(\"float\").alias(c) for c in FEATURE_COLS],\n"
        ")\n",
        "_select_cols = [\"DEVICE_ID\", *_MLFLOW_ID_COLS, \"transit_day\"]\n"
        "if with_label and TARGET_COL in df_joined.columns:\n"
        "    _select_cols.append(F.col(TARGET_COL).cast(\"byte\").alias(TARGET_COL))\n"
        "df_features = df_joined.select(\n"
        "    *_select_cols,\n"
        "    *[F.col(c).cast(\"float\").alias(c) for c in FEATURE_COLS],\n"
        ")\n",
    )
    body = body.replace(
        "if CACHE_FEATURE_FRAME:\n",
        "if materialize and _notebook_flag(\"CACHE_FEATURE_FRAME\"):\n",
    )
    body = body.replace(
        "if IS_LOCAL_SPARK:\n",
        "if _notebook_flag(\"IS_LOCAL_SPARK\"):\n",
    )
    body = body.replace(
        "if RUN_DEEP_GRAIN_AUDIT:\n",
        "if _notebook_flag(\"RUN_DEEP_GRAIN_AUDIT\"):\n",
    )
    body = body.replace(
        "repartition(shuffle_partitions,",
        "repartition(_notebook_val(\"shuffle_partitions\", shuffle_partitions),",
    )
    body = body.replace(
        "if not audit[\"positive_count\"]:\n"
        "    raise RuntimeError(\"No positive labels remain after filters.\")\n",
        "if with_label and materialize and not audit.get(\"positive_count\"):\n"
        "    raise RuntimeError(\"No positive labels remain after filters.\")\n",
    )
    return body


def _wrap_materialize_tail(body: str) -> str:
    start_key = 'with timed_stage("spark_spine_count"):'
    end_key = 'print(f"Positive rate:'
    start = body.find(start_key)
    end = body.find(end_key)
    if start < 0 or end < 0:
        return body
    end = body.find("\n", end) + 1
    block = body[start:end]
    base = body[:start]
    base_indent = ""
    if start > 0:
        line_start = body.rfind("\n", 0, start) + 1
        base_indent = body[line_start:start]
    wrapped = f"{base_indent}if materialize:\n" + textwrap.indent(block, "    ")
    return base + wrapped + body[end:]


def _wrap_materialize_stage(body: str, stage_name: str) -> str:
    lines = body.splitlines()
    out: list[str] = []
    i = 0
    marker = f'with timed_stage("{stage_name}"):'
    while i < len(lines):
        if lines[i].strip() == marker:
            base_indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            out.append(f"{base_indent}if materialize:")
            out.append(f"{base_indent}    {marker}")
            i += 1
            while i < len(lines):
                if lines[i].strip() == "":
                    out.append(lines[i])
                    i += 1
                    continue
                cur_indent = len(lines[i]) - len(lines[i].lstrip())
                if cur_indent <= len(base_indent) and lines[i].strip():
                    break
                out.append(f"    {lines[i]}")
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


if __name__ == "__main__":
    main()
