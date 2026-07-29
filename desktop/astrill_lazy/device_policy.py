from __future__ import annotations

import csv
import ipaddress
import json
import math
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .models import validate_domain, validate_id

DEVICE_POLICY_SCHEMA_VERSION = 1
MAX_TUNNELS = 3
DEFAULT_PROBE_MAX_AGE = 120
AUTO_HOLD_SECONDS = 300
AUTO_HYSTERESIS_RATIO = 0.15
COUNTRY_CODE_RE = re.compile(r"^[A-Z]{2}$")
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class DeviceRouteMode(StrEnum):
    DIRECT = "direct"
    TUNNEL = "tunnel"
    AUTO = "auto"


class DeviceFallback(StrEnum):
    DIRECT = "direct"
    BLOCK = "block"


class DeviceRuleKind(StrEnum):
    APPLICATION = "application"
    SERVICE = "service"
    DOMAIN = "domain"
    NETWORK = "network"
    COUNTRY = "country"


class SelectedPathKind(StrEnum):
    DIRECT = "direct"
    TUNNEL = "tunnel"
    BLOCK = "block"


@dataclass(frozen=True)
class RouteAction:
    mode: DeviceRouteMode
    tunnel_ids: tuple[str, ...] = ()
    fallback: DeviceFallback = DeviceFallback.DIRECT

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RouteAction:
        _reject_unknown_fields(value, {"mode", "tunnel_ids", "fallback"}, "route")
        raw_ids = value.get("tunnel_ids", [])
        if not isinstance(raw_ids, list) or not all(
            isinstance(item, str) for item in raw_ids
        ):
            raise TypeError("route tunnel_ids must be an array of strings")
        fallback = value.get("fallback", "direct")
        if not isinstance(fallback, str):
            raise TypeError("route fallback must be a string")
        action = cls(
            mode=DeviceRouteMode(_string_field(value, "mode")),
            tunnel_ids=tuple(raw_ids),
            fallback=DeviceFallback(fallback),
        )
        action.validate_shape()
        return action

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"mode": self.mode.value}
        if self.tunnel_ids:
            value["tunnel_ids"] = list(self.tunnel_ids)
        if self.mode is not DeviceRouteMode.DIRECT:
            value["fallback"] = self.fallback.value
        return value

    def validate_shape(self) -> None:
        if not isinstance(self.mode, DeviceRouteMode):
            raise TypeError("route mode must be a DeviceRouteMode")
        if not isinstance(self.fallback, DeviceFallback):
            raise TypeError("route fallback must be a DeviceFallback")
        if len(set(self.tunnel_ids)) != len(self.tunnel_ids):
            raise ValueError("route contains duplicate tunnel IDs")
        for tunnel_id in self.tunnel_ids:
            validate_id(tunnel_id)
        if self.mode is DeviceRouteMode.DIRECT and self.tunnel_ids:
            raise ValueError("a Direct route cannot name tunnels")
        if (
            self.mode is DeviceRouteMode.DIRECT
            and self.fallback is not DeviceFallback.DIRECT
        ):
            raise ValueError("a Direct route cannot declare a fallback")
        if self.mode is DeviceRouteMode.TUNNEL and len(self.tunnel_ids) != 1:
            raise ValueError("a fixed tunnel route must name exactly one tunnel")
        if self.mode is DeviceRouteMode.AUTO and not 2 <= len(self.tunnel_ids) <= 3:
            raise ValueError("an Auto route must name two or three tunnels")

    def validate_tunnels(self, known_tunnels: set[str]) -> None:
        self.validate_shape()
        unknown = set(self.tunnel_ids) - known_tunnels
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"route uses unknown tunnel IDs: {names}")


