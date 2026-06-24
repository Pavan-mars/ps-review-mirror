-- =============================================================================
-- silver.dim_device
-- Device master dimension with mars_device_category derived field
--
-- Sources (mars_dev.bronze catalog):
--   EDW.DEVICE_DIMENSION      (189,767 rows, 66 cols)  -- primary source
--   NCS_STAGE.DEVICE          (7,859 rows, 25 cols)    -- NCS device_type_id join
--   NCS_STAGE.DEVICE_TYPE     (84 rows, 16 cols)       -- BUS_DEVICE_FLAG
--   NCS_STAGE.DEVICE_CONTROL_GROUP_TYPE (5 rows)       -- not used (see notes)
--
-- Filter: OPERATOR_ID > 0 only (sentinel exclusion -- CURRENT_FLAG filter removed for SCD2)
--   -> 189,570 rows including full SCD2 history (173,197 historical + 16,375 current)
--   -> OPERATOR_ID > 0 removes 197 sentinel rows: NULL(81), -1(74), 0(42)
--   -> is_current = (CURRENT_FLAG = 1) identifies the active row per DEVICE_ID
--   -> effective_to: LEAD(INSERTED_DTM) per DEVICE_ID -- NULL for current rows (SCD2 convention)
--   -> ALL downstream joins MUST add AND dd.is_current = TRUE to prevent fan-out
--
-- Validation run 2026-06-15 -- key findings:
--   - NO CTA\d{5} device IDs in real data; all use type-prefix format (BMV, BTP, RVG...)
--   - NCS match rate: 47.4% (8,713 devices rely on EDW-name-only CASE fallbacks)
--   - DEVICE_CONTROL_GROUP_TYPE_NAME values are 'Bus Groups'/'Rail'/'Ticket Sale'/'CC Room'
--     -- NEVER 'RVG'/'HBG'/'SAG' -- that GATE branch is removed
--   - TT_ = Turnstile (confirmed at named CTA rail stations) -> GATE
--   - BTP at PortableFarebox (45 devices) = bus-mounted farebox -> VALIDATOR
--   - BTP at depots/garages (3,400+) -> TVM (correct)
--   - DEVICE_SERIAL_NUMBER: 91.8% NULL confirmed; PS5 now sources from CMDB_CI (2026-06-24)
--
-- Final category distribution (updated 2026-06-24):
--   VALIDATOR  6,734  ~41.1%  BMV(NCS) + FBX/ABP(EDW fallback) + BTP PortableFarebox
--   TVM        4,512  ~27.6%  BTP depots + AVM/EVM/TVM(EDW) (RTL/POS removed 2026-06-24)
--   GATE       2,342  ~14.3%  RVG/SAG/HBG(NCS+EDW) + TT_/TTC/TWA/TEX turnstiles
--   OTHER      2,997  ~18.3%  ECX/SCR/CRV/SIC/RSV/CSC + RTL + POS + reader codes
--
-- Michael R2 changes applied 2026-06-24:
--   R2-6:  RTL -> OTHER (Retail Terminal: separate incident-level KPI only; no availability/WO records)
--   R2-6:  POS -> OTHER (out of scope for device availability models)
--   R2-7:  DCR -> OTHER (on-bus messaging module; falls through CASE to OTHER already)
--   R2-14: TRANSIT_ARRAY_ID + ARRAY_POSITION added for PS2 gate-bank cascade analysis
--          FARE_CONTROL_AREA, TURNSTILE_DEVICE_TYPE, TURNSTILE_DEVICE_NUMBER also added
--   R2-15: DEVICE_SERIAL_NUMBER gap -- PS5 now uses CMDB_CI.serial_number (ServiceNow 2026-06-24)
--
-- CONFIRMED DECISIONS (2026-06-24):
--   D50  FBX fareboxes (4,651) → VALIDATOR. Bus-mounted fareboxes confirmed as field devices.
--        NCS-confirmed via BUS_DEVICE_FLAG=TRUE + SHORT_DESC='FBX'; EDW fallback on DEVICE_TYPE_NAME LIKE '%FBX%'.
--        No separate modelling effort required. FBX is within VALIDATOR service boundary.
--   D51  BTP (3,486 total) → SPLIT: not back-office. Both groups are serviceable fare devices:
--        BTP at PORTABLEFAREBOX facility (45 devices) = bus-mounted farebox → VALIDATOR
--        BTP at depots/garages (~3,441 devices) = fare vending terminal → TVM
--        Both groups participate in PS1–PS5 models; no records excluded.
-- NOTE: READER is NOT a device category -- it is a component view across parent devices.
--       Reader failures live in device_event_enriched at component grain (COMPONENT_TYPE_*).
--       See Reader_Component_Modeling_Reframe_22Jun2026.md.
-- NOTE: BMV split rule (rail=READER / bus=VALIDATOR by OPERATOR_ID) pending Michael final answer.
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.dim_device;

