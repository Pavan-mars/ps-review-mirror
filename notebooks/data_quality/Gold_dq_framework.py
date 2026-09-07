# Databricks notebook source
# MAGIC %md
# MAGIC # CUBIC MARS — Gold Layer Data Quality (v1)
# MAGIC GX 0.18.19 on Databricks / `mars_dev`. Same pattern as Bronze/Silver DQ frameworks.
# MAGIC
# MAGIC **5 Gold checks per PS table (device_ps1..ps5):**
# MAGIC 1. **Row Count & Silver Source Reconciliation** — min row floor + upstream silver/bronze sources available
# MAGIC 2. **Grain / PK Uniqueness** — no duplicate rows at documented gold grain
# MAGIC 3. **Completeness** — mandatory not-null columns
# MAGIC 4. **Referential Integrity** — anti-join to silver dims + cross-gold spine consistency
# MAGIC 5. **Business Invariants** — non-negative counts, reject ≤ tap, domain rules
# MAGIC
# MAGIC **Audit tables:** `mars_dev.audit.gold_dq_scorecard_v1`, `mars_dev.audit.gold_dq_expectation_results_v1`

# COMMAND ----------

# MAGIC %pip install great-expectations==0.18.19
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Config

# COMMAND ----------

CATALOG   = 'mars_dev'
GOLD      = f'{CATALOG}.gold'
SILVER    = f'{CATALOG}.silver'
BRONZE    = f'{CATALOG}.bronze'
GX_ROOT   = 'dbfs:/cubic_mars/great_expectations'

SCORECARD = f'{CATALOG}.audit.gold_dq_scorecard_v1'
DETAIL    = f'{CATALOG}.audit.gold_dq_expectation_results_v1'

DEFAULT_ROW_FLOOR       = 1
SAMPLE_FRACTION         = 1.0
FAIL_ON_BLOCKING        = False   # True in scheduled jobs
RESET_DQ_TABLES         = False
MIN_ROWS_FOR_DISTRIBUTION = 100
ANOMALY_Z_THRESHOLD     = 3.0

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Bootstrap + helpers

# COMMAND ----------

import great_expectations as gx
from datetime import datetime, timezone
from pyspark.sql import functions as F

from pyspark.sql import DataFrame as _DF
_DF.persist = lambda self, *args, **kwargs: self
_DF.unpersist = lambda self, *args, **kwargs: self

def now_utc():
    return datetime.now(timezone.utc)

context = gx.get_context(context_root_dir=GX_ROOT)
try:
    ds = context.sources.add_spark('mars_spark')
except Exception:
    ds = context.datasources['mars_spark']

RESULTS = []

_CATEGORY_BY_METHOD = {
    'expect_table_row_count_to_be_between':   'Row Count',
    'expect_column_values_to_be_unique':      'PK Uniqueness',
    'expect_compound_columns_to_be_unique':   'PK Uniqueness',
    'expect_column_values_to_not_be_null':    'Completeness',
    'expect_column_values_to_be_between':     'Domain & Range',
}

def get_validator(df, suite_name):
    if 0.0 < SAMPLE_FRACTION < 1.0:
        df = df.sample(False, SAMPLE_FRACTION, seed=42)
    try:
        asset = ds.add_dataframe_asset(name=suite_name)
    except Exception:
        asset = ds.get_asset(suite_name)
    br = asset.build_batch_request(dataframe=df)
    context.add_or_update_expectation_suite(expectation_suite_name=suite_name)
    v = context.get_validator(batch_request=br, expectation_suite_name=suite_name)
    return v, br

def safe_expect(validator, cols, method, *, severity='blocking', column=None, check_category=None, **kw):
    if column is not None and column not in cols:
        print(f'   SKIP {method}({column}) -- column absent')
        return False
    meta = kw.pop('meta', {}) or {}
    meta['severity'] = severity
    meta['check_category'] = check_category or _CATEGORY_BY_METHOD.get(method, 'Other')
    fn = getattr(validator, method)
    try:
        if column is None:
            fn(meta=meta, **kw)
        else:
            fn(column, meta=meta, **kw)
        return True
    except Exception as e:
        print(f'   SKIP {method}({column}) -- error: {e}')
        return False

