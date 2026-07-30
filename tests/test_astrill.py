from __future__ import annotations

import gzip

import pytest
from astrill_lazy.astrill import (
    ASTRILL_PROTOCOL_NAMES,
    AstrillConnectionSelection,
    AstrillFavorite,
    AstrillServer,
    endpoint_country_name,
    group_by_region,
    parse_applet,
    parse_astrill_favorites,
    serialize_astrill_favorites,
    unpack_applet,
    update_astrill_favorite_list,
    update_astrill_favorite_list_batch,
)
from astrill_lazy.models import Region

SCRIPT = (
    "this.list = [{id:1,name:'USA - Test',servers:["
    "{id:7,lf:1,ips:["
    "{ip:123,port:'443',mode:0,proto:134,index:0,protop:5},"
    "{ip:124,port:'80',mode:0,proto:6,index:0,protop:5}"
    "]}]}];"
)


def test_plain_applet_parser_and_protocol_modes() -> None:
    servers = parse_applet(SCRIPT.encode())
    assert len(servers) == 1
    server = servers[0]
    assert server.name == "USA - Test"

    sid, router_pro = server.endpoint_for(2)
    assert sid == 7
    assert router_pro.encoded_ip == 123
    assert router_pro.vpn_mode_for(2) == 6

    _, openvpn = server.endpoint_for(0)
    assert openvpn.encoded_ip == 124
    assert openvpn.vpn_mode_for(0) == 5


def test_self_extracting_applet_is_unpacked_without_eval() -> None:
    compressed = gzip.compress(SCRIPT.encode())
    wrapper = f'#!/bin/sh\ntail -c {len(compressed)} "$0" | gzip -d\n'.encode()
    assert unpack_applet(wrapper + compressed) == SCRIPT


def test_servers_group_by_catalog_region_tokens() -> None:
    servers = parse_applet(SCRIPT.encode())
    regions = (
        Region("united-states", "United States", "astrill", ("USA",)),
        Region("other", "Other", "astrill"),
    )
    grouped = group_by_region(servers, regions)
    assert grouped["united-states"] == servers
    assert grouped["other"] == ()


def test_endpoint_country_uses_applet_country_names_and_aliases() -> None:
    assert endpoint_country_name("*USA - Los Angeles 10G") == "United States"
    assert endpoint_country_name("*UK - London VX1") == "United Kingdom"
    assert endpoint_country_name("*Hong Kong Supercharged 2") == "Hong Kong"
    assert endpoint_country_name("[China] Supercharged 4") == "China"
    assert endpoint_country_name("*Czechia - Prague VX1") == "Czech Republic"
    assert endpoint_country_name("Korea 2") == "South Korea"


def test_endpoint_country_normalizes_city_only_us_locations() -> None:
    assert endpoint_country_name("*Los Angeles Supercharged 3") == "United States"
    assert endpoint_country_name("*Seattle Supercharged 2") == "United States"
    assert endpoint_country_name("*Buffalo") == "United States"


def test_protocol_names_match_applet_codes() -> None:
    assert ASTRILL_PROTOCOL_NAMES == (
        "OpenVPN UDP",
        "OpenVPN TCP",
        "RouterPro VPN UDP",
        "RouterPro VPN TCP",
    )


def test_connection_options_follow_protocol_and_port_records() -> None:
    server = parse_applet(
        b"this.list = [{id:9,name:'Test',servers:["
        b"{id:70,lf:1,ips:["
        b"{ip:100,port:8292,mode:0,proto:0,index:1,protop:5},"
        b"{ip:101,port:53,mode:0,proto:0,index:2,protop:5},"
        b"{ip:102,port:443,mode:1,proto:1,index:1,protop:6},"
        b"{ip:103,port:'1-65535',mode:0,proto:134,index:0}"
        b"]},"
        b"{id:71,lf:1,ips:["
        b"{ip:200,port:8292,mode:0,proto:0,index:1,protop:5},"
        b"{ip:201,port:53,mode:0,proto:0,index:2,protop:5},"
        b"{ip:202,port:443,mode:1,proto:1,index:1,protop:6},"
        b"{ip:203,port:'1-65535',mode:0,proto:134,index:0}"
        b"]}]}];"
    )[0]

    assert server.supported_protocols() == (0, 1, 2)
    assert [(item.index, item.port) for item in server.port_options(0)] == [
        (1, "8292"),
        (2, "53"),
    ]
    selection = AstrillConnectionSelection.from_server(server, 0, 2)
    assert selection.native_values() == {
        "astrill_serverid": "9",
        "astrill_sid": "70",
        "astrill_ip": "101",
        "astrill_port": "53",
        "astrill_portindex": "2",
        "astrill_protocol": "0",
        "astrill_vpnmode": "5",
    }


