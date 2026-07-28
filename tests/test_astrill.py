from __future__ import annotations

import gzip

from astrill_lazy.astrill import group_by_region, parse_applet, unpack_applet
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