if RESET_DQ_TABLES:
    spark.sql(f'DROP TABLE IF EXISTS {SCORECARD}')
    spark.sql(f'DROP TABLE IF EXISTS {DETAIL}')

spark.sql(f"""CREATE TABLE IF NOT EXISTS {SCORECARD} (
  layer STRING, suite STRING, table_name STRING,
  evaluated INT, successful INT, success_pct DOUBLE,
  blocking_fail INT, warning_fail INT, passed BOOLEAN, run_ts TIMESTAMP)
  USING DELTA COMMENT 'Gold GX DQ scorecard v1'""")

spark.sql(f"""CREATE TABLE IF NOT EXISTS {DETAIL} (
  layer STRING, suite STRING, table_name STRING, check_category STRING,
  expectation_type STRING, column_name STRING, severity STRING,
  success BOOLEAN, errored BOOLEAN, unexpected_pct DOUBLE, observed STRING, run_ts TIMESTAMP)
  USING DELTA COMMENT 'Gold GX DQ per-expectation detail v1'""")

def _write_scorecard_detail(layer, suite, table, evaluated, successful, success_pct,
                             blocking_fail, warning_fail, passed, ts, detail_rows):
    try:
        spark.createDataFrame(
            [(layer, suite, table, evaluated, successful, success_pct,
              blocking_fail, warning_fail, passed, ts)],
            'layer string, suite string, table_name string, evaluated int, successful int, '
            'success_pct double, blocking_fail int, warning_fail int, passed boolean, run_ts timestamp'
        ).write.mode('append').saveAsTable(SCORECARD)
        if detail_rows:
            spark.createDataFrame(detail_rows,
                'layer string, suite string, table_name string, check_category string, '
                'expectation_type string, column_name string, severity string, success boolean, '
                'errored boolean, unexpected_pct double, observed string, run_ts timestamp'
            ).write.mode('append').saveAsTable(DETAIL)
    except Exception as e:
        print(f'  WARN: audit write failed for {suite}: {e}')

def run_and_score(suite_name, table_name, br):
    cp = context.add_or_update_checkpoint(
        name=suite_name.replace('.', '_') + '_cp',
        validations=[{'batch_request': br, 'expectation_suite_name': suite_name}])
    res = cp.run(runtime_configuration={'catch_exceptions': True, 'result_format': 'SUMMARY'})
    vr = res.list_validation_results()[0]
    ts = now_utc()
    blocking_fail = warning_fail = 0
    detail = []
    for r in vr['results']:
        cfg = r['expectation_config']
        meta = cfg.get('meta', {}) or {}
        sev = meta.get('severity', 'blocking')
        cat = meta.get('check_category', 'Other')
        etype = cfg['expectation_type']
        col = cfg['kwargs'].get('column')
        success = bool(r['success'])
        errored = bool((r.get('exception_info') or {}).get('raised_exception'))
        unexp = (r.get('result') or {}).get('unexpected_percent')
        obs = (r.get('result') or {}).get('observed_value')
        if not success:
            if sev == 'blocking':
                blocking_fail += 1
            else:
                warning_fail += 1
        detail.append(('gold', suite_name, table_name, cat, etype, col, sev,
                       success, errored, float(unexp) if unexp is not None else None,
                       str(obs)[:200], ts))
    st = vr['statistics']
    evaluated = int(st['evaluated_expectations'])
    successful = int(st['successful_expectations'])
    success_pct = float(st['success_percent']) if st['success_percent'] is not None else 0.0
    passed = (blocking_fail == 0)
    _write_scorecard_detail('gold', suite_name, table_name, evaluated, successful,
                             success_pct, blocking_fail, warning_fail, passed, ts, detail)
    print(f"[{'PASS' if passed else 'FAIL'}] {suite_name}: {successful}/{evaluated} ok "
          f"({success_pct:.1f}%)  blocking_fail={blocking_fail}  warning_fail={warning_fail}")
    out = {'suite': suite_name, 'table': table_name, 'evaluated': evaluated, 'successful': successful,
           'success_pct': success_pct, 'blocking_fail': blocking_fail, 'warning_fail': warning_fail,
           'passed': passed, 'run_ts': ts}
    RESULTS.append(out)
    return out

