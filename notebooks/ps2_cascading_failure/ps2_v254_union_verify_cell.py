# =============================================================================
# PS2 v2.5.4 -- UNION VERIFICATION
#
# WHERE THIS GOES. Paste at the END of Cell 11, immediately after the
# `daily_oos_trend = (daily_oos_trend .join(fleet_day_union ...))` block
# (around line 312) and before `affected_device_day = (`. Everything it reads
# exists by that point: device_day_union, fleet_day_union and the rebuilt
# daily_oos_trend.
#
# WHY IT IS NEEDED. The v2.5.4 run published all 20 tables with
# ceiling breaches=0, and NOT ONE of the three new columns appears anywhere in
# 5,632 lines of output. The ceiling guard proves the union was COMPUTED. It
# says nothing about whether the values are RIGHT.
#
# The failure this catches is the quiet one. If the interval merge silently
# degrades to "sum the episodes", every overlap factor comes back at 1.0, the
# ceiling guard still passes (a sum under 1440 breaches nothing), all 20 tables
# still publish, the loader still succeeds -- and the dashboard hero gets
# repointed at a column holding exactly the wrong number it was built to
# replace. Nothing downstream would notice.
#
# The expected values are not invented. They are the fleet medians the overlap
# diagnostic measured on this same 210-day window:
#     GATE 4.63    TVM 2.53    VALIDATOR 2.05
# Read-only. Adds no column, writes nothing, changes no frame.
# =============================================================================
print('\n' + '=' * 78)
print('UNION VERIFICATION -- does the interval merge actually differ from the sum?')
print('=' * 78)

_expected = {'GATE': 4.63, 'TVM': 2.53, 'VALIDATOR': 2.05}

# 1. Fleet-level overlap factor, weighted the way the dashboard will read it:
#    total component-burden minutes over total real downtime minutes.
_fleet_check = (
    daily_oos_trend.groupBy('device_category')
    .agg(
        F.round(F.sum('hardware_oos_minutes') / 60.0, 1).alias('burden_hours'),
        F.round(F.sum('hardware_oos_union_minutes') / 60.0, 1).alias('downtime_hours'),
        F.round(F.sum('hardware_oos_minutes')
                / F.greatest(F.sum('hardware_oos_union_minutes'), F.lit(1.0)), 3
                ).alias('overlap_factor_weighted'),
        F.round(F.expr('percentile_approx(oos_minutes_overlap_factor, 0.5)'), 3
                ).alias('overlap_factor_median_day'),
        F.count(F.lit(1)).alias('fleet_days'),
    )
    .orderBy('device_category')
)
_fleet_rows = _fleet_check.collect()
_fleet_check.show(truncate=False)

# 2. The verdict, stated rather than left for a reader to infer.
print(f"{'fleet':12s} {'weighted':>9s} {'expected':>9s} {'ratio':>7s}  verdict")
_bad = []
for _r in _fleet_rows:
    _cat = _r['device_category']
    _got = float(_r['overlap_factor_weighted'] or 0.0)
    _exp = _expected.get(_cat)
    if _exp is None:
        print(f'{_cat:12s} {_got:9.3f} {"--":>9s} {"--":>7s}  no diagnostic baseline')
        continue
    _ratio = _got / _exp if _exp else 0.0
    if _got < 1.05:
        _v = 'FAILED -- union == sum, the merge is not being applied'
        _bad.append(_cat)
    elif 0.75 <= _ratio <= 1.33:
        _v = 'ok -- matches the diagnostic'
    else:
        _v = 'CHECK -- real but far from the diagnostic; do not load blind'
        _bad.append(_cat)
    print(f'{_cat:12s} {_got:9.3f} {_exp:9.2f} {_ratio:7.2f}x  {_v}')

# 3. An overlap factor below 1.0 is arithmetically impossible: a union of
#    intervals can never exceed their sum. If this fires, the two measures are
#    being built on different bases -- which is exactly the defect that made
#    v3 of the overlap diagnostic invalid before the clamp was added.
_impossible = daily_oos_trend.where(
    (F.col('hardware_oos_union_minutes') > 0)
    & (F.col('oos_minutes_overlap_factor') < 0.999)
).count()
print(f'\nfleet-days with overlap_factor < 1.0 (impossible): {_impossible:,}')
if _impossible:
    _bad.append('impossible_ratio')
    daily_oos_trend.where(
        (F.col('hardware_oos_union_minutes') > 0)
        & (F.col('oos_minutes_overlap_factor') < 0.999)
    ).select('event_date', 'device_category', 'hardware_oos_minutes',
             'hardware_oos_union_minutes', 'oos_minutes_overlap_factor'
             ).orderBy('oos_minutes_overlap_factor').show(5, truncate=False)

# 4. The distinct-interval onset count must not exceed the raw onset count.
_onset_check = (
    device_day_union.join(device_day_onset_dedup, ['device_id', 'event_date'], 'inner')
    .agg(
        F.count(F.lit(1)).alias('device_days'),
        F.round(F.avg('hardware_oos_onsets_distinct_interval'), 2).alias('mean_distinct_onsets'),
        F.max('hardware_oos_onsets_distinct_interval').alias('max_distinct_onsets'),
    )
).collect()[0]
print(f'\ndistinct-interval onsets: {_onset_check["device_days"]:,} device-days, '
      f'mean {_onset_check["mean_distinct_onsets"]}, max {_onset_check["max_distinct_onsets"]}')

# 5. What the dashboard hero will actually say, computed here so the number is
#    known BEFORE it reaches a screen rather than after.
_days = daily_oos_trend.select('event_date').distinct().count()
print(f'\nAvailability the hero will show, over {_days} days:')
print(f'  {"fleet":12s} {"published now":>14s} {"after v2.5.4":>13s}')
for _r in _fleet_rows:
    _cat = _r['device_category']
    _n = _device_entity.where(F.col('device_category') == _cat).count()
    _cap = _n * _days * 24.0
    if _cap > 0:
        print(f'  {_cat:12s} {float(_r["burden_hours"]) / _cap * 100:13.2f}% '
              f'{float(_r["downtime_hours"]) / _cap * 100:12.2f}%')

print('\n' + '=' * 78)
if _bad:
    print(f'VERDICT: DO NOT LOAD -- {sorted(set(_bad))}')
    print('The union is not producing what the diagnostic measured. Loading this and')
    print('repointing the hero would replace a wrong number with a different wrong one.')
else:
    print('VERDICT: union confirmed. Safe to load and repoint the availability hero.')
print('=' * 78)
