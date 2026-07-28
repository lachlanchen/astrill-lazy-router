from __future__ import annotations

import pytest
from astrill_lazy.catalog import load_catalog
from astrill_lazy.compiler import MAX_COMPILED_BYTES, compile_rules
from astrill_lazy.models import MatchKind, Protocol, RouteTarget, Rule
from astrill_lazy.store import default_uu_rule


def test_core_catalog_contains_requested_services() -> None:
    catalog = load_catalog()
    identifiers = set(catalog.services_by_id)
    assert {
        "uu-remote",
        "meta",
        "instagram",
        "x",
        "youtube",
        "google",
        "apple",
        "alibaba",
        "taobao",
        "tencent",
        "wechat",
        "qq",
        "wecom",
        "meituan",
        "xiaohongshu",
        "bytedance",
        "douyin",
        "tiktok",
        "bilibili",
        "sohu",
        "yahoo",
        "sina",
        "netease",
        "xiaoyunque",
        "claude",
        "openai",
        "chatgpt",
        "gemini",
        "copilot",
        "github",
        "nutstore",
    } <= identifiers
    assert len(catalog.services) >= 250
    assert {item.profile_type for item in catalog.services} == {
        "company",
        "app",
        "website",
    }
    assert all(item.source.startswith("https://") for item in catalog.services)
    nutstore = catalog.services_by_id["nutstore"]
    assert nutstore.default_route is RouteTarget.DIRECT
    assert {"app.jianguoyun.com", "comet.jianguoyun.com"} <= set(nutstore.domains)


def test_default_uu_rule_compiles_to_direct_domain() -> None:
    compilation = compile_rules([default_uu_rule()], load_catalog())
    assert not compilation.warnings
    assert len(compilation.rules) == 1
    compiled = compilation.rules[0]
    assert compiled.selector == "uuyc.163.com"
    assert compiled.target is RouteTarget.DIRECT
    assert compiled.origin == "uu-remote-direct"


def test_service_rule_expands_every_seed_domain() -> None:
    catalog = load_catalog()
    service = catalog.services_by_id["youtube"]
    rule = Rule.create(
        name="YouTube",
        match_kind=MatchKind.SERVICE,
        selector=service.id,
        target=RouteTarget.VPN,
        region="united-states",
    )
    compilation = compile_rules([rule], catalog)
    assert len(compilation.rules) == len(service.domains)
    assert {item.selector for item in compilation.rules} == set(service.domains)


def test_compiler_rejects_policy_larger_than_router_contract() -> None:
    catalog = load_catalog()
    rules = [
        Rule.create(
            name=f"Google {index}",
            match_kind=MatchKind.SERVICE,
            selector="google",
            target=RouteTarget.VPN,
        )
        for index in range(40)
    ]
    with pytest.raises(ValueError, match=f"router limit is {MAX_COMPILED_BYTES:,}"):
        compile_rules(rules, catalog)


def test_process_rule_requires_allocated_identity() -> None:
    catalog = load_catalog()
    rule = Rule.create(
        name="Browser",
        match_kind=MatchKind.PROCESS,
        selector="/usr/bin/true",
        target=RouteTarget.VPN,
    )
    missing = compile_rules([rule], catalog)
    assert not missing.rules
    assert "launch the application once" in missing.warnings[0]

    rule.metadata["namespace_ip"] = "192.168.1.240"
    compiled = compile_rules([rule], catalog)
    assert compiled.rules[0].kind == "device"
    assert compiled.rules[0].selector == "192.168.1.240"


def test_multiple_requested_vpn_regions_warn() -> None:
    catalog = load_catalog()
    rules = [
        Rule.create(
            name="US",
            match_kind=MatchKind.DOMAIN,
            selector="example.com",
            target=RouteTarget.VPN,
            region="united-states",
        ),
        Rule.create(
            name="Japan",
            match_kind=MatchKind.DOMAIN,
            selector="example.org",
            target=RouteTarget.VPN,
            region="japan",
        ),
    ]
    compilation = compile_rules(rules, catalog)
    assert any(
        "cannot be active simultaneously" in item for item in compilation.warnings
    )


def test_ipv6_and_any_protocol_ports_are_rejected() -> None:
    ipv6 = Rule.create(
        name="IPv6",
        match_kind=MatchKind.CIDR,
        selector="2001:db8::/32",
        target=RouteTarget.DIRECT,
        region="direct",
    )
    with pytest.raises(ValueError, match="IPv4"):
        ipv6.validate()

    ports = Rule.create(
        name="Ports",
        match_kind=MatchKind.DOMAIN,
        selector="example.com",
        target=RouteTarget.VPN,
    )
    ports.protocol = Protocol.ANY
    ports.ports = "443"
    with pytest.raises(ValueError, match="TCP or UDP"):
        ports.validate()
