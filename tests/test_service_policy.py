from __future__ import annotations

from astrill_lazy.catalog import load_catalog
from astrill_lazy.models import RouteTarget
from astrill_lazy.service_policy import ServiceRouteMode, service_policy_route


def test_suggested_service_routes_keep_catalog_mix() -> None:
    services = load_catalog().services_by_id

    assert service_policy_route(services["uu-remote"], ServiceRouteMode.SUGGESTED) == (
        RouteTarget.DIRECT,
        "direct",
    )
    assert service_policy_route(services["youtube"], ServiceRouteMode.SUGGESTED) == (
        RouteTarget.VPN,
        "united-states",
    )


def test_batch_route_can_force_direct_or_astrill() -> None:
    service = load_catalog().services_by_id["uu-remote"]

    assert service_policy_route(service, ServiceRouteMode.DIRECT) == (
        RouteTarget.DIRECT,
        "direct",
    )
    assert service_policy_route(service, ServiceRouteMode.VPN) == (
        RouteTarget.VPN,
        "active-astrill",
    )
    assert service_policy_route(
        service,
        ServiceRouteMode.VPN,
        current_region="japan",
    ) == (RouteTarget.VPN, "japan")


def test_core_catalog_has_provider_country_for_every_service() -> None:
    catalog = load_catalog()

    assert len(catalog.services) == 261
    assert all(service.provider_country for service in catalog.services)
    assert catalog.services_by_id["wechat"].provider_country == "China"
    assert catalog.services_by_id["youtube"].provider_country == "United States"
    assert catalog.services_by_id["line"].provider_country == "Japan"
    assert catalog.services_by_id["grab"].provider_country == "Singapore"
