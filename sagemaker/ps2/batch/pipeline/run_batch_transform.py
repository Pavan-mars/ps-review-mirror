#!/usr/bin/env python3
# =============================================================================
# run_batch_transform.py -- score the PS2 fleet with SageMaker Batch Transform
# on the BYOC ECR image. One Transform per (family, grain): markov/hmm/
# recurrence, each at device grain and (optionally) serial grain. Each state
# parquet already carries a 'family' column (baked in by refresh_device_state.py)
# so the SAME Model/image serves all six calls -- no per-call env-var wiring.
#
# Mirrors the PS3 Transformer pattern (and sagemaker/ps5/batch/pipeline/
# run_batch_transform.py). Cascade scoring is featherweight -- a single
# ml.m5.large finishes quickly per family.
#
# Usage:
#   python run_batch_transform.py \
#     --image-uri 170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-ps2-scorer:v1.0 \
#     --model-data s3://.../chicago/ps2/model/model_v1.0.tar.gz \
#     --role arn:aws:iam::170202974600:role/<sagemaker-exec-role> \
#     --state-prefix s3://.../chicago/ps2/state --output s3://.../chicago/ps2/scored --asof 2026-07-24
# =============================================================================
import argparse


def make_transformer(sm_model, instance_type, output_path, max_payload, label):
    return sm_model.transformer(
        instance_count=1, instance_type=instance_type,
        strategy="SingleRecord", assemble_with="Line",
        output_path=output_path, max_payload=max_payload,
        accept="application/x-parquet",
        tags=[{"Key": "project", "Value": "cubic-mars-ps2"}, {"Key": "grain", "Value": label}],
    )


FAMILIES = ["markov", "hmm", "recurrence"]
GRAINS = ["device", "serial"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image-uri", required=True)
    ap.add_argument("--model-data", required=True)
    ap.add_argument("--role", required=True)
    ap.add_argument("--state-prefix", required=True,
                    help="s3://.../chicago/ps2/state (expects <family>/<grain>/ subfolders per asof)")
    ap.add_argument("--output", required=True, help="S3 prefix for scored output")
    ap.add_argument("--asof", required=True)
    ap.add_argument("--families", default=",".join(FAMILIES))
    ap.add_argument("--instance-type", default="ml.m5.large")
    ap.add_argument("--max-payload", type=int, default=20)
    ap.add_argument("--region", default="us-east-1")
    args = ap.parse_args()

    import boto3, sagemaker
    from sagemaker.model import Model
    sess = sagemaker.Session(boto3.session.Session(region_name=args.region))
    s3 = boto3.client("s3", region_name=args.region)

    def _exists(uri):
        b, _, k = uri.replace("s3://", "").partition("/")
        resp = s3.list_objects_v2(Bucket=b, Prefix=k, MaxKeys=1)
        return resp.get("KeyCount", 0) > 0

    model = Model(image_uri=args.image_uri, model_data=args.model_data, role=args.role,
                  sagemaker_session=sess, name=f"cubic-mars-ps2-scorer-{args.asof}".replace(".", "-"))

    ran, skipped = [], []
    for family in args.families.split(","):
        for grain in GRAINS:
            src = f"{args.state_prefix.rstrip('/')}/{family}/{grain}/asof={args.asof}/"
            if not _exists(src):
                print(f"  [skip] {family}/{grain}: no input at {src}")
                skipped.append((family, grain)); continue
            out = f"{args.output.rstrip('/')}/{family}/{grain}/asof={args.asof}/"
            t = make_transformer(model, args.instance_type, out, args.max_payload, f"{family}-{grain}")
            print(f"[batch] {family}/{grain}: {src} -> {out}")
            t.transform(data=src, content_type="application/x-parquet", split_type="None", wait=True)
            print(f"[batch] {family}/{grain} done -> {t.output_path}")
            ran.append((family, grain))

    print(f"BATCH_OUTPUT={args.output.rstrip('/')}   ASOF={args.asof}   ran={ran}   skipped={skipped}")


if __name__ == "__main__":
    main()
