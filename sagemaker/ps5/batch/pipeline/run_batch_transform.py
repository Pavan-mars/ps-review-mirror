#!/usr/bin/env python3
# =============================================================================
# run_batch_transform.py -- score the PS5 fleet with SageMaker Batch Transform
# on the BYOC ECR image. One transform for the device-state input, one for the
# serial roster. Output parquet lands in S3 for the RDS loader.
#
# Mirrors the PS3 Transformer pattern. RUL scoring is featherweight, so a single
# ml.m5.large finishes in a couple minutes; bump MaxPayloadInMB for wide inputs.
#
# Usage:
#   python run_batch_transform.py \
#     --image-uri 170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-ps5-scorer:v5.1 \
#     --model-data s3://.../chicago/ps5/model/model_v5.1.tar.gz \
#     --role arn:aws:iam::170202974600:role/<sagemaker-exec-role> \
#     --device-input s3://.../chicago/ps5/state/device/ \
#     --serial-input s3://.../chicago/ps5/state/serial/ \
#     --output s3://.../chicago/ps5/scored/ --asof 2026-04-11
# =============================================================================
import argparse


def make_transformer(sm_model, instance_type, output_path, max_payload, label):
    return sm_model.transformer(
        instance_count=1, instance_type=instance_type,
        strategy="SingleRecord", assemble_with="Line",
        output_path=output_path, max_payload=max_payload,
        accept="application/x-parquet",
        tags=[{"Key": "project", "Value": "cubic-mars-ps5"},
              {"Key": "grain", "Value": label}],
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image-uri", required=True)
    ap.add_argument("--model-data", required=True)
    ap.add_argument("--role", required=True)
    ap.add_argument("--device-input", required=True, help="S3 prefix of <type>_device_state.parquet files")
    ap.add_argument("--serial-input", required=False, help="S3 prefix of <type>_serial roster parquet files")
    ap.add_argument("--output", required=True, help="S3 prefix for scored output")
    ap.add_argument("--asof", required=True)
    ap.add_argument("--instance-type", default="ml.m5.large")
    ap.add_argument("--max-payload", type=int, default=20, help="MaxPayloadInMB (raise for wide feature frames)")
    ap.add_argument("--region", default="us-east-1")
    args = ap.parse_args()

    import boto3, sagemaker
    from sagemaker.model import Model
    sess = sagemaker.Session(boto3.session.Session(region_name=args.region))

    model = Model(image_uri=args.image_uri, model_data=args.model_data, role=args.role,
                  sagemaker_session=sess, name=f"cubic-mars-ps5-scorer-{args.asof}".replace(".", "-"))

    # DEVICE grain
    dev_out = args.output.rstrip("/") + f"/device/asof={args.asof}/"
    dt = make_transformer(model, args.instance_type, dev_out, args.max_payload, "device")
    print(f"[batch] device transform: {args.device_input} -> {dev_out}")
    dt.transform(data=args.device_input, content_type="application/x-parquet", split_type="None", wait=True)
    print(f"[batch] device done -> {dt.output_path}")

    # SERIAL grain (reuses the same model/image; container auto-detects the roster payload)
    if args.serial_input:
        ser_out = args.output.rstrip("/") + f"/serial/asof={args.asof}/"
        st = make_transformer(model, args.instance_type, ser_out, args.max_payload, "serial")
        print(f"[batch] serial transform: {args.serial_input} -> {ser_out}")
        st.transform(data=args.serial_input, content_type="application/x-parquet", split_type="None", wait=True)
        print(f"[batch] serial done -> {st.output_path}")

    print(f"BATCH_OUTPUT={args.output.rstrip('/')}   ASOF={args.asof}")


if __name__ == "__main__":
    main()
