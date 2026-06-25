-- =============================================================================
-- S03: silver.dim_event_matrix
-- S-code: S03  |  Build file: 21  |  Status: READY (hardcoded from Michael's file)
--
-- PURPOSE:
--   Event code reference table from Cubic Device Event Matrix (Michael R2 Excel).
--   Source file: "Copy of Device Event Matrix with Context.xlsx"
--   Sheet: "Dev SW Improvements - Events"  |  157 rows, 24 source columns
--
-- This table is the authoritative per-code lookup for:
--   - which device types each event applies to (gate/bus/fmvd)
--   - whether the event causes OOS (is_oos)
--   - whether it is operator-commanded (is_commanded_oos) -- for PS1 label exclusion
--   - whether it is a reader component event (is_reader_event) -- for PS2/PS3
--   - Ventra KPI counting rules (oos_counted_*_kpi)
--   - event priority (1=critical, 2=high, 3=medium, 4=low)
--   - whether a service call is required (requires_service_call)
--
-- DEDUPLICATION NOTES:
--   Code 1401 appears twice in source (priority 1 vs 2; KPI differs).
--     Resolution: priority=1 row retained (higher severity takes precedence).
--   Code 2231 appears twice ("Positive List Behind" / "Positive List Far Behind").
--     Resolution: first row retained (both have same flags).
--
-- DATA QUALITY ANOMALIES FROM SOURCE:
--   Code 121:  No name in source. Set to 'Farebox ID Changed by DCU' from description.
--   Code 1228: OOS_Gate_KPI = "Y?" in source (uncertain). Stored as TRUE with note.
--   Code 156:  Commanded OOS but not in our is_commanded_oos_event list (pending Michael).
--              Marked is_commanded_oos = TRUE here; S07 list unchanged until confirmed.
--   Code 2004: Appears in 2200-range section but numbered 2004 (likely typo for 2204).
--              Stored as 2004 with component_subsystem = FAREBOX until Cubic confirms.
--
-- CONFIRMED DECISIONS (2026-06-24):
--   D52  Event 151 = 'Maintenance Mode' is the correct code for scheduled maintenance.
--        is_commanded_oos=TRUE, event_priority=4 (Info-level - not a hardware fault).
--        S19 maintenance_ledger maps code 151 -> ledger_type='MAINTENANCE_MODE' (confirmed).
--        Codes 145-150: NOT in source matrix (gap between 144=Battery Disconnected and
--        151=Maintenance Mode). If codes 145-150 appear in Oracle DEVICE_EVENT they will
--        fall through all joins as unclassified - raise with Michael if count > 0.
--   D52a Event 148 specifically: NOT in matrix; status unknown. Query to verify:
--        SELECT COUNT(*) FROM EDW.DEVICE_EVENT WHERE EVENT_TYPE_ID = 148 AND TRANSIT_DAY_KEY >= 20240101;
--
-- JOIN TARGETS:
--   silver.dim_event_type:          ON event_code_id = EVENT_TYPE_ID  (add flags via join)
--   silver.device_event_enriched:   ON event_code_id = EVENT_TYPE_ID  (per-event context)
--   gold PS1/PS3/PS4:               event feature engineering
--
-- Michael R2 confirmation 2026-06-23:
--   Matrix is the definitive OOS/KPI flag source. Use this table, not SEVERITY logic.
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.dim_event_matrix;

CREATE TABLE mars_dev.silver.dim_event_matrix AS
SELECT
    t.event_code_id,
    t.event_name,
    t.applies_to_gate,
    t.applies_to_bus,
    t.applies_to_fmvd,
    t.is_oos,
    t.is_set_clear,
    t.requires_service_call,
    t.event_priority,
    t.is_commanded_oos,
    -- is_reader_event: CSC_READER subsystem (200-299 range, all device types)
    (t.event_code_id BETWEEN 200 AND 299)                  AS is_reader_event,
    t.oos_counted_gate_kpi,
    t.oos_counted_bus_kpi,
    t.oos_counted_fmvd_kpi,
    t.oos_counted_central_kpi

FROM (
    VALUES
    -- Columns: (event_code_id, event_name,
    --           gate, bus, fmvd,
    --           is_oos, is_set_clear, requires_service_call, event_priority,
    --           is_commanded_oos,
    --           oos_gate_kpi, oos_bus_kpi, oos_fmvd_kpi, oos_central_kpi)
    --
    -- -- SYSTEM range (100-199) ------------------------------------------------
    (101,  'Power Fail',                                        true,  true,  true,  true,  false, true,  2,    false, false, false, false, false),
    (104,  'Power Reset',                                       true,  true,  false, false, true,  false, 2,    false, true,  true,  true,  true),
    (105,  'System Time Sync Adjustment',                       true,  true,  true,  false, true,  true,  3,    false, true,  true,  true,  false),
    (106,  'Employee Logon/Logoff',                             true,  false, true,  true,  true,  false, 3,    true,  null,  null,  null,  null),
    (108,  'Door Open',                                         true,  false, true,  true,  false, true,  1,    false, null,  null,  null,  null),
    (109,  'Loss of Comms with Local Devices',                  true,  true,  false, true,  false, true,  2,    false, true,  true,  false, false),
    (110,  'Commanded OOS',                                     true,  true,  true,  true,  false, false, 1,    true,  null,  null,  null,  null),
    (112,  'Host Comms Lost',                                   true,  true,  true,  false, false, false, 4,    false, null,  null,  null,  null),
    (113,  'Uninitialized',                                     true,  true,  true,  true,  false, true,  1,    false, true,  true,  true,  false),
    (114,  'Battery Charging',                                  false, false, false, false, false, false, null, false, null,  null,  null,  null),
    (115,  'Battery Charge Complete',                           false, false, false, false, false, false, null, false, null,  null,  null,  null),
    (117,  'Temperature Warning',                               false, false, true,  false, false, false, 4,    false, null,  null,  null,  null),
    (118,  'High Temperature',                                  false, false, true,  true,  false, true,  2,    false, false, false, true,  false),
    (119,  'Emergency Mode',                                    true,  false, false, true,  false, false, 1,    false, null,  null,  null,  null),
    (121,  'Farebox ID Changed by DCU',                         false, true,  false, true,  false, false, null, false, null,  null,  null,  null),
    (122,  'Bus Number Changed',                                false, true,  false, false, true,  false, 4,    false, null,  null,  null,  null),
    (123,  'Home Garage Changed',                               false, true,  false, false, false, false, 4,    false, null,  null,  null,  null),
    (125,  'End of Day',                                        false, true,  false, false, false, false, 4,    false, null,  null,  null,  null),
    (126,  'Bus Sync',                                          false, true,  false, false, false, false, 4,    false, null,  null,  null,  null),
    (127,  'Upload Complete',                                   false, true,  false, false, false, false, 4,    false, null,  null,  null,  null),
    (128,  'Driver Terminal Software Download Complete',         false, true,  false, false, false, false, 4,    false, null,  null,  null,  null),
    (129,  'Table Switched',                                    true,  true,  true,  false, false, false, 4,    false, null,  null,  null,  null),
    (131,  'Missing Tables',                                    true,  true,  true,  true,  false, true,  1,    false, true,  true,  true,  false),
    (132,  'Incorrect Tables',                                  false, false, true,  true,  false, false, 1,    false, false, false, true,  false),
    (133,  'Non-validated Logon',                               false, true,  false, false, false, false, 4,    false, null,  null,  null,  null),
    (134,  'Device Restart',                                    true,  true,  true,  false, true,  false, 4,    false, null,  null,  null,  null),
    (135,  'New Software Received',                             true,  true,  true,  false, false, false, 4,    false, null,  null,  null,  null),
    (138,  'Alarm Triggered',                                   false, false, true,  true,  false, true,  1,    false, null,  null,  null,  null),
    (140,  'In Service',                                        true,  true,  true,  false, false, false, 4,    false, null,  null,  null,  null),
    (141,  'Software Activation Failed',                        true,  true,  false, false, true,  true,  2,    false, null,  null,  null,  null),
    (142,  'Table Activation Failed',                           false, false, true,  false, false, true,  3,    false, false, false, true,  false),
    (143,  'Software Error',                                    true,  true,  false, false, false, true,  2,    false, true,  true,  false, false),
    (144,  'Battery Disconnected',                              true,  true,  false, false, false, true,  2,    false, null,  null,  null,  null),
    (151,  'Maintenance Mode',                                  true,  true,  false, false, true,  false, 4,    true,  null,  null,  null,  null),
    (152,  'Software Digest Missing',                           true,  true,  true,  false, false, false, 3,    false, null,  null,  null,  null),
    (153,  'Software Digest Invalid',                           true,  true,  true,  false, false, false, 3,    false, null,  null,  null,  null),
    -- Code 156: Commanded OOS by Tables -- OOS confirmed; is_commanded_oos=TRUE pending Michael
    (156,  'Commanded OOS by Tables',                           true,  true,  false, true,  false, false, 1,    true,  null,  null,  null,  null),
    (158,  'Battery Charge Fail',                               true,  true,  false, false, true,  true,  2,    false, null,  null,  null,  null),
    (160,  'Card Purchasing Unavailable',                       false, false, true,  false, false, false, 2,    false, false, false, false, true),
    (161,  'Fare Product Purchasing Unavailable',               false, false, true,  false, false, false, 2,    false, false, false, false, true),
    (162,  'Transaction History Unavailable',                   false, false, true,  false, false, false, 2,    false, false, false, false, true),
    (163,  'Balance Inquiry Unavailable',                       false, false, true,  false, false, false, 2,    false, false, false, false, true),
    (165,  'Vehicle Ignition Off',                              false, true,  false, false, true,  false, 3,    false, null,  null,  null,  null),
    (168,  'Bus System Data Invalid',                           false, true,  false, false, true,  true,  3,    false, null,  null,  null,  null),
    (171,  'Invalid Device ID',                                 true,  false, false, true,  false, true,  1,    false, true,  false, false, false),
    (172,  'Orphan Mode Timeout',                               true,  true,  false, true,  false, true,  1,    false, true,  true,  false, false),
    --
    -- -- CSC_READER range (200-299) --------------------------------------------
    (201,  'CSC Target Fault',                                  true,  true,  false, true,  true,  true,  2,    false, true,  true,  false, false),
    (202,  'SCT UTT Comms Error',                               false, false, true,  false, false, true,  2,    false, false, false, false, false),
    (203,  'Magazine Almost Empty',                             false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (204,  'Magazine Empty',                                    false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (205,  'Transport Jam',                                     false, false, true,  false, false, true,  2,    false, false, false, false, false),
    (206,  'CSC Card Read Error',                               true,  true,  true,  false, false, false, 3,    false, false, false, false, false),
    (207,  'CSC Card Write Error',                              false, false, true,  false, false, false, 3,    false, null,  null,  null,  null),
    (208,  'SCT Commanded OOS',                                 false, false, true,  false, false, true,  2,    true,  null,  null,  null,  null),
    (209,  'Tri-Reader Comms Error',                            false, false, true,  false, false, true,  2,    false, false, false, false, false),
    (211,  'Capture Bin Almost Full',                           false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (212,  'Capture Bin Full',                                  false, false, true,  false, false, true,  1,    false, null,  null,  null,  null),
    (220,  'Missing Keys',                                      true,  true,  false, true,  false, true,  1,    false, true,  true,  false, false),
    (221,  'SCT OOS',                                           false, false, true,  false, false, true,  2,    false, false, false, true,  false),
    (222,  'SCRST OOS',                                         false, false, true,  false, false, true,  2,    false, false, false, true,  false),
    (223,  'Door Target OOS',                                   false, false, true,  false, false, true,  1,    false, false, false, false, false),
    (224,  'CSC Captured 1',                                    false, false, true,  false, false, false, 4,    false, null,  null,  null,  null),
    (225,  'CSC Captured 2',                                    false, false, true,  false, false, false, 4,    false, null,  null,  null,  null),
    (226,  'SCRST CSC Capture 1',                               false, false, true,  false, false, false, 4,    false, null,  null,  null,  null),
    (227,  'SCRST CSC Capture 2',                               false, false, true,  false, false, false, 4,    false, null,  null,  null,  null),
    (228,  'Magazine 1 Feed Error',                             false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (229,  'Magazine 2 Feed Error',                             false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (230,  'SCRST Roll 1 Feed Error',                           false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (231,  'SCRST Roll 2 Feed Error',                           false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (237,  'Bad Sam',                                           false, true,  false, true,  false, true,  1,    false, null,  null,  null,  null),
    --
    -- -- SCRST range (300-399) -------------------------------------------------
    (303,  'Roll Stock Low',                                    false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (304,  'Roll Stock Empty',                                  false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (305,  'Jam In Transport',                                  false, false, true,  false, false, true,  1,    false, false, false, false, false),
    (307,  'Card Write Error',                                  false, false, true,  false, false, false, 2,    false, null,  null,  null,  null),
    (308,  'SCRST Xport Fault',                                 false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (309,  'SCRST Shaft Encoder Failure',                       false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (310,  'DAC Failure',                                       false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (311,  'DIO Failure',                                       false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (312,  'Power Failure',                                     false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (313,  'Sensor Calibration Error',                          false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (315,  'Comms Error (SCRST)',                               false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (316,  'Capture Bin Almost Full (SCRST)',                   false, false, true,  false, false, true,  3,    false, null,  null,  null,  null),
    (317,  'Capture Bin Full (SCRST)',                          false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (318,  'Ticket Discarded into Capture Bin',                 false, false, true,  false, false, false, 4,    false, null,  null,  null,  null),
    (324,  'SCRST Max Errors on Issue',                         false, false, true,  false, false, false, 4,    false, null,  null,  null,  null),
    (331,  'SCRST Max Verify Failure',                          false, false, true,  false, false, false, 4,    false, null,  null,  null,  null),
    --
    -- -- BHU range (400-499) ---------------------------------------------------
    (401,  'Bill Jam',                                          false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (402,  'BHU Error',                                         false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (403,  'Comms Error (BHU)',                                 false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (405,  'Vault Almost Full (BHU)',                           false, false, true,  false, false, true,  3,    false, null,  null,  null,  null),
    (406,  'Vault Full (BHU)',                                  false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (407,  'Vault Removed or Rejected',                         false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (408,  'Code Mismatch',                                     false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (410,  'No Bills Accepted',                                 false, false, true,  false, false, true,  2,    false, false, false, true,  false),
    (411,  'Bill Retracted',                                    false, false, true,  false, false, false, 3,    false, null,  null,  null,  null),
    --
    -- -- CHU range (500-599) ---------------------------------------------------
    (504,  'Jam in Tubes (CHU)',                                false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (505,  'Coin Tube Low',                                     false, false, true,  false, false, true,  3,    false, null,  null,  null,  null),
    (506,  'Coin Tube Empty',                                   false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (507,  'Cassette Removed',                                  false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (508,  'Jam in Acceptor',                                   false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (509,  'Throat Blocker Fail',                               false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (510,  'Routing Error',                                     false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (511,  'Comms Error (CHU)',                                 false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (512,  'Vault Almost Full (CHU)',                           false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (513,  'Vault Full (CHU)',                                  false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (514,  'Coin Door Open',                                    false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (516,  'Cash Box Removed',                                  false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (517,  'Cash Box Error',                                    false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (518,  'Not Enough Change',                                 false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (519,  'OOS by Command (CHU)',                              false, false, true,  false, false, true,  2,    true,  null,  null,  null,  null),
    (521,  'Coin Vault Door Open',                              false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (522,  'Coin Tubes Dumped',                                 false, false, true,  false, false, false, 2,    false, null,  null,  null,  null),
    (523,  'Coin Tubes Topped Off',                             false, false, true,  false, false, false, 2,    false, null,  null,  null,  null),
    (534,  'No Coins Accepted',                                 false, false, true,  false, false, true,  2,    false, false, false, true,  false),
    (535,  'Cash Box Not Reset',                                false, false, true,  false, false, false, 2,    false, null,  null,  null,  null),
    (536,  'Invalid Cash Box',                                  false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (542,  'Unauthorized Access',                               false, false, true,  false, false, false, 3,    false, null,  null,  null,  null),
    (543,  'Jam in Coin Chute',                                 false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    --
    -- -- SYSTEM range (600-699) ------------------------------------------------
    (601,  'UPS Battery Low',                                   false, false, true,  false, false, false, 3,    false, null,  null,  null,  null),
    (602,  'Disk Almost Full',                                  false, false, true,  false, false, false, 3,    false, null,  null,  null,  null),
    (603,  'Disk Full',                                         false, false, true,  true,  false, true,  2,    false, true,  true,  true,  false),
    (604,  'SW Checksum Error',                                 false, false, true,  true,  false, false, 2,    false, false, false, true,  false),
    (605,  'DSM Write Error',                                   true,  true,  false, false, false, true,  3,    false, null,  null,  null,  null),
    (608,  'Agency Mismatch',                                   false, true,  false, false, false, true,  2,    false, null,  null,  null,  null),
    (611,  'Configuration File Error',                          false, true,  false, true,  false, true,  1,    false, null,  true,  null,  null),
    --
    -- -- SYSTEM range (700-799) ------------------------------------------------
    (701,  'Softkey Stuck',                                     false, false, true,  false, false, true,  3,    false, false, false, true,  false),
    --
    -- -- PIN_PAD range (800-899) -----------------------------------------------
    (801,  'PIN Entry Error',                                   false, false, true,  false, false, false, 3,    false, null,  null,  null,  null),
    (802,  'PIN Comms Error',                                   false, false, true,  false, false, true,  2,    false, false, false, true,  false),
    --
    -- -- PRINTER range (900-1099) ----------------------------------------------
    (901,  'Paper Low',                                         false, false, true,  false, false, false, 2,    false, null,  null,  true,  null),
    (902,  'Paper Out',                                         false, false, true,  false, false, false, 2,    false, null,  null,  true,  null),
    (903,  'Paper Jam',                                         false, false, true,  false, false, false, 2,    false, null,  null,  true,  null),
    (904,  'Receipt Printer Comms Error',                       false, false, true,  false, false, false, 2,    false, null,  null,  true,  null),
    (910,  'Receipt Printer OOS',                               false, false, true,  false, false, true,  2,    false, false, false, true,  false),
    --
    -- -- GATE_MECH range (1200-1299) -------------------------------------------
    (1203, 'Barrier Comms Error',                               true,  false, false, true,  false, true,  1,    false, false, false, false, false),
    (1227, 'Banked Ride Timeout',                               true,  false, false, false, true,  false, 4,    false, null,  null,  null,  null),
    -- Code 1228: OOS_Gate_KPI="Y?" in source -- stored TRUE with uncertainty noted
    (1228, '68k is OOS',                                        true,  false, false, true,  false, true,  1,    false, true,  null,  null,  null),
    --
    -- -- SYSTEM range (1300-1399) ----------------------------------------------
    (1301, 'Maintenance Handler Comms Error',                   true,  false, false, false, false, false, 4,    false, null,  null,  null,  null),
    --
    -- -- ALARM range (1400-1499) -----------------------------------------------
    -- Code 1401 duplicate in source (priority 1 vs 2): priority=1 row retained
    (1401, 'Comms Error (Alarm Panel)',                         false, false, true,  true,  false, true,  1,    false, false, false, false, false),
    (1402, 'Vibration Sensor Alarm Triggered',                  false, false, true,  true,  false, true,  1,    false, false, false, false, false),
    (1403, 'Vibration Sensor Malfunction',                      false, false, true,  true,  false, true,  1,    false, false, false, false, false),
    (1404, 'Battery Low (Alarm)',                               false, false, true,  false, false, true,  2,    false, null,  null,  null,  null),
    (1406, 'Deactivated with Key',                              false, false, true,  true,  false, true,  2,    false, null,  null,  null,  null),
    (1407, 'Panic Button Pushed',                               false, false, true,  true,  false, false, 1,    false, null,  null,  null,  null),
    (1408, 'Intrusion Alarm',                                   false, false, true,  true,  false, true,  1,    false, false, false, false, false),
    (1409, 'Manipulation on Alarm',                             false, false, true,  true,  false, true,  1,    false, false, false, false, false),
    --
    -- -- BANKCARD range (1600-1699) --------------------------------------------
    (1601, 'Comms Error (Payment Terminal)',                     false, false, true,  false, false, true,  3,    false, false, false, true,  false),
    (1602, 'No Db/Cr Accepted',                                 false, false, true,  false, false, true,  2,    false, false, false, false, true),
    (1603, 'No Credit Cards by Command',                        false, false, true,  false, false, true,  2,    true,  null,  null,  null,  null),
    (1604, 'No Debit Cards by Command',                         false, false, true,  false, false, true,  2,    true,  null,  null,  null,  null),
    (1605, 'Bank Comms Error',                                  false, false, true,  false, false, true,  2,    false, false, false, false, true),
    --
    -- -- FAREBOX range (2000-2099) - Code 2004 anomaly: appears in 2200 section -
    (2004, 'Database Copy Timed Out',                           true,  true,  false, false, false, false, null, false, null,  null,  null,  null),
    --
    -- -- DOPP range (2200-2299) ------------------------------------------------
    (2201, 'DOPP Out of Service',                               true,  true,  false, true,  false, true,  1,    false, true,  true,  false, false),
    (2203, 'A DOPP Component Failed to Start',                  true,  true,  false, true,  false, true,  2,    false, null,  null,  null,  null),
    (2205, 'DOPP Taps Table Almost Full',                       true,  true,  false, false, false, true,  2,    false, null,  null,  null,  null),
    (2206, 'DOPP Taps Table Full',                              true,  true,  false, true,  false, true,  1,    false, null,  null,  null,  null),
    (2207, 'DOPP Database Failure',                             true,  true,  false, false, false, true,  2,    false, null,  null,  null,  null),
    (2208, 'DOPP Offline',                                      false, false, false, false, false, false, null, false, null,  null,  null,  null),
    (2210, 'Missing Expected App',                              false, true,  false, false, false, false, null, false, null,  null,  null,  null),
    (2211, 'DOPP Negative List Digest Changed',                 true,  true,  false, false, false, false, 4,    false, null,  null,  null,  null),
    (2213, 'DOPP Missing Required Data',                        true,  true,  false, true,  false, true,  1,    false, null,  null,  null,  null),
    (2214, 'Taps Sent Up Failed',                               true,  true,  false, false, false, false, 1,    false, null,  null,  null,  null),
    (2215, 'Device Risk Assessment Failed / ABP Timeout',       false, true,  false, false, false, false, 1,    false, null,  null,  null,  null),
    (2216, 'DOPP Import/Export Success',                        true,  true,  false, false, true,  false, 4,    false, null,  null,  null,  null),
    (2217, 'DOPP Import/Export Failed',                         true,  true,  false, false, true,  false, 3,    false, null,  null,  null,  null),
    (2218, 'DOPP Import/Export in Progress',                    true,  true,  false, false, false, false, 4,    false, null,  null,  null,  null),
    (2219, 'DOPP BIN List Error in Record',                     true,  true,  false, false, false, true,  3,    false, null,  null,  null,  null),
    (2220, 'DOPP Configuration Error in Record',                true,  true,  false, false, false, true,  3,    false, null,  null,  null,  null),
    (2221, 'DOPP Negative List Error in Record',                true,  true,  false, false, false, true,  3,    false, null,  null,  null,  null),
    (2222, 'DOPP Positive List Error in Record',                true,  true,  false, false, false, true,  3,    false, null,  null,  null,  null),
    (2223, 'DOPP RTVS Error in Record',                         true,  true,  false, false, false, true,  3,    false, null,  null,  null,  null),
    (2224, 'DOPP TAP Error in Record',                          true,  true,  false, false, false, true,  3,    false, null,  null,  null,  null),
    (2227, 'DOPP Invalid Digest',                               true,  true,  false, false, false, true,  3,    false, null,  null,  null,  null),
    (2228, 'DOPP Invalid Encryption Key',                       true,  true,  false, false, false, true,  3,    false, null,  null,  null,  null),
    (2229, 'DOPP MAC Error',                                    true,  true,  false, false, false, true,  3,    false, null,  null,  null,  null),
    -- Code 2231 duplicate: first row retained
    (2231, 'Positive List Behind',                              true,  true,  false, false, false, false, 3,    false, null,  null,  null,  null),
    (2232, 'DOPP Negative List Behind',                         true,  true,  false, false, false, false, 3,    false, null,  null,  null,  null),
    (2233, 'DOPP Negative List Far Behind',                     true,  true,  false, false, false, false, 3,    false, null,  null,  null,  null),
    (2235, 'DOPP DB Copy Failed',                               true,  true,  false, false, false, true,  2,    false, null,  null,  null,  null),
    (2236, 'DOPP DB Copy Interrupted',                          true,  true,  false, false, false, true,  3,    false, null,  null,  null,  null),
    (2237, 'DOPP DB Repair Failed',                             true,  true,  false, false, false, true,  2,    false, null,  null,  null,  null),
    (2238, 'DOPP DB Repair Interrupted',                        true,  true,  false, false, false, true,  3,    false, null,  null,  null,  null),
    (2239, 'DOPP DB Files Processed Failed',                    true,  true,  false, false, false, true,  2,    false, null,  null,  null,  null),
    (2240, 'DOPP DB Files Processed Interrupted',               true,  true,  false, false, false, true,  3,    false, null,  null,  null,  null),
    (2241, 'DOPP DB Optimization Finished',                     true,  true,  false, false, false, false, 3,    false, null,  null,  null,  null),
    --
    -- -- COMMS range (50000+) --------------------------------------------------
    (50101,'Heartbeat Communication Lost',                      true,  true,  true,  false, false, true,  2,    false, false, false, false, false)

) AS t (event_code_id, event_name,
        applies_to_gate, applies_to_bus, applies_to_fmvd,
        is_oos, is_set_clear, requires_service_call, event_priority,
        is_commanded_oos,
        oos_counted_gate_kpi, oos_counted_bus_kpi, oos_counted_fmvd_kpi, oos_counted_central_kpi);

-- Post-build verification:
-- SELECT COUNT(*)                                                   AS total_rows,      -- Expect 155 (deduplicated)
--        SUM(CASE WHEN is_oos              = TRUE THEN 1 ELSE 0 END) AS oos_events,
--        SUM(CASE WHEN is_commanded_oos    = TRUE THEN 1 ELSE 0 END) AS commanded_oos,  -- Expect 8 (incl 156)
--        SUM(CASE WHEN is_reader_event     = TRUE THEN 1 ELSE 0 END) AS reader_events,   -- Expect ~30
--        SUM(CASE WHEN applies_to_gate     = TRUE THEN 1 ELSE 0 END) AS gate_events,
--        SUM(CASE WHEN applies_to_bus      = TRUE THEN 1 ELSE 0 END) AS bus_events
-- FROM mars_dev.silver.dim_event_matrix;
