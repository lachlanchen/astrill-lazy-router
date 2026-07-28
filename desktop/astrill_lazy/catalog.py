from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .models import Region, Service


@dataclass(frozen=True)
class Catalog:
    services: tuple[Service, ...]
    regions: tuple[Region, ...]
    extensions: tuple[ExtensionInfo, ...] = ()

    @property
    def services_by_id(self) -> dict[str, Service]:
        return {service.id: service for service in self.services}

    @property
    def regions_by_id(self) -> dict[str, Region]:
        return {region.id: region for region in self.regions}

    def search(self, query: str) -> tuple[Service, ...]:
        needle = query.strip().casefold()
        if not needle:
            return self.services
        return tuple(
            service for service in self.services if needle in service.search_text
        )


@dataclass(frozen=True)
class ExtensionInfo:
    id: str
    name: str
    version: str
    path: Path
    capabilities: tuple[str, ...]
    entrypoints: dict[str, str]


def extension_roots() -> Iterable[Path]:
    configured = os.environ.get("ASTRILL_LAZY_EXTENSION_PATH")
    if configured:
        for item in configured.split(os.pathsep):
            if item:
                yield Path(item).expanduser()

    package_file = Path(__file__).resolve()
    yield Path.home() / ".local" / "share" / "astrill-lazy" / "extensions"
    yield package_file.parents[2] / "extensions"
    yield Path(sys.prefix) / "share" / "astrill-lazy" / "extensions"


def find_extension(extension_id: str) -> Path:
    extension = discover_extensions().get(extension_id)
    if extension is not None:
        return extension.path
    searched = ", ".join(str(item) for item in extension_roots())
    raise FileNotFoundError(f"extension {extension_id!r} was not found in: {searched}")


def discover_extensions() -> dict[str, ExtensionInfo]:
    discovered: dict[str, ExtensionInfo] = {}
    for root in extension_roots():
        if not root.is_dir():
            continue
        for candidate in sorted(root.iterdir()):
            manifest_path = candidate / "manifest.json"
            if not manifest_path.is_file():
                continue
            manifest = _read_json(manifest_path)
            if manifest.get("schema_version") != 1:
                continue
            extension_id = str(manifest.get("id", ""))
            if not extension_id or candidate.name != extension_id:
                continue
            if extension_id in discovered:
                continue
            discovered[extension_id] = ExtensionInfo(
                id=extension_id,
                name=str(manifest.get("name", extension_id)),
                version=str(manifest.get("version", "0")),
                path=candidate,
                capabilities=tuple(
                    str(item) for item in manifest.get("capabilities", [])
                ),
                entrypoints={
                    str(key): str(value)
                    for key, value in manifest.get("entrypoints", {}).items()
                },
            )
    return discovered


def load_catalog(
    extension_ids: str | Iterable[str] = ("core-catalog",),
) -> Catalog:
    if isinstance(extension_ids, str):
        requested = (extension_ids,)
    else:
        requested = tuple(extension_ids)
    if "core-catalog" not in requested:
        requested = ("core-catalog", *requested)

    available = discover_extensions()
    services: dict[str, Service] = {}
    regions: dict[str, Region] = {}
    loaded: list[ExtensionInfo] = []
    for extension_id in requested:
        extension = available.get(extension_id)
        if extension is None:
            searched = ", ".join(str(item) for item in extension_roots())
            raise FileNotFoundError(
                f"extension {extension_id!r} was not found in: {searched}"
            )
        extension_services, extension_regions = _load_catalog_extension(extension)
        services.update((item.id, item) for item in extension_services)
        regions.update((item.id, item) for item in extension_regions)
        loaded.append(extension)

    _ensure_unique((item.id for item in services.values()), "service")
    _ensure_unique((item.id for item in regions.values()), "region")
    return Catalog(
        services=tuple(services.values()),
        regions=tuple(regions.values()),
        extensions=tuple(loaded),
    )


def _load_catalog_extension(
    extension: ExtensionInfo,
) -> tuple[tuple[Service, ...], tuple[Region, ...]]:
    root = extension.path
    manifest = _read_json(root / "manifest.json")
    if manifest.get("schema_version") != 1:
        raise ValueError(f"unsupported extension manifest in {root}")
    if manifest.get("id") != extension.id:
        raise ValueError(f"extension directory and manifest IDs differ in {root}")

    services: tuple[Service, ...] = ()
    regions: tuple[Region, ...] = ()
    if "services" in extension.entrypoints:
        services_document = _read_json(root / extension.entrypoints["services"])
        if services_document.get("schema_version") != 1:
            raise ValueError("unsupported services catalog schema")
        services = tuple(
            Service.from_dict(item) for item in services_document["services"]
        )
    if "regions" in extension.entrypoints:
        regions_document = _read_json(root / extension.entrypoints["regions"])
        if regions_document.get("schema_version") != 1:
            raise ValueError("unsupported regions catalog schema")
        regions = tuple(Region.from_dict(item) for item in regions_document["regions"])
    _ensure_unique((item.id for item in services), "service")
    _ensure_unique((item.id for item in regions), "region")
    return services, regions


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _ensure_unique(values: Iterable[str], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {label} identifier: {value}")
        seen.add(value)
