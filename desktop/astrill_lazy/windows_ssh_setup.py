from __future__ import annotations

import os
import re
import shlex
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .router import RouterClient
from .ssh_setup import identity_path, read_public_key

HOST_KEY_TYPES = (
    "ssh-ed25519",
    "ecdsa-sha2-nistp521",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp256",
    "sk-ssh-ed25519@openssh.com",
    "rsa-sha2-512",
    "rsa-sha2-256",
    "ssh-rsa",
)
FINGERPRINT_RE = re.compile(r"\b(SHA256:[A-Za-z0-9+/=]+)")


@dataclass(frozen=True)
class WindowsHostKey:
    host: str
    port: int
    key_type: str
    key_base64: str
    fingerprint: str
    trust_state: str
    known_hosts_path: Path

    @property
    def lookup_name(self) -> str:
        return self.host if self.port == 22 else f"[{self.host}]:{self.port}"

    @property
    def known_hosts_line(self) -> str:
        return f"{self.lookup_name} {self.key_type} {self.key_base64}"


@dataclass(frozen=True)
class WindowsKeyAuthorization:
    host_key: WindowsHostKey
    identity_file: Path
    password_login_disabled: bool


def inspect_windows_host_key(
    host: str,
    port: int,
    *,
    known_hosts_path: Path | None = None,
    timeout: int = 8,
) -> WindowsHostKey:
    """Read an SSH host key without authenticating or changing local trust."""
    if not 1 <= port <= 65535:
        raise ValueError("SSH port must be between 1 and 65535")
    bounded_timeout = max(1, min(timeout, 30))
    scan = subprocess.run(
        [
            "ssh-keyscan.exe",
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


def trust_windows_host_key(expected: WindowsHostKey) -> WindowsHostKey:
    """Persist a user-confirmed key only if the router still presents it."""
    current = inspect_windows_host_key(
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

    verified = inspect_windows_host_key(
        expected.host,
        expected.port,
        known_hosts_path=path,
    )
    if verified.trust_state != "trusted":
        raise RuntimeError("the confirmed SSH host key could not be saved")
    return verified


def authorize_windows_router_key_via_telnet(
    router: RouterClient,
    expected_host_key: WindowsHostKey,
    password: str,
    *,
    user: str,
    identity_file: str,
    telnet_port: int = 23,
    disable_password_login: bool = True,
) -> WindowsKeyAuthorization:
    """Authorize a dedicated key over LAN Telnet, then verify strict SSH."""
    if not password:
        raise ValueError("router password is required for SSH key setup")
    if "\n" in password or "\x00" in password:
        raise ValueError("router password contains an invalid character")
    if router.host != expected_host_key.host:
        raise ValueError("the confirmed host key does not match the configured router")
    if router.port not in {None, expected_host_key.port}:
        raise ValueError("the confirmed host-key port does not match the router")
    if router.user != user:
        raise ValueError("the configured router user changed after confirmation")

    private_key = identity_path(identity_file)
    public_key = read_public_key(identity_file)
    fields = public_key.split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise ValueError("router identity must use an Ed25519 public key")
    key_blob = fields[1]
    trusted_host_key = trust_windows_host_key(expected_host_key)

    stage_script = f"""
set -e
[ "$(id -u)" = 0 ]
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
nvram commit >/dev/null
(sleep 1; stopservice sshd; startservice sshd) >/dev/null 2>&1 &
"""
    _run_telnet_script(
        trusted_host_key.host,
        telnet_port,
        user=user,
        password=password,
        script=stage_script,
        timeout=30,
    )
    _wait_for_key_only(router, "the router did not accept the dedicated SSH key")

    if disable_password_login:
        harden_script = """
set -e
nvram set sshd_passwd_auth=0
nvram set sshd_forwarding=0
nvram set remote_mgt_ssh=0
nvram commit >/dev/null
(sleep 1; stopservice sshd; startservice sshd) >/dev/null 2>&1 &
"""
        router.run_script(harden_script, timeout=30)
        _wait_for_key_only(
            router,
            "key-only SSH failed after disabling router password login",
        )

    return WindowsKeyAuthorization(
        host_key=trusted_host_key,
        identity_file=private_key,
        password_login_disabled=disable_password_login,
    )


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
        ["ssh-keygen.exe", "-lf", "-"],
        input=f"{known_hosts_line}\n",
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
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
        ["ssh-keygen.exe", "-F", lookup_name, "-f", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
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


def _run_telnet_script(
    host: str,
    port: int,
    *,
    user: str,
    password: str,
    script: str,
    timeout: int,
) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("Telnet port must be between 1 and 65535")
    if not user or any(character.isspace() for character in user):
        raise ValueError("Telnet user must not be empty or contain spaces")
    connection = socket.create_connection((host, port), timeout=10)
    try:
        session = _TelnetSession(connection)
        session.read_until(("login:", "username:"), timeout=10)
        session.send_line(user)
        session.read_until(("password:",), timeout=10)
        session.send_line(password)
        authenticated = session.read_until(
            ("# ", "$ ", "login incorrect", "authentication failed"),
            timeout=12,
        )
        if any(
            value in authenticated.casefold()
            for value in ("login incorrect", "authentication failed")
        ):
            raise RuntimeError("the router rejected the supplied Telnet password")
        marker = "__ASTRILL_LAZY_TELNET_DONE__"
        command = (
            "/bin/sh <<'__ASTRILL_LAZY_TELNET_SCRIPT__'\n"
            f"{script.rstrip()}\n"
            "__ASTRILL_LAZY_TELNET_SCRIPT__\n"
            "status=$?\n"
            "result_marker='__ASTRILL_LAZY_'\n"
            'result_marker="${result_marker}TELNET_DONE__"\n'
            'printf \'\\n%s%s\\n\' "$result_marker" "$status"\n'
        )
        session.send(command)
        output = session.read_until((marker,), timeout=timeout)
        output += session.read_until(("\n",), timeout=5)
        match = re.search(rf"{re.escape(marker)}(\d+)", output)
        if match is None:
            raise RuntimeError("the router Telnet setup did not return a status")
        status = int(match.group(1))
        if status != 0:
            raise RuntimeError(f"router Telnet setup failed with status {status}")
    except (TimeoutError, OSError) as exc:
        raise RuntimeError(f"router Telnet setup failed: {exc}") from exc
    finally:
        connection.close()


class _TelnetSession:
    IAC = 255
    DONT = 254
    DO = 253
    WONT = 252
    WILL = 251
    SB = 250
    SE = 240
    ECHO = 1
    SUPPRESS_GO_AHEAD = 3

    def __init__(self, connection: socket.socket) -> None:
        self.connection = connection
        self.pending = ""
        self.state = "data"
        self.command = 0

    def send(self, value: str) -> None:
        self.connection.sendall(value.replace("\n", "\r\n").encode("utf-8"))

    def send_line(self, value: str) -> None:
        self.send(f"{value}\n")

    def read_until(self, needles: tuple[str, ...], *, timeout: int) -> str:
        deadline = time.monotonic() + timeout
        folded_needles = tuple(value.casefold() for value in needles)
        while True:
            folded = self.pending.casefold()
            for needle in folded_needles:
                index = folded.find(needle)
                if index >= 0:
                    end = index + len(needle)
                    result = self.pending[:end]
                    self.pending = self.pending[end:]
                    return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for the router Telnet response")
            self.connection.settimeout(remaining)
            chunk = self.connection.recv(4096)
            if not chunk:
                raise ConnectionError("the router closed the Telnet connection")
            self.pending += self._decode(chunk).decode("utf-8", errors="replace")

    def _decode(self, chunk: bytes) -> bytes:
        output = bytearray()
        for value in chunk:
            if self.state == "data":
                if value == self.IAC:
                    self.state = "iac"
                else:
                    output.append(value)
            elif self.state == "iac":
                if value == self.IAC:
                    output.append(value)
                    self.state = "data"
                elif value in {self.DO, self.DONT, self.WILL, self.WONT}:
                    self.command = value
                    self.state = "option"
                elif value == self.SB:
                    self.state = "subnegotiation"
                else:
                    self.state = "data"
            elif self.state == "option":
                self._answer_option(self.command, value)
                self.state = "data"
            elif self.state == "subnegotiation":
                if value == self.IAC:
                    self.state = "subnegotiation_iac"
            elif self.state == "subnegotiation_iac":
                self.state = "data" if value == self.SE else "subnegotiation"
        return bytes(output)

    def _answer_option(self, command: int, option: int) -> None:
        if command == self.DO:
            response = self.WONT
        elif command == self.WILL:
            response = (
                self.DO if option in {self.ECHO, self.SUPPRESS_GO_AHEAD} else self.DONT
            )
        elif command == self.DONT:
            response = self.WONT
        else:
            response = self.DONT
        self.connection.sendall(bytes((self.IAC, response, option)))


def _wait_for_key_only(router: RouterClient, failure_message: str) -> None:
    last_error = ""
    for _attempt in range(8):
        time.sleep(2)
        try:
            if router.ping():
                return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
    raise RuntimeError(f"{failure_message}: {last_error}".rstrip(": "))
