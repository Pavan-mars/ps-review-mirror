# Playbook: PS2 — Cascade / Fault-Chain Analysis

## 1. Problem Statement

Identify and characterise multi-subsystem fault cascade patterns in fare-collection devices. When one subsystem fails, it often triggers failures in adjacent subsystems — understanding these chains enables targeted pre-emptive maintenance and reduces cascade-induced extended outages.

**Why it matters:** A 5-event cash-cascade on a TVM that starts with a Bill Handling Unit jam and ends with a communications failure can take a device offline for hours. Recognising this pattern early (after the first 1–2 events) enables dispatch before full escalation.

**Grain:** One row per (DEVICE_ID, transit_day) — each row represents one fault chain observed on that day.  
**All rows have `has_cascade = True`** — single-event (non-chain) occurrences are filtered in the silver layer.  
**Analysis type:** Unsupervised (no binary ML target) — pattern mining, Markov modelling, clustering.

---

## 2. Data Requirements

| Item | Detail |
|------|--------|
| Gold table | `device_ps2_chains.parquet` |
| Row count | 7,511 |
| Grain | (DEVICE_ID, transit_day) |
| Date range | 2025-03-01 → 2026-04-30 (approx) |
| Key column | `subsystem_chain` — arrow-delimited string e.g. `CHU->COMMS->OTHER->GATE_FAULT` |

**Gold table path:**
```
D:\Sathish\Chicago_Synthetic data\chicago_oracle_real_data\gold\device_ps2_chains.parquet
```

---

## 3. Feature Reference

### Chain Structure
| Column | Type | Description |
|--------|------|-------------|
| `subsystem_chain` | string | Full ordered chain, arrow-delimited (e.g. `CHU->COMMS->OTHER`) |
| `severity_chain` | string | Severity at each step (e.g. `WARN->WARN->CRITICAL`) |
| `chain_length` | int | Number of events in the chain |
| `chain_span_min` | float | Duration from first to last event in chain (minutes) |
| `chain_start_dtm` | datetime | Timestamp of first event in chain |
| `chain_end_dtm` | datetime | Timestamp of last event in chain |

### Chain Composition Flags
| Column | Type | Description |
|--------|------|-------------|
| `critical_in_chain` | int | Count of CRITICAL events within the chain |
| `oos_in_chain` | int | Count of OOS (Out-of-Service) events within the chain |
| `has_cash_cascade` | bool | Chain involves both BHU and CHU subsystems |
| `has_printer_in_chain` | bool | Printer subsystem appears in chain |
| `has_gate_mech_in_chain` | bool | Gate mechanical subsystem appears |
| `has_csc_reader_in_chain` | bool | CSC reader appears |

### Notebook-Derived
| Feature | Description |
|---------|-------------|
| `first_subsystem` | `chain_list[0]` — chain initiator |
| `last_subsystem` | `chain_list[-1]` — chain terminator |
| `chain_list` | Python list parsed from `subsystem_chain` |
| `cluster` | KMeans cluster assignment from TF-IDF embedding |

### Known Subsystem Tokens
`CHU`, `BHU`, `COMMS`, `GATE_FAULT`, `CSC_READER`, `PRINTER`, `OTHER`

---

## 4. Model Architecture

PS2 is an **unsupervised analysis pipeline** with four analytical stages:

```
device_ps2_chains.parquet
         │
  ┌──────┼──────────────────────────────────┐
  │      │           │                      │
Stage 1  Stage 2   Stage 3              Stage 4
Chain    Markov    Association          TF-IDF
Freq     Chain     Rules                + KMeans
Analysis Matrix   (mlxtend Apriori)    Clustering
  │      │           │                      │
Top-N   P(next       Rule lift/conf     6 chain
chains  |current)    pairs              clusters
```

### Stage Details
| Stage | Method | Key Parameter |
|-------|--------|--------------|
| 1. Pattern frequency | Counter on `subsystem_chain` | Top-30 patterns |
| 2. Markov matrix | Row-normalised transition counts | Per-device-type |
| 3. Association rules | Apriori + lift-ranked rules | min_support=0.03, min_lift=1.0 |
| 4. Clustering | TfidfVectorizer (1-2 gram) → normalize → KMeans | K=6 (elbow-selected) |

---

## 5. Training Protocol

PS2 has no supervised training. The analytical steps are:

1. **Parse chains:** `df['chain_list'] = df['subsystem_chain'].str.split('->')`
2. **Markov matrix:** Build per-device-type using `build_transition_matrix()` helper (see notebook). Normalise rows to obtain P(next | current).
3. **Association rules:** Treat each chain as a transaction of unique subsystem tokens. Run `mlxtend.frequent_patterns.apriori` then `association_rules`.
4. **TF-IDF clustering:**
   - Replace `->` with space to create a "document" per chain
   - Fit `TfidfVectorizer(ngram_range=(1,2), min_df=2)` on all chains
   - L2-normalise the TF-IDF matrix
   - Select K via elbow plot (inspect inertia for K=2..11)
   - Fit `KMeans(n_clusters=K_FINAL=6)`
