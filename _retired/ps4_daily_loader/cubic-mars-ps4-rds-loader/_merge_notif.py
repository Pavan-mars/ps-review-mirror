"""Merge this Lambda's S3 notification rules into a bucket's existing config.

Split out of deploy.sh because put-bucket-notification-configuration REPLACES the
whole configuration: posting only our rules would silently delete PS1's trigger.
Reads the current config, drops any rule carrying one of OUR Ids, appends ours,
writes the union.
"""
import json
import sys

region, acct, fn, prefix, bucket, artifact = sys.argv[1:7]
cfg = json.load(open("/tmp/ps4_notif_cur.json"))
cfg.pop("ResponseMetadata", None)
arn = f"arn:aws:lambda:{region}:{acct}:function:{fn}"

if bucket == artifact:
    ours = [{
        "Id": "ps4-scored-manifest-load",
        "LambdaFunctionArn": arn,
        "Events": ["s3:ObjectCreated:*"],
        "Filter": {"Key": {"FilterRules": [
            {"Name": "prefix", "Value": f"{prefix}/manifest/"},
            {"Name": "suffix", "Value": "manifest.json"}]}},
    }]
else:
    # One rule per device type. An S3 suffix filter is a literal match, so a
    # single rule for "manifest.json" would never fire on "tvm_manifest.json".
    ours = [{
        "Id": f"ps4-cluster-manifest-{t}",
        "LambdaFunctionArn": arn,
        "Events": ["s3:ObjectCreated:*"],
        "Filter": {"Key": {"FilterRules": [
            {"Name": "prefix", "Value": f"{prefix}/clustering/manifest/"},
            {"Name": "suffix", "Value": f"{t}_manifest.json"}]}},
    } for t in ("tvm", "gate", "validator")]

ids = {o["Id"] for o in ours}
keep = [c for c in cfg.get("LambdaFunctionConfigurations", []) if c.get("Id") not in ids]
cfg["LambdaFunctionConfigurations"] = keep + ours
json.dump(cfg, open("/tmp/ps4_notif.json", "w"), indent=2)
print(f"   {bucket}: {len(ours)} rule(s) ours, {len(keep) + len(ours)} total")
