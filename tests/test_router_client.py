from __future__ import annotations

import subprocess

import pytest
from astrill_lazy.router import (
    DOMAIN_REFRESH_TIMEOUT,
    CommandResult,
    RouterClient,
    RouterError,
)


def test_refresh_allows_a_full_forced_domain_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RouterClient()

    def run_alctl(
        arguments: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        assert arguments == ["refresh", "--json"]
        assert input_bytes is None
        assert timeout == DOMAIN_REFRESH_TIMEOUT
        return CommandResult('{"health":"healthy"}\n', "", 0)

    monkeypatch.setattr(client, "_run_alctl", run_alctl)
    assert client.refresh() == {"health": "healthy"}


def test_remote_timeout_is_reported_as_a_router_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RouterClient(timeout=7)

    def time_out(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(["ssh"], 7)

    monkeypatch.setattr(subprocess, "run", time_out)
    with pytest.raises(RouterError, match="timed out after 7 seconds"):
        client.status()
