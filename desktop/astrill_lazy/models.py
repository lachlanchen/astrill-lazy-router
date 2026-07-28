from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import quote, urlparse
from uuid import uuid4

ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$"
)
PORTS_RE = re.compile(r"^(?:\d{1,5}(?::\d{1,5})?)(?:,\d{1,5}(?::\d{1,5})?)*$")


class MatchKind(StrEnum):
    SERVICE = "service"
    DOMAIN = "domain"
    CIDR = "cidr"
    DEVICE = "device"
    PROCESS = "process"


class RouteTarget(StrEnum):
    DIRECT = "direct"
    VPN = "vpn"


class Protocol(StrEnum):
    ANY = "any"
    TCP = "tcp"
    UDP = "udp"


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    company: str
    category: str
    profile_type: str
    default_route: RouteTarget
    preferred_region: str
    domains: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    source: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Service:
        service = cls(
            id=str(value["id"]),
            name=str(value["name"]),
            company=str(value["company"]),
            category=str(value["category"]),
            profile_type=str(value.get("profile_type", "app")),
            default_route=RouteTarget(value["default_route"]),
            preferred_region=str(value.get("preferred_region", "active-astrill")),
            domains=tuple(str(item).lower() for item in value["domains"]),
            aliases=tuple(str(item) for item in value.get("aliases", [])),
            source=str(value.get("source", "")),
        )
        service.validate()
        return service

    def validate(self) -> None:
        validate_id(self.id)
        if (
            not self.name.strip()
            or not self.company.strip()
            or not self.category.strip()
        ):
            raise ValueError(
                f"service {self.id!r} has an empty name, company, or category"
            )
        if self.profile_type not in {"company", "app", "website"}:
            raise ValueError(
                f"service {self.id!r} has invalid profile type {self.profile_type!r}"
            )
        if not self.domains:
            raise ValueError(f"service {self.id!r} has no domains")
        if len(self.domains) > 16:
            raise ValueError(f"service {self.id!r} has more than 16 seed domains")
        if len(set(self.domains)) != len(self.domains):
            raise ValueError(f"service {self.id!r} contains duplicate domains")
        for domain in self.domains:
            validate_domain(domain)
        if any(not alias.strip() for alias in self.aliases):
            raise ValueError(f"service {self.id!r} contains an empty alias")
        if (
            self.default_route is RouteTarget.DIRECT
            and self.preferred_region != "direct"
        ):
            raise ValueError(
                f"direct service {self.id!r} must prefer the direct region"
            )
        if self.source:
            parsed_source = urlparse(self.source)
            if parsed_source.scheme != "https" or not parsed_source.hostname:
                raise ValueError(f"service {self.id!r} has an invalid source URL")

    @property
    def search_text(self) -> str:
        return " ".join(
            [
                self.name,
                self.company,
                self.category,
                self.profile_type,
                *self.aliases,
                *self.domains,
            ]
        ).casefold()


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    kind: str
    match: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Region:
        region = cls(
            id=str(value["id"]),
            name=str(value["name"]),
            kind=str(value["kind"]),
            match=tuple(str(item) for item in value.get("match", [])),
        )
        validate_id(region.id)
        if region.kind not in {"direct", "vpn", "astrill"}:
            raise ValueError(f"region {region.id!r} has invalid kind {region.kind!r}")
        return region


