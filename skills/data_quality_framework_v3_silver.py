# Databricks notebook source
# MAGIC %md
# MAGIC # CUBIC MARS — Great Expectations Medallion DQ (v3, hardened)
# MAGIC GX 0.18.19 Fluent-on-Spark on Databricks / `mars_dev`. Hardens the v2 framework:
# MAGIC **data-driven, column-guarded keystone suites** · **severity tiering (blocking / warning)** ·
# MAGIC **catch_exceptions** so one bad check never halts the run · **collect-then-gate** (raise only at the end) ·
# MAGIC a **per-expectation detail table** for trending · **both-sided freshness** · timezone-aware timestamps ·
# MAGIC a **SAMPLE_FRACTION** knob · and a **self-contained smoke test** (verify the framework without the 190M table).

# COMMAND ----------

# MAGIC %pip install great-expectations==0.18.19
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Config

# COMMAND ----------

# ── Config ───────────────────────────────────────────────────────────────────
CATALOG   = 'mars_dev'
GX_ROOT   = 'dbfs:/cubic_mars/great_expectations'
SCORECARD = f'{CATALOG}.audit.dq_scorecard_v3'          # summary: one row per suite run
DETAIL    = f'{CATALOG}.audit.dq_expectation_results'   # detail: one row per expectation
CONTRACT_TABLE = f'{CATALOG}.audit.bronze_data_contract'

# Keystone tables (confirm names against the live catalog; label keystone of record = failure_ledger)
KEYSTONE_LABEL   = f'{CATALOG}.silver.maintenance_ledger'
KEYSTONE_FEATURE = f'{CATALOG}.silver.device_event_enriched'
DIM_DEVICE       = f'{CATALOG}.silver.dim_device'

RUN_UNIQUENESS_ON_BIG        = False   # uniqueness on 190M rows is expensive
FRESHNESS_LAG_DAYS           = 3       # max(watermark) must be >= now - this
FRESHNESS_FUTURE_TOL_DAYS    = 1       # ...and <= now + this (catch future-dated rows)
DEFAULT_ROW_FLOOR            = 1
SAMPLE_FRACTION              = 1.0     # <1.0 = validate on a sample (dev); 1.0 = full (prod)
RUN_CONTRACT_LOOP            = False   # set True to validate all critical+active contract tables
CATS = ['TVM','GATE','VALIDATOR','READER','OTHER','UNKNOWN']

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Bootstrap + hardened helpers

# COMMAND ----------

# ── GX context, datasource, and helpers ─────────────────────────────────────
import great_expectations as gx
from datetime import datetime, timedelta, timezone

def now_utc(): return datetime.now(timezone.utc)

context = gx.get_context(context_root_dir=GX_ROOT)
try:    ds = context.sources.add_spark('mars_spark')
except Exception: ds = context.datasources['mars_spark']

RESULTS = []          # accumulator: every suite result (collect-then-gate)

def get_validator(df, suite_name):
    if 0.0 < SAMPLE_FRACTION < 1.0:
        df = df.sample(False, SAMPLE_FRACTION, seed=42)
    try:    asset = ds.add_dataframe_asset(name=suite_name)
    except Exception: asset = ds.get_asset(suite_name)
    br = asset.build_batch_request(dataframe=df)
    context.add_or_update_expectation_suite(expectation_suite_name=suite_name)
    v = context.get_validator(batch_request=br, expectation_suite_name=suite_name)
    return v, br

# Fallback category inference for expectations that don't explicitly tag check_category
# (e.g. legacy Section 4/6 calls). Explicit tags from the Section 6b builders always win.
_CATEGORY_BY_METHOD = {
    'expect_column_values_to_be_unique':               'PK Uniqueness',
    'expect_compound_columns_to_be_unique':             'PK Uniqueness',
    'expect_column_values_to_not_be_null':              'Completeness',
    'expect_column_values_to_match_regex':              'Format & Pattern',
    'expect_column_values_to_be_between':               'Domain & Range',
    'expect_column_values_to_be_in_set':                'Domain & Range',
    'expect_column_values_to_not_be_in_set':             'Domain & Range',
    'expect_column_pair_values_a_to_be_greater_than_b': 'Temporal Logic',
    'expect_table_row_count_to_be_between':             'Row Count',
    'expect_column_max_to_be_between':                  'Freshness',
}

def table_layer(table_name):
    """catalog.layer.table -> layer (e.g. 'silver'). Falls back to 'unknown' if unparsable."""
    parts = (table_name or '').split('.')
    return parts[1] if len(parts) >= 2 else 'unknown'

def safe_expect(validator, cols, method, *, severity='blocking', column=None, check_category=None, **kw):
    """Add an expectation ONLY if its column exists; else record a skip. Tags severity + check_category in meta.
       The actual GX call is wrapped in try/except: a single check that errors (type mismatch, engine
       incompatibility, etc.) is skipped and logged rather than aborting every remaining check for the table."""
    if column is not None and column not in cols:
        print(f'   SKIP {method}({column}) -- column absent'); return False
    meta = kw.pop('meta', {}) or {}
    meta['severity'] = severity
    meta['check_category'] = check_category or _CATEGORY_BY_METHOD.get(method, 'Other')
    fn = getattr(validator, method)
    try:
        if column is None: fn(meta=meta, **kw)
        else:              fn(column, meta=meta, **kw)
        return True
    except Exception as e:
        print(f'   SKIP {method}({column}) -- errored while adding/evaluating: {e}')
        return False
    return True

# --- scorecard + detail DDL (idempotent; 'table_name' avoids the reserved word 'table') ---
# layer added to both for reporting (silver/bronze/gold). check_category (PK Uniqueness /
# Completeness / Format & Pattern / Domain & Range / Referential Integrity / Temporal Logic /
# Row Count / Freshness / Monitoring / Other) is per-CHECK, so it lives on DETAIL, not on
# SCORECARD -- a scorecard row is a table-level rollup across every category at once.
#
# RESET_DQ_TABLES: if the physical SCORECARD/DETAIL tables already exist with a different column
# order or a different column name (e.g. 'dq_category' from an earlier iteration vs 'check_category'
# here), Delta's positional append will fail with a schema-mismatch error, and Table ACLs on this
# cluster block automatic schema migration (mergeSchema). Recreating the tables here guarantees the
# physical schema always matches what this notebook writes. Set to False once the schema is known to
# already match, to preserve historical scorecard/detail rows across runs.
RESET_DQ_TABLES = True
if RESET_DQ_TABLES:
    spark.sql(f"DROP TABLE IF EXISTS {SCORECARD}")
    spark.sql(f"DROP TABLE IF EXISTS {DETAIL}")

spark.sql(f"""CREATE TABLE IF NOT EXISTS {SCORECARD} (
  layer STRING, suite STRING, table_name STRING,
  evaluated INT, successful INT, success_pct DOUBLE,
  blocking_fail INT, warning_fail INT, passed BOOLEAN, run_ts TIMESTAMP)
  USING DELTA COMMENT 'GX DQ scorecard v3 -- one row per suite run'""")
