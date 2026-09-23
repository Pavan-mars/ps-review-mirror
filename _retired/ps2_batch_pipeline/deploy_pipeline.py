#!/usr/bin/env python3
# =============================================================================
# deploy_pipeline.py -- create/update the PS2 Step Function state machine from
# state_machine.asl.json (substituting the ${...} placeholders) and wire a
# daily EventBridge rule to it. This is the first real Step Function/EventBridge
# build for this project -- sagemaker/ps5/batch/README_batch_pipeline.md only
# ever recommended this pattern in prose; this script actually stands it up,
# and is written generically enough that PS5 (or PS1/PS3) could reuse it later
# by pointing --asl-template at their own state machine definition.
#
# One-time setup only (state machine + rule config). The daily "asof" date is
# computed INSIDE each Processing Job container at run time (date -u +%F), not
# threaded through Step Functions input, so the schedule itself stays static.
#
# Usage:
#   python deploy_pipeline.py \
#     --state-machine-name cubic-mars-ps2-cascade-daily \
#     --asl-template state_machine.asl.json \
#     --sagemaker-exec-role-arn arn:aws:iam::170202974600:role/<sagemaker-exec-role> \
#     --states-exec-role-arn arn:aws:iam::170202974600:role/<step-functions-exec-role> \
#     --eventbridge-role-arn arn:aws:iam::170202974600:role/<eventbridge-invoke-states-role> \
#     --pipeline-image-uri 170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-ps2-pipeline:v1.0 \
#     --scorer-image-uri   170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-ps2-scorer:v1.0 \
#     --model-tag v1.0 --gold-bucket cubic-mars-pm-s3-datalake-dev-gold-170202974600 \
#     --rds-secret-id cubic/rds/dashboard --cron "cron(0 9 * * ? *)"   # 09:00 UTC daily
# =============================================================================
import argparse, json, re


def render(template_text, subs):
    def _sub(m):
        key = m.group(1)
        if key not in subs:
            raise SystemExit(f"missing substitution for ${{{key}}} -- pass --{key.lower().replace('_', '-')}")
        return subs[key]
    return re.sub(r"\$\{([A-Z0-9_]+)\}", _sub, template_text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-machine-name", default="cubic-mars-ps2-cascade-daily")
    ap.add_argument("--asl-template", default="state_machine.asl.json")
    ap.add_argument("--sagemaker-exec-role-arn", required=True)
    ap.add_argument("--states-exec-role-arn", required=True, help="IAM role Step Functions assumes to run this state machine")
    ap.add_argument("--eventbridge-role-arn", required=True, help="IAM role EventBridge assumes to start a Step Functions execution")
    ap.add_argument("--pipeline-image-uri", required=True)
    ap.add_argument("--scorer-image-uri", required=True)
    ap.add_argument("--model-tag", default="v1.0")
    ap.add_argument("--gold-bucket", required=True)
    ap.add_argument("--rds-secret-id", required=True)
    ap.add_argument("--cron", default="cron(0 9 * * ? *)", help="EventBridge schedule expression, UTC")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--dry-run", action="store_true", help="render + validate only, no AWS calls")
    args = ap.parse_args()

    subs = {
        "SAGEMAKER_EXEC_ROLE_ARN": args.sagemaker_exec_role_arn,
        "PIPELINE_IMAGE_URI": args.pipeline_image_uri,
        "SCORER_IMAGE_URI": args.scorer_image_uri,
        "MODEL_TAG": args.model_tag,
        "GOLD_BUCKET": args.gold_bucket,
        "RDS_SECRET_ID": args.rds_secret_id,
    }
    template = open(args.asl_template).read()
    rendered = render(template, subs)
    json.loads(rendered)  # validate
    print(f"[deploy] rendered + validated {args.asl_template}")

    if args.dry_run:
        print(rendered)
        return

    import boto3
    sfn = boto3.client("stepfunctions", region_name=args.region)
    events = boto3.client("events", region_name=args.region)

    existing = None
    for sm in sfn.list_state_machines().get("stateMachines", []):
        if sm["name"] == args.state_machine_name:
            existing = sm["stateMachineArn"]; break

    if existing:
        sfn.update_state_machine(stateMachineArn=existing, definition=rendered, roleArn=args.states_exec_role_arn)
        sm_arn = existing
        print(f"[deploy] updated state machine {sm_arn}")
    else:
        resp = sfn.create_state_machine(
            name=args.state_machine_name, definition=rendered, roleArn=args.states_exec_role_arn,
            type="STANDARD", tags=[{"key": "project", "value": "cubic-mars-ps2"}])
        sm_arn = resp["stateMachineArn"]
        print(f"[deploy] created state machine {sm_arn}")

    rule_name = f"{args.state_machine_name}-schedule"
    events.put_rule(Name=rule_name, ScheduleExpression=args.cron, State="ENABLED",
                    Description="Daily trigger for the PS2 cascade Step Function (state-refresh -> transform -> load)")
    events.put_targets(Rule=rule_name, Targets=[
        {"Id": "ps2-cascade-daily-target", "Arn": sm_arn, "RoleArn": args.eventbridge_role_arn}
    ])
    print(f"[deploy] EventBridge rule '{rule_name}' -> {sm_arn}  (schedule: {args.cron})")
    print("DONE")


if __name__ == "__main__":
    main()
