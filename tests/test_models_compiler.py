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
    uu_remote = catalog.services_by_id["uu-remote"]
    assert "a56.gdl.netease.com" in uu_remote.domains
    assert set(uu_remote.networks) == {
        "8.221.56.176/32",
        "42.186.47.187/32",
        "115.236.122.145/32",
        "115.236.122.175/32",
        "223.252.194.149/32",
    }
    nutstore = catalog.services_by_id["nutstore"]
    assert nutstore.default_route is RouteTarget.DIRECT
    assert {
        "app.jianguoyun.com",
        "comet.jianguoyun.com",
        "dav.jianguoyun.com",
    } <= set(nutstore.domains)
    assert not nutstore.networks


def test_default_uu_rule_compiles_complete_global_direct_profile() -> None:
    compilation = compile_rules([default_uu_rule()], load_catalog())
    assert not compilation.warnings
    selectors = {compiled.selector for compiled in compilation.rules}
    assert {
        "uuyc.163.com",
        "api.nrd.nie.163.com",
        "sig-3303-d.nrd.nie.163.com",
        "relay-mg-3303-d.nrd.nie.163.com",
        "online-logger.webapp.163.com",
        "a56.gdl.netease.com",
        "8.221.56.176/32",
        "42.186.47.187/32",
        "115.236.122.145/32",
        "115.236.122.175/32",
        "223.252.194.149/32",
    } <= selectors
    assert all(compiled.target is RouteTarget.DIRECT for compiled in compilation.rules)
    assert all(compiled.protocol is Protocol.ANY for compiled in compilation.rules)
    assert all(compiled.ports == "-" for compiled in compilation.rules)
    assert all(compiled.origin == "uu-remote-direct" for compiled in compilation.rules)


def test_nutstore_compiles_official_webdav_host_without_port_restriction() -> None:
    rule = Rule.create(
        name="Nutstore",
        match_kind=MatchKind.SERVICE,
        selector="nutstore",
        target=RouteTarget.DIRECT,
        region="direct",
    )
    compilation = compile_rules([rule], load_catalog())

    assert "dav.jianguoyun.com" in {compiled.selector for compiled in compilation.rules}
    assert all(compiled.protocol is Protocol.ANY for compiled in compilation.rules)
    assert all(compiled.ports == "-" for compiled in compilation.rules)
    assert all(compiled.target is RouteTarget.DIRECT for compiled in compilation.rules)


def test_uu_and_nutstore_direct_profiles_fit_router_contract() -> None:
    nutstore_rule = Rule(
        id="nutstore-direct",
        name="Nutstore",
        match_kind=MatchKind.SERVICE,
        selector="nutstore",
        target=RouteTarget.DIRECT,
        region="direct",
        priority=7600,
    )
    compilation = compile_rules(
        [default_uu_rule(), nutstore_rule],
        load_catalog(),
    )

    assert len(compilation.rules) == 27
    assert len(compilation.to_tsv().encode("ascii")) <= MAX_COMPILED_BYTES


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
    assert len(compilation.rules) == len(service.domains) + len(service.networks)
    assert {item.selector for item in compilation.rules} == {
        *service.domains,
        *service.networks,
    }


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