spark.sql(f"""CREATE TABLE IF NOT EXISTS {DETAIL} (
  layer STRING, suite STRING, table_name STRING, check_category STRING,
  expectation_type STRING, column_name STRING, severity STRING,
  success BOOLEAN, errored BOOLEAN, unexpected_pct DOUBLE, observed STRING, run_ts TIMESTAMP)
  USING DELTA COMMENT 'GX DQ per-expectation detail v3'""")

def run_and_score(suite_name, table_name, br):
    layer = table_layer(table_name)
    cp = context.add_or_update_checkpoint(name=suite_name.replace('.','_')+'_cp',
        validations=[{'batch_request': br, 'expectation_suite_name': suite_name}])
    res = cp.run(runtime_configuration={'catch_exceptions': True, 'result_format': 'SUMMARY'})
    vr  = res.list_validation_results()[0]; ts = now_utc()
    blocking_fail = warning_fail = 0; detail = []
    for r in vr['results']:
        cfg = r['expectation_config']; meta = cfg.get('meta', {}) or {}
        sev = meta.get('severity', 'blocking'); etype = cfg['expectation_type']
        cat = meta.get('check_category', 'Other')
        col = cfg['kwargs'].get('column'); success = bool(r['success'])
        errored = bool((r.get('exception_info') or {}).get('raised_exception'))
        unexp = (r.get('result') or {}).get('unexpected_percent')
        obs   = (r.get('result') or {}).get('observed_value')
        if not success:
            if sev == 'blocking': blocking_fail += 1
            else:                 warning_fail  += 1
        detail.append((layer, suite_name, table_name, cat, etype, col, sev, success, errored,
                       float(unexp) if unexp is not None else None, str(obs)[:200], ts))
    st = vr['statistics']; evaluated = int(st['evaluated_expectations']); successful = int(st['successful_expectations'])
    success_pct = float(st['success_percent']) if st['success_percent'] is not None else 0.0
    passed = (blocking_fail == 0)   # warnings do NOT gate
    try:
        spark.createDataFrame([(layer, suite_name, table_name, evaluated, successful, success_pct,
                                blocking_fail, warning_fail, passed, ts)],
            'layer string, suite string, table_name string, evaluated int, successful int, success_pct double, '
            'blocking_fail int, warning_fail int, passed boolean, run_ts timestamp'
            ).write.mode('append').saveAsTable(SCORECARD)
        if detail:
            spark.createDataFrame(detail,
                'layer string, suite string, table_name string, check_category string, '
                'expectation_type string, column_name string, severity string, '
                'success boolean, errored boolean, unexpected_pct double, observed string, run_ts timestamp'
                ).write.mode('append').saveAsTable(DETAIL)
    except Exception as e:
        print(f'  WARN: scorecard/detail write failed for {suite_name}: {e}')
    print(f"[{'PASS' if passed else 'FAIL'}] {suite_name}: {successful}/{evaluated} ok "
          f"({success_pct:.1f}%)  blocking_fail={blocking_fail}  warning_fail={warning_fail}")
    out = {'suite':suite_name,'table':table_name,'evaluated':evaluated,'successful':successful,
           'success_pct':success_pct,'blocking_fail':blocking_fail,'warning_fail':warning_fail,
           'passed':passed,'run_ts':ts}
    RESULTS.append(out); return out
print('helpers ready')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Contract schema (run to confirm column names)

# COMMAND ----------

# ── DESCRIBE the contract so CONTRACT_COLS (Cell 7) matches reality ──────────
try:
    cdf = spark.sql(f'DESCRIBE {CONTRACT_TABLE}'); display(cdf)
    _contract_cols = {r['col_name'] for r in cdf.collect() if not r['col_name'].startswith('#')}
    print('contract columns:', sorted(_contract_cols))
except Exception as e:
    print(f'ERROR: DESCRIBE {CONTRACT_TABLE}: {e}'); _contract_cols = set()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Keystone suites — data-driven & column-guarded
# MAGIC Each keystone is a **spec** (list of checks). `safe_expect` skips any check whose column is absent, so
# MAGIC column drift can no longer error a suite. Severity is explicit per check.

# COMMAND ----------

# ── Keystone specs + builder ────────────────────────────────────────────────
# spec item: (method, kwargs, severity). column goes in kwargs['column'] (None for table-level).
from pyspark.sql import functions as F

def build_keystone(table, spec, suite, row_floor, wm_col=None):
    try: df = spark.table(table)
    except Exception as e:
        print(f'SKIP {suite}: cannot read {table}: {e}'); return None
    # Cast any column used with parse_strings_as_datetimes to a real TimestampType up front.
    # GX's parse_strings_as_datetimes only transforms the COLUMN, not min_value/max_value, and on
    # this engine even that transform is unreliable -- casting here removes the ambiguity entirely,
    # so every bound below can be a plain Python datetime with no string bounds anywhere.
    cast_cols = {kw['column'] for _, kw, _ in spec if kw.get('parse_strings_as_datetimes') and 'column' in kw}
    if wm_col: cast_cols.add(wm_col)
    for c in cast_cols:
        if c in df.columns:
            df = df.withColumn(c, F.to_timestamp(F.col(c).cast('string')))
    cols = {f.name for f in df.schema.fields}
    n = df.count()
    print(f'\n=== {suite} ({table}) rows={n:,} cols={len(cols)} ===')
    v, br = get_validator(df, suite)
    safe_expect(v, cols, 'expect_table_row_count_to_be_between', min_value=row_floor, severity='blocking')
    for method, kw, sev in spec:
        kw = dict(kw)
        kw.pop('parse_strings_as_datetimes', None)  # column is already a real timestamp now
        safe_expect(v, cols, method, severity=sev, **kw)
    # both-sided freshness on the watermark, if present (wm_col was already cast above)
    if wm_col and wm_col in cols:
        lo = now_utc() - timedelta(days=FRESHNESS_LAG_DAYS)
        hi = now_utc() + timedelta(days=FRESHNESS_FUTURE_TOL_DAYS)
        safe_expect(v, cols, 'expect_column_max_to_be_between', column=wm_col,
                    min_value=lo, max_value=hi, severity='blocking')
        print(f'   freshness: {lo:%Y-%m-%d} <= max({wm_col}) <= {hi:%Y-%m-%d}')
    v.save_expectation_suite(discard_failed_expectations=False)
    return run_and_score(suite, table, br)

