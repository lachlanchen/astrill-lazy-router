from __future__ import annotations

import ipaddress

import pytest
from astrill_lazy.models import RouteTarget
from astrill_lazy.native_settings import (
    SAFE_NATIVE_ASTRILL_KEYS,
    NativeAstrillSettings,
    NativeDevice,
    compile_native_ip_list,
    device_policy_changes,
    normalize_native_changes,
    normalize_site_entries,
    site_policy_changes,
)
from astrill_lazy.router import RouterClient


def test_native_modes_flatten_to_direct_or_astrill() -> None:
    settings = NativeAstrillSettings.from_dict(
        {
            "astrill_routingmode": "2",
            "astrill_devmode": "1",
        }
    )
    assert settings.site_policy.default is RouteTarget.VPN
    assert settings.site_policy.exception is RouteTarget.DIRECT
    assert settings.device_policy.default is RouteTarget.DIRECT
    assert settings.device_policy.exception is RouteTarget.VPN


def test_site_policy_builds_native_mode_and_compiled_ip_list() -> None:
    raw = """
example.com
192.0.2.8
192.0.2.9/32
198.51.100.4 - 198.51.100.7 # relay range
"""
    changes = site_policy_changes(RouteTarget.VPN, raw)
    assert changes["astrill_routingmode"] == "2"
    assert changes["astrill_iplistraw"] == normalize_site_entries(raw)
    networks = [
        ipaddress.IPv4Network(value) for value in changes["astrill_iplist"].split()
    ]
    assert ipaddress.IPv4Network("192.0.2.8/31") in networks
    assert ipaddress.IPv4Network("198.51.100.4/30") in networks
    assert compile_native_ip_list("example.org") == ""


def test_device_policy_serializes_native_exceptions() -> None:
    device = NativeDevice.parse("aa:bb:cc:dd:ee:ff/192.168.1.9/Office Mac")
    changes = device_policy_changes(RouteTarget.DIRECT, [device])
    assert changes == {
        "astrill_devmode": "1",
        "astrill_devices": "AA:BB:CC:DD:EE:FF/192.168.1.9/Office Mac",
    }
    settings = NativeAstrillSettings.from_dict(changes)
    assert settings.devices == (device,)


def test_native_change_validation_excludes_secrets_and_bad_values() -> None:
    with pytest.raises(ValueError, match="not writable"):
        normalize_native_changes({"astrill_token": "secret"})
    with pytest.raises(ValueError, match="between 576 and 1500"):
        normalize_native_changes({"astrill_wanmtu": "400"})
    with pytest.raises(ValueError, match="invalid native Astrill site entry"):
        normalize_native_changes({"astrill_iplistraw": "not a domain"})
    with pytest.raises(ValueError, match="favorite record"):
        normalize_native_changes({"astrill_favlist": "invalid"})

    favorite = "1109:536872021:1-65535:0:6:1109"
    assert normalize_native_changes({"astrill_favlist": favorite}) == {
        "astrill_favlist": favorite
    }


def test_router_native_settings_hex_transport_and_verified_write() -> None:
    values = {key: "" for key in SAFE_NATIVE_ASTRILL_KEYS}
    values["astrill_iplistraw"] = "example.com\n192.0.2.1/32"

    class FakeRouter(RouterClient):
        def __init__(self) -> None:
            super().__init__("unused")
            self.write_script = ""

        def run_script(self, script: str, *, timeout: int = 60) -> str:
            if "hexdump" in script:
                return "\n".join(
                    f"{key}\t{(value + chr(10)).encode().hex()}"
                    for key, value in values.items()
                )
            self.write_script = script
            values["astrill_adsblock"] = "1"
            return ""

    router = FakeRouter()
    settings = router.native_astrill_settings()
    assert settings.get("astrill_iplistraw") == "example.com\n192.0.2.1/32"

    updated = router.update_native_astrill_settings({"astrill_adsblock": "1"})
    assert updated.enabled("astrill_adsblock")
    assert "nvram set astrill_adsblock=1" in router.write_script
    assert "nvram commit" in router.write_script
