"""Manual, local-PC latency probes for Astrill endpoints.

This module has no timers and never talks to the router. Network activity occurs
only when a caller explicitly invokes ``probe_endpoint`` or ``probe_servers``.
The probe is a single TCP connect, which is more representative of VPN endpoint
reachability than ICMP and still works when a server drops ICMP echo requests.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .astrill import AstrillEndpoint, AstrillServer

DEFAULT_PROBE_TIMEOUT_SECONDS = 1.5
DEFAULT_MAX_PROBE_WORKERS = 8
MAX_PROBE_TIMEOUT_SECONDS = 10.0
MAX_PROBE_WORKERS = 16
MAX_PORT_ENTRIES = 32

_PORT_ENTRY_RE = re.compile(r"^(\d+)(?:\s*[-:]\s*(\d+))?$")

Connector = Callable[[tuple[str, int], float], Any]
Clock = Callable[[], float]


class EndpointProbeStatus(StrEnum):
    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class EndpointProbePort:
    value: int
    from_range: bool


@dataclass(frozen=True)
class EndpointProbeTarget:
    server_id: int
    server_name: str
    node_id: int
    encoded_ip: int
    address: str
    port: int
    selected_protocol: int
    tested_protocol: int
    used_tcp_counterpart: bool
    port_from_range: bool


@dataclass(frozen=True)
class EndpointProbeResult:
    server_id: int
    server_name: str
    selected_protocol: int
    status: EndpointProbeStatus
    tested_protocol: int | None = None
    address: str | None = None
    port: int | None = None
    latency_ms: float | None = None
    used_tcp_counterpart: bool = False
    port_from_range: bool = False
    detail: str = ""


def select_tcp_probe_port(value: str) -> EndpointProbePort:
    """Select one deterministic TCP port from an Astrill port specification."""
    if not isinstance(value, str):
        raise TypeError("endpoint port specification must be text")
    entries = [entry.strip() for entry in value.split(",")]
    if not entries or any(not entry for entry in entries):
        raise ValueError("endpoint port specification is empty or malformed")
    if len(entries) > MAX_PORT_ENTRIES:
        raise ValueError(
            f"endpoint port specification has more than {MAX_PORT_ENTRIES} entries"
        )

    exact: list[int] = []
    ranges: list[tuple[int, int]] = []
    for entry in entries:
        match = _PORT_ENTRY_RE.fullmatch(entry)
        if match is None:
            raise ValueError(f"unsupported endpoint port value: {value!r}")
        start = _validated_port(match.group(1))
        end_text = match.group(2)
        if end_text is None:
            exact.append(start)
            continue
        end = _validated_port(end_text)
        if start > end:
            raise ValueError(f"endpoint port range is reversed: {entry!r}")
        ranges.append((start, end))

    if exact:
        return EndpointProbePort(exact[0], from_range=False)
    for start, end in ranges:
        if start <= 443 <= end:
            return EndpointProbePort(443, from_range=True)
    if ranges:
        return EndpointProbePort(ranges[0][0], from_range=True)
    raise ValueError("endpoint port specification did not contain a usable port")


def prepare_endpoint_probe(
    server: AstrillServer, selected_protocol: int
) -> EndpointProbeTarget:
    """Resolve a server's TCP probe target without opening a connection."""
    if (
        not isinstance(selected_protocol, int)
        or isinstance(selected_protocol, bool)
        or selected_protocol not in range(4)
    ):
        raise ValueError("selected Astrill protocol must be between 0 and 3")

    tested_protocol = selected_protocol | 1
    try:
        node_id, endpoint = server.endpoint_for(tested_protocol)
    except ValueError as exc:
        if tested_protocol != selected_protocol:
            raise ValueError(
                "the server has no same-family TCP endpoint for the selected UDP mode"
            ) from exc
        raise

    address = _validated_endpoint_address(endpoint)
    selected_port = select_tcp_probe_port(endpoint.port)
    return EndpointProbeTarget(
        server_id=server.id,
        server_name=server.name,
        node_id=node_id,
        encoded_ip=endpoint.encoded_ip,
        address=address,
        port=selected_port.value,
        selected_protocol=selected_protocol,
        tested_protocol=tested_protocol,
        used_tcp_counterpart=tested_protocol != selected_protocol,
        port_from_range=selected_port.from_range,
    )


