/* ================================================================================
   CUBIC MARS - Chicago (CTA / Ventra) ODS
   DATA QUALITY PROOF PACK  -  v1, 09-Sep-2026
   Prepared by Mars Technologies for the Cubic Chicago data team

   PURPOSE
     These are the exact queries behind the two observations raised in our mail:
       ISSUE 1  NCS_STAGE.CASHBOX_TRACKING - CASHBOX_EVENT_ID no longer uniquely
                identifies an event for rows loaded since 12-Apr-2026.
       ISSUE 2  DEVICE_END_OF_DAY and DEVICE_METRIC - a small number of rows carry
                a TRANSIT_DAY_KEY dated after today.
     Please run them in your own environment and share the output. If our reading is
     wrong, these same queries will show it.

   SAFETY
     Every statement is a read-only SELECT. There is no DDL, no DML, no COMMIT, and
     nothing is created or altered. No bind or substitution variables - paste and run.

   HOW TO RUN
     Any Oracle client (SQL Developer, SQLcl, Toad, SQL*Plus) connected to the Chicago
     ODS with read access to NCS_STAGE and EDW.
     - Short on time? Run SECTION C only: one query, six rows, tells the whole story.
     - Run TEST T0 first - it confirms we are pointing at the same objects.

   COST / RUNTIME  (measured on our side)
     T0, A1-A5, B3, B4  - seconds. Bounded by the indexed INSERTED_DTM /
                          EDW_INSERTED_DTM columns.
     B1, B2             - a full scan of DEVICE_END_OF_DAY (no index on
                          TRANSIT_DAY_KEY). Minutes on our connection.
     C1                 - includes the B-series scan; run off-peak if preferred.

   DATE HANDLING
     'Today' is TRUNC(SYSDATE) in every test, so results stay correct whenever you run
     this. The 12-Apr-2026 boundary is the date our incremental feed resumes from.

   PLEASE SEND BACK
     The output of SECTION C at minimum; ideally A2 (the key census), A3 (the
     before/after contrast), B2 and B4 (the offending rows). CSV or a screenshot is
     fine. In SQL Developer: run, right-click the grid, Export -> CSV.
   ================================================================================ */

/* ============================================================================
   SECTION 0 - OBJECT RESOLUTION
   ============================================================================ */

/* ---- T0_OBJECTS : Object resolution - confirm we are looking at the same three tables ----
   Resolves the owning schema of each table in YOUR environment. Run this
   first: if DEVICE_METRIC resolves to a different owner than EDW, change the
   owner in tests B3/B4 to match.
*/
SELECT owner, table_name, num_rows AS optimizer_num_rows, last_analyzed
  FROM all_tables
 WHERE table_name IN ('CASHBOX_TRACKING','DEVICE_END_OF_DAY','DEVICE_METRIC')
 ORDER BY table_name, owner;

/* ============================================================================
   SECTION A - ISSUE 1 : CASHBOX_EVENT_ID NO LONGER IDENTIFIES AN EVENT
   ============================================================================ */

/* ---- A1_HEADLINE : ISSUE 1 headline - rows vs distinct CASHBOX_EVENT_ID since 12-Apr-2026 ----
   One row. N_ROWS is the volume loaded since 12-Apr; N_DISTINCT_KEYS is how
   many distinct event identifiers those rows carry; ROWS_PER_KEY is the
   average number of events sharing one identifier. In a healthy period
   ROWS_PER_KEY is 1. MONTHS_SPANNED shows the period covered.
*/
SELECT COUNT(*)                                                   AS n_rows,
       COUNT(DISTINCT cashbox_event_id)                          AS n_distinct_keys,
       ROUND(COUNT(*) / NULLIF(COUNT(DISTINCT cashbox_event_id),0)) AS rows_per_key,
       SUM(CASE WHEN cashbox_event_id IS NULL THEN 1 ELSE 0 END) AS n_null_keys,
       COUNT(DISTINCT device_id)                                 AS n_distinct_devices,
       SUM(CASE WHEN device_id IS NULL THEN 1 ELSE 0 END)        AS n_null_device_id,
       SUM(CASE WHEN event_dtm  IS NULL THEN 1 ELSE 0 END)       AS n_null_event_dtm,
       MIN(inserted_dtm)                                         AS first_insert,
       MAX(inserted_dtm)                                         AS last_insert,
       ROUND(MONTHS_BETWEEN(MAX(inserted_dtm), MIN(inserted_dtm)), 1) AS months_spanned
  FROM ncs_stage.cashbox_tracking
 WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00';

