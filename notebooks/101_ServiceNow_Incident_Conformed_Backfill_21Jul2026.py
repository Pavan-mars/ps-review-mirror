# Databricks notebook source
# MAGIC %md
# MAGIC # 101 – ServiceNow Incident Conformed Backfill  ·  21 Jul 2026
# MAGIC
# MAGIC **Purpose:** create `mars_dev.silver.servicenow_incident_conformed` — an
# MAGIC independent Delta table containing every row from `bronze.servicenow_incident`
# MAGIC plus the ~20 K CTA incidents whose normalised incident `number` is absent
# MAGIC from the baseline.
# MAGIC
# MAGIC The enriched table is the intended source for downstream Silver rebuilds
# MAGIC (**S15** `incident_history`, **S17** `incident_root_cause`) and for PS1
# MAGIC Gate / TVM 3-day failure-prediction feature engineering in SageMaker.
# MAGIC
# MAGIC This notebook **deliberately never writes to either Bronze source table**.
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC ## Safe operating sequence
# MAGIC
# MAGIC | Step | `run_mode`   | What happens |
# MAGIC |------|-------------|--------------|
# MAGIC | 1    | `validate`  | All gates evaluated; nothing written. Fix any failures here. |
# MAGIC | 2    | `initialize`| DEEP CLONE of baseline at pinned Delta version. No CTA rows inserted yet. |
# MAGIC | 3    | `merge`     | Insert-only Delta MERGE. Idempotent — rerun is a safe no-op. |
# MAGIC
# MAGIC For either write mode type the complete target table name into the
# MAGIC `confirmation` widget.  The target defaults to
# MAGIC `mars_dev.silver.servicenow_incident_conformed`.
# MAGIC
# MAGIC Key safeguards: deterministic CTA deduplication · rejection of missing keys ·
# MAGIC pinned source versions · exact schema checks · TRY_CAST conversion-loss gate ·
# MAGIC target-lineage verification · strict count reconciliation · full baseline
# MAGIC row-level proof.

# COMMAND ----------

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

dbutils: Any = globals().get('dbutils')
spark: Any   = globals().get('spark')

if dbutils is None or spark is None:
    raise RuntimeError('This notebook must run on Databricks with spark and dbutils available.')


def _ensure_text_widget(name: str, default: str) -> None:
    try:
        dbutils.widgets.get(name)
    except Exception:
        dbutils.widgets.text(name, default)


def _ensure_dropdown(name: str, default: str, choices: Sequence[str]) -> None:
    try:
        dbutils.widgets.get(name)
    except Exception:
        dbutils.widgets.dropdown(name, default, list(choices))


_ensure_dropdown('run_mode',           'validate', ['validate', 'initialize', 'merge'])
_ensure_text_widget('catalog',         'mars_dev')
_ensure_text_widget('source_schema',   'bronze')
_ensure_text_widget('cta_table',       'cta_servicenow_incident')
_ensure_text_widget('base_table',      'servicenow_incident')
_ensure_text_widget('target_schema',   'bronze')
_ensure_text_widget('target_table',    'servicenow_incident_conformed')
_ensure_text_widget('dedupe_order_column', 'sys_updated_on')
_ensure_text_widget('expected_min_new', '20000')
_ensure_text_widget('expected_max_new', '22000')
_ensure_text_widget('max_invalid_source_keys', '0')
_ensure_dropdown('fail_on_cast_loss',  'true',  ['true', 'false'])
_ensure_dropdown('full_reconciliation','true',  ['true', 'false'])
_ensure_text_widget('confirmation',    '')

RUN_MODE               = dbutils.widgets.get('run_mode').strip().lower()
CATALOG                = dbutils.widgets.get('catalog').strip()
SOURCE_SCHEMA          = dbutils.widgets.get('source_schema').strip()
CTA_TABLE_NAME         = dbutils.widgets.get('cta_table').strip()
BASE_TABLE_NAME        = dbutils.widgets.get('base_table').strip()
TARGET_SCHEMA          = dbutils.widgets.get('target_schema').strip()
TARGET_TABLE_NAME      = dbutils.widgets.get('target_table').strip()
DEDUPE_ORDER_COLUMN    = dbutils.widgets.get('dedupe_order_column').strip()
EXPECTED_MIN_NEW       = int(dbutils.widgets.get('expected_min_new'))
EXPECTED_MAX_NEW       = int(dbutils.widgets.get('expected_max_new'))
MAX_INVALID_SOURCE_KEYS= int(dbutils.widgets.get('max_invalid_source_keys'))
FAIL_ON_CAST_LOSS      = dbutils.widgets.get('fail_on_cast_loss').lower() == 'true'
FULL_RECONCILIATION    = dbutils.widgets.get('full_reconciliation').lower() == 'true'
CONFIRMATION           = dbutils.widgets.get('confirmation').strip()

if RUN_MODE not in {'validate', 'initialize', 'merge'}:
    raise ValueError(f'Unsupported run_mode: {RUN_MODE!r}')
if EXPECTED_MIN_NEW < 0 or EXPECTED_MAX_NEW < EXPECTED_MIN_NEW:
    raise ValueError('Expected candidate bounds are invalid.')
if MAX_INVALID_SOURCE_KEYS < 0:
    raise ValueError('max_invalid_source_keys cannot be negative.')

