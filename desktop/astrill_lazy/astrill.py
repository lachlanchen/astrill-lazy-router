from __future__ import annotations

import gzip
import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass

from .models import Region

ASTRILL_PROTOCOL_NAMES = (
    "OpenVPN UDP",
    "OpenVPN TCP",
    "RouterPro VPN UDP",
    "RouterPro VPN TCP",
)

TAIL_RE = re.compile(rb"tail -c\s+(\d+)")
SERVER_HEAD_RE = re.compile(r"id:(\d+),name:'((?:\\.|[^'])*)'")
INNER_SERVER_RE = re.compile(r"\{id:(\d+),lf:(\d+),ips:\[")
IP_RE = re.compile(
    r"\{ip:(-?\d+),port:(?:'([^']+)'|(\d+)),mode:(\d+),"
    r"proto:(\d+),index:(\d+)(?:,protop:(\d+))?\}"
)
ENDPOINT_ADDRESS_RE = re.compile(r"(?:^|;)(-?\d+)=((?:\d{1,3}\.){3}\d{1,3})(?=;)")


@dataclass(frozen=True)
class AstrillEndpoint:
    encoded_ip: int
    port: str
    mode: int
    protocol_code: int
    port_index: int
    protocol_original: int | None = None
    resolved_ip: str | None = None

    @property
    def router_pro(self) -> bool:
        return bool(self.protocol_code & 128)

    @property
    def vpn_mode(self) -> int:
        return self.protocol_code & 127

    def vpn_mode_for(self, protocol: int) -> int:
        code = (
            self.protocol_code
            if protocol & 2 or self.protocol_original is None
            else self.protocol_original
        )
        return code & 127


@dataclass(frozen=True)
class AstrillNode:
    id: int
    weight: int
    endpoints: tuple[AstrillEndpoint, ...]


@dataclass(frozen=True)
class AstrillServer:
    id: int
    name: str
    nodes: tuple[AstrillNode, ...]

    def endpoint_for(
        self, protocol: int, port_index: int = 0
    ) -> tuple[int, AstrillEndpoint]:
        wanted_mode = protocol & 1
        wanted_router_pro = bool(protocol & 2)
        for node in self.nodes:
            for endpoint in node.endpoints:
                if (
                    endpoint.mode == wanted_mode
                    and endpoint.router_pro == wanted_router_pro
                    and endpoint.port_index == port_index
                ):
                    return node.id, endpoint
        for node in self.nodes:
            for endpoint in node.endpoints:
                if (
                    endpoint.mode == wanted_mode
                    and endpoint.router_pro == wanted_router_pro
                ):
                    return node.id, endpoint
        raise ValueError(
            f"{self.name} does not support Astrill protocol mode {protocol}"
        )


def parse_applet(payload: bytes) -> tuple[AstrillServer, ...]:
    script = unpack_applet(payload)
    endpoint_addresses = _extract_endpoint_addresses(script)
    literal = _extract_list_literal(script)
    servers = tuple(
        _parse_server(item, endpoint_addresses)
        for item in _split_top_level_objects(literal)
    )
    if not servers:
        raise ValueError("Astrill server list was empty")
    return servers


def unpack_applet(payload: bytes) -> str:
    match = TAIL_RE.search(payload[:2048])
    if match is None:
        text = payload.decode("utf-8", errors="strict")
        if "this.list = [" not in text:
            raise ValueError("unrecognized Astrill applet format")
        return text
    tail_size = int(match.group(1))
    if tail_size <= 0 or tail_size > len(payload):
        raise ValueError("invalid compressed payload length")
    try:
        return gzip.decompress(payload[-tail_size:]).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("could not unpack the Astrill applet") from exc


def group_by_region(
    servers: Iterable[AstrillServer], regions: Iterable[Region]
) -> dict[str, tuple[AstrillServer, ...]]:
    grouped: dict[str, list[AstrillServer]] = {
        region.id: [] for region in regions if region.kind == "astrill"
    }
    astrill_regions = [region for region in regions if region.kind == "astrill"]
    for server in servers:
        destination = "other"
        for region in astrill_regions:
            if region.id == "other":
                continue
            if any(
                token.casefold() in server.name.casefold() for token in region.match
            ):
                destination = region.id
                break
        grouped.setdefault(destination, []).append(server)
    return {key: tuple(value) for key, value in grouped.items()}


def _extract_list_literal(script: str) -> str:
    marker = "this.list = ["
    marker_index = script.find(marker)
    if marker_index < 0:
        raise ValueError("Astrill server list marker was not found")
    start = marker_index + len("this.list = ")
    end = _matching_bracket(script, start)
    return script[start + 1 : end]


def _matching_bracket(text: str, start: int) -> int:
    if text[start] != "[":
        raise ValueError("server list did not start with an array")
    depth = 0
    quote_char = ""
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if quote_char:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote_char:
                quote_char = ""
            continue
        if character in {"'", '"'}:
            quote_char = character
        elif character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("unterminated Astrill server list")


def _split_top_level_objects(literal: str) -> Iterable[str]:
    depth = 0
    start: int | None = None
    quote_char = ""
    escaped = False
    for index, character in enumerate(literal):
        if quote_char:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote_char:
                quote_char = ""
            continue
        if character in {"'", '"'}:
            quote_char = character
        elif character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0 and start is not None:
                yield literal[start : index + 1]
                start = None
        if depth < 0:
            raise ValueError("malformed Astrill server object")
    if depth:
        raise ValueError("unterminated Astrill server object")


def _parse_server(
    value: str, endpoint_addresses: dict[int, str] | None = None
) -> AstrillServer:
    head = SERVER_HEAD_RE.search(value)
    if head is None:
        raise ValueError("malformed Astrill server header")
    server_id = int(head.group(1))
    name = head.group(2).replace("\\'", "'").replace("\\\\", "\\")
    nodes: list[AstrillNode] = []

    for node_match in INNER_SERVER_RE.finditer(value):
        node_start = node_match.end() - 1
        node_end = _matching_bracket(value, node_start)
        endpoints = tuple(
            AstrillEndpoint(
                encoded_ip=int(match.group(1)),
                port=match.group(2) or match.group(3),
                mode=int(match.group(4)),
                protocol_code=int(match.group(5)),
                port_index=int(match.group(6)),
                protocol_original=(
                    int(match.group(7)) if match.group(7) is not None else None
                ),
                resolved_ip=(
                    endpoint_addresses.get(int(match.group(1)))
                    if endpoint_addresses
                    else None
                ),
            )
            for match in IP_RE.finditer(value[node_start + 1 : node_end])
        )
        nodes.append(
            AstrillNode(
                id=int(node_match.group(1)),
                weight=int(node_match.group(2)),
                endpoints=endpoints,
            )
        )
    if not nodes:
        raise ValueError(f"Astrill server {name!r} has no nodes")
    return AstrillServer(id=server_id, name=name, nodes=tuple(nodes))


def _extract_endpoint_addresses(script: str) -> dict[int, str]:
    addresses: dict[int, str] = {}
    for match in ENDPOINT_ADDRESS_RE.finditer(script):
        try:
            address = str(ipaddress.IPv4Address(match.group(2)))
        except ipaddress.AddressValueError:
            continue
        token = int(match.group(1))
        existing = addresses.get(token)
        if existing is not None and existing != address:
            raise ValueError(
                f"Astrill endpoint token {token} maps to conflicting addresses"
            )
        addresses[token] = address
    return addresses
