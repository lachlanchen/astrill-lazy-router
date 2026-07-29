from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import astrill_lazy.windows_ssh_setup as setup
import pytest
from astrill_lazy.windows_ssh_setup import (
    WindowsHostKey,
    _run_telnet_script,
    _TelnetSession,
    authorize_windows_router_key_via_telnet,
    inspect_windows_host_key,
    trust_windows_host_key,
)

WINDOWS_NO_WINDOW = 0x08000000


def host_key(tmp_path: Path, *, state: str = "unknown") -> WindowsHostKey:
    return WindowsHostKey(
        host="192.168.1.1",
        port=22,
        key_type="ssh-ed25519",
        key_base64="AAAATESTKEY",
        fingerprint="SHA256:test-fingerprint",
        trust_state=state,
        known_hosts_path=tmp_path / "known_hosts",
    )


def test_host_key_inspection_uses_openssh_fingerprint_without_authentication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(
        "other.example ssh-ed25519 AAAAOTHER\n",
        encoding="ascii",
    )

    def run(arguments: list[str], **options: Any) -> SimpleNamespace:
        calls.append(arguments)
        assert options["creationflags"] == WINDOWS_NO_WINDOW
        if arguments[0] == "ssh-keyscan.exe":
            assert "input" not in options
            return SimpleNamespace(
                returncode=0,
                stdout="192.168.1.1 ssh-ed25519 AAAATESTKEY\n",
                stderr="",
            )
        if arguments[1] == "-F":
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        assert arguments == ["ssh-keygen.exe", "-lf", "-"]
        assert "AAAATESTKEY" in options["input"]
        return SimpleNamespace(
            returncode=0,
            stdout=("256 SHA256:dGVzdA== 192.168.1.1 (ED25519)\n"),
            stderr="",
        )

    monkeypatch.setattr(setup.subprocess, "run", run)
    monkeypatch.setattr(
        setup,
        "background_process_options",
        lambda: {"creationflags": WINDOWS_NO_WINDOW},
    )
    result = inspect_windows_host_key(
        "192.168.1.1",
        22,
        known_hosts_path=known_hosts,
    )

    assert result.fingerprint == "SHA256:dGVzdA=="
    assert result.trust_state == "unknown"
    assert result.known_hosts_line == ("192.168.1.1 ssh-ed25519 AAAATESTKEY")
    assert calls[0][0] == "ssh-keyscan.exe"
    assert [call[1] for call in calls[1:]] == ["-lf", "-F"]


def test_confirmed_host_key_is_saved_atomically_and_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = host_key(tmp_path)
    expected.known_hosts_path.write_text(
        "other.example ssh-ed25519 AAAAOTHER\n",
        encoding="ascii",
    )
    states = iter(
        (
            expected,
            host_key(tmp_path, state="trusted"),
            host_key(tmp_path, state="trusted"),
        )
    )
    monkeypatch.setattr(
        setup, "inspect_windows_host_key", lambda *_a, **_k: next(states)
    )

    assert trust_windows_host_key(expected).trust_state == "trusted"
    assert trust_windows_host_key(expected).trust_state == "trusted"
    document = expected.known_hosts_path.read_text(encoding="ascii")
    assert "other.example ssh-ed25519 AAAAOTHER" in document
    assert document.count(expected.known_hosts_line) == 1
    assert not list(tmp_path.glob(".known_hosts.*.tmp"))


def test_changed_host_key_is_never_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = host_key(tmp_path)
    changed = WindowsHostKey(
        **{
            **expected.__dict__,
            "key_base64": "AAAACHANGED",
            "fingerprint": "SHA256:changed",
        }
    )
    monkeypatch.setattr(
        setup,
        "inspect_windows_host_key",
        lambda *_a, **_k: changed,
    )

    with pytest.raises(RuntimeError, match="changed after confirmation"):
        trust_windows_host_key(expected)
    assert not expected.known_hosts_path.exists()


class ScriptedSocket:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.sent: list[bytes] = []

    def settimeout(self, _timeout: float) -> None:
        pass

    def recv(self, _size: int) -> bytes:
        return self.chunks.pop(0)

    def sendall(self, value: bytes) -> None:
        self.sent.append(value)

    def close(self) -> None:
        pass


