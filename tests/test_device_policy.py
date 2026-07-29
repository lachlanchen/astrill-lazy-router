from __future__ import annotations

import json
from pathlib import Path

import pytest
from astrill_lazy.device_policy import (
    AUTO_HOLD_SECONDS,
    CountryGroup,
    CountryNetwork,
    DeviceFallback,
    DevicePolicy,
    DeviceRouteMode,
    DeviceRule,
    DeviceRuleKind,
    PathProbe,
    RouteAction,
    SelectedPathKind,
    TrafficContext,
    TunnelSlot,
    compile_country_routes,
    decide_route,
    load_device_policy,
    select_path,
)


def _tunnel(tunnel_id: str) -> TunnelSlot:
    return TunnelSlot(
        id=tunnel_id,
        name=tunnel_id,
        provider="openvpn",
        region="test-region",
        configuration_ref=f"profiles/{tunnel_id}.ovpn",
    )


def _policy(
    *rules: DeviceRule, default_route: RouteAction | None = None
) -> DevicePolicy:
    return DevicePolicy(
        default_route=default_route or RouteAction(DeviceRouteMode.DIRECT),
        tunnels=(_tunnel("los-angeles"), _tunnel("tokyo"), _tunnel("singapore")),
        rules=rules,
    )


def test_device_policy_rejects_more_than_three_tunnels() -> None:
    policy = DevicePolicy(
        default_route=RouteAction(DeviceRouteMode.DIRECT),
        tunnels=tuple(_tunnel(f"tunnel-{index}") for index in range(4)),
    )
    with pytest.raises(ValueError, match="at most 3 tunnels"):
        policy.validate()


def test_device_policy_rejects_unknown_tunnel_reference() -> None:
    policy = DevicePolicy(
        default_route=RouteAction(
            DeviceRouteMode.TUNNEL, ("not-configured",), DeviceFallback.BLOCK
        )
    )
    with pytest.raises(ValueError, match="unknown tunnel"):
        policy.validate()


def test_device_policy_rejects_ambiguous_direct_fallback() -> None:
    with pytest.raises(ValueError, match="cannot declare a fallback"):
        RouteAction.from_dict({"mode": "direct", "fallback": "block"})


def test_device_policy_rejects_string_boolean() -> None:
    value = _policy().to_dict()
    value["tunnels"][0]["enabled"] = "false"
    with pytest.raises(TypeError, match="enabled must be a boolean"):
        DevicePolicy.from_dict(value)


def test_device_policy_rejects_route_to_disabled_tunnel() -> None:
    policy = DevicePolicy(
        default_route=RouteAction(DeviceRouteMode.TUNNEL, ("los-angeles",)),
        tunnels=(
            TunnelSlot(
                id="los-angeles",
                name="Los Angeles",
                provider="openvpn",
                region="united-states",
                configuration_ref="profiles/los-angeles.ovpn",
                enabled=False,
            ),
        ),
    )
    with pytest.raises(ValueError, match="disabled tunnel"):
        policy.validate()


def test_device_policy_rejects_untyped_route_mode() -> None:
    with pytest.raises(TypeError, match="DeviceRouteMode"):
        RouteAction("direct").validate_shape()  # type: ignore[arg-type]


def test_narrow_rule_wins_at_the_same_priority() -> None:
    country = DeviceRule(
        id="china-direct",
        name="China",
        kind=DeviceRuleKind.COUNTRY,
        selector="CN",
        route=RouteAction(DeviceRouteMode.DIRECT),
        priority=200,
    )
    service = DeviceRule(
        id="uu-via-tokyo",
        name="UU Remote",
        kind=DeviceRuleKind.SERVICE,
        selector="uu-remote",
        route=RouteAction(DeviceRouteMode.TUNNEL, ("tokyo",)),
        priority=200,
    )
    decision = decide_route(
        _policy(country, service),
        TrafficContext(service_ids=("uu-remote",), country_code="CN"),
    )
    assert decision.rule_id == "uu-via-tokyo"


