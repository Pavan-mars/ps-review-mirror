-- =============================================================================
-- silver.dim_device
-- Device master dimension with mars_device_category derived field
--
-- Sources (mars_dev.bronze catalog):
--   EDW.DEVICE_DIMENSION      (189,767 rows, 66 cols)  — primary source
--   NCS_STAGE.DEVICE          (7,859 rows, 25 cols)    — NCS device_type_id join
--   NCS_STAGE.DEVICE_TYPE     (84 rows, 16 cols)       — BUS_DEVICE_FLAG
--   NCS_STAGE.DEVICE_CONTROL_GROUP_TYPE (5 rows)       — not used (see notes)
--
-- Filter: OPERATOR_ID > 0 only (sentinel exclusion — CURRENT_FLAG filter removed for SCD2)
--   → 189,570 rows including full SCD2 history (173,197 historical + 16,375 current)
--   → OPERATOR_ID > 0 removes 197 sentinel rows: NULL(81), -1(74), 0(42)
--   → is_current = (CURRENT_FLAG = 1) identifies the active row per DEVICE_ID
--   → effective_to: LEAD(INSERTED_DTM) per DEVICE_ID — NULL for current rows (SCD2 convention)
--   → ALL downstream joins MUST add AND dd.is_current = TRUE to prevent fan-out
--
-- Validation run 2026-06-15 — key findings:
--   - NO CTA\d{5} device IDs in real data; all use type-prefix format (BMV, BTP, RVG…)
--   - NCS match rate: 47.4% (8,713 devices rely on EDW-name-only CASE fallbacks)
--   - DEVICE_CONTROL_GROUP_TYPE_NAME values are 'Bus Groups'/'Rail'/'Ticket Sale'/'CC Room'
--     — NEVER 'RVG'/'HBG'/'SAG' → that GATE branch is removed
--   - TT_ = Turnstile (confirmed at named CTA rail stations) → GATE
--   - BTP at PortableFarebox (45 devices) = bus-mounted farebox → VALIDATOR
--   - BTP at depots/garages (3,400+) → TVM (correct)
--   - RTL (2,170 Retail Terminals, NCS-matched) → TVM
--   - RVG/SAG/HBG unmatched by NCS (270 devices) → GATE via EDW name fallback
--   - SCR (179 Smart Card Readers) → READER via EDW name fallback
--   - DEVICE_SERIAL_NUMBER: 91.8% NULL confirmed across both EDW and NCS — true data gap
--     PS5 must operate at device level (DEVICE_KEY), not component level
--
-- Final category distribution (validated 2026-06-15; updated 2026-06-23):
--   VALIDATOR  6,734  41.1%   BMV(NCS) + FBX/ABP(EDW fallback) + BTP PortableFarebox
--   TVM        6,682  40.8%   BTP depots + RTL(NCS) + AVM/EVM/TVM(EDW) + TVM name
--   GATE       2,342  ~14.3%  RVG/SAG/HBG(NCS+EDW) + TT_/TTC/TWA/TEX turnstiles + GATE names
--                             (+389 from TTC/TWA/TEX added 2026-06-23)
--   OTHER        827   ~5.0%  ECX/SCR/CRV/SIC/RSV/CSC + reader codes → infra/sub-component EXCLUDE
--                             (-READER category removed 2026-06-23; RSV/CSC are COMPONENT_TYPE not DEVICE_TYPE)
-- NOTE: READER is NOT a device category — it is a component view across parent devices.
--       Reader models (PS1–PS5) source from device_event_enriched COMPONENT_TYPE grain.
--       See Reader_Component_Modeling_Reframe_22Jun2026.md.
-- NOTE: FBX/BTP/AVM/EVM pending Michael (Cubic) confirmation — see taxonomy CSV
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
    CAST(d.INSERTED_DTM AS DATE)                                     AS effective_from,
    -- SCD2 fix: LEAD() gives the next version's start as this version's end date.
    -- NULL for current rows (no next row) = "still valid today" (SCD2 convention).
    -- UPDATED_DTM was wrong here — it's the last EDW record touch, not a supersession date.
    LEAD(CAST(d.INSERTED_DTM AS DATE)) OVER (
        PARTITION BY d.DEVICE_ID
        ORDER BY d.INSERTED_DTM
    )                                                                AS effective_to,

    -- NCS flags — NULL for 52.6% of devices that have no NCS_STAGE.DEVICE record
    COALESCE(CAST(ndt.BUS_DEVICE_FLAG     AS BOOLEAN), FALSE)        AS bus_device_flag,
    COALESCE(CAST(ndt.FIXED_LOCATION_FLAG AS BOOLEAN), TRUE)         AS fixed_location_flag,
    ndt.SHORT_DESC                                                   AS device_type_short_desc,

    CASE
        -- ── TVM: ticket/fare vending + retail sales terminals ─────────────────
        -- NCS SHORT_DESC: TVM, ERM, RST, POS, MCC, POI = fixed-location fare machines
        -- RTL (Retail Terminal, 2,170 devices, NCS-confirmed) = retail partner outlets
        -- AVM = Add Value Machine, EVM = Electronic Vending Machine (EDW-only)
        -- BTP at PortableFarebox (45 devices) = bus-mounted → handled in VALIDATOR below
        WHEN UPPER(d.DEVICE_TYPE_NAME) LIKE '%TVM%'
          OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%FMVD%'
          OR UPPER(COALESCE(ndt.SHORT_DESC,'')) IN ('TVM','ERM','RST','POS','MCC','POI','RTL')
          OR UPPER(d.DEVICE_TYPE_NAME) IN ('AVM','EVM','POS','RTL')
          OR UPPER(COALESCE(ndt.DESCRIPTION,'')) LIKE '%VENDING%'
          OR UPPER(COALESCE(ndt.DESCRIPTION,'')) LIKE '%TICKET%MACHINE%'
          OR UPPER(COALESCE(ndt.DESCRIPTION,'')) LIKE '%RETAIL%'         THEN 'TVM'

        -- ── GATE: entry/exit fare barriers ────────────────────────────────────
        -- NCS SHORT_DESC: ENG/EXG = Entry/Exit Gate, RVG = Reversible Gate,
        --                 SAG = ADA Special Access Gate, HBG = High Barrier Gate
        -- EDW name fallback for 270 gates with no NCS match (same type codes in DEVICE_TYPE_NAME)
        -- TT_  = Turnstile Ticket Only (confirmed at named CTA rail stations)
        -- TTC  = Turnstile Ticket Coin   (389 devices added 2026-06-23 from taxonomy CSV)
        -- TWA  = Turnstile Wheelchair    (same family as TT_ — functionally GATE)
        -- TEX  = Turnstile Exit Only     (same family as TT_)
        -- NOTE: DEVICE_CONTROL_GROUP_TYPE_NAME is NOT used — real values are
        --       'Bus Groups'/'Rail'/'Ticket Sale'/'CC Room', never 'RVG'/'HBG'/'SAG'
        WHEN UPPER(d.DEVICE_TYPE_NAME) LIKE '%GATE%'
          OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%BARRIER%'
          OR UPPER(d.DEVICE_TYPE_NAME) IN ('RVG','SAG','HBG')
          OR UPPER(d.DEVICE_TYPE_NAME) IN ('TT_','TTC','TWA','TEX')
          OR UPPER(COALESCE(ndt.SHORT_DESC,'')) IN ('ENG','EXG','RVG','SAG','HBG')
          OR UPPER(COALESCE(ndt.DESCRIPTION,'')) LIKE '%GATE%'
          OR UPPER(COALESCE(ndt.DESCRIPTION,'')) LIKE '%BARRIER%'        THEN 'GATE'

        -- ── VALIDATOR: bus-mounted fare payment devices ───────────────────────
        -- Primary path: NCS BUS_DEVICE_FLAG=TRUE (4,171 BMV devices, NCS-confirmed)
        -- NCS SHORT_DESC: BMV = Bus Mobile Validator, CMV = Cubic Mobile Validator,
        --                 MPV = Mobile Phone Validator, PIM = Passenger Interface Device,
        --                 DCU = Data Capture Unit, FBX = Fare Box
        WHEN COALESCE(CAST(ndt.BUS_DEVICE_FLAG AS BOOLEAN), FALSE) = TRUE
          AND (UPPER(d.DEVICE_TYPE_NAME) LIKE '%BMV%'
            OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%FBX%'
            OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%DCU%'
            OR UPPER(COALESCE(ndt.SHORT_DESC,'')) IN ('BMV','CMV','MPV','PIM','DCU','FBX'))
                                                                         THEN 'VALIDATOR'

        -- ── NOTE: READER is NOT a mars_device_category (reframed 2026-06-23) ────
        -- Michael confirmed: RSV/CSC are COMPONENT_TYPE codes inside parent TVM/BMV/GATE/RTL.
        -- There are no standalone reader devices in DEVICE_DIMENSION.
        -- Reader failures live in device_event_enriched at component grain (COMPONENT_TYPE_*).
        -- Gold reader models filter device_event_enriched WHERE COMPONENT_TYPE = reader.
        -- These codes (RSV, CSC, RMV, PTD, MPE, SMART CARD VALIDATOR) → OTHER (infra/sub-component).
        -- See Reader_Component_Modeling_Reframe_22Jun2026.md for full rationale.

        -- ── EDW name-only fallbacks (8,713 devices with no NCS match) ─────────

        -- VALIDATOR fallback: FBX = Fare Box (bus-mounted, 2,477 devices, EDW-only)
        WHEN UPPER(d.DEVICE_TYPE_NAME) LIKE '%BMV%'
          OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%FBX%'
          OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%DCU%'
          OR UPPER(d.DEVICE_TYPE_NAME) LIKE '%VALIDATOR%'                THEN 'VALIDATOR'

        -- TVM fallback — BTP at PortableFarebox = bus-mounted farebox → VALIDATOR
        --               BTP elsewhere (depots, garages, divisions) → TVM
        WHEN UPPER(d.DEVICE_TYPE_NAME) IN ('BTP','BFP','BPM')
          AND UPPER(COALESCE(d.FACILITY_NAME,'')) LIKE '%PORTABLEFAREBOX%'
                                                                         THEN 'VALIDATOR'
        WHEN UPPER(d.DEVICE_TYPE_NAME) IN ('BTP','BFP','BPM','AVM','EVM') THEN 'TVM'

        -- VALIDATOR: ABP = Automated Bus Payment (CTA on-bus validator)
        WHEN UPPER(d.DEVICE_TYPE_NAME) = 'ABP'                          THEN 'VALIDATOR'

        -- Catch-all: any remaining device where NCS confirms it is bus-mounted
        WHEN COALESCE(CAST(ndt.BUS_DEVICE_FLAG AS BOOLEAN), FALSE) = TRUE THEN 'VALIDATOR'

        -- OTHER: ECX/SCR/CRV/SIC/POS/COS/AMR (~827 devices) = infra/back-office/retail (confirmed EXCLUDE)
        -- Pending Michael (Cubic) confirmation: FBX category, BTP scope, AVM/EVM/TT_-family in-scope
        ELSE 'OTHER'
    END                                                                  AS mars_device_category,

    (d.CURRENT_FLAG = 1)                                                 AS is_current,
    (d.DEVICE_STATUS_ID = 1)                                             AS is_active

