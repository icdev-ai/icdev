#!/usr/bin/env python3
# CUI // SP-CTI
"""SFTP sync provider — paramiko primary, subprocess ssh/scp fallback (D-SYNC-1).

Pattern: tools/cloud/storage_provider.py graceful import pattern.
Graceful degradation: paramiko → subprocess ssh/scp → unavailable.
"""

import os
import stat
import subprocess
from pathlib import PurePosixPath
from typing import Dict, List, Optional

from tools.filesync.providers.base import SyncTargetProvider

# Graceful paramiko import
try:
    import paramiko

    _HAS_PARAMIKO = True
except ImportError:
    paramiko = None
    _HAS_PARAMIKO = False


class SFTPSyncProvider(SyncTargetProvider):
    """SFTP file sync provider.

    Uses paramiko when available, falls back to subprocess ssh/scp.
    Config via constructor args or env vars:
        ICDEV_SFTP_HOST, ICDEV_SFTP_PORT, ICDEV_SFTP_USER, ICDEV_SFTP_KEY
    """

    def __init__(
        self,
        host: str = "",
        port: int = 22,
        username: str = "",
        key_file: str = "",
        password: str = "",
        known_hosts_check: bool = True,
        timeout: int = 30,
    ):
        self._host = host or os.environ.get("ICDEV_SFTP_HOST", "")
        self._port = port or int(os.environ.get("ICDEV_SFTP_PORT", "22"))
        self._username = username or os.environ.get("ICDEV_SFTP_USER", "")
        self._key_file = key_file or os.environ.get("ICDEV_SFTP_KEY", "")
        self._password = password
        self._known_hosts_check = known_hosts_check
        self._timeout = timeout
        self._client: Optional[object] = None
        self._sftp: Optional[object] = None

    @property
    def provider_name(self) -> str:
        return "sftp"

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _connect_paramiko(self):
        """Establish paramiko SFTP connection."""
        if not _HAS_PARAMIKO:
            return False
        try:
            client = paramiko.SSHClient()
            if self._known_hosts_check:
                client.load_system_host_keys()
                client.set_missing_host_key_policy(paramiko.RejectPolicy())
            else:
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # nosec B507 — user opted into auto-trust via config

            connect_kwargs = {
                "hostname": self._host,
                "port": self._port,
                "username": self._username,
                "timeout": self._timeout,
            }
            if self._key_file and os.path.exists(self._key_file):
                connect_kwargs["key_filename"] = self._key_file
            elif self._password:
                connect_kwargs["password"] = self._password

            client.connect(**connect_kwargs)
            self._client = client
            self._sftp = client.open_sftp()
            return True
        except Exception:
            self._client = None
            self._sftp = None
            return False

    def _ensure_connected(self) -> bool:
        """Ensure SFTP connection is active."""
        if self._sftp is not None:
            try:
                self._sftp.stat(".")
                return True
            except Exception:
                self._close()

        return self._connect_paramiko()

    def _close(self):
        """Close SFTP connection."""
        try:
            if self._sftp:
                self._sftp.close()
        except Exception:
            pass
        try:
            if self._client:
                self._client.close()
        except Exception:
            pass
        self._sftp = None
        self._client = None

    # ------------------------------------------------------------------
    # Subprocess fallback helpers
    # ------------------------------------------------------------------

    def _ssh_cmd_base(self) -> List[str]:
        """Build base ssh command args."""
        cmd = ["ssh", "-p", str(self._port)]
        if self._key_file and os.path.exists(self._key_file):
            cmd.extend(["-i", self._key_file])
        if not self._known_hosts_check:
            cmd.extend(["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"])
        cmd.extend(["-o", f"ConnectTimeout={self._timeout}"])
        return cmd

    def _ssh_target(self) -> str:
        """Build user@host string."""
        if self._username:
            return f"{self._username}@{self._host}"
        return self._host

    def _run_ssh(self, remote_cmd: str) -> Optional[str]:
        """Run a command on the remote host via subprocess ssh."""
        cmd = self._ssh_cmd_base() + [self._ssh_target(), remote_cmd]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self._timeout, stdin=subprocess.DEVNULL
            )
            if result.returncode == 0:
                return result.stdout
            return None
        except Exception:
            return None

    def _scp_download(self, remote_path: str) -> Optional[bytes]:
        """Download a file via scp subprocess."""
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
        try:
            cmd = ["scp", "-P", str(self._port)]
            if self._key_file and os.path.exists(self._key_file):
                cmd.extend(["-i", self._key_file])
            if not self._known_hosts_check:
                cmd.extend(["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"])
            cmd.append(f"{self._ssh_target()}:{remote_path}")
            cmd.append(tmp_path)
            result = subprocess.run(cmd, capture_output=True, timeout=self._timeout, stdin=subprocess.DEVNULL)
            if result.returncode == 0:
                with open(tmp_path, "rb") as f:
                    return f.read()
            return None
        except Exception:
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _scp_upload(self, remote_path: str, data: bytes) -> bool:
        """Upload a file via scp subprocess."""
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            cmd = ["scp", "-P", str(self._port)]
            if self._key_file and os.path.exists(self._key_file):
                cmd.extend(["-i", self._key_file])
            if not self._known_hosts_check:
                cmd.extend(["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"])
            cmd.append(tmp_path)
            cmd.append(f"{self._ssh_target()}:{remote_path}")
            result = subprocess.run(cmd, capture_output=True, timeout=self._timeout, stdin=subprocess.DEVNULL)
            return result.returncode == 0
        except Exception:
            return False
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # SyncTargetProvider interface
    # ------------------------------------------------------------------

    def list_files(self, base_path: str) -> List[Dict]:
        """List files recursively via SFTP or ssh find."""
        # Try paramiko first
        if self._ensure_connected():
            return self._list_files_paramiko(base_path)

        # Subprocess fallback: ssh find
        output = self._run_ssh(f'find "{base_path}" -type f -printf "%P\\t%s\\t%T@\\n"')
        if output is None:
            return []

        files = []
        for line in output.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                rel = parts[0].replace("\\", "/")
                try:
                    size = int(parts[1])
                    mtime = float(parts[2])
                except (ValueError, IndexError):
                    continue
                files.append(
                    {
                        "relative_path": rel,
                        "size": size,
                        "mtime_epoch": mtime,
                    }
                )
        return files

    def _list_files_paramiko(self, base_path: str) -> List[Dict]:
        """Recursively list files via paramiko SFTP."""
        files = []
        dirs_to_scan = [""]

        while dirs_to_scan:
            current = dirs_to_scan.pop()
            full_dir = str(PurePosixPath(base_path) / current) if current else base_path
            try:
                entries = self._sftp.listdir_attr(full_dir)
            except Exception:
                continue

            for entry in entries:
                rel = f"{current}/{entry.filename}" if current else entry.filename
                if stat.S_ISDIR(entry.st_mode):
                    dirs_to_scan.append(rel)
                elif stat.S_ISREG(entry.st_mode):
                    files.append(
                        {
                            "relative_path": rel.replace("\\", "/"),
                            "size": entry.st_size,
                            "mtime_epoch": float(entry.st_mtime),
                        }
                    )
        return files

    def read_file(self, base_path: str, relative_path: str) -> Optional[bytes]:
        """Read file via SFTP or scp."""
        remote = str(PurePosixPath(base_path) / relative_path)

        if self._ensure_connected():
            try:
                with self._sftp.open(remote, "rb") as f:
                    return f.read()
            except Exception:
                return None

        return self._scp_download(remote)

    def write_file(self, base_path: str, relative_path: str, data: bytes) -> bool:
        """Write file via SFTP or scp."""
        remote = str(PurePosixPath(base_path) / relative_path)
        parent = str(PurePosixPath(remote).parent)

        if self._ensure_connected():
            try:
                # Ensure parent dirs exist
                self._mkdir_recursive(parent)
                with self._sftp.open(remote, "wb") as f:
                    f.write(data)
                return True
            except Exception:
                return False

        # Subprocess fallback: mkdir -p + scp
        self._run_ssh(f'mkdir -p "{parent}"')
        return self._scp_upload(remote, data)

    def delete_file(self, base_path: str, relative_path: str) -> bool:
        """Delete file via SFTP or ssh rm."""
        remote = str(PurePosixPath(base_path) / relative_path)

        if self._ensure_connected():
            try:
                self._sftp.remove(remote)
                return True
            except Exception:
                return False

        result = self._run_ssh(f'rm -f "{remote}"')
        return result is not None

    def get_file_info(self, base_path: str, relative_path: str) -> Optional[Dict]:
        """Get file info via SFTP or ssh stat."""
        remote = str(PurePosixPath(base_path) / relative_path)

        if self._ensure_connected():
            try:
                st = self._sftp.stat(remote)
                return {
                    "size": st.st_size,
                    "mtime_epoch": float(st.st_mtime),
                }
            except Exception:
                return None

        output = self._run_ssh(f'stat -c "%s %Y" "{remote}"')
        if output:
            parts = output.strip().split()
            if len(parts) >= 2:
                try:
                    return {
                        "size": int(parts[0]),
                        "mtime_epoch": float(parts[1]),
                    }
                except ValueError:
                    pass
        return None

    def check_availability(self) -> bool:
        """Check if SFTP host is reachable."""
        if not self._host:
            return False

        if self._ensure_connected():
            return True

        # Subprocess fallback: ssh echo
        result = self._run_ssh("echo ok")
        return result is not None and "ok" in result

    def rename_file(self, base_path: str, old_path: str, new_path: str) -> bool:
        """Rename via SFTP posix-rename or fallback."""
        old_remote = str(PurePosixPath(base_path) / old_path)
        new_remote = str(PurePosixPath(base_path) / new_path)
        new_parent = str(PurePosixPath(new_remote).parent)

        if self._ensure_connected():
            try:
                self._mkdir_recursive(new_parent)
                self._sftp.posix_rename(old_remote, new_remote)
                return True
            except Exception:
                try:
                    self._sftp.rename(old_remote, new_remote)
                    return True
                except Exception:
                    pass

        # Subprocess fallback: ssh mv
        self._run_ssh(f'mkdir -p "{new_parent}"')
        result = self._run_ssh(f'mv "{old_remote}" "{new_remote}"')
        return result is not None

    def mkdir(self, base_path: str, relative_dir: str) -> bool:
        """Create directory on remote."""
        remote = str(PurePosixPath(base_path) / relative_dir)

        if self._ensure_connected():
            return self._mkdir_recursive(remote)

        result = self._run_ssh(f'mkdir -p "{remote}"')
        return result is not None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _mkdir_recursive(self, remote_path: str) -> bool:
        """Create remote directory tree via paramiko SFTP."""
        parts = PurePosixPath(remote_path).parts
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else part
            if not current.startswith("/"):
                current = f"/{current}" if remote_path.startswith("/") else current
            try:
                self._sftp.stat(current)
            except FileNotFoundError:
                try:
                    self._sftp.mkdir(current)
                except Exception:
                    pass
            except Exception:
                pass
        return True
