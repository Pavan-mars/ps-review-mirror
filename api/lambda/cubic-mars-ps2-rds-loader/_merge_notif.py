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
        # ONE MARKER PER RUN, NOT ONE PER TABLE.                 23-Sep-2026
        # handler.py ignores event["Records"] and always loads every table, so
        # a manifest.json filter fired a full 47-table load for EACH manifest
        # written -- about 47 redundant loads per run. The patterns notebook
        # writes a single {prefix}/_runs/as_of_date=.../run_id=.../
        # run_complete.json at the end, which is one object per run and sits
        # under the same prefix filter.
        # The Id stays "ps2-manifest-load" deliberately: the merge below drops
        # only rules whose Id is in `ours`, so renaming it would leave the old
        # per-manifest rule alive in the merged configuration.
        {"Name": "suffix", "Value": "run_complete.json"}]}},
}]
ids = {o["Id"] for o in ours}
keep = [c for c in cfg.get("LambdaFunctionConfigurations", []) if c.get("Id") not in ids]
cfg["LambdaFunctionConfigurations"] = keep + ours
json.dump(cfg, open("/tmp/ps2_notif.json", "w"), indent=2)
print(f"   {bucket}: {len(ours)} rule ours, {len(keep) + len(ours)} total")
