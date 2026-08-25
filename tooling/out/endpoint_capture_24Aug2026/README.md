# Endpoint capture, 24-Aug-2026 — the D-4 deletion record

Full describe-endpoint / describe-endpoint-config / describe-model JSON for all
FOUR endpoints (including chicago-ps3-rootcause-v1, which the earlier 15-Aug
capture in tooling/out/endpoint_capture/ did not cover), taken 24-Aug-2026
immediately before deletion.

The deletion executed 25-Aug-2026: 4 endpoints + 4 endpoint-configs deleted,
list-endpoints and list-endpoint-configs both verified empty. Model objects were
deliberately KEPT. All four endpoints had 0 invocations for the preceding 14 days
and were created 24-Jul — before both 26-Jul training runs, so they served neither.
