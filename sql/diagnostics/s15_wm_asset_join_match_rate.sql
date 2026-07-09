-- =============================================================================
-- DIAGNOSTIC: S15 incident_history dim_device join-key match rate
-- (u_wm_asset vs. the previously-validated asset_tag)
-- Added: 2026-07-07, validation pass (Chicago silver/gold rebuild review)
--
-- WHY THIS EXISTS:
-- 15_incident_history__create.sql joins dim_device via
-- dd.DEVICE_ID = UPPER(TRIM(i.u_wm_asset)). The 2026-06-25 CMDB probe
-- (probe_cmdb_device_join.py) validated a DIFFERENT join key -- asset_tag =
-- dim_device.DEVICE_ID, reaching ~29% of the fleet (5,331 devices) -- and
-- explicitly found u_ncs_device_id too sparse to use (2.8-3% populated).
-- u_wm_asset does not appear in that probe at all, since it's a field from the
-- newer 4-file CSV export the probe never saw. It may be a perfectly good (or
-- even better) join key, but that has not been independently confirmed. This
-- query checks both candidate keys side by side against the live table so the
-- join in S15 can be confirmed or corrected with an actual number, not a guess.
--
-- HOW TO READ THE RESULTS:
-- wm_asset_match_rate_pct meaningfully higher than asset_tag_match_rate_pct ->
-- u_wm_asset is the better key; S15's current join is fine, remove the
-- "unverified" note added to that file's header.
-- asset_tag_match_rate_pct meaningfully higher -> S15 should switch to (or add a
-- COALESCE fallback across) asset_tag, matching the 2026-06-25-validated approach.
-- Both low -> neither field reaches most of the fleet from this incident table;
-- worth a fresh look rather than assuming either is sufficient.
-- =============================================================================

WITH incidents AS (
  SELECT
    number AS incident_number,
    u_wm_asset,
    -- asset_tag is not a column on bronze.cta_servicenow_incident itself; it comes
    -- from the linked CMDB_CI row (cmdb_ci.name = incident.cmdb_ci, per S15's own
    -- join logic). Re-derive that same link here so both candidate keys are
    -- compared from the same incident population.
    cmdb_ci AS cmdb_ci_name
  FROM mars_dev.bronze.cta_servicenow_incident
  WHERE CAST(opened_at AS DATE) >= '2024-01-01'
),
ci_lookup AS (
  SELECT name AS ci_name, asset_tag
  FROM mars_dev.bronze.cta_servicenow_cmdb_ci
),
joined AS (
  SELECT
    i.incident_number,
    i.u_wm_asset,
    ci.asset_tag
  FROM incidents i
  LEFT JOIN ci_lookup ci ON i.cmdb_ci_name = ci.ci_name
)

SELECT
  COUNT(*) AS total_incidents,

  SUM(CASE WHEN dd_wm.DEVICE_ID IS NOT NULL THEN 1 ELSE 0 END) AS matched_via_wm_asset,
  ROUND(
    SUM(CASE WHEN dd_wm.DEVICE_ID IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*) * 100, 1
  ) AS wm_asset_match_rate_pct,

  SUM(CASE WHEN dd_tag.DEVICE_ID IS NOT NULL THEN 1 ELSE 0 END) AS matched_via_asset_tag,
  ROUND(
    SUM(CASE WHEN dd_tag.DEVICE_ID IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*) * 100, 1
  ) AS asset_tag_match_rate_pct,

  SUM(
    CASE WHEN dd_wm.DEVICE_ID IS NOT NULL AND dd_tag.DEVICE_ID IS NOT NULL
         AND dd_wm.DEVICE_ID = dd_tag.DEVICE_ID THEN 1 ELSE 0 END
  ) AS both_match_and_agree,
  SUM(
    CASE WHEN dd_wm.DEVICE_ID IS NOT NULL AND dd_tag.DEVICE_ID IS NOT NULL
         AND dd_wm.DEVICE_ID <> dd_tag.DEVICE_ID THEN 1 ELSE 0 END
  ) AS both_match_but_disagree

FROM joined j
LEFT JOIN mars_dev.silver.dim_device dd_wm
  ON dd_wm.DEVICE_ID = UPPER(TRIM(j.u_wm_asset)) AND dd_wm.is_current = TRUE
LEFT JOIN mars_dev.silver.dim_device dd_tag
  ON dd_tag.DEVICE_ID = UPPER(TRIM(j.asset_tag)) AND dd_tag.is_current = TRUE;
