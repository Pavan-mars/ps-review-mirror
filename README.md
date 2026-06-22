# Chicago Ventra — Mars Cubic Analysis

Predictive maintenance platform for the Chicago Transit Authority (CTA) Ventra fare device network.
Built on AWS + Databricks, covering 300 devices (CTA00001–CTA00300) across 25 facilities.

**Client:** Cubic MARS  
**Engagement owner:** Pavan Kumar (Mars-Techs)  
**AWS account:** 170202974600 `Mars-RUL-Dev-Dev` (us-east-1)

---

## Problem Statements

| PS | Name | Algorithm Stack | Gate |
|----|------|----------------|------|
| PS1 | Failure Prediction | RF 0.45 + XGBoost 0.35 + CatBoost 0.20 (7-day horizon) | >90% AUC |
| PS2 | Cascading Failure Detection | Association Rules + Markov + HMM | >85% |
| PS3 | Root Cause Analysis | SHAP + DoWhy (incident grain) | — |
| PS4 | Anomaly Detection | Isolation Forest + SPC/EWMA/HDBSCAN (hourly) | — |
| PS5 | Remaining Useful Life | Weibull AFT + CoxPH + RSF (concordance index) | — |

---

## Architecture

Medallion data lake on S3 (raw → bronze → silver → gold), processed via AWS Glue and Databricks Spark JDBC over Site-to-Site VPN to Oracle EDW at 10.3.10.30.
Hub & Spoke VPC topology (7 VPCs, Transit Gateway) for network isolation.
SageMaker MMEs (one per PS) serve inference via FastAPI on ECS Fargate.

---

## Repo Structure

```
├── docs/                  Architecture docs, PS playbooks, data dictionaries
├── sql/
│   ├── bronze/            DDL + load scripts for 61 Oracle source tables
│   ├── silver/            S01–S13 silver transformation views
│   └── gold/              G01–G05 gold feature engineering tables
├── notebooks/
│   ├── ps1_failure_prediction/
│   ├── ps2_cascading_failure/
│   ├── ps3_root_cause_analysis/
│   ├── ps4_anomaly_detection/
│   └── ps5_remaining_useful_life/
├── glue/                  AWS Glue ETL jobs (bronze / silver / gold)
├── sagemaker/             Training scripts + inference handlers (per PS)
├── fastapi_app/           REST API layer — routers, models, tests
├── monitoring/            Grafana dashboards + Great Expectations DQ checks
├── infrastructure/        IaC for Hub VPC + 6 spokes
└── tooling/               Build and utility scripts
```

---

## S3 Buckets (Medallion)

| Layer | Bucket |
|-------|--------|
| Raw | `cubic-mars-pm-s3-datalake-dev-raw-170202974600` |
| Bronze | `cubic-mars-pm-s3-datalake-dev-bronze-170202974600` |
| Silver | `cubic-mars-pm-s3-datalake-dev-silver-170202974600` |
| Gold | `cubic-mars-pm-s3-datalake-dev-gold-170202974600` |
| Artifacts | `cubic-mars-pm-s3-datalake-dev-artifacts-170202974600` |

Oracle credentials are stored in AWS Secrets Manager (`dev_chicago_oracle`) — never hardcoded.

---

## Key Contacts

| Name | Role |
|------|------|
| Pavan Kumar (PK) | Lead architect, Mars-Techs engagement owner |
| Sathish Sivasankaran | PowerUser / Data Scientist |
| Daniel Clements | Cubic Chief Architect |
| Erik | Weekly status deck (Cubic) |
| Viren (Virender Rana) | Infra engineer — SageMaker setup |
