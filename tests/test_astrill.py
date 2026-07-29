from __future__ import annotations

import gzip

from astrill_lazy.astrill import (
    ASTRILL_PROTOCOL_NAMES,
    AstrillConnectionSelection,
    AstrillFavorite,
    AstrillServer,
    group_by_region,
    parse_applet,
    parse_astrill_favorites,
    serialize_astrill_favorites,
    unpack_applet,
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


def test_empty_server_has_no_connection_options() -> None:
    server = AstrillServer(9, "Empty", ())

    assert server.supported_protocols() == ()
    assert server.port_options(0) == ()


def test_favorites_round_trip_in_native_applet_format() -> None:
    original = "1109:536872021:1-65535:0:6:1109,458:536871370:443:1:6:458"
    favorites = parse_astrill_favorites(original)

    assert favorites[0].server_id == 1109
    assert serialize_astrill_favorites(favorites) == original
    assert AstrillFavorite.parse(favorites[0].to_native()) == favorites[0]
