# Oracle ⇄ Databricks connectivity — root cause & fix (2026-06-24)

## Symptom
Every Oracle JDBC call from the `cubic-mars-dev` Databricks cluster hung, then failed with:

```
java.sql.SQLRecoverableException: IO Error: Socket read timed out,
connect lapse = <ReadTimeout> ms, Authentication lapse 0 ms.
    at oracle.jdbc.driver.T4CConnection.logon(...)
```

`Authentication lapse 0 ms` = the driver **never finished the login handshake**. This is a **connection**
failure, **not a slow query** — and it silently made ingestion jobs appear to "run" for many minutes while
doing nothing (each attempt blocked the full `ReadTimeout`). It started right after the **Jun-23 AWS
maintenance / failover on VPN tunnel T2**; the same loads had worked earlier the same day.

## Root cause — VPN tunnel MTU black-hole (our side)
Bounded network tests from the Databricks driver (inside the Mars VPC) — see
`notebooks/ingestion/oracle_connectivity_diagnostics.py`:

| Test | Result | Meaning |
|---|---|---|
| Route to `10.3.10.30` | `via 10.231.92.132 dev eth0` | routing exists |
| **TCP connect `10.3.10.30:1521`** | ✅ OK in **0.02 s** | tunnel up, listener accepting connections |
| Ping 100 B | ✅ 0% loss, ~21 ms | host reachable; small packets round-trip |
| **Ping 1472 B, DF set** | ❌ `Frag needed and DF set (mtu = 1422)` from **`169.254.3.1`** | **the AWS VPN tunnel's path MTU is 1422; full-size 1500 B packets are dropped** |
| Oracle TNS listener probe | no reply (black hole) | the larger TNS exchange goes into the void |

`169.254.3.1` is the **AWS VPN tunnel inside address**, so the tunnel itself is capping the MTU at **1422**.

**Why it produces this exact symptom:** the TCP 3-way handshake uses tiny packets → they pass → *TCP connect
succeeds*. The Oracle **logon** then exchanges larger packets; those exceed 1422, get dropped, and because
Path-MTU-Discovery is black-holing (the "fragmentation needed" ICMP isn't getting back to the sender to make
it shrink) the sender keeps retransmitting full-size packets that never arrive → the read hangs.

**Why it worked earlier the same day:** before the T2 failover the active path had working MSS clamping /
PMTUD. The failover shifted traffic onto the path that now black-holes large packets.

## Fix (durable — Mars infra: Viren / Jagananth)
1. **Clamp TCP MSS to ~1379** on the VPN path (1422 path-MTU − ~40 B headers; AWS's documented recommendation
   for Site-to-Site VPN). MSS clamping makes both ends size segments to fit the tunnel and sidesteps PMTUD
   entirely — the standard fix for exactly this.
2. Ensure **ICMP type 3 / code 4 ("fragmentation needed")** is allowed through SGs / NACLs / firewalls on both
   ends so PMTUD can self-heal.
3. Coordinate with the **Cubic / Azure** side of the tunnel since the Jun-23 failover touched it.

## Client-side band-aid (use until the tunnel is fixed)
Force a **small Oracle SDU** in the JDBC connect descriptor so TNS never emits a packet bigger than the
1422 MTU:

```python
url = ("jdbc:oracle:thin:@(DESCRIPTION=(SDU=512)"
       "(ADDRESS=(PROTOCOL=TCP)(HOST=10.3.10.30)(PORT=1521))"
       "(CONNECT_DATA=(SERVICE_NAME=<service>)))")
```

It's a workaround, not the fix — keep the MSS-clamp request open with infra.

## Triage checklist (next time something Oracle "hangs")
1. Run `oracle_connectivity_diagnostics.py` (bounded, ~25 s — never hangs).
2. **TCP fails** → tunnel down / routing → Mars infra.
3. **TCP OK + large-packet (DF ping) fails** → MTU black-hole → MSS clamp (this finding).
4. **TCP OK + large packets OK + login still hangs** → Oracle-side (down / session-limit / service) → Cubic DBA.
5. Always set **both** `oracle.net.CONNECT_TIMEOUT` and `oracle.jdbc.ReadTimeout` so a dead DB fails fast.
