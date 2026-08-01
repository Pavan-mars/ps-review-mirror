# PS2/PS1/PS3/PS5 Dashboard -- ECS Fargate Go-Live Runbook
Date: 2026-07-26. Decision: dashboard frontend hosts on **ECS Fargate behind an
internal ALB**, per PK's call and the original app-tier README (App
Runner/ECS Fargate + Cognito SAML + WAF), not the S3+CloudFront static-hosting
alternative that was also on the table.

## Where things stand right now
- **Already live**: Aurora RDS (`cubic-mars-rds-aurora-dev`), the
  `cubic-mars-dashboard-api` VPC Lambda, and its HTTP API GW
  (`https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com`) -- confirmed
  serving real PS1/PS2/PS3/PS5 data. The React dashboard's `api.js` is fully
  converted to the live-only pattern (throws on failure, no mock fallback)
  across all four PS2 tab files as of this session.
- **Not yet standing up anywhere**: the dashboard frontend itself. The
  2026-07-2x CloudShell audit found `apprunner list-services` empty, no
  CloudFront distributions, no dashboard-named S3 buckets, and exactly one
  ECS cluster (`cubic-mars-ecs-cluster-dev`) with **services unconfirmed**.
  Cognito user pools and ACM certs were never checked -- that's Phase 0 below.
- **This session added** (uncommitted, in the working tree, for PK to
  review/commit): `dashboard/Dockerfile`, `dashboard/nginx.conf`,
  `dashboard/.dockerignore`, `infrastructure/app_spoke/ecs-task-def.dashboard.json`,
  `infrastructure/app_spoke/deploy_dashboard_ecs.sh` (the runbook script).

## Why Fargate needs a couple of decisions before the first deploy
Because this is a container behind an ALB rather than static-file hosting,
three things have to exist or be created that a static-hosting path
wouldn't need, and none were confirmed live yet:
1. **A Cognito User Pool** (for the README's "Cognito SAML" auth gate on the
   ALB listener) -- Phase 0 of the script checks whether one already exists;
   if not, that's a separate provisioning step (user pool + app client +
   domain +, if SAML-federated to corporate IdP, an identity provider
   config) that should happen before Phase 4.
2. **An ACM certificate** for the ALB's HTTPS listener -- Phase 0 checks for
   an existing one in `us-east-1`; if none covers the intended internal
   hostname, request one first.
3. **The VPN / corporate CIDR** that's allowed to reach the internal ALB --
   the README says "VPN-only"; the runbook script deliberately leaves this
   as a placeholder rather than defaulting to `0.0.0.0/0`, since an internal
   ALB open to the world defeats the point of making it internal.

None of these block writing/reviewing the Dockerfile or task definition --
they only block Phases 3-4 (security groups, ALB/listener) of the runbook.

## Step-by-step
Full detail with copy-paste commands is in
`infrastructure/app_spoke/deploy_dashboard_ecs.sh` (a runbook, not a script
to run unattended -- read each phase's output before moving on). Summary:

1. **Phase 0 -- discovery.** Read-only: ECS cluster/services, existing ALBs,
   Cognito pools, ACM certs, ECR repos. Resolves the three open decisions
   above before anything is created.
2. **Phase 1 -- build & push the image.** `docker build` with
   `--build-arg VITE_API_BASE_URL=https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com`
   (Vite bakes this in at build time, it is NOT a container runtime env var),
   push to a new ECR repo `cubic-pdm/ps2-dashboard`. Run this from CloudShell
   or your own machine -- the cloud device-bridge VM used for this session's
   file edits has a Linux/Windows `node_modules` platform mismatch and can't
   run `npm run build` reliably; Docker's own `node:20-slim` build stage
   sidesteps that entirely since it does a clean `npm ci` inside the image.
3. **Phase 2 -- IAM.** Standard `ecsTaskExecutionRole` (ECR pull + Logs) if
   it doesn't already exist. Deliberately no custom task role permissions --
   this container only calls the API GW over HTTPS, never AWS APIs directly.
4. **Phase 3 -- security groups.** ALB SG open only to the VPN CIDR on
   443/80; task SG open only to the ALB SG on 8080. No public ingress
   anywhere in this chain.
5. **Phase 4 -- ALB + target group + listener.** Internal ALB in the 3
   existing private subnets; HTTPS listener with an
   `authenticate-cognito` action in front of the `forward` action, so the
   ALB itself enforces login before a request ever reaches a task.
6. **Phase 5 -- register task def + create the ECS service.** Uses
   `ecs-task-def.dashboard.json` (fill in the execution-role ARN and the
   ECR image URI:tag from Phase 1 first). `desired-count 2` for actual
   redundancy across AZs; deployment circuit breaker with automatic
   rollback enabled so a bad image can't wedge the service.
7. **Phase 6 -- verify.** `ecs wait services-stable`, then
   `describe-target-health` (expect `healthy` on both targets), then --
   from a VPN-connected machine -- open the ALB DNS name, sign in via
   Cognito, and click through every PS1/PS2/PS3/PS4/PS5 tab. Per the
   tracker's own "definition of deployed for real": it's not done until the
   dashboard renders live numbers, not a blank panel or an `ApiError` toast.
   (A toast/blank panel at this stage almost always means
   `VITE_API_BASE_URL` wasn't passed as a build-arg, or the ECS task's
   security group can't reach the API GW -- check Phase 3 before assuming
   the backend is broken.)

## Rollback
The service is created with `deploymentCircuitBreaker.rollback=true`, so a
task that fails its container health check auto-reverts to the last
good task-definition revision without manual intervention. For a manual
rollback: `aws ecs update-service --cluster cubic-mars-ecs-cluster-dev
--service ps2-dashboard --task-definition cubic-mars-ps2-dashboard:<previous-revision>`.
Nothing here touches RDS or the existing Lambda/API GW, so a bad dashboard
deploy can't take down PS1/PS2/PS3/PS5 data serving -- only the UI.

## Explicitly out of scope for this runbook
- CI/CD automation of Phases 1/5 (build-on-push, auto-deploy) -- follow-on
  once the manual path is proven once, per the tracker's P2 CI/CD action.
- WAF in front of the ALB -- README lists it; add once the ALB is internal
  and Cognito-gated and proven working, since WAF on an already VPN-only,
  authenticated internal ALB is defense-in-depth, not a launch blocker.
- Route 53 private hosted zone / friendly hostname for the ALB -- fine to
  use the raw ALB DNS name for first go-live, add a CNAME once proven.
- PS4 dashboard tab (still mock-only per the live-only refactor's explicit
  scope) and the CMDB-map / ServiceNow-button dependency chain -- unrelated
  to standing up hosting, tracked separately.