CREATE TABLE mars_dev.silver.dim_device AS
SELECT
    d.DEVICE_KEY,
    d.DEVICE_ID,
    d.DEVICE_NAME,
    d.DEVICE_TYPE_ID,
    d.DEVICE_TYPE_NAME,
    d.DEVICE_CONTROL_GROUP_ID,
    d.DEVICE_CONTROL_GROUP_NAME,
    d.DEVICE_CONTROL_GROUP_TYPE_ID,
    d.DEVICE_CONTROL_GROUP_TYPE_NAME,
    d.OPERATOR_ID,
    d.OPERATOR_NAME,
    d.FACILITY_ID,
    d.FACILITY_NAME,
    d.TRANSIT_MODE_ID,
    d.TRANSIT_MODE_NAME,
    d.AGENCY_ID,
    d.AGENCY_NAME,
    d.AUTHORITY_ID,
    d.AUTHORITY_NAME,
    d.BUS_ID,
    d.CURRENT_FLAG,
    d.DEVICE_SERIAL_NUMBER,

    -- PS2 gate-bank cascade fields (Michael R2-14: group gates by TRANSIT_ARRAY_ID)
    -- TRANSIT_ARRAY_ID: logical group of gates at a turnstile bank (NULL for non-gate devices)
    -- ARRAY_POSITION:   position of this gate within the bank (1-based)
    d.TRANSIT_ARRAY_ID,
    d.ARRAY_POSITION,
    d.POSITION                                                        AS device_position,
    d.FARE_CONTROL_AREA,
    d.TURNSTILE_DEVICE_TYPE,
    d.TURNSTILE_DEVICE_NUMBER,

    CAST(d.INSERTED_DTM AS DATE)                                      AS effective_from,
    -- SCD2: LEAD() gives next version start as this version's end. NULL = still active.
    LEAD(CAST(d.INSERTED_DTM AS DATE)) OVER (
        PARTITION BY d.DEVICE_ID
        ORDER BY d.INSERTED_DTM
    )                                                                 AS effective_to,

    -- NCS flags -- NULL for 52.6% of devices with no NCS_STAGE.DEVICE record
    COALESCE(CAST(ndt.BUS_DEVICE_FLAG     AS BOOLEAN), FALSE)         AS bus_device_flag,
    COALESCE(CAST(ndt.FIXED_LOCATION_FLAG AS BOOLEAN), TRUE)          AS fixed_location_flag,
    ndt.SHORT_DESC                                                    AS device_type_short_desc,

    CASE
        -- ── TVM: ticket/fare vending machines ────────────────────────────────
        -- NCS SHORT_DESC: TVM, ERM, RST, MCC, POI = fixed-location fare machines
        -- AVM = Add Value Machine, EVM = Electronic Vending Machine (EDW-only)
        -- NOTE: RTL (Retail Terminal) and POS removed 2026-06-24 (Michael R2-6):
        --   RTL = retail partner outlets with incident-level KPI only; no availability/WO records
        --   POS = out of scope for device availability models
        --   Both now fall through to OTHER below
        WHEN UPPER(d.DEVICE_TYPE_NAME) LIKE '%TVM%'
          OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%FMVD%'
          OR UPPER(COALESCE(ndt.SHORT_DESC,'')) IN ('TVM','ERM','RST','MCC','POI')
          OR UPPER(d.DEVICE_TYPE_NAME) IN ('AVM','EVM')
          OR UPPER(COALESCE(ndt.DESCRIPTION,'')) LIKE '%VENDING%'
          OR UPPER(COALESCE(ndt.DESCRIPTION,'')) LIKE '%TICKET%MACHINE%'  THEN 'TVM'

        -- ── GATE: entry/exit fare barriers ────────────────────────────────────
        -- NCS SHORT_DESC: ENG/EXG = Entry/Exit Gate, RVG, SAG, HBG
        -- TT_ = Turnstile Ticket Only, TTC = Ticket+Coin, TWA = Wheelchair, TEX = Exit Only
        WHEN UPPER(d.DEVICE_TYPE_NAME) LIKE '%GATE%'
          OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%BARRIER%'
          OR UPPER(d.DEVICE_TYPE_NAME) IN ('RVG','SAG','HBG')
          OR UPPER(d.DEVICE_TYPE_NAME) IN ('TT_','TTC','TWA','TEX')
          OR UPPER(COALESCE(ndt.SHORT_DESC,'')) IN ('ENG','EXG','RVG','SAG','HBG')
          OR UPPER(COALESCE(ndt.DESCRIPTION,'')) LIKE '%GATE%'
          OR UPPER(COALESCE(ndt.DESCRIPTION,'')) LIKE '%BARRIER%'         THEN 'GATE'

        -- ── VALIDATOR: bus-mounted fare payment devices ───────────────────────
        -- Primary: NCS BUS_DEVICE_FLAG=TRUE (4,171 BMV devices, NCS-confirmed)
        -- NCS SHORT_DESC: BMV, CMV, MPV, PIM, DCU, FBX
        WHEN COALESCE(CAST(ndt.BUS_DEVICE_FLAG AS BOOLEAN), FALSE) = TRUE
          AND (UPPER(d.DEVICE_TYPE_NAME) LIKE '%BMV%'
            OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%FBX%'
            OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%DCU%'
            OR UPPER(COALESCE(ndt.SHORT_DESC,'')) IN ('BMV','CMV','MPV','PIM','DCU','FBX'))
                                                                          THEN 'VALIDATOR'

        -- ── EDW name-only fallbacks (8,713 devices with no NCS match) ─────────
        WHEN UPPER(d.DEVICE_TYPE_NAME) LIKE '%BMV%'
          OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%FBX%'
          OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%DCU%'
          OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%VALIDATOR%'                 THEN 'VALIDATOR'

        -- BTP at PortableFarebox location = bus-mounted farebox -> VALIDATOR
        -- BTP elsewhere (depots, garages) -> TVM
        WHEN UPPER(d.DEVICE_TYPE_NAME) IN ('BTP','BFP','BPM')
          AND UPPER(COALESCE(d.FACILITY_NAME,'')) LIKE '%PORTABLEFAREBOX%'
                                                                          THEN 'VALIDATOR'
        WHEN UPPER(d.DEVICE_TYPE_NAME) IN ('BTP','BFP','BPM','AVM','EVM') THEN 'TVM'

        -- ABP = Automated Bus Payment (CTA on-bus validator)
        WHEN UPPER(d.DEVICE_TYPE_NAME) = 'ABP'                           THEN 'VALIDATOR'

        -- Any remaining NCS-confirmed bus device
        WHEN COALESCE(CAST(ndt.BUS_DEVICE_FLAG AS BOOLEAN), FALSE) = TRUE THEN 'VALIDATOR'

        -- ── OTHER: infra/back-office/retail/sub-component (EXCLUDED from PS1-PS5 models)
        -- ECX/CRV/SIC/POS/COS/AMR = legacy infra (pre-Ventra-2, confirmed DROP per Michael R2-5)
        -- RTL = Retail Terminal (confirmed OUT OF SCOPE per Michael R2-6)
        -- POS = out of scope (Michael R2-6)
        -- DCR = on-bus messaging module (Michael R2-7: drop)
        -- SCR/RSV/CSC = COMPONENT_TYPE codes inside parent devices (not standalone)
        ELSE 'OTHER'
    END                                                                   AS mars_device_category,

    -- is_current: exactly one TRUE row per DEVICE_ID (effective_to IS NULL = still active)
    (LEAD(CAST(d.INSERTED_DTM AS DATE)) OVER (PARTITION BY d.DEVICE_ID ORDER BY d.INSERTED_DTM) IS NULL) AS is_current,
    (d.DEVICE_STATUS_ID = 1)                                              AS is_active