@dataclass(frozen=True)
class TunnelSlot:
    id: str
    name: str
    provider: str
    region: str
    configuration_ref: str
    enabled: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TunnelSlot:
        _reject_unknown_fields(
            value,
            {
                "id",
                "name",
                "provider",
                "region",
                "configuration_ref",
                "enabled",
            },
            "tunnel",
        )
        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool):
            raise TypeError("tunnel enabled must be a boolean")
        slot = cls(
            id=_string_field(value, "id"),
            name=_string_field(value, "name"),
            provider=_string_field(value, "provider"),
            region=_string_field(value, "region"),
            configuration_ref=_string_field(value, "configuration_ref"),
            enabled=enabled,
        )
        slot.validate()
        return slot

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "region": self.region,
            "configuration_ref": self.configuration_ref,
            "enabled": self.enabled,
        }

    def validate(self) -> None:
        validate_id(self.id)
        validate_id(self.region)
        if not isinstance(self.enabled, bool):
            raise TypeError("tunnel enabled must be a boolean")
        if not self.name.strip() or len(self.name) > 120:
            raise ValueError("tunnel name must contain 1 to 120 characters")
        if self.provider not in {"astrill", "openvpn", "wireguard"}:
            raise ValueError(f"unsupported tunnel provider: {self.provider!r}")
        if (
            not self.configuration_ref.strip()
            or "\x00" in self.configuration_ref
            or len(self.configuration_ref) > 512
        ):
            raise ValueError("tunnel configuration_ref is invalid")


@dataclass(frozen=True)
class CountryGroup:
    id: str
    name: str
    country_codes: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CountryGroup:
        _reject_unknown_fields(value, {"id", "name", "country_codes"}, "country group")
        raw_codes = value.get("country_codes")
        if not isinstance(raw_codes, list) or not all(
            isinstance(item, str) for item in raw_codes
        ):
            raise TypeError("country group country_codes must be an array of strings")
        group = cls(
            id=_string_field(value, "id"),
            name=_string_field(value, "name"),
            country_codes=tuple(raw_codes),
        )
        group.validate()
        return group

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "country_codes": list(self.country_codes),
        }

    def validate(self) -> None:
        validate_id(self.id)
        if COUNTRY_CODE_RE.fullmatch(self.id):
            raise ValueError("country group ID cannot be a two-letter country code")
        if not self.name.strip() or len(self.name) > 120:
            raise ValueError("country group name must contain 1 to 120 characters")
        if not self.country_codes or len(self.country_codes) > 64:
            raise ValueError("country group must contain 1 to 64 country codes")
        if len(set(self.country_codes)) != len(self.country_codes):
            raise ValueError("country group contains duplicate country codes")
        for country_code in self.country_codes:
            validate_country_code(country_code)


@dataclass(frozen=True)
class DeviceRule:
    id: str
    name: str
    kind: DeviceRuleKind
    selector: str
    route: RouteAction
    priority: int = 500
    enabled: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DeviceRule:
        _reject_unknown_fields(
            value,
            {"id", "name", "kind", "selector", "route", "priority", "enabled"},
            "device rule",
        )
        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool):
            raise TypeError("device rule enabled must be a boolean")
        priority = value.get("priority", 500)
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise TypeError("device rule priority must be an integer")
        raw_route = value.get("route")
        if not isinstance(raw_route, dict):
            raise TypeError("device rule route must be an object")
        rule = cls(
            id=_string_field(value, "id"),
            name=_string_field(value, "name"),
            kind=DeviceRuleKind(_string_field(value, "kind")),
            selector=_string_field(value, "selector"),
            route=RouteAction.from_dict(raw_route),
            priority=priority,
            enabled=enabled,
        )
        rule.validate()
        return rule

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "selector": self.selector,
            "route": self.route.to_dict(),
            "priority": self.priority,
            "enabled": self.enabled,
        }

    def validate(self) -> None:
        validate_id(self.id)
        if not isinstance(self.kind, DeviceRuleKind):
            raise TypeError("device rule kind must be a DeviceRuleKind")
        if not isinstance(self.enabled, bool):
            raise TypeError("device rule enabled must be a boolean")
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            raise TypeError("device rule priority must be an integer")
        if not self.name.strip() or len(self.name) > 120:
            raise ValueError("device rule name must contain 1 to 120 characters")
        if not 0 <= self.priority <= 9999:
            raise ValueError("device rule priority must be between 0 and 9999")
        self.route.validate_shape()
        if self.kind in {DeviceRuleKind.APPLICATION, DeviceRuleKind.SERVICE}:
            validate_id(self.selector)
        elif self.kind is DeviceRuleKind.DOMAIN:
            validate_domain(self.selector.casefold())
        elif self.kind is DeviceRuleKind.NETWORK:
            ipaddress.ip_network(self.selector, strict=False)
        elif self.kind is DeviceRuleKind.COUNTRY:
            if COUNTRY_CODE_RE.fullmatch(self.selector):
                validate_country_code(self.selector)
            else:
                validate_id(self.selector)


