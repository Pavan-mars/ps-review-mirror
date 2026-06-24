-- =============================================================================
-- silver.dim_event_type
-- Event type dimension — component_subsystem, severity_label,
-- applies_to_* device flags, and is_oos_event
--
-- Sources (mars_dev.bronze / silver catalog):
--   EDW.EVENT_TYPE_DIMENSION  (441 rows, 6 cols)  — primary source
--   NCS_STAGE.EVENT           (422 rows)           — SHORT_DESC + DESCRIPTION
--   silver.dim_event_matrix   (S03, 155 rows)     — event_priority, oos_counted_*_kpi,
--                                                    requires_service_call, is_set_clear
--                                                    LEFT JOIN: NULL for codes not in S03
--
-- Validation run 2026-06-15:
--   NCS join: 97.7% match rate (431/441); join key ne.EVENT_ID = et.EVENT_TYPE_ID ✓
--   SEVERITY: 94.8% NULL (418/441 rows — only 23 event types have explicit severity)
--   SEVERITY=10 on MARTA events (10106, 10110) — cross-property codes; confirm Cubic
--   OOS events after full rewrite (2026-06-16): 102 total (validated)
--     60 explicit codes from Cubic doc 9604-60007 + 42 name-based (%OOS%/%OUT OF SERVICE%)
--     Previous logic flagged only 7 events (replaces SEVERITY-based approach)
--
-- component_subsystem ID range map (complete, all 441 event types mapped):
--   0          SYSTEM        Null sentinel event
--   16–17      SYSTEM        Legacy battery events (pre-range taxonomy)
--   45,46,52   CHU           Legacy cashbox events (pre-range taxonomy)
--   100–199    SYSTEM        General: power, comms, SW, tables, bus sync, temp
--   200–299    CSC_READER    Smart card transport (SCT) + CSC target reader
--   300–399    SCRST         Smart card roll stock transport (FMVD only)
--   400–499    BHU           Bank-note handling unit (bill acceptor + vault)
--   500–599    CHU           Cash/coin handling unit
--   600–699    SYSTEM        Disk, SW checksum, config, agency mismatch
--   700–799    SYSTEM        Softkey, display comms
--   800–899    PIN_PAD       PIN pad (FMVD only)
--   900–1099   PRINTER       Receipt printer (original 900–999 + extended 1000–1099)
--   1100–1199  (no codes defined — gap confirmed from Cubic doc)
--   1200–1299  GATE_MECH     Gate mechanical: GDI board (1203), 68k board (1228)
--   1300–1399  SYSTEM        Maintenance handler events
--   1400–1499  ALARM         Alarm module (vibration, intrusion, panic) — FMVD only
--   1500–1599  SYSTEM        Audio/voice module
--   1600–1699  BANKCARD      DIP bankcard reader (FMVD only)
--   1700–1799  SYSTEM        Passenger info display (PID)
--   1800–1899  SYSTEM        Camera, cash drawer, barcode reader
--   1900–1999  SYSTEM        Physical enclosure sensors (cover/door)
--   2000–2099  FAREBOX       Bus farebox events (RDM, driver, vault, data transfer)
--   2100–2199  DEVICE_STATE  HPOV device state changes (2102 = DSOOS)
--   2200–2299  DOPP          Device Open Payment Processor (contactless bankcard module)
--   11131      PRINTER       Stray printer event (Prn Paper Low)
--   10000–49999 LEGACY       Cross-property MARTA codes (10106, 10110 SEV=10)
--   50000+     COMMS         Communications (50101 = Heartbeat Lost — server-generated)
--
-- DOPP clarified 2026-06-16 from Cubic doc:
--   DOPP = Device Open Payment Processor
--   Manages contactless bankcard taps (Visa/MC/Amex), negative/positive lists,
--   BIN list sync, TAP encryption. Present on gates and bus validators.
--   2201 DOPP OOS is the primary indicator; 22XX sub-events give detail.
--
-- Code 2011 anomaly: "DOPP Negative List Digest Changed" appears in Cubic doc
--   with code 2011 (FAREBOX range 2000–2099). Likely a typo for 2211.
--   Confirm with Cubic before classifying.
--
-- Device applicability (from Cubic doc columns Gate/Bus/FMVD):
--   GATE:      SYSTEM, CSC_READER, GATE_MECH, DEVICE_STATE, DOPP, COMMS
--   BUS:       SYSTEM, CSC_READER, FAREBOX, DEVICE_STATE, DOPP, COMMS
--   TVM/FMVD:  SYSTEM, CSC_READER, SCRST, BHU, CHU, PIN_PAD, PRINTER,
--              ALARM, BANKCARD, COMMS
--
-- is_oos_event rewrite 2026-06-16 (Cubic doc 9604-60007):
--   Previous approach: SEVERITY >= 2 in SCRST/GATE_MECH only → 7 events flagged
--   New approach: explicit OOS whitelist from Cubic doc OOS column → 60+ events
--   NOTE: OOS whitelist covers hardware + commanded OOS + maintenance OOS.
--         PS1 notebooks should exclude commanded (110, 208, 519, 1603, 1604) and
--         maintenance (106, 151) codes when computing failure labels.
--
-- Bugs fixed from original (updated from 5 to 8):
--   BUG 1: ne.EVENT_NAME        → ne.SHORT_DESC     (column missing in NCS parquet)
--   BUG 2: ne.EVENT_DESCRIPTION → ne.DESCRIPTION    (column missing in NCS parquet)
--   BUG 3: is_oos_event SEVERITY >= 2 with NULL → COALESCE(SEVERITY,0) → REPLACED
--           Replaced entirely with explicit code whitelist (see above)
--   BUG 4: 83 event types fell to component_subsystem = 'OTHER' → All ranges mapped
--   BUG 5: ID 2102 (DSOOS) and ID 1004 (Prt Comm OOS) missed by SEVERITY logic
--           → Now captured in explicit OOS whitelist
--   BUG 6: silver.dim_event_type → mars_dev.silver.dim_event_type
--   BUG 7: is_oos_event only 7 events → rewritten to 60+ from Cubic doc 9604-60007
--   BUG 8: applies_to_gate/bus/tvm columns missing → added from Cubic Gate/Bus/FMVD cols
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.dim_event_type;

