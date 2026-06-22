# Chicago Ventra Mars Cubic Analysis — the assistant Context

## Project
CUBIC MARS predictive maintenance for Chicago CTA Ventra devices.
AWS account 170202974600, us-east-1.

## Critical Rules
- Oracle credentials (10.3.10.30:1521) MUST stay in Secrets Manager (`dev_chicago_oracle`) — never in code or docs.
- IAM users: NEVER create (SCP p-sx3nl37r blocks iam:CreateUser). Use Identity Center / Entra SSO only.
- SVN_STAGE tables: always LEFT JOIN, never INNER JOIN (0 rows).
- Data stays in corporate environment — never external.

## ML Guidelines
- SMOTE: avoid on time-series data (future-signal leakage). Use `scale_pos_weight` instead.
- Optuna: average AUC across ALL TimeSeriesSplit folds, never just the last fold.
- HDBSCAN preferred over DBSCAN for PS4 anomaly clustering.

## Device Filter
Chicago devices: CTA00001–CTA00300. Filter: `RLIKE '^CTA\d{5}'`

## Failure Labels
ALL maintenance/parts/RMA tables are EMPTY. Use `AVAILABILITY_EVENTS` as failure labels.

## Code Standards
- Always provide complete, runnable code blocks — never partial snippets.
- Single commit per day: squash with `git reset --soft HEAD~N`.