@dataclass
class Rule:
    id: str
    name: str
    match_kind: MatchKind
    selector: str
    target: RouteTarget
    region: str = "active-astrill"
    enabled: bool = True
    priority: int = 500
    protocol: Protocol = Protocol.ANY
    ports: str = "-"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        name: str,
        match_kind: MatchKind,
        selector: str,
        target: RouteTarget,
        region: str = "active-astrill",
        priority: int = 500,
    ) -> Rule:
        slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:36] or "rule"
        return cls(
            id=f"{slug}-{uuid4().hex[:8]}",
            name=name,
            match_kind=match_kind,
            selector=selector,
            target=target,
            region=region,
            priority=priority,
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Rule:
        rule = cls(
            id=str(value["id"]),
            name=str(value["name"]),
            match_kind=MatchKind(value["match_kind"]),
            selector=str(value["selector"]),
            target=RouteTarget(value["target"]),
            region=str(value.get("region", "active-astrill")),
            enabled=bool(value.get("enabled", True)),
            priority=int(value.get("priority", 500)),
            protocol=Protocol(value.get("protocol", "any")),
            ports=str(value.get("ports", "-")),
            metadata=dict(value.get("metadata", {})),
        )
        rule.validate()
        return rule

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "match_kind": self.match_kind.value,
            "selector": self.selector,
            "target": self.target.value,
            "region": self.region,
            "enabled": self.enabled,
            "priority": self.priority,
            "protocol": self.protocol.value,
            "ports": self.ports,
            "metadata": self.metadata,
        }

    def validate(self) -> None:
        validate_id(self.id)
        if not self.name.strip() or len(self.name) > 120:
            raise ValueError("rule name must contain 1 to 120 characters")
        if not 0 <= self.priority <= 9999:
            raise ValueError("rule priority must be between 0 and 9999")
        validate_id(self.region)
        if self.ports != "-":
            validate_ports(self.ports)
            if self.protocol is Protocol.ANY:
                raise ValueError("port-specific rules must use TCP or UDP")

        if self.match_kind is MatchKind.SERVICE:
            validate_id(self.selector)
        elif self.match_kind is MatchKind.DOMAIN:
            validate_domain(self.selector)
        elif self.match_kind in {MatchKind.CIDR, MatchKind.DEVICE}:
            validate_network(self.selector)
        elif self.match_kind is MatchKind.PROCESS and (
            not self.selector.startswith("/") or "\x00" in self.selector
        ):
            raise ValueError("process selector must be an absolute executable path")


@dataclass(frozen=True)
class CompiledRule:
    id: str
    enabled: bool
    priority: int
    kind: str
    selector: str
    target: RouteTarget
    protocol: Protocol
    ports: str
    label: str
    origin: str

    def to_tsv(self) -> str:
        values = (
            self.id,
            "1" if self.enabled else "0",
            str(self.priority),
            self.kind,
            self.selector,
            self.target.value,
            self.protocol.value,
            self.ports,
            quote(self.label, safe="-._~"),
            self.origin,
        )
        return "\t".join(values)


@dataclass(frozen=True)
class Compilation:
    rules: tuple[CompiledRule, ...]
    warnings: tuple[str, ...] = ()

    def to_tsv(self) -> str:
        header = "# astrill-lazy-rules-v1"
        body = "\n".join(rule.to_tsv() for rule in self.rules)
        return f"{header}\n{body}\n" if body else f"{header}\n"


def validate_id(value: str) -> None:
    if not ID_RE.fullmatch(value):
        raise ValueError(f"invalid identifier: {value!r}")


def validate_domain(value: str) -> None:
    value = value.rstrip(".").lower()
    if not DOMAIN_RE.fullmatch(value):
        raise ValueError(f"invalid domain: {value!r}")


def validate_network(value: str) -> None:
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise ValueError(f"invalid IP address or network: {value!r}") from exc
    if network.version != 4:
        raise ValueError("the current DD-WRT router supports IPv4 policy rules only")


def validate_ports(value: str) -> None:
    if not PORTS_RE.fullmatch(value):
        raise ValueError(f"invalid port list: {value!r}")
    entries = value.split(",")
    if len(entries) > 15:
        raise ValueError("a port list can contain at most 15 entries")
    for entry in entries:
        bounds = [int(item) for item in entry.split(":")]
        if any(item < 1 or item > 65535 for item in bounds):
            raise ValueError(f"port outside 1..65535: {entry!r}")
        if len(bounds) == 2 and bounds[0] > bounds[1]:
            raise ValueError(f"port range is reversed: {entry!r}")
