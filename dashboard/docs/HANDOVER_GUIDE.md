# CUBIC MARS Predictive Maintenance Dashboard — Handover Guide

**Project:** CUBIC MARS Predictive Analytics Platform  
**Version:** 2.0  
**Date:** May 2, 2026  
**Prepared by:** MARS Technologies — Data Science & Engineering Team  
**Audience:** API & Dashboard Deployment Team  

---

## 1. Executive Summary

The CUBIC MARS Predictive Maintenance Dashboard is a React-based single-page application (SPA) that provides real-time and historical analytics for transit device health across 4 cities and 11 UK Train Operating Companies (TOCs). The platform runs 80 ML models (5 Problem Statements x 4 Cities x 4 Device Types) and visualizes their outputs through city-based dashboards with 6 analytics tabs per city, plus a global Executive Overview page.

The current codebase is **production-build verified** (2310 modules, zero errors) with mock data that must be replaced with real PostgreSQL-backed API endpoints. This document provides everything the API & Dashboard team needs to take over customization, database mapping, API integration, and phased deployment.

---

## 2. What the Dashboard Currently Consists Of

### 2.1 City-Based Dashboard Architecture

**Executive Overview** (`/dashboard/overview`)
- Global cross-city comparison page
- City KPI summary cards (one per city)
- 5 PS-wise comparison charts (cross-city view for each Problem Statement)

**City Dashboards** (`/dashboard/city/:cityId`)
Per-city view with 6 tabs:

- **Overview Tab** — Cross-PS KPIs, accuracy trend, anomaly pie, error patterns, cascade failures, device reliability table
- **Failure Prediction (PS1)** — 3 sub-tabs: Executive Summary, Model Performance, Causation Analysis
- **Cascading Failure (PS2)** — Error correlations, cascade flows, association rules, network graph
- **Root Cause (PS3)** — 3 sub-tabs: Contributing Factors, Pattern Discovery, Actionable Insights
- **Anomaly Detection (PS4)** — 4 sub-tabs: Real-Time Alerts, Deviation Scoring, Outlier Analysis, Trend Monitoring
- **SLA & Reliability (PS5)** — 6 sub-tabs: SLA Overview, Downtime Analysis, Failure Metrics, Breach Tracking, Compliance, Reliability Metrics

**Cities:** CHI (Chicago/Pilot), BOS (Boston), LAX (Los Angeles), TOC (UK — 11 companies with dropdown filter)

### 2.2 Authentication & RBAC System

- JWT-based authentication with mock API stubs (ready for real Cognito/Auth0 integration)
- 4 roles: Admin, City Manager, Device SME, TOC Operator
- Permission-based filtering: users only see cities/devices/TOCs they have access to
- Login page with quick-demo access buttons
- Admin Console (`/admin`) with 3 tabs: User Management, Permission Matrix, Audit Log
- Protected routes — unauthenticated users redirect to `/login`
- Session persistence via localStorage tokens

### 2.3 Filter System

- City selection is now handled via URL route / sidebar navigation (not filter bar buttons)
- FilterBar shows device filter buttons only: Readers, TVMs, Gates, Validators
- TOC company selection is handled within the CityDashboard component for the TOC page (dropdown filter)
- RBAC-aware: sidebar city links and device filters auto-restrict based on logged-in user's permissions
- All dashboards react to filter changes via React Context

### 2.4 Cities & Devices

| City Code | City Name | Role | Fleet Size |
|-----------|-----------|------|-----------|
| CHI | Chicago | Pilot City | Largest |
| BOS | Boston | Phase 2 | 60% of CHI |
| LAX | Los Angeles | Phase 2 | 85% of CHI |
| TOC | 11 TOC (UK) | Phase 3 | 40% of CHI per TOC |

| Device Type | Prefix | Description |
|-------------|--------|-------------|
| Readers | RDR | Contactless card/NFC readers |
| TVMs | TVM | Ticket Vending Machines |
| Gates | GTE | Fare gates / barriers |
| Validators | VLD | On-board/platform validators |

### 2.5 TOC Companies

| ID | Full Name |
|----|-----------|
| RSPL | Rail Settlement Plan Ltd |
| SET | SE Trains Limited |
| WMT | West Midlands Trains Limited |
| LNER | London North Eastern Railway Limited |
| TPT | Transpennine Trains Limited |
| LSA | London Southend Airport Company Limited |
| HAL | Heathrow Airport Limited |
| NTL | Northern Trains Limited |
| GAT | GA Trains Limited |
| C2C | C2C Trenitalia Limited |
| TFW | Transport for Wales Rail Limited |

