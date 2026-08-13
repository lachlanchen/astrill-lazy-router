from __future__ import annotations

import ipaddress
import socket
from unittest import mock

import pytest
from astrill_lazy.direct_device_proxy import (
    DirectDeviceProxyError,
    build_policy,
    normalize_hostname,
    parse_connect_authority,
    parse_override,
    parse_source,
)


def test_connect_authority_requires_hostname_and_explicit_port() -> None:
    assert parse_connect_authority("firebaseinstallations.googleapis.com:443") == (
        "firebaseinstallations.googleapis.com",
        443,
    )

    for value in (
        "firebaseinstallations.googleapis.com",
        "firebaseinstallations.googleapis.com:https",
        "127.0.0.1:443",
        "[::1]:443",
        "bad_name.example:443",
    ):
        with pytest.raises(DirectDeviceProxyError):
            parse_connect_authority(value)


def test_hostname_normalization_is_bounded() -> None:
    assert normalize_hostname("FCMRegistrations.GoogleApis.com.") == (
        "fcmregistrations.googleapis.com"
    )
    for value in ("", "localhost", "-bad.example", "bad-.example"):
        with pytest.raises(DirectDeviceProxyError):
            normalize_hostname(value)


def test_source_is_exact_private_ipv4() -> None:
    assert parse_source("192.168.1.132") == ipaddress.IPv4Address("192.168.1.132")
    for value in ("8.8.8.8", "::1", "not-an-address"):
        with pytest.raises(DirectDeviceProxyError):
            parse_source(value)


def test_override_requires_public_ipv4() -> None:
    assert parse_override("firebaseinstallations.googleapis.com=142.251.170.95") == (
        "firebaseinstallations.googleapis.com",
        ipaddress.IPv4Address("142.251.170.95"),
    )
    for value in (
        "firebaseinstallations.googleapis.com=127.0.0.1",
        "firebaseinstallations.googleapis.com=::1",
        "missing-separator",
    ):
        with pytest.raises(DirectDeviceProxyError):
            parse_override(value)


def test_policy_deduplicates_overrides_and_allowlists_one_source() -> None:
    policy = build_policy(
        ["192.168.1.132"],
        ["firebaseinstallations.googleapis.com"],
        [
            "firebaseinstallations.googleapis.com=142.251.170.95",
            "firebaseinstallations.googleapis.com=142.251.170.95",
            "firebaseinstallations.googleapis.com=142.251.8.95",
        ],
    )

    assert policy.allows_source("192.168.1.132")
    assert not policy.allows_source("192.168.1.133")
    assert policy.candidate_addresses("firebaseinstallations.googleapis.com", 443) == (
        "142.251.170.95",
        "142.251.8.95",
    )
    with pytest.raises(DirectDeviceProxyError, match="port is not allowed"):
        policy.candidate_addresses("firebaseinstallations.googleapis.com", 80)
    with pytest.raises(DirectDeviceProxyError, match="hostname is not allowed"):
        policy.candidate_addresses("example.com", 443)


def test_normal_resolution_keeps_public_ipv4_only() -> None:
    policy = build_policy(["192.168.1.132"], ["example.com"], [])
    records = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ]

    with mock.patch("socket.getaddrinfo", return_value=records):
        assert policy.candidate_addresses("example.com", 443) == ("93.184.216.34",)


def test_policy_rejects_missing_sources_and_unbounded_timeouts() -> None:
    with pytest.raises(DirectDeviceProxyError, match="source"):
        build_policy([], ["example.com"], [])
    with pytest.raises(DirectDeviceProxyError, match="hostname"):
        build_policy(["192.168.1.132"], [], [])
    with pytest.raises(DirectDeviceProxyError, match="not allowlisted"):
        build_policy(
            ["192.168.1.132"],
            ["example.com"],
            ["firebaseinstallations.googleapis.com=142.251.170.95"],
        )
    with pytest.raises(DirectDeviceProxyError, match="timeouts"):
        build_policy(
            ["192.168.1.132"],
            ["example.com"],
            [],
            idle_timeout_seconds=0,
        )
    with pytest.raises(DirectDeviceProxyError, match="session timeout"):
        build_policy(
            ["192.168.1.132"],
            ["example.com"],
            [],
            idle_timeout_seconds=10,
            session_timeout_seconds=5,
        )
