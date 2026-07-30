#!/usr/bin/env python3
"""Source-bound Astrill Lazy RAM-overlay restore agent.

This file intentionally remains compatible with the Python 3.9 runtime
included with current Intel macOS installations. It uses only the standard
library and OpenSSH command-line tools.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import random
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1
DEFAULT_VERIFY_INTERVAL = 900
DEFAULT_RETRY_INTERVAL = 30
MIN_VERIFY_INTERVAL = 300
MAX_VERIFY_INTERVAL = 3600
MAX_RETRY_INTERVAL = 300
COMMAND_TIMEOUT = 45
MUTATION_TIMEOUT = 360
MAX_HELPER_BYTES = 128 * 1024
MAX_OVERLAY_BYTES = 32_768
HOST_RE = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")
USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MD5_RE = re.compile(r"^[0-9a-f]{32}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT_RE = re.compile(r"\b(SHA256:[A-Za-z0-9+/=]+)")
MAC_RE = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")
HOST_KEY_TYPES = (
    "ssh-ed25519",
    "ecdsa-sha2-nistp521",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp256",
    "rsa-sha2-512",
    "rsa-sha2-256",
    "ssh-rsa",
)
ALLOWED_MANIFEST_FIELDS = {
    "schema_version",
    "router_host",
    "router_user",
    "router_port",
    "identity_file",
    "known_hosts_file",
    "router_host_key_fingerprint",
    "companion_version",
    "companion_package_md5",
    "helper_md5",
    "page_md5",
    "controller_id",
    "source",
    "resolved_source",
    "source_mac",
    "overlay_md5",
    "overlay_sha256",
    "overlay_rule_ids",
    "policy_bundle",
    "enrolled",
    "overlay_generation",
    "last_runtime_epoch",
    "last_attempt_epoch",
    "last_error",
    "verify_interval_seconds",
    "retry_interval_seconds",
}


class AgentError(RuntimeError):
    pass


class RouterUnavailable(AgentError):
    pass


class Agent:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path.expanduser().resolve()
        self.base = self.manifest_path.parent
        self.overlay_path = self.base / "overlay.tsv"
        self.helper_path = self.base / "alhybrid"
        self.page_path = self.base / "alpage-ui"
        self.log_path = self.base / "agent.log"
        self.lock_path = self.base / "agent.lock"
        self.manifest = self._load_manifest()
        self._validate_assets()

    def inspect(self) -> Dict[str, Any]:
        self._verify_host_key()
        status = self._effective_status()
        layers = self._validate_router_status(status)
        epoch = self._runtime_epoch(layers)
        owner = self._owner_overlay(layers)
        return {
            "ok": True,
            "action": "status",
            "runtime_epoch": epoch,
            "companion_version": status.get("version"),
            "policy_health": status.get("policy_health"),
            "core": layers.get("core"),
            "overlay_present": owner is not None,
            "overlay_matches": self._overlay_matches(owner),
            "enrolled": self.manifest["enrolled"],
            "last_error": self.manifest.get("last_error"),
            "mutated": False,
        }

    def reconcile(
        self,
        *,
        enroll: bool = False,
        force: bool = False,
    ) -> Dict[str, Any]:
        self._verify_host_key()
        status = self._effective_status()
        layers = self._validate_router_status(status)
        epoch = self._runtime_epoch(layers)
        owner = self._owner_overlay(layers)

        if self._overlay_matches(owner):
            self._adopt_owner(owner, epoch, enrolled=True)
            return self._result("current", epoch, owner, mutated=False)
        if enroll and self._overlay_matches(owner, permit_unenrolled=True):
            self._adopt_owner(owner, epoch, enrolled=True)
            return self._result("adopted", epoch, owner, mutated=False)
        if owner is not None:
            message = (
                "this controller owner already exists with a different document "
                "or source/MAC binding; no overlay was replaced"
            )
            self._record_attempt(epoch, message)
            raise AgentError(message)
        if not enroll and not self.manifest["enrolled"]:
            raise AgentError(
                "agent is not enrolled; run the explicit enroll command first"
            )
        if (
            not force
            and self.manifest.get("last_attempt_epoch") == epoch
        ):
            return {
                "ok": self.manifest.get("last_error") in {None, ""},
                "action": "already-attempted",
                "runtime_epoch": epoch,
                "last_error": self.manifest.get("last_error"),
                "mutated": False,
            }

        expected_source = "-"
        expected_mac = "-"
        if not enroll:
            expected_source = self.manifest.get("resolved_source") or ""
            expected_mac = self.manifest.get("source_mac") or ""
            if not expected_source or not expected_mac:
                raise AgentError(
                    "enrolled automatic restore requires a saved source and MAC"
                )

        # Persist the attempt before a long DNS/firewall transaction. A crash
        # cannot create an unbounded retry loop in one router runtime.
        self._record_attempt(epoch, None)
        try:
            self._stage_asset(
                self.helper_path,
                "/tmp/astrill-lazy/alhybrid",
                self.manifest["helper_md5"],
                "hybrid helper",
            )
            if self.page_path.is_file() and self.manifest.get("page_md5"):
                self._stage_asset(
                    self.page_path,
                    "/tmp/astrill-lazy/alpage-ui",
                    self.manifest["page_md5"],
                    "policy page",
                )
            result = self._overlay_put(
                expected_source=expected_source,
                expected_mac=expected_mac,
            )
            result_layers = self._layers(result)
            applied = self._owner_overlay(result_layers)
            if not self._overlay_matches(applied, permit_unenrolled=enroll):
                raise AgentError(
                    "router readback did not match the uploaded overlay and binding"
                )
            self._adopt_owner(applied, self._runtime_epoch(result_layers), enrolled=True)
            return self._result("enrolled" if enroll else "restored", epoch, applied)
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            self._record_attempt(epoch, message)
            raise

    def watch(self) -> int:
        verify_interval = int(self.manifest["verify_interval_seconds"])
        retry_interval = int(self.manifest["retry_interval_seconds"])
        stopping = {"value": False}

        def stop(_signum: int, _frame: Any) -> None:
            stopping["value"] = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        last_state = ""
        while not stopping["value"]:
            try:
                result = self.reconcile()
                state = str(result.get("action", "unknown"))
                if state != last_state or result.get("last_error"):
                    self._log("INFO", result)
                last_state = state
                delay = verify_interval + random.randint(0, 30)
            except RouterUnavailable as exc:
                state = "router-unavailable"
                if state != last_state:
                    self._log("WARN", {"action": state, "error": str(exc)})
                last_state = state
                delay = retry_interval
            except Exception as exc:
                state = "restore-refused"
                message = str(exc).strip() or type(exc).__name__
                if state != last_state or message != self.manifest.get("last_error"):
                    self._log("ERROR", {"action": state, "error": message})
                last_state = state
                delay = verify_interval
            deadline = time.monotonic() + delay
            while not stopping["value"] and time.monotonic() < deadline:
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        return 0

    def _load_manifest(self) -> Dict[str, Any]:
        try:
            document = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentError("agent manifest is not valid UTF-8 JSON") from exc
        if not isinstance(document, dict):
            raise AgentError("agent manifest root must be an object")
        unknown = set(document) - ALLOWED_MANIFEST_FIELDS
        if unknown:
            raise AgentError(
                "agent manifest contains unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        if document.get("schema_version") != SCHEMA_VERSION:
            raise AgentError("unsupported agent manifest schema")
        for field in (
            "router_host",
            "router_user",
            "identity_file",
            "known_hosts_file",
            "router_host_key_fingerprint",
            "companion_version",
            "companion_package_md5",
            "helper_md5",
            "controller_id",
            "source",
            "overlay_md5",
            "overlay_sha256",
        ):
            if not isinstance(document.get(field), str) or not document[field].strip():
                raise AgentError("agent manifest field %s is required" % field)
            document[field] = document[field].strip()
        if not HOST_RE.fullmatch(document["router_host"]):
            raise AgentError("agent router host is invalid")
        if not USER_RE.fullmatch(document["router_user"]):
            raise AgentError("agent router user is invalid")
        port = document.get("router_port")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise AgentError("agent router port is invalid")
        if not OWNER_RE.fullmatch(document["controller_id"]):
            raise AgentError("agent controller ID is invalid")
        for field in ("companion_package_md5", "helper_md5", "overlay_md5"):
            document[field] = document[field].casefold()
            if not MD5_RE.fullmatch(document[field]):
                raise AgentError("agent %s is invalid" % field)
        document["overlay_sha256"] = document["overlay_sha256"].casefold()
        if not SHA256_RE.fullmatch(document["overlay_sha256"]):
            raise AgentError("agent overlay SHA-256 is invalid")
        if document.get("page_md5") is not None:
            if not isinstance(document["page_md5"], str):
                raise AgentError("agent page MD5 must be a string")
            document["page_md5"] = document["page_md5"].strip().casefold()
            if not MD5_RE.fullmatch(document["page_md5"]):
                raise AgentError("agent page MD5 is invalid")
        if not re.fullmatch(
            r"SHA256:[A-Za-z0-9+/=]{4,128}",
            document["router_host_key_fingerprint"],
        ):
            raise AgentError("agent SSH host-key fingerprint is invalid")
        document["source"] = self._normalize_source(document["source"], allow_auto=True)
        resolved = document.get("resolved_source")
        if resolved is not None:
            if not isinstance(resolved, str) or not resolved.strip():
                raise AgentError("agent resolved source must be a string or null")
            document["resolved_source"] = self._normalize_source(
                resolved,
                allow_auto=False,
            )
        source_mac = document.get("source_mac")
        if source_mac is not None:
            if not isinstance(source_mac, str):
                raise AgentError("agent source MAC must be a string or null")
            source_mac = source_mac.strip().casefold().replace("-", ":")
            if not MAC_RE.fullmatch(source_mac):
                raise AgentError("agent source MAC is invalid")
            document["source_mac"] = source_mac
        if not isinstance(document.get("enrolled"), bool):
            raise AgentError("agent enrolled state must be a boolean")
        generation = document.get("overlay_generation", 0)
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 0
        ):
            raise AgentError("agent overlay generation is invalid")
        document["overlay_generation"] = generation
        for field in ("last_runtime_epoch", "last_attempt_epoch", "last_error"):
            value = document.get(field)
            if value is not None and not isinstance(value, str):
                raise AgentError("agent %s must be a string or null" % field)
        for field, default, minimum, maximum in (
            (
                "verify_interval_seconds",
                DEFAULT_VERIFY_INTERVAL,
                MIN_VERIFY_INTERVAL,
                MAX_VERIFY_INTERVAL,
            ),
            (
                "retry_interval_seconds",
                DEFAULT_RETRY_INTERVAL,
                5,
                MAX_RETRY_INTERVAL,
            ),
        ):
            value = document.get(field, default)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not minimum <= value <= maximum
            ):
                raise AgentError("agent %s is outside its safe range" % field)
            document[field] = value
        rule_ids = document.get("overlay_rule_ids", [])
        if not isinstance(rule_ids, list) or any(
            not isinstance(item, str) or not OWNER_RE.fullmatch(item)
            for item in rule_ids
        ):
            raise AgentError("agent overlay rule IDs are invalid")
        if len(rule_ids) != len(set(rule_ids)):
            raise AgentError("agent overlay rule IDs contain duplicates")
        return document

    def _validate_assets(self) -> None:
        overlay = self._read_bounded(self.overlay_path, MAX_OVERLAY_BYTES)
        try:
            overlay.decode("ascii")
        except UnicodeDecodeError as exc:
            raise AgentError("overlay.tsv must contain ASCII only") from exc
        if not overlay.startswith(b"# astrill-lazy-rules-v1\n"):
            raise AgentError("overlay.tsv has an invalid policy header")
        if hashlib.md5(overlay).hexdigest() != self.manifest["overlay_md5"]:
            raise AgentError("overlay.tsv MD5 differs from the manifest")
        if hashlib.sha256(overlay).hexdigest() != self.manifest["overlay_sha256"]:
            raise AgentError("overlay.tsv SHA-256 differs from the manifest")
        helper = self._read_bounded(self.helper_path, MAX_HELPER_BYTES)
        if hashlib.md5(helper).hexdigest() != self.manifest["helper_md5"]:
            raise AgentError("alhybrid MD5 differs from the manifest")
        page_md5 = self.manifest.get("page_md5")
        if page_md5:
            page = self._read_bounded(self.page_path, MAX_HELPER_BYTES)
            if hashlib.md5(page).hexdigest() != page_md5:
                raise AgentError("alpage-ui MD5 differs from the manifest")
        for field in ("identity_file", "known_hosts_file"):
            path = self._configured_path(self.manifest[field])
            if not path.is_file():
                raise AgentError("%s was not found: %s" % (field, path))
        identity = self._configured_path(self.manifest["identity_file"])
        if identity.stat().st_mode & 0o077:
            raise AgentError("router private key must not be group/world accessible")

    @staticmethod
    def _read_bounded(path: Path, maximum: int) -> bytes:
        try:
            size = path.stat().st_size
            if size <= 0 or size > maximum:
                raise AgentError("%s is outside its safe size range" % path.name)
            return path.read_bytes()
        except OSError as exc:
            raise AgentError("could not read %s: %s" % (path.name, exc)) from exc

    def _verify_host_key(self) -> None:
        host = self.manifest["router_host"]
        port = self.manifest["router_port"]
        try:
            scan = subprocess.run(
                [
                    "ssh-keyscan",
                    "-T",
                    "8",
                    "-p",
                    str(port),
                    host,
                ],
                check=False,
                capture_output=True,
                timeout=12,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RouterUnavailable("could not inspect router SSH key: %s" % exc) from exc
        candidates = []
        for line in scan.stdout.decode(errors="replace").splitlines():
            fields = line.strip().split()
            if len(fields) >= 3 and fields[1] in HOST_KEY_TYPES:
                candidates.append((fields[1], fields[2]))
        if not candidates:
            message = scan.stderr.decode(errors="replace").strip()
            raise RouterUnavailable(message or "router did not return an SSH host key")
        key_type, key_base64 = min(
            candidates,
            key=lambda item: (
                HOST_KEY_TYPES.index(item[0])
                if item[0] in HOST_KEY_TYPES
                else len(HOST_KEY_TYPES)
            ),
        )
        lookup = host if port == 22 else "[%s]:%s" % (host, port)
        line = "%s %s %s\n" % (lookup, key_type, key_base64)
        try:
            fingerprint = subprocess.run(
                ["ssh-keygen", "-lf", "-"],
                input=line.encode("ascii"),
                check=False,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AgentError("could not calculate router host-key fingerprint") from exc
        match = FINGERPRINT_RE.search(
            fingerprint.stdout.decode(errors="replace")
        )
        if fingerprint.returncode != 0 or match is None:
            raise AgentError("could not calculate router host-key fingerprint")
        if match.group(1) != self.manifest["router_host_key_fingerprint"]:
            raise AgentError(
                "router SSH host-key fingerprint changed; no policy was written"
            )

    def _ssh_arguments(self) -> List[str]:
        return [
            "ssh",
            "-F",
            "/dev/null",
            "-o",
            "BatchMode=yes",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=2",
            "-o",
            "UserKnownHostsFile=%s"
            % self._configured_path(self.manifest["known_hosts_file"]),
            "-p",
            str(self.manifest["router_port"]),
            "-i",
            str(self._configured_path(self.manifest["identity_file"])),
            "%s@%s"
            % (self.manifest["router_user"], self.manifest["router_host"]),
        ]

    def _remote(
        self,
        arguments: Sequence[str],
        *,
        input_bytes: Optional[bytes] = None,
        timeout: int = COMMAND_TIMEOUT,
    ) -> bytes:
        command = shlex.join(list(arguments))
        try:
            result = subprocess.run(
                self._ssh_arguments() + [command],
                input=input_bytes,
                check=False,
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RouterUnavailable("router command failed: %s" % exc) from exc
        if result.returncode != 0:
            message = result.stderr.decode(errors="replace").strip()
            if result.returncode == 255:
                raise RouterUnavailable(message or "router SSH is unavailable")
            raise AgentError(
                message
                or result.stdout.decode(errors="replace").strip()
                or "router command failed with status %s" % result.returncode
            )
        return result.stdout

    def _effective_status(self) -> Dict[str, Any]:
        output = self._remote(
            ["/tmp/astrill-lazy/alctl", "effective-status", "--json"]
        )
        return self._last_json(output, "router returned invalid layered status")

    def _stage_asset(
        self,
        path: Path,
        target: str,
        expected_md5: str,
        label: str,
    ) -> None:
        if target not in {
            "/tmp/astrill-lazy/alhybrid",
            "/tmp/astrill-lazy/alpage-ui",
        }:
            raise AgentError("runtime asset target is not allowlisted")
        payload = path.read_bytes()
        script = """
