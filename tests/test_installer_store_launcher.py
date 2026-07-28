from __future__ import annotations

import json
import tarfile
from io import BytesIO
from pathlib import Path

import pytest
from astrill_lazy.installer import build_router_package, find_router_root
from astrill_lazy.launcher import parse_command
from astrill_lazy.store import ConfigStore, default_uu_rule


def test_router_package_is_deterministic_and_contains_only_runtime_files() -> None:
    root = find_router_root()
    first = build_router_package(root)
    second = build_router_package(root)
    assert first == second
    assert len(first) < 20_000

    with tarfile.open(fileobj=BytesIO(first), mode="r:gz") as archive:
        names = archive.getnames()
        assert names == [
            "astrill-lazy/alctl",
            "astrill-lazy/alapi",
            "astrill-lazy/alpage",
            "astrill-lazy/VERSION",
        ]
        assert all("key" not in name and "backup" not in name for name in names)


def test_config_store_round_trip_is_private(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.rules = [default_uu_rule()]
    store.enabled_extensions = ["core-catalog"]
    store.save()
    assert path.stat().st_mode & 0o777 == 0o600

    loaded = ConfigStore(path)
    assert loaded.rules[0].selector == "uu-remote"
    assert loaded.enabled_extensions == ["core-catalog"]
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1


def test_parse_application_command() -> None:
    executable, arguments = parse_command("/usr/bin/printf '%s' hello")
    assert executable == "/usr/bin/printf"
    assert arguments == ["%s", "hello"]


def test_parse_application_command_rejects_missing_file() -> None:
    with pytest.raises(ValueError, match="not found"):
        parse_command("/does/not/exist")
