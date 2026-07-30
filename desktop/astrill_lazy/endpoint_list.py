"""Pure endpoint-list ordering used by the native Windows frontend."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Any

from .astrill import AstrillServer
from .endpoint_probe import EndpointProbeStatus
from .endpoint_probe_store import (
    SavedEndpointProbe,
    SavedProbeState,
    assess_saved_endpoint_probe,
)

ENDPOINT_SORT_MODES = frozenset({"default", "region", "latency"})
ENDPOINT_HEADER_SORT_FIELDS = frozenset(
    {
        "selected",
        "endpoint",
        "region",
        "favorite",
        "server_id",
        "router_state",
        "nodes",
        "latency",
        "reach",
        "tested",
    }
)


@dataclass(frozen=True)
class EndpointListRow:
    source_index: int
    server: AstrillServer
    region_id: str
    region_name: str


def sort_endpoint_rows(
    rows: Iterable[EndpointListRow],
    mode: str,
    results: Mapping[tuple[int, int], SavedEndpointProbe],
    selected_protocol: int,
    *,
    now: int | None = None,
) -> tuple[EndpointListRow, ...]:
    """Sort model rows while retaining source order as the stable fallback."""
    row_list = tuple(rows)
    if mode not in ENDPOINT_SORT_MODES:
        raise ValueError(f"unsupported endpoint sort mode: {mode!r}")
    if mode == "default":
        return tuple(sorted(row_list, key=lambda row: row.source_index))
    if mode == "region":
        return tuple(
            sorted(
                row_list,
                key=lambda row: (
                    not bool(row.region_id),
                    row.region_name.casefold(),
                    row.server.name.casefold(),
                    row.server.id,
                    row.source_index,
                ),
            )
        )
    return tuple(
        sorted(
            row_list,
            key=lambda row: _latency_sort_key(
                row,
                results.get((row.server.id, selected_protocol)),
                selected_protocol,
                now=now,
            ),
        )
    )


def sort_endpoint_rows_by_header(
    rows: Iterable[EndpointListRow],
    field: str,
    descending: bool,
    results: Mapping[tuple[int, int], SavedEndpointProbe],
    selected_protocol: int,
    *,
    selected_server_ids: AbstractSet[int] = frozenset(),
    favorite_server_ids: AbstractSet[int] | None = None,
    current_server_id: int = 0,
    connected: bool = False,
    now: int | None = None,
) -> tuple[EndpointListRow, ...]:
    """Sort a rendered endpoint column with semantic rather than text values.

    Rows without a meaningful value stay at the end in both directions, and
    Astrill's catalog order is the stable tie breaker.
    """

    if field not in ENDPOINT_HEADER_SORT_FIELDS:
        raise ValueError(f"unsupported endpoint header sort field: {field!r}")
    row_list = tuple(sorted(rows, key=lambda row: row.source_index))
    if field == "latency":
        return _sort_latency_header(
            row_list,
            descending,
            results,
            selected_protocol,
            now=now,
        )

    value_for_row: Callable[[EndpointListRow], Any | None]
    if field == "selected":
        value_for_row = lambda row: row.server.id in selected_server_ids
    elif field == "endpoint":
        value_for_row = lambda row: row.server.name.casefold()
    elif field == "region":
        value_for_row = lambda row: (
            row.region_name.casefold() if row.region_id else None
        )
    elif field == "favorite":
        if favorite_server_ids is None:
            return row_list
        value_for_row = lambda row: row.server.id in favorite_server_ids
    elif field == "server_id":
        value_for_row = lambda row: row.server.id
    elif field == "router_state":
        value_for_row = lambda row: (
            2
            if row.server.id == current_server_id and connected
            else 1
            if row.server.id == current_server_id
            else 0
        )
    elif field == "nodes":
        value_for_row = lambda row: len(row.server.nodes)
    elif field == "reach":
        value_for_row = lambda row: _reach_sort_value(
            row,
            results.get((row.server.id, selected_protocol)),
            selected_protocol,
            now=now,
        )
    else:
        value_for_row = lambda row: _tested_sort_value(
            row,
            results.get((row.server.id, selected_protocol)),
            selected_protocol,
        )
    return _sort_known_values(row_list, value_for_row, descending)


def _sort_known_values(
    rows: tuple[EndpointListRow, ...],
    value_for_row: Callable[[EndpointListRow], Any | None],
    descending: bool,
) -> tuple[EndpointListRow, ...]:
    known: list[EndpointListRow] = []
    missing: list[EndpointListRow] = []
    values: dict[int, Any] = {}
    for row in rows:
        value = value_for_row(row)
        if value is None:
            missing.append(row)
            continue
        values[row.source_index] = value
        known.append(row)
    known.sort(
        key=lambda row: values[row.source_index],
        reverse=descending,
    )
    return (*known, *missing)


def _sort_latency_header(
    rows: tuple[EndpointListRow, ...],
    descending: bool,
    results: Mapping[tuple[int, int], SavedEndpointProbe],
    selected_protocol: int,
    *,
    now: int | None,
) -> tuple[EndpointListRow, ...]:
    reachable: list[EndpointListRow] = []
    other: list[EndpointListRow] = []
    values: dict[int, float] = {}
    for row in rows:
        saved = results.get((row.server.id, selected_protocol))
        latency = _current_latency(
            row,
            saved,
            selected_protocol,
            now=now,
        )
        if latency is None:
            other.append(row)
            continue
        values[row.source_index] = latency
        reachable.append(row)
    reachable.sort(
        key=lambda row: values[row.source_index],
        reverse=descending,
    )
    other.sort(
        key=lambda row: _latency_sort_key(
            row,
            results.get((row.server.id, selected_protocol)),
            selected_protocol,
            now=now,
        )
    )
    return (*reachable, *other)


def _current_latency(
    row: EndpointListRow,
    saved: SavedEndpointProbe | None,
    selected_protocol: int,
    *,
    now: int | None,
) -> float | None:
    if saved is None:
        return None
    if (
        assess_saved_endpoint_probe(
            saved,
            row.server,
            selected_protocol,
            now=now,
        )
        is not SavedProbeState.CURRENT
    ):
        return None
    result = saved.result
    if (
        result.status is not EndpointProbeStatus.REACHABLE
        or result.latency_ms is None
        or not math.isfinite(result.latency_ms)
        or result.latency_ms < 0
    ):
        return None
    return result.latency_ms


def _reach_sort_value(
    row: EndpointListRow,
    saved: SavedEndpointProbe | None,
    selected_protocol: int,
    *,
    now: int | None,
) -> int | None:
    if saved is None:
        return None
    state = assess_saved_endpoint_probe(
        saved,
        row.server,
        selected_protocol,
        now=now,
    )
    if state is SavedProbeState.STALE:
        return 3
    if state is SavedProbeState.ENDPOINT_CHANGED:
        return 4
    if saved.result.status is EndpointProbeStatus.REACHABLE:
        return 0
    if saved.result.status is EndpointProbeStatus.UNREACHABLE:
        return 1
    if saved.result.status is EndpointProbeStatus.UNAVAILABLE:
        return 2
    return None


def _tested_sort_value(
    row: EndpointListRow,
    saved: SavedEndpointProbe | None,
    selected_protocol: int,
) -> int | None:
    if (
        saved is None
        or saved.result.server_id != row.server.id
        or saved.result.selected_protocol != selected_protocol
    ):
        return None
    return saved.checked_at


def _latency_sort_key(
    row: EndpointListRow,
    saved: SavedEndpointProbe | None,
    selected_protocol: int,
    *,
    now: int | None,
) -> tuple[int, float, int]:
    if saved is None:
        return (4, 0.0, row.source_index)
    state = assess_saved_endpoint_probe(
        saved,
        row.server,
        selected_protocol,
        now=now,
    )
    if state is not SavedProbeState.CURRENT:
        return (3, 0.0, row.source_index)
    result = saved.result
    if (
        result.status is EndpointProbeStatus.REACHABLE
        and result.latency_ms is not None
        and math.isfinite(result.latency_ms)
        and result.latency_ms >= 0
    ):
        return (0, result.latency_ms, row.source_index)
    if result.status is EndpointProbeStatus.UNREACHABLE:
        return (1, 0.0, row.source_index)
    if result.status is EndpointProbeStatus.UNAVAILABLE:
        return (2, 0.0, row.source_index)
    return (4, 0.0, row.source_index)