---

## 3. File Inventory — What Each File Does

### 3.1 Project Root

| File | Purpose |
|------|---------|
| `package.json` | Dependencies: React 19, Vite 8, Recharts 3, react-router-dom 7, lucide-react |
| `vite.config.js` | Vite build configuration (React plugin) |
| `index.html` | SPA entry point — mounts `#root` div |
| `eslint.config.js` | ESLint configuration |
| `.gitignore` | Git ignore rules |

### 3.2 Source Files (`src/`)

#### Entry & Layout

| File | Purpose |
|------|---------|
| `main.jsx` | React app bootstrap — renders `<App>` into DOM |
| `App.jsx` | Top-level router: BrowserRouter → AuthProvider → FilterProvider → Routes. Defines DashboardLayout (sidebar + filter bar + content) and AdminLayout (sidebar + content). Routes: `/login`, `/dashboard/overview`, `/dashboard/city/:cityId`, `/admin` |
| `index.css` | Global styles: CSS variables, sidebar, filter bar, cards, tables, badges, tabs, grids, responsive breakpoints |

#### Authentication (`src/auth/`)

| File | Purpose |
|------|---------|
| `mockAuthAPI.js` | **REPLACE WITH REAL API.** Mock JWT auth: 10 seed users, login/logout/refresh/validate token, user CRUD, audit logging. Uses localStorage for persistence. |
| `AuthContext.jsx` | React context providing `currentUser`, `token`, `login()`, `logout()`, `isAuthenticated`, `isAdmin`, permission check helpers (`canAccessCity`, `canAccessDevice`, `canAccessTOC`). Restores sessions from localStorage. |
| `ProtectedRoute.jsx` | Route guard component. Redirects unauthenticated users to `/login`. Supports `requiredRole` prop for admin-only routes. |

#### State Management (`src/context/`)

| File | Purpose |
|------|---------|
| `FilterContext.jsx` | Global filter state: devices, TOCs, date range. RBAC-aware — computes `allowedDevices/TOCs` from user permissions. City selection is URL-driven via route params. All dashboards consume this context. |

#### Layout Components (`src/components/layout/`)

| File | Purpose |
|------|---------|
| `Sidebar.jsx` | Left navigation: Executive Overview link, 4 city links (RBAC-filtered), admin console link (admin only), user avatar badge with role, logout button |
| `FilterBar.jsx` | Top filter bar: device filter buttons only (city selection via sidebar). Only shows device options the user has permission to see. |
| `PageHeader.jsx` | Reusable header: title, subtitle, icon, date display, export button |

#### Dashboard Pages (`src/pages/`)

| File | Lines | Purpose |
|------|-------|---------|
| `ExecutiveOverview.jsx` | ~346 | Global overview with city summary cards + 5 PS cross-city comparison charts |
| `CityDashboard.jsx` | ~240 | City-scoped dashboard with 6 tabs, URL param for city, TOC dropdown for TOC page |
| `LoginPage.jsx` | ~180 | Login form with branded left panel and 8 demo quick-login buttons |
| `AdminConsole.jsx` | ~320 | Admin console: user table, create/edit modal, permission matrix, audit log |

> **Note:** Each Problem Statement is now a tab within the city dashboard (not a separate page). The CityOverviewTab provides a cross-PS summary, while PS1–PS5 tabs provide deep-dive analytics.

#### Tab Components (`src/components/tabs/`)

| File | Lines | Purpose |
|------|-------|---------|
| `CityOverviewTab.jsx` | ~239 | City overview KPIs from all 5 PS |
| `PS1FailurePredictionTab.jsx` | ~504 | Failure prediction — 3 sub-tabs: Executive Summary, Model Performance, Causation Analysis |
| `PS2CascadingFailureTab.jsx` | ~202 | Cascading failure analysis — error correlations, cascade flows, association rules, network graph |
| `PS3RootCauseTab.jsx` | ~317 | Root cause analysis — 3 sub-tabs: Contributing Factors, Pattern Discovery, Actionable Insights |
| `PS4AnomalyDetectionTab.jsx` | ~353 | Anomaly detection — 4 sub-tabs: Real-Time Alerts, Deviation Scoring, Outlier Analysis, Trend Monitoring |
| `PS5SLAReliabilityTab.jsx` | ~423 | SLA + Reliability metrics — 6 sub-tabs: SLA Overview, Downtime Analysis, Failure Metrics, Breach Tracking, Compliance, Reliability Metrics |