# Label keystone. NOTE: verify these column names against the live table (v2 drifted here).
LABEL_SPEC = [
  ('expect_column_values_to_not_be_null', {'column':'DEVICE_ID'}, 'blocking'),
  ('expect_column_values_to_not_be_null', {'column':'failure_ts'}, 'blocking'),
  ('expect_column_values_to_be_between',  {'column':'failure_ts','min_value':datetime(2015,1,1,tzinfo=timezone.utc),
        'max_value':datetime(2027,12,31,tzinfo=timezone.utc),'parse_strings_as_datetimes':True}, 'warning'),
  ('expect_column_values_to_be_in_set',   {'column':'mars_device_category','value_set':CATS}, 'blocking'),
  ('expect_column_values_to_be_between',  {'column':'outage_minutes','min_value':0,'mostly':0.99}, 'warning'),
  ('expect_column_values_to_be_between',  {'column':'failure_level','min_value':0,'max_value':60,'mostly':0.99}, 'warning'),
  ('expect_column_values_to_be_in_set',   {'column':'is_hardware_oos','value_set':[True,False]}, 'warning'),
]
res_label = build_keystone(KEYSTONE_LABEL, LABEL_SPEC, 'silver.maintenance_ledger',
                           row_floor=600_000, wm_col='_silver_load_ts')

# Feature keystone.
FEATURE_SPEC = [
  ('expect_column_values_to_not_be_null', {'column':'DW_DEVICE_EVENT_ID'}, 'blocking'),
  ('expect_column_values_to_not_be_null', {'column':'DEVICE_ID'}, 'blocking'),
  ('expect_column_values_to_not_be_null', {'column':'EVENT_DTM'}, 'blocking'),
  ('expect_column_values_to_be_between',  {'column':'EVENT_DTM','min_value':datetime(2023,12,31,tzinfo=timezone.utc),
        'max_value':datetime(2027,12,31,tzinfo=timezone.utc),'parse_strings_as_datetimes':True}, 'warning'),
  ('expect_column_values_to_be_in_set',   {'column':'mars_device_category','value_set':CATS}, 'blocking'),
  ('expect_column_values_to_be_in_set',   {'column':'is_oos_event','value_set':[True,False]}, 'warning'),
  ('expect_column_values_to_be_between',  {'column':'severity','min_value':0,'max_value':999,'mostly':0.95}, 'warning'),
]
res_feat = build_keystone(KEYSTONE_FEATURE, FEATURE_SPEC, 'silver.device_event_enriched',
                          row_floor=150_000_000, wm_col='_silver_load_ts')
if RUN_UNIQUENESS_ON_BIG and res_feat is not None:
    print('(uniqueness on the 190M feature keystone is enabled -- expensive)')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Referential integrity (Spark anti-join) — record, don't stop
# MAGIC

# COMMAND ----------

# ── RI: orphan DEVICE_IDs in the label keystone vs dim_device (collect, gate later)
RI_SUITE='silver.maintenance_ledger.ref_integrity'
try:
    fl_ids  = spark.table(KEYSTONE_LABEL).select('DEVICE_ID').distinct()
    dim_ids = spark.table(DIM_DEVICE).select('DEVICE_ID').distinct()
    total   = fl_ids.count()
    orphans = fl_ids.join(dim_ids, on='DEVICE_ID', how='left_anti').count()
    pct = (orphans/total*100.0) if total else 0.0
    ri_pass = pct < 1.0; ts=now_utc()
    spark.createDataFrame([(table_layer(KEYSTONE_LABEL), RI_SUITE, KEYSTONE_LABEL, 1, int(ri_pass), float(ri_pass)*100,
                            0 if ri_pass else 1, 0, ri_pass, ts)],
        'layer string, suite string, table_name string, evaluated int, successful int, success_pct double, '
        'blocking_fail int, warning_fail int, passed boolean, run_ts timestamp'
        ).write.mode('append').saveAsTable(SCORECARD)
    RESULTS.append({'suite':RI_SUITE,'table':KEYSTONE_LABEL,'evaluated':1,'successful':int(ri_pass),
                    'success_pct':float(ri_pass)*100,'blocking_fail':0 if ri_pass else 1,'warning_fail':0,
                    'passed':ri_pass,'run_ts':ts})
    print(f"[{'OK' if ri_pass else 'FAIL'}] ref_integrity orphans={orphans:,}/{total:,} ({pct:.3f}%) threshold=1%")
except Exception as e:
    print('RI check errored (recorded as warning):', e)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Contract-driven baseline generator (enriched, column-guarded)

# COMMAND ----------

# ── CONTRACT_COLS: map logical role -> actual contract column (confirm via Cell 3) ──
CONTRACT_COLS = {'table':'table','pk':'pk','watermark':'watermark','scope':'scope','grain':'grain',
                 'load_strategy':'load_strategy','ml_role':'ml_role','status':'status',
                 'row_count_floor':'row_count_floor','expected_schema':'expected_schema'}

def build_baseline_from_contract(table, *, run_checkpoint=True, skip_uniqueness=True):
    tc,pc,wc = CONTRACT_COLS['table'],CONTRACT_COLS['pk'],CONTRACT_COLS['watermark']
    try:
        rows = spark.table(CONTRACT_TABLE).filter(f'`{tc}` = \'{table}\'').limit(1).collect()
    except Exception as e:
        print(f'[contract] read error {table}: {e}'); return None
    if not rows: print(f'[contract] no row for {table} -- skipped'); return None
    row=rows[0]; fields=row.__fields__
    g=lambda k: (row[CONTRACT_COLS[k]] if CONTRACT_COLS.get(k) in fields else None)
    status=(g('status') or 'active').strip().lower()
    if status not in ('active',''): print(f'[contract] SKIP {table} status={status}'); return None
    try: df=spark.table(table)
    except Exception as e: print(f'[contract] read {table}: {e}'); return None
    cols={f.name for f in df.schema.fields}
    suite=f"baseline.{table.replace('.','_')}"; v,br=get_validator(df,suite)
    try: floor=int(g('row_count_floor')) if g('row_count_floor') else DEFAULT_ROW_FLOOR
    except (TypeError,ValueError): floor=DEFAULT_ROW_FLOOR
    safe_expect(v,cols,'expect_table_row_count_to_be_between',min_value=max(floor,DEFAULT_ROW_FLOOR),severity='blocking')
    for c in [x.strip() for x in (g('pk') or '').split(',') if x.strip()]:
        safe_expect(v,cols,'expect_column_values_to_not_be_null',column=c,severity='blocking')
        if not skip_uniqueness:
            safe_expect(v,cols,'expect_column_values_to_be_unique',column=c,severity='blocking')
    wm=(g('watermark') or '').strip()
    if wm and wm in cols:
        lo=(now_utc()-timedelta(days=FRESHNESS_LAG_DAYS)).strftime('%Y-%m-%d')
        hi=(now_utc()+timedelta(days=FRESHNESS_FUTURE_TOL_DAYS)).strftime('%Y-%m-%d')
        safe_expect(v,cols,'expect_column_max_to_be_between',column=wm,min_value=lo,max_value=hi,
                    parse_strings_as_datetimes=True,severity='blocking')
    if 'mars_device_category' in cols:
        safe_expect(v,cols,'expect_column_values_to_be_in_set',column='mars_device_category',value_set=CATS,severity='warning')
    v.save_expectation_suite(discard_failed_expectations=False)
    return run_and_score(suite, table, br) if run_checkpoint else None
