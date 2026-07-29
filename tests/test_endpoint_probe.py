from __future__ import annotations

import threading
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from astrill_lazy.astrill import (
    AstrillEndpoint,
    AstrillNode,
    AstrillServer,
    parse_applet,
)
from astrill_lazy.endpoint_probe import (
    MAX_PROBE_WORKERS,
    EndpointProbeStatus,
    prepare_endpoint_probe,
    probe_endpoint,
    probe_servers,
    select_tcp_probe_port,
)


def _server(
    server_id: int,
    name: str,
    *,
    address: str | None = "67.43.53.5",
    port: str = "8292",
    mode: int = 1,
    router_pro: bool = False,
) -> AstrillServer:
    return AstrillServer(
        id=server_id,
        name=name,
        nodes=(
            AstrillNode(
                id=server_id + 100,
                weight=1,
                endpoints=(
                    AstrillEndpoint(
                        encoded_ip=536872392 + server_id,
                        port=port,
                        mode=mode,
                        protocol_code=(128 if router_pro else 0) | 6,
                        port_index=0,
                        resolved_ip=address,
                    ),
                ),
            ),
        ),
    )


def test_applet_resolves_opaque_endpoint_tokens_from_validated_mapping() -> None:
    script = (
        "var opaque=';536872392=67.43.53.4;"
        "402654664=67.43.53.5;999=999.43.53.5;';"
        "this.list = [{id:1,name:'USA - Test',servers:["
        "{id:7,lf:1,ips:["
        "{ip:536872392,port:'8292',mode:1,proto:6,index:0},"
        "{ip:402654664,port:'1-65535',mode:1,proto:134,index:0},"
        "{ip:999,port:'443',mode:0,proto:6,index:0}"
        "]}]}];"
    )

    server = parse_applet(script.encode())[0]
    endpoints = server.nodes[0].endpoints

    assert endpoints[0].encoded_ip == 536872392
    assert endpoints[0].resolved_ip == "67.43.53.4"
    assert endpoints[1].resolved_ip == "67.43.53.5"
    assert endpoints[2].resolved_ip is None


def test_applet_without_address_mapping_remains_compatible() -> None:
    script = (
        "this.list = [{id:1,name:'USA - Test',servers:["
        "{id:7,lf:1,ips:["
        "{ip:536872392,port:'8292',mode:1,proto:6,index:0}"
        "]}]}];"
    )

    endpoint = parse_applet(script.encode())[0].nodes[0].endpoints[0]

    assert endpoint.encoded_ip == 536872392
    assert endpoint.resolved_ip is None


def test_conflicting_token_address_mapping_is_rejected() -> None:
    script = (
        "var opaque=';536872392=67.43.53.4;536872392=67.43.53.5;';"
        "this.list = [{id:1,name:'USA - Test',servers:["
        "{id:7,lf:1,ips:["
        "{ip:536872392,port:'8292',mode:1,proto:6,index:0}"
        "]}]}];"
    )

    with pytest.raises(ValueError, match="conflicting addresses"):
        parse_applet(script.encode())


@pytest.mark.parametrize(
    ("specification", "expected_port", "from_range"),
    [
        ("8292", 8292, False),
        ("8292,9000-9100", 8292, False),
        ("1-65535", 443, True),
        ("8000:9000", 8000, True),
    ],
)
def test_tcp_probe_port_selection(
    specification: str, expected_port: int, from_range: bool
) -> None:
    selection = select_tcp_probe_port(specification)

    assert selection.value == expected_port
    assert selection.from_range is from_range


