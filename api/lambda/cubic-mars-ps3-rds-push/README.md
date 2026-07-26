# cubic-mars-ps3-rds-push

S3-event-triggered Lambda that loads PS3 deep-dive outputs (device/serial risk
leaderboards, entity SHAP drivers, cross-tabs, risk trend — from
`notebooks/ps3/ps3_deepdive_engine.py`'s `write_output()`) **and** the
on-demand scoring output from the sibling `cubic-mars-ps3-inference` Lambda
into Aurora PostgreSQL, auto-evolving the schema as new columns/tables appear.
Item 4 of the 21-Jul ask ("push all the outputs to RDS while ensuring the RDS
schema is updated") — the auto-ALTER logic is exactly *why* this can say yes
to "ensuring the schema is updated" without a human running a migration for
every new column a future notebook edit adds.

This is a direct fork of `cubic-mars-ps2-rds-push` (built 21-Jul-2026 for the
PS2 serial-grain delivery, itself already proven against real local Postgres
+ moto S3) — see that Lambda's own README for the full design rationale. Kept
as an independent copy (not a shared import) so this Lambda has zero
cross-Lambda deploy dependency; a follow-up refactor to one shared loader
across every PS is a nice-to-have, not a blocker.

## Local test — VERIFIED against real local PostgreSQL 16 (this delivery)

```
sudo service postgresql start
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'testpass';"
sudo -u postgres createdb ps3_test
pip install pg8000 pyarrow moto boto3 pandas
RDS_HOST=127.0.0.1 RDS_PORT=5432 RDS_DATABASE=ps3_test \
RDS_TEST_USER=postgres RDS_TEST_PASSWORD=testpass \
python3 test_handler.py
```

All 4 cases pass: (1) `CREATE TABLE` from scratch, (2) `ADD COLUMN IF NOT
EXISTS` on a re-run with a new metric column + (2b) an idempotent re-run of
the same manifest proving no duplicate rows, (3) a genuine type conflict —
asserts the column is **skipped**, logged to `ps3_load_audit`, and the stored
column type is left **unchanged**, and (4) the `cubic-mars-ps3-inference`
Lambda's on-demand producer shape loading through the identical path (proves
the two Lambdas' write contracts genuinely agree, not just by inspection).

## Deploy (once local test passes)

1. Package: `pip install pg8000 pyarrow -t package/ && cp handler.py package/ && cd package && zip -r ../cubic-mars-ps3-rds-push.zip .`
2. Create the Lambda (python3.12) **inside the same 3 private subnets as
   `cubic-mars-dashboard-api`** (`subnet-0830633f6cab1b8a1`,
   `subnet-01ad20b3b49bf59de`, `subnet-0cd6e4bca78eaab8d`, VPC
   `vpc-0a7775adc7d382fbb`) — the RDS security group (`sg-0eacdb65e98dff5a6`)
   only accepts port 5432 from the VPC CIDR, this is not optional.
3. **New, least-privilege execution role** (do not reuse
   `cubic-mars-dashboard-api`'s role):
   - `s3:GetObject` on `arn:aws:s3:::cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/ps3_deepdive/*`
   - `secretsmanager:GetSecretValue` on the `cubic-mars-secret-rds-dev` secret ARN only
   - Standard `AWSLambdaVPCAccessExecutionRole` managed policy
4. Environment variables: `RDS_SECRET_ID=cubic-mars-secret-rds-dev`,
   `RDS_DATABASE=<the real db name>`, `AWS_REGION=us-east-1`.
5. **S3 trigger — suffix filter `manifest.json` only**, on the
   `chicago/ps3_deepdive/` prefix. Both producers (the deep-dive notebook and
   `cubic-mars-ps3-inference`) write the manifest last per table specifically
   so this filter is correct and sufficient for both.
6. First live run: point at a **scratch prefix**
   (`chicago/ps3_deepdive_scratch/`) and a throwaway table-name suffix,
   confirm CloudWatch logs + `SELECT * FROM ps3_load_audit ORDER BY id DESC
   LIMIT 20` look right, THEN switch to the real prefix the live trigger
   watches.
7. Apply `sql/05_phase1c_ps3_deepdive_enrichment.sql` FIRST (creates the
   tables up front with sane types/PKs/indexes) — this Lambda's auto-ALTER
   then only ever *adds* columns after that point, never bootstraps a brand
   new production table from an untyped Parquet inference.