@dataclass(frozen=True)
class DevicePolicy:
    default_route: RouteAction
    tunnels: tuple[TunnelSlot, ...] = ()
    country_groups: tuple[CountryGroup, ...] = ()
    rules: tuple[DeviceRule, ...] = ()
    schema_version: int = DEVICE_POLICY_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DevicePolicy:
        _reject_unknown_fields(
            value,
            {
                "schema_version",
                "default_route",
                "tunnels",
                "country_groups",
                "rules",
            },
            "device policy",
        )
        schema_version = value.get("schema_version")
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != DEVICE_POLICY_SCHEMA_VERSION
        ):
            raise ValueError("unsupported device policy schema")
        raw_tunnels = value.get("tunnels", [])
        raw_country_groups = value.get("country_groups", [])
        raw_rules = value.get("rules", [])
        if (
            not isinstance(raw_tunnels, list)
            or not isinstance(raw_country_groups, list)
            or not isinstance(raw_rules, list)
        ):
            raise TypeError(
                "device policy tunnels, country_groups, and rules must be arrays"
            )
        if not all(
            isinstance(item, dict)
            for item in (*raw_tunnels, *raw_country_groups, *raw_rules)
        ):
            raise TypeError(
                "device policy tunnel, country group, and rule entries must be objects"
            )
        raw_default = value.get("default_route")
        if not isinstance(raw_default, dict):
            raise TypeError("device policy default_route must be an object")
        policy = cls(
            default_route=RouteAction.from_dict(raw_default),
            tunnels=tuple(TunnelSlot.from_dict(item) for item in raw_tunnels),
            country_groups=tuple(
                CountryGroup.from_dict(item) for item in raw_country_groups
            ),
            rules=tuple(DeviceRule.from_dict(item) for item in raw_rules),
        )
        policy.validate()
        return policy

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "default_route": self.default_route.to_dict(),
            "tunnels": [item.to_dict() for item in self.tunnels],
            "country_groups": [item.to_dict() for item in self.country_groups],
            "rules": [item.to_dict() for item in self.rules],
        }

    def validate(self) -> None:
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version != DEVICE_POLICY_SCHEMA_VERSION
        ):
            raise ValueError("unsupported device policy schema")
        if len(self.tunnels) > MAX_TUNNELS:
            raise ValueError(f"a device policy supports at most {MAX_TUNNELS} tunnels")
        tunnel_ids = _unique_ids((item.id for item in self.tunnels), "tunnel")
        enabled_tunnel_ids = {item.id for item in self.tunnels if item.enabled}
        country_group_ids = _unique_ids(
            (item.id for item in self.country_groups), "country group"
        )
        _unique_ids((item.id for item in self.rules), "device rule")
        self.default_route.validate_tunnels(tunnel_ids)
        _validate_enabled_tunnels(self.default_route, enabled_tunnel_ids)
        for tunnel in self.tunnels:
            tunnel.validate()
        for country_group in self.country_groups:
            country_group.validate()
        for rule in self.rules:
            rule.validate()
            if (
                rule.kind is DeviceRuleKind.COUNTRY
                and not COUNTRY_CODE_RE.fullmatch(rule.selector)
                and rule.selector not in country_group_ids
            ):
                raise ValueError(
                    f"device rule {rule.id!r} uses unknown country group "
                    f"{rule.selector!r}"
                )
            rule.route.validate_tunnels(tunnel_ids)
            if rule.enabled:
                _validate_enabled_tunnels(rule.route, enabled_tunnel_ids)