# Guard against SQL-injection through widget values.
_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _validated_identifier(value: str, label: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(
            f'{label}={value!r} is not a safe SQL identifier. '
            'Use letters, digits, and underscores, starting with a letter or underscore.'
        )
    return value


for _value, _label in [
    (CATALOG,          'catalog'),
    (SOURCE_SCHEMA,    'source_schema'),
    (CTA_TABLE_NAME,   'cta_table'),
    (BASE_TABLE_NAME,  'base_table'),
    (TARGET_SCHEMA,    'target_schema'),
    (TARGET_TABLE_NAME,'target_table'),
]:
    _validated_identifier(_value, _label)


def _fqn(catalog: str, schema: str, table: str) -> str:
    return f'{catalog}.{schema}.{table}'


def _qident(identifier: str) -> str:
    """Back-tick-quote a single SQL identifier."""
    return f'`{identifier.replace("`", "``")}`'


def _qtable(table_name: str) -> str:
    """Back-tick-quote a dot-delimited three-part table name."""
    return '.'.join(_qident(part) for part in table_name.split('.'))


def _sql_literal(value: object) -> str:
    """Single-quote a scalar value for use in a SQL string."""
    return "'" + str(value).replace("'", "''") + "'"


CTA_TABLE    = _fqn(CATALOG, SOURCE_SCHEMA, CTA_TABLE_NAME)
BASE_TABLE   = _fqn(CATALOG, SOURCE_SCHEMA, BASE_TABLE_NAME)
TARGET_TABLE = _fqn(CATALOG, TARGET_SCHEMA, TARGET_TABLE_NAME)

if len({CTA_TABLE, BASE_TABLE, TARGET_TABLE}) != 3:
    raise RuntimeError('CTA, baseline, and target must be three different tables.')

# Make all string-to-timestamp parsing deterministic across clusters.
spark.conf.set('spark.sql.session.timeZone', 'UTC')

print('Configuration')
print(f'  run_mode            : {RUN_MODE}')
print(f'  CTA source          : {CTA_TABLE}')
print(f'  baseline            : {BASE_TABLE}')
print(f'  new target          : {TARGET_TABLE}')
print(f'  dedupe order        : {DEDUPE_ORDER_COLUMN}')
print(f'  expected new rows   : {EXPECTED_MIN_NEW:,} .. {EXPECTED_MAX_NEW:,}')
print(f'  max invalid keys    : {MAX_INVALID_SOURCE_KEYS:,}')
print(f'  fail on cast loss   : {FAIL_ON_CAST_LOSS}')
print(f'  full reconciliation : {FULL_RECONCILIATION}')
print('  session timezone    : UTC')


# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Preflight and reproducible snapshots
# MAGIC
# MAGIC Both input tables are pinned to Delta versions before any computation
# MAGIC begins. In `merge` mode the notebook reads the exact versions recorded when
# MAGIC the target was initialised, even if a Bronze source has since changed.

# COMMAND ----------

from pyspark.sql import DataFrame, Window, functions as F
from pyspark.sql.types import (
    BooleanType,
    ByteType,
    DataType,
    DateType,
    DoubleType,
    FloatType,
    IntegerType,
    LongType,
    ShortType,
    StringType,
    TimestampType,
)


def _require_schema(catalog: str, schema: str) -> None:
    try:
        spark.sql(f'DESCRIBE SCHEMA {_qident(catalog)}.{_qident(schema)}').collect()
    except Exception as exc:
        raise RuntimeError(
            f'Required schema does not exist or is not accessible: {catalog}.{schema}'
        ) from exc


def _require_table(table_name: str) -> None:
    if not spark.catalog.tableExists(table_name):
        raise RuntimeError(f'Required table does not exist: {table_name}')


def _table_detail(table_name: str) -> Dict[str, Any]:
    row = spark.sql(f'DESCRIBE DETAIL {_qtable(table_name)}').first()
    if row is None:
        raise RuntimeError(f'DESCRIBE DETAIL returned no result for {table_name}')
    return row.asDict(recursive=True)


def _require_delta(table_name: str) -> Dict[str, Any]:
    detail = _table_detail(table_name)
    if str(detail.get('format', '')).lower() != 'delta':
        raise RuntimeError(
            f'{table_name} must be a Delta table; found {detail.get("format")!r}.'
        )
    return detail


def _latest_delta_version(table_name: str) -> int:
    row = (
        spark.sql(f'DESCRIBE HISTORY {_qtable(table_name)} LIMIT 1')
        .select('version')
        .first()
    )
    if row is None:
        raise RuntimeError(f'No Delta history is available for {table_name}')
    return int(row['version'])


def _read_version(table_name: str, version: int) -> DataFrame:
    return spark.read.option('versionAsOf', str(version)).table(table_name)


def _table_properties(table_name: str) -> Dict[str, str]:
    return {
        str(row['key']): str(row['value'])
        for row in spark.sql(f'SHOW TBLPROPERTIES {_qtable(table_name)}').collect()
    }


_require_schema(CATALOG, SOURCE_SCHEMA)
_require_schema(CATALOG, TARGET_SCHEMA)
_require_table(CTA_TABLE)
_require_table(BASE_TABLE)
_require_delta(CTA_TABLE)
_require_delta(BASE_TABLE)

# Confirm TRY_CAST is available on this runtime before building 321 expressions.
# Fails cleanly on old Databricks runtimes that do not support it.
try:
    spark.sql("SELECT try_cast('1' AS BIGINT) AS value").collect()
except Exception as exc:
    raise RuntimeError(
        'TRY_CAST is not supported on this cluster runtime. '
        'Use Databricks Runtime 10.4 LTS or later.'
    ) from exc

target_exists_at_start = spark.catalog.tableExists(TARGET_TABLE)
target_properties: Dict[str, str] = {}

if target_exists_at_start:
    _require_delta(TARGET_TABLE)
    target_properties = _table_properties(TARGET_TABLE)

if RUN_MODE == 'initialize' and target_exists_at_start:
    raise RuntimeError(
        f'Initialization stopped: {TARGET_TABLE} already exists. '
        'This notebook never replaces or drops a target. '
        'Use merge mode for a valid initialized target, or choose a new target name.'
    )

if RUN_MODE == 'merge' and not target_exists_at_start:
    raise RuntimeError(f'Merge stopped: initialize {TARGET_TABLE} first.')

latest_cta_version  = _latest_delta_version(CTA_TABLE)
latest_base_version = _latest_delta_version(BASE_TABLE)

if RUN_MODE == 'merge':
    # In merge mode, pin to the versions stamped at initialize-time so the merge
    # always sees exactly the same inputs as the original validation run.
    required_lineage = {
        'servicenow.backfill.cta_table':  CTA_TABLE,
        'servicenow.backfill.base_table': BASE_TABLE,
    }
    for key, expected_value in required_lineage.items():
        actual_value = target_properties.get(key)
        if actual_value != expected_value:
            raise RuntimeError(
                f'Target-lineage check failed for {key}: '
                f'expected {expected_value!r}, found {actual_value!r}. '
                'Refusing to merge into an unverified target.'
            )
    try:
        CTA_VERSION  = int(target_properties['servicenow.backfill.cta_version'])
        BASE_VERSION = int(target_properties['servicenow.backfill.base_version'])
    except (KeyError, ValueError) as exc:
        raise RuntimeError(
            'Target is missing valid pinned source-version properties.'
        ) from exc
else:
    CTA_VERSION  = latest_cta_version
    BASE_VERSION = latest_base_version

try:
    cta  = _read_version(CTA_TABLE,  CTA_VERSION)
    base = _read_version(BASE_TABLE, BASE_VERSION)
except Exception as exc:
    raise RuntimeError(
        'A pinned Delta version could not be read. It may no longer be retained; '
        'initialize a new target from currently available versions.'
    ) from exc

n_cta  = cta.count()
n_base = base.count()
if n_cta == 0:
    raise RuntimeError(f'CTA source is empty at version {CTA_VERSION}.')
if n_base == 0:
    raise RuntimeError(f'Baseline is empty at version {BASE_VERSION}.')

for required_column in ('number',):
    if required_column not in cta.columns:
        raise RuntimeError(
            f'{CTA_TABLE} is missing required key column {required_column!r}.'
        )
    if required_column not in base.columns:
        raise RuntimeError(
            f'{BASE_TABLE} is missing required key column {required_column!r}.'
        )

print('Pinned input snapshots')
print(f'  CTA      : version {CTA_VERSION:,} | {n_cta:,} rows | {len(cta.columns):,} columns')
print(f'  baseline : version {BASE_VERSION:,} | {n_base:,} rows | {len(base.columns):,} columns')
if RUN_MODE == 'merge':
    print(f'  latest versions now : CTA={latest_cta_version:,}, baseline={latest_base_version:,}')
    print('  merge intentionally uses the versions recorded on the initialized target')
print(f'  target currently exists: {target_exists_at_start}')


# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Normalise keys and deterministically deduplicate CTA
# MAGIC
# MAGIC `number` is the business key, normalised by trimming and upper-casing.
# MAGIC Null and blank keys are never inserted.  Where CTA has multiple rows for the
# MAGIC same normalised number, the row with the latest `sys_updated_on` timestamp
# MAGIC wins; a deterministic SHA-256 row fingerprint breaks exact-timestamp ties so
# MAGIC the result is always the same across reruns.

# COMMAND ----------

KEY_COLUMN = 'number'


def _column(name: str):
    """Reference a DataFrame column by back-tick-quoted name."""
    return F.col(_qident(name))


def _normalized_number(name: str = KEY_COLUMN):
    """Return TRIM(UPPER(number)) or NULL when the source value is blank/null."""
    raw       = _column(name).cast('string')
    trimmed   = F.trim(raw)
    normalized = F.upper(trimmed)
    return F.when(
        raw.isNull() | (F.length(trimmed) == 0),
        F.lit(None).cast('string'),
    ).otherwise(normalized)


def _is_present(name: str):
    """True when the column is not null and not blank after trimming."""
    value = _column(name)
    return value.isNotNull() & (F.length(F.trim(value.cast('string'))) > 0)


# ── Identify invalid / duplicate CTA keys ──────────────────────────────────────
cta_norm  = cta.withColumn('_number_norm', _normalized_number())

cta_stats = cta_norm.agg(
    F.count(F.lit(1)).alias('rows'),
    F.sum(F.when(F.col('_number_norm').isNull(), 1).otherwise(0)).alias('invalid_keys'),
    F.countDistinct('_number_norm').alias('distinct_valid_keys'),
).first().asDict()

invalid_source_key_count = int(cta_stats['invalid_keys'] or 0)
cta_valid = cta_norm.filter(F.col('_number_norm').isNotNull()).cache()

duplicate_key_counts = (
    cta_valid.groupBy('_number_norm')
    .agg(F.count(F.lit(1)).alias('row_count'))
    .filter(F.col('row_count') > 1)
    .cache()
)
duplicate_source_key_count = duplicate_key_counts.count()
duplicate_excess_row_count = int(
    duplicate_key_counts
    .agg(F.coalesce(F.sum(F.col('row_count') - 1), F.lit(0)).alias('n'))
    .first()['n']
)

print('CTA key profile')
print(f'  raw rows                  : {n_cta:,}')
print(f'  invalid null/blank keys   : {invalid_source_key_count:,}  (excluded)')
print(f'  distinct valid keys       : {int(cta_stats["distinct_valid_keys"]):,}')
print(f'  duplicated business keys  : {duplicate_source_key_count:,}')
print(f'  excess duplicate rows     : {duplicate_excess_row_count:,}')

if invalid_source_key_count:
    print('Sample CTA records excluded because number is null or blank:')
    _audit_cols = [
        c for c in ['number', 'sys_id', 'opened_at', 'sys_created_on', 'sys_updated_on']
        if c in cta.columns
    ]
    cta_norm.filter(F.col('_number_norm').isNull()).select(*_audit_cols).show(10, truncate=60)

# Initialise deduplication counters (used in write gates regardless of branch).
dedupe_order_parse_failures   = 0
duplicate_keys_without_order  = 0

if duplicate_source_key_count:
    if DEDUPE_ORDER_COLUMN not in cta.columns:
        raise RuntimeError(
            f'CTA has duplicate keys but dedupe_order_column={DEDUPE_ORDER_COLUMN!r} '
            'does not exist. Choose a trusted last-update timestamp column and rerun.'
        )

    order_timestamp = F.expr(f'try_cast({_qident(DEDUPE_ORDER_COLUMN)} AS TIMESTAMP)')

    # Count populated dedupe timestamps that fail to parse (malformed strings).
    duplicate_source_rows = cta_valid.join(
        duplicate_key_counts.select('_number_norm'),
        on='_number_norm',
        how='inner',
    )
    dedupe_order_parse_failures = duplicate_source_rows.filter(
        _is_present(DEDUPE_ORDER_COLUMN) & order_timestamp.isNull()
    ).count()

    # Count duplicate groups where NO row has a usable ordering timestamp.
    duplicate_keys_without_order = (
        duplicate_source_rows
        .withColumn('_dedupe_order_ts', order_timestamp)
        .groupBy('_number_norm')
        .agg(F.count(F.lit(1)).alias('row_count'), F.max('_dedupe_order_ts').alias('latest_order'))
        .filter(F.col('latest_order').isNull())
        .count()
    )

    # SHA-256 fingerprint: sorts columns alphabetically so the fingerprint is
    # stable across schema evolution that only adds new columns at the end.
    row_fingerprint = F.sha2(
        F.to_json(
            F.struct(*[_column(name).alias(name) for name in sorted(cta.columns)]),
            options={'ignoreNullFields': 'false'},
        ),
        256,
    )
    dedupe_window = Window.partitionBy('_number_norm').orderBy(
        order_timestamp.desc_nulls_last(),
        row_fingerprint.desc(),        # deterministic tie-break
    )
    cta_one_per_number = (
        cta_valid
        .withColumn('_row_fingerprint', row_fingerprint)
        .withColumn('_dedupe_rank', F.row_number().over(dedupe_window))
        .filter(F.col('_dedupe_rank') == 1)
        .drop('_row_fingerprint', '_dedupe_rank')
    )
else:
    cta_one_per_number = cta_valid

if dedupe_order_parse_failures:
    print(f'WARNING: {dedupe_order_parse_failures:,} populated dedupe timestamps could not be parsed.')
if duplicate_keys_without_order:
    print(f'WARNING: {duplicate_keys_without_order:,} duplicate keys have no usable ordering timestamp.')

# ── Baseline key profile ───────────────────────────────────────────────────────
base_norm = base.withColumn('_number_norm', _normalized_number())
base_key_stats = base_norm.agg(
    F.sum(F.when(F.col('_number_norm').isNull(), 1).otherwise(0)).alias('invalid_keys'),
    F.countDistinct('_number_norm').alias('distinct_valid_keys'),
).first().asDict()
invalid_base_key_count = int(base_key_stats['invalid_keys'] or 0)

base_keys = (
    base_norm.select('_number_norm')
    .filter(F.col('_number_norm').isNotNull())
    .distinct()
    .cache()
)
base_duplicate_keys = (
    base_norm.filter(F.col('_number_norm').isNotNull())
    .groupBy('_number_norm')
    .agg(F.count(F.lit(1)).alias('row_count'))
    .filter(F.col('row_count') > 1)
    .cache()
)
base_duplicate_key_count = base_duplicate_keys.count()

# ── Anti-join: CTA rows absent from baseline ───────────────────────────────────
candidates_norm = (
    cta_one_per_number
    .join(base_keys, on='_number_norm', how='left_anti')
    .cache()
)

candidate_count           = candidates_norm.count()
candidate_distinct_key_count = candidates_norm.select('_number_norm').distinct().count()
candidate_keys_are_unique = (candidate_count == candidate_distinct_key_count)
candidate_count_in_range  = (EXPECTED_MIN_NEW <= candidate_count <= EXPECTED_MAX_NEW)

print('Baseline and candidate profile')
print(f'  baseline null/blank keys       : {invalid_base_key_count:,}  (preserved as baseline data)')
print(f'  baseline duplicated keys       : {base_duplicate_key_count:,}  (preserved; reported separately)')
print(f'  CTA candidates not in baseline : {candidate_count:,}')
print(f'  distinct candidate keys        : {candidate_distinct_key_count:,}')
print(f'  candidate keys are unique      : {candidate_keys_are_unique}')
print(f'  within expected range          : {candidate_count_in_range}')

print('Candidate distribution by opened year')
if 'opened_at' in candidates_norm.columns:
    (
        candidates_norm
        .withColumn(
            'opened_year',
            F.year(F.expr(f'try_cast({_qident("opened_at")} AS TIMESTAMP)')),
        )
        .groupBy('opened_year')
        .count()
        .orderBy('opened_year')
        .show(50, truncate=False)
    )

print('Top candidate categories')
if 'category' in candidates_norm.columns:
    (
        candidates_norm
        .groupBy('category')
        .count()
        .orderBy(F.desc('count'))
        .show(20, truncate=False)
    )


# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Build the 44-to-baseline schema bridge
# MAGIC
# MAGIC The baseline schema is authoritative: every output column is cast to the
# MAGIC exact target type in the exact column order. Key design decisions:
# MAGIC
# MAGIC - **`TRY_CAST`** prevents malformed CTA values from crashing halfway through
# MAGIC   a write; the conversion-loss gate then catches the resulting NULLs.
# MAGIC - **Label → code** conversion uses a numeric-prefix extraction first, then
# MAGIC   falls back to an explicit ServiceNow label map so that bare labels such as
# MAGIC   `"Resolved"` (no leading digit) map to the correct code. This matters
# MAGIC   directly for PS1: `incident_state` and `state` feed failure labelling.
# MAGIC - **Reference field routing**: values matching a 32-char hex ServiceNow sys_id
# MAGIC   are routed to the `_sys_id` column; all others go to `_display_value`.
# MAGIC - **Duration columns** (`calendar_duration`, `u_estimated_time`) are NOT
# MAGIC   reduced to a leading digit; they must already be numeric when the baseline
# MAGIC   type is numeric — the conversion-loss gate enforces this explicitly.

# COMMAND ----------

# ── Reference fields: CTA has a single column; baseline splits into _sys_id
# and _display_value. The routing logic below decides which sub-column receives
# the CTA value based on whether it looks like a ServiceNow sys_id (32 hex chars).
REF_FIELDS: frozenset = frozenset({
    'assigned_to',
    'assignment_group',
    'caller_id',
    'caused_by',
    'closed_by',
    'cmdb_ci',
    'location',
    'problem_id',
    'resolved_by',
    'rfc',
    'task_for',
    'u_chargeable_level',
    'u_event_code',
})

# Explicit ServiceNow label → code maps.
# Logic: extract leading digit first; if blank, lowercase the label and look it
# up here. Returns NULL (caught by conversion-loss gate) when neither applies.
LABEL_CODE_MAPS: Mapping[str, Mapping[str, int]] = {
    'incident_state': {
        'new':         1,
        'in progress': 2,
        'on hold':     3,
        'resolved':    6,
        'closed':      7,
        'canceled':    8,
        'cancelled':   8,
    },
    'state': {
        'new':         1,
        'in progress': 2,
        'on hold':     3,
        'resolved':    6,
        'closed':      7,
        'canceled':    8,
        'cancelled':   8,
    },
    'priority': {
        'critical':  1,
        'high':      2,
        'moderate':  3,
        'medium':    3,
        'low':       4,
        'planning':  5,
    },
    'severity': {
        'high':     1,
        'medium':   2,
        'moderate': 2,
        'low':      3,
    },
}

# Columns whose CTA string value should be cast to a numeric type without
# label-map lookup (they should already contain a numeric string).
STRING_NUMERIC_COLUMNS: frozenset = frozenset({'child_incidents', 'reopen_count'})

# Duration columns: TRY_CAST to the baseline type; conversion-loss gate fires
# if the value is not already numeric (no leading-digit extraction here).
DURATION_COLUMNS: frozenset = frozenset({'calendar_duration', 'u_estimated_time'})

INTEGRAL_TYPES = (LongType, IntegerType, ShortType, ByteType)

source_fields    = {field.name: field.dataType for field in cta.schema.fields}
baseline_fields  = list(base.schema.fields)
baseline_field_names = {field.name for field in baseline_fields}


def _try_cast_exact(source_name: str, target_type: DataType):
    """Cast source column to target type; return as-is when types already match."""
    source_type = source_fields[source_name]
    if source_type == target_type:
        return _column(source_name)
    if isinstance(target_type, StringType):
        return _column(source_name).cast('string')
    return F.expr(f'try_cast({_qident(source_name)} AS {target_type.simpleString()})')


def _literal_map(mapping: Mapping[str, int]):
    """Build a Spark MapType literal from a Python dict."""
    entries: List[Any] = []
    for key, value in mapping.items():
        entries.extend([F.lit(key), F.lit(int(value))])
    return F.create_map(*entries)


def _label_code(source_name: str, target_type: DataType):
    """
    Convert a ServiceNow text label to its numeric code.

    Priority:
      1. Extract leading digit group  — "2 - High" → 2,  "2" → 2
      2. Strip numeric prefix then lookup in LABEL_CODE_MAPS
         — "High" → 2,  "high" → 2
      3. If neither matches, return NULL (conversion-loss gate fires).
    """
    raw = F.trim(_column(source_name).cast('string'))
    numeric_prefix  = F.regexp_extract(raw, r'^(\d+)', 1)
    # After stripping the optional numeric prefix and surrounding punctuation,
    # lower-case the remaining label for case-insensitive lookup.
    normalized_label = F.lower(
        F.trim(F.regexp_replace(raw, r'^\d+\s*[-:]?\s*', ''))
    )
    lookup = _literal_map(LABEL_CODE_MAPS[source_name])
    numeric_value = (
        F.when(numeric_prefix != '', numeric_prefix.cast('long'))
        .otherwise(F.element_at(lookup, normalized_label))
        # element_at returns NULL when the key is not in the map — conversion-loss
        # gate will report those values so a business decision can be made.
    )
    return numeric_value.cast(target_type)


def _looks_like_servicenow_sys_id(source_name: str):
    """True when the trimmed value is exactly 32 lowercase hex characters."""
    value = F.trim(_column(source_name).cast('string'))
    return value.rlike(r'(?i)^[0-9a-f]{32}$')


# ── Build 321 SELECT expressions, one per baseline column ──────────────────────
select_expressions: List[Any] = []
cast_checks: List[Tuple[str, str, Any]] = []   # (dest_col, src_col, expression)
bridge_report: Dict[str, List[str]] = {
    'direct':            [],
    'converted':         [],
    'reference_display': [],
    'reference_sys_id':  [],
    'baseline_only_null':[],
}

for field in baseline_fields:
    destination_name = field.name
    destination_type = field.dataType
    raw_expression   = None
    source_name: Optional[str] = None
    potential_conversion_loss  = False

    if destination_name in source_fields and destination_name not in REF_FIELDS:
        # ── Direct or converted column ─────────────────────────────────────────
        source_name = destination_name

        if destination_name in LABEL_CODE_MAPS and isinstance(destination_type, INTEGRAL_TYPES):
            raw_expression            = _label_code(source_name, destination_type)
            potential_conversion_loss = True
            bridge_report['converted'].append(
                f'{source_name} [label -> {destination_type.simpleString()}]'
            )
        else:
            raw_expression = _try_cast_exact(source_name, destination_type)
            potential_conversion_loss = (
                source_fields[source_name] != destination_type
                and not isinstance(destination_type, StringType)
            )
            if (
                destination_name in STRING_NUMERIC_COLUMNS
                or destination_name in DURATION_COLUMNS
                or potential_conversion_loss
            ):
                bridge_report['converted'].append(
                    f'{source_name}'
                    f' [{source_fields[source_name].simpleString()}'
                    f' -> {destination_type.simpleString()}]'
                )
            else:
                bridge_report['direct'].append(destination_name)

    elif destination_name.endswith('_display_value'):
        # ── Reference field: route non-sys_id values to _display_value ────────
        reference_name = destination_name[: -len('_display_value')]
        if reference_name in REF_FIELDS and reference_name in source_fields:
            source_name        = reference_name
            display_expression = _try_cast_exact(reference_name, destination_type)
            raw_expression = F.when(
                _looks_like_servicenow_sys_id(reference_name),
                F.lit(None).cast(destination_type),   # sys_id → goes to _sys_id column
            ).otherwise(display_expression)
            potential_conversion_loss = not isinstance(destination_type, StringType)
            bridge_report['reference_display'].append(
                f'{reference_name} -> {destination_name}'
            )
        else:
            raw_expression = F.lit(None).cast(destination_type)
            bridge_report['baseline_only_null'].append(destination_name)

    elif destination_name.endswith('_sys_id'):
        # ── Reference field: route sys_id values to _sys_id ───────────────────
        reference_name = destination_name[: -len('_sys_id')]
        if reference_name in REF_FIELDS and reference_name in source_fields:
            source_name       = reference_name
            sys_id_expression = _try_cast_exact(reference_name, destination_type)
            raw_expression = F.when(
                _looks_like_servicenow_sys_id(reference_name),
                sys_id_expression,
            ).otherwise(F.lit(None).cast(destination_type))
            bridge_report['reference_sys_id'].append(
                f'{reference_name} -> {destination_name} when value is a sys_id'
            )
        else:
            raw_expression = F.lit(None).cast(destination_type)
            bridge_report['baseline_only_null'].append(destination_name)

    else:
        # ── XML-only baseline column: no equivalent in CTA ────────────────────
        raw_expression = F.lit(None).cast(destination_type)
        bridge_report['baseline_only_null'].append(destination_name)

    if raw_expression is None:
        raise RuntimeError(f'No bridge expression was built for column {destination_name!r}.')

    select_expressions.append(raw_expression.alias(destination_name))

    if source_name is not None and potential_conversion_loss:
        cast_checks.append((destination_name, source_name, raw_expression))

# Apply bridge and verify schema + row count immediately.
bridged       = candidates_norm.select(*select_expressions).cache()
bridged_count = bridged.count()

expected_schema_signature = [
    (field.name, field.dataType.simpleString()) for field in baseline_fields
]
actual_schema_signature = [
    (field.name, field.dataType.simpleString()) for field in bridged.schema.fields
]
schema_exact_match = (actual_schema_signature == expected_schema_signature)

if not schema_exact_match:
    mismatches = []
    for idx, (exp, act) in enumerate(zip(expected_schema_signature, actual_schema_signature)):
        if exp != act:
            mismatches.append((idx, exp, act))
    if len(expected_schema_signature) != len(actual_schema_signature):
        mismatches.append(('column_count', len(expected_schema_signature), len(actual_schema_signature)))
    raise RuntimeError(
        f'Bridged schema does not exactly match the baseline schema: {mismatches[:20]}'
    )

if bridged_count != candidate_count:
    raise RuntimeError(
        f'Schema bridge changed row count: {candidate_count:,} -> {bridged_count:,}.'
    )

# Verify that the bridge preserved exactly one valid key per row.
bridged_key_stats = (
    bridged.withColumn('_number_norm', _normalized_number())
    .agg(
        F.sum(F.when(F.col('_number_norm').isNull(), 1).otherwise(0)).alias('invalid_keys'),
        F.countDistinct('_number_norm').alias('distinct_keys'),
    )
    .first()
    .asDict()
)
bridged_invalid_key_count  = int(bridged_key_stats['invalid_keys'] or 0)
bridged_distinct_key_count = int(bridged_key_stats['distinct_keys'] or 0)

# ── Measure conversion loss: populated source values that became NULL ───────────
cast_loss_counts: Dict[str, int] = {}
if cast_checks:
    audit_expressions = []
    audit_aliases: List[Tuple[str, str, str]] = []
    for index, (destination_name, source_name, output_expression) in enumerate(cast_checks):
        alias = f'check_{index}'
        audit_expressions.append(
            F.sum(
                F.when(_is_present(source_name) & output_expression.isNull(), 1).otherwise(0)
            ).alias(alias)
        )
        audit_aliases.append((alias, destination_name, source_name))
    # Run against candidates_norm (pre-bridge) so source columns are still reachable.
    audit_row = candidates_norm.agg(*audit_expressions).first().asDict()
    for alias, destination_name, source_name in audit_aliases:
        loss_count = int(audit_row.get(alias) or 0)
        if loss_count:
            cast_loss_counts[destination_name] = loss_count

total_cast_loss_count = sum(cast_loss_counts.values())

print('Schema bridge summary')
print(f'  output rows                         : {bridged_count:,}')
print(f'  output columns                      : {len(bridged.columns):,}')
print(f'  exact baseline name/type signature  : {schema_exact_match}')
print(f'  direct copies                       : {len(bridge_report["direct"]):,}')
print(f'  explicit conversions                : {len(bridge_report["converted"]):,}')
print(f'  reference -> display_value mappings : {len(bridge_report["reference_display"]):,}')
print(f'  reference -> sys_id routes          : {len(bridge_report["reference_sys_id"]):,}')
print(f'  baseline-only columns set to null   : {len(bridge_report["baseline_only_null"]):,}')
print(f'  invalid keys after bridge           : {bridged_invalid_key_count:,}')
print(f'  distinct keys after bridge          : {bridged_distinct_key_count:,}')
print(f'  conversion losses                   : {total_cast_loss_count:,}')

print('Explicit conversions')
for item in bridge_report['converted']:
    print(f'  - {item}')

print('Reference mappings')
for item in bridge_report['reference_display']:
    print(f'  - {item}')
for item in bridge_report['reference_sys_id']:
    print(f'  - {item}')

if cast_loss_counts:
    print('Conversion losses by destination column')
    for dest_col, loss_count in sorted(cast_loss_counts.items()):
        print(f'  {dest_col:<45} {loss_count:>10,}')

    # Show up to 5 columns and the top offending source values to aid diagnosis.
    failing_lookup = {
        dest: (src, expr)
        for dest, src, expr in cast_checks
    }
    for dest_col in list(sorted(cast_loss_counts))[:5]:
        src_col, output_expression = failing_lookup[dest_col]
        print(f'Values causing conversion loss: {src_col} -> {dest_col}')
        (
            candidates_norm
            .filter(_is_present(src_col) & output_expression.isNull())
            .groupBy(_column(src_col).alias(src_col))
            .count()
            .orderBy(F.desc('count'))
            .show(25, truncate=80)
        )


# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Data-quality summary and write gates
# MAGIC
# MAGIC Every write gate must pass. In validation mode failed gates are reported but
# MAGIC nothing is modified. Write modes abort before any table change when a gate
# MAGIC is red.

# COMMAND ----------

# ── Fill-rate table for key columns ────────────────────────────────────────────
# Includes the columns most important for PS1 Gate / TVM failure prediction.
KEY_QUALITY_COLUMNS = [
    column_name
    for column_name in [
        # Identity / time
        'number',
        'opened_at',
        'closed_at',
        'resolved_at',
        'sys_created_on',
        'sys_updated_on',
        # PS1-critical classification signals
        'category',
        'subcategory',
        'priority',
        'severity',
        'incident_state',
        'state',
        # PS1-critical device linkage
        'cmdb_ci_display_value',
        'u_event_code_display_value',
        'u_chargeable_level_display_value',
        'u_chargeable',
        'u_cause_category',
        # Assignment
        'assigned_to_display_value',
        'assignment_group_display_value',
    ]
    if column_name in bridged.columns
]

fill_expressions = [
    F.sum(F.when(_is_present(col_name), 1).otherwise(0)).alias(col_name)
    for col_name in KEY_QUALITY_COLUMNS
]
fill_counts = bridged.agg(*fill_expressions).first().asDict() if fill_expressions else {}

print(f'Candidate field fill rates  (n = {bridged_count:,} CTA-unique rows)')
print(f'  {"Column":<45} {"Filled":>10}  {"Fill %":>7}')
print(f'  {"-"*45} {"-"*10}  {"-"*7}')
for col_name in KEY_QUALITY_COLUMNS:
    filled       = int(fill_counts.get(col_name) or 0)
    fill_percent = (filled / bridged_count * 100.0) if bridged_count else 0.0
    marker = '  <<< LOW' if fill_percent < 10.0 and col_name not in ('closed_at', 'resolved_at') else ''
    print(f'  {col_name:<45} {filled:>10,}  {fill_percent:>6.1f}%{marker}')

# ── PS1 spotlight: label/code distributions for the 4 key numeric fields ───────
print()
print('PS1 spotlight — incident_state / state / priority / severity distributions')
for _ps1_col in ('incident_state', 'state', 'priority', 'severity'):
    if _ps1_col in bridged.columns:
        print(f'\n  {_ps1_col}')
        bridged.groupBy(_ps1_col).count().orderBy(_ps1_col).show(20, truncate=False)

# ── Write gates ────────────────────────────────────────────────────────────────
write_gates: List[Tuple[str, bool, str]] = [
    (
        'Candidate count is within the approved range',
        candidate_count_in_range,
        f'{candidate_count:,}  expected {EXPECTED_MIN_NEW:,}..{EXPECTED_MAX_NEW:,}',
    ),
    (
        'CTA invalid-key count is within tolerance',
        invalid_source_key_count <= MAX_INVALID_SOURCE_KEYS,
        f'{invalid_source_key_count:,}  allowed <= {MAX_INVALID_SOURCE_KEYS:,}',
    ),
    (
        'CTA candidate business keys are unique',
        candidate_keys_are_unique,
        f'{candidate_count:,} rows / {candidate_distinct_key_count:,} keys',
    ),
    (
        'Duplicate CTA keys have usable ordering timestamps',
        duplicate_keys_without_order == 0,
        f'{duplicate_keys_without_order:,} duplicate keys without a usable timestamp',
    ),
    (
        'Populated dedupe timestamps parse successfully',
        dedupe_order_parse_failures == 0,
        f'{dedupe_order_parse_failures:,} parse failures',
    ),
    (
        'Bridge row count equals candidate row count',
        bridged_count == candidate_count,
        f'{bridged_count:,} / {candidate_count:,}',
    ),
    (
        'Bridge schema exactly matches baseline names and types',
        schema_exact_match,
        f'{len(bridged.columns):,} / {len(base.columns):,} columns',
    ),
    (
        'Bridge preserves one valid business key per row',
        bridged_invalid_key_count == 0 and bridged_distinct_key_count == bridged_count,
        f'{bridged_invalid_key_count:,} invalid; {bridged_distinct_key_count:,} distinct',
    ),
    (
        'No disallowed conversion loss',
        (total_cast_loss_count == 0) or (not FAIL_ON_CAST_LOSS),
        f'{total_cast_loss_count:,} losses; fail_on_cast_loss={FAIL_ON_CAST_LOSS}',
    ),
]

print()
print('Write-gate summary')
print(f'  {"Result":<4}  {"Gate":<60}  Detail')
print(f'  {"-"*4}  {"-"*60}  {"-"*40}')
for gate_name, passed, detail in write_gates:
    print(f'  {"PASS" if passed else "FAIL":<4}  {gate_name:<60}  {detail}')

WRITE_READY = all(passed for _, passed, _ in write_gates)
print(f'\nOverall write readiness: {"READY" if WRITE_READY else "BLOCKED"}')

if RUN_MODE == 'validate':
    print()
    print('Validation mode complete: no table was created or modified.')
    if WRITE_READY:
        print(f'  Next: set run_mode=initialize and confirmation={TARGET_TABLE}')
    else:
        print('  Resolve or explicitly approve every failed gate before initialization.')


def _require_write_ready(expected_mode: str) -> None:
    """Abort if gates are not all passing or the confirmation widget is wrong."""
    if RUN_MODE != expected_mode:
        return
    if not WRITE_READY:
        failed = [name for name, passed, _ in write_gates if not passed]
        raise RuntimeError(f'Write blocked by failed gates: {failed}')
    if CONFIRMATION != TARGET_TABLE:
        raise RuntimeError(
            f'Write confirmation failed. '
            f'Enter the exact target name {TARGET_TABLE!r} in the confirmation widget.'
        )


# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Initialise the independent target
# MAGIC
# MAGIC A DEEP CLONE of the baseline at the pinned version creates the independent
# MAGIC target and records full source lineage as table properties. This step is
# MAGIC intentionally separate from the merge so each phase can be inspected
# MAGIC individually.  This step never replaces an existing table.

# COMMAND ----------

if RUN_MODE == 'initialize':
    _require_write_ready('initialize')

    # Race-safety check: re-verify the target does not exist immediately before
    # writing, in case a concurrent session created it between preflight and here.
    if spark.catalog.tableExists(TARGET_TABLE):
        raise RuntimeError(
            f'Race-safety check: {TARGET_TABLE} now exists; initialization stopped.'
        )

    created_utc = datetime.now(timezone.utc).isoformat()
    lineage_properties = {
        'servicenow.backfill.cta_table':        CTA_TABLE,
        'servicenow.backfill.cta_version':       str(CTA_VERSION),
        'servicenow.backfill.base_table':        BASE_TABLE,
        'servicenow.backfill.base_version':      str(BASE_VERSION),
        'servicenow.backfill.key':               'trim(upper(number))',
        'servicenow.backfill.status':            'initialized',
        'servicenow.backfill.initialized_utc':   created_utc,
    }
    property_sql = ', '.join(
        f'{_sql_literal(key)} = {_sql_literal(value)}'
        for key, value in lineage_properties.items()
    )
    clone_sql = (
        f'CREATE TABLE {_qtable(TARGET_TABLE)} '
        f'DEEP CLONE {_qtable(BASE_TABLE)} VERSION AS OF {BASE_VERSION} '
        f'TBLPROPERTIES ({property_sql})'
    )
    print(f'Creating independent target from baseline version {BASE_VERSION:,} …')
    spark.sql(clone_sql).show(truncate=False)

    spark.sql(
        f'COMMENT ON TABLE {_qtable(TARGET_TABLE)} IS '
        + _sql_literal(
            'Conformed ServiceNow incident table: pinned servicenow_incident baseline plus '
            'insert-only CTA incidents absent by normalised incident number. '
            'Downstream source for S15 incident_history, S17 incident_root_cause, and PS1 features.'
        )
    )

    initialized       = spark.table(TARGET_TABLE)
    initialized_count = initialized.count()
    initialized_signature = [
        (field.name, field.dataType.simpleString()) for field in initialized.schema.fields
    ]

    if initialized_count != n_base:
        raise RuntimeError(
            f'Clone count mismatch: expected {n_base:,}, found {initialized_count:,}.'
        )
    if initialized_signature != expected_schema_signature:
        raise RuntimeError(
            'Clone schema does not exactly match the pinned baseline schema.'
        )

    print('Initialization succeeded')
    print(f'  target        : {TARGET_TABLE}')
    print(f'  rows          : {initialized_count:,}')
    print(f'  columns       : {len(initialized.columns):,}')
    print(f'  base version  : {BASE_VERSION:,}')
    print(f'  CTA version   : {CTA_VERSION:,}')
    print(f'  Next: set run_mode=merge and retain confirmation={TARGET_TABLE}')
else:
    print('Initialization step skipped (not in initialize mode).')


# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Idempotent insert-only merge and strict reconciliation
# MAGIC
# MAGIC Candidates are anti-joined against the live target keys immediately before
# MAGIC the MERGE so that a rerun after a successful merge inserts 0 rows without
# MAGIC any error. Matched target rows are always left untouched.
# MAGIC
# MAGIC Post-merge reconciliation verifies:
# MAGIC - row growth exactly equals pending inserts
# MAGIC - final row count equals baseline + validated candidates
# MAGIC - every candidate key is present in the target
# MAGIC - no unexpected keys entered the target
# MAGIC - no new duplicate keys were introduced
# MAGIC - full baseline row-level proof via `exceptAll` (when `full_reconciliation=true`)

# COMMAND ----------

if RUN_MODE == 'merge':
    _require_write_ready('merge')
    _require_table(TARGET_TABLE)
    _require_delta(TARGET_TABLE)

    # Re-verify lineage properties have not drifted since preflight.
    live_properties = _table_properties(TARGET_TABLE)
    lineage_expectations = {
        'servicenow.backfill.cta_table':    CTA_TABLE,
        'servicenow.backfill.cta_version':  str(CTA_VERSION),
        'servicenow.backfill.base_table':   BASE_TABLE,
        'servicenow.backfill.base_version': str(BASE_VERSION),
    }
    lineage_mismatches = {
        key: (expected, live_properties.get(key))
        for key, expected in lineage_expectations.items()
        if live_properties.get(key) != expected
    }
    if lineage_mismatches:
        raise RuntimeError(
            f'Target lineage changed after preflight: {lineage_mismatches}'
        )

    target_before = spark.table(TARGET_TABLE)
    target_signature = [
        (field.name, field.dataType.simpleString()) for field in target_before.schema.fields
    ]
    if target_signature != expected_schema_signature:
        raise RuntimeError(
            'Target schema differs from the pinned baseline; merge stopped.'
        )

    target_before_count = target_before.count()
    target_keys_before = (
        target_before
        .withColumn('_number_norm', _normalized_number())
        .select('_number_norm')
        .filter(F.col('_number_norm').isNotNull())
        .distinct()
        .cache()
    )

    # Idempotency: exclude candidates already present in the target.
    pending = (
        bridged
        .withColumn('_number_norm', _normalized_number())
        .join(target_keys_before, on='_number_norm', how='left_anti')
        .drop('_number_norm')
        .cache()
    )
    pending_count = pending.count()
    pending_distinct_key_count = (
        pending
        .withColumn('_number_norm', _normalized_number())
        .select('_number_norm')
        .distinct()
        .count()
    )
    if pending_count != pending_distinct_key_count:
        raise RuntimeError(
            'Pending merge source is not unique by normalised incident number.'
        )

    print('Merge plan')
    print(f'  validated candidates : {candidate_count:,}')
    print(f'  already in target    : {candidate_count - pending_count:,}')
    print(f'  pending inserts      : {pending_count:,}')

    if pending_count:
        from delta.tables import DeltaTable

        target_delta  = DeltaTable.forName(spark, TARGET_TABLE)
        merge_condition = (
            'upper(trim(cast(t.number as string))) = '
            'upper(trim(cast(s.number as string)))'
        )
        (
            target_delta.alias('t')
            .merge(pending.alias('s'), merge_condition)
            .whenNotMatchedInsertAll()
            .execute()
        )
        print('Delta MERGE committed.')
    else:
        print('No pending inserts: merge is already fully applied (idempotent rerun).')

    # ── Strict post-merge reconciliation ──────────────────────────────────────
    target_after       = spark.table(TARGET_TABLE)
    target_after_count = target_after.count()
    actual_growth      = target_after_count - target_before_count

    if actual_growth != pending_count:
        raise RuntimeError(
            f'Strict growth reconciliation failed: '
            f'expected +{pending_count:,}, found {actual_growth:+,}. '
            'This can indicate an unexpected concurrent writer.'
        )

    expected_final_count = n_base + candidate_count
    if target_after_count != expected_final_count:
        raise RuntimeError(
            f'Final count mismatch: expected baseline {n_base:,} '
            f'+ candidates {candidate_count:,} = {expected_final_count:,}, '
            f'found {target_after_count:,}.'
        )

    target_after_norm = target_after.withColumn('_number_norm', _normalized_number()).cache()
    target_keys_after = (
        target_after_norm.select('_number_norm')
        .filter(F.col('_number_norm').isNotNull())
        .distinct()
        .cache()
    )
    candidate_keys = (
        bridged.withColumn('_number_norm', _normalized_number())
        .select('_number_norm')
        .distinct()
        .cache()
    )

    # All validated candidate keys must be present.
    missing_candidate_keys = (
        candidate_keys.join(target_keys_after, '_number_norm', 'left_anti').count()
    )
    # Target-only keys (those not in baseline) must exactly equal candidate keys.
    target_only_keys = (
        target_keys_after.join(base_keys, '_number_norm', 'left_anti').cache()
    )
    unexpected_target_only_keys = (
        target_only_keys.join(candidate_keys, '_number_norm', 'left_anti').count()
    )
    missing_target_only_keys = (
        candidate_keys.join(target_only_keys, '_number_norm', 'left_anti').count()
    )

    if missing_candidate_keys:
        raise RuntimeError(
            f'{missing_candidate_keys:,} validated candidate keys are missing after merge.'
        )
    if unexpected_target_only_keys or missing_target_only_keys:
        raise RuntimeError(
            'Target-only key set does not exactly equal the validated CTA candidate key set: '
            f'unexpected={unexpected_target_only_keys:,}, '
            f'missing={missing_target_only_keys:,}.'
        )

    # No new duplicate keys should have been introduced.
    target_duplicate_keys = (
        target_after_norm.filter(F.col('_number_norm').isNotNull())
        .groupBy('_number_norm')
        .agg(F.count(F.lit(1)).alias('row_count'))
        .filter(F.col('row_count') > 1)
        .cache()
    )
    new_duplicate_keys = (
        target_duplicate_keys.select('_number_norm')
        .join(base_duplicate_keys.select('_number_norm'), '_number_norm', 'left_anti')
        .count()
    )
    if new_duplicate_keys:
        raise RuntimeError(
            f'Merge introduced {new_duplicate_keys:,} new duplicate incident keys.'
        )

    # Full baseline row-level proof: no baseline row was modified or lost.
    baseline_rows_missing = 0
    if FULL_RECONCILIATION:
        baseline_rows_missing = (
            base.select(*base.columns)
            .exceptAll(target_after.select(*base.columns))
            .count()
        )
        if baseline_rows_missing:
            raise RuntimeError(
                f'{baseline_rows_missing:,} baseline rows are missing or changed in the target.'
            )

    # Stamp completion metadata on the target table.
    completion_utc = datetime.now(timezone.utc).isoformat()
    completion_properties = {
        'servicenow.backfill.status':          'merged_and_validated',
        'servicenow.backfill.candidate_count': str(candidate_count),
        'servicenow.backfill.completed_utc':   completion_utc,
    }
    completion_property_sql = ', '.join(
        f'{_sql_literal(key)} = {_sql_literal(value)}'
        for key, value in completion_properties.items()
    )
    spark.sql(
        f'ALTER TABLE {_qtable(TARGET_TABLE)} SET TBLPROPERTIES ({completion_property_sql})'
    )

    print()
    print('MERGE AND RECONCILIATION SUCCEEDED')
    print(f'  baseline rows                   : {n_base:,}')
    print(f'  validated CTA candidate rows    : {candidate_count:,}')
    print(f'  rows inserted in this run       : {pending_count:,}')
    print(f'  final target rows               : {target_after_count:,}')
    print(f'  candidate keys missing          : {missing_candidate_keys:,}')
    print(f'  unexpected target-only keys     : {unexpected_target_only_keys:,}')
    print(f'  new duplicate keys introduced   : {new_duplicate_keys:,}')
    if FULL_RECONCILIATION:
        print(f'  baseline rows missing/changed   : {baseline_rows_missing:,}')
    else:
        print('  full baseline row reconciliation: skipped by widget')

    print()
    print('Latest target history')
    spark.sql(
        f'DESCRIBE HISTORY {_qtable(TARGET_TABLE)} LIMIT 5'
    ).select('version', 'timestamp', 'operation', 'operationMetrics', 'userName').show(
        truncate=False
    )
else:
    print('Merge step skipped (not in merge mode).')


# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Post-merge quality spot-check
# MAGIC
# MAGIC Runs only after a successful merge. Confirms the combined table looks correct
# MAGIC before downstream pipelines are re-pointed to it.

# COMMAND ----------

if RUN_MODE == 'merge':
    _conformed = spark.table(TARGET_TABLE).cache()
    _n_total   = _conformed.count()

    # ── Year distribution across the full conformed table ─────────────────────
    print(f'Year distribution — {TARGET_TABLE}  (n={_n_total:,})')
    if 'opened_at' in _conformed.columns:
        (
            _conformed
            .withColumn(
                'opened_year',
                F.year(F.expr(f'try_cast({_qident("opened_at")} AS TIMESTAMP)')),
            )
            .groupBy('opened_year')
            .count()
            .orderBy('opened_year')
            .show(50, truncate=False)
        )

    # ── PS1 field fill rates on the full conformed table ──────────────────────
    _ps1_spot_cols = [
        c for c in [
            'opened_at', 'closed_at', 'resolved_at',
            'category', 'subcategory', 'priority', 'severity',
            'incident_state', 'state',
            'cmdb_ci_display_value',
            'u_event_code_display_value',
            'u_chargeable_level_display_value',
            'u_chargeable', 'u_cause_category',
        ]
        if c in _conformed.columns
    ]

    _fill_exprs = [
        F.sum(F.when(_is_present(c), 1).otherwise(0)).alias(c)
        for c in _ps1_spot_cols
    ]
    _fill_row = _conformed.agg(*_fill_exprs).first().asDict()

    print(f'\nPS1 column fill rates — full conformed table  (n={_n_total:,})')
    print(f'  {"Column":<45} {"Filled":>10}  {"Fill %":>7}')
    print(f'  {"-"*45} {"-"*10}  {"-"*7}')
    for _c in _ps1_spot_cols:
        _filled  = int(_fill_row.get(_c) or 0)
        _pct     = (_filled / _n_total * 100.0) if _n_total else 0.0
        print(f'  {_c:<45} {_filled:>10,}  {_pct:>6.1f}%')

    # ── Sample of 10 CTA-origin rows (incident numbers NOT in original baseline)
    print('\nSample CTA-origin rows in conformed table')
    _sample_cols = [
        c for c in [
            'number', 'opened_at', 'category', 'subcategory',
            'priority', 'severity', 'incident_state',
            'cmdb_ci_display_value', 'u_event_code_display_value',
        ]
        if c in _conformed.columns
    ]
    _base_num_df = base.withColumn('_number_norm', _normalized_number())
    (
        _conformed
        .withColumn('_number_norm', _normalized_number())
        .join(_base_num_df.select('_number_norm').distinct(), '_number_norm', 'left_anti')
        .drop('_number_norm')
        .select(*_sample_cols)
        .limit(10)
        .show(truncate=60)
    )

    _conformed.unpersist(blocking=False)
    print('\nPost-merge quality spot-check complete.')
else:
    print('Post-merge spot-check skipped (not in merge mode).')


# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Handover checklist
# MAGIC
# MAGIC After a successful merge the following steps must be completed before PS1
# MAGIC SageMaker models are retrained.
# MAGIC
# MAGIC ### Immediate — Databricks
# MAGIC
# MAGIC 1. **Re-point S15 (`incident_history`)** — update the Silver rebuild notebook
# MAGIC    to read from `mars_dev.silver.servicenow_incident_conformed` instead of
# MAGIC    `mars_dev.bronze.servicenow_incident` and re-run it.
# MAGIC 2. **Re-point S17 (`incident_root_cause`)** — same substitution; re-run.
# MAGIC 3. **Update `export_silver_to_s3.py`** — add or replace the servicenow export
# MAGIC    entry so the S3 Parquet path (`chicago/silver/servicenow_incident/`) is
# MAGIC    sourced from the conformed table, then re-run the export job.
# MAGIC
# MAGIC ### SageMaker — PS1 models
# MAGIC
# MAGIC 4. Re-run **`PS1_GATE_Failure_Prediction.ipynb`** and
# MAGIC    **`PS1_TVM_Failure_Prediction.ipynb`** against the refreshed S3 Parquet.
# MAGIC    The extra ~20 K historical incidents expand the failure event coverage and
# MAGIC    are expected to improve AUC, particularly for low-frequency failure modes.
# MAGIC
# MAGIC ### Source preservation
# MAGIC
# MAGIC 5. **Retain both Bronze source tables** (`bronze.servicenow_incident` and
# MAGIC    `bronze.cta_servicenow_incident`) unchanged for lineage, recovery, and
# MAGIC    audit purposes. This notebook does not rename, archive, or delete either
# MAGIC    source.
# MAGIC 6. **Archive NB100** (`100_ServiceNow_CTA_Merge_Into_Incident_21Jul2026.py`).
# MAGIC    It appended directly to Bronze and is superseded by this notebook.
# MAGIC
# MAGIC ### Future ingestion
# MAGIC
# MAGIC 7. If CTA incidents are ingested on a recurring basis, promote this
# MAGIC    notebook to an orchestrated pipeline. Define how a later, richer XML record
# MAGIC    from `bronze.servicenow_incident` should replace an earlier CTA-only record
# MAGIC    in the conformed table (currently, existing rows are never overwritten).
# MAGIC
# MAGIC ---
# MAGIC
# MAGIC Reference documentation:
# MAGIC
# MAGIC - [Clone a table — Azure Databricks](https://learn.microsoft.com/en-us/azure/databricks/tables/operations/clone)
# MAGIC - [Upsert using Delta MERGE](https://learn.microsoft.com/en-us/azure/databricks/delta/merge)
# MAGIC - [TRY_CAST — Databricks SQL](https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/functions/try_cast)

# COMMAND ----------

# ── Release cached DataFrames to free executor memory ──────────────────────────
for _cached_name in [
    'cta_valid',
    'duplicate_key_counts',
    'base_keys',
    'base_duplicate_keys',
    'candidates_norm',
    'bridged',
    'target_keys_before',
    'pending',
    'target_after_norm',
    'target_keys_after',
    'candidate_keys',
    'target_only_keys',
    'target_duplicate_keys',
]:
    _cached_frame = globals().get(_cached_name)
    if _cached_frame is not None:
        try:
            _cached_frame.unpersist(blocking=False)
        except Exception:
            pass

print('Notebook complete.')
