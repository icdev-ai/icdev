#!/usr/bin/env python3
# CUI // SP-CTI
"""Fabric ``peer`` CLI transport (D-GC-1).

``args/blockchain_config.yaml`` has declared ``fabric.cli_path: peer`` and
``cli_timeout_seconds: 60`` under the comment "Fabric CLI via subprocess (same
as SAST wrapping bandit)" since GovChain shipped. There was no subprocess call
anywhere in ``tools/blockchain/``. This is that transport.

Why the CLI rather than the SDK: ``hfc``/fabric-sdk-py is in neither
``requirements.txt`` nor ``pyproject.toml``, so it can never be assumed present.
The ``peer`` binary is what a Fabric deployment actually ships, it needs no
Python dependency, and it is the same "wrap the vendor binary" shape ICDEV
already uses for bandit and git.

What ``health()`` proves, exactly
--------------------------------
That the configured binary exists on PATH and answers ``peer version``. It does
NOT prove an orderer will accept a transaction — that requires MSP material and
a live network, and probing it would mean submitting a transaction on every
health check. A healthy probe therefore means "worth trying"; a write that then
fails returns ``status='failed'`` so the caller queues it. The failure mode we
refuse is the silent one, not the retry.

Security posture: argv form, ``shell=False``, a fixed subcommand vector, and a
bounded timeout. Chaincode arguments are JSON-encoded into a single ``-c``
operand, so they cannot become additional argv entries. See
``docs/security/sandbox-coverage.md`` (Gap 9).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 — fixed 'peer' argv, shell=False, bounded timeout
import time
from typing import Any

from tools.blockchain.transports.base import (
    STATUS_DEGRADED,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    STATUS_UNREACHABLE,
    AnchorTransport,
    TransportHealth,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger("blockchain.transport.peer_cli")

#: Fabric logs the transaction id in several shapes across versions; all of
#: them put a hex digest after the literal "txid".
_TXID_RE = re.compile(r"txid[\s:=]*\[?([0-9a-fA-F]{16,64})\]?", re.IGNORECASE)

#: Health probe caps its own timeout — `peer version` is local and instant, and
#: a 60s wait for it would stall every is_enabled() call.
_HEALTH_TIMEOUT_CAP = 15


class PeerCliTransport(AnchorTransport):
    """Anchors via ``peer chaincode invoke`` / ``peer chaincode query``.

    One instance per peer endpoint. Registering several with distinct
    ``priority`` values is how peer failover works: the registry tries the
    lowest-priority healthy endpoint and moves on when it is unreachable.
    """

    backend = "peer_cli"

    def __init__(
        self,
        cli_path: str = "peer",
        timeout_seconds: int = 60,
        peer_address: str | None = None,
        orderer: str | None = None,
        tls: dict | None = None,
        priority: int = 20,
        name: str | None = None,
        wait_for_event: bool = True,
        env: dict | None = None,
    ) -> None:
        self.cli_path = cli_path or "peer"
        self.timeout_seconds = int(timeout_seconds or 60)
        self.peer_address = peer_address
        self.orderer = orderer
        self.tls = tls or {}
        self.priority = priority
        self.wait_for_event = wait_for_event
        self.extra_env = env or {}
        self.name = name or (
            f"peer_cli:{peer_address}" if peer_address else "peer_cli"
        )

    # -- health --------------------------------------------------------------

    def health(self) -> TransportHealth:
        resolved = shutil.which(self.cli_path)
        if resolved is None:
            # Cheap and decisive — no subprocess spawned on the common
            # "no Fabric installed" path, which is every CI run.
            return self._health(
                STATUS_UNAVAILABLE,
                f"Fabric CLI {self.cli_path!r} is not on PATH",
            )

        started = time.monotonic()
        try:
            proc = self._run([resolved, "version"], timeout=min(self.timeout_seconds, _HEALTH_TIMEOUT_CAP))
        except subprocess.TimeoutExpired:
            return self._health(
                STATUS_UNREACHABLE,
                f"{self.cli_path} version timed out",
                latency_ms=(time.monotonic() - started) * 1000,
            )
        except Exception as exc:  # noqa: BLE001 — health() must never raise
            return self._health(STATUS_UNREACHABLE, f"{self.cli_path} version failed: {exc}")

        latency_ms = (time.monotonic() - started) * 1000
        if proc.returncode != 0:
            return self._health(
                STATUS_UNREACHABLE,
                f"{self.cli_path} version exited {proc.returncode}: "
                f"{(proc.stderr or '').strip()[:200]}",
                latency_ms=latency_ms,
            )

        version = (proc.stdout or "").strip().splitlines()
        detail = version[0][:120] if version else "peer CLI responded"
        if not self.orderer:
            # Invokes need an orderer; queries do not. Say so rather than
            # reporting a clean bill of health we cannot back.
            return self._health(
                STATUS_DEGRADED,
                f"{detail} — no orderer configured, invokes will fail",
                latency_ms=latency_ms,
            )
        return self._health(STATUS_OK, detail, latency_ms=latency_ms)

    # -- chaincode -----------------------------------------------------------

    def chaincode_invoke(
        self,
        channel: str,
        chaincode: str,
        fcn: str,
        args: list,
        **kwargs: Any,
    ) -> dict:
        argv = self._base_argv("invoke", channel, chaincode, fcn, args)
        if self.orderer:
            argv += ["-o", self.orderer]
        if self.wait_for_event:
            argv.append("--waitForEvent")
        argv += self._tls_argv()

        try:
            proc = self._run(argv, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            return self._failed(f"peer chaincode invoke timed out after {self.timeout_seconds}s")
        except FileNotFoundError:
            return self._failed(f"Fabric CLI {self.cli_path!r} is not on PATH")
        except Exception as exc:  # noqa: BLE001 — surface, never raise into the anchor path
            return self._failed(f"peer chaincode invoke failed: {exc}")

        combined = f"{proc.stdout or ''}\n{proc.stderr or ''}"
        if proc.returncode != 0:
            logger.warning(
                "peer chaincode invoke %s:%s exited %s", chaincode, fcn, proc.returncode
            )
            return self._failed(
                f"peer chaincode invoke exited {proc.returncode}: {combined.strip()[:300]}",
                returncode=proc.returncode,
            )

        tx_id = self._parse_tx_id(combined)
        if tx_id is None:
            logger.warning(
                "peer chaincode invoke %s:%s succeeded but reported no txid", chaincode, fcn
            )
        return self._anchored(tx_id, channel=channel, chaincode=chaincode, fcn=fcn)

    def chaincode_query(
        self,
        channel: str,
        chaincode: str,
        fcn: str,
        args: list,
        **kwargs: Any,
    ) -> dict:
        argv = self._base_argv("query", channel, chaincode, fcn, args) + self._tls_argv()

        try:
            proc = self._run(argv, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            return {"status": "failed", "result": None, "transport": self.name,
                    "reason": f"peer chaincode query timed out after {self.timeout_seconds}s"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "result": None, "transport": self.name,
                    "reason": f"peer chaincode query failed: {exc}"}

        if proc.returncode != 0:
            return {
                "status": "failed",
                "result": None,
                "transport": self.name,
                "reason": f"peer chaincode query exited {proc.returncode}: "
                          f"{(proc.stderr or '').strip()[:300]}",
            }

        raw = (proc.stdout or "").strip()
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = raw
        return {"status": "ok", "result": parsed, "transport": self.name}

    # -- internals -----------------------------------------------------------

    def _base_argv(self, verb: str, channel: str, chaincode: str, fcn: str, args: list) -> list:
        payload = json.dumps({"Args": [str(fcn)] + [str(a) for a in (args or [])]})
        return [
            self.cli_path,
            "chaincode",
            verb,
            "-C", str(channel),
            "-n", str(chaincode),
            "-c", payload,
        ]

    def _tls_argv(self) -> list:
        if not self.tls.get("enabled"):
            return []
        argv = ["--tls"]
        cafile = self.tls.get("cafile") or self.tls.get("ca_file")
        if cafile:
            argv += ["--cafile", str(cafile)]
        return argv

    def _env(self) -> dict:
        env = dict(os.environ)
        if self.peer_address:
            env["CORE_PEER_ADDRESS"] = self.peer_address
        env.update({str(k): str(v) for k, v in self.extra_env.items()})
        return env

    def _run(self, argv: list, timeout: int) -> "subprocess.CompletedProcess":
        return subprocess.run(  # nosec B603 — argv list, shell=False, fixed 'peer' subcommands
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            env=self._env(),
            encoding="utf-8",
            errors="replace",
        )

    @staticmethod
    def _parse_tx_id(output: str) -> str | None:
        match = _TXID_RE.search(output or "")
        return match.group(1) if match else None