@dataclass(frozen=True)
class TrafficContext:
    application_ids: tuple[str, ...] = ()
    service_ids: tuple[str, ...] = ()
    domain: str | None = None
    destination_ip: str | None = None
    country_code: str | None = None

    def normalized(self) -> TrafficContext:
        domain = self.domain.rstrip(".").casefold() if self.domain else None
        if domain:
            validate_domain(domain)
        destination_ip = (
            str(ipaddress.ip_address(self.destination_ip))
            if self.destination_ip
            else None
        )
        country_code = self.country_code.upper() if self.country_code else None
        if country_code:
            validate_country_code(country_code)
        for value in (*self.application_ids, *self.service_ids):
            validate_id(value)
        return TrafficContext(
            application_ids=tuple(dict.fromkeys(self.application_ids)),
            service_ids=tuple(dict.fromkeys(self.service_ids)),
            domain=domain,
            destination_ip=destination_ip,
            country_code=country_code,
        )


@dataclass(frozen=True)
class RouteDecision:
    route: RouteAction
    rule_id: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route.to_dict(),
            "rule_id": self.rule_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PathProbe:
    path_id: str
    reachable: bool
    latency_ms: float
    loss_percent: float
    checked_at: float

    def validate(self) -> None:
        validate_id(self.path_id)
        if not isinstance(self.reachable, bool):
            raise TypeError("probe reachable must be a boolean")
        if not math.isfinite(self.latency_ms) or self.latency_ms < 0:
            raise ValueError("probe latency must be a finite non-negative number")
        if not math.isfinite(self.loss_percent) or not 0 <= self.loss_percent <= 100:
            raise ValueError("probe loss must be between 0 and 100")
        if not math.isfinite(self.checked_at) or self.checked_at < 0:
            raise ValueError("probe timestamp is invalid")

    @property
    def score(self) -> float:
        return self.latency_ms + (self.loss_percent * 20)


@dataclass(frozen=True)
class SelectedPath:
    kind: SelectedPathKind
    tunnel_id: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "tunnel_id": self.tunnel_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CountryNetwork:
    country_code: str
    network: IPNetwork


@dataclass(frozen=True)
class CountryRoutePlan:
    route: RouteAction
    networks: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"route": self.route.to_dict(), "networks": list(self.networks)}


def load_device_policy(path: Path) -> DevicePolicy:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle, object_pairs_hook=_object_without_duplicates)
    if not isinstance(value, dict):
        raise TypeError("device policy root must be an object")
    return DevicePolicy.from_dict(value)


def decide_route(policy: DevicePolicy, context: TrafficContext) -> RouteDecision:
    policy.validate()
    normalized = context.normalized()
    return _decide_route_validated(policy, normalized, _ordered_rules(policy))


def _ordered_rules(policy: DevicePolicy) -> tuple[DeviceRule, ...]:
    specificity = {
        DeviceRuleKind.APPLICATION: 0,
        DeviceRuleKind.SERVICE: 1,
        DeviceRuleKind.DOMAIN: 2,
        DeviceRuleKind.NETWORK: 3,
        DeviceRuleKind.COUNTRY: 4,
    }

    def rule_key(item: DeviceRule) -> tuple[int, int, str]:
        rank = specificity[item.kind]
        if item.kind is DeviceRuleKind.COUNTRY and not COUNTRY_CODE_RE.fullmatch(
            item.selector
        ):
            rank += 1
        return item.priority, rank, item.id

    return tuple(
        sorted(
            (item for item in policy.rules if item.enabled),
            key=rule_key,
        )
    )


