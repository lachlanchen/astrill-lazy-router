"""Validated persistence for manually collected endpoint latency results."""

from __future__ import annotations

import ipaddress
import json
import math
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from time import time
from typing import Any

from .astrill import AstrillServer
from .endpoint_probe import (
    EndpointProbeResult,
    EndpointProbeStatus,
    prepare_endpoint_probe,
)

CACHE_SCHEMA_VERSION = 1
MAX_CACHE_BYTES = 2 * 1024 * 1024
MAX_CACHE_RECORDS = 4096
MAX_SERVER_NAME_LENGTH = 256
MAX_DETAIL_LENGTH = 1024
MAX_SAVED_LATENCY_MS = 60_000.0
STALE_AFTER_SECONDS = 24 * 60 * 60
FUTURE_TOLERANCE_SECONDS = 5 * 60

EndpointProbeCache = dict[tuple[int, int], "SavedEndpointProbe"]


class SavedProbeState(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    ENDPOINT_CHANGED = "endpoint_changed"


@dataclass(frozen=True)
class SavedEndpointProbe:
    result: EndpointProbeResult
    checked_at: int


def endpoint_probe_cache_path(config_path: Path) -> Path:
    """Return the derived-data cache beside the main desktop configuration."""
    return config_path.with_name("endpoint-latency.json")


def load_endpoint_probe_cache(path: Path) -> EndpointProbeCache:
    """Load valid cache records without allowing cache damage to block startup."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_CACHE_BYTES:
            return {}
        document = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda value: _reject_json_constant(value),
        )
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != CACHE_SCHEMA_VERSION
        or not isinstance(document.get("results"), list)
    ):
        return {}

    loaded: EndpointProbeCache = {}
    for value in document["results"][:MAX_CACHE_RECORDS]:
        try:
            saved = _saved_probe_from_dict(value)
        except (TypeError, ValueError):
            continue
        key = (saved.result.server_id, saved.result.selected_protocol)
        loaded[key] = saved
    return loaded


def save_endpoint_probe_cache(path: Path, cache: EndpointProbeCache) -> None:
    """Atomically save the bounded cache, or remove it when results are empty."""
    if not cache:
        path.unlink(missing_ok=True)
        return
    if len(cache) > MAX_CACHE_RECORDS:
        raise ValueError(f"endpoint latency cache exceeds {MAX_CACHE_RECORDS} records")

    results: list[dict[str, Any]] = []
    for key, saved in sorted(cache.items()):
        if key != (saved.result.server_id, saved.result.selected_protocol):
            raise ValueError("endpoint latency cache key does not match its result")
        results.append(_saved_probe_to_dict(saved))
    document = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "results": results,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".endpoint-latency.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                document,
                handle,
                ensure_ascii=True,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def assess_saved_endpoint_probe(
    saved: SavedEndpointProbe,
    server: AstrillServer,
    selected_protocol: int,
    *,
    now: int | None = None,
) -> SavedProbeState:
    """Check age and the current applet target without opening a connection."""
    result = saved.result
    if result.server_id != server.id or result.selected_protocol != selected_protocol:
        return SavedProbeState.ENDPOINT_CHANGED
    try:
        target = prepare_endpoint_probe(server, selected_protocol)
    except (TypeError, ValueError):
        if result.status is not EndpointProbeStatus.UNAVAILABLE:
            return SavedProbeState.ENDPOINT_CHANGED
    else:
        if (
            result.tested_protocol != target.tested_protocol
            or result.address != target.address
            or result.port != target.port
        ):
            return SavedProbeState.ENDPOINT_CHANGED

    current_time = int(time()) if now is None else now
    age = current_time - saved.checked_at
    if age > STALE_AFTER_SECONDS or age < -FUTURE_TOLERANCE_SECONDS:
        return SavedProbeState.STALE
    return SavedProbeState.CURRENT


def _saved_probe_to_dict(saved: SavedEndpointProbe) -> dict[str, Any]:
    validated = _saved_probe_from_dict(
        {
            "server_id": saved.result.server_id,
            "server_name": saved.result.server_name,
            "selected_protocol": saved.result.selected_protocol,
            "status": saved.result.status.value,
            "tested_protocol": saved.result.tested_protocol,
            "address": saved.result.address,
            "port": saved.result.port,
            "latency_ms": saved.result.latency_ms,
            "used_tcp_counterpart": saved.result.used_tcp_counterpart,
            "port_from_range": saved.result.port_from_range,
            "detail": saved.result.detail,
            "checked_at": saved.checked_at,
        }
    )
    result = validated.result
    return {
        "server_id": result.server_id,
        "server_name": result.server_name,
        "selected_protocol": result.selected_protocol,
        "status": result.status.value,
        "tested_protocol": result.tested_protocol,
        "address": result.address,
        "port": result.port,
        "latency_ms": result.latency_ms,
        "used_tcp_counterpart": result.used_tcp_counterpart,
        "port_from_range": result.port_from_range,
        "detail": result.detail,
        "checked_at": validated.checked_at,
    }


def _saved_probe_from_dict(value: object) -> SavedEndpointProbe:
    if not isinstance(value, dict):
        raise TypeError("endpoint latency record must be an object")
    server_id = _bounded_integer(value.get("server_id"), "server_id", 1, 2**31 - 1)
    server_name = _bounded_text(
        value.get("server_name"),
        "server_name",
        maximum=MAX_SERVER_NAME_LENGTH,
        allow_empty=False,
    )
    selected_protocol = _bounded_integer(
        value.get("selected_protocol"), "selected_protocol", 0, 3
    )
    try:
        status = EndpointProbeStatus(value.get("status"))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid endpoint latency status") from exc

    tested_value = value.get("tested_protocol")
    tested_protocol = (
        None
        if tested_value is None
        else _bounded_integer(tested_value, "tested_protocol", 0, 3)
    )
    address_value = value.get("address")
    address = None if address_value is None else _validated_address(address_value)
    port_value = value.get("port")
    port = (
        None if port_value is None else _bounded_integer(port_value, "port", 1, 65535)
    )
    latency_value = value.get("latency_ms")
    latency_ms = None if latency_value is None else _validated_latency(latency_value)
    used_tcp_counterpart = _strict_boolean(
        value.get("used_tcp_counterpart"), "used_tcp_counterpart"
    )
    port_from_range = _strict_boolean(value.get("port_from_range"), "port_from_range")
    detail = _bounded_text(
        value.get("detail"), "detail", maximum=MAX_DETAIL_LENGTH, allow_empty=True
    )
    checked_at = _bounded_integer(value.get("checked_at"), "checked_at", 0, 2**63 - 1)

    if status is EndpointProbeStatus.REACHABLE:
        if (
            latency_ms is None
            or tested_protocol is None
            or address is None
            or port is None
        ):
            raise ValueError("reachable endpoint latency record is incomplete")
    elif latency_ms is not None:
        raise ValueError("only reachable endpoint records may contain latency")
    if status is EndpointProbeStatus.UNREACHABLE and (
        tested_protocol is None or address is None or port is None
    ):
        raise ValueError("unreachable endpoint latency record is incomplete")

    return SavedEndpointProbe(
        result=EndpointProbeResult(
            server_id=server_id,
            server_name=server_name,
            selected_protocol=selected_protocol,
            status=status,
            tested_protocol=tested_protocol,
            address=address,
            port=port,
            latency_ms=latency_ms,
            used_tcp_counterpart=used_tcp_counterpart,
            port_from_range=port_from_range,
            detail=detail,
        ),
        checked_at=checked_at,
    )


def _bounded_integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _bounded_text(
    value: object,
    name: str,
    *,
    maximum: int,
    allow_empty: bool,
) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{name} must be text no longer than {maximum} characters")
    if not allow_empty and not value:
        raise ValueError(f"{name} cannot be empty")
    return value


def _validated_address(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("endpoint address must be text")
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValueError("endpoint address is not valid IPv4") from exc
    if address.is_unspecified or address.is_multicast or int(address) == 0xFFFFFFFF:
        raise ValueError("endpoint address is not connectable")
    return str(address)


def _validated_latency(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("latency_ms must be numeric")
    latency = float(value)
    if not math.isfinite(latency) or latency < 0 or latency > MAX_SAVED_LATENCY_MS:
        raise ValueError(
            f"latency_ms must be finite and between 0 and {MAX_SAVED_LATENCY_MS:g}"
        )
    return latency


def _strict_boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"unsupported JSON number: {value}")
