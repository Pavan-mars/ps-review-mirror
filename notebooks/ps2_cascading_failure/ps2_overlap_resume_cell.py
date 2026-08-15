# =============================================================================
# RESUME CELL -- run this INSTEAD of re-running cell 2.
#
# og_raw is already loaded and cached from the failed run, so this picks up at
# A.0 and skips the six-minute S3 read. The only change is that max_outage_end
# is collected as TEXT: it holds year-3000 sentinels, and pandas timestamp[ns]
# cannot represent anything past 2262-04-11, which is what raised ArrowInvalid.
# =============================================================================
if 'og_raw' not in dir():
    raise RuntimeError('og_raw is not in the session; re-run cell 2 of the updated notebook instead.')

# A.0 -- how far past the window do intervals run, and are there sentinels?
print('A.0  Unbounded outage_end values (this is what crashed the previous run)')
tail = (
    og_raw.groupBy('fleet').agg(
        F.count('*').alias('rows'),
        F.sum(F.when(F.col('raw_end') > F.lit(WIN_END), 1).otherwise(0)).alias('end_after_window'),
        F.sum(F.when(F.col('raw_end') > F.lit(WIN_END) + F.expr('INTERVAL 365 DAYS'), 1).otherwise(0)).alias('end_over_1_year_past'),
        # pandas timestamp[ns] tops out at 2262-04-11 and this column holds
        # year-3000 sentinels, so it is collected as text, never as a timestamp.
        F.date_format(F.max('raw_end'), 'yyyy-MM-dd HH:mm:ss').alias('max_outage_end'),
        F.expr('percentile_approx((unix_timestamp(raw_end) - unix_timestamp(raw_start)) / 86400.0, 0.999)').alias('p999_span_days'),
        F.max((F.col('raw_end').cast('long') - F.col('raw_start').cast('long')) / 86400.0).alias('max_span_days'),
    ).toPandas()
)
print(tail.sort_values('fleet').to_string(index=False))
print()

# Clamp to the window. Measuring downtime inside the window means counting only
# the part of each interval that falls inside it.
og = (
    og_raw
    .withColumn('start_ts', F.greatest('raw_start', F.lit(WIN_START)))
    .withColumn('end_ts', F.least('raw_end', F.lit(WIN_END)))
    .where(F.col('end_ts') >= F.col('start_ts'))
    .withColumn('span_min', (F.col('end_ts').cast('long') - F.col('start_ts').cast('long')) / 60.0)
    .persist(StorageLevel.DISK_ONLY)
)
n_rows = og.count()
print(f'device_outage rows in window: {n_rows:,} (of {n_raw:,}; '
      f'{n_raw - n_rows:,} dropped because the clamped interval inverted)')
print()

# ---------------------------------------------------------------- A: duration vs span
print('A.1  Does duration_min agree with (outage_end - outage_start), after clamping?')
agree = (
    og.groupBy('fleet').agg(
        F.count('*').alias('episodes'),
        F.sum(F.when(F.abs(F.col('dur_min') - F.col('span_min')) <= 0.5, 1).otherwise(0)).alias('agree_within_30s'),
        F.sum(F.when(F.col('dur_min') > F.col('span_min') + 0.5, 1).otherwise(0)).alias('duration_gt_span'),
        F.sum(F.when(F.col('dur_min') < F.col('span_min') - 0.5, 1).otherwise(0)).alias('duration_lt_span'),
        F.expr('percentile_approx(span_min - dur_min, 0.5)').alias('p50_span_minus_duration'),
        F.max(F.col('span_min') - F.col('dur_min')).alias('max_span_minus_duration'),
        F.sum('dur_min').alias('sum_duration_min'),
        F.sum('span_min').alias('sum_span_min'),
    ).toPandas()
)
agree['pct_agree'] = (100.0 * agree['agree_within_30s'] / agree['episodes']).round(2)
print(agree.sort_values('fleet').to_string(index=False))
print()

print('A.2  The 7-day cap, and exact-duplicate intervals')
dup = (
    og.groupBy('fleet', 'device_id', 'start_ts', 'end_ts').agg(F.count('*').alias('n'))
    .groupBy('fleet').agg(
        F.count('*').alias('distinct_intervals'),
        F.sum('n').alias('rows'),
        F.sum(F.when(F.col('n') > 1, F.col('n') - 1).otherwise(0)).alias('duplicate_rows'),
        F.max('n').alias('max_rows_on_one_interval'),
    ).toPandas()
)
cap = (
    og.groupBy('fleet').agg(
        F.sum(F.when(F.col('dur_min') >= 10080.0, 1).otherwise(0)).alias('at_7_day_cap'),
        F.sum(F.when(F.col('span_min') > 10080.0, 1).otherwise(0)).alias('span_over_7_days'),
        F.sum(F.when(F.col('dur_min') == 0.0, 1).otherwise(0)).alias('zero_duration'),
    ).toPandas()
)
capdup = dup.merge(cap, on='fleet')
capdup['pct_duplicate_rows'] = (100.0 * capdup['duplicate_rows'] / capdup['rows']).round(2)
print(capdup.sort_values('fleet').to_string(index=False))
print()

# ---------------------------------------------------------------- A.3: what ARE the duplicates
# v3 ADDITION, prompted by the first run: 55.4% of TVM outage rows share an exact
# (device, start, end) with another row, one interval carrying up to 189 rows.
# Whether that is 189 genuinely distinct component records that happen to share
# timing, or the same episode written 189 times, decides whether the fix is ours
# or Cubic's. source_event_id was already shown to be unique, so the question is
# what else differs.
print('A.3  Exact-duplicate intervals: what distinguishes the rows sharing one interval?')
def _o(name, alias):
    return (F.coalesce(F.col(name).cast('string'), F.lit('(null)')).alias(alias)
            if name in og.columns else F.lit('(not projected)').alias(alias))