def _decide_route_validated(
    policy: DevicePolicy,
    context: TrafficContext,
    rules: tuple[DeviceRule, ...],
) -> RouteDecision:
    country_groups = {
        item.id: frozenset(item.country_codes) for item in policy.country_groups
    }
    for rule in rules:
        if _rule_matches(rule, context, country_groups):
            return RouteDecision(
                route=rule.route,
                rule_id=rule.id,
                reason=f"matched {rule.kind.value} rule {rule.name}",
            )
    return RouteDecision(
        route=policy.default_route,
        rule_id=None,
        reason="used the device default route",
    )


def select_path(
    action: RouteAction,
    probes: Iterable[PathProbe],
    *,
    current_tunnel_id: str | None = None,
    last_switch_at: float | None = None,
    now: float | None = None,
    max_probe_age: int = DEFAULT_PROBE_MAX_AGE,
) -> SelectedPath:
    action.validate_shape()
    if (
        not isinstance(max_probe_age, int)
        or isinstance(max_probe_age, bool)
        or max_probe_age <= 0
    ):
        raise ValueError("max_probe_age must be a positive integer")
    if action.mode is DeviceRouteMode.DIRECT:
        return SelectedPath(SelectedPathKind.DIRECT, None, "policy selected Direct")

    current_time = time.time() if now is None else now
    if not math.isfinite(current_time) or current_time < 0:
        raise ValueError("current time is invalid")
    if last_switch_at is not None and (
        not math.isfinite(last_switch_at)
        or last_switch_at < 0
        or last_switch_at > current_time
    ):
        raise ValueError("last switch timestamp is invalid")
    probe_map: dict[str, PathProbe] = {}
    for probe in probes:
        probe.validate()
        if probe.path_id in probe_map:
            raise ValueError(f"duplicate probe for path {probe.path_id!r}")
        probe_map[probe.path_id] = probe

    usable = {
        tunnel_id: probe_map[tunnel_id]
        for tunnel_id in action.tunnel_ids
        if tunnel_id in probe_map
        and probe_map[tunnel_id].reachable
        and 0 <= current_time - probe_map[tunnel_id].checked_at <= max_probe_age
    }
    if action.mode is DeviceRouteMode.TUNNEL:
        tunnel_id = action.tunnel_ids[0]
        if tunnel_id in usable:
            return SelectedPath(
                SelectedPathKind.TUNNEL,
                tunnel_id,
                f"fixed tunnel {tunnel_id} is healthy",
            )
        return _fallback_path(action, f"fixed tunnel {tunnel_id} is unavailable")

    if not usable:
        return _fallback_path(action, "no Auto tunnel has a fresh healthy probe")

    best_id, best_probe = min(usable.items(), key=lambda item: (item[1].score, item[0]))
    current_probe = usable.get(current_tunnel_id or "")
    if current_probe is not None:
        within_hold = (
            last_switch_at is not None
            and current_time - last_switch_at < AUTO_HOLD_SECONDS
        )
        within_hysteresis = current_probe.score <= (
            best_probe.score * (1 + AUTO_HYSTERESIS_RATIO)
        )
        if within_hold or within_hysteresis:
            reason = (
                "kept current tunnel during the minimum hold period"
                if within_hold
                else "kept current tunnel within the hysteresis margin"
            )
            return SelectedPath(SelectedPathKind.TUNNEL, current_tunnel_id, reason)
    return SelectedPath(
        SelectedPathKind.TUNNEL,
        best_id,
        f"selected the lowest healthy path score ({best_probe.score:.1f})",
    )


def load_country_networks(path: Path) -> tuple[CountryNetwork, ...]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["country_code", "network"]:
            raise ValueError("country network CSV header must be: country_code,network")
        networks: list[CountryNetwork] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                country_code = str(row["country_code"]).upper()
                validate_country_code(country_code)
                network = ipaddress.ip_network(str(row["network"]), strict=True)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid country network CSV row {line_number}: {exc}"
                ) from exc
            networks.append(CountryNetwork(country_code, network))
    return tuple(networks)


