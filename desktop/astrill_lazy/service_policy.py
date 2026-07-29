from __future__ import annotations

from enum import StrEnum

from .models import RouteTarget, Service


class ServiceRouteMode(StrEnum):
    SUGGESTED = "suggested"
    DIRECT = "direct"
    VPN = "vpn"


def service_policy_route(
    service: Service,
    mode: ServiceRouteMode,
    *,
    current_region: str | None = None,
) -> tuple[RouteTarget, str]:
    if mode is ServiceRouteMode.SUGGESTED:
        return service.default_route, service.preferred_region
    if mode is ServiceRouteMode.DIRECT:
        return RouteTarget.DIRECT, "direct"

    region = current_region or service.preferred_region
    if region == "direct":
        region = "active-astrill"
    return RouteTarget.VPN, region
