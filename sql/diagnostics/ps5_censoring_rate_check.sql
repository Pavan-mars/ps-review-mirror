-- =============================================================================
-- DIAGNOSTIC: PS5 censoring-rate check
-- Added: 2026-07-07, validation pass (Chicago silver/gold rebuild review)
--
-- WHY THIS EXISTS:
-- gold.device_ps5_component__create.sql already contains this exact verification
-- query, commented out at the bottom of the file. It has never been run against
-- the live table as far as this review found. This is the single highest-value,
-- lowest-effort check outstanding from that review: PS5 was retracted once before
-- (2026-06) when its survival label turned out to be ~100% censored because
-- install_date/expected_lifespan were 100% NULL. The 2026-07-07 rebuild moved the
-- age source to silver.hw_config_current.REPORTED_CHANGED_DTM (a different,
-- previously-unused field) specifically to avoid that dead end -- but nothing in
-- the rebuild confirms the fix actually worked. Run this before telling Cubic PS5
-- is unblocked.
--
-- HOW TO READ THE RESULTS:
-- has_failures = 0 (or near-0) for a category -> PS5 is back to square one for that
-- category, same failure mode as before, despite the improved design.
-- has_failures in a workable range (even 5-15% of total_rows) -> PS5 has a usable
-- event rate for a first Weibull/Cox pass, genuinely unblocked for the first time
-- in the project's history.
-- distinct_devices much smaller than the known in-scope fleet size for that
-- category (TVM ~4,512 / GATE ~2,342 / VALIDATOR ~2,000 per gold.device_ps1_daily's
-- header) -> the component-serial-number/age-source population is thin; see
-- hw_config_null_rate_profile.sql in this same folder to find out why.
-- =============================================================================

SELECT
mars_device_category,
COUNT(*) AS total_rows,
COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
SUM(CASE WHEN is_censored = FALSE THEN 1 END) AS has_failures,
SUM(CASE WHEN is_censored = TRUE THEN 1 END) AS censored,
ROUND(
  SUM(CASE WHEN is_censored = FALSE THEN 1 ELSE 0 END) / COUNT(*) * 100, 2
) AS event_rate_pct,
ROUND(AVG(component_age_days), 0) AS avg_age_days,
ROUND(AVG(days_to_failure), 0) AS avg_days_to_failure,
ROUND(AVG(failures_total), 2) AS avg_failures_per_component,
ROUND(AVG(mtbf_days), 0) AS avg_mtbf_days,
SUM(CASE WHEN COMPONENT_TYPE_NAME IS NOT NULL THEN 1 END) AS has_component_type
FROM mars_dev.gold.device_ps5_component
GROUP BY mars_device_category
ORDER BY mars_device_category;

-- Follow-up if event_rate_pct comes back near 0 for a category: check whether that
-- category's rows even have a populated COMPONENT_SERIAL_NBR / REPORTED_CHANGED_DTM
-- to begin with, via hw_config_null_rate_profile.sql -- a low event rate could mean
-- "these components genuinely don't fail often" (fine) or "the component universe
-- itself is too thin to observe failures in" (the same problem as before, recurring
-- via a new field). The two look identical in this query alone; the null-rate
-- profile is what tells them apart.
