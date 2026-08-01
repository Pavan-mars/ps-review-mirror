# cubic-mars-ps3-inference

New Lambda: scores **new** device/serial data against the PS3 two-head SageMaker
real-time endpoint (item 2+3 of the 21-Jul PS3 deep-dive ask -- "containerize +
endpoints" then "a new Lambda function to generate inference to the endpoints
with new data"). Decoupled from RDS writing: this Lambda's job stops at
"scored the data + wrote it to S3 in the load contract" -- `cubic-mars-ps3-rds-push`
(sibling Lambda in this delivery) does the actual RDS upsert, same
separation-of-concerns the PS2 serial-grain delivery already proved out.

## Deploy

1. Package `handler.py` + a `pandas`/`pyarrow` layer (attach the AWS-managed
   `AWSSDKPandas-Python312` layer -- do not bundle these, see `requirements.txt`).
2. Create the function (Python 3.12, same VPC/subnets as `cubic-mars-dashboard-api`
   if this Lambda is reached only via the API GW route below; give it its own SG
   if it also needs outbound to the SageMaker endpoint's VPC endpoint).
3. Environment variables:
   - `SAGEMAKER_ENDPOINT_NAME` = the endpoint `deploy_endpoint.py` created
     (default assumed: `cubic-mars-ps3-two-head-dev`)
   - `OUTPUT_S3_BUCKET` = `cubic-mars-pm-s3-datalake-dev-gold-170202974600`
   - `OUTPUT_S3_PREFIX` = `chicago/ps3_deepdive` (default)
   - `CITY_ID` = `CHI`
4. Execution role needs: `sagemaker:InvokeEndpoint` on the PS3 endpoint ARN,
   `s3:PutObject` on `OUTPUT_S3_BUCKET/OUTPUT_S3_PREFIX/*`.
5. Wire `POST /ps3/infer` on the existing API Gateway to this Lambda (see
   `routes_ps3_deepdive_and_servicenow.py` for the dashboard-side contract this
   Lambda's response shape needs to match, if you'd rather proxy through the
   existing `cubic-mars-dashboard-api` Lambda instead of a direct API GW
   integration -- both work, direct integration is simpler and is what this
   README assumes).

## Invocation shapes

- **Dashboard "Score new data" panel** -> `POST /ps3/infer` with
  `{"instances": [{...feature row...}]}` -> synchronous JSON response with
  predictions inline, so the UI shows results immediately.
- **Batch / new-data-landed trigger** -> direct Lambda invoke or an S3
  `ObjectCreated` trigger with `{"s3_bucket": ..., "s3_key": ...}` pointing at
  a CSV/JSON of new rows (e.g. a Databricks job emitting only the incidents
  new since the last daily PS3 batch score, ahead of the next full run).

## Test

`python3 -m pytest test_handler.py -v` -- 6 cases, no live AWS needed (mocks
`sagemaker-runtime` and `s3`). Covers: both invocation shapes, the CORRECTED
severity collapse (the 18-Jul bug this delivery must not repeat -- verified
directly in `test_direct_invoke_instances_shape` and
`test_api_gateway_body_shape`), the S3 write contract
`cubic-mars-ps3-rds-push` depends on, and error handling (missing instances,
SageMaker `ModelError`).

## What this Lambda deliberately does NOT do

- Does not reimplement any scoring logic -- it only calls
  `sagemaker-runtime.invoke_endpoint`; `inference.py`'s `model_fn`/`predict_fn`
  on the endpoint side is the single source of truth for how a prediction is
  produced.
- Does not write to RDS directly -- see `cubic-mars-ps3-rds-push`.