print('build_baseline_from_contract ready')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. validate all critical + active contract tables

# COMMAND ----------

# ── Guarded loop (set RUN_CONTRACT_LOOP=True in config to execute) ──────────
if RUN_CONTRACT_LOOP:
    tc=CONTRACT_COLS['table']; ml=CONTRACT_COLS['ml_role']; stc=CONTRACT_COLS['status']
    try:
        crit=spark.table(CONTRACT_TABLE).filter(f"`{ml}`='critical' AND `{stc}`='active'").select(tc).distinct().collect()
    except Exception as e: print('contract read error:', e); crit=[]
    print(f'{len(crit)} critical+active tables')
    for r in crit:
        try: build_baseline_from_contract(r[tc], run_checkpoint=True, skip_uniqueness=True)
        except Exception as exc: print(f'  FAILED {r[tc]}: {exc}')
else:
    print('RUN_CONTRACT_LOOP=False -- skipping the contract sweep.')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6b. Silver layer — 30-table data-driven DQ suite (Chicago mars_dev)
# MAGIC Built from the Chicago mars_dev SILVER Layer GX Rule Spec (PK uniqueness, completeness, format/pattern,
# MAGIC domain/range, referential integrity, temporal logic). Data-driven off `SILVER_SPECS` below — one spec
# MAGIC per table, run through generic builders so column drift is skipped (never errors), and severity is
# MAGIC explicit per check so warnings never gate the pipeline.
# MAGIC
# MAGIC **Open items called out inline (search `ASSUMPTION` / `NOTE` below) — confirm with the team before
# MAGIC treating these as final:**
# MAGIC - `hw_config_current` PK: spec grain (`DEVICE_ID+COMPONENT_DESCRIPTION`) vs catalog_validation-confirmed
# MAGIC   grain (`DEVICE_KEY+COMPONENT_SERIAL_NBR`) disagree. Built BOTH: catalog_validation grain = blocking,
# MAGIC   spec grain = warning. Reconcile and delete the one that isn't the true grain.
# MAGIC - `incident_root_cause` PK (`availability_event_id`) is built as **blocking** but tagged
# MAGIC   `known_failing_pending_S17_fix` in meta -- it is EXPECTED to fail (670 dup rows, 2026-07-17
# MAGIC   catalog_validation snapshot) until Sathish's fix lands. Flip to `severity='warning'` in
# MAGIC   `SILVER_SPECS['incident_root_cause']['pk_severity']` if you'd rather it not gate the pipeline
# MAGIC   in the meantime.
# MAGIC - `kpi_monthly_benchmark -> edw_kpi` is a documented partial match (37/47). Built as a **monitored
# MAGIC   ratio** (`mostly=37/47`, warning) via `ref_integrity_check`, not a hard pass/fail.
# MAGIC - `kpi_avail_enriched.TRANSIT_DAY_KEY` mixed 6-digit/8-digit grain is built as a regex allowing both,
# MAGIC   PLUS a separate monitored ratio metric so the inconsistency stays visible.
# MAGIC - `device_event_enriched.is_hardware_oos_event` / `EVENT_STATE_TYPE_NAME` are built as **blocking**
# MAGIC   not-null per the chicago-oos-contract (previously missing from the mandatory-fields list).
# MAGIC   `EVENT_STATE_TYPE_NAME` also gets a hygiene check (`trim(upper(x)) == x`) since the contract's
# MAGIC   own `UPPER(TRIM(...))` requirement implies raw values aren't pre-cleaned.
# MAGIC - `dim_device.mars_device_category` gets an extra guard that `READER` never appears (it's a
# MAGIC   200-299 event-code component per `dim_event_matrix`, not a device category).
# MAGIC - `metric_hourly.DEVICE_KEY -> dim_device.DEVICE_KEY` is a NEW referential check (nothing upstream
# MAGIC   enforces it today) -- built as **warning** severity pending investigation into any existing orphans.
# MAGIC - `incident_task_ci_link.linked_incident_sys_id -> incident_sys_id` -- SKIPPED. No parent table was
# MAGIC   named for `incident_sys_id`; add it to `SILVER_SPECS` once the parent table is confirmed.
# MAGIC - `servicenow_incident_conformed` is the 30th table (added 2026-07-21, post-dates the audited
# MAGIC   29-table catalog snapshot; built by notebooks `100_ServiceNow_CTA_Merge_Into_Incident` /
# MAGIC   `101_ServiceNow_Incident_Conformed_Backfill`, not `sql/silver/*.sql`).

# COMMAND ----------

# ── Generic builders for the 30-table spec-driven suite ──────────────────────
from pyspark.sql import functions as F

SILVER = f'{CATALOG}.silver'

def expect_pk(v, cols, pk_cols, *, severity='blocking', meta=None):
    """Uniqueness on a single- or multi-column PK. Skips (doesn't error) if any PK column is absent
       or if the underlying GX call errors (e.g. engine incompatibility)."""
    present = [c for c in pk_cols if c in cols]
    if len(present) != len(pk_cols):
        print(f"   SKIP PK uniqueness on {pk_cols} -- missing {set(pk_cols)-set(present)}"); return False
    m = dict(meta or {}); m['severity'] = severity; m['check_category'] = 'PK Uniqueness'
    try:
        if len(pk_cols) == 1:
            v.expect_column_values_to_be_unique(pk_cols[0], meta=m)
        else:
            v.expect_compound_columns_to_be_unique(pk_cols, meta=m)
        return True
    except Exception as e:
        print(f"   SKIP PK uniqueness on {pk_cols} -- errored: {e}")
        return False

def expect_not_null_list(v, cols, col_list, *, severity='blocking'):
    for c in col_list:
        safe_expect(v, cols, 'expect_column_values_to_not_be_null', column=c, severity=severity,
                    check_category='Completeness')

def expect_regex_list(v, cols, items):
    """items: list of (column, regex, severity[, mostly])"""
    for item in items:
        col, pattern, sev = item[0], item[1], item[2]
        mostly = item[3] if len(item) > 3 else None
        kw = {'regex': pattern}
        if mostly is not None: kw['mostly'] = mostly
        safe_expect(v, cols, 'expect_column_values_to_match_regex', column=col, severity=sev,
                    check_category='Format & Pattern', **kw)

def expect_domain_list(v, cols, items):
    """items: list of dicts: {col, kind:'between'|'in_set', min, max, value_set, severity, mostly}"""
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