dup_detail = (
    og.select('fleet', 'device_id', 'start_ts', 'end_ts',
              _o('component_subsystem', 'subsystem'),
              _o('COMPONENT_SERIAL_NBR', 'serial'),
              _o('source_event_id', 'event_id'),
              _o('failure_level', 'failure_level'))
    .groupBy('fleet', 'device_id', 'start_ts', 'end_ts')
    .agg(F.count('*').alias('rows_on_interval'),
         F.countDistinct('subsystem').alias('n_subsystems'),
         F.countDistinct('serial').alias('n_serials'),
         F.countDistinct('event_id').alias('n_event_ids'),
         F.countDistinct('failure_level').alias('n_failure_levels'))
    .where(F.col('rows_on_interval') > 1)
    .persist(StorageLevel.DISK_ONLY)
)
dup_profile = (
    dup_detail.groupBy('fleet').agg(
        F.count('*').alias('duplicated_intervals'),
        F.sum('rows_on_interval').alias('rows_involved'),
        F.sum(F.when(F.col('n_subsystems') > 1, 1).otherwise(0)).alias('differ_by_subsystem'),
        F.sum(F.when(F.col('n_serials') > 1, 1).otherwise(0)).alias('differ_by_serial'),
        F.sum(F.when(F.col('n_failure_levels') > 1, 1).otherwise(0)).alias('differ_by_failure_level'),
        F.sum(F.when((F.col('n_subsystems') == 1) & (F.col('n_serials') == 1)
                     & (F.col('n_failure_levels') == 1), 1).otherwise(0)).alias('indistinguishable_apart_from_event_id'),
        F.max('rows_on_interval').alias('max_rows_on_one_interval'),
    ).toPandas()
)
for column in ('differ_by_subsystem', 'differ_by_serial', 'differ_by_failure_level',
               'indistinguishable_apart_from_event_id'):
    dup_profile['pct_' + column] = (
        100.0 * dup_profile[column] / dup_profile['duplicated_intervals'].clip(lower=1)).round(2)
print(dup_profile.sort_values('fleet').to_string(index=False))
print()
print('A.3b  The ten worst-duplicated intervals')
print(dup_detail.orderBy(F.desc('rows_on_interval')).limit(10).toPandas().to_string(index=False))
print()
dup_detail.unpersist()

# ---------------------------------------------------------------- B: the union
print('B.1  Merging overlapping intervals per device (the decisive test)')
w_dev = Window.partitionBy('fleet', 'device_id').orderBy('start_ts', 'end_ts')
# The block id is attached to the ORIGINAL rows and kept. Cell 3 then derives
# both the block composition and the per-day split from this one frame, with no
# range join anywhere.
og_blocked = (
    og.select('fleet', 'device_id', 'start_ts', 'end_ts', 'dur_min', 'span_min',
              *[c for c in ('component_subsystem', 'COMPONENT_SERIAL_NBR', 'source_event_id') if c in og.columns])
    .withColumn('_prior_max_end', F.max('end_ts').over(w_dev.rowsBetween(Window.unboundedPreceding, -1)))
    .withColumn('_is_new_block',
                F.when(F.col('_prior_max_end').isNull(), F.lit(1))
                 .when(F.col('start_ts') > F.col('_prior_max_end'), F.lit(1))
                 .otherwise(F.lit(0)))
    .withColumn('_block', F.sum('_is_new_block').over(w_dev.rowsBetween(Window.unboundedPreceding, Window.currentRow)))
    .drop('_prior_max_end', '_is_new_block')
    .persist(StorageLevel.DISK_ONLY)
)
og_blocked.count()

merged = (
    og_blocked.groupBy('fleet', 'device_id', '_block')
    .agg(F.min('start_ts').alias('block_start'),
         F.max('end_ts').alias('block_end'),
         F.count('*').alias('episodes_in_block'),
         F.sum('dur_min').alias('summed_duration_min'))
    .withColumn('block_minutes', (F.col('block_end').cast('long') - F.col('block_start').cast('long')) / 60.0)
    .persist(StorageLevel.DISK_ONLY)
)
n_blocks = merged.count()
print(f'merged downtime blocks: {n_blocks:,} (from {n_rows:,} episodes)')
print()

union_by_fleet = (
    merged.groupBy('fleet').agg(
        F.countDistinct('device_id').alias('devices'),
        F.count('*').alias('merged_blocks'),
        F.sum('block_minutes').alias('union_minutes'),
        F.sum('summed_duration_min').alias('summed_minutes'),
        F.max('episodes_in_block').alias('max_episodes_in_one_block'),
        F.expr('percentile_approx(episodes_in_block, 0.5)').alias('p50_episodes_per_block'),
        F.expr('percentile_approx(episodes_in_block, 0.99)').alias('p99_episodes_per_block'),
        F.max('block_minutes').alias('longest_block_minutes'),
    ).toPandas()
)
union_by_fleet['overlap_factor'] = (union_by_fleet['summed_minutes'] / union_by_fleet['union_minutes']).round(3)
union_by_fleet['minutes_double_counted'] = union_by_fleet['summed_minutes'] - union_by_fleet['union_minutes']
union_by_fleet['pct_of_sum_that_is_double_counted'] = (
    100.0 * union_by_fleet['minutes_double_counted'] / union_by_fleet['summed_minutes']).round(2)
print(union_by_fleet.sort_values('fleet').to_string(index=False))
print()
og_raw.unpersist()
