from __future__ import annotations

import json
from pathlib import Path

import pytest
from astrill_lazy.catalog import discover_extensions, load_catalog


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