@pytest.mark.parametrize(
    ("specification", "message"),
    [
        ("", "empty or malformed"),
        ("443,", "empty or malformed"),
        ("0", "outside 1..65535"),
        ("65536", "outside 1..65535"),
        ("9000-8000", "reversed"),
        ("443/udp", "unsupported"),
    ],
)
def test_invalid_tcp_probe_port_values_are_clear(
    specification: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        select_tcp_probe_port(specification)


def test_udp_selection_uses_same_family_tcp_endpoint() -> None:
    server = _server(
        1,
        "USA - TCP counterpart",
        address="67.43.53.5",
        port="1-65535",
        mode=1,
    )

    target = prepare_endpoint_probe(server, selected_protocol=0)

    assert target.selected_protocol == 0
    assert target.tested_protocol == 1
    assert target.used_tcp_counterpart
    assert target.address == "67.43.53.5"
    assert target.port == 443
    assert target.port_from_range


def test_missing_mapping_is_unavailable_without_opening_a_socket() -> None:
    calls: list[tuple[tuple[str, int], float]] = []

    def connector(address: tuple[str, int], timeout: float) -> Any:
        calls.append((address, timeout))
        raise AssertionError("an unavailable target must not be connected")

    results = probe_servers(
        [_server(1, "No mapping", address=None)],
        selected_protocol=1,
        connector=connector,
    )

    assert len(results) == 1
    assert results[0].status is EndpointProbeStatus.UNAVAILABLE
    assert "did not provide an IPv4 mapping" in results[0].detail
    assert calls == []


def test_reachable_probe_reports_tcp_connect_latency_and_closes_socket() -> None:
    connection = SimpleNamespace(closed=False)

    def close() -> None:
        connection.closed = True

    connection.close = close
    calls: list[tuple[tuple[str, int], float]] = []

    def connector(address: tuple[str, int], timeout: float) -> Any:
        calls.append((address, timeout))
        return connection

    clock_values: Iterator[float] = iter((10.0, 10.04234))
    target = prepare_endpoint_probe(_server(1, "USA - Reachable"), selected_protocol=1)

    result = probe_endpoint(
        target,
        timeout_seconds=1.25,
        connector=connector,
        clock=lambda: next(clock_values),
    )

    assert result.status is EndpointProbeStatus.REACHABLE
    assert result.latency_ms == 42.3
    assert result.address == "67.43.53.5"
    assert result.port == 8292
    assert calls == [(("67.43.53.5", 8292), 1.25)]
    assert connection.closed


@pytest.mark.parametrize(
    ("error", "expected_detail"),
    [
        (TimeoutError(), "timed out after 1.5 seconds"),
        (ConnectionRefusedError(10061, "Connection refused"), "Connection refused"),
    ],
)
def test_unreachable_probe_has_no_latency(error: OSError, expected_detail: str) -> None:
    def connector(_address: tuple[str, int], _timeout: float) -> Any:
        raise error

    target = prepare_endpoint_probe(
        _server(1, "USA - Unreachable"), selected_protocol=1
    )

    result = probe_endpoint(target, connector=connector)

    assert result.status is EndpointProbeStatus.UNREACHABLE
    assert result.latency_ms is None
    assert expected_detail in result.detail


def test_probe_batch_preserves_order_and_bounds_concurrency() -> None:
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    active = 0
    maximum_active = 0

    def connector(_address: tuple[str, int], _timeout: float) -> Any:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        barrier.wait(timeout=2)
        with lock:
            active -= 1
        return SimpleNamespace(close=lambda: None)

    servers = [_server(index, f"Server {index}") for index in range(1, 5)]
    results = probe_servers(
        servers,
        selected_protocol=1,
        max_workers=2,
        connector=connector,
    )

    assert [result.server_id for result in results] == [1, 2, 3, 4]
    assert all(result.status is EndpointProbeStatus.REACHABLE for result in results)
    assert maximum_active == 2


def test_probe_limits_are_validated_before_starting_work() -> None:
    server = _server(1, "USA - Limits")

    with pytest.raises(ValueError, match="at most 10 seconds"):
        probe_servers([server], 1, timeout_seconds=11)
    with pytest.raises(ValueError, match=f"between 1 and {MAX_PROBE_WORKERS}"):
        probe_servers([server], 1, max_workers=MAX_PROBE_WORKERS + 1)