def _record_custom_check(suite, table, check_category, expectation_type, column_name,
                          severity, passed, unexpected_pct, observed, note=''):
    ts = now_utc()
    success = bool(passed)
    bf = 0 if success or severity != 'blocking' else 1
    wf = 0 if success or severity != 'warning' else 1
    _write_scorecard_detail('gold', suite, table, 1, int(success), 100.0 if success else 0.0,
                             bf, wf, success, ts,
                             [('gold', suite, table, check_category, expectation_type, column_name,
                               severity, success, False,
                               float(unexpected_pct) if unexpected_pct is not None else None,
                               f'{str(observed)[:180]} {note}'.strip()[:200], ts)])
    RESULTS.append({'suite': suite, 'table': table, 'evaluated': 1, 'successful': int(success),
                    'success_pct': 100.0 if success else 0.0, 'blocking_fail': bf,
                    'warning_fail': wf, 'passed': success, 'run_ts': ts})
    status = 'PASS' if success else 'FAIL'
    print(f'  [{status}] {check_category} | {expectation_type} | {column_name} | observed={str(observed)[:80]}')

print('helpers ready')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Gold DQ check functions

# COMMAND ----------

def expect_pk(v, cols, pk_cols, *, severity='blocking'):
    present = [c for c in pk_cols if c in cols]
    if len(present) != len(pk_cols):
        print(f'   SKIP PK {pk_cols} -- missing columns')
        return False
    meta = {'severity': severity, 'check_category': 'PK Uniqueness'}
    try:
        if len(pk_cols) == 1:
            v.expect_column_values_to_be_unique(pk_cols[0], meta=meta)
        else:
            v.expect_compound_columns_to_be_unique(pk_cols, meta=meta)
        return True
    except Exception as e:
        print(f'   SKIP PK {pk_cols} -- {e}')
        return False

def ref_integrity_check(suite, child_table, fk_cols, parent_table, parent_cols, *,
                         mostly=1.0, severity='blocking', note=''):
    try:
        cdf = spark.table(child_table)
        pdf = spark.table(parent_table)
        ccols = {f.name for f in cdf.schema.fields}
        pcols = {f.name for f in pdf.schema.fields}
    except Exception as e:
        print(f'[RI] SKIP {suite}: {e}')
        return None
    if not all(c in ccols for c in fk_cols) or not all(c in pcols for c in parent_cols):
        print(f'[RI] SKIP {suite}: columns absent')
        return None
    child_sel = cdf.select(*fk_cols).dropna().distinct()
    parent_sel = pdf.select(*parent_cols).dropna().distinct()
    for fc, pc in zip(fk_cols, parent_cols):
        if fc != pc:
            parent_sel = parent_sel.withColumnRenamed(pc, fc)
    total = child_sel.count()
    orphans = child_sel.join(parent_sel, on=fk_cols, how='left_anti').count() if total else 0
    match_pct = ((total - orphans) / total) if total else 1.0
    passed = match_pct >= mostly
    _record_custom_check(suite, child_table, 'Referential Integrity', 'ref_integrity', ','.join(fk_cols),
                         severity, passed, round((1 - match_pct) * 100, 4),
                         f'orphans={orphans}/{total} vs {parent_table}', note)
    return passed