FROM      mars_dev.bronze.edw_device_dimension                                                                     d
LEFT JOIN mars_dev.bronze.ncs_stage_device                                                                         nd  ON nd.DEVICE_ID      = d.DEVICE_ID
LEFT JOIN mars_dev.bronze.ncs_stage_device_type                                                                    ndt ON ndt.DEVICE_TYPE_ID = nd.DEVICE_TYPE_ID
WHERE d.OPERATOR_ID > 0;
-- SCD2 fix: CURRENT_FLAG = 1 filter removed — load all 189,767 rows (current + historical).
-- Downstream joins must use AND dd.is_current = TRUE to get the active row per DEVICE_ID.

-- Post-build verification:
-- SELECT COUNT(*)                     AS total_rows,           -- Expect 189,570
--        SUM(CASE WHEN is_current  = TRUE  THEN 1 ELSE 0 END) AS current_rows,   -- Expect 16,375
--        SUM(CASE WHEN is_current  = FALSE THEN 1 ELSE 0 END) AS historical_rows, -- Expect 173,195
--        COUNT(DISTINCT mars_device_category)                  AS categories,     -- Expect 5
--        COUNT(DISTINCT FACILITY_ID)                           AS facilities,     -- Expect 25
--        SUM(CASE WHEN effective_to IS NULL THEN 1 ELSE 0 END) AS null_eff_to     -- Expect 16,375
-- FROM mars_dev.silver.dim_device;
