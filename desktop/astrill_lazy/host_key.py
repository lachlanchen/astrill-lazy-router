from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from .subprocess_support import background_process_options
from .windows_ssh_setup import HOST_KEY_TYPES, WindowsHostKey

FINGERPRINT_RE = re.compile(r"\b(SHA256:[A-Za-z0-9+/=]+)")


def inspect_host_key(
    host: str,
    port: int,
    *,
    known_hosts_path: Path | None = None,
    timeout: int = 8,
) -> WindowsHostKey:
    """Inspect an OpenSSH host key on Unix-like desktop platforms."""

    if not 1 <= port <= 65535:
        raise ValueError("SSH port must be between 1 and 65535")
    bounded_timeout = max(1, min(timeout, 30))
    scan = subprocess.run(
        [
            "ssh-keyscan",
            "-T",
            str(bounded_timeout),
            "-p",
            str(port),
            host,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=bounded_timeout + 3,
        **background_process_options(),
    )
    candidates = _parse_scanned_keys(scan.stdout)
    if not candidates:
        message = scan.stderr.strip()
        raise RuntimeError(message or "the router did not return an SSH host key")
    key_type, key_base64 = min(
        candidates,
        key=lambda item: (
            HOST_KEY_TYPES.index(item[0])
            if item[0] in HOST_KEY_TYPES
            else len(HOST_KEY_TYPES)
        ),
    )
    lookup_name = host if port == 22 else f"[{host}]:{port}"
    normalized_line = f"{lookup_name} {key_type} {key_base64}"
    fingerprint = _fingerprint(normalized_line)
    path = known_hosts_path or Path.home() / ".ssh" / "known_hosts"
    known = _known_host_keys(lookup_name, path)
    same_type = [value for kind, value in known if kind == key_type]
    if key_base64 in same_type:
        trust_state = "trusted"
    elif same_type:
        trust_state = "changed"
    elif known:
        trust_state = "additional"
    else:
        trust_state = "unknown"
    return WindowsHostKey(
        host=host,
        port=port,
        key_type=key_type,
        key_base64=key_base64,
        fingerprint=fingerprint,
        trust_state=trust_state,
        known_hosts_path=path,
    )


def trust_host_key(expected: WindowsHostKey) -> WindowsHostKey:
    """Persist a confirmed key only when the endpoint still presents it."""

    current = inspect_host_key(
        expected.host,
        expected.port,
        known_hosts_path=expected.known_hosts_path,
    )
    if (
        current.key_type != expected.key_type
        or current.key_base64 != expected.key_base64
    ):
        raise RuntimeError(
            "the router SSH host key changed after confirmation; nothing was trusted"
        )
    if current.trust_state == "changed":
        raise RuntimeError(
            "the saved SSH host key conflicts with the router; verify the router "
            "before removing the old known_hosts entry"
        )
    if current.trust_state == "trusted":
        return current

    path = expected.known_hosts_path
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = path.read_bytes() if path.exists() else b""
    separator = b"" if not existing or existing.endswith((b"\n", b"\r")) else b"\n"
    replacement = (
        existing + separator + expected.known_hosts_line.encode("ascii") + b"\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".known_hosts.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(replacement)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)

    verified = inspect_host_key(
        expected.host,
        expected.port,
        known_hosts_path=path,
    )
    if verified.trust_state != "trusted":
        raise RuntimeError("the confirmed SSH host key could not be saved")
    return verified


def _parse_scanned_keys(output: str) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) >= 3 and fields[1] in HOST_KEY_TYPES:
            keys.append((fields[1], fields[2]))
    return keys


def _fingerprint(known_hosts_line: str) -> str:
    result = subprocess.run(
        ["ssh-keygen", "-lf", "-"],
        input=f"{known_hosts_line}\n",
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        **background_process_options(),
    )
    match = FINGERPRINT_RE.search(result.stdout)
    if result.returncode != 0 or match is None:
        raise RuntimeError(
            result.stderr.strip() or "could not calculate the SSH host-key fingerprint"
        )
    return match.group(1)


def _known_host_keys(lookup_name: str, path: Path) -> list[tuple[str, str]]:
    if not path.is_file():
        return []
    result = subprocess.run(
        ["ssh-keygen", "-F", lookup_name, "-f", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        **background_process_options(),
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError(
            result.stderr.strip() or "could not inspect the OpenSSH known_hosts file"
        )
    keys: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        fields = line.strip().split()
        if len(fields) >= 3 and not fields[0].startswith("#"):
            keys.append((fields[1], fields[2]))
    return keys