/* ---- A2_CENSUS : ISSUE 1 census - EVERY distinct key value that exists in the window ----
   Expected to return only a handful of rows. Each row shows one identifier
   value and how many events, devices and days share it. A single identifier
   covering millions of rows across hundreds of devices cannot be a valid
   per-event key.
*/
SELECT cashbox_event_id,
       COUNT(*)                  AS n_rows,
       COUNT(DISTINCT device_id) AS n_devices,
       MIN(event_dtm)            AS first_event,
       MAX(event_dtm)            AS last_event,
       MIN(inserted_dtm)         AS first_insert,
       MAX(inserted_dtm)         AS last_insert
  FROM ncs_stage.cashbox_tracking
 WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'
 GROUP BY cashbox_event_id
 ORDER BY n_rows DESC;

/* ---- A3_BEFORE_AFTER : ISSUE 1 contrast - the same measure before and after 11-Apr-2026 ----
   Two rows from the SAME table and the SAME column. The 'BEFORE' row is the
   healthy baseline (effectively one key per row). The 'AFTER' row is the
   current behaviour. This is the core of the finding: the column's behaviour
   changed, the table did not.
*/
SELECT 'BEFORE  01-Apr to 11-Apr-2026' AS period,
       COUNT(*)                                                     AS n_rows,
       COUNT(DISTINCT cashbox_event_id)                             AS n_distinct_keys,
       ROUND(COUNT(*) / NULLIF(COUNT(DISTINCT cashbox_event_id),0), 2) AS rows_per_key
  FROM ncs_stage.cashbox_tracking
 WHERE inserted_dtm >= TIMESTAMP '2026-04-01 00:00:00'
   AND inserted_dtm <  TIMESTAMP '2026-04-12 00:00:00'
UNION ALL
SELECT 'AFTER   12-Apr-2026 to today' AS period,
       COUNT(*)                                                     AS n_rows,
       COUNT(DISTINCT cashbox_event_id)                             AS n_distinct_keys,
       ROUND(COUNT(*) / NULLIF(COUNT(DISTINCT cashbox_event_id),0), 2) AS rows_per_key
  FROM ncs_stage.cashbox_tracking
 WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00';

/* ---- A4_DAILY : ISSUE 1 daily series - the exact day the identifier stopped being assigned ----
   One row per insert day from 01-Apr-2026. Read down the N_DISTINCT_KEYS
   column: it tracks N_ROWS until 11-Apr and collapses to a near-constant
   small number from 12-Apr onward, including after the September reload.
   This shows the change has a date, and that it never recovered.
*/
SELECT TRUNC(inserted_dtm)              AS insert_day,
       COUNT(*)                          AS n_rows,
       COUNT(DISTINCT cashbox_event_id)  AS n_distinct_keys
  FROM ncs_stage.cashbox_tracking
 WHERE inserted_dtm >= TIMESTAMP '2026-04-01 00:00:00'
 GROUP BY TRUNC(inserted_dtm)
 ORDER BY 1;

