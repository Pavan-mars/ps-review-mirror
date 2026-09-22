# =============================================================================
# PS1 completeness-guard self-test -- 23-Sep-2026
#
# Pure Python. No Spark, no S3, no credentials. Run it anywhere:
#
#     python ps1_completeness_guard_selftest.py
#
# WHY THIS EXISTS
#   The guard in ps1_features.py changes the sessionisation rule's UNIT from calendar
#   days to OBSERVED days. That is a one-line change to a predicate that decides every
#   positive in the PS1 label, and getting it subtly wrong is silent: no error, no
#   schema change, just a different number of episode starts.
#
#   This models both rules in a few lines of Python and asserts the properties the
#   Spark implementation must have. It cannot catch a Spark-specific defect (a NULL
#   join, a window without a partition), but it pins the ARITHMETIC, which is where
#   the design review found the blocking errors.
#
# THE PROPERTIES
#   1. When every day is observed the guard is BIT-IDENTICAL to today's rule.
#   2. The guard is a PURE SUPPRESSOR -- it can never mint an episode start.
#   3. A FEED GAP no longer mints a start.
#   4. A GENUINE quiet period still does.  (3 and 4 together are the whole point:
#      if a rule cannot tell these apart it is worthless.)
#   5. The merge hazard is real and is the cost of the guard -- one day wrongly
#      marked unobserved inside a gap of exactly GAP+1 MERGES two genuine episodes.
#      That is a lost true positive, which is worse than the manufactured start it
#      is trying to remove, since manufactured starts are orthogonal to device risk
#      and merely dilute. This test documents the hazard rather than fixing it.
#   6. The lookback stops the window boundary minting a start (2023-07 carries 557
#      of these).
# =============================================================================
import datetime as dt
import random

GAP = 3                      # PS1_EVENT_SESSION_GAP_DAYS
D0 = dt.date(2025, 1, 1)


def calendar_rule(fails, gap=GAP):
    """Today's rule: keep a failure day with no predecessor, or a CALENDAR gap > gap."""
    keep, prev = [], None
    for d in sorted(fails):
        if prev is None or (d - prev).days > gap:
            keep.append(d)
        prev = d
    return keep


def observed_rule(fails, observed, cal_start, cal_end, gap=GAP):
    """The guard: an index that advances only on OBSERVED days.

    The escape is on PREDECESSOR EXISTENCE (prev is None), never on the arithmetic.
    A NULL in the arithmetic would make the predicate NULL, and Spark's `where` keeps
    only TRUE -- so the row would be silently DROPPED rather than kept. That inversion
    was the blocking defect the design review caught.
    """
    idx, run, d = {}, 0, cal_start
    while d <= cal_end:
        run += 1 if d in observed else 0
        idx[d] = run
        d += dt.timedelta(days=1)

    keep, prev = [], None
    for d in sorted(fails):
        if d not in idx:
            raise AssertionError(
                f"failure day {d} lies outside the calendar [{cal_start}..{cal_end}] -- "
                "obs_idx would be NULL and the start would vanish without trace")
        if prev is None or (idx[d] - idx[prev]) > gap:
            keep.append(d)
        prev = d
    return keep


def _days(a, b):
    return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]


def main():
    random.seed(7)
    cal_start, cal_end = D0, D0 + dt.timedelta(days=200)
    all_days = _days(cal_start, cal_end)
    failures = []

    # 1 -- equivalence when nothing is missing ------------------------------
    bad = 0
    for _ in range(400):
        f = sorted(random.sample(all_days, random.randint(1, 40)))
        if calendar_rule(f) != observed_rule(f, set(all_days), cal_start, cal_end):
            bad += 1
    if bad:
        failures.append(f"1. all-observed equivalence broke on {bad}/400 trials")
    print(f"1. bit-identical when fully observed  {'PASS' if not bad else 'FAIL'}  (400 trials)")

    # 2 -- pure suppressor ---------------------------------------------------
    viol = 0
    for _ in range(2000):
        f = sorted(random.sample(all_days, random.randint(1, 40)))
        drop = random.choice([0.0, 0.1, 0.4, 0.8])
        obs = {d for d in all_days if random.random() > drop}
        if not set(observed_rule(f, obs, cal_start, cal_end)) <= set(calendar_rule(f)):
            viol += 1
    if viol:
        failures.append(f"2. guard minted starts on {viol}/2000 trials -- it must only suppress")
    print(f"2. pure suppressor, never mints       {'PASS' if not viol else 'FAIL'}  (2000 trials)")

    # 3 + 4 -- the discriminator --------------------------------------------
    # Same failure dates either way. The ONLY difference is whether the quiet
    # stretch was dark in the feed or genuinely quiet on a reporting device.
    f = [D0, D0 + dt.timedelta(days=1), D0 + dt.timedelta(days=20), D0 + dt.timedelta(days=21)]
    dark = {D0 + dt.timedelta(days=i) for i in range(2, 20)}
    n_gap = len(observed_rule(f, set(all_days) - dark, cal_start, cal_end))
    n_real = len(observed_rule(f, set(all_days), cal_start, cal_end))
    n_cal = len(calendar_rule(f))
    if not (n_cal == 2 and n_gap == 1 and n_real == 2):
        failures.append(f"3/4. discriminator broke: calendar={n_cal} gap={n_gap} genuine={n_real}")
    print(f"3. an 18-day FEED GAP mints nothing   {'PASS' if n_gap == 1 else 'FAIL'}  "
          f"(calendar rule {n_cal} -> guard {n_gap})")
    print(f"4. a genuine 18-day quiet keeps both  {'PASS' if n_real == 2 else 'FAIL'}  "
          f"(guard {n_real})")

    # 5 -- the merge hazard, documented not fixed ---------------------------
    f = [D0, D0 + dt.timedelta(days=GAP + 1)]
    clean = len(observed_rule(f, set(all_days), cal_start, cal_end))
    one_dark = len(observed_rule(f, set(all_days) - {D0 + dt.timedelta(days=2)},
                                 cal_start, cal_end))
    if not (clean == 2 and one_dark == 1):
        failures.append(f"5. merge hazard model wrong: clean={clean} one_dark={one_dark}")
    print(f"5. merge hazard is real               {'PASS' if one_dark == 1 else 'FAIL'}  "
          f"(gap of exactly {GAP + 1}d: clean {clean}, one day dark {one_dark} = MERGED)")
    print( "   ^ this is the guard's COST. Every day wrongly marked unobserved inside a")
    print( "     minimum-length gap destroys a true positive. Keep the floor low, count")
    print( "     exactly, and watch the by-gap-length breakdown the guard prints.")

    # 6 -- the left edge -----------------------------------------------------
    win = D0 + dt.timedelta(days=50)
    pair = [win - dt.timedelta(days=1), win + dt.timedelta(days=1)]
    minted = len(calendar_rule([d for d in pair if d >= win]))
    with_lb = len([d for d in calendar_rule(pair) if d >= win])
    if not (minted == 1 and with_lb == 0):
        failures.append(f"6. lookback model wrong: minted={minted} with_lookback={with_lb}")
    print(f"6. lookback kills boundary-minting    {'PASS' if with_lb == 0 else 'FAIL'}  "
          f"(no lookback {minted} start, with lookback {with_lb})")

    print()
    if failures:
        for msg in failures:
            print(f"  FAIL  {msg}")
        raise SystemExit(1)
    print("  all properties hold")


if __name__ == "__main__":
    main()
