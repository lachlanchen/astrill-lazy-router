from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any

from .models import DOMAIN_RE, RouteTarget

SAFE_NATIVE_ASTRILL_KEYS = (
    "astrill_serverid",
    "astrill_sid",
    "astrill_ip",
    "astrill_port",
    "astrill_portindex",
    "astrill_protocol",
    "astrill_cipher",
    "astrill_wanmtu",
    "astrill_vpnmode",
    "astrill_accel",
    "astrill_blockinternet",
    "astrill_autocycle",
    "astrill_favlist",
    "astrill_routingmode",
    "astrill_devmode",
    "astrill_adsblock",
    "astrill_ifmode",
    "astrill_iflist",
    "astrill_ifexlist",
    "astrill_vlanmode",
    "astrill_vlanlist",
    "astrill_dmzdevice",
    "astrill_iplist",
    "astrill_iplistraw",
    "astrill_iplistext",
    "astrill_iplistfile",
    "astrill_exflt",
    "astrill_dnsserver",
    "astrill_userdns",
    "astrill_nosplitdns",
    "astrill_vpndnsallsites",
    "astrill_autostart",
    "astrill_devices",
    "astrill_status",
)

WRITABLE_NATIVE_ASTRILL_KEYS = frozenset(
    {
        "astrill_cipher",
        "astrill_wanmtu",
        "astrill_accel",
        "astrill_blockinternet",
        "astrill_autocycle",
        "astrill_routingmode",
        "astrill_devmode",
        "astrill_adsblock",
        "astrill_ifmode",
        "astrill_iflist",
        "astrill_ifexlist",
        "astrill_vlanmode",
        "astrill_vlanlist",
        "astrill_dmzdevice",
        "astrill_iplist",
        "astrill_iplistraw",
        "astrill_iplistext",
        "astrill_iplistfile",
        "astrill_exflt",
        "astrill_dnsserver",
        "astrill_userdns",
        "astrill_nosplitdns",
        "astrill_vpndnsallsites",
        "astrill_autostart",
        "astrill_devices",
    }
)

BOOL_KEYS = frozenset(
    {
        "astrill_accel",
        "astrill_blockinternet",
        "astrill_autocycle",
        "astrill_adsblock",
        "astrill_nosplitdns",
        "astrill_vpndnsallsites",
        "astrill_autostart",
    }
)

MODE_KEYS = {
    "astrill_routingmode": frozenset({"0", "1", "2", "3", "4"}),
    "astrill_devmode": frozenset({"0", "1", "2"}),
    "astrill_ifmode": frozenset({"0", "1", "2"}),
    "astrill_vlanmode": frozenset({"0", "1", "2"}),
    "astrill_iplistext": frozenset({"0", "1"}),
    "astrill_dnsserver": frozenset(
        {"0", "1", "2", "3", "7", "8", "9", "254", "255"}
    ),
    "astrill_cipher": frozenset(
        {"default", "AES-128-CBC", "AES-256-CBC", "none"}
    ),
}

MAC_RE = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
INTERFACE_LIST_RE = re.compile(r"^[a-zA-Z0-9_.:-]*(?:;[a-zA-Z0-9_.:-]+)*$")


@dataclass(frozen=True)
class EffectivePolicy:
    default: RouteTarget
    exception: RouteTarget
    automatic_mode: int | None = None


@dataclass(frozen=True)
class NativeDevice:
    mac: str
    address: str
    name: str

    @classmethod
    def parse(cls, value: str) -> NativeDevice:
        parts = value.split("/", 2)
        if len(parts) != 3:
            raise ValueError(f"invalid Astrill device record: {value!r}")
        mac, address, name = parts
        if not MAC_RE.fullmatch(mac):
            raise ValueError(f"invalid device MAC address: {mac!r}")
        ipaddress.IPv4Address(address)
        if any(character in name for character in "\x00;/"):
            raise ValueError(f"invalid device name: {name!r}")
        return cls(mac.upper(), address, name)

    def to_native(self) -> str:
        return f"{self.mac}/{self.address}/{self.name}"


@dataclass(frozen=True)
class NativeAstrillSettings:
    values: dict[str, str]

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> NativeAstrillSettings:
        normalized = {
            key: str(values.get(key, "")) for key in SAFE_NATIVE_ASTRILL_KEYS
        }
        return cls(normalized)

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    def integer(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get(key))
        except ValueError:
            return default

    def enabled(self, key: str) -> bool:
        return self.get(key) == "1"

    @property
    def site_policy(self) -> EffectivePolicy:
        mode = self.integer("astrill_routingmode")
        if mode == 1:
            return EffectivePolicy(RouteTarget.DIRECT, RouteTarget.VPN)
        if mode == 2:
            return EffectivePolicy(RouteTarget.VPN, RouteTarget.DIRECT)
        if mode in {3, 4}:
            exception = RouteTarget.DIRECT if mode == 3 else RouteTarget.VPN
            return EffectivePolicy(RouteTarget.DIRECT, exception, mode)
        return EffectivePolicy(RouteTarget.VPN, RouteTarget.DIRECT)

    @property
    def device_policy(self) -> EffectivePolicy:
        mode = self.integer("astrill_devmode")
        if mode == 1:
            return EffectivePolicy(RouteTarget.DIRECT, RouteTarget.VPN)
        return EffectivePolicy(RouteTarget.VPN, RouteTarget.DIRECT)

    @property
    def devices(self) -> tuple[NativeDevice, ...]:
        records: list[NativeDevice] = []
        for value in self.get("astrill_devices").split(";"):
            if not value:
                continue
            records.append(NativeDevice.parse(value))
        return tuple(records)