FROM      mars_dev.bronze.edw_device_dimension                            d
LEFT JOIN mars_dev.bronze.ncs_stage_device                                nd  ON nd.DEVICE_ID      = d.DEVICE_ID
LEFT JOIN mars_dev.bronze.ncs_stage_device_type                           ndt ON ndt.DEVICE_TYPE_ID = nd.DEVICE_TYPE_ID
WHERE d.OPERATOR_ID > 0;
-- SCD2: CURRENT_FLAG = 1 filter removed -- load all 189,767 rows (current + historical).
-- Downstream joins must use AND dd.is_current = TRUE to get the active row per DEVICE_ID.

-- Post-build verification:
-- SELECT COUNT(*)                                             AS total_rows,        -- Expect ~189,570
--        SUM(CASE WHEN is_current = TRUE  THEN 1 ELSE 0 END) AS current_rows,       -- Expect ~16,375
--        SUM(CASE WHEN is_current = FALSE THEN 1 ELSE 0 END) AS historical_rows,    -- Expect ~173,195
--        COUNT(DISTINCT mars_device_category)                 AS categories,         -- Expect 4
--        COUNT(DISTINCT FACILITY_ID)                          AS facilities,         -- Expect 25
--        SUM(CASE WHEN TRANSIT_ARRAY_ID IS NOT NULL THEN 1 ELSE 0 END) AS gate_array_rows -- Check PS2
-- FROM mars_dev.silver.dim_device;
