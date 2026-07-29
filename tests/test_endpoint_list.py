from __future__ import annotations

from astrill_lazy.astrill import AstrillEndpoint, AstrillNode, AstrillServer
from astrill_lazy.endpoint_list import EndpointListRow, sort_endpoint_rows
from astrill_lazy.endpoint_probe import EndpointProbeResult, EndpointProbeStatus
from astrill_lazy.endpoint_probe_store import (
    STALE_AFTER_SECONDS,
    SavedEndpointProbe,
)

NOW = 1_800_000_000


def _row(
    source_index: int,
    server_id: int,
    region_id: str,
    region_name: str,
) -> EndpointListRow:
    return EndpointListRow(
        source_index=source_index,
        server=AstrillServer(
            id=server_id,
            name=f"Endpoint {server_id}",
            nodes=(
                AstrillNode(
                    id=server_id + 100,
                    weight=1,
                    endpoints=(
                        AstrillEndpoint(
                            encoded_ip=536872392 + server_id,
                            port="443",
                            mode=1,
                            protocol_code=6,
                            port_index=0,
                            resolved_ip=f"67.43.53.{server_id}",
                        ),
                    ),
                ),
            ),
        ),
        region_id=region_id,
        region_name=region_name,
    )


def _saved(
    row: EndpointListRow,
    status: EndpointProbeStatus,
    *,
    latency_ms: float | None = None,
    checked_at: int = NOW,
) -> SavedEndpointProbe:
    has_target = status is not EndpointProbeStatus.UNAVAILABLE
    return SavedEndpointProbe(
        result=EndpointProbeResult(
            server_id=row.server.id,
            server_name=row.server.name,
            selected_protocol=1,
            tested_protocol=1 if has_target else None,
            address=(
                row.server.nodes[0].endpoints[0].resolved_ip if has_target else None
            ),
            port=443 if has_target else None,
            status=status,
            latency_ms=latency_ms,
            detail="test",
        ),
        checked_at=checked_at,
    )


def test_default_sort_restores_exact_astrill_source_order() -> None:
    rows = (_row(2, 3, "eu", "Europe"), _row(0, 1, "us", "United States"))

    ordered = sort_endpoint_rows(rows, "default", {}, 1, now=NOW)

    assert [row.server.id for row in ordered] == [1, 3]


def test_region_sort_is_case_insensitive_and_puts_unknown_last() -> None:
    rows = (
        _row(0, 1, "us", "united States"),
        _row(1, 2, "", "Unknown"),
        _row(2, 3, "eu", "Europe"),
    )

    ordered = sort_endpoint_rows(rows, "region", {}, 1, now=NOW)

    assert [row.server.id for row in ordered] == [3, 1, 2]


def test_latency_sort_is_numeric_and_groups_result_states() -> None:
    fast = _row(0, 1, "us", "United States")
    slow = _row(1, 2, "us", "United States")
    unreachable = _row(2, 3, "eu", "Europe")
    unavailable = _row(3, 4, "eu", "Europe")
    stale = _row(4, 5, "eu", "Europe")
    untested = _row(5, 6, "eu", "Europe")
    results = {
        (fast.server.id, 1): _saved(
            fast, EndpointProbeStatus.REACHABLE, latency_ms=9.5
        ),
        (slow.server.id, 1): _saved(
            slow, EndpointProbeStatus.REACHABLE, latency_ms=100.0
        ),
        (unreachable.server.id, 1): _saved(
            unreachable, EndpointProbeStatus.UNREACHABLE
        ),
        (unavailable.server.id, 1): _saved(
            unavailable, EndpointProbeStatus.UNAVAILABLE
        ),
        (stale.server.id, 1): _saved(
            stale,
            EndpointProbeStatus.REACHABLE,
            latency_ms=1.0,
            checked_at=NOW - STALE_AFTER_SECONDS - 1,
        ),
    }

    ordered = sort_endpoint_rows(
        (untested, stale, unavailable, slow, unreachable, fast),
        "latency",
        results,
        1,
        now=NOW,
    )

    assert [row.server.id for row in ordered] == [1, 2, 3, 4, 5, 6]


def test_latency_sort_does_not_reuse_another_protocol_result() -> None:
    first = _row(0, 1, "us", "United States")
    second = _row(1, 2, "us", "United States")
    results = {
        (second.server.id, 3): _saved(
            second, EndpointProbeStatus.REACHABLE, latency_ms=1.0
        )
    }

    ordered = sort_endpoint_rows(
        (first, second),
        "latency",
        results,
        1,
        now=NOW,
    )

    assert [row.server.id for row in ordered] == [1, 2]
