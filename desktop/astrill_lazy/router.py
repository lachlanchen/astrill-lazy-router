from __future__ import annotations

import ipaddress
import json
import re
import shlex
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .astrill import AstrillConnectionSelection
from .native_settings import (
    SAFE_NATIVE_ASTRILL_KEYS,
    NativeAstrillSettings,
    normalize_native_changes,
)
from .subprocess_support import background_process_options

DOMAIN_REFRESH_TIMEOUT = 180


class RouterError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


@dataclass(frozen=True)
class AstrillConnectionResult:
    status: dict[str, Any]
    settings: NativeAstrillSettings


@dataclass(frozen=True)
class RouterMonitorSnapshot:
    native_status: dict[str, Any]
    settings: NativeAstrillSettings
    companion_presence: dict[str, Any]
    companion_status: dict[str, Any] | None

    def selected_status(self, companion_enabled: bool) -> dict[str, Any]:
        if companion_enabled and self.companion_status is not None:
            return self.companion_status
        return self.native_status


class RouterClient:
    def __init__(
        self,
        host: str = "astrill-router",
        timeout: int = 15,
        *,
        user: str | None = None,
        port: int | None = None,
        identity_file: str | Path | None = None,
        host_key_policy: str = "accept-new",
        known_hosts_file: str | Path | None = None,
    ) -> None:
        if host_key_policy not in {"accept-new", "yes"}:
            raise ValueError("SSH host-key policy must be 'accept-new' or 'yes'")
        self.host = host
        self.timeout = timeout
        self.user = user
        self.port = port
        self.host_key_policy = host_key_policy
        self.identity_file = (
            str(Path(identity_file).expanduser()) if identity_file is not None else None
        )
        self.known_hosts_file = (
            str(Path(known_hosts_file).expanduser())
            if known_hosts_file is not None
            else None
        )

    def ping(self) -> bool:
        result = self._run_remote(["printf", "ready"])
        return result.stdout.strip().endswith("ready")

    def status(self) -> dict[str, Any]:
        result = self._run_alctl(["status", "--json"])
        try:
            return json.loads(_last_json_line(result.stdout))
        except json.JSONDecodeError as exc:
            raise RouterError(f"router returned invalid status JSON: {exc}") from exc

    def rules(self) -> str:
        return self._run_alctl(["rules"]).stdout

    def apply_rules(self, rules_tsv: str) -> dict[str, Any]:
        result = self._run_alctl(
            ["apply", "-"], input_bytes=rules_tsv.encode(), timeout=120
        )
        try:
            return json.loads(_last_json_line(result.stdout))
        except json.JSONDecodeError as exc:
            raise RouterError(f"router returned invalid apply result: {exc}") from exc

    def rollback(self) -> dict[str, Any]:
        result = self._run_alctl(["rollback", "--json"])
        return json.loads(_last_json_line(result.stdout))

    def refresh(self) -> dict[str, Any]:
        result = self._run_alctl(["refresh", "--json"], timeout=DOMAIN_REFRESH_TIMEOUT)
        return json.loads(_last_json_line(result.stdout))

    def clients(self) -> list[dict[str, Any]]:
        result = self._run_alctl(["clients", "--json"])
        return list(json.loads(_last_json_line(result.stdout)))

    def native_clients(self) -> list[dict[str, Any]]:
        """Read LAN clients without requiring or writing companion runtime files."""
        script = """
printf 'leases\\t'
[ ! -r /tmp/dnsmasq.leases ] ||
    hexdump -v -e '1/1 "%02x"' /tmp/dnsmasq.leases
printf '\\narp\\t'
[ ! -r /proc/net/arp ] || hexdump -v -e '1/1 "%02x"' /proc/net/arp
printf '\\n'
for key in static_leases dhcp_staticlist dhcpd_static lan_ifname; do
    printf 'nvram:%s\\t' "$key"
    nvram get "$key" | hexdump -v -e '1/1 "%02x"'
    printf '\\n'
done
"""
        return _parse_native_clients(self.run_script(script))

    def companion_presence(self) -> dict[str, Any]:
        """Inspect companion markers without starting, repairing, or installing it."""
        script = """
for key in astrill_lazy_installed astrill_lazy_version; do
    printf '%s\\t' "$key"
    nvram get "$key" | hexdump -v -e '1/1 "%02x"'
    printf '\\n'
done
[ -x /tmp/astrill-lazy/alctl ] && runtime=true || runtime=false
printf 'runtime\\t%s\\n' "$runtime"
"""
        values = _decode_tagged_hex(self.run_script(script), plain_tags={"runtime"})
        return {
            "installed": values.get("astrill_lazy_installed") == "1",
            "version": values.get("astrill_lazy_version") or None,
            "runtime": values.get("runtime") == "true",
        }

    def monitor_snapshot(self, *, include_companion: bool) -> RouterMonitorSnapshot:
        """Read status, settings, and companion markers in one SSH session."""
        keys = " ".join(shlex.quote(key) for key in SAFE_NATIVE_ASTRILL_KEYS)
        companion_status = (
            """
if [ -x /tmp/astrill-lazy/alctl ]; then
    printf 'companion_status\\t'
    /tmp/astrill-lazy/alctl status --json 2>/dev/null |
        tail -n 1 |
        hexdump -v -e '1/1 "%02x"'
    printf '\\n'
fi
"""
            if include_companion
            else ""
        )
        script = f"""
vpn_state=down
ip route show table main | grep -q ' dev tun0' && vpn_state=up
[ -x /dev/astrill/astrillvpn ] && applet=true || applet=false
[ -x /tmp/astrill-lazy/alctl ] && companion_runtime=true || companion_runtime=false
printf 'meta:vpn_state\\t%s\\n' "$vpn_state"
printf 'meta:applet\\t%s\\n' "$applet"
printf 'presence:runtime\\t%s\\n' "$companion_runtime"
printf 'meta:wan_iface\\t'
nvram get wan_iface | hexdump -v -e '1/1 "%02x"'
printf '\\n'
for key in astrill_lazy_installed astrill_lazy_version astrill_lazy_pkg_md5; do
    printf 'presence:%s\\t' "$key"
    nvram get "$key" | hexdump -v -e '1/1 "%02x"'
    printf '\\n'
done
for key in {keys}; do
    printf 'setting:%s\\t' "$key"
    nvram get "$key" | hexdump -v -e '1/1 "%02x"'
    printf '\\n'
done
{companion_status}
"""
        values = _decode_tagged_hex(
            self.run_script(script, timeout=30),
            plain_tags={
                "meta:vpn_state",
                "meta:applet",
                "presence:runtime",
            },
        )
        required_meta = {
            "meta:vpn_state",
            "meta:applet",
            "meta:wan_iface",
            "presence:runtime",
            "presence:astrill_lazy_installed",
            "presence:astrill_lazy_version",
            "presence:astrill_lazy_pkg_md5",
        }
        missing_meta = required_meta - values.keys()
        if missing_meta:
            raise RouterError(
                "router omitted monitor fields: " + ", ".join(sorted(missing_meta))
            )
        missing_settings = {
            key for key in SAFE_NATIVE_ASTRILL_KEYS if f"setting:{key}" not in values
        }
        if missing_settings:
            raise RouterError(
                "router omitted native Astrill settings: "
                + ", ".join(sorted(missing_settings))
            )

        settings = NativeAstrillSettings.from_dict(
            {key: values[f"setting:{key}"] for key in SAFE_NATIVE_ASTRILL_KEYS}
        )
        parsed_companion: dict[str, Any] | None = None
        raw_companion = values.get("companion_status", "").strip()
        if raw_companion:
            try:
                candidate = json.loads(_last_json_line(raw_companion))
            except (json.JSONDecodeError, RouterError):
                candidate = None
            if isinstance(candidate, dict):
                parsed_companion = candidate

        return RouterMonitorSnapshot(
            native_status=_native_status_from_monitor(settings, values),
            settings=settings,
            companion_presence={
                "installed": (values["presence:astrill_lazy_installed"] == "1"),
                "version": values["presence:astrill_lazy_version"] or None,
                "runtime": values["presence:runtime"] == "true",
                "package_md5": values["presence:astrill_lazy_pkg_md5"] or None,
            },
            companion_status=parsed_companion,
        )

    def switch_astrill(
        self,
        *,
        server_id: int,
        sid: int,
        encoded_ip: int,
        port: str,
        port_index: int,
        protocol: int,
        vpn_mode: int,
    ) -> dict[str, Any]:
        arguments = [
            "astrill-switch",
            str(server_id),
            str(sid),
            str(encoded_ip),
            port,
            str(port_index),
            str(protocol),
            str(vpn_mode),
            "--json",
        ]
        result = self._run_alctl(arguments, timeout=90)
        return json.loads(_last_json_line(result.stdout))

    def set_astrill_connection(
        self, connected: bool, *, companion_enabled: bool
    ) -> dict[str, Any]:
        if companion_enabled:
            command = "astrill-connect" if connected else "astrill-disconnect"
            result = self._run_alctl([command], timeout=80)
            return json.loads(_last_json_line(result.stdout))

        action = "start" if connected else "stop"
        expected = "up" if connected else "down"
        limit = 60 if connected else 65
        script = f"""
set -e
[ -x /dev/astrill/astrillvpn ]
/dev/astrill/astrillvpn {action} >/dev/null 2>&1 || true
attempts=0
while [ "$attempts" -lt {limit} ]; do
    vpn_state=down
    ip route show table main | grep -q ' dev tun0' && vpn_state=up
    [ "$vpn_state" != "{expected}" ] || exit 0
    attempts=$((attempts + 1))
    sleep 1
done
exit 1
"""
        try:
            self.run_script(script, timeout=limit + 10)
        except RouterError as exc:
            verb = "connect" if connected else "disconnect"
            raise RouterError(f"Astrill did not {verb} within {limit} seconds") from exc
        return self.native_astrill_status()

    def native_astrill_status(self) -> dict[str, Any]:
        script = """
astrill_status=$(nvram get astrill_status)
astrill_server=$(nvram get astrill_serverid)
astrill_protocol=$(nvram get astrill_protocol)
wan_iface=$(nvram get wan_iface)
case $astrill_status in ''|*[!0-9]*) astrill_status=0 ;; esac
case $astrill_server in ''|*[!0-9]*) astrill_server=0 ;; esac
case $astrill_protocol in ''|*[!0-9]*) astrill_protocol=0 ;; esac
vpn_state=down
ip route show table main | grep -q ' dev tun0' && vpn_state=up
health=degraded
[ -x /dev/astrill/astrillvpn ] && health=healthy
printf '{"schema_version":1,"ok":true'
printf ',"version":null,"native_mode":true,"health":"%s"' "$health"
printf ',"vpn_state":"%s","astrill_status":%s' "$vpn_state" "$astrill_status"
printf ',"astrill_server_id":%s,"astrill_protocol":%s' "$astrill_server" "$astrill_protocol"
printf ',"wan_interface":"%s","active_chain":null' "$wan_iface"
printf ',"watchdog":false,"jump_installed":false'
printf ',"rules_count":0,"origin_count":0,"direct_rules":0,"vpn_rules":0'
printf ',"resolved_addresses":0,"unresolved_domains":0,"last_apply":0,"rules":[]}\n'
"""
        result = self.run_script(script)
        try:
            return json.loads(_last_json_line(result))
        except json.JSONDecodeError as exc:
            raise RouterError(
                f"router returned invalid native Astrill status JSON: {exc}"
            ) from exc

    def native_astrill_settings(self) -> NativeAstrillSettings:
        keys = " ".join(shlex.quote(key) for key in SAFE_NATIVE_ASTRILL_KEYS)
        script = f"""
for key in {keys}; do
    printf '%s\\t' "$key"
    nvram get "$key" | hexdump -v -e '1/1 "%02x"'
    printf '\\n'
done
"""
        output = self.run_script(script)
        values: dict[str, str] = {}
        for line in output.splitlines():
            if "\t" not in line:
                continue
            key, encoded = line.split("\t", 1)
            if key not in SAFE_NATIVE_ASTRILL_KEYS:
                continue
            try:
                raw = bytes.fromhex(encoded)
                value = raw.decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise RouterError(
                    f"router returned an invalid encoded value for {key}"
                ) from exc
            values[key] = value.removesuffix("\n")
        missing = set(SAFE_NATIVE_ASTRILL_KEYS) - values.keys()
        if missing:
            raise RouterError(
                "router omitted native Astrill settings: " + ", ".join(sorted(missing))
            )
        return NativeAstrillSettings.from_dict(values)

    def update_native_astrill_settings(
        self, changes: dict[str, Any]
    ) -> NativeAstrillSettings:
        normalized = normalize_native_changes(changes)
        if not normalized:
            return self.native_astrill_settings()
        return self._write_native_astrill_values(normalized)

    def replace_astrill_favorites(
        self,
        expected_current: str,
        replacement: str,
    ) -> NativeAstrillSettings:
        """Replace the native favorite list only if it is still current."""

        expected = normalize_native_changes({"astrill_favlist": expected_current})[
            "astrill_favlist"
        ]
        normalized = normalize_native_changes({"astrill_favlist": replacement})[
            "astrill_favlist"
        ]
        script = [
            "set -e",
            f"expected={shlex.quote(expected)}",
            'current="$(nvram get astrill_favlist)"',
            'if [ "$current" != "$expected" ]; then',
            (
                "    printf '%s\\n' "
                "'router favorite endpoints changed before this save; "
                "reload and try again' >&2"
            ),
            "    exit 75",
            "fi",
            f"nvram set {shlex.quote(f'astrill_favlist={normalized}')}",
            "nvram commit >/dev/null",
        ]
        self.run_script("\n".join(script) + "\n", timeout=30)
        settings = self.native_astrill_settings()
        self._verify_native_astrill_values(
            settings,
            {"astrill_favlist": normalized},
        )
        return settings

    def save_astrill_connection(
        self,
        selection: AstrillConnectionSelection,
        changes: dict[str, Any],
    ) -> NativeAstrillSettings:
        normalized = normalize_native_changes(changes)
        values = {**selection.native_values(), **normalized}
        return self._write_native_astrill_values(values)

    def apply_astrill_connection(
        self,
        selection: AstrillConnectionSelection,
        changes: dict[str, Any],
        *,
        companion_enabled: bool = True,
    ) -> AstrillConnectionResult:
        normalized = normalize_native_changes(changes)
        before = self.native_astrill_settings()
        if not companion_enabled:
            values = {**selection.native_values(), **normalized}
            previous = {key: before.get(key) for key in values}
            was_connected = self.native_astrill_status().get("vpn_state") == "up"
            settings_attempted = False
            try:
                if was_connected:
                    self.set_astrill_connection(
                        False,
                        companion_enabled=False,
                    )
                settings_attempted = True
                self._write_native_astrill_values(values)
                status = self.set_astrill_connection(True, companion_enabled=False)
                settings = self.native_astrill_settings()
                self._verify_native_astrill_values(settings, values)
            except RouterError as exc:
                recovery_errors: list[str] = []
                if settings_attempted:
                    try:
                        self.set_astrill_connection(
                            False,
                            companion_enabled=False,
                        )
                    except RouterError as recovery_error:
                        recovery_errors.append(f"disconnect: {recovery_error}")
                    try:
                        self._write_native_astrill_values(previous)
                    except RouterError as recovery_error:
                        recovery_errors.append(f"settings: {recovery_error}")
                if was_connected:
                    try:
                        self.set_astrill_connection(
                            True,
                            companion_enabled=False,
                        )
                    except RouterError as recovery_error:
                        recovery_errors.append(f"reconnect: {recovery_error}")
                if recovery_errors:
                    raise RouterError(
                        f"{exc}; native connection recovery also failed: "
                        + "; ".join(recovery_errors)
                    ) from exc
                raise
            return AstrillConnectionResult(status=status, settings=settings)

        if normalized:
            self._write_native_astrill_values(normalized)
        try:
            status = self.switch_astrill(
                server_id=selection.server_id,
                sid=selection.sid,
                encoded_ip=selection.encoded_ip,
                port=selection.port,
                port_index=selection.port_index,
                protocol=selection.protocol,
                vpn_mode=selection.vpn_mode,
            )
        except RouterError as exc:
            if normalized:
                previous = {key: before.get(key) for key in normalized}
                try:
                    self._write_native_astrill_values(previous)
                except RouterError as rollback_error:
                    raise RouterError(
                        f"{exc}; connection settings rollback also failed: "
                        f"{rollback_error}"
                    ) from exc
            raise

        settings = self.native_astrill_settings()
        expected = {**selection.native_values(), **normalized}
        self._verify_native_astrill_values(settings, expected)
        return AstrillConnectionResult(status=status, settings=settings)

    def _write_native_astrill_values(
        self, values: dict[str, str]
    ) -> NativeAstrillSettings:
        script = ["set -e"]
        script.extend(
            f"nvram set {shlex.quote(f'{key}={value}')}"
            for key, value in values.items()
        )
        script.append("nvram commit >/dev/null")
        self.run_script("\n".join(script) + "\n", timeout=30)
        settings = self.native_astrill_settings()
        self._verify_native_astrill_values(settings, values)
        return settings

    @staticmethod
    def _verify_native_astrill_values(
        settings: NativeAstrillSettings, expected: dict[str, str]
    ) -> None:
        mismatched = [
            key for key, value in expected.items() if settings.get(key) != value
        ]
        if mismatched:
            raise RouterError(
                "router did not persist native Astrill settings: "
                + ", ".join(sorted(mismatched))
            )

    def fetch_astrill_payload(self) -> bytes:
        result = subprocess.run(
            [*self._ssh_arguments(), self._target(), "cat /dev/astrill/astrillvpn"],
            check=False,
            capture_output=True,
            timeout=self.timeout,
            **background_process_options(),
        )
        if result.returncode != 0:
            raise RouterError(
                result.stderr.decode(errors="replace").strip()
                or "could not read the Astrill applet"
            )
        return result.stdout

    def raw(self, arguments: Iterable[str], *, timeout: int | None = None) -> str:
        return self._run_remote(list(arguments), timeout=timeout).stdout

    def run_script(self, script: str, *, timeout: int = 60) -> str:
        return self._run_remote(
            ["/bin/sh", "-s"],
            input_bytes=script.encode("utf-8"),
            timeout=timeout,
        ).stdout

    def _run_alctl(
        self,
        arguments: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        return self._run_remote(
            ["/tmp/astrill-lazy/alctl", *arguments],
            input_bytes=input_bytes,
            timeout=timeout,
        )

    def _run_remote(
        self,
        arguments: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        remote_command = shlex.join(arguments)
        effective_timeout = timeout or self.timeout
        try:
            result = subprocess.run(
                [*self._ssh_arguments(), self._target(), remote_command],
                input=input_bytes,
                check=False,
                capture_output=True,
                timeout=effective_timeout,
                **background_process_options(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RouterError(
                f"router command timed out after {effective_timeout} seconds"
            ) from exc
        decoded = CommandResult(
            stdout=result.stdout.decode(errors="replace"),
            stderr=result.stderr.decode(errors="replace"),
            returncode=result.returncode,
        )
        if result.returncode != 0:
            message = _clean_ssh_stderr(decoded.stderr) or decoded.stdout.strip()
            raise RouterError(
                message or f"router command failed with {result.returncode}"
            )
        return decoded

    def _target(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    def _ssh_arguments(self) -> list[str]:
        arguments = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            f"StrictHostKeyChecking={self.host_key_policy}",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "ConnectionAttempts=2",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "TCPKeepAlive=yes",
        ]
        if self.port is not None:
            arguments.extend(("-p", str(self.port)))
        if self.identity_file is not None:
            arguments.extend(("-o", "IdentitiesOnly=yes", "-i", self.identity_file))
        if self.known_hosts_file is not None:
            arguments.extend(
                (
                    "-o",
                    f"UserKnownHostsFile={_openssh_config_path(self.known_hosts_file)}",
                )
            )
        return arguments


def _openssh_config_path(value: str | Path) -> str:
    """Encode a path embedded in an OpenSSH ``-o`` configuration value."""
    normalized = Path(value).expanduser().as_posix()
    return "".join(f"\\{char}" if char in {" ", "\t"} else char for char in normalized)


def _last_json_line(output: str) -> str:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped.startswith(("{", "[")):
            return stripped
    raise RouterError("router response did not contain JSON")


def _clean_ssh_stderr(output: str) -> str:
    ignored_prefixes = ("DD-WRT ", "Release: ", "Board: ")
    return "\n".join(
        line
        for line in output.splitlines()
        if line.strip() and not line.startswith(ignored_prefixes)
    ).strip()


def _decode_tagged_hex(
    output: str, *, plain_tags: set[str] | None = None
) -> dict[str, str]:
    plain = plain_tags or set()
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "\t" not in line:
            continue
        tag, encoded = line.split("\t", 1)
        if tag in plain:
            values[tag] = encoded
            continue
        try:
            values[tag] = bytes.fromhex(encoded).decode("utf-8").removesuffix("\n")
        except (ValueError, UnicodeDecodeError) as exc:
            raise RouterError(
                f"router returned invalid encoded data for {tag}"
            ) from exc
    return values


def _native_status_from_monitor(
    settings: NativeAstrillSettings,
    values: dict[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "ok": True,
        "version": None,
        "native_mode": True,
        "health": ("healthy" if values.get("meta:applet") == "true" else "degraded"),
        "vpn_state": values.get("meta:vpn_state", "down"),
        "astrill_status": settings.integer("astrill_status"),
        "astrill_server_id": settings.integer("astrill_serverid"),
        "astrill_protocol": settings.integer("astrill_protocol"),
        "wan_interface": values.get("meta:wan_iface", ""),
        "active_chain": None,
        "watchdog": False,
        "jump_installed": False,
        "rules_count": 0,
        "origin_count": 0,
        "direct_rules": 0,
        "vpn_rules": 0,
        "resolved_addresses": 0,
        "unresolved_domains": 0,
        "last_apply": 0,
        "rules": [],
    }


def _parse_native_clients(output: str) -> list[dict[str, Any]]:
    values = _decode_tagged_hex(output)
    records: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def add(
        address: str,
        mac: str,
        hostname: str,
        expires: int,
        source: str,
        active: bool,
    ) -> None:
        normalized_mac = mac.casefold()
        if not _valid_ipv4_network(address) or not _valid_mac(normalized_mac):
            return
        key = f"mac:{normalized_mac}"
        known_name = hostname not in {"", "*", "unknown"}
        if key not in records:
            order.append(key)
            records[key] = {
                "address": address,
                "mac": normalized_mac,
                "hostname": hostname or "unknown",
                "expires": max(0, expires),
                "sources": [source],
                "active": active,
            }
            return
        record = records[key]
        if active:
            record["address"] = address
            record["active"] = True
        existing_name = str(record["hostname"])
        if (source == "static" or existing_name in {"", "*", "unknown"}) and known_name:
            record["hostname"] = hostname
        record["expires"] = max(int(record["expires"]), max(0, expires))
        if source not in record["sources"]:
            record["sources"].append(source)

    for line in values.get("leases", "").splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        expires = int(fields[0]) if fields[0].isdigit() else 0
        add(fields[2], fields[1], fields[3], expires, "dhcp", False)

    for nvram_key in ("static_leases", "dhcp_staticlist", "dhcpd_static"):
        for token in re.split(r"[<>\s]+", values.get(f"nvram:{nvram_key}", "")):
            if not token:
                continue
            fields = token.split("=")
            if len(fields) < 3:
                continue
            add(fields[2], fields[0], fields[1], 0, "static", False)

    lan_interface = values.get("nvram:lan_ifname") or "br0"
    for position, line in enumerate(values.get("arp", "").splitlines()):
        if position == 0:
            continue
        fields = line.split()
        if len(fields) < 6 or fields[2].casefold() != "0x2":
            continue
        if fields[5] != lan_interface:
            continue
        add(fields[0], fields[3], "unknown", 0, "arp", True)

    clients: list[dict[str, Any]] = []
    for key in order:
        record = records[key]
        clients.append(
            {
                "address": record["address"],
                "mac": record["mac"],
                "hostname": record["hostname"],
                "expires": record["expires"],
                "source": ",".join(record["sources"]),
                "active": record["active"],
            }
        )
    return clients


def _valid_ipv4_network(value: str) -> bool:
    try:
        address, separator, prefix = value.partition("/")
        ipaddress.IPv4Address(address)
        return not separator or (prefix.isdigit() and 0 <= int(prefix) <= 32)
    except ipaddress.AddressValueError:
        return False


def _valid_mac(value: str) -> bool:
    return re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", value) is not None
