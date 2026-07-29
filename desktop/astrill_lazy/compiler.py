from __future__ import annotations

import ipaddress

from .catalog import Catalog
from .models import Compilation, CompiledRule, MatchKind, Rule

MAX_COMPILED_BYTES = 6144


def compile_rules(rules: list[Rule], catalog: Catalog) -> Compilation:
    services = catalog.services_by_id
    regions = catalog.regions_by_id
    compiled: list[CompiledRule] = []
    warnings: list[str] = []
    used_ids: set[str] = set()
    vpn_regions: set[str] = set()

    for rule in sorted(rules, key=lambda item: (item.priority, item.id)):
        rule.validate()
        if rule.region not in regions:
            raise ValueError(f"rule {rule.name!r} uses unknown region {rule.region!r}")
        if rule.target.value == "vpn" and rule.enabled:
            vpn_regions.add(rule.region)

        if rule.match_kind is MatchKind.SERVICE:
            service = services.get(rule.selector)
            if service is None:
                raise ValueError(
                    f"rule {rule.name!r} uses unknown service {rule.selector!r}"
                )
            for index, domain in enumerate(service.domains):
                compiled_id = _unique_id(f"{rule.id}.{index}", used_ids)
                compiled.append(
                    CompiledRule(
                        id=compiled_id,
                        enabled=rule.enabled,
                        priority=rule.priority,
                        kind="domain",
                        selector=domain,
                        target=rule.target,
                        protocol=rule.protocol,
                        ports=rule.ports,
                        label=rule.name,
                        origin=rule.id,
                    )
                )
            for index, network in enumerate(service.networks):
                compiled_id = _unique_id(f"{rule.id}.net{index}", used_ids)
                compiled.append(
                    CompiledRule(
                        id=compiled_id,
                        enabled=rule.enabled,
                        priority=rule.priority,
                        kind="cidr",
                        selector=network,
                        target=rule.target,
                        protocol=rule.protocol,
                        ports=rule.ports,
                        label=rule.name,
                        origin=rule.id,
                    )
                )
            continue

        if rule.match_kind is MatchKind.PROCESS:
            namespace_ip = str(rule.metadata.get("namespace_ip", "")).strip()
            if not namespace_ip:
                warnings.append(
                    f"{rule.name}: launch the application once to allocate its "
                    "network identity"
                )
                continue
            ipaddress.ip_address(namespace_ip)
            kind = "device"
            selector = namespace_ip
        else:
            kind = rule.match_kind.value
            selector = rule.selector

        compiled.append(
            CompiledRule(
                id=_unique_id(rule.id, used_ids),
                enabled=rule.enabled,
                priority=rule.priority,
                kind=kind,
                selector=selector,
                target=rule.target,
                protocol=rule.protocol,
                ports=rule.ports,
                label=rule.name,
                origin=rule.id,
            )
        )

    specific_regions = vpn_regions - {"active-astrill"}
    if len(specific_regions) > 1:
        names = ", ".join(sorted(specific_regions))
        warnings.append(
            "The router has one Astrill tunnel; these requested regions cannot be "
            f"active simultaneously: {names}"
        )

    compilation = Compilation(tuple(compiled), tuple(warnings))
    compiled_bytes = len(compilation.to_tsv().encode("ascii"))
    if compiled_bytes > MAX_COMPILED_BYTES:
        raise ValueError(
            f"compiled policy is {compiled_bytes:,} bytes; the router limit is "
            f"{MAX_COMPILED_BYTES:,}. Use narrower app profiles or remove policies."
        )
    return compilation


def _unique_id(candidate: str, used: set[str]) -> str:
    value = candidate[:64]
    if value not in used:
        used.add(value)
        return value
    suffix = 2
    while True:
        marker = f"-{suffix}"
        value = f"{candidate[: 64 - len(marker)]}{marker}"
        if value not in used:
            used.add(value)
            return value
        suffix += 1