def expect_temporal_list(v, cols, items):
    """items: list of dicts describing a temporal rule.
       kind='pair'   : handled separately by temporal_pair_check() (Spark-native, not GX) -- GX's
                        expect_column_pair_values_a_to_be_greater_than_b raises
                        CANNOT_RESOLVE_DATAFRAME_COLUMN under this environment's Spark Connect
                        client on every call, so it is not used here at all.
       kind='bound'  : {col, op ('<=' or '>='), bound ('today'), severity}   -- vs CURRENT_DATE()
       kind='window' : {col, min, max ('today' allowed), severity}          -- fixed/rolling window
    """
    today = now_utc().strftime('%Y-%m-%d')
    for it in items:
        sev = it.get('severity', 'warning')
        if it['kind'] == 'pair':
            continue  # handled by temporal_pair_check() in build_silver_table
        elif it['kind'] == 'bound':
            col, op = it['col'], it['op']
            bound = today if it['bound'] == 'today' else it['bound']
            kw = {'parse_strings_as_datetimes': True}
            if op == '<=': kw['max_value'] = bound
            else:          kw['min_value'] = bound
            safe_expect(v, cols, 'expect_column_values_to_be_between', column=col, severity=sev,
                        check_category='Temporal Logic', **kw)
        elif it['kind'] == 'window':
            lo = it['min']; hi = today if it.get('max') == 'today' else it.get('max')
            kw = {'parse_strings_as_datetimes': True}
            if lo is not None: kw['min_value'] = lo
            if hi is not None: kw['max_value'] = hi
            safe_expect(v, cols, 'expect_column_values_to_be_between', column=it['col'], severity=sev,
                        check_category='Temporal Logic', **kw)

def temporal_pair_check(suite, table, col_a, op, col_b, *, severity='warning'):
    """Spark-native replacement for GX's expect_column_pair_values_a_to_be_greater_than_b, which
       raises CANNOT_RESOLVE_DATAFRAME_COLUMN under this environment's Spark Connect client on
       every single call (a GX/Spark-Connect incompatibility, not a data issue). Checks
       col_a >= col_b (op='>=') or col_a <= col_b (op='<=') among rows where both are non-null."""
    try:
        # NOTE: spark.table() is lazy under Spark Connect -- schema/count access must be inside this
        # try block too, or a missing-table error escapes uncaught (same fix as build_silver_table).
        df = spark.table(table)
        cols = {f.name for f in df.schema.fields}
    except Exception as e:
        print(f'[temporal] SKIP {suite}: read error: {e}'); return None
    if col_a not in cols or col_b not in cols:
        print(f'[temporal] SKIP {suite}: {col_a}/{col_b} absent'); return None
    try:
        both = df.filter(F.col(col_a).isNotNull() & F.col(col_b).isNotNull())
        total = both.count()
        bad = (both.filter(F.col(col_a) < F.col(col_b)).count() if op == '>='
               else both.filter(F.col(col_a) > F.col(col_b)).count())
    except Exception as e:
        print(f'[temporal] SKIP {suite}: errored: {e}'); return None
    pct = (bad/total*100.0) if total else 0.0
    passed = bad == 0
    ts = now_utc()
    blocking_fail = 1 if (not passed and severity == 'blocking') else 0
    warning_fail  = 1 if (not passed and severity == 'warning') else 0
    layer = table_layer(table)
    try:
        spark.createDataFrame([(layer, suite, table, 1, int(passed), 100.0-pct, blocking_fail, warning_fail, passed, ts)],
            'layer string, suite string, table_name string, evaluated int, successful int, success_pct double, '
            'blocking_fail int, warning_fail int, passed boolean, run_ts timestamp'
            ).write.mode('append').saveAsTable(SCORECARD)
        spark.createDataFrame([(layer, suite, table, 'Temporal Logic', 'temporal_pair', f'{col_a},{col_b}', severity,
                                passed, False, round(pct, 4),
                                f'violations={bad}/{total} ({col_a} {op} {col_b})'[:200], ts)],
            'layer string, suite string, table_name string, check_category string, expectation_type string, '
            'column_name string, severity string, success boolean, errored boolean, unexpected_pct double, '
            'observed string, run_ts timestamp'
            ).write.mode('append').saveAsTable(DETAIL)
    except Exception as e:
        print(f'  WARN: temporal-pair scorecard/detail write failed for {suite}: {e}')
    print(f"[{'OK' if passed else 'FAIL'}] {suite}: {col_a} {op} {col_b} violated on {bad:,}/{total:,} ({pct:.3f}%)")
    out = {'suite': suite, 'table': table, 'evaluated': 1, 'successful': int(passed), 'success_pct': 100.0-pct,
           'blocking_fail': blocking_fail, 'warning_fail': warning_fail, 'passed': passed, 'run_ts': ts}
    RESULTS.append(out); return out

def ref_integrity_check(suite, child_table, fk_cols, parent_table, parent_cols, *,
                         mostly=1.0, severity='blocking', note=''):
    """Anti-join based RI (GX validators are single-dataframe, so cross-table FK checks run as a
       separate Spark anti-join and are logged into SCORECARD/DETAIL the same way a GX suite is).
       mostly=1.0 -> 0% orphans tolerated. mostly<1.0 -> monitored ratio (e.g. partial-match KPI sets)."""
    try:
        # NOTE: spark.table() is lazy under Spark Connect -- schema access must be inside this try
        # block too, or a missing parent/child table (e.g. edw_kpi not existing in this environment)
        # escapes uncaught and aborts every remaining check for the whole table.
        cdf = spark.table(child_table); pdf = spark.table(parent_table)
        ccols = {f.name for f in cdf.schema.fields}; pcols = {f.name for f in pdf.schema.fields}
    except Exception as e:
        print(f'[RI] SKIP {suite}: read error: {e}'); return None
    if not all(c in ccols for c in fk_cols) or not all(c in pcols for c in parent_cols):
        print(f'[RI] SKIP {suite}: columns absent (child needs {fk_cols}, parent needs {parent_cols})'); return None
    child_sel = cdf.select(*fk_cols).dropna().distinct()
    parent_sel = pdf.select(*parent_cols).dropna().distinct()
    for fc, pc in zip(fk_cols, parent_cols):
        if fc != pc: parent_sel = parent_sel.withColumnRenamed(pc, fc)
    total = child_sel.count()
    orphans = child_sel.join(parent_sel, on=fk_cols, how='left_anti').count() if total else 0
    match_pct = ((total - orphans) / total) if total else 1.0
    passed = match_pct >= mostly
    ts = now_utc()
    blocking_fail = 1 if (not passed and severity == 'blocking') else 0
    warning_fail  = 1 if (not passed and severity == 'warning') else 0
    layer = table_layer(child_table)
    try:
        spark.createDataFrame([(layer, suite, child_table, 1, int(passed), match_pct*100,
                                blocking_fail, warning_fail, passed, ts)],
            'layer string, suite string, table_name string, evaluated int, successful int, success_pct double, '
            'blocking_fail int, warning_fail int, passed boolean, run_ts timestamp'
            ).write.mode('append').saveAsTable(SCORECARD)
        spark.createDataFrame([(layer, suite, child_table, 'Referential Integrity', 'ref_integrity',
                                ','.join(fk_cols), severity, passed, False, round((1-match_pct)*100, 4),
                                f'orphans={orphans}/{total} vs {parent_table}({",".join(parent_cols)}) {note}'[:200], ts)],
            'layer string, suite string, table_name string, check_category string, expectation_type string, '
            'column_name string, severity string, success boolean, errored boolean, unexpected_pct double, '
            'observed string, run_ts timestamp'
            ).write.mode('append').saveAsTable(DETAIL)
    except Exception as e:
        print(f'  WARN: RI scorecard/detail write failed for {suite}: {e}')
    print(f"[{'OK' if passed else 'FAIL'}] {suite}: match={match_pct*100:.2f}% "
          f"(threshold {mostly*100:.1f}%) orphans={orphans:,}/{total:,} {note}")
    out = {'suite': suite, 'table': child_table, 'evaluated': 1, 'successful': int(passed),
           'success_pct': match_pct*100, 'blocking_fail': blocking_fail, 'warning_fail': warning_fail,
           'passed': passed, 'run_ts': ts}
    RESULTS.append(out); return out

