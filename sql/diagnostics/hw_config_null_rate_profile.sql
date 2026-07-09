-- =============================================================================
-- DIAGNOSTIC: hw_config_current null-rate profile (COMPONENT_SERIAL_NBR,
-- REPORTED_CHANGED_DTM, LAST_REPORTED_DTM)
-- Added: 2026-07-07, validation pass (Chicago silver/gold rebuild review)
--
-- WHY THIS EXISTS:
-- gold.device_ps5_component's entire component universe starts from
-- silver.hw_config_current filtered to COMPONENT_SERIAL_NBR IS NOT NULL, and its
-- age/censoring-origin field is component_age_days, itself derived from
-- REPORTED_CHANGED_DTM (falling back to LAST_REPORTED_DTM). Neither
-- 09_hw_config_current__create.sql nor any downstream file states what fraction
-- of the underlying EDW.DEVICE_CURRENT_HW_CONFIG rows actually have these fields
-- populated -- it only says they "may be NULL for legacy components." A prior,
-- separate PS5 attempt was retracted specifically because a different age source
-- (ServiceNow CMDB install_date/expected_lifespan) turned out to be ~100% NULL.
-- This query answers the equivalent question for the NEW source before anyone
-- treats PS5's rebuild as proven.
--
-- HOW TO READ THE RESULTS:
-- pct_null_serial high (the source project record for the *ServiceNow CMDB*
-- COMPONENT_SERIAL_NBR was ~92% null; this profiles the *EDW hw_config* version,
-- which may or may not share that problem -- that is exactly what this query
-- is for) -> the device_ps5_component "all_hw" CTE's population is thin before
-- any age/failure logic even runs.
-- pct_null_reported_changed high but pct_null_both_age_sources low -> most rows
-- are falling back to LAST_REPORTED_DTM, which is a weaker age proxy (it reflects
-- last EDW report, not actual install/swap date) -- worth flagging even if row
-- counts look fine.
-- pct_null_both_age_sources high -> those rows are silently dropped by
-- device_ps5_component's final WHERE hw.component_age_days IS NOT NULL clause;
-- this number is effectively PS5's true component-attrition rate before the
-- censoring-rate check (ps5_censoring_rate_check.sql, same folder) even runs.
-- =============================================================================

SELECT
mars_device_category,
COUNT(*) AS total_rows,

SUM(CASE WHEN COMPONENT_SERIAL_NBR IS NULL THEN 1 ELSE 0 END) AS null_serial,
ROUND(
  SUM(CASE WHEN COMPONENT_SERIAL_NBR IS NULL THEN 1 ELSE 0 END) / COUNT(*) * 100, 1
) AS pct_null_serial,

SUM(CASE WHEN REPORTED_CHANGED_DTM IS NULL THEN 1 ELSE 0 END) AS null_reported_changed,
ROUND(
  SUM(CASE WHEN REPORTED_CHANGED_DTM IS NULL THEN 1 ELSE 0 END) / COUNT(*) * 100, 1
) AS pct_null_reported_changed,

SUM(CASE WHEN LAST_REPORTED_DTM IS NULL THEN 1 ELSE 0 END) AS null_last_reported,

SUM(
  CASE WHEN REPORTED_CHANGED_DTM IS NULL AND LAST_REPORTED_DTM IS NULL THEN 1 ELSE 0 END
) AS null_both_age_sources,
ROUND(
  SUM(CASE WHEN REPORTED_CHANGED_DTM IS NULL AND LAST_REPORTED_DTM IS NULL THEN 1 ELSE 0 END)
  / COUNT(*) * 100, 1
) AS pct_null_both_age_sources,

SUM(CASE WHEN component_age_days IS NULL THEN 1 ELSE 0 END) AS null_component_age_days

FROM mars_dev.silver.hw_config_current
GROUP BY mars_device_category
ORDER BY mars_device_category;

-- -----------------------------------------------------------------------------
-- Follow-up: how many rows would actually reach gold.device_ps5_component's
-- "all_hw" CTE after BOTH of its filters (COMPONENT_SERIAL_NBR IS NOT NULL at
-- the silver->gold read, AND component_age_days IS NOT NULL at the final WHERE)?
-- This is the real, pre-failure-logic denominator for PS5's component universe.
-- -----------------------------------------------------------------------------
SELECT
mars_device_category,
COUNT(*) AS would_pass_serial_filter,
SUM(CASE WHEN component_age_days IS NOT NULL THEN 1 ELSE 0 END) AS would_also_pass_age_filter
FROM mars_dev.silver.hw_config_current
WHERE COMPONENT_SERIAL_NBR IS NOT NULL
GROUP BY mars_device_category
ORDER BY mars_device_category;
