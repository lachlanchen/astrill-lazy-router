from __future__ import annotations

import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Literal

MAX_LATENCY_TARGETS = 256
DEFAULT_LATENCY_TIMEOUT = 1.5
DEFAULT_LATENCY_WORKERS = 12
EndpointSortField = Literal["applet", "country", "latency"]


@dataclass(frozen=True)
class LatencyTarget:
    server_id: int
    address: str
    port: int

    def validate(self) -> None:
        if self.server_id <= 0:
            raise ValueError("latency target server ID must be positive")
        ipaddress.IPv4Address(self.address)
        if not 1 <= self.port <= 65535:
            raise ValueError("latency target port must be between 1 and 65535")


def probe_endpoint_latencies(
    targets: list[LatencyTarget],
    *,
    timeout: float = DEFAULT_LATENCY_TIMEOUT,
    workers: int = DEFAULT_LATENCY_WORKERS,
) -> dict[int, float | None]:
    if len(targets) > MAX_LATENCY_TARGETS:
        raise ValueError(
            f"endpoint latency is limited to {MAX_LATENCY_TARGETS} targets"
        )
    if not 0.1 <= timeout <= 10:
        raise ValueError("endpoint latency timeout must be between 0.1 and 10 seconds")
    if not 1 <= workers <= 32:
        raise ValueError("endpoint latency workers must be between 1 and 32")

    seen: set[int] = set()
    for target in targets:
        target.validate()
        if target.server_id in seen:
            raise ValueError(f"duplicate latency target for server {target.server_id}")
        seen.add(target.server_id)

    results: dict[int, float | None] = {}
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(targets)))) as pool:
        futures = {
            pool.submit(_probe_target, target, timeout): target for target in targets
        }
        for future in as_completed(futures):
            target = futures[future]
            results[target.server_id] = future.result()
    return results


def sort_endpoint_ids(
    endpoint_ids: list[int],
    *,
    names: dict[int, str],
    countries: dict[int, str],
    latencies: dict[int, float | None],
    pending: set[int],
    field: EndpointSortField,
    descending: bool = False,
) -> list[int]:
    if field == "applet":
        return list(endpoint_ids)
    if field not in {"country", "latency"}:
        raise ValueError(f"unsupported endpoint sort field: {field}")

    def text_key(server_id: int) -> tuple[str, str, int]:
        return (
            countries.get(server_id, "Other").casefold(),
            names.get(server_id, "").casefold(),
            server_id,
        )

    if field == "country":
        return sorted(endpoint_ids, key=text_key, reverse=descending)

    measured = [
        server_id
        for server_id in endpoint_ids
        if server_id not in pending
        and server_id in latencies
        and latencies[server_id] is not None
    ]
    measured.sort(
        key=lambda server_id: (
            (
                -float(latencies[server_id])
                if descending
                else float(latencies[server_id])
            ),
            *text_key(server_id),
        )
    )

    unavailable = [server_id for server_id in endpoint_ids if server_id not in measured]

    def unavailable_key(server_id: int) -> tuple[int, str, str, int]:
        if server_id in pending:
            state = 0
        elif server_id in latencies:
            state = 1
        else:
            state = 2
        return state, *text_key(server_id)

    unavailable.sort(key=unavailable_key)
    return measured + unavailable


def _probe_target(target: LatencyTarget, timeout: float) -> float | None:
    started = time.monotonic()
    try:
        connection = socket.create_connection(
            (target.address, target.port),
            timeout=timeout,
        )
    except OSError:
        return None
    elapsed = max(0.0, time.monotonic() - started)
    connection.close()
    return round(elapsed * 1000, 1)