def test_explicit_priority_can_override_specificity() -> None:
    country = DeviceRule(
        id="china-direct",
        name="China",
        kind=DeviceRuleKind.COUNTRY,
        selector="CN",
        route=RouteAction(DeviceRouteMode.DIRECT),
        priority=100,
    )
    service = DeviceRule(
        id="uu-via-tokyo",
        name="UU Remote",
        kind=DeviceRuleKind.SERVICE,
        selector="uu-remote",
        route=RouteAction(DeviceRouteMode.TUNNEL, ("tokyo",)),
        priority=200,
    )
    decision = decide_route(
        _policy(country, service),
        TrafficContext(service_ids=("uu-remote",), country_code="CN"),
    )
    assert decision.rule_id == "china-direct"


def test_named_country_group_matches_a_member_country() -> None:
    europe = CountryGroup(
        id="europe",
        name="Europe",
        country_codes=("DE", "FR", "GB"),
    )
    rule = DeviceRule(
        id="europe-tokyo",
        name="Europe",
        kind=DeviceRuleKind.COUNTRY,
        selector="europe",
        route=RouteAction(DeviceRouteMode.TUNNEL, ("tokyo",)),
    )
    policy = DevicePolicy(
        default_route=RouteAction(DeviceRouteMode.DIRECT),
        tunnels=(_tunnel("tokyo"),),
        country_groups=(europe,),
        rules=(rule,),
    )
    decision = decide_route(policy, TrafficContext(country_code="DE"))
    assert decision.rule_id == "europe-tokyo"


def test_individual_country_wins_over_group_at_same_priority() -> None:
    europe = CountryGroup(
        id="europe",
        name="Europe",
        country_codes=("DE", "FR"),
    )
    group_rule = DeviceRule(
        id="a-europe-tokyo",
        name="Europe",
        kind=DeviceRuleKind.COUNTRY,
        selector="europe",
        route=RouteAction(DeviceRouteMode.TUNNEL, ("tokyo",)),
    )
    germany_rule = DeviceRule(
        id="z-germany-direct",
        name="Germany",
        kind=DeviceRuleKind.COUNTRY,
        selector="DE",
        route=RouteAction(DeviceRouteMode.DIRECT),
    )
    policy = DevicePolicy(
        default_route=RouteAction(DeviceRouteMode.TUNNEL, ("tokyo",)),
        tunnels=(_tunnel("tokyo"),),
        country_groups=(europe,),
        rules=(group_rule, germany_rule),
    )
    decision = decide_route(policy, TrafficContext(country_code="DE"))
    assert decision.rule_id == "z-germany-direct"


def test_unknown_country_group_is_rejected() -> None:
    rule = DeviceRule(
        id="unknown-group",
        name="Unknown",
        kind=DeviceRuleKind.COUNTRY,
        selector="not-configured",
        route=RouteAction(DeviceRouteMode.DIRECT),
    )
    with pytest.raises(ValueError, match="unknown country group"):
        _policy(rule).validate()


def test_domain_rule_matches_subdomains() -> None:
    rule = DeviceRule(
        id="netease-direct",
        name="NetEase",
        kind=DeviceRuleKind.DOMAIN,
        selector="163.com",
        route=RouteAction(DeviceRouteMode.DIRECT),
    )
    decision = decide_route(
        _policy(
            rule,
            default_route=RouteAction(DeviceRouteMode.TUNNEL, ("los-angeles",)),
        ),
        TrafficContext(domain="API.NRD.163.com."),
    )
    assert decision.rule_id == "netease-direct"


def test_auto_path_uses_lowest_healthy_score() -> None:
    now = 1_000.0
    action = RouteAction(
        DeviceRouteMode.AUTO,
        ("los-angeles", "tokyo", "singapore"),
        DeviceFallback.DIRECT,
    )
    selected = select_path(
        action,
        (
            PathProbe("los-angeles", True, 160, 0, now),
            PathProbe("tokyo", True, 60, 1, now),
            PathProbe("singapore", True, 45, 0, now),
        ),
        now=now,
    )
    assert selected.kind is SelectedPathKind.TUNNEL
    assert selected.tunnel_id == "singapore"


