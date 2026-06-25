# Databricks notebook source
# Oracle connectivity ROOT-CAUSE diagnostics from the Databricks driver (inside the Mars VPC).
# All tests are bounded (short timeouts) so nothing hangs like the JDBC logon did.
import socket, time, struct, subprocess, json
HOST, PORT = "10.3.10.30", 1521
out = {}

def sh(cmd, t=20):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t).stdout.strip() or "(no stdout)"
    except Exception as e:
        return f"ERR {type(e).__name__}: {e}"

# --- 0. OUR-SIDE network context: which interface/route/MTU does the driver use to reach Oracle ---
out["route_to_oracle"] = sh(f"ip route get {HOST}")
out["iface_addrs"]     = sh("ip -o -4 addr show | awk '{print $2,$4}'")
out["iface_mtus"]      = sh("ip -o link show | awk -F'mtu ' '{print $1, $2}' | awk '{print $2,$3}'")

# --- 1. TCP connect to the Oracle listener (tunnel + routing + listener-accept) ---
def tcp(host, port, t=10):
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=t); s.close()
        return {"ok": True,  "secs": round(time.time()-t0, 2)}
    except Exception as e:
        return {"ok": False, "secs": round(time.time()-t0, 2), "err": f"{type(e).__name__}: {e}"}
out["tcp_oracle_1521"]   = tcp(HOST, PORT, 10)
out["tcp_egress_8888_53"] = tcp("8.8.8.8", 53, 5)   # sanity: does the driver have any egress at all

# --- 2. Does the Oracle LISTENER reply at all? (any bytes / RST = alive + return path OK; silence = black hole) ---
def tns_probe(host, port, service, t=8):
    cs = f"(DESCRIPTION=(CONNECT_DATA=(SERVICE_NAME={service})(CID=(PROGRAM=netdiag)(HOST=mars)(USER=diag)))(ADDRESS=(PROTOCOL=TCP)(HOST={host})(PORT={port})))"
    data = cs.encode()
    body = (struct.pack(">H", 0x0138) + struct.pack(">H", 0x012c) + struct.pack(">H", 0x0000)
            + struct.pack(">H", 0x2000) + struct.pack(">H", 0x7fff) + struct.pack(">H", 0x4f98)
            + struct.pack(">H", 0x0000) + struct.pack(">H", 0x0001) + struct.pack(">H", len(data))
            + struct.pack(">H", 0x003a) + struct.pack(">I", 0x00000000)
            + struct.pack(">BB", 0x41, 0x41) + struct.pack(">I", 0) + struct.pack(">I", 0)
            + struct.pack(">H", 0) + struct.pack(">H", 0) + struct.pack(">I", 0) + struct.pack(">I", 0))
    pkt = struct.pack(">H", 8+len(body)+len(data)) + struct.pack(">H", 0) + struct.pack(">BB", 1, 0) + struct.pack(">H", 0) + body + data
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=t); s.settimeout(t)
        s.sendall(pkt)
        resp = s.recv(2048); s.close()
        typ = resp[4] if len(resp) >= 5 else None
        names = {1:"CONNECT",2:"ACCEPT",4:"REFUSE",5:"REDIRECT",11:"RESEND",12:"MARKER"}
        return {"listener_replied": True, "bytes": len(resp), "tns_type": names.get(typ, typ), "secs": round(time.time()-t0, 2)}
    except ConnectionResetError as e:
        return {"listener_replied": True, "note": "RST (alive, return path OK)", "secs": round(time.time()-t0, 2)}
    except socket.timeout:
        return {"listener_replied": False, "err": "recv timeout = NO reply (black hole)", "secs": round(time.time()-t0, 2)}
    except Exception as e:
        return {"listener_replied": False, "err": f"{type(e).__name__}: {e}", "secs": round(time.time()-t0, 2)}
try:
    SVC = dbutils.secrets.get("cubic", "ods_service")
except Exception:
    SVC = "ORCL"
out["service_name_resolved"] = (SVC != "ORCL")
out["tns_listener_probe"] = tns_probe(HOST, PORT, SVC)

# --- 3. MTU / large-packet path test (prime suspect after a tunnel failover). ICMP may be filtered -> inconclusive if so ---
out["ping_small_100B"]      = sh(f"ping -c 2 -W 2 -s 100 {HOST}", 12)
out["ping_large_DF_1472B"]  = sh(f"ping -c 2 -W 2 -M do -s 1472 {HOST}", 12)
out["ping_mid_DF_1400B"]    = sh(f"ping -c 2 -W 2 -M do -s 1400 {HOST}", 12)
out["tracepath"]            = sh(f"tracepath -n {HOST} 2>&1 | head -15", 25)

print(json.dumps(out, indent=2, default=str))
dbutils.notebook.exit(json.dumps(out, default=str))
