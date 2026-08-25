# ps4v2_* -- delivered 29-Jul-2026, never run, retired 25-Aug-2026

The additive migration + backfill loader + CloudShell runner for a set of
ps4v2_* Aurora tables. Confirmed never executed: no ps4v2_* table exists in
appdb (verified live 24-Aug), and content review shows the tables would have
duplicated ps4_cluster_assignments (8,978 rows) and ps4_cluster_summary (25),
both already loaded daily by cubic-mars-ps4-rds-loader. cluster_profile_out
was also incomplete at delivery.

Retired rather than run. Committed here because until 25-Aug these three files
existed as a SINGLE UNTRACKED COPY outside the repo -- the same hazard that
nearly lost the PS4 v3 producer (19-Aug) and did lose the PS5 view DDL until
its recovery from a deployed Lambda zip (25-Aug). Anything delivered to this
project gets committed before it gets retired.
