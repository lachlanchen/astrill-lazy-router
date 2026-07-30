from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import MatchKind, RouteTarget, Rule
from .ssh_setup import (
    DEFAULT_IDENTITY_FILE,
    DEFAULT_ROUTER_HOST,
    DEFAULT_ROUTER_PORT,
    DEFAULT_ROUTER_USER,
)

SCHEMA_VERSION = 1
DEPLOYMENT_SCHEMA_VERSION = 1
CONTROLLER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
HOST_KEY_FINGERPRINT_RE = re.compile(r"^SHA256:[A-Za-z0-9+/=]{4,128}$")
MAC_ADDRESS_RE = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")
POLICY_HASH_RE = re.compile(r"^md5:[0-9a-f]{32}$")


def new_controller_id() -> str:
    """Return an opaque ID suitable for one owner slot on the companion."""

    return f"controller-{uuid.uuid4().hex}"


def normalize_overlay_source(value: str) -> str:
    """Normalize an owner source without turning ``auto`` into a global rule."""

    normalized = str(value).strip().casefold()
    if normalized == "auto":
        return normalized
    try:
        network = ipaddress.ip_network(normalized, strict=False)
    except ValueError as exc:
        raise ValueError("overlay source must be 'auto' or an IPv4 host/CIDR") from exc
    if network.version != 4:
        raise ValueError("overlay source must be an IPv4 host/CIDR")
    if network.is_multicast or network.is_unspecified:
        raise ValueError("overlay source cannot be multicast or unspecified")
    if "/" not in normalized and network.prefixlen == 32:
        return str(network.network_address)
    return str(network)


