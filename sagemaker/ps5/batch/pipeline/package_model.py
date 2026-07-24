#!/usr/bin/env python3
# =============================================================================
# package_model.py -- bundle the slim PS5 params into model.tar.gz for SageMaker.
# The tar's contents land in /opt/ml/model in the container; serve.py loads the
# *_device_survival_params.json + *_serial_params.json from there.
#
# Usage:
#   python package_model.py --outputs PS5_reliability_v5_outputs \
#       --bucket cubic-mars-pm-s3-datalake-dev-gold-170202974600 \
#       --prefix chicago/ps5/model --tag v5.1
# Prints the model_data S3 URI to feed run_batch_transform.py.
# =============================================================================
import argparse, glob, os, tarfile, tempfile


def collect_params(outputs_dir):
    """Find the 6 param JSONs whether outputs are in per-type subfolders or flat."""
    pats = ["*/*_device_survival_params.json", "*/*_serial_params.json",
            "*_device_survival_params.json", "*_serial_params.json"]
    files = []
    for p in pats:
        files += glob.glob(os.path.join(outputs_dir, p))
    return sorted(set(files))


def build_tar(files, tar_path):
    with tarfile.open(tar_path, "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=os.path.basename(f))   # flat inside the tar -> /opt/ml/model/<name>
    return tar_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outputs", default="PS5_reliability_v5_outputs")
    ap.add_argument("--bucket", required=False)
    ap.add_argument("--prefix", default="chicago/ps5/model")
    ap.add_argument("--tag", default="v5.1")
    ap.add_argument("--out", default=None, help="local model.tar.gz path (default: temp)")
    args = ap.parse_args()

    files = collect_params(args.outputs)
    if not files:
        raise SystemExit(f"no param JSONs found under {args.outputs}")
    tar_path = args.out or os.path.join(tempfile.gettempdir(), f"ps5_model_{args.tag}.tar.gz")
    build_tar(files, tar_path)
    print(f"[package] {len(files)} param files -> {tar_path}")
    for f in files:
        print(f"          + {os.path.basename(f)}")

    if args.bucket:
        import boto3
        key = f"{args.prefix}/model_{args.tag}.tar.gz"
        boto3.client("s3").upload_file(tar_path, args.bucket, key)
        uri = f"s3://{args.bucket}/{key}"
        print(f"[package] uploaded -> {uri}")
        print(f"MODEL_DATA={uri}")
    else:
        print("[package] no --bucket given; upload manually, then pass its s3:// as --model-data")


if __name__ == "__main__":
    main()
