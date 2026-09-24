> **STALE — and it names a source it does not come from.**
>
> This file's own header says `Source: notebooks/catalog/generate_data_catalog.py`. **That generator
> does not write this file.** It emits `chicago_catalog_{bronze,silver,gold}.md`,
> `chicago_samples_{...}.md`, `chicago_catalog_facts.csv` and `chicago_validation_scorecard.md`
> into `mars_dev.audit.catalog_docs` — **none of which has ever been committed to this folder.**
> So nothing refreshes this file and nothing ever did.
>
> Measured drift, 24-Sep-2026: it states **silver 29 / gold 5** tables. The repo DDL has
> **silver 30 / gold 6**. The gold table it omits is **`device_ps3_oos_component`** — the
> component-level OOS fact. Anyone asking "do we have component-level OOS?" and reading this file
> would conclude we do not.
>
> Row counts, sizes and the validation summary below are a **2026-07-17** snapshot. Re-run the
> generator and read `mars_dev.audit.catalog_docs`; treat the DDL under `sql/` as authoritative
> for schema.

# Chicago Data Catalog - Key Facts & Findings

**Snapshot:** 2026-07-17 · **Catalog:** `mars_dev` · **Source:** `notebooks/catalog/generate_data_catalog.py`

## Scale
| Layer | Tables | Columns | Rows | Size |
|---|---|---|---|---|
| bronze | 85 | 3,822 | 2,896,892,989 | 119.5 GB |
| silver | 29 | 808 | 273,203,886 | 18.6 GB |
| gold | 5 | 331 | 35,544,469 | 1.48 GB |

**Largest:** `ncs_stage_device_event_history` 1.25B (46.7 GB) · `edw_device_metric` 643.7M (15.4 GB) · `edw_abp_tap` 573.5M (42.3 GB) · `edw_device_event` / `silver.device_event_enriched` 190.9M (16.6 GB) · `gold.device_ps4_hourly` 30.4M · `gold.device_ps1_daily` 2.89M.

## Validation summary
132 PASS · 1 WARN · **1 FAIL** · 8 INFO.

- **FAIL - `silver.incident_root_cause`: 670 duplicate rows on `availability_event_id`.** The table is not unique on its intended grain. Downstream PS3 (`gold.device_ps3_incident`) dedups via `QUALIFY ROW_NUMBER()` so PS3 gold is clean (0 dups), but the silver table itself should be deduped at S17 (same SN 2-rows-per-sys_id + multi-WOT-per-AE fan-out already handled in PS3; apply it in S17 too).
- **WARN - `bronze.edw_late_transaction_detail`: 0 rows** (empty source; not used downstream).
- **All 5 gold PS tables PASS grain uniqueness** (device_ps1/ps2 on DEVICE_ID+transit_day, ps3 on availability_event_id, ps4 on DEVICE_ID+DEVICE_KEY+hour_bucket+transit_day, ps5 on DEVICE_ID+COMPONENT_SERIAL_NBR).

## PS1-PS5 reconciliation metrics (from the run)
| Metric | Value | Reading |
|---|---|---|
| `dim_device` current categories | TVM 1,019 · GATE 1,382 · VALIDATOR 4,218 · OTHER 11,997 | TVM still includes ~431 AVM + ~75 EVM legacy units (S06 AVM->OTHER patch would drop TVM to ~513) |
| AVM ghosts in PS1 spine | **0** | AVM units emit no events, so despite the dim_device mislabel they do NOT enter PS1 training - the AVM cleanup is cosmetic for PS1, real only for fleet counts |
| device_failures device coverage (>=1 failure) | TVM 469 · GATE 944 · VALIDATOR 1,535 | validators now covered (were 0 pre-rebuild) |
| PS1 `will_fail_3d` positive rate | **20.27%** | high for a 3-day horizon - review label balance / validator weighting before training |
| PS3 rows / level-2 | 34,612 / 18,777 | severity target populated |
| PS3 `kpi_rule_id` fill | **0.0%** | the KPI-rule join still does not populate (QMW-1 open) - fix the join key or drop the column |
| PS4 ensemble anomaly rate | **1.05%** | healthy - the adaptive per-device 2-sigma recalibration fixed the earlier 36% over-firing |
| PS5 component censoring | **100.0%** | no observed component failures - PS5 must move to device-level survival (`silver.device_survival_intervals`) |
| device_event_enriched vs bronze device_event | 190,874,858 = 190,874,858 | exact reconciliation |

## Freshness note
Most fact tables show `max_date` ~ **2026-04-11** (the ODS / ingestion watermark) and are flagged `stale=True` only because that is >45 days before the 2026-07-17 run - this is the known ingestion recency, not corruption. Exceptions to watch: `edw_device_metric` carries future-dated rows (max **2028-05-13**); several NCS tables have sentinel future dates (2032-2034) and one 1949/1969 min - filter with `<= current_date()` in downstream builds.

## Action items surfaced
1. Dedup `silver.incident_root_cause` at S17 (670 dups on availability_event_id) - the only validation FAIL.
2. PS5: switch gold to device-level survival off `silver.device_survival_intervals` (component-level is 100% censored).
3. PS3: fix or drop `kpi_rule_id` (0% fill).
4. Optional: S06 `AVM/EVM -> OTHER` (cosmetic for PS1 - 0 ghosts - but corrects the ~1,019 vs ~513 TVM fleet count).
5. Review PS1 20.27% positive rate (label balance).

## Live source of truth
`mars_dev.audit.catalog_columns` · `catalog_facts` · `catalog_validation` · `catalog_docs`. Regenerate with `notebooks/catalog/generate_data_catalog.py`.