def check1_row_count_sources(short_name, table, df, spec):
    suite = f'gold.reconciliation.{short_name}'
    print(f'\n  >> Check 1 — Row count & source reconciliation: {short_name}')
    cols = {f.name for f in df.schema.fields}
    v, br = get_validator(df, suite)
    floor = spec.get('row_floor', DEFAULT_ROW_FLOOR)
    safe_expect(v, cols, 'expect_table_row_count_to_be_between',
                min_value=max(floor, DEFAULT_ROW_FLOOR),
                severity=spec.get('row_floor_severity', 'blocking'),
                check_category='Row Count')
    v.save_expectation_suite(discard_failed_expectations=False)
    run_and_score(suite, table, br)

    gold_rows = df.count()
    print(f'   Gold rows: {gold_rows:,}')
    for src in spec.get('sources', []):
        layer = src['layer']
        src_table = src['table'] if '.' in src['table'] else f'{CATALOG}.{layer}.{src["table"]}'
        src_name = src_table.split('.')[-1]
        try:
            src_rows = spark.table(src_table).count()
            available = src_rows > 0
            _record_custom_check(
                f'{suite}.source.{src_name}', table, 'Source Reconciliation', 'source_available',
                src_name, src.get('severity', 'blocking'), available, None,
                f'{layer} rows={src_rows:,} role={src.get("role", "")}',
                '' if available else 'source empty or missing')
            print(f'   [{"PASS" if available else "FAIL"}] {layer:6} {src_name:40} rows={src_rows:,}')
        except Exception as e:
            _record_custom_check(
                f'{suite}.source.{src_name}', table, 'Source Reconciliation', 'source_available',
                src_name, src.get('severity', 'blocking'), False, None,
                f'unavailable: {str(e)[:80]}', '')
            print(f'   [FAIL] {src_name}: {e}')

def check2_grain(short_name, table, df, spec):
    suite = f'gold.grain.{short_name}'
    grain = spec.get('grain', [])
    print(f'\n  >> Check 2 — Grain / PK: {short_name} grain={grain}')
    if not grain or not all(c in df.columns for c in grain):
        print(f'  SKIP grain check -- columns unavailable')
        return
    cols = {f.name for f in df.schema.fields}
    v, br = get_validator(df, suite)
    expect_pk(v, cols, grain, severity=spec.get('grain_severity', 'blocking'))
    v.save_expectation_suite(discard_failed_expectations=False)
    run_and_score(suite, table, br)

def check3_completeness(short_name, table, df, spec):
    suite = f'gold.completeness.{short_name}'
    print(f'\n  >> Check 3 — Completeness: {short_name}')
    cols = {f.name for f in df.schema.fields}
    v, br = get_validator(df, suite)
    for col in spec.get('not_null', []):
        safe_expect(v, cols, 'expect_column_values_to_not_be_null', column=col,
                    severity=spec.get('not_null_severity', 'blocking'),
                    check_category='Completeness')
    v.save_expectation_suite(discard_failed_expectations=False)
    run_and_score(suite, table, br)

def check4_referential_integrity(short_name, table, spec):
    print(f'\n  >> Check 4 — Referential integrity (joins): {short_name}')
    for r in spec.get('ri', []):
        layer = r.get('parent_layer', 'silver')
        parent = r['parent'] if '.' in r['parent'] else f'{CATALOG}.{layer}.{r["parent"]}'
        ri_suite = f'gold.ri.{short_name}.{"_".join(r["fk"])}__{r["parent"]}'
        try:
            ref_integrity_check(ri_suite, table, r['fk'], parent, r['parent_cols'],
                                mostly=r.get('mostly', 1.0), severity=r.get('severity', 'blocking'),
                                note=r.get('note', ''))
        except Exception as e:
            print(f'  SKIP RI {ri_suite}: {e}')
    for cg in spec.get('cross_gold', []):
        parent = f'{GOLD}.{cg["parent"]}'
        ri_suite = f'gold.cross.{cg["parent"]}__{short_name}'
        try:
            ref_integrity_check(ri_suite, table, [cg['key']], parent, [cg['key']],
                                mostly=cg.get('mostly', 1.0), severity=cg.get('severity', 'blocking'),
                                note=cg.get('note', 'cross-gold spine'))
        except Exception as e:
            print(f'  SKIP cross-gold {ri_suite}: {e}')

