# Feature Group capture, 25-Aug-2026 — pre-deletion record

describe-feature-group JSON for chicago-ps2-features and chicago-ps3-features,
taken immediately before both were deleted on 25-Aug-2026.

Why deleted: both producers are formally retired (notebooks/_retired/ps2/ and
/ps3/), yet both groups kept an ONLINE store enabled (OnDemand mode; sizes
1,042,530 and 187,455 bytes at capture) — paying for pipelines that no longer
exist. Deletion per the 24-Aug adjudicated cleanup inventory.

What survives: both OFFLINE stores (S3 URIs inside each JSON) and their Glue
tables (chicago_ps2_features_1782723612 / chicago_ps3_features_1783925411 in
sagemaker_featurestore) retain the accumulated history. Recreation, if ever
needed, is the delete-and-recreate self-healing path in the retired notebooks
plus these definitions.

Remaining groups (7) were deliberately kept — see the adjudicated inventory.