def test_telnet_negotiation_and_transient_password_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = ScriptedSocket(
        [
            bytes((_TelnetSession.IAC, _TelnetSession.WILL, _TelnetSession.ECHO))
            + b"DD-WRT login:",
            b"Password:",
            b"root@DD-WRT:~# ",
            b"__ASTRILL_LAZY_TELNET_DONE__0\r\n",
        ]
    )
    monkeypatch.setattr(
        setup.socket,
        "create_connection",
        lambda *_a, **_k: connection,
    )

    _run_telnet_script(
        "192.168.1.1",
        23,
        user="root",
        password="one-time-secret",
        script='[ "$(id -u)" = 0 ]\nprintf ready\n',
        timeout=10,
    )

    assert (
        bytes((_TelnetSession.IAC, _TelnetSession.DO, _TelnetSession.ECHO))
        in connection.sent
    )
    assert b"root\r\n" in connection.sent
    assert b"one-time-secret\r\n" in connection.sent
    command = connection.sent[-1]
    assert b"one-time-secret" not in command
    assert b"__ASTRILL_LAZY_TELNET_DONE__" not in command
    assert b"id -u" in command


class FakeRouter:
    host = "192.168.1.1"
    port = 22
    user = "root"

    def __init__(self, pings: list[bool]) -> None:
        self.pings = iter(pings)
        self.scripts: list[str] = []

    def ping(self) -> bool:
        return next(self.pings)

    def run_script(self, script: str, *, timeout: int) -> str:
        assert timeout == 30
        self.scripts.append(script)
        return ""


def test_telnet_authorization_verifies_key_before_disabling_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = host_key(tmp_path, state="trusted")
    events: list[str] = []
    router = FakeRouter([True, True])
    identity = tmp_path / "router-key"
    monkeypatch.setattr(setup, "identity_path", lambda _value: identity)
    monkeypatch.setattr(
        setup,
        "read_public_key",
        lambda _value: "ssh-ed25519 AAAAPUBLIC astrill-lazy-router",
    )
    monkeypatch.setattr(setup, "trust_windows_host_key", lambda value: value)
    monkeypatch.setattr(setup.time, "sleep", lambda _seconds: None)

    def telnet(*_args: Any, **options: Any) -> None:
        events.append("telnet")
        assert "AAAAPUBLIC" in options["script"]
        assert "one-time-secret" not in options["script"]

    monkeypatch.setattr(setup, "_run_telnet_script", telnet)
    original_ping = router.ping

    def ping() -> bool:
        events.append("ping")
        return original_ping()

    router.ping = ping  # type: ignore[method-assign]
    original_run_script = router.run_script

    def run_script(script: str, *, timeout: int) -> str:
        events.append("harden")
        return original_run_script(script, timeout=timeout)

    router.run_script = run_script  # type: ignore[method-assign]
    result = authorize_windows_router_key_via_telnet(
        router,  # type: ignore[arg-type]
        expected,
        "one-time-secret",
        user="root",
        identity_file=str(identity),
    )

    assert result.identity_file == identity
    assert result.password_login_disabled is True
    assert events == ["telnet", "ping", "harden", "ping"]
    assert "sshd_passwd_auth=0" in router.scripts[0]


def test_failed_key_verification_never_disables_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = host_key(tmp_path, state="trusted")
    router = FakeRouter([False] * 8)
    monkeypatch.setattr(setup, "identity_path", lambda _value: tmp_path / "key")
    monkeypatch.setattr(
        setup,
        "read_public_key",
        lambda _value: "ssh-ed25519 AAAAPUBLIC astrill-lazy-router",
    )
    monkeypatch.setattr(setup, "trust_windows_host_key", lambda value: value)
    monkeypatch.setattr(setup, "_run_telnet_script", lambda *_a, **_k: None)
    monkeypatch.setattr(setup.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="did not accept"):
        authorize_windows_router_key_via_telnet(
            router,  # type: ignore[arg-type]
            expected,
            "one-time-secret",
            user="root",
            identity_file=str(tmp_path / "key"),
        )
    assert router.scripts == []
