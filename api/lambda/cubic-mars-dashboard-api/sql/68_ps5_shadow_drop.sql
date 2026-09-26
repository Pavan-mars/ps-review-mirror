-- 68_ps5_shadow_drop.sql -- PS5 shadow stack retirement. DROP. Irreversible.   26-Sep-2026
--
-- WHAT THESE WERE. The first-generation PS5 path: ps5-rds-loader (bare name) and the
-- never-deployed cubic-mars-ps5-daily-scorer wrote them; ps5-api (API GW b1s4xxlddb) served
-- them to the V1 PS5SLAReliabilityTab, which no longer exists. V4 reads v_ps5_device_rul /
-- v_ps5_serial_rul (sql/29-32, 67), loaded daily by cubic-mars-ps5-rds-loader.
--
-- HOW THAT WAS ESTABLISHED
--   Code:  no reference outside comments in the dashboard-api handler, dashboard/src or any
--          live loader; the Device-360 fallback to ps5_reliability_estimates was removed first.
--   AWS:   26-Sep CloudShell -- ps5-api and ps5-rds-loader 0 invocations in 14 days;
--          cubic-mars-ps5-daily-scorer does not exist; the only PS5 rule is
--          cubic-mars-ps5-daily-load -> cubic-mars-ps5-rds-loader (15 invocations).
--   DB:    run {"action":"depends"} on each name BEFORE this file. No CASCADE below, so an
--          unexpected dependent makes this fail rather than silently take it with it.
--
-- KEPT: ps5_weibull_params, ps5_cox_hazard_ratios (declared in sql/01, reserved for the
-- survival-curve panel).  The CREATE/seed blocks for everything dropped here were removed from
-- sql/01, 02 and 29 in the same commit, so a migrate replay cannot resurrect them.
DROP VIEW  IF EXISTS v_ps5_reliability_oos_latest;
DROP VIEW  IF EXISTS v_ps5_serial_oos_latest;
DROP VIEW  IF EXISTS v_ps5_dashboard_ready;
DROP TABLE IF EXISTS ps5_scoring_runs;
DROP TABLE IF EXISTS ps5_serial_reliability;
DROP TABLE IF EXISTS ps5_reliability_estimates;
DROP TABLE IF EXISTS ps5_reliability_status;
