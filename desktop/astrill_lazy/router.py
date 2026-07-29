from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .native_settings import (
    SAFE_NATIVE_ASTRILL_KEYS,
    NativeAstrillSettings,
    normalize_native_changes,
)


class RouterError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


class RouterClient:
    def __init__(self, host: str = "astrill-router", timeout: int = 15) -> None:
        self.host = host
        self.timeout = timeout

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
        result = self._run_alctl(["refresh", "--json"])
        return json.loads(_last_json_line(result.stdout))

    def clients(self) -> list[dict[str, Any]]:
        result = self._run_alctl(["clients", "--json"])
        return list(json.loads(_last_json_line(result.stdout)))

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
        script = ["set -e"]
        script.extend(
            f"nvram set {shlex.quote(f'{key}={value}')}"
            for key, value in normalized.items()
        )
        script.append("nvram commit >/dev/null")
        self.run_script("\n".join(script) + "\n", timeout=30)
        settings = self.native_astrill_settings()
        mismatched = [
            key for key, value in normalized.items() if settings.get(key) != value
        ]
        if mismatched:
            raise RouterError(
                "router did not persist native Astrill settings: "
                + ", ".join(sorted(mismatched))
            )
        return settings

    def fetch_astrill_payload(self) -> bytes:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                self.host,
                "cat /dev/astrill/astrillvpn",
            ],
            check=False,
            capture_output=True,
            timeout=self.timeout,
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
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", self.host, remote_command],
            input=input_bytes,
            check=False,
            capture_output=True,
            timeout=timeout or self.timeout,
        )
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