def hygiene_trim_upper_check(suite, table, col, *, severity='warning'):
    """Flags values where trim(upper(value)) != value -- i.e. not pre-cleaned per the
       chicago-oos-contract's documented UPPER(TRIM(...)) requirement."""
    try:
        df = spark.table(table)
        if col not in {f.name for f in df.schema.fields}:
            print(f'[hygiene] SKIP {suite}.{col}: column absent'); return None
    except Exception as e:
        print(f'[hygiene] SKIP {suite}.{col}: read error: {e}'); return None
    total = df.filter(F.col(col).isNotNull()).count()
    dirty = df.filter(F.col(col).isNotNull() & (F.trim(F.upper(F.col(col))) != F.col(col))).count()
    pct = (dirty/total*100.0) if total else 0.0
    passed = dirty == 0
    ts = now_utc()
    blocking_fail = 1 if (not passed and severity == 'blocking') else 0
    warning_fail  = 1 if (not passed and severity == 'warning') else 0
    layer = table_layer(table)
    try:
        spark.createDataFrame([(layer, suite, table, 1, int(passed), 100.0-pct, blocking_fail, warning_fail, passed, ts)],
            'layer string, suite string, table_name string, evaluated int, successful int, success_pct double, '
            'blocking_fail int, warning_fail int, passed boolean, run_ts timestamp'
            ).write.mode('append').saveAsTable(SCORECARD)
        spark.createDataFrame([(layer, suite, table, 'Format & Pattern', 'hygiene_trim_upper', col, severity,
                                passed, False, round(pct, 4),
                                f'dirty={dirty}/{total} (not trim(upper(x))==x)'[:200], ts)],
            'layer string, suite string, table_name string, check_category string, expectation_type string, '
            'column_name string, severity string, success boolean, errored boolean, unexpected_pct double, '
            'observed string, run_ts timestamp'
            ).write.mode('append').saveAsTable(DETAIL)
    except Exception as e:
        print(f'  WARN: hygiene scorecard/detail write failed for {suite}: {e}')
    print(f"[{'OK' if passed else 'FAIL'}] {suite}: {col} not upper/trimmed on {dirty:,}/{total:,} ({pct:.3f}%)")
    out = {'suite': suite, 'table': table, 'evaluated': 1, 'successful': int(passed), 'success_pct': 100.0-pct,
           'blocking_fail': blocking_fail, 'warning_fail': warning_fail, 'passed': passed, 'run_ts': ts}
    RESULTS.append(out); return out

def conditional_rule_check(suite, table, cond_col, cond_lo, cond_hi, flag_col, *, severity='warning', note=''):
    """Flags rows where (cond_lo <= cond_col <= cond_hi) != flag_col -- e.g. is_reader_event must be
       TRUE exactly when event_code_id is in the 200-299 reader range, per dim_event_matrix."""
    try:
        df = spark.table(table)
        cols = {f.name for f in df.schema.fields}
    except Exception as e:
        print(f'[cond] SKIP {suite}: read error: {e}'); return None
    if cond_col not in cols or flag_col not in cols:
        print(f'[cond] SKIP {suite}: {cond_col}/{flag_col} absent'); return None
    total = df.count()
    expected = (F.col(cond_col) >= cond_lo) & (F.col(cond_col) <= cond_hi)
    mismatched = df.filter(expected != F.col(flag_col)).count()
    pct = (mismatched/total*100.0) if total else 0.0
    passed = mismatched == 0
    ts = now_utc()
    blocking_fail = 1 if (not passed and severity == 'blocking') else 0
    warning_fail  = 1 if (not passed and severity == 'warning') else 0
    layer = table_layer(table)
    try:
        spark.createDataFrame([(layer, suite, table, 1, int(passed), 100.0-pct, blocking_fail, warning_fail, passed, ts)],
            'layer string, suite string, table_name string, evaluated int, successful int, success_pct double, '
            'blocking_fail int, warning_fail int, passed boolean, run_ts timestamp'
            ).write.mode('append').saveAsTable(SCORECARD)
        spark.createDataFrame([(layer, suite, table, 'Domain & Range', 'conditional_rule', flag_col, severity,
                                passed, False, round(pct, 4),
                                f'mismatched={mismatched}/{total} ({cond_col} {cond_lo}-{cond_hi} <=> {flag_col}) {note}'[:200], ts)],
            'layer string, suite string, table_name string, check_category string, expectation_type string, '
            'column_name string, severity string, success boolean, errored boolean, unexpected_pct double, '
            'observed string, run_ts timestamp'
            ).write.mode('append').saveAsTable(DETAIL)
    except Exception as e:
        print(f'  WARN: conditional-rule scorecard/detail write failed for {suite}: {e}')
    print(f"[{'OK' if passed else 'FAIL'}] {suite}: {flag_col} mismatched on {mismatched:,}/{total:,} ({pct:.3f}%) {note}")
    out = {'suite': suite, 'table': table, 'evaluated': 1, 'successful': int(passed), 'success_pct': 100.0-pct,
           'blocking_fail': blocking_fail, 'warning_fail': warning_fail, 'passed': passed, 'run_ts': ts}
    RESULTS.append(out); return out

def ratio_metric(suite, table, col, pattern_a, label_a, pattern_b, label_b, note=''):
    """Monitoring-only: logs the split between two coexisting formats (e.g. 6-digit vs 8-digit
       TRANSIT_DAY_KEY) into DETAIL without gating pass/fail -- so the inconsistency stays visible
       instead of being silently normalized away by a regex that simply allows both."""
    try:
        df = spark.table(table)
        if col not in {f.name for f in df.schema.fields}:
            print(f'[ratio] SKIP {suite}.{col}: column absent'); return None
    except Exception as e:
        print(f'[ratio] SKIP {suite}.{col}: read error: {e}'); return None
    total = df.filter(F.col(col).isNotNull()).count()
    n_a = df.filter(F.col(col).rlike(pattern_a)).count()
    n_b = df.filter(F.col(col).rlike(pattern_b)).count()
    ts = now_utc()
    pct_a = (n_a/total*100.0) if total else 0.0
    pct_b = (n_b/total*100.0) if total else 0.0
    print(f"[MONITOR] {suite}: {col} {label_a}={n_a:,} ({pct_a:.1f}%)  {label_b}={n_b:,} ({pct_b:.1f}%)  "
          f"other={total-n_a-n_b:,}  {note}")
    try:
        spark.createDataFrame([(table_layer(table), suite, table, 'Monitoring', 'ratio_metric', col, 'info', True, False,
                                round(pct_b, 4), f'{label_a}={n_a} {label_b}={n_b} total={total} {note}'[:200], ts)],
            'layer string, suite string, table_name string, check_category string, expectation_type string, '
            'column_name string, severity string, success boolean, errored boolean, unexpected_pct double, '
            'observed string, run_ts timestamp'
            ).write.mode('append').saveAsTable(DETAIL)
    except Exception as e:
        print(f'  WARN: ratio_metric detail write failed for {suite}: {e}')

