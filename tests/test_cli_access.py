from __future__ import annotations

import json

from astrill_lazy.cli import main


def test_access_guard_can_be_changed_without_contacting_router(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert main(["access", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["access"] == "read-only"

    assert main(["access", "read-write"]) == 0
    assert json.loads(capsys.readouterr().out)["access"] == "read-write"

    assert main(["access", "read-only"]) == 0
    assert json.loads(capsys.readouterr().out)["access"] == "read-only"


def test_fresh_read_only_config_blocks_mutating_cli_before_ssh(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert main(["apply"]) == 2
    assert "read-only access blocks this command" in capsys.readouterr().err

    assert (
        main(
            [
                "device-flow",
                "set",
                "--owner",
                "test-phone",
                "--source",
                "192.168.1.132",
                "--mac",
                "aa:bb:cc:dd:ee:ff",
                "--domain",
                "play.googleapis.com",
            ]
        )
        == 2
    )
    assert "read-only access blocks this command" in capsys.readouterr().err

    assert (
        main(
            [
                "app-flow",
                "set",
                "mac-uuremote",
                "192.168.1.99",
                "udp",
                "64479",
                "direct",
            ]
        )
        == 2
    )
    assert "read-only access blocks this command" in capsys.readouterr().err

    assert (
        main(
            [
                "isolated-run",
                "--allow-domain",
                "example.com",
                "--",
                "/bin/true",
            ]
        )
        == 2
    )
    assert "read-only access blocks this command" in capsys.readouterr().err
