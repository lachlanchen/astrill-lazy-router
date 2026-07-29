from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .subprocess_support import background_process_options

if TYPE_CHECKING:
    from .router import RouterClient

DEFAULT_ROUTER_HOST = "192.168.1.1"
DEFAULT_ROUTER_USER = "root"
DEFAULT_ROUTER_PORT = 22
DEFAULT_IDENTITY_FILE = "~/.ssh/astrill_lazy_router_ed25519"


def identity_path(value: str = DEFAULT_IDENTITY_FILE) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("SSH identity path must be absolute or start with ~")
    if "\n" in str(path) or "\x00" in str(path):
        raise ValueError("SSH identity path contains an invalid character")
    return path


def ensure_local_identity(value: str = DEFAULT_IDENTITY_FILE) -> Path:
    private_key = identity_path(value)
    public_key = private_key.with_name(f"{private_key.name}.pub")
    parent_existed = private_key.parent.exists()
    private_key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not parent_existed or private_key.parent == Path.home() / ".ssh":
        os.chmod(private_key.parent, 0o700)

    if private_key.exists():
        if not private_key.is_file():
            raise ValueError(f"SSH identity is not a file: {private_key}")
        os.chmod(private_key, 0o600)
        if not public_key.exists():
            result = subprocess.run(
                ["ssh-keygen", "-y", "-f", str(private_key)],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
                **background_process_options(),
            )
            if result.returncode != 0 or not result.stdout.strip():
                raise RuntimeError(
                    result.stderr.strip() or "could not derive the SSH public key"
                )
            _write_public_key(public_key, result.stdout.strip())
        return private_key

    if public_key.exists():
        raise ValueError(f"SSH public key exists without its private key: {public_key}")
    result = subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "astrill-lazy-router",
            "-f",
            str(private_key),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        **background_process_options(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or "could not generate the SSH identity"
        )
    os.chmod(private_key, 0o600)
    os.chmod(public_key, 0o644)
    return private_key


def read_public_key(value: str = DEFAULT_IDENTITY_FILE) -> str:
    private_key = ensure_local_identity(value)
    public_key = private_key.with_name(f"{private_key.name}.pub")
    key = public_key.read_text(encoding="ascii").strip()
    fields = key.split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise ValueError("router identity must use an Ed25519 public key")
    return key


def authorize_router_key(client: RouterClient, password: str) -> None:
    if not password:
        raise ValueError("router password is required for SSH key setup")
    if "\n" in password or "\x00" in password:
        raise ValueError("router password contains an invalid character")
    if client.user is None:
        raise ValueError("router SSH user is required")
    if client.identity_file is None:
        raise ValueError("router SSH identity is required")
    if shutil.which("sshpass") is None:
        raise RuntimeError("sshpass is required for one-time password bootstrap")

    public_key = read_public_key(client.identity_file)
    key_blob = public_key.split()[1]
    target = f"{client.user}@{client.host}"
    port = client.port or DEFAULT_ROUTER_PORT
    stage_script = f"""
set -e
new_key={shlex.quote(public_key)}
key_blob={shlex.quote(key_blob)}
current=$(nvram get sshd_authorized_keys)
case "$current" in
    *"$key_blob"*) ;;
    *)
        [ -z "$current" ] || current="$current
"
        nvram set "sshd_authorized_keys=$current$new_key"
        ;;
esac
nvram set sshd_enable=1
nvram set sshd_port={port}
nvram commit >/dev/null
(sleep 1; stopservice sshd; startservice sshd) >/dev/null 2>&1 &
"""
    environment = os.environ.copy()
    environment["SSHPASS"] = password
    result = subprocess.run(
        [
            "sshpass",
            "-e",
            "ssh",
            "-o",
            "BatchMode=no",
            "-o",
            "PreferredAuthentications=password,keyboard-interactive",
            "-o",
            "PubkeyAuthentication=no",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=8",
            "-p",
            str(port),
            target,
            "/bin/sh -s",
        ],
        input=stage_script.encode("utf-8"),
        check=False,
        capture_output=True,
        timeout=30,
        env=environment,
        **background_process_options(),
    )
    environment["SSHPASS"] = ""
    del environment

    verified = False
    last_error = ""
    for _attempt in range(6):
        time.sleep(2)
        try:
            if client.ping():
                verified = True
                break
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
    if not verified:
        message = result.stderr.decode(errors="replace").strip() or last_error
        raise RuntimeError(
            message
            or "the router did not accept the generated key; password SSH was unchanged"
        )

    harden_script = """
set -e
nvram set sshd_passwd_auth=0
nvram set sshd_forwarding=0
nvram set remote_mgt_ssh=0
nvram commit >/dev/null
(sleep 1; stopservice sshd; startservice sshd) >/dev/null 2>&1 &
"""
    harden_error = ""
    try:
        client.run_script(harden_script, timeout=30)
    except RuntimeError as exc:
        harden_error = str(exc)

    verified = False
    for _attempt in range(6):
        time.sleep(2)
        try:
            if client.ping():
                verified = True
                break
        except Exception as exc:  # noqa: BLE001
            harden_error = str(exc)
    if not verified:
        raise RuntimeError(
            harden_error or "key-only SSH failed after disabling password login"
        )


def _write_public_key(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(f"{value} astrill-lazy-router\n", encoding="ascii")
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