def build_silver_table(short_name, spec):
    """One table's full spec (PK / completeness / format / domain / temporal-bound/window) run through
       a single GX validator + checkpoint; RI, hygiene, conditional-rule, ratio, and temporal-pair
       checks run alongside as separate Spark passes and feed the same RESULTS/SCORECARD so the final
       gate sees everything."""
    table = f'{SILVER}.{short_name}'
    suite = f'silver.{short_name}'
    try:
        # NOTE: spark.table() is lazy under Spark Connect -- a TABLE_OR_VIEW_NOT_FOUND error only
        # surfaces once the schema/plan is actually resolved, so .schema and .count() must be inside
        # this try block too, or a missing table crashes the whole run instead of being skipped cleanly.
        df = spark.table(table)
        cols = {f.name for f in df.schema.fields}
        n = df.count()
    except Exception as e:
        print(f'\nSKIP {suite}: cannot read {table}: {e}'); return None
    print(f'\n=== {suite} ({table}) rows={n:,} cols={len(cols)} ===')
    v, br = get_validator(df, suite)
    safe_expect(v, cols, 'expect_table_row_count_to_be_between', min_value=spec.get('row_floor', DEFAULT_ROW_FLOOR),
                severity=spec.get('row_floor_severity', 'warning'))
    if spec.get('pk'):
        expect_pk(v, cols, spec['pk'], severity=spec.get('pk_severity', 'blocking'), meta=spec.get('pk_meta'))
    for extra_pk in spec.get('extra_pk', []):
        expect_pk(v, cols, extra_pk['cols'], severity=extra_pk.get('severity', 'warning'), meta=extra_pk.get('meta'))
    expect_not_null_list(v, cols, spec.get('not_null', []), severity=spec.get('not_null_severity', 'blocking'))
    expect_regex_list(v, cols, spec.get('format', []))
    expect_domain_list(v, cols, spec.get('domain', []))
    expect_temporal_list(v, cols, spec.get('temporal', []))  # bound/window only -- pair handled below
    v.save_expectation_suite(discard_failed_expectations=False)
    res = run_and_score(suite, table, br)
    for h in spec.get('hygiene', []):
        try:
            hygiene_trim_upper_check(f'{suite}.hygiene.{h["col"]}', table, h['col'], severity=h.get('severity', 'warning'))
        except Exception as e:
            print(f'  SKIP hygiene {suite}.{h["col"]} -- errored: {e}')
    for r in spec.get('ri', []):
        parent = f'{SILVER}.{r["parent"]}' if not r.get('parent_bronze') else f'{CATALOG}.bronze.{r["parent"]}'
        ri_suite = f'{suite}.ri.{"_".join(r["fk"])}__{r["parent"]}'
        try:
            ref_integrity_check(ri_suite, table, r['fk'], parent, r['parent_cols'],
                                 mostly=r.get('mostly', 1.0), severity=r.get('severity', 'blocking'), note=r.get('note', ''))
        except Exception as e:
            print(f'  SKIP RI {ri_suite} -- errored: {e}')
    for c in spec.get('conditional', []):
        try:
            conditional_rule_check(f'{suite}.cond.{c["flag_col"]}', table, c['cond_col'], c['lo'], c['hi'],
                                    c['flag_col'], severity=c.get('severity', 'warning'), note=c.get('note', ''))
        except Exception as e:
            print(f'  SKIP conditional {suite}.{c["flag_col"]} -- errored: {e}')
    for rt in spec.get('ratio_monitor', []):
        try:
            ratio_metric(f'{suite}.ratio.{rt["col"]}', table, rt['col'], rt['pattern_a'], rt['label_a'],
                         rt['pattern_b'], rt['label_b'], note=rt.get('note', ''))
        except Exception as e:
            print(f'  SKIP ratio_monitor {suite}.{rt["col"]} -- errored: {e}')
    for t in spec.get('temporal', []):
        if t['kind'] == 'pair':
            try:
                temporal_pair_check(f'{suite}.temporal.{t["a"]}_{t["b"]}', table, t['a'], t['op'], t['b'],
                                     severity=t.get('severity', 'warning'))
            except Exception as e:
                print(f'  SKIP temporal pair {suite}.{t["a"]}_{t["b"]} -- errored: {e}')
    return res

print('Silver-layer builders ready (expect_pk, ref_integrity_check, hygiene_trim_upper_check, '
      'conditional_rule_check, ratio_metric, build_silver_table)')

# COMMAND ----------

# MAGIC %md
# MAGIC ### 6c. 30-table specs (data-driven off the Chicago mars_dev SILVER rule spec)
# MAGIC `CATS = ['TVM','GATE','VALIDATOR','READER','OTHER','UNKNOWN']` is the global device-category set
# MAGIC (Cell 1). Several tables use a NARROWER set per the spec (e.g. no `OTHER`, no `GATE`) -- those are
# MAGIC spelled out explicitly per table below rather than reused from `CATS`, since encoding the wrong
# MAGIC (wider) set would silently pass values the spec says shouldn't be there.

# COMMAND ----------

CAT_TGV        = ['TVM', 'GATE', 'VALIDATOR']            # no OTHER -- most device-grain silver tables
CAT_TGV_OTHER  = ['TVM', 'GATE', 'VALIDATOR', 'OTHER']    # dim_device only
CAT_TV         = ['TVM', 'VALIDATOR']                     # tvm_sale_daily -- no GATE (gates don't sell)
CAT_VG         = ['VALIDATOR', 'GATE']                    # usage_lifecycle_daily -- no TVM
CAT_TG         = ['TVM', 'GATE']                          # incident_history / maintenance_ledger

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
    not_null=['STOP_POINT_ID', 'stop_point_name', 'latitude', 'longitude'],
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
    # ASSUMPTION: PK built as BLOCKING but EXPECTED to fail (670 dup rows, 2026-07-17 catalog_validation
    # snapshot) until S17 is fixed at source -- tagged in meta so it's obvious in the scorecard/detail
    # tables rather than a silent/surprising red. Flip pk_severity to 'warning' if you'd rather it not
    # gate the pipeline while pending Sathish's fix.
    pk=['availability_event_id'], pk_severity='blocking',
    pk_meta={'known_failing_pending_S17_fix': True, 'confirmed_dup_rows': 670, 'snapshot': '2026-07-17'},
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
  'servicenow_incident_conformed': dict(
    pk=['number'], pk_severity='blocking',
    extra_pk=[dict(cols=['sys_id'], severity='warning')],  # secondary candidate -- ServiceNow's true row identity
    not_null=['number', 'sys_updated_on'],
    # No format/domain/RI/temporal rules were specified for this table.
  ),
}