def probe_endpoint(
    target: EndpointProbeTarget,
    *,
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    connector: Connector | None = None,
    clock: Clock | None = None,
) -> EndpointProbeResult:
    """Perform one bounded TCP-connect latency measurement from this computer."""
    timeout = _validated_timeout(timeout_seconds)
    connect = connector or socket.create_connection
    monotonic = clock or time.perf_counter
    started = monotonic()
    try:
        connection = connect((target.address, target.port), timeout)
    except TimeoutError:
        return _target_result(
            target,
            EndpointProbeStatus.UNREACHABLE,
            detail=f"TCP connection timed out after {timeout:g} seconds",
        )
    except OSError as exc:
        reason = exc.strerror or str(exc) or exc.__class__.__name__
        return _target_result(
            target,
            EndpointProbeStatus.UNREACHABLE,
            detail=f"TCP connection failed: {reason}",
        )

    finished = monotonic()
    close = getattr(connection, "close", None)
    if callable(close):
        close()
    return _target_result(
        target,
        EndpointProbeStatus.REACHABLE,
        latency_ms=round(max(0.0, finished - started) * 1000.0, 1),
        detail="TCP connection established",
    )


def probe_servers(
    servers: Iterable[AstrillServer],
    selected_protocol: int,
    *,
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    max_workers: int = DEFAULT_MAX_PROBE_WORKERS,
    connector: Connector | None = None,
    clock: Clock | None = None,
) -> tuple[EndpointProbeResult, ...]:
    """Probe servers concurrently, preserving input order.

    Concurrency is deliberately capped. Targets that cannot be resolved or lack
    a usable TCP endpoint are reported as unavailable without opening a socket.
    """
    timeout = _validated_timeout(timeout_seconds)
    workers = _validated_worker_count(max_workers)
    server_list = tuple(servers)
    if not server_list:
        return ()

    prepared: list[EndpointProbeTarget | EndpointProbeResult] = []
    for server in server_list:
        try:
            prepared.append(prepare_endpoint_probe(server, selected_protocol))
        except ValueError as exc:
            prepared.append(
                EndpointProbeResult(
                    server_id=server.id,
                    server_name=server.name,
                    selected_protocol=selected_protocol,
                    status=EndpointProbeStatus.UNAVAILABLE,
                    detail=str(exc),
                )
            )

    def run(
        item: EndpointProbeTarget | EndpointProbeResult,
    ) -> EndpointProbeResult:
        if isinstance(item, EndpointProbeResult):
            return item
        return probe_endpoint(
            item,
            timeout_seconds=timeout,
            connector=connector,
            clock=clock,
        )

    with ThreadPoolExecutor(
        max_workers=min(workers, len(prepared)),
        thread_name_prefix="endpoint-probe",
    ) as executor:
        return tuple(executor.map(run, prepared))


def _validated_endpoint_address(endpoint: AstrillEndpoint) -> str:
    if not endpoint.resolved_ip:
        raise ValueError(
            "the applet did not provide an IPv4 mapping for this endpoint token"
        )
    try:
        address = ipaddress.IPv4Address(endpoint.resolved_ip)
    except ipaddress.AddressValueError as exc:
        raise ValueError("the endpoint has an invalid resolved IPv4 address") from exc
    if address.is_unspecified or address.is_multicast or int(address) == 0xFFFFFFFF:
        raise ValueError(f"the endpoint IPv4 address {address} is not connectable")
    return str(address)


def _validated_port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError(f"endpoint port is outside 1..65535: {value!r}")
    return port


def _validated_timeout(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 < float(value) <= MAX_PROBE_TIMEOUT_SECONDS
    ):
        raise ValueError(
            f"probe timeout must be greater than 0 and at most "
            f"{MAX_PROBE_TIMEOUT_SECONDS:g} seconds"
        )
    return float(value)


def _validated_worker_count(value: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= MAX_PROBE_WORKERS
    ):
        raise ValueError(
            f"probe worker count must be between 1 and {MAX_PROBE_WORKERS}"
        )
    return value


def _target_result(
    target: EndpointProbeTarget,
    status: EndpointProbeStatus,
    *,
    latency_ms: float | None = None,
    detail: str,
) -> EndpointProbeResult:
    notes: list[str] = []
    if target.used_tcp_counterpart:
        notes.append("used the same-family TCP endpoint for the selected UDP mode")
    if target.port_from_range:
        notes.append(f"selected port {target.port} from the advertised range")
    if notes:
        detail = f"{detail}; {'; '.join(notes)}"
    return EndpointProbeResult(
        server_id=target.server_id,
        server_name=target.server_name,
        selected_protocol=target.selected_protocol,
        tested_protocol=target.tested_protocol,
        address=target.address,
        port=target.port,
        status=status,
        latency_ms=latency_ms,
        used_tcp_counterpart=target.used_tcp_counterpart,
        port_from_range=target.port_from_range,
        detail=detail,
    )