def binary_native_mode(default: RouteTarget, *, has_exceptions: bool) -> str:
    if default is RouteTarget.DIRECT:
        return "1"
    return "2" if has_exceptions else "0"


def site_policy_changes(
    default: RouteTarget, raw_entries: str
) -> dict[str, str]:
    entries = normalize_site_entries(raw_entries)
    return {
        "astrill_routingmode": binary_native_mode(
            default, has_exceptions=bool(entries)
        ),
        "astrill_iplistraw": entries,
        "astrill_iplist": compile_native_ip_list(entries),
    }


def device_policy_changes(
    default: RouteTarget, devices: list[NativeDevice]
) -> dict[str, str]:
    unique: dict[str, NativeDevice] = {}
    for device in devices:
        unique[device.mac] = device
    ordered = sorted(unique.values(), key=lambda item: (item.address, item.mac))
    return {
        "astrill_devmode": binary_native_mode(
            default, has_exceptions=bool(ordered)
        ),
        "astrill_devices": ";".join(item.to_native() for item in ordered),
    }


def normalize_site_entries(value: str) -> str:
    if "\x00" in value:
        raise ValueError("site entries cannot contain NUL characters")
    entries: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("#", ";")):
            entries.append(line)
            continue
        candidate = re.split(r"\s*[#;]", line, maxsplit=1)[0].strip()
        if _valid_site_selector(candidate):
            entries.append(line)
            continue
        raise ValueError(f"invalid native Astrill site entry: {line!r}")
    if len(entries) > 500:
        raise ValueError("native Astrill accepts at most 500 site entries")
    normalized = "\n".join(entries)
    if len(normalized.encode("utf-8")) > 10_000:
        raise ValueError("native Astrill site entries exceed 10,000 bytes")
    return normalized


def compile_native_ip_list(value: str) -> str:
    networks: list[ipaddress.IPv4Network] = []
    for raw_line in value.splitlines():
        line = re.split(r"\s*[#;]", raw_line.strip(), maxsplit=1)[0].strip()
        if not line:
            continue
        if "-" in line:
            bounds = [item.strip() for item in line.split("-", 1)]
            start, end = (ipaddress.IPv4Address(item) for item in bounds)
            if int(end) < int(start):
                raise ValueError(f"reversed IP range: {line!r}")
            networks.extend(ipaddress.summarize_address_range(start, end))
        else:
            try:
                network = ipaddress.IPv4Network(line, strict=False)
            except ValueError:
                if DOMAIN_RE.fullmatch(line.rstrip(".").lower()):
                    continue
                raise
            networks.append(network)
    return " ".join(str(item) for item in ipaddress.collapse_addresses(networks))


def normalize_native_changes(changes: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, raw_value in changes.items():
        if key not in WRITABLE_NATIVE_ASTRILL_KEYS:
            raise ValueError(f"native Astrill setting is not writable: {key}")
        value = str(raw_value)
        if "\x00" in value:
            raise ValueError(f"{key} cannot contain NUL characters")
        if len(value.encode("utf-8")) > 12_000:
            raise ValueError(f"{key} exceeds the router value limit")
        if key in BOOL_KEYS and value not in {"0", "1"}:
            raise ValueError(f"{key} must be 0 or 1")
        allowed = MODE_KEYS.get(key)
        if allowed is not None and value not in allowed:
            raise ValueError(f"{key} has an unsupported value: {value!r}")
        if key == "astrill_wanmtu":
            try:
                mtu = int(value)
            except ValueError as exc:
                raise ValueError("Internet MTU must be a number") from exc
            if not 576 <= mtu <= 1500:
                raise ValueError("Internet MTU must be between 576 and 1500")
        if (
            key in {"astrill_iflist", "astrill_ifexlist", "astrill_vlanlist"}
            and not INTERFACE_LIST_RE.fullmatch(value)
        ):
            raise ValueError(f"{key} contains an invalid interface name")
        if key == "astrill_userdns" and value:
            addresses = value.split()
            if len(addresses) > 2:
                raise ValueError("user DNS accepts at most two addresses")
            for address in addresses:
                ipaddress.IPv4Address(address)
        if key == "astrill_devices" and value:
            for record in value.split(";"):
                NativeDevice.parse(record)
        if key == "astrill_iplistraw":
            value = normalize_site_entries(value)
        normalized[key] = value
    return normalized


def _valid_site_selector(value: str) -> bool:
    if DOMAIN_RE.fullmatch(value.rstrip(".").lower()):
        return True
    if "-" in value:
        try:
            start, end = (
                ipaddress.IPv4Address(item.strip())
                for item in value.split("-", 1)
            )
        except ValueError:
            return False
        return int(start) <= int(end)
    try:
        ipaddress.IPv4Network(value, strict=False)
    except ValueError:
        return False
    return True