#### Data Layer (`src/data/`)

| File | Purpose |
|------|---------|
| `mockData.js` | **REPLACE WITH API CALLS.** 21 exported data generator functions using seeded PRNG (Mulberry32, seed=42) for deterministic mock data. Each function accepts `(cities[], devices[])` params. |

### 3.3 Build Output (`dist/`)

| File | Size | Purpose |
|------|------|---------|
| `dist/index.html` | 0.74 KB | Production HTML entry |
| `dist/assets/index-*.css` | 6.62 KB | Bundled CSS |
| `dist/assets/index-*.js` | 792.92 KB | Bundled JS (all React + Recharts + app code) |

---

## 4. How to Use the Codebase

### 4.1 Prerequisites

- Node.js >= 18.x
- npm >= 9.x

### 4.2 Local Development

```bash
# Install dependencies
cd cubic-dashboards
npm install

# Start dev server (hot reload)
npm run dev
# Opens at http://localhost:5173

# Production build
npm run build

# Preview production build
npm run preview
# Opens at http://localhost:4173
```

### 4.3 Login Credentials (Mock)

| Username | Password | Role | Access |
|----------|----------|------|--------|
| admin | admin123 | Admin | All cities, all devices, all TOCs |
| chi_manager | chicago1 | City Manager | Chicago only |
| bos_manager | boston1 | City Manager | Boston only |
| lax_manager | losangeles1 | City Manager | Los Angeles only |
| toc_manager | toc1 | City Manager | All 11 TOCs |
| reader_sme | readers1 | Device SME | All cities, Readers only |
| tvm_sme | tvms1 | Device SME | All cities, TVMs only |
| lner_operator | lner1 | TOC Operator | TOC city, LNER only |
| northern_operator | northern1 | TOC Operator | TOC city, Northern only |
| tfw_operator | tfw1 | TOC Operator | TOC city, TFW only |

### 4.4 Key Integration Points

The following files need modification to connect to real APIs:

1. **`src/data/mockData.js`** — Replace each exported function with API calls to your backend
2. **`src/auth/mockAuthAPI.js`** — Replace with real AWS Cognito / Auth0 / custom JWT backend
3. **`src/auth/AuthContext.jsx`** — Update token storage from localStorage to httpOnly cookies for production
4. **`src/context/FilterContext.jsx`** — No changes needed; it reads permissions from AuthContext automatically

---

## 5. Data Mapping — Mock to Production (PostgreSQL + API)

### 5.1 Mock Data Functions → API Endpoints

Each function in `mockData.js` should become a REST API endpoint. Below is the recommended mapping:

| Mock Function | Recommended API Endpoint | HTTP | Description |
|--------------|-------------------------|------|-------------|
| `getModelPerformance(cities, devices)` | `/api/v1/models/performance` | GET | Model accuracy metrics per city/device |
| `getAccuracyTrend(cities, devices)` | `/api/v1/models/accuracy-trend` | GET | 30-day accuracy time series |
| `getPredictionSummary(cities, devices)` | `/api/v1/predictions/summary` | GET | Aggregate prediction counts by severity |
| `getConfusionMatrix(city, device)` | `/api/v1/models/confusion-matrix` | GET | TP/FP/FN/TN for specific model |
| `getFeatureImportance(city, device)` | `/api/v1/models/feature-importance` | GET | SHAP values for specific model |
| `getReliabilityMetrics(cities, devices)` | `/api/v1/reliability/metrics` | GET | MTTF, RUL, Cox hazard, Weibull params |
| `getSurvivalCurve(city, device)` | `/api/v1/reliability/survival-curve` | GET | Kaplan-Meier data points |
| `getRootCauseFactors(cities, devices)` | `/api/v1/root-cause/factors` | GET | Hardware, software, environmental breakdown |
| `getErrorCorrelations(cities, devices)` | `/api/v1/root-cause/correlations` | GET | Error pair correlation coefficients |
| `getErrorCascades(cities, devices)` | `/api/v1/root-cause/cascades` | GET | Error cascade flow data (Sankey) |
| `getTemporalPatterns(cities, devices)` | `/api/v1/root-cause/temporal` | GET | Hourly/daily/monthly failure patterns |
| `getAnomalyAlerts(cities, devices)` | `/api/v1/anomalies/alerts` | GET | Real-time anomaly alert feed |
| `getDeviationScores(cities, devices)` | `/api/v1/anomalies/deviations` | GET | Per-device deviation scores |
| `getAnomalyTrend(cities, devices)` | `/api/v1/anomalies/trend` | GET | 30-day anomaly count trend |
| `getSLAMetrics(cities, devices)` | `/api/v1/sla/metrics` | GET | Uptime %, MTBF, MTTR KPIs |
| `getUptimeTrend(cities, devices)` | `/api/v1/sla/uptime-trend` | GET | 30-day uptime per city |
| `getSLABreaches(cities, devices)` | `/api/v1/sla/breaches` | GET | SLA breach incident log |
| `getDowntimeByDevice(cities, devices)` | `/api/v1/sla/downtime-by-device` | GET | Planned vs unplanned downtime |
| `getDowntimeByCause(cities, devices)` | `/api/v1/sla/downtime-by-cause` | GET | Downtime hours by root cause |
| `getMTBFTrend(cities, devices)` | `/api/v1/sla/mtbf-trend` | GET | 12-month MTBF per device type |
| `getComplianceScorecard(cities, devices)` | `/api/v1/sla/compliance` | GET | 10-metric compliance scorecard |

