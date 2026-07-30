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
PORT_RE = re.compile(r"^\d{1,5}(?:-\d{1,5})?$")
SERVER_COUNTRY_QUALIFIER_RE = re.compile(
    r"\s+(?:Supercharged(?:\s+\d+)?|10GB?(?:-\d+)?|[A-Z]{1,3}\d+|\d+)$"
)
SERVER_COUNTRY_ALIASES = {
    "usa": "United States",
    "uk": "United Kingdom",
    "korea": "South Korea",
    "czechia": "Czech Republic",
}
SERVER_CITY_COUNTRIES = {
    "buffalo": "United States",
    "los angeles": "United States",
    "seattle": "United States",
}


@dataclass(frozen=True)
class AstrillPortOption:
    index: int
    port: str


@dataclass(frozen=True)
class AstrillConnectionSelection:
    server_id: int
    sid: int
    encoded_ip: int
    port: str
    port_index: int
    protocol: int
    vpn_mode: int

    def __post_init__(self) -> None:
        if self.server_id <= 0 or self.sid <= 0:
            raise ValueError("Astrill server identifiers must be positive")
        if not -(2**31) <= self.encoded_ip < 2**31:
            raise ValueError("Astrill encoded address is outside the 32-bit range")
        _validate_port(self.port)
        if self.port_index < 0:
            raise ValueError("Astrill port index cannot be negative")
        if self.protocol not in range(len(ASTRILL_PROTOCOL_NAMES)):
            raise ValueError("Astrill protocol must be between 0 and 3")
        if not 0 <= self.vpn_mode <= 127:
            raise ValueError("Astrill VPN mode must be between 0 and 127")

    @classmethod
    def from_server(
        cls,
        server: AstrillServer,
        protocol: int,
        port_index: int,
    ) -> AstrillConnectionSelection:
        sid, endpoint = server.endpoint_for(protocol, port_index)
        return cls(
            server_id=server.id,
            sid=sid,
            encoded_ip=endpoint.encoded_ip,
            port=endpoint.port,
            port_index=endpoint.port_index,
            protocol=protocol,
            vpn_mode=endpoint.vpn_mode_for(protocol),
        )

    def native_values(self) -> dict[str, str]:
        return {
            "astrill_serverid": str(self.server_id),
            "astrill_sid": str(self.sid),
            "astrill_ip": str(self.encoded_ip),
            "astrill_port": self.port,
            "astrill_portindex": str(self.port_index),
            "astrill_protocol": str(self.protocol),
            "astrill_vpnmode": str(self.vpn_mode),
        }


@dataclass(frozen=True)
class AstrillFavorite:
    server_id: int
    encoded_ip: int
    port: str
    mode: int
    vpn_mode: int
    sid: int

    @classmethod
    def parse(cls, value: str) -> AstrillFavorite:
        parts = value.split(":")
        if len(parts) != 6:
            raise ValueError(f"invalid Astrill favorite record: {value!r}")
        try:
            favorite = cls(
                server_id=int(parts[0]),
                encoded_ip=int(parts[1]),
                port=parts[2],
                mode=int(parts[3]),
                vpn_mode=int(parts[4]),
                sid=int(parts[5]),
            )
        except ValueError as exc:
            raise ValueError(f"invalid Astrill favorite record: {value!r}") from exc
        favorite._validate()
        return favorite

    @classmethod
    def from_selection(cls, selection: AstrillConnectionSelection) -> AstrillFavorite:
        return cls(
            server_id=selection.server_id,
            encoded_ip=selection.encoded_ip,
            port=selection.port,
            mode=selection.protocol & 1,
            vpn_mode=selection.vpn_mode,
            sid=selection.sid,
        )

    def _validate(self) -> None:
        if self.server_id <= 0 or self.sid <= 0:
            raise ValueError("Astrill favorite identifiers must be positive")
        if not -(2**31) <= self.encoded_ip < 2**31:
            raise ValueError("Astrill favorite address is outside the 32-bit range")
        _validate_port(self.port)
        if self.mode not in {0, 1}:
            raise ValueError("Astrill favorite mode must be UDP or TCP")
        if not 0 <= self.vpn_mode <= 127:
            raise ValueError("Astrill favorite VPN mode must be between 0 and 127")

    def to_native(self) -> str:
        self._validate()
        return (
            f"{self.server_id}:{self.encoded_ip}:{self.port}:"
            f"{self.mode}:{self.vpn_mode}:{self.sid}"
        )


@dataclass(frozen=True)
class AstrillEndpoint:
    encoded_ip: int
    port: str
    mode: int
    protocol_code: int
    port_index: int
    protocol_original: int | None = None
    address: str | None = None
    resolved_ip: str | None = None

    def __post_init__(self) -> None:
        if (
            self.address is not None
            and self.resolved_ip is not None
            and self.address != self.resolved_ip
        ):
            raise ValueError("Astrill endpoint address aliases conflict")
        address = self.address if self.address is not None else self.resolved_ip
        object.__setattr__(self, "address", address)
        object.__setattr__(self, "resolved_ip", address)

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

    def supported_protocols(self) -> tuple[int, ...]:
        if not self.nodes:
            return ()
        supported = set(range(len(ASTRILL_PROTOCOL_NAMES)))
        for node in self.nodes:
            node_protocols = {
                _endpoint_protocol(endpoint) for endpoint in node.endpoints
            }
            supported.intersection_update(node_protocols)
        return tuple(sorted(supported))

    def port_options(self, protocol: int) -> tuple[AstrillPortOption, ...]:
        if not self.nodes:
            return ()
        node_ports: list[dict[int, str]] = []
        for node in self.nodes:
            ports = {
                endpoint.port_index: endpoint.port
                for endpoint in node.endpoints
                if _endpoint_protocol(endpoint) == protocol
            }
            if not ports:
                return ()
            node_ports.append(ports)
        common_indexes = set(node_ports[0])
        for ports in node_ports[1:]:
            common_indexes.intersection_update(ports)
        return tuple(
            AstrillPortOption(index, node_ports[0][index])
            for index in sorted(common_indexes)
        )

    def tcp_probe_target(self) -> tuple[str, int] | None:
        candidates = [
            endpoint
            for node in self.nodes
            for endpoint in node.endpoints
            if endpoint.address is not None and endpoint.mode == 1
        ]
        if not candidates:
            return None
        endpoint = min(
            candidates,
            key=lambda item: (
                _probe_port(item) != 443,
                item.router_pro,
                item.port_index,
                item.address or "",
            ),
        )
        return endpoint.address, _probe_port(endpoint)

    def country_name(self) -> str:
        return endpoint_country_name(self.name)


