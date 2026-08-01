#!/usr/bin/env python3
# =============================================================================
# package_model.py -- bundle the PS2 markov/hmm/recurrence params into
# model.tar.gz for SageMaker. Mirrors sagemaker/ps5/batch/pipeline/package_model.py
# exactly. The tar's contents land in /opt/ml/model in the container; serve.py
# loads *_markov_params.json + ps2_hmm_params.json + ps2_recurrence_params.json
# from there.
#
# Usage:
#   python package_model.py --outputs ps2_model_out \
#       --bucket cubic-mars-pm-s3-datalake-dev-gold-170202974600 \
#       --prefix chicago/ps2/model --tag v1.0
# Prints the model_data S3 URI to feed run_batch_transform.py.
# =============================================================================
import argparse, glob, os, tarfile, tempfile


def collect_params(outputs_dir):
    pats = ["*_markov_params.json", "ps2_hmm_params.json", "ps2_recurrence_params.json"]
    files = []
    for p in pats:
        files += glob.glob(os.path.join(outputs_dir, p))
    return sorted(set(files))


def build_tar(files, tar_path):
    with tarfile.open(tar_path, "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=os.path.basename(f))
    return tar_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outputs", default="ps2_model_out")
    ap.add_argument("--bucket", required=False)
    ap.add_argument("--prefix", default="chicago/ps2/model")
    ap.add_argument("--tag", default="v1.0")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    files = collect_params(args.outputs)
    if not files:
        raise SystemExit(f"no param JSONs found under {args.outputs}")
    tar_path = args.out or os.path.join(tempfile.gettempdir(), f"ps2_model_{args.tag}.tar.gz")
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
