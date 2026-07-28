from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


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

    def clients(self) -> list[dict[str, str]]:
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
