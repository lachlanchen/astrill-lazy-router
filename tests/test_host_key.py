from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from astrill_lazy import host_key


def test_unix_host_key_inspection_uses_platform_openssh_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(arguments: list[str], **options: Any) -> SimpleNamespace:
        calls.append(arguments)
        if arguments[0] == "ssh-keyscan":
            return SimpleNamespace(
                returncode=0,
                stdout="192.168.1.1 ssh-ed25519 AAAATESTKEY\n",
                stderr="",
            )
        if arguments[1] == "-F":
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        assert arguments == ["ssh-keygen", "-lf", "-"]
        assert "AAAATESTKEY" in options["input"]
        return SimpleNamespace(
            returncode=0,
            stdout="256 SHA256:dGVzdA== 192.168.1.1 (ED25519)\n",
            stderr="",
        )

    monkeypatch.setattr(host_key.subprocess, "run", run)
    result = host_key.inspect_host_key(
        "192.168.1.1",
        22,
        known_hosts_path=tmp_path / "known_hosts",
    )

    assert result.fingerprint == "SHA256:dGVzdA=="
    assert result.trust_state == "unknown"
    assert calls[0][0] == "ssh-keyscan"
    assert all(call[0] != "ssh-keyscan.exe" for call in calls)


def test_unix_trust_refuses_a_changed_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = host_key.WindowsHostKey(
        host="192.168.1.1",
        port=22,
        key_type="ssh-ed25519",
        key_base64="AAAAEXPECTED",
        fingerprint="SHA256:expected",
        trust_state="unknown",
        known_hosts_path=tmp_path / "known_hosts",
    )
    changed = host_key.WindowsHostKey(
        host=expected.host,
        port=expected.port,
        key_type=expected.key_type,
        key_base64="AAAACHANGED",
        fingerprint="SHA256:changed",
        trust_state="unknown",
        known_hosts_path=expected.known_hosts_path,
    )
    monkeypatch.setattr(host_key, "inspect_host_key", lambda *_a, **_k: changed)

    with pytest.raises(RuntimeError, match="changed after confirmation"):
        host_key.trust_host_key(expected)
    assert not expected.known_hosts_path.exists()
