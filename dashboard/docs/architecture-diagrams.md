# CUBIC MARS - Architecture Diagrams

## 1. System Architecture Overview

```mermaid
graph TB
    subgraph "Client Layer"
        Browser["Browser (React SPA)"]
    end

    subgraph "AWS Cloud"
        subgraph "Public Subnet"
            ALB["Application Load Balancer<br/>HTTPS :443"]
        end

        subgraph "Private Subnet - Frontend"
            ECS_FE["ECS Fargate<br/>Dashboard Service<br/>(Nginx + React)"]
        end

        subgraph "Private Subnet - Backend"
            ECS_API["ECS Fargate<br/>API Service<br/>(FastAPI / Node.js)"]
            ECS_ML["ECS Fargate<br/>ML Inference<br/>(SageMaker Endpoint)"]
        end

        subgraph "Data Layer"
            RDS["Amazon RDS<br/>PostgreSQL 15<br/>(Multi-AZ)"]
            S3_Models["S3 Bucket<br/>ML Model Artifacts"]
            S3_Static["S3 Bucket<br/>Static Assets / Backups"]
            ElastiCache["ElastiCache Redis<br/>Session & Cache"]
        end

        subgraph "Monitoring"
            CW["CloudWatch<br/>Logs & Metrics"]
            SNS["SNS<br/>Alerts"]
        end
    end

    Browser -->|HTTPS| ALB
    ALB -->|/| ECS_FE
    ALB -->|/api/*| ECS_API
    ECS_API --> RDS
    ECS_API --> ElastiCache
    ECS_API --> ECS_ML
    ECS_ML --> S3_Models
    ECS_FE --> CW
    ECS_API --> CW
    CW --> SNS
```

## 2. Authentication & RBAC Flow

```mermaid
sequenceDiagram
    participant U as User (Browser)
    participant FE as React Frontend
    participant API as Auth API
    participant DB as PostgreSQL
    participant Redis as ElastiCache

    U->>FE: Enter credentials
    FE->>API: POST /api/auth/login
    API->>DB: Validate credentials (bcrypt)
    DB-->>API: User + Permissions
    API->>Redis: Store refresh token
    API-->>FE: JWT Access Token + Refresh Token

    Note over FE: Store JWT in memory<br/>Refresh token in httpOnly cookie

    FE->>API: GET /api/predictions (Bearer JWT)
    API->>API: Verify JWT + Extract permissions
    API->>DB: Query with RLS (SET app.allowed_cities)
    DB-->>API: Filtered results
    API-->>FE: JSON response

    Note over FE: Token expired after 30 min

    FE->>API: POST /api/auth/refresh
    API->>Redis: Validate refresh token
    Redis-->>API: Valid
    API-->>FE: New JWT Access Token

    U->>FE: Logout
    FE->>API: POST /api/auth/logout
    API->>Redis: Revoke refresh token
    API-->>FE: 200 OK
```

## 3. Database Schema (Entity Relationship)

```mermaid
erDiagram
    CITIES ||--o{ FAILURE_PREDICTIONS : has
    CITIES ||--o{ ANOMALIES : has
    CITIES ||--o{ SLA_METRICS : has
    CITIES ||--o{ ROOT_CAUSES : has
    CITIES ||--o{ DEVICES : has

    TOC_COMPANIES ||--o{ DEVICES : operates
    TOC_COMPANIES ||--o{ FAILURE_PREDICTIONS : scoped_to
    TOC_COMPANIES ||--o{ ANOMALIES : scoped_to

    USERS ||--o{ USER_PERMISSIONS : has
    USERS ||--o{ AUDIT_LOG : generates
    USERS ||--o{ REFRESH_TOKENS : owns

    USER_PERMISSIONS }o--|| CITIES : grants_access
    USER_PERMISSIONS }o--|| TOC_COMPANIES : grants_access

    ML_MODELS }o--|| CITIES : trained_for

    CITIES {
        city_code id PK
        varchar name
        boolean is_pilot
        date go_live
    }

    TOC_COMPANIES {
        toc_company code PK
        varchar full_name
        varchar region
    }

    USERS {
        uuid id PK
        varchar username UK
        varchar email UK
        user_role role
        boolean is_active
    }

    FAILURE_PREDICTIONS {
        uuid id PK
        city_code city_id FK
        device_type device_type
        numeric probability
        severity_level severity
    }

    ANOMALIES {
        uuid id PK
        city_code city_id FK
        device_type device_type
        severity_level severity
        alert_status status
    }

    SLA_METRICS {
        uuid id PK
        city_code city_id FK
        device_type device_type
        numeric target_value
        numeric actual_value
        boolean is_breached
    }

    ML_MODELS {
        uuid id PK
        varchar problem_stmt
        city_code city_id FK
        device_type device_type
        varchar version
    }
```

## 4. CI/CD Deployment Pipeline

