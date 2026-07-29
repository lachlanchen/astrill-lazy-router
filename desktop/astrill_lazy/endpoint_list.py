"""Pure endpoint-list ordering used by the native Windows frontend."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .astrill import AstrillServer
from .endpoint_probe import EndpointProbeStatus
from .endpoint_probe_store import (
    SavedEndpointProbe,
    SavedProbeState,
    assess_saved_endpoint_probe,
)

ENDPOINT_SORT_MODES = frozenset({"default", "region", "latency"})


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