def check5_business_invariants(short_name, table, df, spec):
    suite = f'gold.invariants.{short_name}'
    print(f'\n  >> Check 5 — Business invariants: {short_name}')
    cols = set(df.columns)
    for inv in spec.get('invariants', []):
        itype = inv['type']
        sev = inv.get('severity', 'blocking')
        if itype == 'non_negative':
            for col in inv.get('columns', []):
                if col not in cols:
                    continue
                bad = df.filter(F.col(col) < 0).limit(1).count()
                _record_custom_check(f'{suite}.nonneg.{col}', table, 'Business Invariants',
                                     'non_negative', col, sev, bad == 0, None,
                                     f'negative_rows={"yes" if bad else "no"}', '')
        elif itype == 'pair_lte':
            left, right = inv['left'], inv['right']
            if left in cols and right in cols:
                bad = df.filter(F.col(left) > F.col(right)).limit(1).count()
                _record_custom_check(f'{suite}.pair.{left}_{right}', table, 'Business Invariants',
                                     'pair_lte', f'{left}<={right}', sev, bad == 0, None,
                                     f'violations={"yes" if bad else "no"}', inv.get('note', ''))
        elif itype == 'in_set':
            col = inv['column']
            if col in cols:
                allowed = inv['value_set']
                bad = df.filter(~F.col(col).isin(allowed) & F.col(col).isNotNull()).limit(1).count()
                _record_custom_check(f'{suite}.in_set.{col}', table, 'Business Invariants',
                                     'in_set', col, sev, bad == 0, None,
                                     f'allowed={allowed}', '')

    # Optional distribution anomaly (warning only)
    if spec.get('distribution_monitor') and df.count() >= MIN_ROWS_FOR_DISTRIBUTION:
        for col in spec.get('distribution_monitor', []):
            if col not in cols:
                continue
            stats = df.select(F.avg(col).alias('mean'), F.stddev(col).alias('std')).collect()[0]
            if stats['mean'] is None or stats['std'] is None or float(stats['std']) == 0:
                continue
            mean_v, std_v = float(stats['mean']), float(stats['std'])
            lo, hi = mean_v - ANOMALY_Z_THRESHOLD * std_v, mean_v + ANOMALY_Z_THRESHOLD * std_v
            outliers = df.filter((F.col(col) < lo) | (F.col(col) > hi)).count()
            _record_custom_check(f'{suite}.dist.{col}', table, 'Distribution Monitor',
                                 'z_score_outliers', col, 'warning', outliers == 0,
                                 round(outliers / max(df.count(), 1) * 100, 4),
                                 f'outliers={outliers} range=[{lo:.2f},{hi:.2f}]', '')

def run_gold_table(short_name, spec):
    table = f'{GOLD}.{short_name}'
    try:
        df = spark.table(table)
        n = df.count()
    except Exception as e:
        print(f'\nSKIP gold.{short_name}: {e}')
        return None
    print(f'\n{"=" * 60}')
    print(f'▶ {short_name} → {table}  rows={n:,}')
    print(f'{"=" * 60}')
    for fn, args in (
        (check1_row_count_sources, (short_name, table, df, spec)),
        (check2_grain, (short_name, table, df, spec)),
        (check3_completeness, (short_name, table, df, spec)),
        (check4_referential_integrity, (short_name, table, spec)),
        (check5_business_invariants, (short_name, table, df, spec)),
    ):
        try:
            fn(*args)
        except Exception as e:
            print(f'  ERROR {fn.__name__}: {e}')

print('Gold DQ check functions ready')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Gold PS table specs (5 tables)

# COMMAND ----------

S = SILVER
B = BRONZE