@dataclass
class PolicyDeploymentManifest:
    """Local expectations for one companion version on one trusted router."""

    router_host: str
    router_port: int
    router_host_key_fingerprint: str
    companion_version: str
    controller_id: str
    source: str = "auto"
    resolved_source: str | None = None
    source_mac: str | None = None
    core_rule_ids: tuple[str, ...] = ()
    overlay_rule_ids: tuple[str, ...] = ()
    core_hash: str | None = None
    overlay_hash: str | None = None
    core_generation: int = 0
    overlay_generation: int = 0
    restore_overlay_after_reboot: bool = False
    last_runtime_epoch: str | None = None

    def validate(self) -> None:
        if not self.router_host.strip():
            raise ValueError("deployment router host cannot be empty")
        if not 1 <= self.router_port <= 65535:
            raise ValueError("deployment router port must be between 1 and 65535")
        if not HOST_KEY_FINGERPRINT_RE.fullmatch(self.router_host_key_fingerprint):
            raise ValueError("deployment requires an SSH SHA256 host-key fingerprint")
        if not self.companion_version.strip():
            raise ValueError("deployment companion version cannot be empty")
        if not CONTROLLER_ID_RE.fullmatch(self.controller_id):
            raise ValueError("deployment controller ID is invalid")
        self.source = normalize_overlay_source(self.source)
        if self.resolved_source is not None:
            resolved = normalize_overlay_source(self.resolved_source)
            if resolved == "auto":
                raise ValueError("resolved overlay source cannot be 'auto'")
            self.resolved_source = resolved
        if self.source_mac is not None:
            normalized_mac = self.source_mac.strip().casefold().replace("-", ":")
            if not MAC_ADDRESS_RE.fullmatch(normalized_mac):
                raise ValueError("deployment source MAC address is invalid")
            self.source_mac = normalized_mac
        for name in ("core_hash", "overlay_hash"):
            value = getattr(self, name)
            if value is None:
                continue
            normalized_hash = value.strip().casefold()
            if not POLICY_HASH_RE.fullmatch(normalized_hash):
                raise ValueError(f"deployment {name} must be an md5 document hash")
            setattr(self, name, normalized_hash)
        for name, values in (
            ("core_rule_ids", self.core_rule_ids),
            ("overlay_rule_ids", self.overlay_rule_ids),
        ):
            if len(values) != len(set(values)) or any(not item for item in values):
                raise ValueError(f"deployment {name} must contain unique non-empty IDs")
        for name, value in (
            ("core_generation", self.core_generation),
            ("overlay_generation", self.overlay_generation),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"deployment {name} must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": DEPLOYMENT_SCHEMA_VERSION,
            "router_host": self.router_host,
            "router_port": self.router_port,
            "router_host_key_fingerprint": self.router_host_key_fingerprint,
            "companion_version": self.companion_version,
            "controller_id": self.controller_id,
            "source": self.source,
            "resolved_source": self.resolved_source,
            "source_mac": self.source_mac,
            "core_rule_ids": list(self.core_rule_ids),
            "overlay_rule_ids": list(self.overlay_rule_ids),
            "core_hash": self.core_hash,
            "overlay_hash": self.overlay_hash,
            "core_generation": self.core_generation,
            "overlay_generation": self.overlay_generation,
            "restore_overlay_after_reboot": self.restore_overlay_after_reboot,
            "last_runtime_epoch": self.last_runtime_epoch,
        }

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> PolicyDeploymentManifest:
        if document.get("schema_version") != DEPLOYMENT_SCHEMA_VERSION:
            raise ValueError("unsupported policy deployment manifest schema")
        restore = document.get("restore_overlay_after_reboot", False)
        if not isinstance(restore, bool):
            raise TypeError("restore_overlay_after_reboot must be a boolean")
        manifest = cls(
            router_host=str(document.get("router_host", "")),
            router_port=document.get("router_port", DEFAULT_ROUTER_PORT),
            router_host_key_fingerprint=str(
                document.get("router_host_key_fingerprint", "")
            ),
            companion_version=str(document.get("companion_version", "")),
            controller_id=str(document.get("controller_id", "")),
            source=str(document.get("source", "auto")),
            resolved_source=_optional_string(document.get("resolved_source")),
            source_mac=_optional_string(document.get("source_mac")),
            core_rule_ids=tuple(
                str(item) for item in document.get("core_rule_ids", [])
            ),
            overlay_rule_ids=tuple(
                str(item) for item in document.get("overlay_rule_ids", [])
            ),
            core_hash=_optional_string(document.get("core_hash")),
            overlay_hash=_optional_string(document.get("overlay_hash")),
            core_generation=document.get("core_generation", 0),
            overlay_generation=document.get("overlay_generation", 0),
            restore_overlay_after_reboot=restore,
            last_runtime_epoch=_optional_string(document.get("last_runtime_epoch")),
        )
        manifest.validate()
        return manifest


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("optional deployment fields must be strings or null")
    normalized = value.strip()
    return normalized or None