```mermaid
graph LR
    subgraph "Source"
        GH["GitHub<br/>Repository"]
    end

    subgraph "CI Pipeline"
        GA["GitHub Actions"]
        LINT["Lint & Type Check"]
        TEST["Unit Tests<br/>(Vitest)"]
        BUILD["npm run build"]
        DOCKER["Docker Build<br/>& Push to ECR"]
    end

    subgraph "CD Pipeline"
        ECR["Amazon ECR<br/>Container Registry"]
        DEV["Deploy to DEV<br/>(Auto)"]
        UAT["Deploy to UAT<br/>(Manual Gate)"]
        PROD["Deploy to PROD<br/>(Manual Gate +<br/>Canary 10%)"]
    end

    subgraph "Environments"
        DEV_ECS["DEV ECS Cluster"]
        UAT_ECS["UAT ECS Cluster"]
        PROD_ECS["PROD ECS Cluster<br/>(Multi-AZ)"]
    end

    GH -->|push/PR| GA
    GA --> LINT --> TEST --> BUILD --> DOCKER
    DOCKER --> ECR
    ECR --> DEV --> DEV_ECS
    DEV -->|Approval| UAT --> UAT_ECS
    UAT -->|Approval| PROD --> PROD_ECS
```

## 5. Frontend Component Architecture

```mermaid
graph TB
    subgraph "Entry Point"
        Main["main.jsx"]
        App["App.jsx<br/>(Router)"]
    end

    subgraph "Context Providers"
        Auth["AuthProvider<br/>(JWT, roles, permissions)"]
        Filter["FilterProvider<br/>(device filters, RBAC)"]
    end

    subgraph "Layouts"
        DashLayout["DashboardLayout<br/>(Sidebar + FilterBar + Content)"]
        AdminLayout["AdminLayout<br/>(Sidebar + Content)"]
    end

    subgraph "Pages"
        ExecOverview["ExecutiveOverview<br/>(/dashboard/overview)<br/>Cross-city KPIs + PS comparisons"]
        CityDash["CityDashboard<br/>(/dashboard/city/:cityId)<br/>6-tab city view"]
        Login["LoginPage"]
        Admin["AdminConsole<br/>(Users, Permissions, Audit)"]
    end

    subgraph "Tab Components (inside CityDashboard)"
        T0["CityOverviewTab<br/>(Cross-PS KPIs)"]
        T1["PS1FailurePredictionTab<br/>(3 sub-tabs)"]
        T2["PS2CascadingFailureTab<br/>(Cascades + Rules)"]
        T3["PS3RootCauseTab<br/>(3 sub-tabs)"]
        T4["PS4AnomalyDetectionTab<br/>(4 sub-tabs)"]
        T5["PS5SLAReliabilityTab<br/>(6 sub-tabs)"]
    end

    subgraph "Shared Components"
        Sidebar["Sidebar<br/>(Executive Overview + City Links<br/>+ Admin + User Badge)"]
        FilterBar["FilterBar<br/>(Device-only buttons)"]
        PageHeader["PageHeader<br/>(Title + Date + Export)"]
    end

    subgraph "Data Layer"
        MockData["mockData.js<br/>(21 generator functions)"]
        MockAuth["mockAuthAPI.js<br/>(JWT + CRUD stubs)"]
    end

    Main --> App
    App --> Auth --> Filter
    Filter --> DashLayout
    Filter --> AdminLayout
    DashLayout --> Sidebar
    DashLayout --> FilterBar
    DashLayout --> ExecOverview
    DashLayout --> CityDash
    CityDash --> T0
    CityDash --> T1
    CityDash --> T2
    CityDash --> T3
    CityDash --> T4
    CityDash --> T5
    AdminLayout --> Admin
    App --> Login
    ExecOverview --> MockData
    T0 --> MockData
    T1 --> MockData
    T2 --> MockData
    T3 --> MockData
    T4 --> MockData
    T5 --> MockData
    Login --> MockAuth
    Admin --> MockAuth
```

## 6. Phased City Deployment

```mermaid
gantt
    title CUBIC MARS - Phased City Deployment
    dateFormat  YYYY-MM-DD
    axisFormat  %b %Y

    section Phase 1 - Chicago (Pilot)
    Infrastructure Setup           :p1a, 2026-06-01, 2w
    API Development (CHI)          :p1b, after p1a, 4w
    Integration Testing            :p1c, after p1b, 2w
    UAT & Go-Live                  :milestone, p1d, after p1c, 0d

    section Phase 2 - Boston
    API Config (BOS)               :p2a, after p1c, 2w
    Data Migration & Testing       :p2b, after p2a, 3w
    UAT & Go-Live                  :milestone, p2d, after p2b, 0d

    section Phase 3 - Los Angeles
    API Config (LAX)               :p3a, after p2b, 2w
    Data Migration & Testing       :p3b, after p3a, 3w
    UAT & Go-Live                  :milestone, p3d, after p3b, 0d

    section Phase 4 - TOC (UK)
    TOC Integration (11 companies) :p4a, after p3b, 4w
    Per-TOC Testing & Rollout      :p4b, after p4a, 4w
    Full Go-Live                   :milestone, p4d, after p4b, 0d
```

## 7. Data Flow: Mock to Production

```mermaid
graph LR
    subgraph "Current State (Mock)"
        UI["Dashboard UI<br/>(React Components)"]
        MOCK["mockData.js<br/>(Seeded PRNG)"]
        UI -->|useMemo| MOCK
    end

    subgraph "Target State (Production)"
        UI2["Dashboard UI<br/>(Same Components)"]
        HOOKS["Custom Hooks<br/>(useQuery / SWR)"]
        API["REST API<br/>(FastAPI / Express)"]
        PG["PostgreSQL<br/>(RDS)"]
        UI2 -->|useQuery| HOOKS
        HOOKS -->|fetch| API
        API -->|SQL| PG
    end

    MOCK -.->|Replace with| HOOKS

    style MOCK fill:#ff9800,color:#000
    style HOOKS fill:#4caf50,color:#fff
```
