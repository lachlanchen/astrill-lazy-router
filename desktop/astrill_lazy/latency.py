from __future__ import annotations

import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

MAX_LATENCY_TARGETS = 256
DEFAULT_LATENCY_TIMEOUT = 1.5
DEFAULT_LATENCY_WORKERS = 12


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
