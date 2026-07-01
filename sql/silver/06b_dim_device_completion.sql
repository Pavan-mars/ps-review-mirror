-- =============================================================================
-- silver.dim_device -- COMPLETION for in-scope orphans  (S06b, 2026-07-01, DQ v3)
--
-- Context: the DQ referential-integrity check found in-scope device_ids (TVM / RVG /
--   SAG / HBG) present in the fact + ledger data but missing from dim_device. Root cause:
--   the S06 `WHERE OPERATOR_ID > 0` sentinel filter dropped devices whose OPERATOR_ID is
--   NULL / -1 / 0.
--
-- PRIMARY FIX (already applied in 06_dim_device__create.sql): the WHERE clause now also
--   keeps `UPPER(DEVICE_ID) RLIKE '^(TVM|RVG|SAG|HBG)'`, so those devices are rescued from
--   the real source on the next dim rebuild. Rebuild order:  S06 -> S16 -> S19 -> DQ.
--
-- THIS FILE (S06b) is a follow-up you run AFTER the S06 rebuild to:
--   (1) VERIFY the rescue worked (Step 1 -- diagnostic), and
--   (2) only if the diagnostic shows devices GENUINELY ABSENT from edw_device_dimension,
--       add minimal synthetic dim rows for them (Step 2 -- optional, guarded).
-- Idempotent. Read-only until you uncomment Step 2.
-- =============================================================================

-- -- STEP 1: DIAGNOSTIC (read-only) ------------------------------------------
-- Classifies every in-scope orphan: is it in the source (and was it a sentinel-OPERATOR_ID
-- row now rescued by S06), or is it genuinely absent (needs Step 2)?
WITH fact_devices AS (
    SELECT DISTINCT DEVICE_ID FROM mars_dev.silver.maintenance_ledger    WHERE DEVICE_ID IS NOT NULL
    UNION
    SELECT DISTINCT DEVICE_ID FROM mars_dev.silver.device_event_enriched WHERE DEVICE_ID IS NOT NULL
),
in_scope AS (
    SELECT DEVICE_ID,
           CASE WHEN UPPER(DEVICE_ID) RLIKE '^TVM'          THEN 'TVM'
                WHEN UPPER(DEVICE_ID) RLIKE '^(RVG|SAG|HBG)' THEN 'GATE' END AS derived_category
    FROM fact_devices
    WHERE UPPER(DEVICE_ID) RLIKE '^(TVM|RVG|SAG|HBG)'
),
orphans AS (          -- in-scope devices still not in dim_device
    SELECT i.DEVICE_ID, i.derived_category
    FROM in_scope i
    LEFT ANTI JOIN mars_dev.silver.dim_device dd ON dd.DEVICE_ID = i.DEVICE_ID
),
src AS (              -- most-recent source row per device (any OPERATOR_ID)
    SELECT DEVICE_ID, DEVICE_KEY, OPERATOR_ID, DEVICE_TYPE_NAME,
           ROW_NUMBER() OVER (PARTITION BY DEVICE_ID ORDER BY INSERTED_DTM DESC) AS rn
    FROM mars_dev.bronze.edw_device_dimension
)
SELECT
    o.DEVICE_ID,
    o.derived_category,
    (s.DEVICE_ID IS NOT NULL)                             AS exists_in_source,
    s.OPERATOR_ID                                         AS src_operator_id,
    s.DEVICE_TYPE_NAME                                    AS src_device_type_name,
    CASE
        WHEN s.DEVICE_ID IS NULL                THEN 'ABSENT_FROM_SOURCE -> needs Step 2 (synthetic)'
        WHEN COALESCE(s.OPERATOR_ID, 0) <= 0    THEN 'SENTINEL_OPERATOR -> fixed by S06 rescue (rebuild)'
        ELSE                                         'IN_SOURCE -> should already be in dim (investigate)'
    END                                                   AS resolution
FROM orphans o
LEFT JOIN src s ON s.DEVICE_ID = o.DEVICE_ID AND s.rn = 1
ORDER BY exists_in_source, o.DEVICE_ID;
-- Expect: after the S06 rebuild, most/all rows show SENTINEL_OPERATOR and 'orphans' is empty.
-- Any ABSENT_FROM_SOURCE rows are the only ones that need Step 2.


-- -- STEP 2 (OPTIONAL, guarded): synthetic rows for devices ABSENT from source ----------
-- Only run this for the ABSENT_FROM_SOURCE devices from Step 1, and ideally confirm with
-- Michael that they are legitimate in-scope devices first. Synthetic rows use a NEGATIVE
-- DEVICE_KEY so they are auditable as backfilled; all source attributes are NULL.
--
-- INSERT INTO mars_dev.silver.dim_device (
--     DEVICE_KEY, DEVICE_ID, mars_device_category, is_current, is_active, effective_from
-- )
-- WITH fact_devices AS (
--     SELECT DISTINCT DEVICE_ID FROM mars_dev.silver.maintenance_ledger    WHERE DEVICE_ID IS NOT NULL
--     UNION
--     SELECT DISTINCT DEVICE_ID FROM mars_dev.silver.device_event_enriched WHERE DEVICE_ID IS NOT NULL
-- ),
-- absent AS (
--     SELECT f.DEVICE_ID,
--            CASE WHEN UPPER(f.DEVICE_ID) RLIKE '^TVM'          THEN 'TVM'
--                 WHEN UPPER(f.DEVICE_ID) RLIKE '^(RVG|SAG|HBG)' THEN 'GATE' END AS derived_category
--     FROM fact_devices f
--     WHERE UPPER(f.DEVICE_ID) RLIKE '^(TVM|RVG|SAG|HBG)'
--       AND NOT EXISTS (SELECT 1 FROM mars_dev.silver.dim_device dd  WHERE dd.DEVICE_ID = f.DEVICE_ID)
--       AND NOT EXISTS (SELECT 1 FROM mars_dev.bronze.edw_device_dimension s WHERE s.DEVICE_ID = f.DEVICE_ID)
-- )
-- SELECT
--     -1 * ROW_NUMBER() OVER (ORDER BY DEVICE_ID)  AS DEVICE_KEY,   -- negative = synthetic/backfilled
--     DEVICE_ID,
--     derived_category                             AS mars_device_category,
--     TRUE                                         AS is_current,
--     TRUE                                         AS is_active,
--     current_date()                               AS effective_from
-- FROM absent;

-- -- Verify after Step 2:
-- SELECT mars_device_category, COUNT(*) FROM mars_dev.silver.dim_device
-- WHERE DEVICE_KEY < 0 GROUP BY mars_device_category;   -- the synthetic rows just added