set -e
target=%s
expected=%s
expected_version=%s
expected_package=%s
lock=/tmp/astrill-lazy/controller.lock
locked=false
temporary=
cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    [ -z "$temporary" ] || rm -f "$temporary"
    if [ "$locked" = true ]; then
        rm -f "$lock/pid"
        rmdir "$lock" 2>/dev/null || true
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
[ -d /tmp/astrill-lazy ] || exit 1
temporary="$target.$$"
umask 077
cat > "$temporary"
[ "$(md5sum "$temporary" | awk '{print $1}')" = "$expected" ] || exit 1
chmod 700 "$temporary"
attempts=0
while ! mkdir "$lock" 2>/dev/null; do
    pid=$(cat "$lock/pid" 2>/dev/null || printf 0)
    case $pid in ''|*[!0-9]*) pid=0 ;; esac
    if [ "$pid" -le 1 ] || ! kill -0 "$pid" 2>/dev/null; then
        sleep 1
        pid=$(cat "$lock/pid" 2>/dev/null || printf 0)
        case $pid in ''|*[!0-9]*) pid=0 ;; esac
    fi
    if [ "$pid" -le 1 ] || ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$lock/pid"
        rmdir "$lock" 2>/dev/null || true
        continue
    fi
    attempts=$((attempts + 1))
    [ "$attempts" -lt 90 ] || exit 75
    sleep 1