def endpoint_country_name(server_name: str) -> str:
    value = server_name.lstrip("* ").strip()
    if not value:
        return "Other"
    if value.startswith("[") and "]" in value:
        candidate = value[1 : value.index("]")].strip()
    else:
        candidate = value.split(" - ", 1)[0].strip()
        candidate = SERVER_COUNTRY_QUALIFIER_RE.sub("", candidate).strip()
    if not candidate:
        return "Other"
    normalized = candidate.casefold()
    if normalized in SERVER_CITY_COUNTRIES:
        return SERVER_CITY_COUNTRIES[normalized]
    return SERVER_COUNTRY_ALIASES.get(normalized, candidate)


def parse_astrill_favorites(value: str) -> tuple[AstrillFavorite, ...]:
    if not value:
        return ()
    favorites = tuple(
        AstrillFavorite.parse(record) for record in value.split(",") if record
    )
    if len({favorite.server_id for favorite in favorites}) != len(favorites):
        raise ValueError("Astrill favorites contain duplicate servers")
    return favorites


def serialize_astrill_favorites(
    favorites: Iterable[AstrillFavorite],
) -> str:
    ordered: dict[int, AstrillFavorite] = {}
    for favorite in favorites:
        favorite._validate()
        ordered[favorite.server_id] = favorite
    return ",".join(favorite.to_native() for favorite in ordered.values())


def update_astrill_favorite_list_batch(
    value: str,
    changes: Iterable[tuple[int, AstrillFavorite | None]],
) -> str:
    """Apply several favorite membership changes to one native snapshot.

    Every change is validated before the result is built. Existing records
    keep their value and order, removals affect only their requested server
    IDs, and new records are appended in request order.
    """

    requested = tuple(changes)
    validated: list[tuple[int, AstrillFavorite | None]] = []
    requested_ids: set[int] = set()
    for server_id, favorite in requested:
        if (
            not isinstance(server_id, int)
            or isinstance(server_id, bool)
            or server_id <= 0
        ):
            raise ValueError("Astrill favorite server ID must be positive")
        if server_id in requested_ids:
            raise ValueError(
                f"Astrill favorite batch contains duplicate server ID {server_id}"
            )
        requested_ids.add(server_id)
        if favorite is not None:
            if not isinstance(favorite, AstrillFavorite):
                raise TypeError(
                    "Astrill favorite change must contain a favorite record"
                )
            favorite._validate()
            if favorite.server_id != server_id:
                raise ValueError(
                    "Astrill favorite server ID does not match the selection"
                )
        validated.append((server_id, favorite))

    favorites = list(parse_astrill_favorites(value))
    if not validated:
        return value

    changes_by_id = dict(validated)
    existing_ids = {favorite.server_id for favorite in favorites}
    updated = [
        favorite
        for favorite in favorites
        if not (
            favorite.server_id in changes_by_id
            and changes_by_id[favorite.server_id] is None
        )
    ]
    changed = len(updated) != len(favorites)
    for server_id, favorite in validated:
        if favorite is not None and server_id not in existing_ids:
            updated.append(favorite)
            changed = True

    return serialize_astrill_favorites(updated) if changed else value


def update_astrill_favorite_list(
    value: str,
    server_id: int,
    favorite: AstrillFavorite | None,
) -> str:
    """Return a favorite list with one server added or removed.

    Existing records retain their order and value.  In particular, adding a
    server that is already present is idempotent rather than silently changing
    the transport details stored by Astrill for that favorite.
    """

    return update_astrill_favorite_list_batch(
        value,
        ((server_id, favorite),),
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


def _endpoint_protocol(endpoint: AstrillEndpoint) -> int:
    return (2 if endpoint.router_pro else 0) | endpoint.mode


def _validate_port(value: str) -> None:
    if not PORT_RE.fullmatch(value):
        raise ValueError(f"invalid Astrill port: {value!r}")
    bounds = [int(part) for part in value.split("-", 1)]
    if any(part < 1 or part > 65535 for part in bounds):
        raise ValueError("Astrill port must be between 1 and 65535")
    if len(bounds) == 2 and bounds[0] > bounds[1]:
        raise ValueError("Astrill port range is reversed")


def _probe_port(endpoint: AstrillEndpoint) -> int:
    bounds = [int(part) for part in endpoint.port.split("-", 1)]
    if len(bounds) == 1:
        return bounds[0]
    if bounds[0] <= 443 <= bounds[1]:
        return 443
    return bounds[0]


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
                address=(
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