CREATE TABLE mars_dev.silver.dim_event_type AS
SELECT
    et.EVENT_TYPE_KEY,
    et.EVENT_TYPE_ID,
    et.EVENT_SOURCE,
    et.EVENT_TYPE_NAME,
    et.EVENT_TYPE_DESC,
    et.SEVERITY,

    -- ── component_subsystem ──────────────────────────────────────────────────
    -- All 441 event types fully mapped; gap 1100-1199 confirmed empty in Cubic doc
    CASE
        -- Legacy low-ID events (pre-range taxonomy)
        WHEN et.EVENT_TYPE_ID = 0                         THEN 'SYSTEM'
        WHEN et.EVENT_TYPE_ID IN (16, 17)                 THEN 'SYSTEM'
        WHEN et.EVENT_TYPE_ID IN (45, 46, 52)             THEN 'CHU'

        -- Standard Cubic event taxonomy ranges
        WHEN et.EVENT_TYPE_ID BETWEEN 100  AND 199        THEN 'SYSTEM'
        WHEN et.EVENT_TYPE_ID BETWEEN 200  AND 299        THEN 'CSC_READER'
        WHEN et.EVENT_TYPE_ID BETWEEN 300  AND 399        THEN 'SCRST'
        WHEN et.EVENT_TYPE_ID BETWEEN 400  AND 499        THEN 'BHU'
        WHEN et.EVENT_TYPE_ID BETWEEN 500  AND 599        THEN 'CHU'
        WHEN et.EVENT_TYPE_ID BETWEEN 600  AND 699        THEN 'SYSTEM'
        WHEN et.EVENT_TYPE_ID BETWEEN 700  AND 799        THEN 'SYSTEM'
        WHEN et.EVENT_TYPE_ID BETWEEN 800  AND 899        THEN 'PIN_PAD'
        -- PRINTER: original 900–999 + extended 1000–1099
        WHEN et.EVENT_TYPE_ID BETWEEN 900  AND 1099       THEN 'PRINTER'
        -- 1100-1199: no codes in Cubic doc; falls to ELSE safely
        WHEN et.EVENT_TYPE_ID BETWEEN 1200 AND 1299       THEN 'GATE_MECH'
        WHEN et.EVENT_TYPE_ID BETWEEN 1300 AND 1399       THEN 'SYSTEM'
        WHEN et.EVENT_TYPE_ID BETWEEN 1400 AND 1499       THEN 'ALARM'
        WHEN et.EVENT_TYPE_ID BETWEEN 1500 AND 1599       THEN 'SYSTEM'
        WHEN et.EVENT_TYPE_ID BETWEEN 1600 AND 1699       THEN 'BANKCARD'
        WHEN et.EVENT_TYPE_ID BETWEEN 1700 AND 1799       THEN 'SYSTEM'
        WHEN et.EVENT_TYPE_ID BETWEEN 1800 AND 1899       THEN 'SYSTEM'
        WHEN et.EVENT_TYPE_ID BETWEEN 1900 AND 1999       THEN 'SYSTEM'
        -- FAREBOX: bus farebox events (code 2011 may be misplaced DOPP — confirm Cubic)
        WHEN et.EVENT_TYPE_ID BETWEEN 2000 AND 2099       THEN 'FAREBOX'
        -- DEVICE_STATE: HPOV state changes (2102 = DSOOS = device OOS trigger)
        WHEN et.EVENT_TYPE_ID BETWEEN 2100 AND 2199       THEN 'DEVICE_STATE'
        -- DOPP: Device Open Payment Processor (contactless bankcard module)
        WHEN et.EVENT_TYPE_ID BETWEEN 2200 AND 2299       THEN 'DOPP'
        -- Stray printer event above the main ranges
        WHEN et.EVENT_TYPE_ID = 11131                     THEN 'PRINTER'
        -- Cross-property MARTA event codes (not in CTA range taxonomy)
        WHEN et.EVENT_TYPE_ID BETWEEN 10000 AND 49999     THEN 'LEGACY'
        -- Server-generated communications events
        WHEN et.EVENT_TYPE_ID >= 50000                    THEN 'COMMS'
        ELSE 'OTHER'
    END                                                   AS component_subsystem,

    -- ── severity_label ───────────────────────────────────────────────────────
    -- 94.8% of event types have NULL SEVERITY → default 'DEBUG'
    -- SEVERITY=10 on MARTA events (10106, 10110) maps to CRITICAL — confirm with Cubic
    -- whether these cross-property codes should be included in Chicago PS1/PS2 training
    CASE
        WHEN et.SEVERITY IS NULL   THEN 'DEBUG'
        WHEN et.SEVERITY >= 3      THEN 'CRITICAL'
        WHEN et.SEVERITY = 2       THEN 'WARN'
        WHEN et.SEVERITY = 1       THEN 'INFO'
        ELSE                            'DEBUG'
    END                                                   AS severity_label,

    -- ── is_oos_event ─────────────────────────────────────────────────────────
    -- Rewritten 2026-06-16 from Cubic doc 9604-60007 (OOS column).
    -- Previous SEVERITY-based logic flagged only 7 events; doc shows 60+.
    --
    -- SYSTEM range (100-199): hardware, SW, comms, table failures
    -- CSC_READER range (200-299): smart card transport + reader failures
    -- SCRST range (300-399): roll stock transport failures
    -- BHU range (400-499): banknote handling failures
    -- CHU range (500-599): coin handling failures
    -- SYSTEM range (600-699): disk full, SW checksum, config errors
    -- GATE_MECH range (1200-1299): gate board failures
    -- ALARM range (1400-1499): all alarm events put FMVD OOS
    -- DEVICE_STATE range (2100-2199): DSOOS device state change
    -- DOPP range (2200-2299): DOPP module OOS conditions
    -- COMMS range (50000+): heartbeat lost (server-generated)
    --
    -- NOTE for PS1 notebooks:
    --   Commanded/maintenance OOS codes (not hardware failures):
    --     106 = Employee Logon, 110 = Commanded OOS, 151 = Maintenance Mode,
    --     208 = SCT Commanded OOS, 519 = CHU OOS by Command,
    --     1603 = No Credit by Cmd, 1604 = No Debit by Cmd
    --   Exclude these when building failure labels for PS1 training.
    (
        et.EVENT_TYPE_ID IN (
            -- ── SYSTEM (100-199) ──────────────────────────────────────────
            101,    -- Power Fail (FMVD OOS)
            106,    -- Employee Logon/Logoff (BMV, RMV, FMVD OOS — commanded/maintenance)
            108,    -- Door Open (RMV, FMVD OOS)
            109,    -- Loss of Comms with Local Devices (Gate OOS)
            110,    -- Commanded OOS (BMV, RMV, FMVD OOS — commanded)
            113,    -- Uninitialized (BMV, RMV, FMVD OOS)
            118,    -- High Temperature (FMVD OOS)
            119,    -- Emergency Mode (Gate OOS — freewheel mode)
            131,    -- Missing Tables (BMV, RMV, FMVD OOS)
            132,    -- Incorrect Tables (FMVD OOS)
            138,    -- Alarm Triggered (FMVD OOS)
            141,    -- Software Activation Failed (device OOS)
            143,    -- Software Error Storage Card (device OOS)
            144,    -- Battery Disconnected (device OOS)
            151,    -- Maintenance Mode (device OOS — maintenance)
            152,    -- Software Digest Missing (BMV, RMV, FMVD OOS)
            153,    -- Software Digest Invalid (BMV, RMV, FMVD OOS)
            158,    -- Battery Charge Failed (BMV, RMV OOS)
            171,    -- Invalid Device ID (Gate OOS)
            172,    -- Orphan Mode Timeout (BMV, RMV OOS)

            -- ── CSC_READER (200-299) ──────────────────────────────────────
            201,    -- CSC Target Fault (BMV, RMV OOS)
            204,    -- Magazine Empty (FMVD OOS if both magazines empty)
            205,    -- SCT Transport Jam (FMVD OOS)
            208,    -- SCT Commanded OOS (FMVD OOS — commanded)
            209,    -- Tri-Reader Comms Error (FMVD OOS)
            212,    -- Capture Bin Full (FMVD OOS)
            220,    -- Missing Keys / SAM Issues (BMV, RMV OOS)
            221,    -- SCT OOS (FMVD OOS)
            222,    -- SCRST OOS (FMVD OOS)
            223,    -- Door Target OOS (FMVD OOS)
            228,    -- Magazine 1 Feed Error (FMVD OOS if both magazine errors)
            229,    -- Magazine 2 Feed Error (FMVD OOS if both magazine errors)
            230,    -- SCRST Roll 1 Feed Error (FMVD OOS if both rolls fail)
            231,    -- SCRST Roll 2 Feed Error (FMVD OOS if both rolls fail)

            -- ── SCRST (300-399) ───────────────────────────────────────────
            304,    -- Roll Stock Empty (FMVD OOS)
            305,    -- Jam In Transport (FMVD OOS)
            308,    -- SCRST Transport Fault (FMVD OOS)
            309,    -- Shaft Encoder Failure (FMVD OOS)
            310,    -- DAC Failure (FMVD OOS)
            311,    -- DIO Failure (FMVD OOS)
            312,    -- Power Failure (FMVD OOS)
            313,    -- Sensor Calibration Error (FMVD OOS)
            315,    -- SCRST Comms Error (FMVD OOS)
            317,    -- SCRST Capture Bin Full (FMVD OOS)

            -- ── BHU (400-499) ─────────────────────────────────────────────
            401,    -- Bill Jam (FMVD OOS — bill payment unavailable)
            402,    -- BHU Error (FMVD OOS)
            403,    -- BHU Comms Error (FMVD OOS)
            406,    -- Vault Full (FMVD OOS)
            407,    -- Vault Removed or Rejected (FMVD OOS)
            408,    -- Code Mismatch (FMVD OOS)
            410,    -- No Bills Accepted (FMVD OOS)

            -- ── CHU (500-599) ─────────────────────────────────────────────
            504,    -- Jam in Tubes (FMVD OOS)
            507,    -- Cassette Removed (FMVD OOS)
            508,    -- Jam in Acceptor (FMVD OOS)
            509,    -- Throat Blocker Fail (FMVD OOS)
            510,    -- Routing Error (FMVD OOS)
            511,    -- CHU Comms Error (FMVD OOS)
            513,    -- Vault Full (FMVD OOS)
            514,    -- Coin Door Open (FMVD OOS)
            516,    -- Cash Box Removed (FMVD OOS)
            517,    -- Cash Box Error (FMVD OOS)
            519,    -- OOS by Command (FMVD OOS — commanded)
            521,    -- Coin Vault Door Open (FMVD OOS)
            534,    -- No Coins Accepted (FMVD OOS)
            536,    -- Invalid Cash Box (FMVD OOS)
            543,    -- Jam in Coin Chute (FMVD OOS)

            -- ── SYSTEM (600-699) ──────────────────────────────────────────
            603,    -- Disk Full (BMV, RMV, FMVD OOS)
            604,    -- SW Checksum Error (FMVD OOS)
            611,    -- Configuration File Error / No USB (Bus OOS)

            -- ── GATE_MECH (1200-1299) ─────────────────────────────────────
            1203,   -- Barrier Comms Error / GDI board comms (Gate OOS)
            1228,   -- 68k OOS (Gate OOS)

            -- ── ALARM (1400-1499) — all alarm events put FMVD OOS ────────
            1401,   -- Alarm Comms Error (FMVD OOS)
            1402,   -- Vibration Sensor Alarm (FMVD OOS)
            1403,   -- Vibration Sensor Malfunction (FMVD OOS)
            1404,   -- Alarm Battery Low (FMVD OOS)
            1406,   -- Alarm Deactivated with Key (FMVD OOS)
            1407,   -- Panic Button Pushed (FMVD OOS)
            1408,   -- Intrusion Alarm (FMVD OOS)
            1409,   -- Manipulation on Alarm (FMVD OOS)

            -- ── DEVICE_STATE (2100-2199) ──────────────────────────────────
            2102,   -- DSOOS = device out-of-service state change (HPOV trigger)

            -- ── DOPP (2200-2299) ──────────────────────────────────────────
            2201,   -- DOPP Out of Service (primary DOPP OOS indicator)
            2202,   -- DOPP Comms Error (DOPP OOS)
            2203,   -- DOPP Component Failed to Start (DOPP OOS)
            2206,   -- DOPP Taps Table Full (BMV, RMV OOS)
            2207,   -- DOPP Database Failure (DOPP OOS)
            2213,   -- DOPP Missing Required Data (DOPP OOS)

            -- ── COMMS (50000+) ────────────────────────────────────────────
            50101   -- Heartbeat Communication Lost (all device types OOS)
        )
        -- Name-based fallback for OOS events not in the whitelist above
        OR UPPER(et.EVENT_TYPE_NAME) LIKE '%OOS%'
        OR UPPER(et.EVENT_TYPE_DESC) LIKE '%OUT OF SERVICE%'
        OR UPPER(et.EVENT_TYPE_DESC) LIKE '%COMMANDED OOS%'
    )                                                     AS is_oos_event,

    -- ── is_hardware_oos_event ─────────────────────────────────────────────────
    -- Hardware failures only — commanded + maintenance OOS codes removed.
    -- USE THIS for PS1 failure labels and silver.device_outage (S18).
    -- Do NOT use is_oos_event for labels — it includes operator-triggered OOS.
    --
    -- Removed vs is_oos_event:
    --   106 Employee Logon, 110 Commanded OOS, 151 Maintenance Mode,
    --   208 SCT Commanded OOS, 519 OOS by Command (CHU), 1603/1604 No Credit/Debit by Cmd
    (
        et.EVENT_TYPE_ID IN (
            -- SYSTEM (100-199) — 106/110/151 removed
            101, 108, 109, 113, 118, 119, 131, 132, 138, 141, 143, 144,
            152, 153, 158, 171, 172,
            -- CSC_READER (200-299) — 208 removed
            201, 204, 205, 209, 212, 220, 221, 222, 223, 228, 229, 230, 231,
            -- SCRST (300-399)
            304, 305, 308, 309, 310, 311, 312, 313, 315, 317,
            -- BHU (400-499)
            401, 402, 403, 406, 407, 408, 410,
            -- CHU (500-599) — 519 removed
            504, 507, 508, 509, 510, 511, 513, 514, 516, 517, 521, 534, 536, 543,
            -- SYSTEM (600-699)
            603, 604, 611,
            -- GATE_MECH (1200-1299)
            1203, 1228,
            -- ALARM (1400-1499)
            1401, 1402, 1403, 1404, 1406, 1407, 1408, 1409,
            -- DEVICE_STATE (2100-2199)
            2102,
            -- DOPP (2200-2299)
            2201, 2202, 2203, 2206, 2207, 2213,
            -- COMMS (50000+)
            50101
        )
        OR (
            (UPPER(et.EVENT_TYPE_NAME) LIKE '%OOS%'
             OR UPPER(et.EVENT_TYPE_DESC) LIKE '%OUT OF SERVICE%')
            AND et.EVENT_TYPE_ID NOT IN (106, 110, 151, 208, 519, 1603, 1604)
        )
    )                                                     AS is_hardware_oos_event,

    -- ── is_commanded_oos_event ────────────────────────────────────────────────
    -- Operator-triggered OOS — NOT hardware failures.
    -- Exclude from PS1 labels. Useful for PS3 root-cause context.
    -- NOTE: Code 156 "Commanded OOS by Tables" also appears commanded-OOS in Device
    --       Event Matrix but is NOT listed here pending Michael clarification (Rail too?).
    et.EVENT_TYPE_ID IN (106, 110, 151, 208, 519, 1603, 1604)
                                                          AS is_commanded_oos_event,

    -- ── is_reader_event ──────────────────────────────────────────────────────
    -- CSC_READER subsystem: all events in the 200-299 range.
    -- Captures both gate/bus reader events (201 CSC Target Fault, 220 Missing Keys,
    -- 237 Bad Sam) and FMVD smart card transport events (205/221/222/223 SCT/SCRST).
    -- Use this flag to filter device_event_enriched to reader-component grain for PS2/PS3.
    -- Downstream: WHERE is_reader_event = TRUE AND COMPONENT_TYPE = 'CSC_READER'
    (et.EVENT_TYPE_ID BETWEEN 200 AND 299)                AS is_reader_event,

    -- ── Device applicability flags ───────────────────────────────────────────
    -- Derived from Cubic doc Gate/Bus/FMVD columns (doc 9604-60007)
    -- Approximated by component_subsystem; more precise per-code mapping
    -- would require a full lookup table (future enhancement).
    --
    -- GATE:  SYSTEM, CSC_READER, GATE_MECH, DEVICE_STATE, DOPP, COMMS
    -- BUS:   SYSTEM, CSC_READER, FAREBOX, DEVICE_STATE, DOPP, COMMS
    -- TVM:   SYSTEM, CSC_READER, SCRST, BHU, CHU, PIN_PAD, PRINTER,
    --        ALARM, BANKCARD, COMMS
    CASE
        WHEN CASE
            WHEN et.EVENT_TYPE_ID = 0                     THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID IN (16,17)              THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID IN (45,46,52)           THEN 'CHU'
            WHEN et.EVENT_TYPE_ID BETWEEN 100  AND 199    THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 200  AND 299    THEN 'CSC_READER'
            WHEN et.EVENT_TYPE_ID BETWEEN 600  AND 699    THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 700  AND 799    THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 1200 AND 1299   THEN 'GATE_MECH'
            WHEN et.EVENT_TYPE_ID BETWEEN 1300 AND 1399   THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 1500 AND 1599   THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 1700 AND 1799   THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 1800 AND 1899   THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 1900 AND 1999   THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 2100 AND 2199   THEN 'DEVICE_STATE'
            WHEN et.EVENT_TYPE_ID BETWEEN 2200 AND 2299   THEN 'DOPP'
            WHEN et.EVENT_TYPE_ID >= 50000                THEN 'COMMS'
            ELSE NULL
        END IS NOT NULL THEN TRUE
        ELSE FALSE
    END                                                   AS applies_to_gate,

    CASE
        WHEN CASE
            WHEN et.EVENT_TYPE_ID = 0                     THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID IN (16,17)              THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 100  AND 199    THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 200  AND 299    THEN 'CSC_READER'
            WHEN et.EVENT_TYPE_ID BETWEEN 600  AND 699    THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 700  AND 799    THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 1300 AND 1399   THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 1500 AND 1599   THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 1700 AND 1799   THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 1800 AND 1899   THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 1900 AND 1999   THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 2000 AND 2099   THEN 'FAREBOX'
            WHEN et.EVENT_TYPE_ID BETWEEN 2100 AND 2199   THEN 'DEVICE_STATE'
            WHEN et.EVENT_TYPE_ID BETWEEN 2200 AND 2299   THEN 'DOPP'
            WHEN et.EVENT_TYPE_ID >= 50000                THEN 'COMMS'
            ELSE NULL
        END IS NOT NULL THEN TRUE
        ELSE FALSE
    END                                                   AS applies_to_bus,

    CASE
        WHEN CASE
            WHEN et.EVENT_TYPE_ID = 0                     THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID IN (16,17)              THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID IN (45,46,52)           THEN 'CHU'
            WHEN et.EVENT_TYPE_ID BETWEEN 100  AND 199    THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 200  AND 299    THEN 'CSC_READER'
            WHEN et.EVENT_TYPE_ID BETWEEN 300  AND 399    THEN 'SCRST'
            WHEN et.EVENT_TYPE_ID BETWEEN 400  AND 499    THEN 'BHU'
            WHEN et.EVENT_TYPE_ID BETWEEN 500  AND 599    THEN 'CHU'
            WHEN et.EVENT_TYPE_ID BETWEEN 600  AND 699    THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 700  AND 799    THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 800  AND 899    THEN 'PIN_PAD'
            WHEN et.EVENT_TYPE_ID BETWEEN 900  AND 1099   THEN 'PRINTER'
            WHEN et.EVENT_TYPE_ID BETWEEN 1300 AND 1399   THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 1400 AND 1499   THEN 'ALARM'
            WHEN et.EVENT_TYPE_ID BETWEEN 1500 AND 1599   THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 1600 AND 1699   THEN 'BANKCARD'
            WHEN et.EVENT_TYPE_ID BETWEEN 1700 AND 1799   THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 1800 AND 1899   THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID BETWEEN 1900 AND 1999   THEN 'SYSTEM'
            WHEN et.EVENT_TYPE_ID = 11131                 THEN 'PRINTER'
            WHEN et.EVENT_TYPE_ID >= 50000                THEN 'COMMS'
            ELSE NULL
        END IS NOT NULL THEN TRUE
        ELSE FALSE
    END                                                   AS applies_to_tvm,

    -- ── NCS event reference ──────────────────────────────────────────────────
    -- NCS_STAGE.EVENT has SHORT_DESC and DESCRIPTION (not EVENT_NAME/EVENT_DESCRIPTION)
    ne.SHORT_DESC                                         AS ncs_event_name,
    ne.DESCRIPTION                                        AS ncs_event_description,

    -- ── S03 dim_event_matrix enrichment (Michael R2 Device Event Matrix Excel) ──
    -- 155-row lookup from "Copy of Device Event Matrix with Context.xlsx"
    -- NULL for event codes not in Michael's Device Event Matrix (codes absent from S03)
    -- S03 is the authoritative source for KPI counting rules and service call flags.
    em.event_priority,          -- 1=critical, 2=high, 3=medium, 4=low (NULL if not in S03)
    em.oos_counted_gate_kpi,    -- TRUE = this OOS event counts toward gate availability KPI
    em.oos_counted_bus_kpi,     -- TRUE = this OOS event counts toward bus availability KPI
    em.oos_counted_fmvd_kpi,    -- TRUE = this OOS event counts toward FMVD/TVM KPI
    em.requires_service_call,   -- TRUE = event requires field technician dispatch
    em.is_set_clear             -- TRUE = event fires in Set/Clear pairs (not one-shot)

FROM      mars_dev.bronze.edw_event_type_dimension et
LEFT JOIN mars_dev.bronze.ncs_stage_event          ne
    ON ne.EVENT_ID = et.EVENT_TYPE_ID
LEFT JOIN mars_dev.silver.dim_event_matrix         em
    ON em.event_code_id = et.EVENT_TYPE_ID;
-- NOTE: S03 must be built before S07 (dim_event_type depends on dim_event_matrix).
-- Build order: S03 → S07 → S16 → S18 → ...

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.dim_event_type ZORDER BY (EVENT_TYPE_ID);
