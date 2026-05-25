
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""Hyper-V adapter — pull live VM inventory via PowerShell remoting.

Uses subprocess to invoke PowerShell (WinRM or local).  No third-party
dependencies.  Air-gap safe: unreachable hosts return empty list.

Canonical output schema matches mc_srv_inventory columns.
"""

import json
import socket
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = get_logger("icdev.migration_canvas.adapters.hyperv")

# PowerShell script that outputs VM inventory as JSON array.
_PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$vms = @()
try {
    $allVMs = Get-VM -ErrorAction Stop
    foreach ($vm in $allVMs) {
        $drives = Get-VMHardDiskDrive -VMName $vm.Name -ErrorAction SilentlyContinue
        $nics   = Get-VMNetworkAdapter -VMName $vm.Name -ErrorAction SilentlyContinue
        $totalDisk = 0
        $diskCount = 0
        foreach ($d in $drives) {
            $diskCount++
            if ($d.Path) {
                try {
                    $vhd = Get-VHD -Path $d.Path -ErrorAction SilentlyContinue
                    if ($vhd) { $totalDisk += [math]::Round($vhd.Size / 1GB, 1) }
                } catch {}
            }
        }
        $vms += [PSCustomObject]@{
            Name         = $vm.Name
            State        = $vm.State.ToString()
            CPUCount     = $vm.ProcessorCount
            MemoryGB     = [math]::Round($vm.MemoryAssigned / 1GB, 2)
            DiskCount    = $diskCount
            TotalDiskGB  = $totalDisk
            NicCount     = @($nics).Count
            Generation   = $vm.Generation
        }
    }
} catch {
    Write-Error "Get-VM failed: $_"
}
$vms | ConvertTo-Json -Depth 3
"""


def _run_ps_local() -> list[dict]:
    """Run PowerShell locally (on-host Hyper-V)."""
    with tempfile.NamedTemporaryFile(suffix=".ps1", mode="w", delete=False) as f:
        f.write(_PS_SCRIPT)
        ps_path = f.name
    try:
        result = subprocess.run(
            ["powershell", "-NonInteractive", "-NoProfile", "-File", ps_path],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            logger.warning("PowerShell error: %s", result.stderr[:500])
            return []
        out = result.stdout.strip()
        if not out:
            return []
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        logger.warning("Local PowerShell execution failed: %s", exc)
        return []
    finally:
        Path(ps_path).unlink(missing_ok=True)


def _run_ps_remote(host: str, user: str, password: str) -> list[dict]:
    """Run PowerShell against remote Hyper-V host via WinRM (Enter-PSSession equivalent)."""
    encoded_script = _PS_SCRIPT.replace('"', '\\"')
    remote_cmd = (
        f"$pw = ConvertTo-SecureString '{password}' -AsPlainText -Force; "
        f"$cred = New-Object System.Management.Automation.PSCredential('{user}', $pw); "
        f"Invoke-Command -ComputerName {host} -Credential $cred "
        f"-ScriptBlock {{ {encoded_script} }}"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NonInteractive", "-NoProfile", "-Command", remote_cmd],
            capture_output=True, text=True, timeout=90
        )
        if result.returncode != 0:
            logger.warning("Remote PowerShell error on %s: %s", host, result.stderr[:500])
            return []
        out = result.stdout.strip()
        if not out:
            return []
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        logger.warning("Remote PowerShell to %s failed: %s", host, exc)
        return []


def _os_family_from_gen(generation: int) -> str:
    return "windows"  # Hyper-V VMs are predominantly Windows; Linux also supported but indistinguishable from gen alone


def _normalize(raw: dict, host: str) -> dict:
    gen = raw.get("Generation", 1) or 1
    bios = "UEFI" if gen == 2 else "Legacy"
    return {
        "hostname": raw.get("Name", "unknown"),
        "vcpus": raw.get("CPUCount", 1) or 1,
        "ram_gb": raw.get("MemoryGB", 0) or 0,
        "disk_count": raw.get("DiskCount", 0) or 0,
        "total_disk_gb": raw.get("TotalDiskGB", 0) or 0,
        "disk_type": "VHD/VHDX",
        "nic_count": raw.get("NicCount", 1) or 1,
        "primary_nic_gbps": 1.0,
        "os_family": "windows",
        "os_name": "Windows (Hyper-V guest)",
        "os_arch": "x86_64",
        "bios_type": bios,
        "virtualization_ext": 1,
        "power_state": raw.get("State", ""),
        "hypervisor": "hyperv",
        "datacenter": host,
        "cluster": "",
        "source": "hyperv_live",
        "pulled_at": datetime.now(timezone.utc).isoformat(),
    }


def pull_inventory(host: str, user: str, password: str, **kwargs) -> list[dict]:
    """Pull live VM inventory from Hyper-V host.

    Args:
        host: Hyper-V hostname or IP.  Pass 'localhost' for local host.
        user: Windows username (DOMAIN\\user or user@domain).
        password: Windows password.

    Returns:
        List of canonical inventory dicts.  Empty list if host unreachable.
    """
    is_local = host.lower() in ("localhost", "127.0.0.1", "::1")

    if not is_local:
        try:
            socket.setdefaulttimeout(5)
            socket.getaddrinfo(host, 5985)  # WinRM HTTP port
        except OSError:
            logger.info("Hyper-V host %s not reachable on WinRM — returning empty", host)
            return []
        finally:
            socket.setdefaulttimeout(None)

    raw_vms = _run_ps_local() if is_local else _run_ps_remote(host, user, password)
    results = [_normalize(vm, host) for vm in raw_vms]
    logger.info("Hyper-V pull from %s: %d VMs", host, len(results))
    return results