5. **Cluster profiling:** Compute mean `chain_length`, `critical_in_chain`, cascade flags per cluster.

### Dependency
```
pip install mlxtend  # required for stage 3
```

---

## 6. Evaluation Criteria

PS2 is unsupervised — there is no single numeric score. Use the following to assess quality:

### Markov Matrix
| Check | Criterion |
|-------|-----------|
| Row sums | Each row must sum to 1.0 (or 0 for states with no outgoing transitions) |
| Self-loop dominance | `OTHER→OTHER` probability should be highest for most device types (generic events recur) |
| Cross-type consistency | TVM cash-subsystem transitions should be distinct from GATE gate-mechanical transitions |

### Association Rules
| Check | Criterion |
|-------|-----------|
| Lift > 2.0 | Strong co-occurrence pairs worth operational action |
| Confidence > 0.6 | Reliable conditional patterns |
| Support > 0.05 | Frequent enough to be operational rather than outlier-driven |

### KMeans Clusters
| Check | Criterion |
|-------|-----------|
| Elbow plot | Clear elbow visible; do not over-segment (K > 10 rarely actionable) |
| Cluster interpretability | Each cluster should have a dominant chain type recognisable by operations staff |
| Silhouette score (optional) | Target > 0.15 on sparse TF-IDF |

---

## 7. How to Run

### Prerequisites
```
Python venv: D:\Sathish\ML_Device_Telemetry\venv\Scripts\python.exe
Packages: pandas, numpy, scikit-learn, mlxtend, matplotlib, seaborn
```

### Steps
```bash
D:\Sathish\ML_Device_Telemetry\venv\Scripts\activate
jupyter notebook "D:\Sathish\Chicago_Synthetic data\chicago_oracle_real_data\notebooks\Chicago_PS2_Cascade_Analysis.ipynb"
```

Run all cells sequentially. Expected runtime: 3–7 minutes (TF-IDF on 7,511 rows is fast).

---

## 8. How to Interpret Results

### Markov Transition Matrix
- **High P(CHU→COMMS)** in TVM devices: bill-jam events trigger communication timeouts — inspect BHU cabling when CHU fault logged
- **P(X→X) > 0.5** (self-loop): subsystem repeatedly faults before cascade resolves — escalation needed
- **P(ANY→OOS) > 0.3**: that subsystem-type frequently leads to Out-of-Service — high priority for SLA

### Association Rules
- **Lift** > 1.0: antecedent and consequent co-occur more than by chance
- **Interpret `{CHU, BHU} → {COMMS}` with lift=3.5:** When both cash subsystems fault on the same day, comms failure is 3.5× more likely than the base rate
- Low-lift rules (< 1.5) are noise and should not drive operational decisions

### Cluster Profiles
| Cluster characteristic | Operational interpretation |
|------------------------|---------------------------|
| High `avg_critical` + `avg_span_min > 600` | Major prolonged cascade — dispatch technician same day |
| `pct_cash > 0.6` | Cash-subsystem cascade — TVM/BMV focus; likely needs vault service |
| Short chains (len=2), low critical | Minor transient fault — self-recovery likely; log and monitor |
| `pct_gate > 0.5` | Gate mechanical cascade — SAG/HBG/TVM gate-type focus |

### Chain Initiators (first_subsystem)
- The most common initiator is the subsystem to monitor first during morning device checks
- Devices where `first_subsystem == 'COMMS'` warrant network infrastructure inspection at the station level

---

## 9. Known Limitations / Data Gaps

| Limitation | Impact | Mitigation |
|-----------|--------|-----------|
| `first_subsystem` / `last_subsystem` not pre-computed | Must be parsed at runtime | Notebook includes parsing step |
| `OTHER` token dominates | Masks specific component information | Work with Cubic to reduce `OTHER` bucketing in EventType classification |
| All rows are cascade rows | No non-cascade baseline for comparison | Use PS1 data for device-day rows without chains |
| Synthetic subsystem assignments | Transitions may not reflect real CTA failure physics | Validate against Cubic real-data export once available |
| `mlxtend` optional dependency | Association rule section skips if not installed | `pip install mlxtend` before running |
| KMeans is sensitive to K choice | Wrong K produces uninterpretable clusters | Always inspect elbow plot before accepting K=6 |

---

## 10. Refresh Cadence

| Action | Frequency | Notes |
|--------|-----------|-------|
| Re-run chain frequency analysis | Weekly | New fault chains accumulate daily |
| Update Markov matrices | Monthly | Seasonal failure patterns shift |
| Re-run association rules | Monthly | New subsystem combinations may emerge after software updates |
| Re-cluster TF-IDF | Quarterly | Chain vocabulary changes as device firmware updates |
| Review K (number of clusters) | Quarterly | New device types or firmware may warrant additional clusters |
| Gold table rebuild | After each silver pipeline run | Triggered by nightly silver job |
