# Retired 13-Sep-2026: the PS3 Batch Transform path

PS3 keeps one pipeline: `PS3_V26_PRODUCTION.ipynb` as a SageMaker Processing job
(`sagemaker/ps3/processing/`) publishing to `chicago/ps3_outputs/<table>/computed_date=/run_id=/`,
loaded by `cubic-mars-ps3-v25-loader`. That is the path every `/ps3/v25/*` route reads.

The Step Functions Batch Transform path wrote `chicago/ps3/scored/asof=`, which no loader could read
(the v25 loader is fenced to `chicago/ps3_outputs`), so a green execution loaded nothing. Retired
rather than repointed so that each use case has exactly one path.

Files kept here for reference: `ps3_daily_scoring.asl.json`, `ps3_eventbridge_build.sh`,
`batch_transform_daily.py`, `deploy_endpoint.py`. AWS side: `tooling/retire_ps3_batch_transform.sh`
(captures, then deletes the state machine, the three never-enabled rules and two roles).