/* ---- A5_SAMPLE : ISSUE 1 sample rows - unrelated events carrying the identical identifier ----
   20 actual rows sharing the most frequent identifier value. Different
   devices, different event times, same identifier - so the identifier cannot
   distinguish one cashbox event from another.
*/
SELECT * FROM (
  SELECT device_id, event_dtm, inserted_dtm, cashbox_event_id
    FROM ncs_stage.cashbox_tracking
   WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'
     AND cashbox_event_id = (SELECT cashbox_event_id FROM (
                               SELECT cashbox_event_id, COUNT(*) AS n
                                 FROM ncs_stage.cashbox_tracking
                                WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'
                                GROUP BY cashbox_event_id
                                ORDER BY n DESC)
                              WHERE ROWNUM = 1)
   ORDER BY event_dtm DESC)
 WHERE ROWNUM <= 20;

/* ============================================================================
   SECTION B - ISSUE 2 : BUSINESS DAY KEYS DATED IN THE FUTURE
   ============================================================================ */

/* ---- B1_EOD_SUMMARY : ISSUE 2a summary - DEVICE_END_OF_DAY rows dated in the future ----
   One row. N_FUTURE_ROWS counts rows whose TRANSIT_DAY_KEY is greater than
   today's date key. PCT_OF_TABLE shows how small the affected population is.
   MAX_FUTURE_KEY is the furthest date.
*/
SELECT COUNT(*)                                                         AS n_total_rows,
       SUM(CASE WHEN transit_day_key > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))
                THEN 1 ELSE 0 END)                                       AS n_future_rows,
       ROUND(100 * SUM(CASE WHEN transit_day_key > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))
                THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0), 6)              AS pct_of_table,
       MAX(CASE WHEN transit_day_key > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))
                THEN transit_day_key END)                                AS max_future_key,
       MIN(transit_day_key)                                              AS min_key_in_table,
       TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))                     AS todays_key
  FROM ncs_stage.device_end_of_day;

/* ---- B2_EOD_DETAIL : ISSUE 2a detail - every future-dated DEVICE_END_OF_DAY row ----
   The complete list of offending rows, one line each. The key is split into
   year/month/day with SUBSTR rather than converted with TO_DATE, so a
   malformed key cannot abort the query. Compare TRANSIT_DAY against
   TRANSIT_DAY_KEY on each row: where the date column is sane and only the
   derived key is not, the defect is in the key derivation. This is the
   population we propose to exclude as outliers.
*/
SELECT device_id,
       transit_day,
       transit_day_key,
       SUBSTR(TO_CHAR(transit_day_key,'FM99999999'), 1, 4) AS key_year,
       SUBSTR(TO_CHAR(transit_day_key,'FM99999999'), 5, 2) AS key_month,
       SUBSTR(TO_CHAR(transit_day_key,'FM99999999'), 7, 2) AS key_day,
       last_eod_date
  FROM ncs_stage.device_end_of_day
 WHERE transit_day_key > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))
 ORDER BY transit_day_key, device_id;

/* ---- B3_DM_SUMMARY : ISSUE 2b summary - DEVICE_METRIC rows dated in the future (rows loaded since 12-Apr-2026) ----
   Counts only the offending rows, restricted to inserts since 12-Apr-2026 so
   the query drives off the indexed EDW_INSERTED_DTM column. The percentage
   is deliberately NOT computed here: a COUNT(*) over the whole ~100M-row
   window is a long-running query, so it is offered separately as B3B for
   anyone who wants the denominator. NOTE: our contract resolves
   DEVICE_METRIC to the EDW schema. If test T0 shows a different owner in
   your environment, change it here and in B3B/B4.
*/
SELECT COUNT(*)                                    AS n_future_rows,
       COUNT(DISTINCT transit_day_key)               AS n_distinct_future_keys,
       MIN(transit_day_key)                          AS min_future_key,
       MAX(transit_day_key)                          AS max_future_key,
       COUNT(DISTINCT edw_inserted_dtm)              AS n_insert_batches,
       MIN(edw_inserted_dtm)                         AS first_insert,
       MAX(edw_inserted_dtm)                         AS last_insert,
       TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD')) AS todays_key
  FROM edw.device_metric
 WHERE edw_inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'
   AND transit_day_key  > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'));

