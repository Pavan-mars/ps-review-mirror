-- =============================================================================
-- silver.dim_facility
-- Facility dimension - NCS retail/vendor partner locations for Ventra card network
--
-- Source: mars_dev.bronze.ncs_stage_transit_facility (1,986 MANAGED rows)
--   Ingested 2026-06-10 from NCS_STAGE.TRANSIT_FACILITY via Oracle ODS pipeline.
--   These are Ventra card reload partner locations (convenience stores, pharmacies,
--   etc.) - NOT CTA transit stations. CTA station facility data lives in
--   EDW.DEVICE_DIMENSION -> mars_dev.silver.dim_device (FACILITY_ID + FACILITY_NAME).
--
-- Schema discoveries (catalog probe 2026-06-19):
--   BUG 18 (RESOLVED): TRANSIT_MODE_ID does not exist in the NCS schema.
--                      Set transit_mode_id = NULL, transit_mode = 'OTHER'.
--                      All 1,986 rows are retail partner locations - transit mode
--                      is not applicable (OPERATOR_ID = 5 = retail partner).
--   BUG 17 (CONFIRMED): LATITUDE / LONGITUDE do not exist -> CAST(NULL AS DOUBLE).
--   STATE column:       decimal(3,0) numeric FIPS state code (e.g. 17 = Illinois),
--                       NOT a state abbreviation string. Cast to INT.
--   ADDRESS components: ROAD_TYPE/ROAD_PREFIX/ROAD_NUMBER/ROAD_NAME/ROAD_SUFFIX
--                       all present but mostly NULL in data (retail location addresses
--                       not fully populated in NCS source). ROAD_NAME used as address.
--
-- Bug fixes applied (original SQL had PostgreSQL syntax - converted to Databricks Spark SQL):
--   BUG 1:  silver.dim_facility             -> mars_dev.silver.dim_facility
--   BUG 2:  FROM bronze.ncs_transit_facility -> mars_dev.bronze.ncs_stage_transit_facility
--   BUG 3:  f.FACID::text                   -> CAST(f.FACID AS STRING)
--   BUG 4:  f.LATITUDE::float               -> CAST(NULL AS DOUBLE)  [col does not exist]
--   BUG 5:  f.LONGITUDE::float              -> CAST(NULL AS DOUBLE)  [col does not exist]
--   BUG 6:  f.OPERATOR_ID::integer          -> CAST(f.OPERATOR_ID AS INT)
--   BUG 7:  f.AGENCY_ID::text               -> CAST(f.AGENCY_ID AS STRING)
--   BUG 8:  f.TRANSIT_MODE_ID::integer      -> CAST(NULL AS INT)     [col does not exist]
--   BUG 9:  f.FACILITY_TYPE_ID::integer     -> CAST(f.TRANSIT_FACILITY_TYPE_ID AS INT)
--   BUG 10: ROUND(x::numeric, 2)::text      -> removed (lat/lon not available)
--   BUG 11: CREATE UNIQUE INDEX             -> not supported on Delta; removed
--   BUG 12: CREATE INDEX                    -> not supported on Delta; removed
--   BUG 13: COMMENT ON TABLE               -> not supported on Delta; removed
--   BUG 14: f.ADDRESS                      -> f.ROAD_NAME            [ADDRESS col absent]
--   BUG 15: f.ZIP                          -> f.POSTAL_CODE          [ZIP col absent]
--   BUG 16: f.FACILITY_TYPE_ID             -> f.TRANSIT_FACILITY_TYPE_ID
--   BUG 17: f.LATITUDE / f.LONGITUDE       -> CAST(NULL AS DOUBLE)
--   BUG 18: f.TRANSIT_MODE_ID              -> CAST(NULL AS INT)       [col does not exist]
--   BUG 19: STATE varchar                  -> CAST(f.STATE AS INT)    [decimal(3,0) FIPS code]
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.dim_facility;

CREATE TABLE mars_dev.silver.dim_facility AS
SELECT
    CAST(f.FACID AS STRING)                                   AS facid,
    TRIM(f.SHORT_DESC)                                        AS facility_short_name,
    TRIM(f.DESCRIPTION)                                       AS facility_name,
    TRIM(f.ROAD_NAME)                                         AS address,
    TRIM(f.CITY)                                              AS city,
    -- STATE is a numeric FIPS code (decimal 3,0), not a state abbreviation.
    -- 17 = Illinois. Cast to INT to avoid decimal representation (e.g. 17.0).
    CAST(f.STATE AS INT)                                      AS state_fips,
    TRIM(f.POSTAL_CODE)                                       AS zip_code,
    CAST(NULL AS DOUBLE)                                      AS latitude,
    CAST(NULL AS DOUBLE)                                      AS longitude,
    CAST(f.OPERATOR_ID AS INT)                                AS operator_id,
    CAST(f.AGENCY_ID AS STRING)                               AS agency_id,
    -- TRANSIT_MODE_ID does not exist in NCS_STAGE.TRANSIT_FACILITY (BUG 18).
    -- All rows are retail partner locations - transit mode not applicable.
    CAST(NULL AS INT)                                         AS transit_mode_id,
    CAST(f.TRANSIT_FACILITY_TYPE_ID AS INT)                   AS facility_type_id,
    -- transit_mode cannot be derived from NCS retail facility data.
    -- CTA station transit modes live in dim_device (DEVICE_CONTROL_GROUP_TYPE_NAME).
    CAST('OTHER' AS STRING)                                   AS transit_mode,
    CAST(NULL AS STRING)                                      AS geo_cluster_key,
    CURRENT_TIMESTAMP                                         AS silver_loaded_at

FROM mars_dev.bronze.ncs_stage_transit_facility f
WHERE f.FACID IS NOT NULL;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.dim_facility ZORDER BY (facid);

-- Verification (run separately after build):
-- SELECT COUNT(*)                                                    AS total_facilities,
--        COUNT(DISTINCT facility_type_id)                           AS distinct_types,
--        COUNT(DISTINCT operator_id)                                AS distinct_operators,
--        COUNT(NULLIF(TRIM(address), ''))                           AS with_address,
--        COUNT(NULLIF(TRIM(city),    ''))                           AS with_city,
--        COUNT(state_fips)                                          AS with_state_fips,
--        MIN(CAST(facid AS INT)) AS facid_min, MAX(CAST(facid AS INT)) AS facid_max
-- FROM mars_dev.silver.dim_facility;
-- Post-validation 2026-06-19: 1,986 rows, 1,986 distinct facids, 0 duplicates.
-- facid range: 0-9,112. facid=0 = sentinel (3 rows with facility_type_id=0 / operator_id=0).
-- NOTE: address, city, state_fips, zip_code ALL NULL (100%) - NCS source carries no
--       location data for retail facilities. geo_cluster_key also NULL for all rows.
--       dim_facility is operator/type lookup only; no geospatial features available.
-- operator distribution: 6 distinct operators.
--   facility_type_id=5 / operator_id=5 = 1,687 rows (84.9%) - main Ventra reload partners.
--   facility_type_id=1 / operator_id=1 = 187 rows (9.4%) - transit agency-owned.
--   Other type/operator combos = 99 rows (5.0%). Sentinel type_id=0/op_id=0 = 3 rows (0.2%).
