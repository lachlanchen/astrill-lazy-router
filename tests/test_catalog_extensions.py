from __future__ import annotations

import json
from pathlib import Path

import pytest
from astrill_lazy.catalog import _read_json, discover_extensions, load_catalog


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_user_extension_is_discovered_and_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extension = tmp_path / "example-catalog"
    extension.mkdir()
    _write_json(
        extension / "manifest.json",
        {
            "schema_version": 1,
            "id": "example-catalog",
            "name": "Example Catalog",
            "version": "1.0.0",
            "capabilities": ["catalog.services"],
            "entrypoints": {"services": "services.json"},
        },
    )
    _write_json(
        extension / "services.json",
        {
            "schema_version": 1,
            "services": [
                {
                    "id": "example-service",
                    "name": "Example Service",
                    "company": "Example",
                    "category": "Test",
                    "default_route": "vpn",
                    "preferred_region": "active-astrill",
                    "domains": ["example.net"],
                }
            ],
        },
    )
    monkeypatch.setenv("ASTRILL_LAZY_EXTENSION_PATH", str(tmp_path))
    assert "example-catalog" in discover_extensions()
    catalog = load_catalog(["core-catalog", "example-catalog"])
    assert catalog.services_by_id["example-service"].domains == ("example.net",)
    assert [item.id for item in catalog.extensions] == [
        "core-catalog",
        "example-catalog",
    ]


def test_missing_enabled_extension_is_an_error() -> None:
    with pytest.raises(FileNotFoundError, match="missing-extension"):
        load_catalog(["core-catalog", "missing-extension"])


def test_extension_can_split_services_across_entrypoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extension = tmp_path / "split-catalog"
    extension.mkdir()
    _write_json(
        extension / "manifest.json",
        {
            "schema_version": 1,
            "id": "split-catalog",
            "name": "Split Catalog",
            "entrypoints": {"services": ["first.json", "second.json"]},
        },
    )
    for index, filename in enumerate(("first.json", "second.json"), start=1):
        _write_json(
            extension / filename,
            {
                "schema_version": 1,
                "services": [
                    {
                        "id": f"split-{index}",
                        "name": f"Split {index}",
                        "company": "Example",
                        "category": "Test",
                        "profile_type": "app",
                        "default_route": "vpn",
                        "preferred_region": "active-astrill",
                        "domains": [f"split-{index}.example"],
                    }
                ],
            },
        )
    monkeypatch.setenv("ASTRILL_LAZY_EXTENSION_PATH", str(tmp_path))
    catalog = load_catalog(["core-catalog", "split-catalog"])
    assert {"split-1", "split-2"} <= set(catalog.services_by_id)


def test_extension_entrypoint_cannot_escape_its_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extension = tmp_path / "escaping-catalog"
    extension.mkdir()
    _write_json(
        extension / "manifest.json",
        {
            "schema_version": 1,
            "id": "escaping-catalog",
            "name": "Escaping Catalog",
            "entrypoints": {"services": "../outside.json"},
        },
    )
    _write_json(tmp_path / "outside.json", {"schema_version": 1, "services": []})
    monkeypatch.setenv("ASTRILL_LAZY_EXTENSION_PATH", str(tmp_path))
    with pytest.raises(ValueError, match="escapes"):
        load_catalog(["core-catalog", "escaping-catalog"])


def test_json_loader_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version": 1, "schema_version": 2}', encoding="ascii")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        _read_json(path)