/* ---- B4_DM_DETAIL : ISSUE 2b detail - every future-dated DEVICE_METRIC row in the same window ----
   The complete list of offending rows. Small enough to review line by line.
   EDW_INSERTED_DTM shows when each row arrived, so it is clear these are
   recent loads rather than historical residue.
*/
SELECT device_id,
       transit_day_key,
       SUBSTR(TO_CHAR(transit_day_key,'FM99999999'), 1, 4) AS key_year,
       SUBSTR(TO_CHAR(transit_day_key,'FM99999999'), 5, 2) AS key_month,
       SUBSTR(TO_CHAR(transit_day_key,'FM99999999'), 7, 2) AS key_day,
       edw_inserted_dtm,
       edw_updated_dtm
  FROM edw.device_metric
 WHERE edw_inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'
   AND transit_day_key  > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))
 ORDER BY transit_day_key, device_id;

/* ---- B3B_DM_DENOMINATOR : ISSUE 2b denominator - OPTIONAL, LONG RUNNING - total DEVICE_METRIC rows in the same window ----
   Only needed to express the affected rows as a percentage. This counts
   roughly 100 million rows and took longer than 15 minutes on our
   connection, so skip it or run it off-peak - it does not change the finding
   either way.
*/
SELECT COUNT(*) AS n_window_rows
  FROM edw.device_metric
 WHERE edw_inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00';

/* ============================================================================
   SECTION C - ONE-QUERY SCORECARD
   ============================================================================ */

/* ---- C1_SCORECARD : SCORECARD - all findings in one result set (run this one if you run nothing else) ----
   Six rows. MEASURED_VALUE is what your database returns right now;
   EXPECTED_IF_HEALTHY is what a healthy feed would return. Copy the output
   back to us and we can close or revise each item.
*/
SELECT 'A. CASHBOX_TRACKING rows loaded since 12-Apr-2026'            AS check_item,
       TO_CHAR(COUNT(*))                                               AS measured_value,
       'volume is healthy - this row is context, not a defect'         AS expected_if_healthy
  FROM ncs_stage.cashbox_tracking WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'
UNION ALL
SELECT 'B. ...distinct CASHBOX_EVENT_ID values in those rows',
       TO_CHAR(COUNT(DISTINCT cashbox_event_id)),
       'one per event (i.e. equal to the row count above)'
  FROM ncs_stage.cashbox_tracking WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'
UNION ALL
SELECT 'C. ...average events sharing ONE identifier',
       TO_CHAR(ROUND(COUNT(*) / NULLIF(COUNT(DISTINCT cashbox_event_id),0))),
       '1'
  FROM ncs_stage.cashbox_tracking WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'
UNION ALL
SELECT 'D. ...same measure for 01-Apr to 11-Apr-2026 (baseline)',
       TO_CHAR(ROUND(COUNT(*) / NULLIF(COUNT(DISTINCT cashbox_event_id),0), 2)),
       '1 - and this baseline period does return 1'
  FROM ncs_stage.cashbox_tracking
 WHERE inserted_dtm >= TIMESTAMP '2026-04-01 00:00:00' AND inserted_dtm < TIMESTAMP '2026-04-12 00:00:00'
UNION ALL
SELECT 'E. DEVICE_END_OF_DAY rows with TRANSIT_DAY_KEY after today',
       TO_CHAR(COUNT(*)),
       '0'
  FROM ncs_stage.device_end_of_day
 WHERE transit_day_key > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))
UNION ALL
SELECT 'F. DEVICE_METRIC rows (loaded since 12-Apr) with TRANSIT_DAY_KEY after today',
       TO_CHAR(COUNT(*)),
       '0'
  FROM edw.device_metric
 WHERE edw_inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'
   AND transit_day_key  > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'));

/* ---- end of proof pack ---- */
