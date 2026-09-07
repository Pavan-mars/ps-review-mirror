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
-- Final category distribution (updated 2026-06-25, Michael R3):
--   VALIDATOR  ~4,200  BMV bus only - OPERATOR_ID 2 (CTA Bus, 2,993+20) + OPERATOR_ID 3 (PACE Bus, 1,190+9)
--                      Rail BMV confirmed = 0 devices; all BMV are bus-operated (query 2026-06-25)
--   TVM        ~4,512  AVM/EVM/TVM(EDW) (RTL/POS/BTP/FBX removed)
--   GATE       ~2,342  RVG/SAG/HBG ONLY (TT_/TTC/TWA/TEX turnstiles removed)
--   OTHER      ~rest   FBX + BTP + TT_/TTC/TWA/TEX + ECX/SCR/RTL/POS/RSV/CSC
--   Target in-scope fleet: ~11,054 (updated: VALIDATOR count corrected to ~4,200)
--
-- Michael R2 changes applied 2026-06-24:
--   R2-6:  RTL -> OTHER (Retail Terminal: separate incident-level KPI only; no availability/WO records)
--   R2-6:  POS -> OTHER (out of scope for device availability models)
--   R2-7:  DCR -> OTHER (on-bus messaging module; falls through CASE to OTHER already)
--   R2-14: TRANSIT_ARRAY_ID + ARRAY_POSITION added for PS2 gate-bank cascade analysis
--          FARE_CONTROL_AREA, TURNSTILE_DEVICE_TYPE, TURNSTILE_DEVICE_NUMBER also added
--   R2-15: DEVICE_SERIAL_NUMBER gap -- PS5 now uses CMDB_CI.serial_number (ServiceNow 2026-06-24)
--
-- Michael R3 changes applied 2026-06-25:
--   R3-FBX: FBX (4,651 fareboxes) -> OTHER. Fareboxes are out of Ventra scope. (REVERSED D50)
--   R3-BTP: BTP (3,486 devices)   -> OTHER. Legacy devices, dropped. (REVERSED D51)
--   R3-TT:  TT_/TTC/TWA/TEX (legacy turnstiles) -> OTHER. Only RVG/HBG/SAG remain in GATE.
--   Device universe CLOSED: ~9,270 in-scope (TVM + GATE[RVG/HBG/SAG] + VALIDATOR[BMV bus]).
-- NOTE: READER is NOT a device category -- it is a component view across parent devices.
--       Reader failures live in device_event_enriched at component grain (COMPONENT_TYPE_*).
--       See Reader_Component_Modeling_Reframe_22Jun2026.md.
-- NOTE: BMV rail/bus split CLOSED 2026-06-25 - rail BMV = 0 devices in data.
--       All BMV are bus (OPERATOR_ID 2=CTA Bus, 3=PACE Bus). OPERATOR_ID guard applied.
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
        -- -- TVM: ticket/fare vending machines --------------------------------
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

        -- -- GATE: entry/exit fare barriers - RVG/HBG/SAG ONLY (Michael R3) ----
        -- TT_/TTC/TWA/TEX (legacy turnstiles) -> OTHER per Michael R3 (dropped)
        -- Only physical gate barriers with active SLA remain in scope
        WHEN UPPER(d.DEVICE_TYPE_NAME) LIKE '%GATE%'
          OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%BARRIER%'
          OR UPPER(d.DEVICE_TYPE_NAME) IN ('RVG','SAG','HBG')
          OR UPPER(COALESCE(ndt.SHORT_DESC,'')) IN ('ENG','EXG','RVG','SAG','HBG')
          OR UPPER(COALESCE(ndt.DESCRIPTION,'')) LIKE '%GATE%'
          OR UPPER(COALESCE(ndt.DESCRIPTION,'')) LIKE '%BARRIER%'         THEN 'GATE'

        -- -- VALIDATOR: bus-mounted fare payment devices (BMV bus only, Michael R3) --
        -- FBX removed R3: fareboxes out of Ventra scope -> fall to OTHER
        -- NCS SHORT_DESC: BMV, CMV, MPV, PIM, DCU (FBX removed)
        -- OPERATOR_ID guard per Michael R3 (precedence over control group):
        --   OPERATOR_ID 2 = CTA Bus, OPERATOR_ID 3 = PACE Bus - all bus BMV
        --   Rail BMV = 0 devices confirmed (query 2026-06-25); no rail OPERATOR_ID seen
        WHEN COALESCE(CAST(ndt.BUS_DEVICE_FLAG AS BOOLEAN), FALSE) = TRUE
          AND (UPPER(d.DEVICE_TYPE_NAME) LIKE '%BMV%'
            OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%DCU%'
            OR UPPER(COALESCE(ndt.SHORT_DESC,'')) IN ('BMV','CMV','MPV','PIM','DCU'))
          AND d.OPERATOR_ID IN (2, 3)                                     THEN 'VALIDATOR'

        -- -- EDW name-only fallbacks (8,713 devices with no NCS match) ---------
        -- FBX removed R3 (fareboxes out of scope)
        -- OPERATOR_ID IN (2,3) = CTA Bus / PACE Bus; rail BMV absent from data
        WHEN (UPPER(d.DEVICE_TYPE_NAME) LIKE '%BMV%'
           OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%DCU%'
           OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%VALIDATOR%')
          AND d.OPERATOR_ID IN (2, 3)                                     THEN 'VALIDATOR'

        -- BTP removed R3: legacy devices, out of scope -> fall to OTHER
        -- AVM/EVM remain in TVM
        WHEN UPPER(d.DEVICE_TYPE_NAME) IN ('AVM','EVM')                  THEN 'TVM'

        -- ABP = Automated Bus Payment (CTA on-bus validator)
        WHEN UPPER(d.DEVICE_TYPE_NAME) = 'ABP'                           THEN 'VALIDATOR'

        -- Any remaining NCS-confirmed bus device
        WHEN COALESCE(CAST(ndt.BUS_DEVICE_FLAG AS BOOLEAN), FALSE) = TRUE THEN 'VALIDATOR'

        -- -- OTHER: excluded from PS1-PS5 models ------------------------------
        -- FBX (4,651) = fareboxes, out of Ventra scope (Michael R3)
        -- BTP (3,486) = legacy devices, out of scope (Michael R3)
        -- TT_/TTC/TWA/TEX = legacy turnstiles (Michael R2)
        -- ECX/CRV/SIC/POS/COS/AMR = legacy infra (Michael R2-5)
        -- RTL = Retail Terminal, out of scope (Michael R2-6)
        -- POS = out of scope (Michael R2-6)
        -- DCR = on-bus messaging module (Michael R2-7)
        -- SCR/RSV/CSC = sub-component codes, not standalone devices
        ELSE 'OTHER'
    END                                                                   AS mars_device_category,

    -- is_current: exactly one TRUE row per DEVICE_ID (effective_to IS NULL = still active)
    (LEAD(CAST(d.INSERTED_DTM AS DATE)) OVER (PARTITION BY d.DEVICE_ID ORDER BY d.INSERTED_DTM) IS NULL) AS is_current,
    COALESCE(d.DEVICE_STATUS_ID = 1, FALSE)                               AS is_active

FROM      mars_dev.bronze.edw_device_dimension                            d
LEFT JOIN mars_dev.bronze.ncs_stage_device                                nd  ON nd.DEVICE_ID      = d.DEVICE_ID
LEFT JOIN mars_dev.bronze.ncs_stage_device_type                           ndt ON ndt.DEVICE_TYPE_ID = nd.DEVICE_TYPE_ID
WHERE d.OPERATOR_ID > 0
   -- FIX 2026-07-01 (DQ v3): rescue in-scope devices (TVM / RVG / SAG / HBG) that appear in the
   -- fact + ledger data but carry a sentinel OPERATOR_ID (NULL / -1 / 0) and were dropped above,
   -- so they land in dim_device. Fixes the ~17 in-scope orphans behind the RI check. The prefix
   -- guard keeps this narrow (out-of-scope sentinel rows are still excluded).
   OR UPPER(d.DEVICE_ID) RLIKE '^(TVM|RVG|SAG|HBG)';
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
