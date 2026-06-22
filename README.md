# Chicago Ventra — Mars Cubic Analysis

Predictive maintenance platform for the Chicago Transit Authority (CTA) Ventra fare device network.
Built on AWS + Databricks medallion architecture (raw → bronze → silver → gold) with Hub & Spoke VPC topology.

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
├── .github/workflows/          CI/CD (data-pipeline, ml-training, ml-deploy, api-deploy, terraform)
│
├── docs/
│   ├── architecture/           Hub & Spoke, Medallion, VPC diagrams
│   ├── playbooks/              PS1–PS5 ML playbooks
│   └── data_dictionary/        Column references, schema docs
│
├── sql/
│   ├── bronze/                 DDL + load scripts for 61 Oracle source tables
│   ├── silver/                 S01–S13 silver transformation views
│   └── gold/                   G01–G05 gold feature engineering tables
│
├── notebooks/
│   ├── ps1_failure_prediction/
│   ├── ps2_cascading_failure/
│   ├── ps3_root_cause_analysis/
│   ├── ps4_anomaly_detection/
│   └── ps5_remaining_useful_life/
│
├── glue/
│   ├── jobs/                   7 Glue jobs (bronze-ingest, silver-transform, gold-features,
│   │                           quality-check, ml-trigger, monitoring-sync, maintenance-window)
│   ├── step_functions/         ETL + ML Step Functions state machines
│   ├── bronze/                 Bronze layer Glue scripts
│   ├── silver/                 Silver layer Glue scripts
│   └── gold/                   Gold layer Glue scripts
│
├── sagemaker/
│   ├── training/               Training scripts per PS (ps1_training.py … ps5_training.py)
│   ├── mme/                    Multi-Model Endpoint configs + Model Monitor
│   ├── inference/              Inference handlers + predictor
│   ├── ps1/ … ps5/             PS-specific configs and artifacts
│
├── api/
│   ├── lambda/                 5 Lambda functions (webhook, servicenow_integration,
│   │                           jwt_authorizer, keep_warm, shap_async)
│   └── tests/
│
├── fastapi_app/                FastAPI inference service (API Spoke — App Runner)
│   ├── routers/
│   ├── models/
│   └── tests/
│
├── dashboard/                  React UI (App Spoke — App Runner)
│   └── src/
│       ├── components/
│       ├── pages/
│       └── auth/               Cognito SAML SSO integration
│
├── mlops/
│   ├── eks/                    EKS Argo Rollouts canary configs (0→10→50→100%)
│   └── monitoring/             Evidently AI drift reports + retrain trigger
│
├── monitoring/
│   ├── grafana/                Grafana dashboard JSON definitions (25+ dashboards)
│   ├── cloudwatch/             CloudWatch alarms (50+) + log groups
│   └── data_quality/           Great Expectations suites + DQ checks
│
├── infrastructure/
│   ├── terraform/
│   │   ├── modules/            14 reusable Terraform modules:
│   │   │   ├── vpc/            Hub VPC + 6 spoke VPCs
│   │   │   ├── transit_gateway/
│   │   │   ├── network_firewall/ Suricata IDPS + TLS inspection
│   │   │   ├── rds/            PostgreSQL (app state) + SQL Server (predictions)
│   │   │   ├── sagemaker/      MME endpoints + Model Monitor
│   │   │   ├── lambda/
│   │   │   ├── app_runner/     FastAPI + React containerized services
│   │   │   ├── cognito/        SAML SSO + city-scoped RBAC
│   │   │   ├── kms/            3 CMKs (data, ML, secrets)
│   │   │   ├── secrets_manager/ Oracle creds + 90-day rotation
│   │   │   ├── cloudwatch/
│   │   │   ├── iam/
│   │   │   ├── ecr/            Container repos + Trivy scanning
│   │   │   ├── eks/            EKS cluster (m5.2xlarge, 2–6 autoscale)
│   │   │   └── waf/            WAF v2 — rate limiting + geo-blocking
│   │   └── environments/
│   │       ├── dev/
│   │       ├── staging/
│   │       └── prod/
│   ├── hub_vpc/
│   ├── data_spoke/             S3, Glue, Databricks, Lake Formation
│   ├── ml_spoke/               SageMaker training + MMEs + Studio
│   ├── api_spoke/              API Gateway, Lambda, ElastiCache Redis, Cognito
│   ├── app_spoke/              App Runner (FastAPI + React), RDS
│   ├── devops_spoke/           ECR, CodeBuild, EKS, GitHub Actions runners
│   └── integration_spoke/      SQS FIFO (ServiceNow), SNS, EventBridge
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── load/                   Locust load testing
│   └── e2e/
│
├── tooling/                    Build and utility scripts
└── diagrams/                   Architecture diagrams
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