def compile_country_routes(
    policy: DevicePolicy,
    country_networks: Iterable[CountryNetwork],
) -> tuple[CountryRoutePlan, ...]:
    policy.validate()
    grouped: dict[str, tuple[RouteAction, list[IPNetwork]]] = {}
    items = tuple(country_networks)
    _validate_country_network_set(items)
    rules = _ordered_rules(policy)
    country_decisions: dict[str, RouteDecision] = {}
    for item in items:
        validate_country_code(item.country_code)
        decision = country_decisions.get(item.country_code)
        if decision is None:
            decision = _decide_route_validated(
                policy,
                TrafficContext(country_code=item.country_code),
                rules,
            )
            country_decisions[item.country_code] = decision
        key = json.dumps(decision.route.to_dict(), sort_keys=True)
        grouped.setdefault(key, (decision.route, []))[1].append(item.network)

    plans: list[CountryRoutePlan] = []
    for key in sorted(grouped):
        action, networks = grouped[key]
        collapsed: list[str] = []
        for version in (4, 6):
            version_networks = [item for item in networks if item.version == version]
            collapsed.extend(
                str(item) for item in ipaddress.collapse_addresses(version_networks)
            )
        plans.append(CountryRoutePlan(action, tuple(collapsed)))
    return tuple(plans)


def validate_country_code(value: str) -> None:
    if not COUNTRY_CODE_RE.fullmatch(value):
        raise ValueError(f"invalid ISO 3166-1 alpha-2 country code: {value!r}")


def _rule_matches(
    rule: DeviceRule,
    context: TrafficContext,
    country_groups: dict[str, frozenset[str]],
) -> bool:
    if rule.kind is DeviceRuleKind.APPLICATION:
        return rule.selector in context.application_ids
    if rule.kind is DeviceRuleKind.SERVICE:
        return rule.selector in context.service_ids
    if rule.kind is DeviceRuleKind.DOMAIN:
        return bool(
            context.domain
            and (
                context.domain == rule.selector.casefold()
                or context.domain.endswith(f".{rule.selector.casefold()}")
            )
        )
    if rule.kind is DeviceRuleKind.NETWORK:
        return bool(
            context.destination_ip
            and ipaddress.ip_address(context.destination_ip)
            in ipaddress.ip_network(rule.selector, strict=False)
        )
    if rule.kind is DeviceRuleKind.COUNTRY:
        if COUNTRY_CODE_RE.fullmatch(rule.selector):
            return context.country_code == rule.selector
        return bool(
            context.country_code
            and context.country_code in country_groups.get(rule.selector, ())
        )
    return False


def _fallback_path(action: RouteAction, reason: str) -> SelectedPath:
    if action.fallback is DeviceFallback.DIRECT:
        return SelectedPath(SelectedPathKind.DIRECT, None, f"{reason}; used Direct")
    return SelectedPath(SelectedPathKind.BLOCK, None, f"{reason}; blocked traffic")


def _unique_ids(values: Iterable[str], label: str) -> set[str]:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {label} identifier: {value}")
        seen.add(value)
    return seen


def _validate_country_network_set(items: tuple[CountryNetwork, ...]) -> None:
    for version in (4, 6):
        networks = sorted(
            (item.network for item in items if item.network.version == version),
            key=lambda item: (int(item.network_address), int(item.broadcast_address)),
        )
        previous: IPNetwork | None = None
        for network in networks:
            if previous is not None and int(network.network_address) <= int(
                previous.broadcast_address
            ):
                raise ValueError(f"country networks overlap: {previous} and {network}")
            previous = network


def _validate_enabled_tunnels(
    action: RouteAction, enabled_tunnel_ids: set[str]
) -> None:
    disabled = set(action.tunnel_ids) - enabled_tunnel_ids
    if disabled:
        names = ", ".join(sorted(disabled))
        raise ValueError(f"route uses disabled tunnel IDs: {names}")


def _string_field(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise TypeError(f"{key} must be a string")
    return item


def _reject_unknown_fields(
    value: dict[str, Any], allowed: set[str], label: str
) -> None:
    unknown = set(value) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"{label} contains unknown fields: {names}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value
