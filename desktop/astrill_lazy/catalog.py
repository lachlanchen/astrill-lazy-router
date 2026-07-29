from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    entrypoints: dict[str, tuple[str, ...]]


def extension_roots() -> Iterable[Path]:
    configured = os.environ.get("ASTRILL_LAZY_EXTENSION_PATH")
    if configured:
        for item in configured.split(os.pathsep):
            if item:
                yield Path(item).expanduser()

    package_file = Path(__file__).resolve()
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        yield Path(frozen_root) / "extensions"
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
                entrypoints=_normalize_entrypoints(manifest.get("entrypoints", {})),
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
    for service in services.values():
        if service.preferred_region not in regions:
            raise ValueError(
                f"service {service.id!r} uses unknown preferred region "
                f"{service.preferred_region!r}"
            )
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
    company_countries = _load_company_countries(root, extension.entrypoints)
    if "services" in extension.entrypoints:
        service_items: list[Service] = []
        for path in _entrypoint_paths(root, extension.entrypoints["services"]):
            services_document = _read_json(path)
            if services_document.get("schema_version") != 1:
                raise ValueError("unsupported services catalog schema")
            for item in services_document["services"]:
                value = dict(item)
                value.setdefault(
                    "provider_country",
                    company_countries.get(
                        str(value.get("company", "")), "Other / Global"
                    ),
                )
                service_items.append(Service.from_dict(value))
        services = tuple(service_items)
    if "regions" in extension.entrypoints:
        region_items: list[Region] = []
        for path in _entrypoint_paths(root, extension.entrypoints["regions"]):
            regions_document = _read_json(path)
            if regions_document.get("schema_version") != 1:
                raise ValueError("unsupported regions catalog schema")
            region_items.extend(
                Region.from_dict(item) for item in regions_document["regions"]
            )
        regions = tuple(region_items)
    _ensure_unique((item.id for item in services), "service")
    _ensure_unique((item.id for item in regions), "region")
    return services, regions


def _load_company_countries(
    root: Path, entrypoints: dict[str, tuple[str, ...]]
) -> dict[str, str]:
    company_countries: dict[str, str] = {}
    for path in _entrypoint_paths(root, entrypoints.get("service_countries", ())):
        document = _read_json(path)
        if document.get("schema_version") != 1:
            raise ValueError("unsupported service country catalog schema")
        countries = document.get("countries")
        if not isinstance(countries, dict):
            raise TypeError("service country catalog must contain a countries object")
        for raw_country, raw_companies in countries.items():
            country = str(raw_country).strip()
            if not country:
                raise ValueError("service country name cannot be empty")
            if (
                not isinstance(raw_companies, list)
                or not raw_companies
                or not all(
                    isinstance(item, str) and item.strip() for item in raw_companies
                )
            ):
                raise ValueError(
                    f"service country {country!r} must contain company names"
                )
            for company in raw_companies:
                if company in company_countries:
                    raise ValueError(
                        f"company {company!r} appears in more than one service country"
                    )
                company_countries[company] = country
    return company_countries


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=_object_without_duplicates)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _normalize_entrypoints(value: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        raise TypeError("extension entrypoints must be an object")
    normalized: dict[str, tuple[str, ...]] = {}
    for raw_key, raw_paths in value.items():
        key = str(raw_key)
        if isinstance(raw_paths, str):
            paths = (raw_paths,)
        elif (
            isinstance(raw_paths, list)
            and raw_paths
            and all(isinstance(item, str) for item in raw_paths)
        ):
            paths = tuple(raw_paths)
        else:
            raise ValueError(f"invalid extension entrypoint {key!r}")
        normalized[key] = paths
    return normalized


def _entrypoint_paths(root: Path, values: tuple[str, ...]) -> tuple[Path, ...]:
    resolved_root = root.resolve()
    paths: list[Path] = []
    for value in values:
        candidate = (root / value).resolve()
        if not candidate.is_relative_to(resolved_root):
            raise ValueError(f"extension entrypoint escapes its directory: {value!r}")
        paths.append(candidate)
    return tuple(paths)


def _ensure_unique(values: Iterable[str], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {label} identifier: {value}")
        seen.add(value)
