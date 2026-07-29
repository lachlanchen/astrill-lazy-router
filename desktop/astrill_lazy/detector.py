from __future__ import annotations

import shlex
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from .catalog import Catalog
from .models import MatchKind, RouteTarget, Rule, validate_domain
from .router import RouterClient

MINIMUM_BYPASS_SERVICES = frozenset(
    {"uu-remote", "wechat", "taobao", "meituan"}
)


@dataclass(frozen=True)
class RouteProbe:
    domain: str
    address: str
    direct_ms: float | None
    astrill_ms: float | None


@dataclass(frozen=True)
class RouteRecommendation:
    rule_id: str
    target: RouteTarget
    probe: RouteProbe
    reason: str

    def to_metadata(self) -> dict[str, object]:
        return {
            "target": self.target.value,
            "domain": self.probe.domain,
            "address": self.probe.address,
            "direct_ms": self.probe.direct_ms,
            "astrill_ms": self.probe.astrill_ms,
            "checked_at": int(time.time()),
            "reason": self.reason,
        }


def detect_rules(
    router: RouterClient,
    rules: list[Rule],
    catalog: Catalog,
) -> list[RouteRecommendation]:
    candidates: list[tuple[Rule, str]] = []
    for rule in rules:
        if not rule.enabled:
            continue
        domain = _representative_domain(rule, catalog)
        if domain:
            candidates.append((rule, domain))
    probes = probe_domains(router, [domain for _rule, domain in candidates])
    recommendations: list[RouteRecommendation] = []
    for rule, domain in candidates:
        probe = probes[domain]
        minimum_direct = bool(rule.metadata.get("minimum_bypass")) or (
            rule.match_kind is MatchKind.SERVICE
            and rule.selector in MINIMUM_BYPASS_SERVICES
        )
        preferred = (
            catalog.services_by_id[rule.selector].default_route
            if rule.match_kind is MatchKind.SERVICE
            and rule.selector in catalog.services_by_id
            else None
        )
        target, reason = recommend_target(
            probe,
            current=rule.target,
            minimum_direct=minimum_direct,
            preferred=preferred,
        )
        recommendations.append(
            RouteRecommendation(
                rule_id=rule.id,
                target=target,
                probe=probe,
                reason=reason,
            )
        )
    return recommendations


def probe_domains(
    router: RouterClient, domains: list[str]
) -> dict[str, RouteProbe]:
    unique = tuple(dict.fromkeys(domain.rstrip(".").lower() for domain in domains))
    for domain in unique:
        validate_domain(domain)
    if not unique:
        return {}
    values = " ".join(shlex.quote(domain) for domain in unique)
    script = f"""
wan_iface=$(nvram get wan_iface)
[ -n "$wan_iface" ] || wan_iface=$(get_wanface 2>/dev/null)
dns_server=$(nvram get lan_ipaddr)
for domain in {values}; do
    address=$(nslookup "$domain" "$dns_server" 2>/dev/null | awk '
        /^Name:/ {{ seen = 1; next }}
        seen && /^Address [0-9]+:/ {{
            if ($3 ~ /^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$/) {{
                print $3
                exit
            }}
        }}
    ')
    direct=-
    astrill=-
    if [ -n "$address" ]; then
        direct=$(ping -c 2 -W 2 -I "$wan_iface" "$address" 2>/dev/null |
            awk -F/ '
                /round-trip/ {{ print $4; exit }}
                /^rtt / {{ print $5; exit }}
            ')
        [ -n "$direct" ] || direct=-
        if ip link show tun0 >/dev/null 2>&1; then
            astrill=$(ping -c 2 -W 2 -I tun0 "$address" 2>/dev/null |
                awk -F/ '
                    /round-trip/ {{ print $4; exit }}
                    /^rtt / {{ print $5; exit }}
                ')
            [ -n "$astrill" ] || astrill=-
        fi
    fi
    printf '%s\\t%s\\t%s\\t%s\\n' "$domain" "$address" "$direct" "$astrill"
done
"""
    output = router.run_script(script, timeout=max(20, len(unique) * 10))
    probes: dict[str, RouteProbe] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 4 or parts[0] not in unique:
            continue
        domain, address, direct, astrill = parts
        probes[domain] = RouteProbe(
            domain=domain,
            address=address,
            direct_ms=_latency(direct),
            astrill_ms=_latency(astrill),
        )
    for domain in unique:
        probes.setdefault(domain, RouteProbe(domain, "", None, None))
    return probes


def recommend_target(
    probe: RouteProbe,
    *,
    current: RouteTarget,
    minimum_direct: bool = False,
    preferred: RouteTarget | None = None,
) -> tuple[RouteTarget, str]:
    if minimum_direct:
        return RouteTarget.DIRECT, "Minimum bypass"
    direct = probe.direct_ms
    astrill = probe.astrill_ms
    if direct is None and astrill is None:
        return current, "No reliable probe"
    if direct is None or astrill is None:
        return current, "Paths could not be compared"
    if preferred is not None:
        profile = "Direct" if preferred is RouteTarget.DIRECT else "Astrill"
        return preferred, f"{profile} service profile"
    assert direct is not None and astrill is not None
    improvement = abs(direct - astrill)
    noise_floor = max(20.0, min(direct, astrill) * 0.15)
    if improvement < noise_floor:
        return current, "Paths are effectively equal"
    if direct < astrill:
        return RouteTarget.DIRECT, "Direct is faster"
    return RouteTarget.VPN, "Astrill is faster"


def _representative_domain(rule: Rule, catalog: Catalog) -> str | None:
    if rule.match_kind is MatchKind.DOMAIN:
        return rule.selector
    if rule.match_kind is not MatchKind.SERVICE:
        return None
    service = catalog.services_by_id.get(rule.selector)
    if service is None:
        return None
    source_host = urlparse(service.source).hostname
    if source_host:
        return source_host.lower()
    return service.domains[0]


def _latency(value: str) -> float | None:
    if value in {"", "-"}:
        return None
    try:
        return round(float(value), 1)
    except ValueError:
        return None
