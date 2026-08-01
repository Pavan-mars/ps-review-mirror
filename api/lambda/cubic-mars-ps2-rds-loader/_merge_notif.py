"""Merge this Lambda's S3 notification rule into the bucket's existing config.

put-bucket-notification-configuration REPLACES the whole configuration, so
posting only our rule would delete PS1's and PS4's. Read, drop any rule with OUR
Id, append ours, write the union.
"""
import json
import sys

region, acct, fn, prefix, bucket = sys.argv[1:6]
cfg = json.load(open("/tmp/ps2_notif_cur.json"))
cfg.pop("ResponseMetadata", None)
ours = [{
    "Id": "ps2-manifest-load",
    "LambdaFunctionArn": f"arn:aws:lambda:{region}:{acct}:function:{fn}",
    "Events": ["s3:ObjectCreated:*"],
    "Filter": {"Key": {"FilterRules": [
        {"Name": "prefix", "Value": f"{prefix}/"},
        {"Name": "suffix", "Value": "manifest.json"}]}},
}]
ids = {o["Id"] for o in ours}
keep = [c for c in cfg.get("LambdaFunctionConfigurations", []) if c.get("Id") not in ids]
cfg["LambdaFunctionConfigurations"] = keep + ours
json.dump(cfg, open("/tmp/ps2_notif.json", "w"), indent=2)
print(f"   {bucket}: {len(ours)} rule ours, {len(keep) + len(ours)} total")