### 5.2 Query Parameters

All endpoints should accept these query parameters:

```
GET /api/v1/models/performance?cities=CHI,BOS&devices=Readers,TVMs&date_from=2026-01-01&date_to=2026-04-30
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `cities` | CSV string | Filter by city codes (CHI, BOS, LAX, TOC) |
| `devices` | CSV string | Filter by device types (Readers, TVMs, Gates, Validators) |
| `tocs` | CSV string | Filter by TOC IDs (LNER, NTL, etc.) — applies when city=TOC |
| `date_from` | ISO date | Start of date range |
| `date_to` | ISO date | End of date range |

### 5.3 Auth API Endpoints

| Mock Function | API Endpoint | HTTP | Description |
|--------------|-------------|------|-------------|
| `loginAPI()` | `/api/v1/auth/login` | POST | Authenticate, return JWT |
| `refreshTokenAPI()` | `/api/v1/auth/refresh` | POST | Refresh expiring token |
| `validateTokenAPI()` | `/api/v1/auth/validate` | GET | Check token validity |
| `listUsersAPI()` | `/api/v1/admin/users` | GET | List all users (admin) |
| `createUserAPI()` | `/api/v1/admin/users` | POST | Create new user (admin) |
| `updateUserAPI()` | `/api/v1/admin/users/:id` | PUT | Update user (admin) |
| `deleteUserAPI()` | `/api/v1/admin/users/:id` | DELETE | Delete user (admin) |
| `getAuditLogAPI()` | `/api/v1/admin/audit-log` | GET | Audit trail (admin) |

### 5.4 How to Replace Mock Data with API Calls

Create an `apiClient.js` utility:

```javascript
// src/api/apiClient.js
const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

export async function apiGet(path, params = {}) {
  const token = localStorage.getItem('cubic_mars_token');
  const query = new URLSearchParams(params).toString();
  const url = `${API_BASE}${path}${query ? '?' + query : ''}`;
  
  const res = await fetch(url, {
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
  });
  
  if (res.status === 401) {
    window.location.href = '/login';
    throw new Error('Unauthorized');
  }
  
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}
```

Then replace each mock function. Example:

```javascript
// Before (mock):
import { getModelPerformance } from '../data/mockData';
const data = getModelPerformance(selectedCities, selectedDevices);