def default_config_path() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    if configured:
        return Path(configured).expanduser() / "astrill-lazy" / "config.json"
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "Astrill Lazy Router" / "config.json"
    return Path.home() / ".config" / "astrill-lazy" / "config.json"


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_config_path()
        self.router_host = DEFAULT_ROUTER_HOST
        self.router_user = DEFAULT_ROUTER_USER
        self.router_port = DEFAULT_ROUTER_PORT
        self.router_identity = DEFAULT_IDENTITY_FILE
        self.router_use_ssh_config = False
        self.controller_id = new_controller_id()
        self.policy_deployments: list[PolicyDeploymentManifest] = []
        self.rules: list[Rule] = []
        self.active_region = "active-astrill"
        self.enabled_extensions = ["core-catalog"]
        # A new workstation may point at an already-working Astrill router.
        # Start in inspection-only native mode until the operator explicitly
        # enables writes and installs the optional companion.
        self.companion_enabled = False
        self.read_only = True
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
        if document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported desktop configuration schema")
        self.router_host = str(document.get("router_host", DEFAULT_ROUTER_HOST))
        self.router_user = str(document.get("router_user", DEFAULT_ROUTER_USER))
        router_port = document.get("router_port", DEFAULT_ROUTER_PORT)
        if not isinstance(router_port, int) or isinstance(router_port, bool):
            raise TypeError("router_port must be an integer")
        if not 1 <= router_port <= 65535:
            raise ValueError("router_port must be between 1 and 65535")
        self.router_port = router_port
        self.router_identity = str(
            document.get("router_identity", DEFAULT_IDENTITY_FILE)
        )
        legacy_ssh_config = not any(
            key in document for key in ("router_user", "router_port", "router_identity")
        )
        router_use_ssh_config = document.get("router_use_ssh_config", legacy_ssh_config)
        if not isinstance(router_use_ssh_config, bool):
            raise TypeError("router_use_ssh_config must be a boolean")
        self.router_use_ssh_config = router_use_ssh_config
        controller_id = str(document.get("controller_id", self.controller_id))
        if not CONTROLLER_ID_RE.fullmatch(controller_id):
            raise ValueError("controller_id is invalid")
        self.controller_id = controller_id
        deployments = document.get("policy_deployments", [])
        if not isinstance(deployments, list):
            raise TypeError("policy_deployments must be a list")
        self.policy_deployments = [
            PolicyDeploymentManifest.from_dict(item)
            for item in deployments
            if isinstance(item, dict)
        ]
        if len(self.policy_deployments) != len(deployments):
            raise TypeError("each policy deployment must be an object")
        self.active_region = str(document.get("active_region", "active-astrill"))
        companion_enabled = document.get("companion_enabled", True)
        if not isinstance(companion_enabled, bool):
            raise TypeError("companion_enabled must be a boolean")
        self.companion_enabled = companion_enabled
        # Configurations created before read-only access existed were already
        # writable. Preserve that behavior instead of silently disabling a
        # deployed companion during an upgrade.
        read_only = document.get("read_only", False)
        if not isinstance(read_only, bool):
            raise TypeError("read_only must be a boolean")
        self.read_only = read_only
        self.enabled_extensions = [
            str(item) for item in document.get("enabled_extensions", ["core-catalog"])
        ]
        if "core-catalog" not in self.enabled_extensions:
            self.enabled_extensions.insert(0, "core-catalog")
        self.rules = [Rule.from_dict(item) for item in document.get("rules", [])]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "router_host": self.router_host,
            "router_user": self.router_user,
            "router_port": self.router_port,
            "router_identity": self.router_identity,
            "router_use_ssh_config": self.router_use_ssh_config,
            "controller_id": self.controller_id,
            "policy_deployments": [
                deployment.to_dict() for deployment in self.policy_deployments
            ],
            "active_region": self.active_region,
            "companion_enabled": self.companion_enabled,
            "read_only": self.read_only,
            "enabled_extensions": self.enabled_extensions,
            "rules": [rule.to_dict() for rule in self.rules],
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".config.", suffix=".tmp", dir=self.path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def deployment_for(
        self,
        *,
        router_host: str,
        router_port: int,
        companion_version: str,
    ) -> PolicyDeploymentManifest | None:
        """Find the version-bound manifest for the configured router endpoint."""

        matches = [
            deployment
            for deployment in self.policy_deployments
            if deployment.router_host == router_host
            and deployment.router_port == router_port
            and deployment.companion_version == companion_version
            and deployment.controller_id == self.controller_id
        ]
        return matches[-1] if matches else None

    def upsert_deployment(self, manifest: PolicyDeploymentManifest) -> None:
        manifest.validate()
        if manifest.controller_id != self.controller_id:
            raise ValueError(
                "deployment controller ID does not match this installation"
            )
        self.policy_deployments = [
            current
            for current in self.policy_deployments
            if not (
                current.router_host == manifest.router_host
                and current.router_port == manifest.router_port
                and current.companion_version == manifest.companion_version
                and current.controller_id == manifest.controller_id
            )
        ]
        self.policy_deployments.append(manifest)
        self.save()


def default_uu_rule() -> Rule:
    return Rule(
        id="uu-remote-direct",
        name="UU Remote",
        match_kind=MatchKind.SERVICE,
        selector="uu-remote",
        target=RouteTarget.DIRECT,
        region="direct",
        enabled=True,
        priority=100,
        metadata={"minimum_bypass": True},
    )