GOLD_SPECS = {
  'device_ps1_daily': dict(
    row_floor=1,
    grain=['DEVICE_ID', 'transit_day'],
    not_null=['DEVICE_ID', 'transit_day'],
    sources=[
      dict(layer='silver', table=f'{S}.dim_device', role='S06 device spine', severity='blocking'),
      dict(layer='silver', table=f'{S}.device_event_enriched', role='S16 events/OOS', severity='blocking'),
      dict(layer='silver', table=f'{S}.device_outage', role='S18 outage', severity='blocking'),
      dict(layer='silver', table=f'{S}.device_failures', role='S26 labels', severity='blocking'),
      dict(layer='silver', table=f'{S}.metric_daily', role='S10 M401', severity='warning'),
      dict(layer='silver', table=f'{S}.kpi_daily', role='S12 KPI', severity='warning'),
      dict(layer='silver', table=f'{S}.tap_event_daily', role='S13 taps', severity='warning'),
      dict(layer='silver', table=f'{S}.tvm_sale_daily', role='S14 TVM sales', severity='warning'),
      dict(layer='silver', table=f'{S}.use_revenue_daily', role='S21 revenue', severity='warning'),
      dict(layer='silver', table=f'{S}.device_incident_features_daily', role='S24 incidents', severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_ID'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='blocking')],
    invariants=[
      dict(type='non_negative', columns=['event_count', 'failure_count', 'outage_count', 'tap_count', 'reject_count']),
      dict(type='pair_lte', left='reject_count', right='tap_count', note='reject <= tap'),
    ],
    distribution_monitor=['event_count', 'tap_count', 'outage_count'],
  ),

  'device_ps2_chains': dict(
    row_floor=1,
    grain=['DEVICE_ID', 'transit_day'],
    not_null=['DEVICE_ID'],
    sources=[
      dict(layer='silver', table=f'{S}.dim_device', role='S06', severity='blocking'),
      dict(layer='silver', table=f'{S}.device_event_enriched', role='S16', severity='blocking'),
      dict(layer='silver', table=f'{S}.device_incident_features_daily', role='S24', severity='warning'),
      dict(layer='silver', table=f'{S}.station_network_daily', role='S27 cascade', severity='warning'),
      dict(layer='silver', table=f'{S}.device_survival_intervals', role='S29 survival', severity='warning'),
      dict(layer='bronze', table=f'{B}.ncs_stage_cashbox_tracking', role='cashbox', severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_ID'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='blocking')],
    cross_gold=[dict(parent='device_ps1_daily', key='DEVICE_ID', severity='warning', note='PS1→PS2 spine')],
    invariants=[dict(type='non_negative', columns=['chain_length', 'cascade_device_count'])],
  ),

  'device_ps3_incident': dict(
    row_floor=1,
    grain=['availability_event_id'],
    not_null=['availability_event_id'],
    sources=[
      dict(layer='silver', table=f'{S}.incident_root_cause', role='S17 spine', severity='blocking'),
      dict(layer='silver', table=f'{S}.device_event_enriched', role='S16 pre-incident', severity='blocking'),
      dict(layer='silver', table=f'{S}.hw_config_current', role='S09 component', severity='warning'),
      dict(layer='silver', table=f'{S}.dim_event_type', role='event type', severity='warning'),
      dict(layer='bronze', table=f'{B}.edw_kpi_rules', role='KPI rules', severity='warning'),
      dict(layer='bronze', table=f'{B}.edw_kpi', role='KPI dim', severity='warning'),
    ],
    ri=[
      dict(fk=['device_id'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='warning'),
    ],
    invariants=[dict(type='non_negative', columns=['pre_incident_event_count_7d'])],
  ),

  'device_ps4_hourly': dict(
    row_floor=1,
    grain=['DEVICE_ID', 'DEVICE_KEY', 'hour_bucket', 'transit_day'],
    not_null=['DEVICE_ID', 'transit_day'],
    sources=[
      dict(layer='silver', table=f'{S}.dim_device', role='S06', severity='blocking'),
      dict(layer='silver', table=f'{S}.device_event_enriched', role='S16 hourly events', severity='blocking'),
      dict(layer='silver', table=f'{S}.metric_hourly', role='S05 M401 hourly', severity='warning'),
      dict(layer='silver', table=f'{S}.tap_event_daily', role='S13 daily broadcast', severity='warning'),
      dict(layer='silver', table=f'{S}.use_revenue_daily', role='S21 daily broadcast', severity='warning'),
      dict(layer='silver', table=f'{S}.device_incident_features_daily', role='S24 broadcast', severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_ID'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='blocking')],
    cross_gold=[dict(parent='device_ps1_daily', key='DEVICE_ID', severity='blocking', note='PS1→PS4 spine')],
    invariants=[
      dict(type='non_negative', columns=['event_count', 'tap_count', 'reject_count']),
      dict(type='pair_lte', left='reject_count', right='tap_count'),
    ],
    distribution_monitor=['event_count'],
  ),

  'device_ps5_component': dict(
    row_floor=1,
    grain=['DEVICE_ID', 'COMPONENT_SERIAL_NBR'],
    not_null=['DEVICE_ID', 'COMPONENT_SERIAL_NBR'],
    sources=[
      dict(layer='silver', table=f'{S}.hw_config_current', role='S09 component spine', severity='blocking'),
      dict(layer='silver', table=f'{S}.device_outage', role='S18 failure proxy', severity='warning'),
      dict(layer='silver', table=f'{S}.incident_history', role='S15 SN lifetime', severity='warning'),
      dict(layer='silver', table=f'{S}.tap_event_daily', role='S13 usage', severity='warning'),
      dict(layer='silver', table=f'{S}.device_mttr', role='S28 MTTR', severity='warning'),
      dict(layer='bronze', table=f'{B}.ncs_stage_cashbox_tracking', role='cashbox wear', severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_ID'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='blocking')],
    invariants=[
      dict(type='non_negative', columns=[
        'lifetime_incident_count', 'lifetime_tap_count', 'lifetime_mttr', 'failure_count',
        'component_age_days', 'active_service_days']),
    ],
    distribution_monitor=['lifetime_tap_count', 'failure_count'],
  ),
}

print(f'GOLD_SPECS loaded: {len(GOLD_SPECS)} tables')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Discover gold tables + run all checks

# COMMAND ----------

print('=' * 70)
print('Step 1: Discovering gold PS tables from catalog...')
print('=' * 70)
try:
    gold_tables = [t.name for t in spark.catalog.listTables(f'{CATALOG}.gold')]
    print(f'  Found {len(gold_tables)} tables in {CATALOG}.gold')
except Exception as e:
    print(f'  WARN: {e}')
    gold_tables = list(GOLD_SPECS.keys())

print('\nStep 2: Running Gold DQ for PS tables...')
print('=' * 70)
for short_name in sorted(GOLD_SPECS.keys()):
    if short_name not in gold_tables:
        print(f'\nSKIP gold.{short_name} -- not in catalog')
        continue
    try:
        run_gold_table(short_name, GOLD_SPECS[short_name])
    except Exception as e:
        print(f'  ERROR gold.{short_name}: {e}')

print('\nAll gold checks complete')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Report + gate

# COMMAND ----------

context.build_data_docs()
print('Data Docs:', f'{GX_ROOT}/uncommitted/data_docs/local_site/index.html')

display(spark.table(SCORECARD).orderBy('run_ts', ascending=False).limit(50))
display(spark.table(DETAIL).filter('success = false').orderBy('run_ts', ascending=False).limit(100))

tot_block = sum(r['blocking_fail'] for r in RESULTS)
tot_warn  = sum(r['warning_fail'] for r in RESULTS)
print(f"\n{'SUITE':<55} {'BLOCK':>6} {'WARN':>6}")
print('-' * 70)
for r in RESULTS:
    print(f"{r['suite']:<55} {r['blocking_fail']:>6} {r['warning_fail']:>6}")
print('-' * 70)
print(f'TOTAL blocking={tot_block}  warning={tot_warn}')

if tot_block > 0 and FAIL_ON_BLOCKING:
    raise RuntimeError(
        f'GOLD DQ GATE FAILED: {tot_block} blocking check(s) -- review audit tables before export.'
    )
elif tot_block > 0:
    print(f'\n{tot_block} blocking check(s) FAILED -- set FAIL_ON_BLOCKING=True to hard-fail in jobs.')
else:
    print('\nGOLD DQ GATE PASSED (warnings do not block).')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Smoke test

# COMMAND ----------

def smoke_test():
    good = (1, 'TVM', 5)
    bad  = (2, 'NOPE', -1)
    sdf = spark.createDataFrame([good, bad], 'id int, cat string, cnt int')
    cols = {f.name for f in sdf.schema.fields}
    v, br = get_validator(sdf, 'smoke.gold_selftest')
    safe_expect(v, cols, 'expect_column_values_to_not_be_null', column='cat', severity='blocking')
    safe_expect(v, cols, 'expect_column_values_to_be_between', column='cnt', min_value=0, severity='blocking')
    v.save_expectation_suite(discard_failed_expectations=False)
    r = run_and_score('smoke.gold_selftest', '<in-memory>', br)
    ok = r['blocking_fail'] >= 1
    print('SMOKE TEST', 'PASSED' if ok else 'FAILED')
    return ok

smoke_test()
