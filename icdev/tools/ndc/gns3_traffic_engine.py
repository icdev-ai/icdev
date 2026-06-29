# CUI // SP-CTI
"""GNS3 DoD BCAP Lab — Traffic Engine.

Three phases:
  1. ZTP  — push RouterOS .rsc scripts and VPCS ip-config to all dod-bcap-lab nodes
  2. Traffic — run ping/traceroute matrix via GNS3 WebSocket console
  3. Collect — write real RTT / hop telemetry to network_canvas.db tables

All GNS3 REST calls go through DataBridge (GNS3Connector via GNS3Adapter).
WebSocket console is the only direct connection (DataBridge has no WS layer yet).

Usage:
  python tools/ndc/gns3_traffic_engine.py [--ztp] [--traffic] [--all]
  python tools/ndc/gns3_traffic_engine.py --all --json
  python tools/ndc/gns3_traffic_engine.py --traffic --json   # skip ZTP, re-run traffic

Canvas-agnostic class (importable by SDC, PDC, etc.):
  from tools.ndc.gns3_traffic_engine import GNS3TrafficEngine
  engine = GNS3TrafficEngine(
      server="http://localhost:3080",
      project="sdc-lab",
      ztp_dir="/path/to/ztp",
      traffic_matrix=[("NODE-A", "10.1.1.1", "Core ping", "s1")],
      trace_targets=[],
      db_table="sdc_live_flows",
  )
  result = engine.run(do_ztp=True, do_traffic=True)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

# NDC-specific defaults (used by the module-level run() entry point)
_SERVER      = "http://localhost:3080"
_PROJECT     = "dod-bcap-lab"
_ZTP_DIR     = _ROOT / "data" / "studio_artifacts" / "ndc" / "dod_lab" / "ztp"
_CONVERGENCE = 45   # seconds to wait after ZTP before running traffic

# ── IP plan (matches gns3_dod_topology_builder.py DEVICE_MAP) ─────────────────
# source node → list of (target_name, target_ip, label, scenario_tag)
_TRAFFIC_MATRIX = [
    # S1: NIPR user traversing BCAP to reach CSP proxy
    ("ONPREM-WORKSTA",  "10.30.1.1",   "OnPrem GW",            "s1"),
    ("ONPREM-WORKSTA",  "10.30.0.1",   "NIPR-CORE WAN",        "s1"),
    ("ONPREM-WORKSTA",  "10.20.0.2",   "BCAP-VDSS-FW",        "s1"),
    ("ONPREM-WORKSTA",  "10.20.1.2",   "BCAP-VDSS-IDS",       "s1"),
    ("ONPREM-WORKSTA",  "10.20.2.2",   "BCAP-VDMS",           "s1"),
    # S2: ISP health (router self-ping + adjacent hops)
    ("NIPR-EDGE-1",     "10.10.0.2",   "NIPR-CORE-RTR",        "s2"),
    ("NIPR-EDGE-1",     "10.20.0.2",   "BCAP-VDSS-FW",        "s2"),
    ("NIPR-EDGE-2",     "10.10.0.2",   "NIPR-CORE-RTR",        "s2"),
    # S3: BCAP IDS chain
    ("BCAP-VDSS-FW",    "10.20.1.2",   "BCAP-VDSS-IDS",       "s3"),
    ("BCAP-VDSS-IDS",   "10.20.2.2",   "BCAP-VDMS",           "s3"),
    # S4: JWICS cross-domain path
    ("ONPREM-SRV1",     "10.30.1.1",   "OnPrem GW",            "s4"),
    ("ONPREM-SRV1",     "10.40.0.2",   "JWICS-EDGE",           "s4"),
    ("JWICS-SRV1",      "192.168.100.1","JWICS GW",            "s4"),
    # S5: TCCM credential path
    ("MGMT-JUMPHOST",   "10.99.0.1",   "MGMT-CORE",            "s5"),
    ("BCAP-TCCM",       "10.20.3.1",   "VDMS-side GW",         "s5"),
    # S6: management / modernization
    ("MGMT-JUMPHOST",   "10.10.0.2",   "NIPR-CORE-RTR",        "s6"),
]

# MikroTik-only: run traceroute to these targets
_TRACE_TARGETS = [
    ("NIPR-EDGE-1",  "10.20.2.2",  "BCAP-VDMS end-to-end",  "s1"),
    ("ONPREM-DC1-RTR", "10.20.0.2", "BCAP-VDSS-FW",         "s3"),
]


# ── Telnet / IAC helpers ──────────────────────────────────────────────────────

_IAC = 255; _DONT = 254; _DO = 253; _WONT = 252; _WILL = 251; _SB = 250; _SE = 240


def _iac_responses(data: bytes) -> bytes:
    out: list[bytes] = []
    i = 0
    while i < len(data):
        b = data[i]
        if b == _IAC and i + 1 < len(data):
            cmd = data[i + 1]
            if cmd in (_DO, _DONT) and i + 2 < len(data):
                out.append(bytes([_IAC, _WONT, data[i + 2]])); i += 3
            elif cmd in (_WILL, _WONT) and i + 2 < len(data):
                out.append(bytes([_IAC, _DONT, data[i + 2]])); i += 3
            else:
                i += 2
        else:
            i += 1
    return b"".join(out)


def _strip_iac(data: bytes) -> bytes:
    out: list[int] = []
    i = 0
    while i < len(data):
        b = data[i]
        if b == _IAC and i + 1 < len(data):
            cmd = data[i + 1]
            if cmd == _SB:
                j = i + 2
                while j + 1 < len(data) and not (data[j] == _IAC and data[j + 1] == _SE):
                    j += 1
                i = j + 2
            else:
                i += 2 if cmd not in (_DO, _DONT, _WILL, _WONT) else 3
        else:
            out.append(b); i += 1
    return bytes(out)


# ── GNS3 node discovery via DataBridge ───────────────────────────────────────

def _discover(server: str, project_name: str) -> dict[str, dict]:
    """Return {node_name: {node_id, project_id, console_type, console}}."""
    from tools.network.adapters.gns3_adapter import GNS3Adapter
    g = GNS3Adapter(server)
    projects = g.list_projects()
    proj = next((p for p in projects if p.get("name") == project_name), None)
    if not proj:
        return {}
    pid = proj["project_id"]
    nodes = g._get_list(f"/v2/projects/{pid}/nodes")
    return {
        n["name"]: {
            "node_id":      n["node_id"],
            "project_id":   pid,
            "node_type":    n.get("node_type", ""),
            "console_type": n.get("console_type", "telnet"),
            "console":      n.get("console"),
        }
        for n in nodes
    }


# ── WebSocket console session ─────────────────────────────────────────────────

async def _ws_session(ws_url: str, node_name: str, node_type: str,
                      commands: list[str], capture_output: bool = False
                      ) -> dict:
    """Connect to a node console, authenticate if needed, run commands."""
    try:
        import websockets  # noqa: PLC0415
    except ImportError:
        return {"device": node_name, "status": "skip", "error": "websockets not installed",
                "output": [], "captured": [], "errors": []}

    output: list[str] = []
    captured: list[str] = []
    errors: list[str] = []
    is_vpcs = node_type == "vpcs"

    async def _recv_until(kw: bytes, timeout: float = 8.0) -> bytes:
        buf = b""
        dl = asyncio.get_event_loop().time() + timeout
        while True:
            rem = dl - asyncio.get_event_loop().time()
            if rem <= 0:
                break
            try:
                chunk = await asyncio.wait_for(ws.recv(), timeout=min(rem, 0.5))
            except asyncio.TimeoutError:
                if kw and kw in _strip_iac(buf):
                    break
                continue
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", errors="replace")
            reply = _iac_responses(chunk)
            if reply:
                await ws.send(reply)
            buf += chunk
            if kw and kw in _strip_iac(buf):
                break
        return _strip_iac(buf)

    async def _recv_eager(wait: float = 0.5) -> bytes:
        return await _recv_until(b"", timeout=wait)

    import os
    from dotenv import load_dotenv
    load_dotenv()
    _router_pw = os.getenv("GNS3_ROUTER_PASSWORD", "").encode("utf-8")

    try:
        async with websockets.connect(ws_url, open_timeout=10) as ws:
            await asyncio.sleep(0.3)
            banner_raw = await _recv_eager(2.0)
            banner = banner_raw.decode("utf-8", errors="replace")
            output.append(f"[banner] {banner.strip()[:150]}")

            if is_vpcs:
                await ws.send(b"\r\n")
                wake = (await _recv_eager(1.0)).decode("utf-8", errors="replace")
                output.append(f"[wake] {wake.strip()[:80]}")
            else:
                # MikroTik CHR — state-machine login: handles EULA, first-boot, and login
                _SHELL = ("] >", "]>")
                _LOGIN = ("Login:", "login:", "MikroTik Login:")
                _NEWPW = ("new password>", "new password> ")
                _EULA  = ("press Enter", "press any key", "q to abort", "q to quit")

                def _classify(s: str) -> str:
                    if any(p in s for p in _NEWPW):  return "firstboot"
                    if any(p in s for p in _LOGIN):   return "login"
                    if any(p in s for p in _SHELL):   return "shell"
                    if any(p in s for p in _EULA):    return "eula"
                    return "unknown"

                deadline = asyncio.get_event_loop().time() + 120
                ctx = banner
                state = _classify(ctx)
                output.append(f"[init-state] {state!r} banner={repr(ctx.strip()[:80])}")

                while state in ("unknown", "eula") and asyncio.get_event_loop().time() < deadline:
                    await ws.send(b"\r\n")
                    chunk = (await _recv_eager(2.0)).decode("utf-8", errors="replace")
                    ctx += chunk
                    state = _classify(ctx)
                    output.append(f"[state] {state!r} +{len(chunk)}b")
                    ctx = ctx[-500:]

                output.append(f"[final-state] {state!r}")

                if state == "firstboot":
                    pw = _router_pw or b"icdev1234"
                    output.append(f"[first-boot] Setting password {pw!r}")
                    await ws.send(pw + b"\r\n")
                    await asyncio.sleep(0.4)
                    rep = (await _recv_until(b"password>", timeout=10)).decode("utf-8", errors="replace")
                    output.append(f"[confirm-pw] {rep.strip()[:80]}")
                    await ws.send(pw + b"\r\n")
                    await asyncio.sleep(0.6)
                    _router_pw = pw
                    after = (await _recv_eager(3.0)).decode("utf-8", errors="replace")
                    ctx = after
                    state = _classify(after)
                    output.append(f"[post-firstboot-state] {state!r}")

                if state == "login":
                    await ws.send(b"admin\r\n")
                    await asyncio.sleep(0.3)
                    pw_prompt = (await _recv_until(b"Password:", timeout=6)).decode("utf-8", errors="replace")
                    if "Password:" in pw_prompt or "password:" in pw_prompt:
                        logged_in = False
                        pw_list = [b"", _router_pw] if _router_pw else [b""]
                        for attempt_pw in pw_list:
                            await ws.send(attempt_pw + b"\r\n")
                            await asyncio.sleep(0.5)
                            post = (await _recv_eager(3.0)).decode("utf-8", errors="replace")
                            output.append(f"[login pw={attempt_pw!r}] {post.strip()[:120]}")
                            if "Login failed" not in post and _classify(post) == "shell":
                                if attempt_pw == b"" and _router_pw:
                                    set_pw = f'/user set [find name=admin] password="{_router_pw.decode()}"'
                                    await ws.send((set_pw + "\r\n").encode("utf-8"))
                                    await asyncio.sleep(0.5)
                                    set_out = (await _recv_eager(1.5)).decode("utf-8", errors="replace")
                                    output.append(f"[set-pw] {set_out.strip()[:80]}")
                                logged_in = True
                                break
                            await ws.send(b"\r\n")
                            await asyncio.sleep(0.3)
                            re_buf = (await _recv_eager(2.0)).decode("utf-8", errors="replace")
                            if _classify(re_buf) == "login":
                                await ws.send(b"admin\r\n")
                                re2 = (await _recv_until(b"Password:", timeout=5)).decode("utf-8", errors="replace")
                                if "Password:" not in re2 and "password:" not in re2:
                                    break
                            elif "Password:" not in re_buf and "password:" not in re_buf:
                                break
                        if not logged_in:
                            return {"device": node_name, "status": "login_failed",
                                    "output": output, "captured": [], "errors": ["Login failed"]}

            for cmd in commands:
                cmd = cmd.strip()
                if not cmd or cmd.startswith("#"):
                    continue
                await ws.send((cmd + "\r\n").encode("utf-8"))
                wait = 6.0 if any(k in cmd for k in ("ping", "traceroute", "/ping", "/tool trace")) else 0.5
                raw = await _recv_eager(wait)
                line = raw.decode("utf-8", errors="replace")
                output.append(f"[{cmd[:40]}] => {line.strip()[:300]}")
                if capture_output and line.strip():
                    captured.append(line)
                if "bad command" in line.lower() or "syntax error" in line.lower():
                    errors.append(f"{cmd[:60]}: {line.strip()[:80]}")

            await ws.send(b"\r\n")
            tail = (await _recv_eager(0.5)).decode("utf-8", errors="replace")
            if tail.strip():
                output.append(f"[tail] {tail.strip()[:100]}")

    except ConnectionRefusedError:
        return {"device": node_name, "status": "unreachable",
                "output": output, "captured": [], "errors": [f"Refused: {ws_url}"]}
    except Exception as exc:
        return {"device": node_name, "status": "error",
                "output": output, "captured": [], "errors": [str(exc)]}

    return {
        "device":   node_name,
        "status":   "error" if errors else "success",
        "output":   output,
        "captured": captured,
        "errors":   errors,
    }


# ── Pure parsing helpers ──────────────────────────────────────────────────────

def _ping_cmd(node_type: str, target_ip: str, count: int = 5) -> str:
    if node_type == "vpcs":
        return f"ping {target_ip} -c {count}"
    return f"/ping address={target_ip} count={count}"


def _trace_cmd(node_type: str, target_ip: str) -> str:
    if node_type == "vpcs":
        return f"trace {target_ip}"
    return f"/tool traceroute address={target_ip} max-hops=10 count=3"


def _parse_rtt(output: str, node_type: str) -> list[float]:
    rtts: list[float] = []
    if node_type == "vpcs":
        for m in re.finditer(r"time=(\d+\.?\d*)\s*ms", output):
            rtts.append(float(m.group(1)))
    else:
        for m in re.finditer(r"avg-rtt=(\d+)ms", output):
            rtts.append(float(m.group(1)))
        if not rtts:
            for m in re.finditer(r"\b(\d+)ms\b", output):
                rtts.append(float(m.group(1)))
    return rtts


def _parse_hops(output: str) -> int:
    count = 0
    for line in output.splitlines():
        if re.match(r"\s*\d+\s+\d+\.\d+\.\d+\.\d+", line):
            count += 1
    return max(count, 1)


# ── Canvas-agnostic engine class ──────────────────────────────────────────────

class GNS3TrafficEngine:
    """Reusable GNS3 traffic engine — injectable config for any canvas/lab.

    NDC defaults are pre-wired; other canvases supply their own server, project,
    ztp_dir, traffic_matrix, trace_targets, and db_table at construction time.
    """

    def __init__(
        self,
        server: str = _SERVER,
        project: str = _PROJECT,
        ztp_dir: "Path | str" = _ZTP_DIR,
        traffic_matrix: list = _TRAFFIC_MATRIX,
        trace_targets: list = _TRACE_TARGETS,
        db_table: str = "ndc_live_flows",
        convergence_s: int = _CONVERGENCE,
    ):
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', db_table):
            raise ValueError(f"Invalid db_table name: {db_table!r}")
        self.server         = server
        self.project        = project
        self.ztp_dir        = Path(ztp_dir)
        self.traffic_matrix = traffic_matrix
        self.trace_targets  = trace_targets
        self.db_table       = db_table
        self.convergence_s  = convergence_s

    # ── Discovery ─────────────────────────────────────────────────────────────

    def discover(self) -> dict[str, dict]:
        return _discover(self.server, self.project)

    # ── ZTP phase ─────────────────────────────────────────────────────────────

    def _load_ztp(self, node_name: str, node_type: str) -> list[str]:
        if node_type == "vpcs":
            f = self.ztp_dir / f"vpcs_{node_name}.txt"
            if f.exists():
                return [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()
                        if ln.strip() and not ln.startswith("#")]
            return []
        f = self.ztp_dir / f"{node_name}.rsc"
        if f.exists():
            return f.read_text(encoding="utf-8").splitlines()
        return []

    async def _ztp_all(self, nodes: dict[str, dict]) -> dict:
        ws_base = self.server.replace("http://", "ws://").replace("https://", "wss://")
        tasks = []
        meta = []
        for name, info in nodes.items():
            if info.get("console_type") not in ("telnet",):
                continue
            if not info.get("console"):
                continue
            cmds = self._load_ztp(name, info["node_type"])
            if not cmds:
                continue
            pid = info["project_id"]
            nid = info["node_id"]
            ws_url = f"{ws_base}/v2/projects/{pid}/nodes/{nid}/console/ws"
            tasks.append(_ws_session(ws_url, name, info["node_type"], cmds))
            meta.append(name)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        out = {}
        for name, r in zip(meta, results):
            if isinstance(r, Exception):
                out[name] = {"status": "error", "error": str(r)}
            else:
                out[name] = r
        return out

    # ── Traffic phase ─────────────────────────────────────────────────────────

    async def _traffic_all(self, nodes: dict[str, dict]) -> list[dict]:
        ws_base = self.server.replace("http://", "ws://").replace("https://", "wss://")
        ping_tasks: list = []
        ping_meta:  list = []

        for src_name, tgt_ip, label, scenario in self.traffic_matrix:
            info = nodes.get(src_name)
            if not info or not info.get("console") or info.get("console_type") != "telnet":
                continue
            pid = info["project_id"]
            nid = info["node_id"]
            ws_url = f"{ws_base}/v2/projects/{pid}/nodes/{nid}/console/ws"
            cmd = _ping_cmd(info["node_type"], tgt_ip, count=5)
            ping_tasks.append(_ws_session(ws_url, src_name, info["node_type"],
                                          [cmd], capture_output=True))
            ping_meta.append((src_name, tgt_ip, label, scenario, info["node_type"]))

        for src_name, tgt_ip, label, scenario in self.trace_targets:
            info = nodes.get(src_name)
            if not info or not info.get("console") or info.get("console_type") != "telnet":
                continue
            pid = info["project_id"]
            nid = info["node_id"]
            ws_url = f"{ws_base}/v2/projects/{pid}/nodes/{nid}/console/ws"
            cmd = _trace_cmd(info["node_type"], tgt_ip)
            ping_tasks.append(_ws_session(ws_url, src_name, info["node_type"],
                                          [cmd], capture_output=True))
            ping_meta.append((src_name, tgt_ip, f"trace:{label}", scenario, info["node_type"]))

        raw = await asyncio.gather(*ping_tasks, return_exceptions=True)

        flows = []
        ts = datetime.now(timezone.utc).isoformat()
        for meta_item, result in zip(ping_meta, raw):
            src_name, tgt_ip, label, scenario, ntype = meta_item
            if isinstance(result, Exception):
                flows.append({"src": src_name, "dst": tgt_ip, "label": label,
                              "scenario": scenario, "status": "error", "error": str(result)})
                continue

            combined = "\n".join(result.get("captured", []) + result.get("output", []))
            rtts = _parse_rtt(combined, ntype)
            is_trace = label.startswith("trace:")
            hops = _parse_hops(combined) if is_trace else None

            reachable = bool(rtts) or ("bytes from" in combined) or ("avg-rtt" in combined)
            avg_rtt = sum(rtts) / len(rtts) if rtts else None
            loss = 0 if reachable else 100

            flows.append({
                "src":        src_name,
                "dst_ip":     tgt_ip,
                "label":      label.replace("trace:", ""),
                "scenario":   scenario,
                "is_trace":   is_trace,
                "reachable":  reachable,
                "avg_rtt_ms": round(avg_rtt, 2) if avg_rtt is not None else None,
                "hops":       hops,
                "loss_pct":   loss,
                "rtts":       rtts,
                "raw_output": combined[:500],
                "status":     result.get("status", "unknown"),
                "timestamp":  ts,
            })

        return flows

    # ── Telemetry storage ─────────────────────────────────────────────────────

    def _store_flows(self, flows: list[dict]) -> int:
        try:
            from tools.network.db.init_db import get_connection
            conn = get_connection()
        except Exception:
            import sqlite3
            conn = sqlite3.connect(str(_ROOT / "data" / "network_canvas.db"))

        ts = datetime.now(timezone.utc).isoformat()
        written = 0

        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.db_table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collected_at TEXT,
                src TEXT, dst_ip TEXT, label TEXT, scenario TEXT,
                reachable INTEGER, avg_rtt_ms REAL, loss_pct REAL, hops INTEGER,
                raw_output TEXT
            )
        """)

        isp_rows = [f for f in flows if f.get("scenario") == "s2" and not f.get("is_trace")]

        for f in flows:
            conn.execute(f"""
                INSERT INTO {self.db_table}
                (collected_at, src, dst_ip, label, scenario, reachable, avg_rtt_ms, loss_pct, hops, raw_output)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (ts, f["src"], f.get("dst_ip", f.get("dst", "")), f["label"], f["scenario"],
                  1 if f.get("reachable") else 0,
                  f.get("avg_rtt_ms"), f.get("loss_pct", 0),
                  f.get("hops"), f.get("raw_output", "")[:400]))
            written += 1

        if isp_rows:
            for row in isp_rows:
                if row.get("avg_rtt_ms") is not None:
                    isp_name = ("ATT" if "EDGE-1" in row["src"] and "NIPR-CORE" in row.get("label", "")
                                else "LUMEN")
                    try:
                        conn.execute("""
                            UPDATE ndc_isp_telemetry
                            SET latency_ms=%s, loss_pct=%s
                            WHERE isp=%s AND timestamp=(SELECT MAX(timestamp) FROM ndc_isp_telemetry WHERE isp=%s)
                        """, (row["avg_rtt_ms"], row["loss_pct"], isp_name, isp_name))
                    except Exception:
                        pass

        conn.commit()
        conn.close()
        return written

    # ── Orchestrator ──────────────────────────────────────────────────────────

    def run(self, do_ztp: bool = True, do_traffic: bool = True,
            wait_s: "int | None" = None) -> dict:
        if wait_s is None:
            wait_s = self.convergence_s

        result: dict = {"server": self.server, "project": self.project, "phases": {}}

        nodes = self.discover()
        if not nodes:
            return {"status": "error",
                    "error": f"No nodes found in {self.project!r} at {self.server}"}
        result["node_count"] = len(nodes)

        if do_ztp:
            t0 = time.monotonic()
            ztp_results = asyncio.run(self._ztp_all(nodes))
            pushed  = sum(1 for r in ztp_results.values() if r.get("status") == "success")
            failed  = sum(1 for r in ztp_results.values() if r.get("status") == "error")
            skipped = len(nodes) - len(ztp_results)
            result["phases"]["ztp"] = {
                "pushed": pushed, "failed": failed, "skipped": skipped,
                "duration_s": round(time.monotonic() - t0, 1),
                "devices": {k: {"status": v.get("status"), "errors": v.get("errors", [])}
                            for k, v in ztp_results.items()},
            }

            if do_traffic:
                print(f"[traffic-engine] ZTP done. Waiting {wait_s}s for BGP convergence...",
                      flush=True, file=sys.stderr)
                time.sleep(wait_s)

        if do_traffic:
            t0 = time.monotonic()
            flows = asyncio.run(self._traffic_all(nodes))
            reachable = sum(1 for f in flows if f.get("reachable"))
            stored = self._store_flows(flows)
            result["phases"]["traffic"] = {
                "flows_tested": len(flows),
                "reachable":    reachable,
                "unreachable":  len(flows) - reachable,
                "stored_to_db": stored,
                "duration_s":   round(time.monotonic() - t0, 1),
                "flows":        flows,
            }

        result["status"] = "success"
        return result


# ── Module-level entry point (NDC defaults, backward-compatible) ──────────────

def run(server: str = _SERVER, do_ztp: bool = True, do_traffic: bool = True,
        wait_s: int = _CONVERGENCE) -> dict:
    return GNS3TrafficEngine(server=server).run(
        do_ztp=do_ztp, do_traffic=do_traffic, wait_s=wait_s)


def main() -> None:
    p = argparse.ArgumentParser(description="GNS3 DoD BCAP Traffic Engine")
    p.add_argument("--server",  default=_SERVER)
    p.add_argument("--ztp",     action="store_true", help="Push ZTP configs")
    p.add_argument("--traffic", action="store_true", help="Run traffic matrix")
    p.add_argument("--all",     action="store_true", help="ZTP + traffic")
    p.add_argument("--wait",    type=int, default=_CONVERGENCE,
                   help="Seconds to wait for convergence after ZTP")
    p.add_argument("--json",    action="store_true")
    args = p.parse_args()

    do_ztp     = args.ztp or args.all
    do_traffic = args.traffic or args.all

    if not do_ztp and not do_traffic:
        p.print_help(); sys.exit(0)

    out = run(args.server, do_ztp=do_ztp, do_traffic=do_traffic, wait_s=args.wait)

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        phases = out.get("phases", {})
        if "ztp" in phases:
            z = phases["ztp"]
            print(f"\nZTP: pushed={z['pushed']}  failed={z['failed']}  skipped={z['skipped']}  "
                  f"({z['duration_s']}s)")
            for dev, r in z["devices"].items():
                icon = "OK" if r["status"] == "success" else r["status"].upper()
                print(f"  [{icon}] {dev}")

        if "traffic" in phases:
            t = phases["traffic"]
            print(f"\nTraffic: {t['reachable']}/{t['flows_tested']} reachable  "
                  f"({t['duration_s']}s)  stored={t['stored_to_db']} rows")
            for f in t["flows"]:
                icon = "OK  " if f["reachable"] else "FAIL"
                rtt = f"{f['avg_rtt_ms']:.1f}ms" if f.get("avg_rtt_ms") is not None else "N/A"
                hops = f"  hops={f['hops']}" if f.get("hops") else ""
                print(f"  [{icon}] {f['src']:<22} -> {f['dst_ip']:<18} {f['label']:<28} rtt={rtt}{hops}")


if __name__ == "__main__":
    main()