def test_applet_address_map_exposes_a_tcp_latency_target() -> None:
    server = parse_applet(
        b"_AS42=';102=203.0.113.42;103=203.0.113.43;';\n"
        b"this.list = [{id:9,name:'Test',servers:["
        b"{id:70,lf:1,ips:["
        b"{ip:102,port:443,mode:1,proto:1,index:1,protop:6},"
        b"{ip:103,port:'1-65535',mode:1,proto:134,index:0}"
        b"]}]}];"
    )[0]

    assert server.nodes[0].endpoints[0].address == "203.0.113.42"
    assert server.nodes[0].endpoints[1].address == "203.0.113.43"
    assert server.tcp_probe_target() == ("203.0.113.42", 443)

    range_only = parse_applet(
        b"_AS42=';103=203.0.113.43;';\n"
        b"this.list = [{id:10,name:'Range',servers:["
        b"{id:71,lf:1,ips:["
        b"{ip:103,port:'1-65535',mode:1,proto:134,index:0}"
        b"]}]}];"
    )[0]
    assert range_only.tcp_probe_target() == ("203.0.113.43", 443)


def test_empty_server_has_no_connection_options() -> None:
    server = AstrillServer(9, "Empty", ())

    assert server.supported_protocols() == ()
    assert server.port_options(0) == ()
    assert server.tcp_probe_target() is None


def test_favorites_round_trip_in_native_applet_format() -> None:
    original = "1109:536872021:1-65535:0:6:1109,458:536871370:443:1:6:458"
    favorites = parse_astrill_favorites(original)

    assert favorites[0].server_id == 1109
    assert serialize_astrill_favorites(favorites) == original
    assert AstrillFavorite.parse(favorites[0].to_native()) == favorites[0]


def test_favorite_list_updates_preserve_order_orphans_and_existing_records() -> None:
    original = (
        "1109:536872021:1-65535:0:6:1109,"
        "9999:123:443:1:6:9999,"
        "458:536871370:443:1:6:458"
    )
    added = AstrillFavorite(700, 456, "8292", 0, 5, 701)

    assert update_astrill_favorite_list(original, 700, added) == (
        original + ",700:456:8292:0:5:701"
    )
    assert update_astrill_favorite_list(original, 9999, None) == (
        "1109:536872021:1-65535:0:6:1109,458:536871370:443:1:6:458"
    )

    different_existing = AstrillFavorite(458, 1, "53", 0, 1, 2)
    assert update_astrill_favorite_list(original, 458, different_existing) == original
    assert update_astrill_favorite_list(original, 700, None) == original


def test_favorite_list_update_blocks_malformed_or_mismatched_records() -> None:
    favorite = AstrillFavorite(700, 456, "8292", 0, 5, 701)

    with pytest.raises(ValueError, match="favorite record"):
        update_astrill_favorite_list("invalid", 700, favorite)
    with pytest.raises(ValueError, match="does not match"):
        update_astrill_favorite_list("", 701, favorite)
    with pytest.raises(ValueError, match="server ID must be positive"):
        update_astrill_favorite_list("", 0, None)


def test_favorite_list_batch_preserves_order_orphans_and_request_order() -> None:
    original = (
        "1109:536872021:1-65535:0:6:1109,"
        "9999:123:443:1:6:9999,"
        "458:536871370:443:1:6:458"
    )
    existing_replacement = AstrillFavorite(458, 1, "53", 0, 1, 2)
    first_added = AstrillFavorite(700, 456, "8292", 0, 5, 701)
    second_added = AstrillFavorite(701, 457, "443", 1, 6, 702)

    updated = update_astrill_favorite_list_batch(
        original,
        (
            (458, existing_replacement),
            (1109, None),
            (700, first_added),
            (701, second_added),
        ),
    )

    assert updated == (
        "9999:123:443:1:6:9999,"
        "458:536871370:443:1:6:458,"
        "700:456:8292:0:5:701,"
        "701:457:443:1:6:702"
    )


def test_favorite_list_batch_validates_all_changes_and_keeps_noops_exact() -> None:
    original = "1109:536872021:1-65535:0:6:1109"
    favorite = AstrillFavorite(700, 456, "8292", 0, 5, 701)

    assert (
        update_astrill_favorite_list_batch(
            original,
            (
                (1109, AstrillFavorite(1109, 1, "53", 0, 1, 2)),
                (700, None),
            ),
        )
        == original
    )
    with pytest.raises(ValueError, match="duplicate server ID"):
        update_astrill_favorite_list_batch(
            original,
            ((700, favorite), (700, None)),
        )
    with pytest.raises(ValueError, match="does not match"):
        update_astrill_favorite_list_batch(original, ((701, favorite),))
    with pytest.raises(ValueError, match="favorite record"):
        update_astrill_favorite_list_batch("invalid", ())
