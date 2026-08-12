from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from typing import Any
from typing import Protocol as TypingProtocol

from .catalog import Catalog
from .compiler import compile_rules
from .models import MatchKind, Protocol, RouteTarget, Rule, validate_domain

MAX_OVERLAY_BYTES = 32 * 1024
OVERLAY_OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAC_ADDRESS_RE = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")


class OverlayRouter(TypingProtocol):
    def effective_status(self) -> dict[str, Any]: ...

    def overlay_put(
        self,
        owner: str,
        expected_generation: int,
        source: str,
        rules_tsv: str,
        *,
        expected_source: str | None = None,
        expected_mac: str | None = None,
    ) -> dict[str, Any]: ...

    def overlay_remove(
        self, owner: str, expected_generation: int
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DeviceFlowSpec:
    owner: str
    source: str
    mac: str
    domains: tuple[str, ...]
    destination_ips: tuple[str, ...]
    target: RouteTarget
    protocols: tuple[Protocol, ...]
    port: int

    @classmethod
    def create(
        cls,
        *,
        owner: str,
        source: str,
        mac: str,
        domains: list[str] | None,
        destination_ips: list[str] | None = None,
        target: str,
        protocols: list[str] | None = None,
        port: int = 443,
    ) -> DeviceFlowSpec:
        normalized_owner = owner.strip().casefold()
        if not OVERLAY_OWNER_RE.fullmatch(normalized_owner):
            raise ValueError(
                "device-flow owner must be 1..64 lowercase ASCII identifier characters"
            )

        try:
            address = ipaddress.ip_address(source.strip())
        except ValueError as exc:
            raise ValueError("device-flow source must be one exact IPv4 address") from exc
        if address.version != 4 or address.is_multicast or address.is_unspecified:
            raise ValueError("device-flow source must be one exact unicast IPv4 address")

        normalized_mac = mac.strip().casefold().replace("-", ":")
        if not MAC_ADDRESS_RE.fullmatch(normalized_mac):
            raise ValueError("device-flow MAC address is invalid")

        normalized_domains = tuple(
            dict.fromkeys(
                domain.strip().rstrip(".").casefold() for domain in (domains or [])
            )
        )
        if any(not domain for domain in normalized_domains):
            raise ValueError("device-flow domains must be non-empty")
        for domain in normalized_domains:
            validate_domain(domain)

        normalized_destination_ips: list[str] = []
        for raw_destination in destination_ips or []:
            try:
                destination = ipaddress.ip_address(raw_destination.strip())
            except ValueError as exc:
                raise ValueError(
                    "device-flow destination IPs must be exact IPv4 addresses"
                ) from exc
            if (
                destination.version != 4
                or destination.is_multicast
                or destination.is_unspecified
            ):
                raise ValueError(
                    "device-flow destination IPs must be exact unicast IPv4 addresses"
                )
            normalized_destination_ips.append(str(destination))
        normalized_destinations = tuple(dict.fromkeys(normalized_destination_ips))
        if not normalized_domains and not normalized_destinations:
            raise ValueError(
                "device-flow requires at least one domain or exact destination IP"
            )

        if not 1 <= port <= 65535:
            raise ValueError("device-flow port must be between 1 and 65535")
        protocol_values = protocols or [Protocol.TCP.value, Protocol.UDP.value]
        normalized_protocols = tuple(
            dict.fromkeys(Protocol(value.casefold()) for value in protocol_values)
        )
        if Protocol.ANY in normalized_protocols:
            raise ValueError("device-flow uses explicit TCP and/or UDP protocols")

        return cls(
            owner=normalized_owner,
            source=f"{address}/32",
            mac=normalized_mac,
            domains=normalized_domains,
            destination_ips=normalized_destinations,
            target=RouteTarget(target.casefold()),
            protocols=normalized_protocols,
            port=port,
        )


def compile_device_flow(spec: DeviceFlowSpec, catalog: Catalog) -> str:
    region = "direct" if spec.target is RouteTarget.DIRECT else "active-astrill"
    rules: list[Rule] = []
    for domain in spec.domains:
        for protocol in spec.protocols:
            identity = hashlib.sha256(
                f"{spec.owner}\0{domain}\0{protocol.value}\0{spec.port}".encode("ascii")
            ).hexdigest()[:20]
            rules.append(
                Rule(
                    id=f"deviceflow-{identity}",
                    name=f"Temporary {domain} {protocol.value}/{spec.port}",
                    match_kind=MatchKind.DOMAIN,
                    selector=domain,
                    target=spec.target,
                    region=region,
                    priority=100,
                    protocol=protocol,
                    ports=str(spec.port),
                )
            )
    for destination in spec.destination_ips:
        for protocol in spec.protocols:
            identity = hashlib.sha256(
                f"{spec.owner}\0{destination}\0{protocol.value}\0{spec.port}".encode(
                    "ascii"
                )
            ).hexdigest()[:20]
            rules.append(
                Rule(
                    id=f"deviceflow-{identity}",
                    name=f"Temporary {destination} {protocol.value}/{spec.port}",
                    match_kind=MatchKind.CIDR,
                    selector=f"{destination}/32",
                    target=spec.target,
                    region=region,
                    priority=100,
                    protocol=protocol,
                    ports=str(spec.port),
                )
            )
    return compile_rules(rules, catalog, max_bytes=MAX_OVERLAY_BYTES).to_tsv()


def find_owner_overlay(
    status: dict[str, Any], owner: str
) -> dict[str, Any] | None:
    overlays = status.get("overlays", [])
    if not isinstance(overlays, list):
        raise TypeError("router status has an invalid overlays field")
    for overlay in overlays:
        if isinstance(overlay, dict) and overlay.get("owner") == owner:
            return overlay
    return None


def put_device_flow(
    router: OverlayRouter,
    spec: DeviceFlowSpec,
    catalog: Catalog,
) -> dict[str, Any]:
    current = router.effective_status()
    overlay = find_owner_overlay(current, spec.owner)
    generation = 0 if overlay is None else _overlay_generation(overlay)
    payload = compile_device_flow(spec, catalog)
    return router.overlay_put(
        spec.owner,
        generation,
        spec.source,
        payload,
        expected_source=spec.source,
        expected_mac=spec.mac,
    )


def remove_device_flow(
    router: OverlayRouter, owner: str
) -> tuple[bool, dict[str, Any]]:
    normalized_owner = owner.strip().casefold()
    if not OVERLAY_OWNER_RE.fullmatch(normalized_owner):
        raise ValueError("device-flow owner is invalid")
    current = router.effective_status()
    overlay = find_owner_overlay(current, normalized_owner)
    if overlay is None:
        return False, current
    return True, router.overlay_remove(normalized_owner, _overlay_generation(overlay))


def summarize_device_flow(
    status: dict[str, Any], owner: str
) -> dict[str, Any] | None:
    overlay = find_owner_overlay(status, owner)
    if overlay is None:
        return None
    return {
        key: overlay.get(key)
        for key in ("owner", "generation", "source", "mac", "rows", "bytes", "hash")
    }


def _overlay_generation(overlay: dict[str, Any]) -> int:
    generation = overlay.get("generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise ValueError("router status has an invalid overlay generation")
    return generation