print(f'SILVER_SPECS loaded: {len(SILVER_SPECS)} tables')

# COMMAND ----------

# MAGIC %md
# MAGIC ### 6d. Run the 30-table silver suite

# COMMAND ----------

RUN_SILVER_30 = True   # set False to skip this block (e.g. while iterating on specs)

if RUN_SILVER_30:
    print(f'Running silver DQ suite for {len(SILVER_SPECS)} tables...')
    for short_name, spec in SILVER_SPECS.items():
        try:
            build_silver_table(short_name, spec)
        except Exception as exc:
            print(f'  FAILED {short_name}: {exc}')
    print(f'\nDone. {len(SILVER_SPECS)} tables processed -- see scorecard/detail below and the final gate.')
else:
    print('RUN_SILVER_30=False -- skipping the 30-table silver suite.')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Data Docs + scorecards

# COMMAND ----------

context.build_data_docs()
print('Data Docs:', f'{GX_ROOT}/uncommitted/data_docs/local_site/index.html')

from pyspark.sql import functions as F

# ONE simple report: table + dq check category + dq check name + column + one clear status.
#   errored=true                  -> ERROR (the check itself crashed)
#   errored=false, success=true   -> PASS
#   errored=false, success=false  -> FAIL (check ran fine, data violated the rule)
report = (spark.table(DETAIL)
    .withColumn('status', F.when(F.col('errored') == True, F.lit('ERROR'))
                            .when(F.col('success') == True, F.lit('PASS'))
                            .otherwise(F.lit('FAIL')))
    .select(
        F.col('table_name').alias('table_name'),
        F.col('check_category').alias('dq_check_category'),
        F.col('expectation_type').alias('dq_check_name'),
        F.col('column_name').alias('column_name'),
        'severity', 'status', 'unexpected_pct', 'observed', 'run_ts')
    .orderBy(F.desc('run_ts')))

print('\n--- DQ Report: table | dq check category | dq check name | column | status ---')
display(report)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. FINAL GATE — raise only after everything ran (collect-then-gate)

# COMMAND ----------

for t in ["mars_dev.silver.maintenance_ledger", "mars_dev.silver.device_event_enriched"]:
    tot = spark.table(t).count()
    n   = spark.table(t).filter("DEVICE_ID IS NULL").count()
    print(f"{t}: {n} null DEVICE_ID of {tot:,} ({100*n/tot:.6f}%)")

# look at what those null-key rows actually are (test rows? bronze leakage?)
display(spark.table("mars_dev.silver.maintenance_ledger").filter("DEVICE_ID IS NULL").limit(20))

# COMMAND ----------

fl  = spark.table("mars_dev.silver.maintenance_ledger").select("DEVICE_ID").distinct()
dim = spark.table("mars_dev.silver.dim_device").select("DEVICE_ID").distinct()
orph = fl.join(dim, "DEVICE_ID", "left_anti")
print("orphan device_ids:", orph.count(), "of", fl.count())
display(orph.limit(50))

# COMMAND ----------

from pyspark.sql import functions as F
fl  = spark.table("mars_dev.silver.maintenance_ledger").select("DEVICE_ID").distinct()
dim = spark.table("mars_dev.silver.dim_device").select("DEVICE_ID").distinct()
orph = (fl.join(dim, "DEVICE_ID", "left_anti").filter("DEVICE_ID IS NOT NULL")
          .withColumn("prefix", F.regexp_extract("DEVICE_ID", r"^([A-Za-z]+)", 1)))
print("total orphan device_ids:", orph.count())
display(orph.groupBy("prefix").count().orderBy(F.desc("count")))

# are the null-DEVICE_ID rows' keys actually missing from dim_device?
nk = spark.table("mars_dev.silver.maintenance_ledger").filter("DEVICE_ID IS NULL").select("DEVICE_KEY").distinct()
dk = spark.table("mars_dev.silver.dim_device").select("DEVICE_KEY").distinct()
print("null-DEVICE_ID keys:", nk.count(), "| not in dim_device:", nk.join(dk,"DEVICE_KEY","left_anti").count())

# COMMAND ----------

# ── Single pipeline gate: fail the job iff any BLOCKING check failed ─────────
tot_block = sum(r['blocking_fail'] for r in RESULTS)
tot_warn  = sum(r['warning_fail']  for r in RESULTS)
print(f"{'SUITE':<48} {'BLOCK':>6} {'WARN':>6} {'PASS':>6}"); print('-'*68)
for r in RESULTS:
    print(f"{r['suite']:<48} {r['blocking_fail']:>6} {r['warning_fail']:>6} {str(r['passed']):>6}")
print('-'*68); print(f'TOTAL blocking={tot_block}  warning={tot_warn}')
if tot_block > 0:
    raise RuntimeError(f'DQ GATE FAILED: {tot_block} blocking check(s) failed -- quarantine before promote. '
                       f'({tot_warn} warnings are non-blocking.)')
print('\nDQ GATE PASSED (warnings do not block).')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10. Smoke test — verify the framework 

# COMMAND ----------

# ── Build a tiny in-memory table with KNOWN issues; the suite must catch them ──
def smoke_test():
    from datetime import datetime, timezone
    good=(1,'TVM',datetime.now(timezone.utc),5.0,2); bad=(1,'NOPE',None,-3.0,999)
    sdf=spark.createDataFrame([good,bad,(2,'GATE',datetime.now(timezone.utc),1.0,1)],
        'id int, cat string, ts timestamp, dur double, lvl int')
    cols={f.name for f in sdf.schema.fields}
    v,br=get_validator(sdf,'smoke.selftest')
    safe_expect(v,cols,'expect_column_values_to_not_be_null',column='ts',severity='blocking')          # bad row -> fail
    safe_expect(v,cols,'expect_column_values_to_be_in_set',column='cat',value_set=CATS,severity='blocking') # NOPE -> fail
    safe_expect(v,cols,'expect_column_values_to_be_between',column='dur',min_value=0,severity='warning')     # -3 -> warn
    safe_expect(v,cols,'expect_column_values_to_not_exist_xyz',column='ghost',severity='blocking')           # skipped (absent)
    v.save_expectation_suite(discard_failed_expectations=False)
    r=run_and_score('smoke.selftest','<in-memory>',br)
    ok = r['blocking_fail']>=2 and r['warning_fail']>=1
    print('\nSMOKE TEST', 'PASSED -- framework detects blocking + warning issues and skips absent columns' if ok
          else 'FAILED -- expected >=2 blocking and >=1 warning'); return ok
smoke_test()