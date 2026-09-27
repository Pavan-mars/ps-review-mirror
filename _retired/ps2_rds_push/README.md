# cubic-mars-ps2-rds-push - RETIRED, source kept for the record

S3-event-triggered auto-schema loader from the 21-Jul-2026 serial-grain delivery.
Committed 19-Aug-2026, having never been in this repo before; it existed only as the
deployed function, a 21-Jul delivery zip, and a CloudShell build directory (~/ps2push).

## Verification at time of commit
Deployed CodeSha256 `rUHPoCvGPlszMXRJWviqLYMEfVCnNbBK46yInKjBv8Q=` matched the local
build zip exactly, and handler.py in-zip vs on-disk were byte-identical (14,050 bytes).
So this file is verifiably the code that was running.
Full deployment package archived at
`s3://cubic-mars-pm-s3-datalake-dev-artifacts-170202974600/archive/lambda_source/ps2-rds-push-source-19Aug2026.zip`
(63,636,135 bytes, size-verified).

## What it did
Loaded PS2_Serial_Grain_Analysis_v1 output (Parquet + manifest.json) into Aurora,
auto-evolving the schema: `ADD COLUMN IF NOT EXISTS` for new columns, and a deliberate
refusal to auto-`ALTER TYPE` on a type conflict - skipped and logged to `ps2_load_audit`
for human review. Idempotent via DELETE on (grain, computed_date, run_id) then INSERT.
Config: python3.12, 512 MB, 600s, 3 private subnets, sg-09a66a0caa24a5fc6, host read
from the secret with `RDS_HOST` as fallback.

## Why retired (19-Aug-2026, tracker #80)
- No S3 notification on either bucket - nothing could invoke it.
- Superseded: the scheduled `cubic-mars-ps2-rds-loader` loads every serial family it
  served (verified in the 19-Aug dry run: 47 families, 294,749 rows, 0 errors).
- Its last real run, 01-Aug-2026 14:46 UTC, FAILED with `KeyError: 'grain'` on
  `ps2_outputs/ps2_v2_run_quality/.../manifest.json` - a bare-prefix S3 event was feeding
  it v2_5_4 manifests, which carry no `grain` key. It was built for the serial-grain
  manifest contract only.
- It held credentials to `cubic-mars-secret-rds-dev`, unrotated since 22-Jul (A8).