// After (real API):
import { apiGet } from '../api/apiClient';
const data = await apiGet('/models/performance', {
  cities: selectedCities.join(','),
  devices: selectedDevices.join(','),
});
```

---

## 6. PostgreSQL Database Schema

A complete schema file is provided at `docs/schema.sql`. Key tables:

| Table | Purpose | Rows (Est.) |
|-------|---------|-------------|
| `cities` | City master data | 4 |
| `toc_companies` | TOC company master data | 11 |
| `devices` | Device inventory per city | ~10,000+ |
| `model_runs` | ML model execution results | Daily per model |
| `predictions` | Individual device predictions | Millions |
| `anomaly_alerts` | Real-time anomaly detections | Thousands/day |
| `deviation_scores` | Per-device deviation analysis | Per device |
| `sla_metrics` | SLA KPI snapshots | Daily per city |
| `sla_breaches` | SLA breach incidents | Event-based |
| `downtime_events` | Device downtime records | Event-based |
| `error_events` | Raw error/fault events | Millions |
| `root_cause_analysis` | RCA results per investigation | Per incident |
| `users` | Application users | ~50-200 |
| `audit_log` | User action audit trail | Append-only |

---

## 7. AWS Deployment — ECS/Fargate + RDS

### 7.1 Architecture Overview

```
                    ┌─────────────────────────────────────────┐
                    │              AWS Cloud                   │
                    │                                         │
  Users ──────────▶ │  CloudFront (CDN)                       │
                    │       │                                 │
                    │       ▼                                 │
                    │  ALB (Application Load Balancer)         │
                    │       │                                 │
                    │   ┌───┴───────────────────┐             │
                    │   │                       │             │
                    │   ▼                       ▼             │
                    │  ECS Fargate             ECS Fargate     │
                    │  (Frontend -             (API Backend -  │
                    │   Nginx+React)           FastAPI/Node)   │
                    │                              │          │
                    │                              ▼          │
                    │                        RDS PostgreSQL    │
                    │                        (Multi-AZ)        │
                    │                                         │
                    │  Cognito (Auth)    S3 (ML Artifacts)    │
                    │  ECR (Container    Secrets Manager      │
                    │   Registry)        CloudWatch (Logs)    │
                    └─────────────────────────────────────────┘
```

### 7.2 Environment Configuration

| Environment | Purpose | API URL | Database |
|------------|---------|---------|----------|
| Dev | Development & testing | `https://dev-api.cubic-mars.com` | `cubic_mars_dev` |
| UAT | User acceptance testing | `https://uat-api.cubic-mars.com` | `cubic_mars_uat` |
| Production | Live deployment | `https://api.cubic-mars.com` | `cubic_mars_prod` |

Use environment variables in `.env` files:

```bash
# .env.development
VITE_API_URL=https://dev-api.cubic-mars.com/api/v1
VITE_AUTH_PROVIDER=cognito
VITE_COGNITO_POOL_ID=us-east-1_XXXXX
VITE_COGNITO_CLIENT_ID=xxxxx

# .env.staging (UAT)
VITE_API_URL=https://uat-api.cubic-mars.com/api/v1

# .env.production
VITE_API_URL=https://api.cubic-mars.com/api/v1
```

### 7.3 Docker Setup

A `Dockerfile` and `nginx.conf` are provided in the `docs/` folder. Build and deploy:

```bash
# Build production image
docker build -t cubic-mars-dashboard:latest .

# Test locally
docker run -p 8080:80 cubic-mars-dashboard:latest

# Push to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-1.amazonaws.com
docker tag cubic-mars-dashboard:latest <account>.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-dashboard:latest
docker push <account>.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-dashboard:latest
```

### 7.4 ECS Task Definition

```json
{
  "family": "cubic-mars-dashboard",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "containerDefinitions": [
    {
      "name": "dashboard",
      "image": "<account>.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-dashboard:latest",
      "portMappings": [{ "containerPort": 80, "protocol": "tcp" }],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/cubic-mars-dashboard",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      }
    }
  ]
}
```

### 7.5 RDS PostgreSQL Setup

```bash
aws rds create-db-instance \
  --db-instance-identifier cubic-mars-prod \
  --db-instance-class db.r6g.large \
  --engine postgres \
  --engine-version 15.4 \
  --master-username cubic_admin \
  --master-user-password <secure-password> \
  --allocated-storage 100 \
  --multi-az \
  --storage-encrypted \
  --vpc-security-group-ids sg-xxxxx \
  --db-subnet-group-name cubic-mars-subnet-group
```

---

## 8. Phased Deployment Plan — City-Wise Rollout

### Phase 1: Chicago (Pilot) — Weeks 1-4

1. Deploy Dev environment with CHI data only
2. Connect ML pipeline outputs to PostgreSQL
3. Integrate real device inventory data for CHI
4. Validate CHI city dashboard (all 6 tabs) with real data
5. UAT sign-off from Chicago operations team
6. Production deploy for CHI

### Phase 2: Boston + Los Angeles — Weeks 5-8