def test_auto_path_hysteresis_keeps_current_tunnel() -> None:
    now = 1_000.0
    action = RouteAction(
        DeviceRouteMode.AUTO, ("tokyo", "singapore"), DeviceFallback.DIRECT
    )
    selected = select_path(
        action,
        (
            PathProbe("tokyo", True, 50, 0, now),
            PathProbe("singapore", True, 45, 0, now),
        ),
        current_tunnel_id="tokyo",
        last_switch_at=now - AUTO_HOLD_SECONDS - 1,
        now=now,
    )
    assert selected.tunnel_id == "tokyo"
    assert "hysteresis" in selected.reason


def test_unavailable_fixed_tunnel_honors_block_fallback() -> None:
    action = RouteAction(DeviceRouteMode.TUNNEL, ("los-angeles",), DeviceFallback.BLOCK)
    selected = select_path(action, (), now=1_000)
    assert selected.kind is SelectedPathKind.BLOCK


def test_future_probe_is_not_treated_as_healthy() -> None:
    action = RouteAction(
        DeviceRouteMode.TUNNEL, ("los-angeles",), DeviceFallback.DIRECT
    )
    selected = select_path(
        action,
        (PathProbe("los-angeles", True, 20, 0, 1_001),),
        now=1_000,
    )
    assert selected.kind is SelectedPathKind.DIRECT


def test_country_route_compilation_collapses_adjacent_networks() -> None:
    china = DeviceRule(
        id="china-direct",
        name="China",
        kind=DeviceRuleKind.COUNTRY,
        selector="CN",
        route=RouteAction(DeviceRouteMode.DIRECT),
        priority=100,
    )
    policy = _policy(
        china,
        default_route=RouteAction(DeviceRouteMode.TUNNEL, ("los-angeles",)),
    )
    plans = compile_country_routes(
        policy,
        (
            CountryNetwork("CN", _network("1.0.0.0/25")),
            CountryNetwork("CN", _network("1.0.0.128/25")),
            CountryNetwork("US", _network("8.8.8.0/24")),
        ),
    )
    direct = next(plan for plan in plans if plan.route.mode is DeviceRouteMode.DIRECT)
    tunnel = next(plan for plan in plans if plan.route.mode is DeviceRouteMode.TUNNEL)
    assert direct.networks == ("1.0.0.0/24",)
    assert tunnel.networks == ("8.8.8.0/24",)


def test_country_route_compilation_rejects_overlaps() -> None:
    policy = _policy()
    with pytest.raises(ValueError, match="overlap"):
        compile_country_routes(
            policy,
            (
                CountryNetwork("CN", _network("1.0.0.0/24")),
                CountryNetwork("US", _network("1.0.0.128/25")),
            ),
        )


def test_policy_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(
        '{"schema_version":1,"schema_version":1,"default_route":{"mode":"direct"}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_device_policy(path)


def test_policy_loader_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(
        '{"schema_version":1,"default_route":{"mode":"direct"},'
        '"tunnels":[],"rules":[],"secret":"not-allowed"}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown fields: secret"):
        load_device_policy(path)


def test_policy_round_trip() -> None:
    policy = _policy(
        DeviceRule(
            id="uu-direct",
            name="UU Remote",
            kind=DeviceRuleKind.SERVICE,
            selector="uu-remote",
            route=RouteAction(DeviceRouteMode.DIRECT),
            priority=100,
        ),
        default_route=RouteAction(
            DeviceRouteMode.AUTO,
            ("los-angeles", "tokyo", "singapore"),
            DeviceFallback.DIRECT,
        ),
    )
    assert DevicePolicy.from_dict(json.loads(json.dumps(policy.to_dict()))) == policy


def _network(value: str):
    import ipaddress

    return ipaddress.ip_network(value)