done
locked=true
printf '%%s\n' "$$" > "$lock/pid"
[ "$(cat /tmp/astrill-lazy/VERSION 2>/dev/null)" = "$expected_version" ] &&
[ "$(cat /tmp/astrill-lazy/PACKAGE_MD5 2>/dev/null)" = "$expected_package" ] &&
[ "$(nvram get astrill_lazy_installed)" = 1 ] &&
[ "$(nvram get astrill_lazy_version)" = "$expected_version" ] &&
[ "$(nvram get astrill_lazy_pkg_md5 | tr 'A-F' 'a-f')" = "$expected_package" ] ||
    exit 75
if [ -f "$target" ] && [ -x "$target" ] &&
   [ "$(md5sum "$target" | awk '{print $1}')" = "$expected" ]; then
    exit 0
fi
mv -f "$temporary" "$target"
temporary=
[ -x "$target" ]
""" % (
            shlex.quote(target),
            shlex.quote(expected_md5),
            shlex.quote(self.manifest["companion_version"]),
            shlex.quote(self.manifest["companion_package_md5"]),
        )
        try:
            self._remote(
                ["/bin/sh", "-c", script],
                input_bytes=payload,
                timeout=120,
            )
        except Exception as exc:
            raise AgentError("could not stage %s: %s" % (label, exc)) from exc

    def _overlay_put(
        self,
        *,
        expected_source: str,
        expected_mac: str,
    ) -> Dict[str, Any]:
        output = self._remote(
            [
                "/tmp/astrill-lazy/alctl",
                "overlay-put",
                self.manifest["companion_version"],
                self.manifest["companion_package_md5"],
                self.manifest["helper_md5"],
                self.manifest["controller_id"],
                "0",
                self.manifest["source"],
                expected_source,
                expected_mac,
                "-",
            ],
            input_bytes=self.overlay_path.read_bytes(),
            timeout=MUTATION_TIMEOUT,
        )
        return self._last_json(output, "router returned invalid overlay result")

    @staticmethod
    def _last_json(payload: bytes, message: str) -> Dict[str, Any]:
        for line in reversed(payload.decode(errors="replace").splitlines()):
            try:
                document = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(document, dict):
                return document
        raise AgentError(message)

    def _validate_router_status(self, status: Dict[str, Any]) -> Dict[str, Any]:
        layers = self._layers(status)
        if status.get("version") != self.manifest["companion_version"]:
            raise AgentError(
                "router companion version differs from the enrolled package"
            )
        package = str(
            status.get("package_md5", layers.get("package_md5", ""))
        ).casefold()
        if package != self.manifest["companion_package_md5"]:
            raise AgentError(
                "router companion package MD5 differs from the enrolled package"
            )
        stored = status.get("stored_package_md5", layers.get("stored_package_md5"))
        if stored is not None and str(stored).casefold() != package:
            raise AgentError("router stored/running package identities differ")
        if status.get("policy_health") != "ready":
            raise AgentError(
                "router policy runtime is not ready; overlay restore was skipped"
            )
        if status.get("precedence_ok") is not True:
            raise AgentError(
                "router policy precedence is not verified; overlay restore was skipped"
            )
        return layers

    @staticmethod
    def _layers(status: Dict[str, Any]) -> Dict[str, Any]:
        for key in ("policy_layers", "layered_policy"):
            nested = status.get(key)
            if isinstance(nested, dict) and isinstance(nested.get("core"), dict):
                return nested
        if (
            isinstance(status.get("core"), dict)
            and isinstance(status.get("overlays"), list)
            and isinstance(status.get("effective"), dict)
        ):
            return status
        raise AgentError("router status omitted hybrid policy layers")

    @staticmethod
    def _runtime_epoch(layers: Dict[str, Any]) -> str:
        value = layers.get("runtime_epoch")
        if not isinstance(value, str) or not value.strip():
            raise AgentError("router status omitted its runtime epoch")
        return value.strip()

    def _owner_overlay(
        self,
        layers: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        overlays = layers.get("overlays")
        if not isinstance(overlays, list):
            raise AgentError("router overlay list is invalid")
        for item in overlays:
            if (
                isinstance(item, dict)
                and str(item.get("owner", "")).casefold()
                == self.manifest["controller_id"].casefold()
            ):
                return item
        return None

    def _configured_path(self, value: str) -> Path:
        path = Path(os.path.expanduser(value))
        return path if path.is_absolute() else self.base / path

    def _overlay_matches(
        self,
        owner: Optional[Dict[str, Any]],
        *,
        permit_unenrolled: bool = False,
    ) -> bool:
        if owner is None:
            return False
        layer_hash = str(owner.get("hash", owner.get("md5", ""))).casefold()
        if layer_hash == self.manifest["overlay_md5"]:
            layer_hash = "md5:" + layer_hash
        if layer_hash != "md5:" + self.manifest["overlay_md5"]:
            return False
        source = self._layer_source(owner)
        mac = self._layer_mac(owner)
        if permit_unenrolled and not self.manifest["enrolled"]:
            return source is not None and mac is not None
        expected_source = self.manifest.get("resolved_source")
        expected_mac = self.manifest.get("source_mac")
        return (
            expected_source is not None
            and expected_mac is not None
            and source == expected_source
            and mac == expected_mac
        )

    def _adopt_owner(
        self,
        owner: Optional[Dict[str, Any]],
        epoch: str,
        *,
        enrolled: bool,
    ) -> None:
        if owner is None:
            raise AgentError("router omitted this controller's overlay")
        source = self._layer_source(owner)
        mac = self._layer_mac(owner)
        generation = owner.get("generation", 0)
        if source is None or mac is None:
            raise AgentError("router omitted the source/MAC overlay binding")
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation <= 0
        ):
            raise AgentError("router omitted the overlay generation")
        self.manifest["resolved_source"] = source
        self.manifest["source_mac"] = mac
        self.manifest["overlay_generation"] = generation
        self.manifest["enrolled"] = enrolled
        self.manifest["last_runtime_epoch"] = epoch
        self.manifest["last_attempt_epoch"] = epoch
        self.manifest["last_error"] = None
        self._save_manifest()

    def _record_attempt(self, epoch: str, error: Optional[str]) -> None:
        self.manifest["last_attempt_epoch"] = epoch
        self.manifest["last_error"] = error[:2048] if error else None
        self._save_manifest()

    def _save_manifest(self) -> None:
        payload = (
            json.dumps(
                self.manifest,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("ascii")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".manifest.",
            suffix=".tmp",
            dir=str(self.base),
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(str(temporary), 0o600)
            os.replace(str(temporary), str(self.manifest_path))
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _result(
        self,
        action: str,
        epoch: str,
        owner: Optional[Dict[str, Any]],
        mutated: bool = True,
    ) -> Dict[str, Any]:
        return {
            "ok": True,
            "action": action,
            "runtime_epoch": epoch,
            "controller_id": self.manifest["controller_id"],
            "source": self._layer_source(owner) if owner else None,
            "mac": self._layer_mac(owner) if owner else None,
            "generation": (
                owner.get("generation") if isinstance(owner, dict) else None
            ),
            "overlay_md5": self.manifest["overlay_md5"],
            "mutated": mutated,
        }

    @staticmethod
    def _normalize_source(value: str, *, allow_auto: bool) -> str:
        normalized = str(value).strip().casefold()
        if allow_auto and normalized == "auto":
            return normalized
        try:
            network = ipaddress.ip_network(normalized, strict=False)
        except ValueError as exc:
            raise AgentError("agent source must be auto or an IPv4 host/CIDR") from exc
        if network.version != 4 or network.is_multicast or network.is_unspecified:
            raise AgentError("agent source must be a usable IPv4 host/CIDR")
        return str(network)

    def _layer_source(self, owner: Dict[str, Any]) -> Optional[str]:
        for key in ("source", "source_cidr", "resolved_source"):
            value = owner.get(key)
            if isinstance(value, str) and value.strip():
                try:
                    return self._normalize_source(value, allow_auto=False)
                except AgentError:
                    continue
        return None

    @staticmethod
    def _layer_mac(owner: Dict[str, Any]) -> Optional[str]:
        for key in ("mac", "source_mac"):
            value = owner.get(key)
            if isinstance(value, str):
                normalized = value.strip().casefold().replace("-", ":")
                if MAC_RE.fullmatch(normalized):
                    return normalized
        return None

    def _log(self, level: str, document: Dict[str, Any]) -> None:
        record = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "level": level,
            **document,
        }
        line = json.dumps(record, ensure_ascii=True, sort_keys=True)
        try:
            if self.log_path.exists() and self.log_path.stat().st_size > 256 * 1024:
                previous = self.log_path.with_suffix(".log.previous")
                try:
                    previous.unlink()
                except FileNotFoundError:
                    pass
                self.log_path.replace(previous)
            with self.log_path.open("a", encoding="ascii") as handle:
                handle.write(line + "\n")
            os.chmod(str(self.log_path), 0o600)
        except OSError:
            pass


class AgentLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> "AgentLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            raise AgentError("another restore agent already owns this manifest") from exc
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write("%s\n" % os.getpid())
        self.handle.flush()
        os.chmod(str(self.path), 0o600)
        return self

    def __exit__(self, _kind: Any, _value: Any, _traceback: Any) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astrill-lazy-agent")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().with_name("manifest.json"),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="verify local assets and read router status")
    enroll = commands.add_parser(
        "enroll",
        help="explicitly approve the first source/MAC-bound overlay load",
    )
    enroll.add_argument("--force", action="store_true")
    restore = commands.add_parser(
        "restore",
        help="restore the enrolled overlay once in a new router runtime",
    )
    restore.add_argument("--force", action="store_true")
    commands.add_parser(
        "watch",
        help="run a low-frequency verified restore loop",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        agent = Agent(arguments.manifest)
        with AgentLock(agent.lock_path):
            if arguments.command == "status":
                result = agent.inspect()
            elif arguments.command == "enroll":
                result = agent.reconcile(
                    enroll=True,
                    force=arguments.force,
                )
            elif arguments.command == "restore":
                result = agent.reconcile(force=arguments.force)
            else:
                return agent.watch()
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        return 0
    except (AgentError, OSError, ValueError) as exc:
        print("astrill-lazy-agent: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
