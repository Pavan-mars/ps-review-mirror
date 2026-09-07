# Databricks notebook source
# MAGIC %md
# MAGIC # CUBIC MARS — Silver Layer Data Quality (v1)
# MAGIC GX 0.18.19 on Databricks / `mars_dev`. Same pattern as Bronze_dq_framework.
# MAGIC
# MAGIC **5 Silver checks per table:**
# MAGIC 1. **Row Count & Bronze Reconciliation** — min row floor + silver vs bronze count (where 1:1 lineage exists)
# MAGIC 2. **PK & Completeness** — primary-key uniqueness + mandatory not-null columns
# MAGIC 3. **Referential Integrity** — Spark anti-join FK checks (orphan detection)
# MAGIC 4. **Freshness** — watermark column within rolling window (timestamp-cast safe)
# MAGIC 5. **Business Rules** — format regex, domain/range, temporal pairs, hygiene, conditional rules
# MAGIC
# MAGIC **Audit tables:** `mars_dev.audit.silver_dq_scorecard_v1`, `mars_dev.audit.silver_dq_expectation_results_v1`

# COMMAND ----------

# MAGIC %pip install great-expectations==0.18.19
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Config

# COMMAND ----------

CATALOG  = 'mars_dev'
SILVER   = f'{CATALOG}.silver'
BRONZE   = f'{CATALOG}.bronze'
GX_ROOT  = 'dbfs:/cubic_mars/great_expectations'

SCORECARD = f'{CATALOG}.audit.silver_dq_scorecard_v1'
DETAIL    = f'{CATALOG}.audit.silver_dq_expectation_results_v1'
CONTRACT_TABLE = f'{CATALOG}.audit.bronze_data_contract'

FRESHNESS_LAG_DAYS        = 3
FRESHNESS_FUTURE_TOL_DAYS = 1
DEFAULT_ROW_FLOOR         = 1
COUNT_MATCH_THRESHOLD     = 95.0   # silver must be >= this % of bronze row count (1:1 tables)
SAMPLE_FRACTION           = 1.0
FAIL_ON_BLOCKING          = False  # True in scheduled jobs
RESET_DQ_TABLES           = False

CATS = ['TVM', 'GATE', 'VALIDATOR', 'READER', 'OTHER', 'UNKNOWN']

# Silver short_name -> primary bronze source table (1:1 lineage only; skip multi-source tables)
BRONZE_RECONCILE = {
    'device_event_enriched': 'edw_device_event',
    'dim_device':            'edw_device_dimension',
    'dim_event_type':        'edw_event_type_dimension',
    'dim_stop_point':        'ncs_stage_stop_point',
    'dim_facility':          'ncs_stage_transit_facility',
    'hw_config_current':     'edw_device_current_hw_config',
    'kpi_avail_enriched':    'edw_availability_events',
    'incident_history':      'servicenow_incident',
    'metric_daily':          'edw_device_metric',
    'metric_hourly':         'edw_device_metric',
}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Bootstrap + helpers

# COMMAND ----------

import great_expectations as gx
from datetime import datetime, timedelta, timezone
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
    'expect_table_row_count_to_be_between':             'Row Count',
    'expect_column_values_to_be_unique':                'PK Uniqueness',
    'expect_compound_columns_to_be_unique':             'PK Uniqueness',
    'expect_column_values_to_not_be_null':              'Completeness',
    'expect_column_values_to_match_regex':              'Format & Pattern',
    'expect_column_values_to_be_between':               'Domain & Range',
    'expect_column_values_to_be_in_set':                'Domain & Range',
    'expect_column_values_to_not_be_in_set':            'Domain & Range',
    'expect_column_max_to_be_between':                  'Freshness',
}