1. Onboard BOS and LAX device data into PostgreSQL
2. Train/deploy ML models for BOS and LAX
3. Create City Manager accounts for BOS and LAX
4. Validate cross-city filtering and role isolation
5. UAT sign-off from BOS and LAX teams
6. Production deploy (add BOS + LAX routes to ALB)

### Phase 3: TOC (11 UK Companies) — Weeks 9-14

1. Onboard each TOC company's device inventory
2. Map TOC-specific data feeds (different data formats per TOC)
3. Create TOC Operator accounts per company
4. Validate TOC sub-filter functionality per company
5. Staged rollout: 3-4 TOCs per week
6. Full production deploy for all 11 TOCs

### Phase 4: Hardening & Optimization — Weeks 15-16

1. Performance optimization: API response caching, CDN rules
2. Implement real-time WebSocket feeds for PS4 Anomaly Detection tab (real-time alerts)
3. Add data export functionality (CSV/PDF)
4. Load testing at full scale (all cities + TOCs)
5. Security audit and penetration testing
6. DR (Disaster Recovery) validation

---

## 9. Key Processes & Steps

### 9.1 API Integration Checklist

- [ ] Set up API backend (FastAPI or Node/Express recommended)
- [ ] Implement all 21 data endpoints (see Section 5.1)
- [ ] Implement all 8 auth endpoints (see Section 5.3)
- [ ] Add JWT validation middleware
- [ ] Add RBAC middleware (check user permissions against requested data)
- [ ] Create `src/api/apiClient.js` utility (see Section 5.4)
- [ ] Replace each `mockData.js` import in dashboard pages with API calls
- [ ] Replace `mockAuthAPI.js` with real auth provider calls
- [ ] Add loading states and error handling to all API calls
- [ ] Add API response caching where appropriate (SWR/React Query recommended)

### 9.2 Database Setup Checklist

- [ ] Create RDS PostgreSQL instances (Dev/UAT/Prod)
- [ ] Run `docs/schema.sql` to create tables
- [ ] Set up database migrations tool (Flyway or Alembic)
- [ ] Seed reference data (cities, TOCs, device types)
- [ ] Configure connection pooling (PgBouncer recommended)
- [ ] Set up automated backups (RDS snapshots)
- [ ] Create read replicas for dashboard queries

### 9.3 Security Checklist

- [ ] Replace localStorage JWT with httpOnly secure cookies
- [ ] Implement CORS policies on API
- [ ] Add rate limiting to auth endpoints
- [ ] Enable HTTPS everywhere (ACM certificates)
- [ ] Set up AWS WAF on ALB/CloudFront
- [ ] Implement API key rotation
- [ ] Add CSP (Content Security Policy) headers
- [ ] Remove all hardcoded passwords from `mockAuthAPI.js`

### 9.4 Monitoring & Observability

- [ ] CloudWatch dashboards for ECS metrics (CPU, memory, request count)
- [ ] CloudWatch Logs for application logging
- [ ] RDS Performance Insights for database monitoring
- [ ] Set up alarms: API error rate > 1%, latency > 2s, CPU > 80%
- [ ] Implement health check endpoints (`/api/health`, `/api/ready`)

### 9.5 CI/CD Pipeline

```
GitHub Push → GitHub Actions → Build → Test → ECR Push → ECS Deploy
                                                            │
                                        ┌───────────────────┼────────────────┐
                                        │                   │                │
                                    Dev (auto)          UAT (manual)    Prod (manual)
```

Recommended: Use AWS CodePipeline or GitHub Actions with separate deployment stages for Dev → UAT → Production.

---

## 10. Technology Stack Summary

| Layer | Technology | Version |
|-------|-----------|---------|
| Frontend Framework | React | 19.2.5 |
| Build Tool | Vite | 8.0.10 |
| Routing | react-router-dom | 7.14.2 |
| Charts | Recharts | 3.8.1 |
| Icons | lucide-react | 1.14.0 |
| Web Server | Nginx | 1.25+ |
| Container | Docker | Latest |
| Orchestration | AWS ECS Fargate | - |
| Database | PostgreSQL (RDS) | 15.x |
| Auth | AWS Cognito (recommended) | - |
| CDN | CloudFront | - |
| Container Registry | AWS ECR | - |
| Monitoring | CloudWatch | - |

---

## 11. Contact & Support

For questions about this handover, contact:

- **Data Science Team:** pavan.kumar@mars-techs.com
- **Project Lead:** MARS Technologies Engineering
- **Documentation Version:** 2.0 (May 2, 2026)
