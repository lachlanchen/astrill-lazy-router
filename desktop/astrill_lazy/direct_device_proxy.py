"""Temporary direct CONNECT proxy for one explicitly allowed LAN device.

The proxy never starts or discovers Astrill. It relays end-to-end TLS without
decrypting it and can override a small set of DNS answers when a provider-owned
edge selected by ordinary DNS is unreachable from one device.
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import re
import select
import socket
import socketserver
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Final

DEFAULT_CONNECT_TIMEOUT_SECONDS: Final = 6.0
DEFAULT_IDLE_TIMEOUT_SECONDS: Final = 30.0
DEFAULT_SESSION_TIMEOUT_SECONDS: Final = 120.0
MAX_HEADER_BYTES: Final = 16 * 1024
MAX_RELAY_BYTES: Final = 256 * 1024
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
LOGGER = logging.getLogger(__name__)


class DirectDeviceProxyError(ValueError):
    """Raised when a proxy policy or request is unsafe or malformed."""


def normalize_hostname(value: str) -> str:
    """Return one validated lowercase ASCII DNS hostname."""
    if not isinstance(value, str):
        raise DirectDeviceProxyError("hostname must be text")
    hostname = value.rstrip(".").lower()
    if not hostname or len(hostname) > 253:
        raise DirectDeviceProxyError("hostname length is invalid")
    labels = hostname.split(".")
    if len(labels) < 2 or any(_HOST_LABEL.fullmatch(label) is None for label in labels):
        raise DirectDeviceProxyError("hostname is not a valid public DNS name")
    return hostname


def parse_connect_authority(value: str) -> tuple[str, int]:
    """Parse a CONNECT authority while rejecting IP literals and odd ports."""
    if not isinstance(value, str) or value.count(":") != 1:
        raise DirectDeviceProxyError("CONNECT target must be hostname:port")
    raw_hostname, raw_port = value.rsplit(":", 1)
    try:
        ipaddress.ip_address(raw_hostname.strip("[]"))
    except ValueError:
        pass
    else:
        raise DirectDeviceProxyError("CONNECT target must use a DNS hostname")
    hostname = normalize_hostname(raw_hostname)
    if not raw_port.isascii() or not raw_port.isdecimal():
        raise DirectDeviceProxyError("CONNECT port is invalid")
    port = int(raw_port)
    if not 1 <= port <= 65535:
        raise DirectDeviceProxyError("CONNECT port is outside 1..65535")
    return hostname, port


def parse_override(value: str) -> tuple[str, ipaddress.IPv4Address]:
    """Parse one HOST=PUBLIC_IPV4 override."""
    if not isinstance(value, str) or value.count("=") != 1:
        raise DirectDeviceProxyError("override must be HOST=PUBLIC_IPV4")
    raw_hostname, raw_address = value.split("=", 1)
    hostname = normalize_hostname(raw_hostname)
    try:
        address = ipaddress.IPv4Address(raw_address)
    except ipaddress.AddressValueError as exc:
        raise DirectDeviceProxyError("override address must be IPv4") from exc
    if not address.is_global:
        raise DirectDeviceProxyError("override address must be globally routable")
    return hostname, address


def parse_source(value: str) -> ipaddress.IPv4Address:
    """Parse one exact LAN-device IPv4 address."""
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise DirectDeviceProxyError("allowed source must be IPv4") from exc
    if not address.is_private:
        raise DirectDeviceProxyError("allowed source must be a private LAN address")
    return address


@dataclass(frozen=True)
class DirectDeviceProxyPolicy:
    """Immutable access and destination policy for the relay."""

    allowed_sources: frozenset[ipaddress.IPv4Address]
    allowed_hosts: frozenset[str]
    allowed_ports: frozenset[int]
    overrides: dict[str, tuple[ipaddress.IPv4Address, ...]]
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS
    session_timeout_seconds: float = DEFAULT_SESSION_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not self.allowed_sources:
            raise DirectDeviceProxyError("at least one exact source is required")
        if not self.allowed_hosts or any(
            normalize_hostname(hostname) != hostname for hostname in self.allowed_hosts
        ):
            raise DirectDeviceProxyError("at least one exact hostname is required")
        if not self.allowed_ports or any(
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
            for port in self.allowed_ports
        ):
            raise DirectDeviceProxyError("allowed ports are invalid")
        for timeout in (
            self.connect_timeout_seconds,
            self.idle_timeout_seconds,
            self.session_timeout_seconds,
        ):
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise DirectDeviceProxyError("timeouts must be numeric")
            if not 0.1 <= float(timeout) <= 600.0:
                raise DirectDeviceProxyError(
                    "timeouts must be between 0.1 and 600 seconds"
                )
        if self.session_timeout_seconds < self.idle_timeout_seconds:
            raise DirectDeviceProxyError(
                "session timeout must not be shorter than idle timeout"
            )
        for hostname, addresses in self.overrides.items():
            if normalize_hostname(hostname) != hostname or not addresses:
                raise DirectDeviceProxyError("override map is invalid")
            if hostname not in self.allowed_hosts:
                raise DirectDeviceProxyError("override hostname is not allowlisted")
            if any(
                not isinstance(address, ipaddress.IPv4Address) or not address.is_global
                for address in addresses
            ):
                raise DirectDeviceProxyError(
                    "override map contains a non-public address"
                )

    def allows_source(self, value: str) -> bool:
        try:
            address = ipaddress.IPv4Address(value)
        except ipaddress.AddressValueError:
            return False
        return address in self.allowed_sources

    def candidate_addresses(self, hostname: str, port: int) -> tuple[str, ...]:
        """Resolve one destination and retain globally routable IPv4 only."""
        if port not in self.allowed_ports:
            raise DirectDeviceProxyError("CONNECT destination port is not allowed")
        hostname = normalize_hostname(hostname)
        if hostname not in self.allowed_hosts:
            raise DirectDeviceProxyError("CONNECT destination hostname is not allowed")
        overridden = self.overrides.get(hostname)
        if overridden:
            return tuple(str(address) for address in overridden)

        try:
            records = socket.getaddrinfo(
                hostname,
                port,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise DirectDeviceProxyError("destination DNS lookup failed") from exc
        addresses: list[str] = []
        for record in records:
            address = ipaddress.IPv4Address(record[4][0])
            if address.is_global and str(address) not in addresses:
                addresses.append(str(address))
        if not addresses:
            raise DirectDeviceProxyError("destination has no public IPv4 address")
        return tuple(addresses)


class _DirectProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        policy: DirectDeviceProxyPolicy,
    ) -> None:
        self.policy = policy
        super().__init__(address, DirectDeviceProxyHandler)


class DirectDeviceProxyHandler(BaseHTTPRequestHandler):
    """Relay CONNECT requests without reading or rewriting TLS payloads."""

    protocol_version = "HTTP/1.1"
    server_version = "AstrillLazyDirectDeviceProxy/1"
    sys_version = ""

    @property
    def policy(self) -> DirectDeviceProxyPolicy:
        return self.server.policy  # type: ignore[attr-defined,no-any-return]

    def handle_one_request(self) -> None:
        self.raw_requestline = self.rfile.readline(MAX_HEADER_BYTES + 1)
        if len(self.raw_requestline) > MAX_HEADER_BYTES:
            self.send_error(431, "Request header too large")
            return
        if not self.raw_requestline:
            self.close_connection = True
            return
        if not self.parse_request():
            return
        method_name = "do_" + self.command
        method = getattr(self, method_name, None)
        if method is None:
            self.send_error(405, "CONNECT required")
            return
        method()
        self.wfile.flush()

    def do_CONNECT(self) -> None:
        source = self.client_address[0]
        if not self.policy.allows_source(source):
            self.send_error(403, "Source device is not allowed")
            return
        try:
            hostname, port = parse_connect_authority(self.path)
            candidates = self.policy.candidate_addresses(hostname, port)
        except DirectDeviceProxyError as exc:
            self.send_error(403, str(exc))
            return

        upstream: socket.socket | None = None
        selected = ""
        for candidate in candidates:
            try:
                upstream = socket.create_connection(
                    (candidate, port),
                    timeout=self.policy.connect_timeout_seconds,
                )
            except OSError:
                continue
            selected = candidate
            break
        if upstream is None:
            self.send_error(502, "No direct provider endpoint was reachable")
            return

        LOGGER.info(
            "direct CONNECT source=%s host=%s port=%d endpoint=%s",
            source,
            hostname,
            port,
            selected,
        )
        try:
            self.send_response(200, "Connection Established")
            self.end_headers()
            self.wfile.flush()
            self._relay(upstream)
        finally:
            upstream.close()
            self.close_connection = True

    def _relay(self, upstream: socket.socket) -> None:
        client = self.connection
        client.setblocking(False)
        upstream.setblocking(False)
        sockets = {client: upstream, upstream: client}
        started = last_activity = time.monotonic()
        while sockets:
            now = time.monotonic()
            if now - started >= self.policy.session_timeout_seconds:
                return
            remaining_idle = self.policy.idle_timeout_seconds - (now - last_activity)
            if remaining_idle <= 0:
                return
            readable, _, exceptional = select.select(
                tuple(sockets),
                (),
                tuple(sockets),
                min(remaining_idle, 1.0),
            )
            if exceptional:
                return
            for source in readable:
                destination = sockets.get(source)
                if destination is None:
                    continue
                try:
                    payload = source.recv(MAX_RELAY_BYTES)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError:
                    return
                if not payload:
                    sockets.pop(source, None)
                    try:
                        destination.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    continue
                try:
                    destination.sendall(payload)
                except OSError:
                    return
                last_activity = time.monotonic()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def build_policy(
    source_values: list[str],
    host_values: list[str],
    override_values: list[str],
    *,
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
    session_timeout_seconds: float = DEFAULT_SESSION_TIMEOUT_SECONDS,
) -> DirectDeviceProxyPolicy:
    """Build an immutable policy from CLI-compatible text inputs."""
    sources = frozenset(parse_source(value) for value in source_values)
    hosts = frozenset(normalize_hostname(value) for value in host_values)
    override_lists: dict[str, list[ipaddress.IPv4Address]] = {}
    for value in override_values:
        hostname, address = parse_override(value)
        values = override_lists.setdefault(hostname, [])
        if address not in values:
            values.append(address)
    overrides = {hostname: tuple(values) for hostname, values in override_lists.items()}
    return DirectDeviceProxyPolicy(
        allowed_sources=sources,
        allowed_hosts=hosts,
        allowed_ports=frozenset({443}),
        overrides=overrides,
        connect_timeout_seconds=connect_timeout_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
        session_timeout_seconds=session_timeout_seconds,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="astrill-lazy-direct-proxy",
        description=(
            "Run a manual direct-only TLS CONNECT relay for exact LAN devices; "
            "this command never uses Astrill"
        ),
    )
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=18080)
    parser.add_argument("--allow-source", action="append", required=True)
    parser.add_argument("--allow-host", action="append", required=True)
    parser.add_argument("--override", action="append", default=[])
    parser.add_argument(
        "--connect-timeout", type=float, default=DEFAULT_CONNECT_TIMEOUT_SECONDS
    )
    parser.add_argument(
        "--idle-timeout", type=float, default=DEFAULT_IDLE_TIMEOUT_SECONDS
    )
    parser.add_argument(
        "--session-timeout", type=float, default=DEFAULT_SESSION_TIMEOUT_SECONDS
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.listen_port <= 65535:
        raise SystemExit("listen port must be inside 1..65535")
    policy = build_policy(
        args.allow_source,
        args.allow_host,
        args.override,
        connect_timeout_seconds=args.connect_timeout,
        idle_timeout_seconds=args.idle_timeout,
        session_timeout_seconds=args.session_timeout,
    )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    with _DirectProxyServer((args.listen_host, args.listen_port), policy) as server:
        LOGGER.info(
            "direct device proxy listening on %s:%d for %s; Astrill is not used",
            args.listen_host,
            args.listen_port,
            ",".join(str(value) for value in sorted(policy.allowed_sources)),
        )
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            LOGGER.info("direct device proxy stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