def table_layer(table_name):
    parts = (table_name or '').split('.')
    return parts[1] if len(parts) >= 2 else 'unknown'

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
  USING DELTA COMMENT 'Silver GX DQ scorecard v1'""")

spark.sql(f"""CREATE TABLE IF NOT EXISTS {DETAIL} (
  layer STRING, suite STRING, table_name STRING, check_category STRING,
  expectation_type STRING, column_name STRING, severity STRING,
  success BOOLEAN, errored BOOLEAN, unexpected_pct DOUBLE, observed STRING, run_ts TIMESTAMP)
  USING DELTA COMMENT 'Silver GX DQ per-expectation detail v1'""")

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
        detail.append((table_layer(table_name), suite_name, table_name, cat, etype, col, sev,
                       success, errored, float(unexp) if unexp is not None else None,
                       str(obs)[:200], ts))
    st = vr['statistics']
    evaluated = int(st['evaluated_expectations'])
    successful = int(st['successful_expectations'])
    success_pct = float(st['success_percent']) if st['success_percent'] is not None else 0.0
    passed = (blocking_fail == 0)
    _write_scorecard_detail(table_layer(table_name), suite_name, table_name, evaluated, successful,
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
    layer = table_layer(table)
    _write_scorecard_detail(layer, suite, table, 1, int(success), 100.0 if success else 0.0,
                             bf, wf, success, ts,
                             [(layer, suite, table, check_category, expectation_type, column_name,
                               severity, success, False,
                               float(unexpected_pct) if unexpected_pct is not None else None,
                               f'{str(observed)[:180]} {note}'.strip()[:200], ts)])
    RESULTS.append({'suite': suite, 'table': table, 'evaluated': 1, 'successful': int(success),
                    'success_pct': 100.0 if success else 0.0, 'blocking_fail': bf,
                    'warning_fail': wf, 'passed': success, 'run_ts': ts})
    status = 'PASS' if success else 'FAIL'
    print(f'  [{status}] {check_category} | {expectation_type} | {column_name} | observed={str(observed)[:80]}')

def cast_timestamp_cols(df, col_names):
    for c in col_names:
        if c and c in df.columns:
            df = df.withColumn(c, F.to_timestamp(F.col(c).cast('string')))
    return df

print('helpers ready')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Silver DQ check functions

# COMMAND ----------


def expect_pk(v, cols, pk_cols, *, severity='blocking', meta=None):
    present = [c for c in pk_cols if c in cols]
    if len(present) != len(pk_cols):
        print(f'   SKIP PK {pk_cols} -- missing {set(pk_cols)-set(present)}')
        return False
    m = dict(meta or {})
    m['severity'] = severity
    m['check_category'] = 'PK Uniqueness'
    try:
        if len(pk_cols) == 1:
            v.expect_column_values_to_be_unique(pk_cols[0], meta=m)
        else:
            v.expect_compound_columns_to_be_unique(pk_cols, meta=m)
        return True
    except Exception as e:
        print(f'   SKIP PK {pk_cols} -- {e}')
        return False

def expect_not_null_list(v, cols, col_list, *, severity='blocking'):
    for c in col_list:
        safe_expect(v, cols, 'expect_column_values_to_not_be_null', column=c,
                    severity=severity, check_category='Completeness')

def expect_regex_list(v, cols, items):
    for item in items:
        col, pattern, sev = item[0], item[1], item[2]
        mostly = item[3] if len(item) > 3 else None
        kw = {'regex': pattern}
        if mostly is not None:
            kw['mostly'] = mostly
        safe_expect(v, cols, 'expect_column_values_to_match_regex', column=col, severity=sev,
                    check_category='Format & Pattern', **kw)

def expect_domain_list(v, cols, items):
    for it in items:
        col, kind, sev = it['col'], it['kind'], it.get('severity', 'warning')
        mostly = it.get('mostly')
        if kind == 'between':
            kw = {}
            if it.get('min') is not None: kw['min_value'] = it['min']
            if it.get('max') is not None: kw['max_value'] = it['max']
            if mostly is not None: kw['mostly'] = mostly
            safe_expect(v, cols, 'expect_column_values_to_be_between', column=col, severity=sev,
                        check_category='Domain & Range', **kw)
        elif kind == 'in_set':
            kw = {'value_set': it['value_set']}
            if mostly is not None: kw['mostly'] = mostly
            safe_expect(v, cols, 'expect_column_values_to_be_in_set', column=col, severity=sev,
                        check_category='Domain & Range', **kw)
        elif kind == 'not_in_set':
            kw = {'value_set': it['value_set']}
            if mostly is not None: kw['mostly'] = mostly
            safe_expect(v, cols, 'expect_column_values_to_not_be_in_set', column=col, severity=sev,
                        check_category='Domain & Range', **kw)

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
    _record_custom_check(
        suite, child_table, 'Referential Integrity', 'ref_integrity', ','.join(fk_cols),
        severity, passed, round((1 - match_pct) * 100, 4),
        f'orphans={orphans}/{total} vs {parent_table}({",".join(parent_cols)})', note)
    return passed

def temporal_pair_check(suite, table, col_a, op, col_b, *, severity='warning'):
    try:
        df = spark.table(table)
        cols = {f.name for f in df.schema.fields}
    except Exception as e:
        print(f'[temporal] SKIP {suite}: {e}')
        return None
    if col_a not in cols or col_b not in cols:
        print(f'[temporal] SKIP {suite}: {col_a}/{col_b} absent')
        return None
    both = df.filter(F.col(col_a).isNotNull() & F.col(col_b).isNotNull())
    total = both.count()
    bad = (both.filter(F.col(col_a) < F.col(col_b)).count() if op == '>='
           else both.filter(F.col(col_a) > F.col(col_b)).count())
    pct = (bad / total * 100.0) if total else 0.0
    passed = bad == 0
    _record_custom_check(suite, table, 'Temporal Logic', 'temporal_pair', f'{col_a},{col_b}',
                         severity, passed, round(pct, 4), f'violations={bad}/{total}', '')
    return passed

def hygiene_trim_upper_check(suite, table, col, *, severity='warning'):
    try:
        df = spark.table(table)
        if col not in {f.name for f in df.schema.fields}:
            return None
    except Exception:
        return None
    total = df.filter(F.col(col).isNotNull()).count()
    dirty = df.filter(F.col(col).isNotNull() & (F.trim(F.upper(F.col(col))) != F.col(col))).count()
    passed = dirty == 0
    _record_custom_check(suite, table, 'Format & Pattern', 'hygiene_trim_upper', col,
                         severity, passed, round(dirty / max(total, 1) * 100, 4),
                         f'dirty={dirty}/{total}', '')

def conditional_rule_check(suite, table, cond_col, cond_lo, cond_hi, flag_col, *, severity='warning', note=''):
    try:
        df = spark.table(table)
        cols = {f.name for f in df.schema.fields}
    except Exception:
        return None
    if cond_col not in cols or flag_col not in cols:
        return None
    total = df.count()
    expected = (F.col(cond_col) >= cond_lo) & (F.col(cond_col) <= cond_hi)
    mismatched = df.filter(expected != F.col(flag_col)).count()
    passed = mismatched == 0
    _record_custom_check(suite, table, 'Domain & Range', 'conditional_rule', flag_col,
                         severity, passed, round(mismatched / max(total, 1) * 100, 4),
                         f'mismatched={mismatched}/{total}', note)

def expect_temporal_list(v, cols, df, items):
    """bound/window temporal checks — columns cast to timestamp before GX."""
    today = now_utc().strftime('%Y-%m-%d')
    cast_cols = {it['col'] for it in items if it.get('kind') in ('bound', 'window')}
    df = cast_timestamp_cols(df, list(cast_cols))
    cols = {f.name for f in df.schema.fields}
    for it in items:
        if it.get('kind') == 'pair':
            continue
        sev = it.get('severity', 'warning')
        if it['kind'] == 'bound':
            col, op = it['col'], it['op']
            bound = today if it['bound'] == 'today' else it['bound']
            kw = {}
            if op == '<=':
                kw['max_value'] = bound
            else:
                kw['min_value'] = bound
            safe_expect(v, cols, 'expect_column_values_to_be_between', column=col, severity=sev,
                        check_category='Temporal Logic', **kw)
        elif it['kind'] == 'window':
            lo = it['min']
            hi = today if it.get('max') == 'today' else it.get('max')
            kw = {}
            if lo is not None:
                kw['min_value'] = lo
            if hi is not None:
                kw['max_value'] = hi
            safe_expect(v, cols, 'expect_column_values_to_be_between', column=it['col'], severity=sev,
                        check_category='Temporal Logic', **kw)

def ratio_metric(suite, table, col, pattern_a, label_a, pattern_b, label_b, note=''):
    try:
        df = spark.table(table)
        if col not in {f.name for f in df.schema.fields}:
            return
    except Exception:
        return
    total = df.filter(F.col(col).isNotNull()).count()
    n_a = df.filter(F.col(col).rlike(pattern_a)).count()
    n_b = df.filter(F.col(col).rlike(pattern_b)).count()
    print(f'[MONITOR] {suite}: {col} {label_a}={n_a} {label_b}={n_b} total={total} {note}')

def check1_row_count_bronze(short_name, table, df, spec):
    suite = f'silver.reconciliation.{short_name}'
    print(f'\n  >> Check 1 — Row count & bronze reconcile: {short_name}')
    cols = {f.name for f in df.schema.fields}
    v, br = get_validator(df, suite)
    floor = spec.get('row_floor', DEFAULT_ROW_FLOOR)
    safe_expect(v, cols, 'expect_table_row_count_to_be_between',
                min_value=max(floor, DEFAULT_ROW_FLOOR),
                severity=spec.get('row_floor_severity', 'blocking'),
                check_category='Row Count')
    v.save_expectation_suite(discard_failed_expectations=False)
    run_and_score(suite, table, br)

    bronze_tbl = spec.get('bronze_table') or BRONZE_RECONCILE.get(short_name)
    if not bronze_tbl:
        return
    try:
        silver_count = df.count()
        bronze_count = spark.table(f'{BRONZE}.{bronze_tbl}').count()
    except Exception as e:
        print(f'  [SKIP] bronze reconcile: {e}')
        return
    ratio = round((silver_count / bronze_count) * 100, 1) if bronze_count > 0 else 0.0
    threshold = spec.get('bronze_threshold', COUNT_MATCH_THRESHOLD)
    passed = ratio >= threshold
    _record_custom_check(
        f'{suite}.bronze_vs_silver', table, 'Row Count', 'silver_vs_bronze_row_count', '*',
        spec.get('bronze_reconcile_severity', 'warning'), passed, None,
        f'Silver={silver_count:,} Bronze={bronze_count:,} ({ratio}%)',
        '' if passed else f'below {threshold}% threshold')

def check2_pk_completeness(short_name, table, df, spec):
    suite = f'silver.completeness.{short_name}'
    print(f'\n  >> Check 2 — PK & completeness: {short_name}')
    cols = {f.name for f in df.schema.fields}
    v, br = get_validator(df, suite)
    if spec.get('pk'):
        expect_pk(v, cols, spec['pk'], severity=spec.get('pk_severity', 'blocking'), meta=spec.get('pk_meta'))
    for extra in spec.get('extra_pk', []):
        expect_pk(v, cols, extra['cols'], severity=extra.get('severity', 'warning'), meta=extra.get('meta'))
    expect_not_null_list(v, cols, spec.get('not_null', []), severity=spec.get('not_null_severity', 'blocking'))
    v.save_expectation_suite(discard_failed_expectations=False)
    run_and_score(suite, table, br)

def check3_referential_integrity(short_name, table, spec):
    print(f'\n  >> Check 3 — Referential integrity (joins): {short_name}')
    for r in spec.get('ri', []):
        parent = f"{SILVER}.{r['parent']}" if not r.get('parent_bronze') else f'{BRONZE}.{r["parent"]}'
        ri_suite = f'silver.ri.{short_name}.{"_".join(r["fk"])}__{r["parent"]}'
        try:
            ref_integrity_check(ri_suite, table, r['fk'], parent, r['parent_cols'],
                                mostly=r.get('mostly', 1.0), severity=r.get('severity', 'blocking'),
                                note=r.get('note', ''))
        except Exception as e:
            print(f'  SKIP RI {ri_suite}: {e}')

def check4_freshness(short_name, table, df, spec):
    suite = f'silver.freshness.{short_name}'
    print(f'\n  >> Check 4 — Freshness: {short_name}')
    wm = spec.get('watermark') or spec.get('freshness_col') or '_silver_load_ts'
    cast_cols = [wm, '_ingest_ts', '_raw_ingest_ts']
    df = cast_timestamp_cols(df, [c for c in cast_cols if c in df.columns])
    cols = {f.name for f in df.schema.fields}
    if wm not in cols:
        for candidate in ['_silver_load_ts', '_ingest_ts', 'event_dtm', 'TRANSIT_DAY_KEY']:
            if candidate in cols:
                wm = candidate
                df = cast_timestamp_cols(df, [wm])
                cols = {f.name for f in df.schema.fields}
                break
    if wm not in cols:
        print(f'  SKIP freshness -- no watermark column on {short_name}')
        return
    v, br = get_validator(df, suite)
    lo = now_utc() - timedelta(days=FRESHNESS_LAG_DAYS)
    hi = now_utc() + timedelta(days=FRESHNESS_FUTURE_TOL_DAYS)
    safe_expect(v, cols, 'expect_column_max_to_be_between', column=wm,
                min_value=lo, max_value=hi, severity=spec.get('freshness_severity', 'warning'),
                check_category='Freshness')
    v.save_expectation_suite(discard_failed_expectations=False)
    run_and_score(suite, table, br)

def check5_business_rules(short_name, table, spec):
    suite = f'silver.business_rules.{short_name}'
    print(f'\n  >> Check 5 — Business rules: {short_name}')
    try:
        df = spark.table(table)
        cols = {f.name for f in df.schema.fields}
    except Exception as e:
        print(f'  SKIP check5: {e}')
        return
    temporal_cols = [t['col'] for t in spec.get('temporal', []) if t.get('kind') in ('bound', 'window')]
    df = cast_timestamp_cols(df, temporal_cols)
    cols = {f.name for f in df.schema.fields}
    v, br = get_validator(df, suite)
    expect_regex_list(v, cols, spec.get('format', []))
    expect_domain_list(v, cols, spec.get('domain', []))
    expect_temporal_list(v, cols, df, spec.get('temporal', []))
    v.save_expectation_suite(discard_failed_expectations=False)
    run_and_score(suite, table, br)
    for h in spec.get('hygiene', []):
        hygiene_trim_upper_check(f'{suite}.hygiene.{h["col"]}', table, h['col'], severity=h.get('severity', 'warning'))
    for c in spec.get('conditional', []):
        conditional_rule_check(f'{suite}.cond.{c["flag_col"]}', table, c['cond_col'], c['lo'], c['hi'],
                               c['flag_col'], severity=c.get('severity', 'warning'), note=c.get('note', ''))
    for rt in spec.get('ratio_monitor', []):
        ratio_metric(f'{suite}.ratio.{rt["col"]}', table, rt['col'], rt['pattern_a'], rt['label_a'],
                     rt['pattern_b'], rt['label_b'], note=rt.get('note', ''))
    for t in spec.get('temporal', []):
        if t.get('kind') == 'pair':
            temporal_pair_check(f'{suite}.temporal.{t["a"]}_{t["b"]}', table, t['a'], t['op'], t['b'],
                                severity=t.get('severity', 'warning'))

def run_silver_table(short_name, spec):
    table = f'{SILVER}.{short_name}'
    try:
        df = spark.table(table)
        n = df.count()
        cols_n = len(df.schema.fields)
    except Exception as e:
        print(f'\nSKIP silver.{short_name}: {e}')
        return None
    print(f'\n{"=" * 60}')
    print(f'▶ {short_name} → {table}  rows={n:,}  cols={cols_n}')
    print(f'{"=" * 60}')
    try:
        check1_row_count_bronze(short_name, table, df, spec)
    except Exception as e:
        print(f'  ERROR check1: {e}')
    try:
        check2_pk_completeness(short_name, table, df, spec)
    except Exception as e:
        print(f'  ERROR check2: {e}')
    try:
        check3_referential_integrity(short_name, table, spec)
    except Exception as e:
        print(f'  ERROR check3: {e}')
    try:
        check4_freshness(short_name, table, df, spec)
    except Exception as e:
        print(f'  ERROR check4: {e}')
    try:
        check5_business_rules(short_name, table, spec)
    except Exception as e:
        print(f'  ERROR check5: {e}')

print('Silver DQ check functions ready')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Silver table specs (30 tables)

# COMMAND ----------

CAT_TGV        = ['TVM', 'GATE', 'VALIDATOR']
CAT_TGV_OTHER  = ['TVM', 'GATE', 'VALIDATOR', 'OTHER']
CAT_TV         = ['TVM', 'VALIDATOR']
CAT_VG         = ['VALIDATOR', 'GATE']
CAT_TG         = ['TVM', 'GATE']

SILVER_SPECS = {

  'device_event_enriched': dict(
    row_floor=150_000_000,
    pk=['DW_DEVICE_EVENT_ID'], pk_severity='blocking',   # confirmed 0 dup via catalog_validation grain_unique
    not_null=['DW_DEVICE_EVENT_ID', 'EVENT_DTM', 'EVENT_DAY_KEY', 'DEVICE_ID', 'EVENT_TYPE_KEY',
               'is_hardware_oos_event', 'EVENT_STATE_TYPE_NAME'],  # last 2: chicago-oos-contract gap-fill
    format=[('EVENT_DAY_KEY', r'^\d{8}$', 'blocking')],
    hygiene=[dict(col='EVENT_STATE_TYPE_NAME', severity='warning')],  # trim(upper(x))==x per contract
    domain=[
      dict(col='hour_of_day', kind='between', min=0, max=23, severity='warning'),
      dict(col='day_of_week', kind='between', min=0, max=6, severity='warning'),
      dict(col='month_of_year', kind='between', min=1, max=12, severity='warning'),
      dict(col='duration_to_clear_min', kind='between', min=0, max=10080, severity='warning'),
    ],
    ri=[
      dict(fk=['DEVICE_ID'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='warning'),
      dict(fk=['EVENT_TYPE_KEY'], parent='dim_event_type', parent_cols=['EVENT_TYPE_KEY'], severity='warning'),
    ],
    temporal=[dict(kind='pair', a='CLEAR_DTM', op='>=', b='EVENT_DTM', severity='warning')],
  ),

  'device_failures': dict(
    # 3-part grain per catalog_validation (0 dup) supersedes the 2-part DEVICE_KEY+failure_date given first
    pk=['DEVICE_KEY', 'device_category', 'failure_date'], pk_severity='blocking',
    not_null=['DEVICE_KEY', 'device_category', 'failure_date', 'failure_event_count',
               'first_failure_dtm', 'last_failure_dtm', 'failure_source'],
    format=[('failure_date', r'^\d{4}-\d{2}-\d{2}$', 'warning')],
    domain=[
      dict(col='device_category', kind='in_set', value_set=CAT_TGV, severity='blocking'),  # confirmed no OTHER
      dict(col='failure_event_count', kind='between', min=1, severity='warning'),
      dict(col='downtime_minutes', kind='between', min=0, max=1440, severity='warning'),
      dict(col='failure_source', kind='in_set', value_set=['availability_events', 'device_event_enriched'], severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_KEY'], parent='dim_device', parent_cols=['DEVICE_KEY'], severity='warning')],
    temporal=[dict(kind='pair', a='last_failure_dtm', op='>=', b='first_failure_dtm', severity='warning')],
  ),

  'device_incident_features_daily': dict(
    pk=['DEVICE_KEY', 'transit_day'], pk_severity='blocking',
    not_null=['DEVICE_KEY', 'transit_day'],
    format=[('transit_day', r'^\d{4}-\d{2}-\d{2}$', 'warning')],
    domain=[
      # NOTE: incident/chargeable/major_inc *_count_* columns were listed by name only ("(see note)"),
      # no explicit min/max given -- not encoded here to avoid inventing a threshold. Confirm bounds
      # (presumably >=0) and add them if so.
      dict(col='distinct_event_codes_30d', kind='between', min=0, severity='warning'),
      dict(col='min_priority_30d_past', kind='between', min=1, max=4, severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_KEY'], parent='incident_history', parent_cols=['DEVICE_KEY'], severity='warning')],
    temporal=[dict(kind='bound', col='transit_day', op='>=', bound='2023-07-01', severity='warning')],
  ),

  'device_mttr': dict(
    pk=['DEVICE_KEY', 'failure_date'], pk_severity='blocking',  # confirmed via catalog_validation
    not_null=['DEVICE_KEY', 'device_category', 'failure_date', 'downtime_minutes', 'failure_source'],
    format=[('failure_date', r'^\d{4}-\d{2}-\d{2}$', 'warning')],
    domain=[
      dict(col='device_category', kind='in_set', value_set=CAT_TGV, severity='warning'),
      dict(col='downtime_minutes', kind='between', min=0, max=1440, severity='warning'),
      dict(col='failure_days_30d', kind='between', min=1, severity='warning'),
      dict(col='failure_days_90d', kind='between', min=1, severity='warning'),
      dict(col='max_downtime_30d', kind='between', min=0, max=1440, severity='warning'),
      dict(col='failure_source', kind='in_set', value_set=['availability_events', 'device_event_enriched'], severity='warning'),
      dict(col='days_since_prev_failure', kind='between', min=0, severity='warning'),  # temporal-logic sheet, single-col
    ],
    ri=[dict(fk=['DEVICE_KEY'], parent='device_failures', parent_cols=['DEVICE_KEY'], severity='warning')],
  ),

  'device_outage': dict(
    pk=['source_event_id'], pk_severity='blocking',
    not_null=['source_event_id', 'DEVICE_KEY', 'DEVICE_ID', 'outage_start'],
    format=[('transit_day', r'^\d{4}-\d{2}-\d{2}$', 'warning')],
    domain=[
      dict(col='duration_min', kind='between', min=0, max=10080, severity='warning'),
      dict(col='failure_level', kind='between', min=0, severity='warning'),
      dict(col='is_chargeable', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='is_resolved', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='has_explicit_clear', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='is_auto_cleared', kind='in_set', value_set=[True, False], severity='warning'),
    ],
    ri=[
      dict(fk=['source_event_id'], parent='device_event_enriched', parent_cols=['DW_DEVICE_EVENT_ID'], severity='warning'),
      dict(fk=['DEVICE_ID', 'transit_day'], parent='incident_root_cause', parent_cols=['device_id', 'transit_day'], severity='warning'),
    ],
    temporal=[dict(kind='pair', a='outage_end', op='>=', b='outage_start', severity='warning')],
  ),

  'device_survival_intervals': dict(
    pk=['DEVICE_KEY', 'interval_start_date'], pk_severity='blocking',  # confirmed via catalog_validation
    not_null=['DEVICE_KEY', 'device_category', 'interval_start_date', 'interval_days', 'is_ongoing', 'is_first_interval'],
    format=[('interval_start_date', r'^\d{4}-\d{2}-\d{2}$', 'warning'),
            ('interval_end_date', r'^\d{4}-\d{2}-\d{2}$', 'warning')],
    domain=[
      dict(col='device_category', kind='in_set', value_set=CAT_TGV, severity='warning'),
      dict(col='interval_days', kind='between', min=1, severity='warning'),
      dict(col='is_ongoing', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='is_first_interval', kind='in_set', value_set=[True, False], severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_KEY'], parent='device_failures', parent_cols=['DEVICE_KEY'], severity='warning')],
    temporal=[dict(kind='pair', a='interval_end_date', op='>=', b='interval_start_date', severity='warning')],
  ),

  'device_uptime_intervals': dict(
    pk=['DEVICE_KEY', 'eod_date'], pk_severity='blocking',
    not_null=['DEVICE_KEY', 'DEVICE_ID', 'eod_date', 'date_key', 'COMPLETE_FLAG'],
    format=[('eod_date', r'^\d{4}-\d{2}-\d{2}$', 'warning'),
            ('date_key', r'^\d{8}$', 'blocking')],
    domain=[
      dict(col='COMPLETE_FLAG', kind='in_set', value_set=[0, 1], severity='warning'),
      dict(col='eod_count_messages', kind='between', min=0, severity='warning'),
      dict(col='eod_count_received_messages', kind='between', min=0, severity='warning'),
      dict(col='total_msg_count', kind='between', min=0, severity='warning'),
      dict(col='distinct_message_types', kind='between', min=0, severity='warning'),
      dict(col='hours_since_last_hb', kind='between', min=0, severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_ID'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='warning')],
    temporal=[dict(kind='bound', col='eod_date', op='<=', bound='today', severity='warning')],
  ),

  'dim_device': dict(
    pk=['DEVICE_KEY'], pk_severity='blocking',
    not_null=['DEVICE_KEY', 'DEVICE_ID', 'mars_device_category', 'effective_from', 'is_current', 'is_active'],
    format=[('effective_from', r'^\d{4}-\d{2}-\d{2}$', 'warning'),
            ('effective_to', r'^\d{4}-\d{2}-\d{2}$', 'warning')],
    domain=[
      dict(col='mars_device_category', kind='in_set', value_set=CAT_TGV_OTHER, severity='blocking'),
      # READER is a 200-299 event-code component (per dim_event_matrix), never a device category -- own guard:
      dict(col='mars_device_category', kind='not_in_set', value_set=['READER'], severity='blocking'),
      dict(col='is_current', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='is_active', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='bus_device_flag', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='fixed_location_flag', kind='in_set', value_set=[True, False], severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_ID'], parent='edw_device_dimension', parent_cols=['DEVICE_ID'], parent_bronze=True,
             severity='warning', note='cross-layer silver->bronze lineage check')],
    temporal=[dict(kind='pair', a='effective_to', op='>=', b='effective_from', severity='warning')],
  ),

  'dim_event_matrix': dict(
    pk=['event_code_id'], pk_severity='blocking',
    not_null=['event_code_id', 'event_name', 'applies_to_gate', 'applies_to_bus', 'applies_to_fmvd',
               'is_oos', 'is_set_clear', 'requires_service_call', 'event_priority', 'is_commanded_oos'],
    format=[('event_code_id', r'^\d+$', 'warning')],
    domain=[
      dict(col='event_priority', kind='between', min=1, max=4, severity='warning'),
      dict(col='applies_to_gate', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='applies_to_bus', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='applies_to_fmvd', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='is_oos', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='is_set_clear', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='requires_service_call', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='is_commanded_oos', kind='in_set', value_set=[True, False], severity='warning'),
    ],
    # no FK defined; conditional business rule instead: is_reader_event TRUE iff event_code_id in 200-299
    conditional=[dict(cond_col='event_code_id', lo=200, hi=299, flag_col='is_reader_event',
                       severity='warning', note='reader-event range per dim_event_matrix spec')],
  ),

  'dim_event_type': dict(
    pk=['EVENT_TYPE_KEY'], pk_severity='blocking',
    not_null=['EVENT_TYPE_KEY', 'EVENT_TYPE_ID', 'EVENT_TYPE_NAME'],
    format=[('EVENT_TYPE_ID', r'^\d+$', 'warning')],
    domain=[
      # component_subsystem list given as "SYSTEM, CSC_READER, SCRST, BHU, CHU, etc." -- open-ended ("etc."),
      # so this is warning-severity and the set below should be extended once the full enum is confirmed.
      dict(col='component_subsystem',
           kind='in_set',
           value_set=['SYSTEM', 'CSC_READER', 'SCRST', 'BHU', 'CHU'],
           severity='warning', mostly=0.90),
      dict(col='severity_label', kind='in_set', value_set=['DEBUG', 'INFO', 'WARN', 'CRITICAL'], severity='warning'),
    ],
    ri=[dict(fk=['EVENT_TYPE_ID'], parent='dim_event_matrix', parent_cols=['event_code_id'], severity='warning')],
  ),

  'dim_facility': dict(
    pk=['facid'], pk_severity='blocking',
    not_null=['facid'],
    domain=[
      dict(col='transit_mode', kind='in_set', value_set=['OTHER'], severity='warning'),
      # NOTE: spec literally says "transit_mode = OTHER" -- verify this isn't the only valid value
      dict(col='operator_id', kind='between', min=0, severity='warning'),
      dict(col='facility_type_id', kind='between', min=0, severity='warning'),
    ],
    # NOTE: dim_facility has NO confirmed FK consumer (station_network_daily.FACILITY_ID actually joins to
    # dim_device.FACILITY_ID, not dim_facility.facid -- see RI sheet note). No RI built into/out of this table.
  ),

  'dim_failure_level': dict(
    pk=['failure_level'], pk_severity='blocking',
    not_null=['failure_level', 'description', 'metric_category', 'is_device_fault'],
    format=[('failure_level', r'^\d{1,2}$', 'warning')],
    domain=[
      dict(col='metric_category', kind='in_set', value_set=[2, 3, 4, 5], severity='warning'),
      dict(col='is_device_fault', kind='in_set', value_set=[True, False], severity='warning'),
    ],
  ),

  'dim_stop_point': dict(
    pk=['STOP_POINT_ID'], pk_severity='blocking',
    # latitude/longitude: NCS bronze has no geo (100% null until EDW STOP_POINT_DIMENSION)
    not_null=['STOP_POINT_ID', 'stop_point_name'],
    domain=[
      dict(col='STOP_POINT_TYPE_ID', kind='in_set', value_set=[1, 2], severity='warning'),
      dict(col='ACTIVE_FLAG', kind='in_set', value_set=[0, 1], severity='warning'),
      dict(col='latitude', kind='between', min=-90, max=90, severity='warning'),
      dict(col='longitude', kind='between', min=-180, max=180, severity='warning'),
    ],
  ),

  'hw_config_current': dict(
    # ASSUMPTION (unconfirmed -- reconcile grains before treating as final):
    #  - catalog_validation-confirmed grain (0 dup) = blocking
    #  - spec-listed grain = warning, kept for comparison until reconciled
    pk=['DEVICE_KEY', 'COMPONENT_SERIAL_NBR'], pk_severity='blocking',
    extra_pk=[dict(cols=['DEVICE_ID', 'COMPONENT_DESCRIPTION'], severity='warning')],
    not_null=['DEVICE_ID', 'DEVICE_KEY', 'city_id'],
    domain=[
      dict(col='hw_source', kind='in_set', value_set=['EDW', None], severity='warning'),
      dict(col='city_id', kind='in_set', value_set=['CHICAGO'], severity='warning'),
      dict(col='component_age_days', kind='between', min=0, severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_ID'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='warning')],
    temporal=[dict(kind='bound', col='REPORTED_CHANGED_DTM', op='<=', bound='today', severity='warning')],
  ),

  'incident_history': dict(
    pk=['incident_number'], pk_severity='blocking',  # CONFIRMED via catalog_validation grain_unique (0 dup)
    not_null=['incident_number', 'opened_dtm', 'DEVICE_KEY', 'mars_device_category'],
    format=[('incident_date', r'^\d{4}-\d{2}-\d{2}$', 'warning')],
    domain=[
      dict(col='mars_device_category', kind='in_set', value_set=CAT_TG, severity='warning'),
      dict(col='is_chargeable', kind='in_set', value_set=[0, 1], severity='warning'),
      dict(col='is_major_incident', kind='in_set', value_set=[0, 1], severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_KEY'], parent='dim_device', parent_cols=['DEVICE_KEY'], severity='warning', note='via wm_asset match')],
    temporal=[
      dict(kind='pair', a='resolved_dtm', op='>=', b='opened_dtm', severity='warning'),
      dict(kind='pair', a='closed_dtm', op='>=', b='opened_dtm', severity='warning'),
    ],
  ),

  'incident_root_cause': dict(
    pk=['availability_event_id'], pk_severity='blocking',
    pk_meta={'dedup': 'UPPER(TRIM(availability_event_id)) per S17 fix 2026-09-01'},
    not_null=['availability_event_id', 'device_id', 'transit_day', 'AE_FAILURE_LEVEL'],
    format=[('transit_day_key', r'^\d{8}$', 'warning')],
    domain=[
      dict(col='AE_FAILURE_LEVEL', kind='in_set', value_set=[0, 1, 2, 3, 4, 5, 6, 16, 98, 99], severity='warning'),
      dict(col='is_chargeable', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='is_device_fault', kind='in_set', value_set=[True, False], severity='warning'),
    ],
    ri=[dict(fk=['device_id'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='warning')],
    temporal=[dict(kind='pair', a='AE_END_DTM', op='>=', b='AE_START_DTM', severity='warning')],
  ),

  'incident_task_ci_link': dict(
    pk=['task_ci_sys_id'], pk_severity='blocking',
    not_null=['task_ci_sys_id', 'ci_item', 'incident_number'],
    format=[('constructed_device_id', r'^BMV\d{5}$', 'warning')],
    domain=[
      dict(col='mars_device_category', kind='in_set', value_set=['VALIDATOR'], severity='warning'),
      dict(col='is_chargeable', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='is_major_incident', kind='in_set', value_set=[True, False], severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_KEY'], parent='dim_device', parent_cols=['DEVICE_KEY'], severity='warning', note='via constructed_device_id')],
    # NOTE: linked_incident_sys_id -> incident_sys_id SKIPPED -- no parent table named for incident_sys_id.
    temporal=[dict(kind='pair', a='resolved_at', op='>=', b='opened_at', severity='warning')],
  ),

  'kpi_avail_enriched': dict(
    pk=['EVENT_ID'], pk_severity='blocking',
    not_null=['EVENT_ID', 'DEVICE_ID', 'TRANSIT_DAY_KEY', 'START_DTM'],
    format=[('TRANSIT_DAY_KEY', r'^(\d{6}|\d{8})$', 'warning')],  # allows both -- see ratio_monitor below
    domain=[
      dict(col='mars_device_category', kind='in_set', value_set=CAT_TGV, severity='warning'),
      dict(col='EXCLUDED', kind='in_set', value_set=[True, False], severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_ID'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='warning')],
    temporal=[dict(kind='pair', a='END_DTM', op='>=', b='START_DTM', severity='warning')],
    # mixed 6-digit/8-digit grain IS the defect -- monitor the split, don't let the regex above hide it:
    ratio_monitor=[dict(col='TRANSIT_DAY_KEY', pattern_a=r'^\d{6}$', label_a='6digit',
                          pattern_b=r'^\d{8}$', label_b='8digit', note='mixed-format defect per spec')],
  ),

  'kpi_daily': dict(
    # NOTE: "No PK identified in source review" -- PK uniqueness intentionally NOT built for this table.
    not_null=['KPI_ID', 'DEVICE_ID', 'TRANSIT_DAY_KEY', 'KPI_VALUE'],
    format=[('TRANSIT_DAY_KEY', r'^\d{8}$', 'warning')],
    domain=[
      dict(col='EXCLUDED', kind='in_set', value_set=[0], severity='warning', mostly=0.99),
      dict(col='kpi_target_band', kind='in_set', value_set=['A', 'B', 'C', 'D'], severity='warning'),
      dict(col='meets_target', kind='in_set', value_set=[True, False], severity='warning'),
    ],
    ri=[
      dict(fk=['DEVICE_ID'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='warning'),
      dict(fk=['KPI_ID'], parent='edw_kpi', parent_cols=['KPI_ID'], severity='warning'),
    ],
    temporal=[dict(kind='pair', a='END_DTM', op='>=', b='START_DTM', severity='warning')],
  ),

  'kpi_monthly_benchmark': dict(
    pk=['month_start', 'KPI_ID'], pk_severity='blocking',
    not_null=['month_start', 'KPI_ID', 'KPI_VALUE'],
    format=[('month_start', r'^\d{4}-\d{2}-\d{2}$', 'warning')],
    domain=[
      # BAND enum given as open-ended ("A,B,C...") -- warning + mostly so an incomplete list doesn't hard-fail:
      dict(col='BAND', kind='in_set', value_set=['A', 'B', 'C', 'D', 'E'], severity='warning', mostly=0.95),
      dict(col='is_compound', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='is_persistent', kind='in_set', value_set=[True, False], severity='warning'),
    ],
    ri=[dict(fk=['KPI_ID'], parent='edw_kpi', parent_cols=['KPI_ID'], mostly=37/47, severity='warning',
             note='documented partial match 37/47 -- monitored ratio, not hard pass/fail')],
    temporal=[dict(kind='pair', a='eb_50_pct_month', op='<=', b='eb_25_pct_month', severity='warning')],
  ),

  'maintenance_ledger': dict(
    row_floor=600_000,
    pk=['source_event_id', 'source_table'], pk_severity='blocking',
    not_null=['DEVICE_ID', 'event_dtm', 'ledger_type', 'source_table'],
    format=[('ledger_date', r'^\d{4}-\d{2}-\d{2}$', 'warning')],
    domain=[
      dict(col='mars_device_category', kind='in_set', value_set=CAT_TG, severity='warning'),
      dict(col='ledger_type', kind='in_set',
           value_set=['REPAIR_EPISODE', 'TECH_LOGIN', 'MAINTENANCE_MODE', 'COMMANDED_OOS'], severity='warning'),
      dict(col='is_commanded_oos', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='is_maintenance_oos', kind='in_set', value_set=[True, False], severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_ID'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='warning')],
    temporal=[dict(kind='pair', a='event_end_dtm', op='>=', b='event_dtm', severity='warning')],
  ),

  'metric_daily': dict(
    pk=['DEVICE_KEY', 'transit_day'], pk_severity='blocking',  # confirmed via catalog_validation
    not_null=['DEVICE_KEY', 'transit_day', 'TRANSIT_DAY_KEY'],
    format=[('TRANSIT_DAY_KEY', r'^\d{8}$', 'warning')],
    domain=[
      dict(col='mars_device_category', kind='in_set', value_set=CAT_TGV, severity='warning'),
      dict(col='m401_slow_tap_pct', kind='between', min=0, max=100, severity='warning'),
      dict(col='volume_drop_flag', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='comms_event_flag', kind='in_set', value_set=[True, False], severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_KEY'], parent='dim_device', parent_cols=['DEVICE_KEY'], severity='warning')],
    temporal=[dict(kind='bound', col='transit_day', op='<=', bound='today', severity='warning')],
  ),

  'metric_hourly': dict(
    pk=['DEVICE_KEY', 'hour_bucket'], pk_severity='blocking',
    not_null=['DEVICE_KEY', 'hour_bucket', 'transit_day'],
    format=[('transit_day', r'^\d{4}-\d{2}-\d{2}$', 'warning')],
    domain=[
      dict(col='metric_401_tap_count_hour', kind='between', min=1, severity='warning'),
      dict(col='metric_401_avg_ms_hour', kind='between', min=0, severity='warning'),
      dict(col='metric_401_max_ms_hour', kind='between', min=0, severity='warning'),
    ],
    # NEW check -- nothing upstream enforces this today (documented gap). Warning until investigated.
    ri=[dict(fk=['DEVICE_KEY'], parent='dim_device', parent_cols=['DEVICE_KEY'], severity='warning',
             note='NEW: previously unenforced anywhere in the pipeline -- investigate orphans before promoting to blocking')],
    temporal=[dict(kind='window', col='transit_day', min='2024-01-01', max='today', severity='warning')],
  ),

  'read_tap_daily': dict(
    pk=['DEVICE_ID', 'transit_day'], pk_severity='blocking',
    not_null=['DEVICE_ID', 'transit_day', 'daily_read_count'],
    format=[('TRANSIT_DAY_KEY', r'^\d{8}$', 'warning')],
    domain=[
      dict(col='reject_rate_pct', kind='between', min=0, max=100, severity='warning'),
      dict(col='entry_count', kind='between', min=0, severity='warning'),
      dict(col='exit_count', kind='between', min=0, severity='warning'),
      dict(col='daily_read_count', kind='between', min=1, severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_ID'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='warning')],
    temporal=[
      dict(kind='pair', a='last_read_dtm', op='>=', b='first_read_dtm', severity='warning'),
      dict(kind='window', col='transit_day', min='2024-01-01', max='2027-01-01', severity='warning'),
    ],
  ),

  'station_network_daily': dict(
    pk=['FACILITY_ID', 'device_category', 'transit_day'], pk_severity='blocking',  # confirmed via catalog_validation
    not_null=['FACILITY_ID', 'device_category', 'transit_day', 'devices_failed'],
    domain=[
      dict(col='device_category', kind='in_set', value_set=CAT_TGV, severity='warning'),  # confirmed no OTHER
      dict(col='is_coordinated_failure', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='is_major_station_event', kind='in_set', value_set=[True, False], severity='warning'),
      dict(col='devices_failed', kind='between', min=1, severity='warning'),
    ],
    # NOTE: joins to dim_device.FACILITY_ID per the RI sheet -- NOT dim_facility.facid.
    ri=[dict(fk=['FACILITY_ID'], parent='dim_device', parent_cols=['FACILITY_ID'], severity='warning')],
    temporal=[dict(kind='pair', a='last_failure_dtm', op='>=', b='first_failure_dtm', severity='warning')],
  ),

  'tap_event_daily': dict(
    pk=['DEVICE_ID', 'transit_day', 'OPERATOR_ID', 'BUS_ID'], pk_severity='blocking',
    not_null=['DEVICE_ID', 'transit_day', 'tap_count'],
    domain=[
      dict(col='tap_reject_rate_pct', kind='between', min=0, max=100, severity='warning'),
      dict(col='tap_timeout_rate_pct', kind='between', min=0, max=100, severity='warning'),
      dict(col='tap_count', kind='between', min=1, severity='warning'),
      dict(col='mars_device_category', kind='in_set', value_set=CAT_TGV, severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_ID'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='warning')],
    temporal=[dict(kind='pair', a='last_tap_dtm', op='>=', b='first_tap_dtm', severity='warning')],
  ),

  'tvm_sale_daily': dict(
    pk=['DEVICE_ID', 'transit_day', 'OPERATOR_ID', 'FACILITY_ID'], pk_severity='blocking',
    not_null=['DEVICE_ID', 'transit_day', 'daily_sales_count'],
    domain=[
      dict(col='error_txn_rate_pct', kind='between', min=0, max=100, severity='warning'),
      dict(col='cash_sales_pct', kind='between', min=0, max=100, severity='warning'),
      dict(col='daily_sales_count', kind='between', min=1, severity='warning'),
      dict(col='mars_device_category', kind='in_set', value_set=CAT_TV, severity='warning',
           mostly=0.95),  # "worth a data-profiling pass" per spec note -- kept lenient (not blocking)
    ],
    ri=[dict(fk=['DEVICE_ID'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='warning')],
    temporal=[dict(kind='pair', a='last_sale_dtm', op='>=', b='first_sale_dtm', severity='warning')],
  ),

  'usage_lifecycle_daily': dict(
    pk=['DEVICE_KEY', 'transit_day'], pk_severity='blocking',
    not_null=['DEVICE_KEY', 'transit_day', 'days_in_service'],
    domain=[
      dict(col='mars_device_category', kind='in_set', value_set=CAT_VG, severity='warning',
           mostly=0.95),  # "worth verifying TVM never appears" per spec note -- kept lenient (not blocking)
      dict(col='days_in_service', kind='between', min=1, severity='warning'),
      dict(col='cumulative_tap_count', kind='between', min=0, severity='warning'),
      dict(col='cumulative_failure_count', kind='between', min=0, severity='warning'),
    ],
    ri=[
      dict(fk=['DEVICE_KEY'], parent='metric_daily', parent_cols=['DEVICE_KEY'], severity='warning', note='upstream'),
      dict(fk=['DEVICE_ID'], parent='device_outage', parent_cols=['DEVICE_ID'], severity='warning'),
      dict(fk=['DEVICE_ID'], parent='maintenance_ledger', parent_cols=['DEVICE_ID'], severity='warning'),
    ],
    temporal=[dict(kind='bound', col='transit_day', op='<=', bound='today', severity='warning')],
  ),

  'use_revenue_daily': dict(
    pk=['DEVICE_ID', 'transit_day'], pk_severity='blocking',
    not_null=['DEVICE_ID', 'transit_day', 'daily_txn_count'],
    format=[('TRANSIT_DAY_KEY', r'^\d{8}$', 'warning')],
    domain=[
      dict(col='priced_txn_pct', kind='between', min=0, max=100, severity='warning'),
      dict(col='daily_txn_count', kind='between', min=0, severity='warning'),
      dict(col='mars_device_category', kind='in_set', value_set=CAT_TGV, severity='warning'),
    ],
    ri=[dict(fk=['DEVICE_ID'], parent='dim_device', parent_cols=['DEVICE_ID'], severity='warning')],
    temporal=[dict(kind='pair', a='LAST_TXN_DTM', op='>=', b='FIRST_TXN_DTM', severity='warning')],
  ),

  # 30th table -- postdates the audited 29-table catalog snapshot (added 2026-07-21; built by notebooks
  # 100_ServiceNow_CTA_Merge_Into_Incident / 101_ServiceNow_Incident_Conformed_Backfill, not sql/silver/*.sql).
  # 'servicenow_incident_conformed': dict(
  #   pk=['number'], pk_severity='blocking',
  #   extra_pk=[dict(cols=['sys_id'], severity='warning')],  # secondary candidate -- ServiceNow's true row identity
  #   not_null=['number', 'sys_updated_on'],
  #   # No format/domain/RI/temporal rules were specified for this table.
  # ),
}

print(f'SILVER_SPECS loaded: {len(SILVER_SPECS)} tables')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Discover silver tables + run all checks

# COMMAND ----------

print('=' * 70)
print('Step 1: Discovering silver tables from catalog...')
print('=' * 70)
try:
    silver_tables = [t.name for t in spark.catalog.listTables(f'{CATALOG}.silver')]
    print(f'  Found {len(silver_tables)} tables in {CATALOG}.silver')
except Exception as e:
    print(f'  WARN: catalog list failed: {e}')
    silver_tables = list(SILVER_SPECS.keys())

print('\nStep 2: Running Silver DQ for spec tables...')
print('=' * 70)
for short_name in sorted(SILVER_SPECS.keys()):
    if short_name not in silver_tables:
        print(f'\nSKIP silver.{short_name} -- not in catalog')
        continue
    try:
        run_silver_table(short_name, SILVER_SPECS[short_name])
    except Exception as e:
        print(f'  ERROR silver.{short_name}: {e}')

print('\nAll silver checks complete')

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
        f'SILVER DQ GATE FAILED: {tot_block} blocking check(s) -- review audit tables before gold.'
    )
elif tot_block > 0:
    print(f'\n{tot_block} blocking check(s) FAILED -- set FAIL_ON_BLOCKING=True to hard-fail in jobs.')
else:
    print('\nSILVER DQ GATE PASSED (warnings do not block).')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Smoke test

# COMMAND ----------

def smoke_test():
    good = (1, 'TVM', datetime.now(timezone.utc))
    bad  = (2, 'NOPE', None)
    sdf = spark.createDataFrame([good, bad], 'id int, cat string, ts timestamp')
    cols = {f.name for f in sdf.schema.fields}
    v, br = get_validator(sdf, 'smoke.silver_selftest')
    safe_expect(v, cols, 'expect_column_values_to_not_be_null', column='ts', severity='blocking')
    safe_expect(v, cols, 'expect_column_values_to_be_in_set', column='cat', value_set=CATS, severity='blocking')
    v.save_expectation_suite(discard_failed_expectations=False)
    r = run_and_score('smoke.silver_selftest', '<in-memory>', br)
    ok = r['blocking_fail'] >= 2
    print('SMOKE TEST', 'PASSED' if ok else 'FAILED')
    return ok

smoke_test()
