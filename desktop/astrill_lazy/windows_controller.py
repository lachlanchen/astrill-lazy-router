from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .astrill import (
    ASTRILL_PROTOCOL_NAMES,
    AstrillConnectionSelection,
    AstrillFavorite,
    AstrillServer,
    group_by_region,
    parse_applet,
    parse_astrill_favorites,
    update_astrill_favorite_list_batch,
)
from .catalog import Catalog, load_catalog
from .compiler import MAX_COMPILED_BYTES, compile_rules
from .detector import MINIMUM_BYPASS_SERVICES
from .installer import EnsureResult, InstallResult, RouterInstaller
from .models import Compilation, MatchKind, RouteTarget, Rule
from .native_settings import NativeAstrillSettings
from .router import AstrillConnectionResult, RouterClient, RouterError
from .service_policy import ServiceRouteMode, service_policy_route
from .ssh_setup import identity_path
from .store import (
    ConfigStore,
    PolicyDeploymentManifest,
    normalize_overlay_source,
)
from .windows_ssh_setup import (
    WindowsHostKey,
    WindowsKeyAuthorization,
    authorize_windows_router_key_via_telnet,
    inspect_windows_host_key,
)

SSH_HOST_RE = re.compile(r"^[a-zA-Z0-9._:\[\]-]{1,255}$")
SSH_USER_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
COMPILED_CAPACITY_RE = re.compile(
    r"compiled policy is ([\d,]+) bytes; the router limit is ([\d,]+)",
    re.IGNORECASE,
)
MAX_OVERLAY_BYTES = 32_768
MAX_OVERLAY_ROWS = 320


class ControllerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceChangeSummary:
    added: int
    updated: int


@dataclass(frozen=True)
class ServerCatalog:
    servers: tuple[AstrillServer, ...]
    groups: dict[str, tuple[AstrillServer, ...]]


@dataclass(frozen=True)
class PolicyCompilationSummary:
    """A non-throwing preview of the exact document offered to DD-WRT."""

    rule_ids: tuple[str, ...]
    rule_count: int
    enabled_count: int
    compiled_rows: int
    compiled_bytes: int | None
    limit_bytes: int | None = MAX_COMPILED_BYTES
    warnings: tuple[str, ...] = ()
    error: str | None = None
    compilation: Compilation | None = field(default=None, repr=False, compare=False)

    @property
    def can_apply(self) -> bool:
        return self.error is None and self.compilation is not None

    @property
    def remaining_bytes(self) -> int | None:
        if self.compiled_bytes is None or self.limit_bytes is None:
            return None
        return self.limit_bytes - self.compiled_bytes


@dataclass(frozen=True)
class HybridPolicyComparison:
    """UI-neutral comparison of a local manifest with layered router state."""

    manifest: PolicyDeploymentManifest | None
    status: dict[str, Any]
    runtime_epoch: str | None
    core_matches: bool | None
    overlay_present: bool
    overlay_matches: bool | None
    restore_needed: bool


@dataclass(frozen=True)
class PolicyRuntimeSummary:
    """Backward-compatible interpretation of optional companion health fields."""

    state: str
    precedence_ok: bool | None
    native_min_pref: int | None
    direct_pref: int | None
    vpn_pref: int | None
    table_readiness: dict[str, bool]
    last_error: str
    vpn_fail_closed: bool | None

    @property
    def degraded(self) -> bool:
        return self.state == "degraded"


@dataclass(frozen=True)
class PolicyOriginComparison:
    """Exact enabled-origin agreement when router rule detail is available."""

    local_enabled_ids: frozenset[str]
    applied_enabled_ids: frozenset[str] | None
    fallback_applied_count: int | None

    @property
    def exact(self) -> bool:
        return self.applied_enabled_ids is not None

    @property
    def applied_count(self) -> int | None:
        if self.applied_enabled_ids is not None:
            return len(self.applied_enabled_ids)
        return self.fallback_applied_count

    @property
    def missing_ids(self) -> frozenset[str]:
        if self.applied_enabled_ids is None:
            return frozenset()
        return self.local_enabled_ids - self.applied_enabled_ids

    @property
    def extra_ids(self) -> frozenset[str]:
        if self.applied_enabled_ids is None:
            return frozenset()
        return self.applied_enabled_ids - self.local_enabled_ids

    @property
    def matches(self) -> bool | None:
        if self.applied_enabled_ids is None:
            return None
        return not self.missing_ids and not self.extra_ids


def summarize_policy_runtime(status: dict[str, Any]) -> PolicyRuntimeSummary:
    """Interpret both current and older companion status documents."""

    raw_health = str(status.get("policy_health", "")).strip().casefold()
    if raw_health in {"ready", "healthy", "ok"}:
        state = "ready"
    elif raw_health in {"degraded", "failed", "error"}:
        state = "degraded"
    else:
        state = "unknown"

    precedence_ok = _optional_bool(status.get("precedence_ok"))
    last_error = str(status.get("last_reconcile_error") or "").strip()
    raw_tables = status.get("table_readiness")
    table_readiness = (
        {
            str(name): ready
            for name, value in raw_tables.items()
            if (ready := _optional_bool(value)) is not None
        }
        if isinstance(raw_tables, dict)
        else {}
    )
    if (
        precedence_ok is False
        or bool(last_error)
        or (
            state == "unknown"
            and status.get("vpn_state") == "up"
            and any(not ready for ready in table_readiness.values())
        )
    ):
        state = "degraded"

    return PolicyRuntimeSummary(
        state=state,
        precedence_ok=precedence_ok,
        native_min_pref=_optional_int(status.get("native_min_pref")),
        direct_pref=_optional_int(status.get("direct_pref")),
        vpn_pref=_optional_int(status.get("vpn_pref")),
        table_readiness=table_readiness,
        last_error=last_error,
        vpn_fail_closed=_optional_bool(status.get("vpn_fail_closed")),
    )


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _enabled_origins_from_status(
    status: dict[str, Any],
) -> frozenset[str] | None:
    if "rules" not in status:
        return None
    rows = status.get("rules")
    if not isinstance(rows, list):
        return None
    enabled_origins: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            return None
        origin = row.get("origin")
        enabled = _optional_bool(row.get("enabled"))
        if not isinstance(origin, str) or not origin.strip() or enabled is None:
            return None
        if enabled:
            enabled_origins.add(origin.strip())
    return frozenset(enabled_origins)


def _policy_layers(status: dict[str, Any]) -> dict[str, Any] | None:
    """Accept top-level fields plus defensive nested companion envelopes."""

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
    return None


def _layer_hash(layer: dict[str, Any] | None) -> str | None:
    if not isinstance(layer, dict):
        return None
    value = layer.get("hash", layer.get("sha256"))
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().casefold()
    if re.fullmatch(r"[0-9a-f]{32}", normalized):
        return f"md5:{normalized}"
    if re.fullmatch(r"[0-9a-f]{64}", normalized):
        return f"sha256:{normalized}"
    return normalized


def _payload_hash(payload: str) -> str:
    digest = hashlib.md5(payload.encode("ascii"), usedforsecurity=False).hexdigest()
    return f"md5:{digest}"


def _runtime_epoch_from_layers(layers: dict[str, Any]) -> str | None:
    value = layers.get("runtime_epoch")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _owner_overlay(
    layers: dict[str, Any],
    owner: str,
) -> dict[str, Any] | None:
    overlays = layers.get("overlays")
    if not isinstance(overlays, list):
        return None
    for item in overlays:
        if (
            isinstance(item, dict)
            and str(item.get("owner", "")).strip().casefold() == owner.casefold()
        ):
            return item
    return None


def _layer_generation(layer: dict[str, Any] | None) -> int:
    if not isinstance(layer, dict):
        return 0
    value = _optional_int(layer.get("generation"))
    return value if value is not None and value >= 0 else 0


def _layer_source(layer: dict[str, Any] | None) -> str | None:
    if not isinstance(layer, dict):
        return None
    for key in ("source", "source_cidr", "resolved_source"):
        value = layer.get(key)
        if isinstance(value, str) and value.strip():
            try:
                normalized = normalize_overlay_source(value)
            except ValueError:
                continue
            if normalized != "auto":
                return normalized
    return None


def _layer_source_mac(layer: dict[str, Any] | None) -> str | None:
    if not isinstance(layer, dict):
        return None
    for key in ("source_mac", "mac"):
        value = layer.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().casefold().replace("-", ":")
    return None


def _layer_origin_ids(layer: dict[str, Any] | None) -> tuple[str, ...] | None:
    if not isinstance(layer, dict):
        return None
    for key in ("origin_ids", "origins"):
        value = layer.get(key)
        if isinstance(value, list) and all(
            isinstance(item, str) and item.strip() for item in value
        ):
            return tuple(dict.fromkeys(item.strip() for item in value))
    return None


@dataclass(frozen=True)
class WindowsConnectionState:
    settings: NativeAstrillSettings
    status: dict[str, Any]
    server_catalog: ServerCatalog


class WindowsController:
    """UI-neutral operations shared by the native Windows frontend."""

    def __init__(
        self,
        *,
        store: ConfigStore | None = None,
        catalog: Catalog | None = None,
        router: RouterClient | None = None,
    ) -> None:
        self.store = store or ConfigStore()
        self.catalog = catalog or load_catalog(self.store.enabled_extensions)
        self.router = router or self._router_client_from_store()
        self.server_catalog = ServerCatalog((), {})
        self.recovery_notice: str | None = None
        # Reconciliation is event-driven by the frontend. Never turn it into a
        # recurring router poll or repeatedly mutate one runtime epoch.
        self._overlay_restore_attempted_epochs: set[str] = set()

    def configure_router(
        self,
        target: str,
        *,
        user: str | None = None,
        port: int | None = None,
        identity_file: str | None = None,
        use_ssh_config: bool | None = None,
    ) -> str:
        original_target = target.strip()
        normalized = original_target
        embedded_user: str | None = None
        if normalized.count("@") == 1:
            embedded_user, normalized = normalized.split("@", 1)
        elif "@" in normalized:
            raise ValueError("router target contains more than one user separator")
        if (
            not SSH_HOST_RE.fullmatch(normalized)
            or normalized.startswith("-")
            or ".." in normalized
        ):
            raise ValueError(
                "router target must be an SSH alias, hostname, IP address, "
                "or user@host without spaces"
            )
        normalized_user = embedded_user or (
            user.strip() if user is not None else self.store.router_user
        )
        if not SSH_USER_RE.fullmatch(normalized_user):
            raise ValueError(
                "router SSH user must contain only letters, numbers, dot, "
                "underscore, or hyphen"
            )
        normalized_port = self.store.router_port if port is None else port
        if (
            not isinstance(normalized_port, int)
            or isinstance(normalized_port, bool)
            or not 1 <= normalized_port <= 65535
        ):
            raise ValueError("router SSH port must be between 1 and 65535")
        normalized_identity = (
            self.store.router_identity
            if identity_file is None
            else identity_file.strip()
        )
        normalized_use_ssh_config = (
            self.store.router_use_ssh_config
            if use_ssh_config is None
            else bool(use_ssh_config)
        )
        if not normalized_use_ssh_config:
            try:
                identity_path(normalized_identity)
            except ValueError as exc:
                raise ValueError(f"router SSH identity path is invalid: {exc}") from exc
        self.store.router_host = (
            original_target if normalized_use_ssh_config else normalized
        )
        self.store.router_user = normalized_user
        self.store.router_port = normalized_port
        self.store.router_identity = normalized_identity
        self.store.router_use_ssh_config = normalized_use_ssh_config
        self.store.save()
        self.router = self._router_client_from_store()
        self.server_catalog = ServerCatalog((), {})
        self._overlay_restore_attempted_epochs.clear()
        if normalized_use_ssh_config:
            return original_target
        return f"{normalized_user}@{normalized}"

    def set_read_only(self, read_only: bool) -> None:
        self.store.read_only = bool(read_only)
        self.store.save()

    def test_connection(self) -> bool:
        return self.router.ping()

    def inspect_router_host_key(self) -> WindowsHostKey:
        if self.store.router_use_ssh_config:
            raise ControllerError(
                "guided key setup requires explicit host, user, port, and "
                "private-key fields; disable OpenSSH config mode first"
            )
        return inspect_windows_host_key(
            self.store.router_host,
            self.store.router_port,
            known_hosts_path=self.known_hosts_path,
        )

    def authorize_router_key_via_telnet(
        self,
        host_key: WindowsHostKey,
        password: str,
        *,
        confirmed: bool = False,
    ) -> WindowsKeyAuthorization:
        if not confirmed:
            raise ControllerError(
                "explicit SSH fingerprint and Telnet warning confirmation is required"
            )
        if self.store.router_use_ssh_config:
            raise ControllerError(
                "guided key setup is unavailable while OpenSSH config mode is enabled"
            )
        if (
            host_key.host != self.store.router_host
            or host_key.port != self.store.router_port
            or host_key.known_hosts_path != self.known_hosts_path
        ):
            raise ControllerError(
                "router settings changed after the SSH fingerprint was inspected"
            )
        return authorize_windows_router_key_via_telnet(
            self.router,
            host_key,
            password,
            user=self.store.router_user,
            identity_file=self.store.router_identity,
        )

    def configure_policy_deployment(
        self,
        *,
        core_rule_ids: Iterable[str] = (),
        overlay_rule_ids: Iterable[str] = (),
        source: str = "auto",
        restore_overlay_after_reboot: bool = False,
        status: dict[str, Any] | None = None,
        host_key: WindowsHostKey | None = None,
    ) -> PolicyDeploymentManifest:
        """Bind selected layers to one trusted router runtime without mutating it."""

        self._require_companion_write("configuring hybrid policy storage")
        current, layers = self._read_layered_status(status)
        version = self._companion_version(current, layers)
        fingerprint = self._verified_host_fingerprint(host_key)
        normalized_source = normalize_overlay_source(source)
        core_ids = self._normalize_rule_ids(core_rule_ids)
        overlay_ids = self._normalize_rule_ids(overlay_rule_ids)
        overlap = set(core_ids) & set(overlay_ids)
        if overlap:
            raise ControllerError(
                "persistent core and RAM overlay cannot contain the same policy IDs: "
                + ", ".join(sorted(overlap))
            )
        core_payload = self._compile_layer_payload(core_ids, layer="core")
        overlay_payload = self._compile_layer_payload(
            overlay_ids,
            layer="overlay",
        )
        owner_layer = _owner_overlay(layers, self.store.controller_id)
        manifest = PolicyDeploymentManifest(
            router_host=self.store.router_host,
            router_port=self.store.router_port,
            router_host_key_fingerprint=fingerprint,
            companion_version=version,
            controller_id=self.store.controller_id,
            source=normalized_source,
            resolved_source=(
                _layer_source(owner_layer)
                if normalized_source == "auto"
                else normalized_source
            ),
            source_mac=_layer_source_mac(owner_layer),
            core_rule_ids=core_ids,
            overlay_rule_ids=overlay_ids,
            core_hash=_payload_hash(core_payload) if core_ids else None,
            overlay_hash=_payload_hash(overlay_payload) if overlay_ids else None,
            core_generation=_layer_generation(layers.get("core")),
            overlay_generation=_layer_generation(owner_layer),
            restore_overlay_after_reboot=bool(restore_overlay_after_reboot),
            last_runtime_epoch=None,
        )
        self.store.upsert_deployment(manifest)
        return manifest

    def hybrid_policy_status(
        self,
        status: dict[str, Any] | None = None,
    ) -> HybridPolicyComparison:
        """Compare this installation's manifest with layered router status."""

        current, layers = self._read_layered_status(status)
        version = self._companion_version(current, layers)
        manifest = self.store.deployment_for(
            router_host=self.store.router_host,
            router_port=self.store.router_port,
            companion_version=version,
        )
        epoch = _runtime_epoch_from_layers(layers)
        if manifest is None:
            return HybridPolicyComparison(
                manifest=None,
                status=current,
                runtime_epoch=epoch,
                core_matches=None,
                overlay_present=False,
                overlay_matches=None,
                restore_needed=False,
            )
        core_hash = _layer_hash(layers.get("core"))
        core_matches = (
            None
            if manifest.core_hash is None or core_hash is None
            else core_hash == manifest.core_hash.casefold()
        )
        owner_layer = _owner_overlay(layers, manifest.controller_id)
        overlay_hash = _layer_hash(owner_layer)
        overlay_matches = (
            None
            if manifest.overlay_hash is None
            else (
                owner_layer is not None
                and overlay_hash is not None
                and overlay_hash == manifest.overlay_hash.casefold()
            )
        )
        return HybridPolicyComparison(
            manifest=manifest,
            status=current,
            runtime_epoch=epoch,
            core_matches=core_matches,
            overlay_present=owner_layer is not None,
            overlay_matches=overlay_matches,
            restore_needed=(
                bool(manifest.overlay_rule_ids) and overlay_matches is not True
            ),
        )

    def set_overlay_restore_enabled(
        self,
        enabled: bool,
        source: str | None = None,
        *,
        status: dict[str, Any] | None = None,
        host_key: WindowsHostKey | None = None,
    ) -> PolicyDeploymentManifest:
        """Persist the explicit opt-in used by one-shot startup reconciliation."""

        if not isinstance(enabled, bool):
            raise TypeError("overlay restore preference must be a boolean")
        current, layers = self._read_layered_status(status)
        manifest = self._require_deployment(current, layers)
        if enabled:
            self._require_companion_write("enabling automatic RAM overlay restore")
            self._verified_host_fingerprint(
                host_key,
                expected=manifest.router_host_key_fingerprint,
            )
            if not manifest.overlay_rule_ids or manifest.overlay_hash is None:
                raise ControllerError(
                    "select and save at least one RAM overlay policy before "
                    "enabling automatic restore"
                )
            if source is not None:
                manifest.source = normalize_overlay_source(source)
                if manifest.source != "auto":
                    manifest.resolved_source = manifest.source
        manifest.restore_overlay_after_reboot = enabled
        if not enabled:
            manifest.last_runtime_epoch = _runtime_epoch_from_layers(layers)
        self.store.upsert_deployment(manifest)
        return manifest

    def apply_persistent_core(
        self,
        rule_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Persist selected core rules while retaining the router's RAM overlays."""

        self._require_companion_write("pinning policies to the persistent core")
        requested = None if rule_ids is None else self._normalize_rule_ids(rule_ids)
        local_manifest = self._local_deployment_for_endpoint()
        if local_manifest is not None:
            candidate_ids = (
                local_manifest.core_rule_ids if requested is None else requested
            )
            self._reject_layer_overlap(
                candidate_ids,
                local_manifest.overlay_rule_ids,
            )
        status, layers = self._read_layered_status()
        manifest = self._require_deployment(status, layers)
        self._verified_host_fingerprint(
            expected=manifest.router_host_key_fingerprint,
        )
        helper_action = self._ensure_hybrid_helper()
        if helper_action == "installed":
            status, layers = self._read_layered_status()
            manifest = self._require_deployment(status, layers)
        selected = manifest.core_rule_ids if requested is None else requested
        self._reject_layer_overlap(selected, manifest.overlay_rule_ids)
        payload = self._compile_layer_payload(selected, layer="core")
        result = self.router.core_apply(payload)
        result_layers = self._require_layered_document(result)
        expected_hash = _payload_hash(payload)
        self._verify_applied_hash(
            expected_hash,
            _layer_hash(result_layers.get("core")),
            "persistent core",
        )
        manifest.core_rule_ids = selected
        manifest.core_hash = expected_hash
        manifest.core_generation = _layer_generation(result_layers.get("core"))
        self.store.upsert_deployment(manifest)
        return result

    def rollback_persistent_core(self) -> dict[str, Any]:
        """Roll back only the persistent core on a hybrid-capable companion."""

        self._require_companion_write("rolling back the persistent policy core")
        status, layers = self._read_layered_status()
        manifest = self._require_deployment(status, layers)
        self._verified_host_fingerprint(
            expected=manifest.router_host_key_fingerprint,
        )
        if self._ensure_hybrid_helper() == "installed":
            status, layers = self._read_layered_status()
            manifest = self._require_deployment(status, layers)
        result = self.router.core_rollback()
        result_layers = self._require_layered_document(result)
        core_layer = result_layers.get("core")
        manifest.core_rule_ids = _layer_origin_ids(core_layer) or ()
        manifest.core_hash = _layer_hash(core_layer)
        manifest.core_generation = _layer_generation(core_layer)
        self.store.upsert_deployment(manifest)
        return result

    def load_ram_overlay(
        self,
        rule_ids: Iterable[str],
        source: str | None = None,
    ) -> dict[str, Any]:
        """Explicitly replace this controller's volatile, source-scoped overlay."""

        selected = self._normalize_rule_ids(rule_ids)
        local_manifest = self._local_deployment_for_endpoint()
        if local_manifest is not None:
            self._reject_layer_overlap(
                local_manifest.core_rule_ids,
                selected,
            )
        return self._put_ram_overlay(
            selected,
            source=source,
            require_saved_hash=False,
        )

    def restore_ram_overlay_now(
        self,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Explicitly restore the exact overlay recorded in the local manifest."""

        return self._put_ram_overlay(
            None,
            source=source,
            require_saved_hash=True,
        )

    def remove_ram_overlay(self) -> dict[str, Any]:
        """Remove only this controller's RAM layer, leaving core/peers untouched."""

        self._require_companion_write("removing this computer's RAM overlay")
        status, layers = self._read_layered_status()
        manifest = self._require_deployment(status, layers)
        self._verified_host_fingerprint(
            expected=manifest.router_host_key_fingerprint,
        )
        if self._ensure_hybrid_helper() == "installed":
            status, layers = self._read_layered_status()
            manifest = self._require_deployment(status, layers)
        owner_layer = _owner_overlay(layers, manifest.controller_id)
        if owner_layer is None:
            manifest.overlay_generation = 0
            manifest.restore_overlay_after_reboot = False
            manifest.last_runtime_epoch = _runtime_epoch_from_layers(layers)
            self.store.upsert_deployment(manifest)
            return status
        result = self.router.overlay_remove(
            manifest.controller_id,
            _layer_generation(owner_layer),
        )
        result_layers = self._require_layered_document(result)
        if _owner_overlay(result_layers, manifest.controller_id) is not None:
            raise ControllerError(
                "router reported success but this computer's overlay remains active"
            )
        manifest.overlay_generation = 0
        manifest.restore_overlay_after_reboot = False
        manifest.last_runtime_epoch = _runtime_epoch_from_layers(result_layers)
        self.store.upsert_deployment(manifest)
        return result

    def refresh_status(self) -> dict[str, Any]:
        if self.store.companion_enabled:
            return self.router.status()
        return self.router.native_astrill_status()

    def policy_origin_comparison(
        self,
        status: dict[str, Any],
    ) -> PolicyOriginComparison:
        local_ids = frozenset(rule.id for rule in self.store.rules if rule.enabled)
        applied_ids = _enabled_origins_from_status(status)
        count_value = status.get(
            "enabled_origin_count",
            status.get("origin_count"),
        )
        fallback_count = _optional_int(count_value)
        if fallback_count is not None and fallback_count < 0:
            fallback_count = None
        return PolicyOriginComparison(
            local_enabled_ids=local_ids,
            applied_enabled_ids=applied_ids,
            fallback_applied_count=fallback_count,
        )

    def reconcile_status(
        self,
        *,
        presence: dict[str, Any] | None = None,
        companion_status: dict[str, Any] | None = None,
        native_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resume safely after a router reboot or lost companion installation."""
        self.recovery_notice = None
        if not self.store.companion_enabled:
            return (
                native_status
                if native_status is not None
                else self.router.native_astrill_status()
            )

        if presence is None:
            presence = self.router.companion_presence()
        if not presence.get("installed"):
            if presence.get("runtime"):
                raise ControllerError(
                    "the companion runtime exists without its persistent router "
                    "markers; use Restore native only or a separately confirmed "
                    "Install / upgrade before making router changes"
                )
            self.store.companion_enabled = False
            self.store.save()
            self.recovery_notice = (
                "The router no longer has the companion. Native-only mode was "
                "resumed; Install / upgrade remains separately confirmed."
            )
            return (
                native_status
                if native_status is not None
                else self.router.native_astrill_status()
            )

        installer = RouterInstaller(self.router)
        check = installer.check(
            presence=presence,
            status=companion_status,
        )
        if check.action == "none":
            if check.status is None:
                raise ControllerError(
                    "the router companion passed inspection without returning status"
                )
            return self._finish_policy_reconciliation(check.status)
        if check.action == "repair":
            result = installer.ensure(allow_install=False)
            if result.action == "degraded":
                reason = str(
                    result.status.get("last_reconcile_error")
                    or "policy precedence or routing-table verification failed"
                ).strip()
                self.recovery_notice = (
                    "The companion runtime is present, but policy routing remains "
                    f"degraded: {reason}. Astrill tunnel state was preserved."
                )
            elif result.action == "repaired":
                self.recovery_notice = (
                    "The validated companion runtime was restored from router "
                    "NVRAM after reboot."
                )
            else:
                self.recovery_notice = (
                    "The companion runtime became healthy during recovery; no "
                    "rewrite was needed."
                )
            return self._finish_policy_reconciliation(result.status)

        installed = check.installed_version or "unknown"
        raise ControllerError(
            f"router companion {installed} requires the separately confirmed "
            f"Install / upgrade action: {check.reason}"
        )

    def load_clients(self) -> list[dict[str, Any]]:
        if self.store.companion_enabled:
            return self.router.clients()
        return self.router.native_clients()

    def load_servers(self) -> ServerCatalog:
        servers = parse_applet(self.router.fetch_astrill_payload())
        self.server_catalog = ServerCatalog(
            servers=servers,
            groups=group_by_region(servers, self.catalog.regions),
        )
        return self.server_catalog

    def load_native_settings(self) -> NativeAstrillSettings:
        return self.router.native_astrill_settings()

    def load_connection_state(
        self,
        *,
        refresh_servers: bool = True,
    ) -> WindowsConnectionState:
        """Read connection status/settings together, then sequence catalog I/O."""

        snapshot = self.router.monitor_snapshot(
            include_companion=self.store.companion_enabled
        )
        status = self.reconcile_status(
            presence=snapshot.companion_presence,
            companion_status=snapshot.companion_status,
            native_status=snapshot.native_status,
        )
        if refresh_servers or not self.server_catalog.servers:
            self.load_servers()
        return WindowsConnectionState(
            settings=snapshot.settings,
            status=status,
            server_catalog=self.server_catalog,
        )

    def save_native_settings(self, changes: dict[str, Any]) -> NativeAstrillSettings:
        self._require_write("saving native Astrill settings")
        return self.router.update_native_astrill_settings(changes)

    def set_endpoint_favorite(
        self,
        server: AstrillServer,
        protocol: int | None,
        *,
        enabled: bool,
    ) -> NativeAstrillSettings:
        """Add or remove one native favorite using a fresh router snapshot."""

        return self.set_endpoint_favorites(
            (server,),
            protocol,
            enabled=enabled,
        )

    def set_endpoint_favorites(
        self,
        servers: Iterable[AstrillServer],
        protocol: int | None,
        *,
        enabled: bool,
    ) -> NativeAstrillSettings:
        """Set favorite membership for several endpoints in one transaction."""

        self._require_write("changing Astrill favorite endpoints")
        if not isinstance(enabled, bool):
            raise TypeError("favorite state must be true or false")
        selected = tuple(servers)
        if not selected:
            raise ValueError("select at least one Astrill endpoint")
        selected_ids: set[int] = set()
        for server in selected:
            if not isinstance(server, AstrillServer):
                raise TypeError("favorite endpoint must be an Astrill server")
            if server.id in selected_ids:
                raise ValueError(
                    f"favorite endpoint selection contains duplicate server ID "
                    f"{server.id}"
                )
            selected_ids.add(server.id)
        if enabled and (
            not isinstance(protocol, int)
            or isinstance(protocol, bool)
            or protocol not in range(len(ASTRILL_PROTOCOL_NAMES))
        ):
            raise ValueError("select an Astrill protocol for the new favorites")

        settings = self.router.native_astrill_settings()
        current = settings.get("astrill_favlist")
        existing_ids = {
            favorite.server_id for favorite in parse_astrill_favorites(current)
        }
        changes: list[tuple[int, AstrillFavorite | None]] = []
        if enabled:
            catalog_by_id = {
                server.id: server for server in self.server_catalog.servers
            }
            for server in selected:
                if server.id in existing_ids:
                    continue
                catalog_server = catalog_by_id.get(server.id)
                if catalog_server is None:
                    raise ValueError("load Astrill endpoints before adding favorites")
                selection = AstrillConnectionSelection.from_server(
                    catalog_server,
                    protocol,
                    0,
                )
                changes.append(
                    (
                        server.id,
                        AstrillFavorite.from_selection(selection),
                    )
                )
        else:
            changes.extend((server.id, None) for server in selected)

        replacement = update_astrill_favorite_list_batch(
            current,
            changes,
        )
        if replacement == current:
            return settings
        return self.router.replace_astrill_favorites(current, replacement)

    def apply_endpoint_favorite_changes(
        self,
        changes: Iterable[tuple[int, AstrillFavorite | None]],
    ) -> NativeAstrillSettings:
        """Fresh-merge explicit favorite edits and replace the list with CAS."""

        self._require_write("changing Astrill favorite endpoints")
        requested = tuple(changes)
        if not requested:
            raise ValueError("select at least one Astrill favorite change")
        settings = self.router.native_astrill_settings()
        current = settings.get("astrill_favlist")
        replacement = update_astrill_favorite_list_batch(current, requested)
        if replacement == current:
            return settings
        return self.router.replace_astrill_favorites(current, replacement)

    def next_priority(self) -> int:
        return min(
            9999,
            max((rule.priority for rule in self.store.rules), default=0) + 100,
        )

    def add_custom_rule(
        self,
        *,
        name: str,
        match_kind: MatchKind,
        selector: str,
        target: RouteTarget,
        region: str,
        priority: int | None = None,
    ) -> Rule:
        if match_kind is MatchKind.PROCESS:
            raise ValueError(
                "per-application policies require the Ubuntu network-namespace "
                "provider and cannot be created by the Windows frontend"
            )
        normalized_name = name.strip() or selector.strip()
        normalized_selector = selector.strip()
        normalized_region = "direct" if target is RouteTarget.DIRECT else region
        rule = Rule.create(
            name=normalized_name,
            match_kind=match_kind,
            selector=normalized_selector,
            target=target,
            region=normalized_region,
            priority=self.next_priority() if priority is None else priority,
        )
        rule.validate()
        self.store.rules.append(rule)
        self.store.save()
        return rule

    def add_services(
        self,
        service_ids: list[str] | tuple[str, ...] | set[str],
        mode: ServiceRouteMode,
    ) -> ServiceChangeSummary:
        requested = set(service_ids)
        unknown = requested - self.catalog.services_by_id.keys()
        if unknown:
            raise ValueError("unknown service profile: " + ", ".join(sorted(unknown)))
        existing = {
            rule.selector: rule
            for rule in self.store.rules
            if rule.match_kind is MatchKind.SERVICE
        }
        vpn_regions = {
            region.id for region in self.catalog.regions if region.kind != "direct"
        }
        added = 0
        updated = 0
        for service in self.catalog.services:
            if service.id not in requested:
                continue
            rule = existing.get(service.id)
            current_region: str | None = None
            if rule is not None:
                current_region = rule.region
                if current_region == "direct":
                    remembered = str(rule.metadata.get("country_override", ""))
                    current_region = remembered if remembered in vpn_regions else None
            target, region = service_policy_route(
                service,
                mode,
                current_region=current_region,
            )
            if rule is None:
                rule = Rule.create(
                    name=service.name,
                    match_kind=MatchKind.SERVICE,
                    selector=service.id,
                    target=target,
                    region=region,
                    priority=self.next_priority(),
                )
                if service.id in MINIMUM_BYPASS_SERVICES:
                    rule.metadata["minimum_bypass"] = True
                self.store.rules.append(rule)
                existing[service.id] = rule
                added += 1
                continue
            if rule.target is target and rule.region == region:
                continue
            if target is RouteTarget.DIRECT and rule.region != "direct":
                rule.metadata["country_override"] = rule.region
            elif target is RouteTarget.VPN:
                rule.metadata["country_override"] = region
            rule.target = target
            rule.region = region
            rule.metadata.pop("route_recommendation", None)
            updated += 1
        if added or updated:
            self.store.save()
        return ServiceChangeSummary(added=added, updated=updated)

    def add_device(
        self,
        address: str,
        name: str,
        target: RouteTarget,
    ) -> Rule:
        return self.add_custom_rule(
            name=name.strip() or address,
            match_kind=MatchKind.DEVICE,
            selector=address,
            target=target,
            region=("direct" if target is RouteTarget.DIRECT else "active-astrill"),
        )

    def update_rule(
        self,
        rule_id: str,
        *,
        name: str | None = None,
        target: RouteTarget | None = None,
        region: str | None = None,
        enabled: bool | None = None,
        priority: int | None = None,
    ) -> Rule:
        current = self.rule_by_id(rule_id)
        rule = Rule.from_dict(current.to_dict())
        if name is not None:
            rule.name = name.strip()
        if target is not None and target is not rule.target:
            if target is RouteTarget.DIRECT:
                if rule.region != "direct":
                    rule.metadata["country_override"] = rule.region
                rule.region = "direct"
            else:
                remembered = str(
                    rule.metadata.get("country_override", "active-astrill")
                )
                known = {item.id for item in self.catalog.regions}
                rule.region = remembered if remembered in known else "active-astrill"
            rule.target = target
            rule.metadata.pop("route_recommendation", None)
        if region is not None:
            if rule.target is RouteTarget.DIRECT and region != "direct":
                raise ValueError("Direct policies must use the Direct region")
            rule.region = region
        if enabled is not None:
            rule.enabled = bool(enabled)
        if priority is not None:
            rule.priority = priority
        rule.validate()
        self.store.rules[self.store.rules.index(current)] = rule
        self.store.save()
        return rule

    def delete_rule(self, rule_id: str) -> Rule:
        rule = self.rule_by_id(rule_id)
        self.store.rules.remove(rule)
        self.store.save()
        return rule

    def rule_by_id(self, rule_id: str) -> Rule:
        try:
            return next(rule for rule in self.store.rules if rule.id == rule_id)
        except StopIteration as exc:
            raise KeyError(rule_id) from exc

    def policy_preflight(
        self,
        rule_ids: Iterable[str] | None = None,
    ) -> PolicyCompilationSummary:
        """Compile a policy scope without raising for validation or capacity errors."""

        return self._policy_preflight(
            rule_ids,
            max_bytes=MAX_COMPILED_BYTES,
            capacity_name="router",
        )

    def policy_layer_preflight(
        self,
        rule_ids: Iterable[str],
        *,
        layer: str,
    ) -> PolicyCompilationSummary:
        """Preview a persistent core or RAM overlay with the right byte contract."""

        normalized_layer = layer.strip().casefold()
        if normalized_layer == "core":
            max_bytes: int | None = MAX_COMPILED_BYTES
        elif normalized_layer == "overlay":
            # Router admission additionally protects generated matches and RAM.
            # Do not incorrectly apply the NVRAM limit to a volatile document.
            max_bytes = MAX_OVERLAY_BYTES
        else:
            raise ValueError("policy layer must be 'core' or 'overlay'")
        return self._policy_preflight(
            rule_ids,
            max_bytes=max_bytes,
            capacity_name=normalized_layer,
        )

    def _policy_preflight(
        self,
        rule_ids: Iterable[str] | None,
        *,
        max_bytes: int | None,
        capacity_name: str,
    ) -> PolicyCompilationSummary:
        if rule_ids is None:
            selected = list(self.store.rules)
        else:
            requested = tuple(dict.fromkeys(str(rule_id) for rule_id in rule_ids))
            if not requested:
                return PolicyCompilationSummary(
                    rule_ids=(),
                    rule_count=0,
                    enabled_count=0,
                    compiled_rows=0,
                    compiled_bytes=None,
                    limit_bytes=max_bytes,
                    error=(
                        "Select at least one policy for Apply selected. Use the "
                        "full Apply policies action to intentionally install an "
                        "empty policy document."
                    ),
                )
            by_id = {rule.id: rule for rule in self.store.rules}
            missing = tuple(rule_id for rule_id in requested if rule_id not in by_id)
            if missing:
                names = ", ".join(missing)
                return PolicyCompilationSummary(
                    rule_ids=requested,
                    rule_count=0,
                    enabled_count=0,
                    compiled_rows=0,
                    compiled_bytes=None,
                    limit_bytes=max_bytes,
                    error=f"Selected policy no longer exists: {names}.",
                )
            selected = [by_id[rule_id] for rule_id in requested]

        selected_ids = tuple(rule.id for rule in selected)
        compiled_rows = self._compiled_row_count(selected)
        try:
            compilation = compile_rules(
                selected,
                self.catalog,
                max_bytes=max_bytes,
            )
        except ValueError as exc:
            original = str(exc).strip() or "Policy compilation failed."
            match = COMPILED_CAPACITY_RE.search(original)
            if match is not None:
                compiled_bytes = int(match.group(1).replace(",", ""))
                limit_bytes = int(match.group(2).replace(",", ""))
                if capacity_name == "router":
                    error = (
                        f"Compiled policy needs {compiled_bytes:,} bytes, but this "
                        f"router accepts at most {limit_bytes:,}. Select a smaller "
                        "set in the policy table and use Apply selected; all other "
                        "policies will remain saved locally."
                    )
                else:
                    error = (
                        f"Compiled {capacity_name} needs {compiled_bytes:,} bytes, "
                        f"but it accepts at most {limit_bytes:,}."
                    )
            else:
                compiled_bytes = None
                limit_bytes = max_bytes
                error = f"Policies cannot be compiled: {original}"
            return PolicyCompilationSummary(
                rule_ids=selected_ids,
                rule_count=len(selected),
                enabled_count=sum(rule.enabled for rule in selected),
                compiled_rows=compiled_rows,
                compiled_bytes=compiled_bytes,
                limit_bytes=limit_bytes,
                error=error,
            )

        payload = compilation.to_tsv()
        if capacity_name == "overlay" and len(compilation.rules) > MAX_OVERLAY_ROWS:
            return PolicyCompilationSummary(
                rule_ids=selected_ids,
                rule_count=len(selected),
                enabled_count=sum(rule.enabled for rule in selected),
                compiled_rows=len(compilation.rules),
                compiled_bytes=len(payload.encode("ascii")),
                limit_bytes=max_bytes,
                warnings=compilation.warnings,
                error=(
                    f"Compiled RAM overlay has {len(compilation.rules):,} rows, "
                    f"but one owner accepts at most {MAX_OVERLAY_ROWS:,}."
                ),
            )
        return PolicyCompilationSummary(
            rule_ids=selected_ids,
            rule_count=len(selected),
            enabled_count=sum(rule.enabled for rule in selected),
            compiled_rows=len(compilation.rules),
            compiled_bytes=len(payload.encode("ascii")),
            limit_bytes=max_bytes,
            warnings=compilation.warnings,
            compilation=compilation,
        )

    def apply_rules(
        self,
        rule_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        self._require_companion_write("applying router policies")
        preflight = self.policy_preflight(rule_ids)
        if not preflight.can_apply or preflight.compilation is None:
            raise ControllerError(preflight.error or "Policies cannot be compiled.")
        return self.router.apply_rules(preflight.compilation.to_tsv())

    def _compiled_row_count(self, rules: Iterable[Rule]) -> int:
        rows = 0
        services = self.catalog.services_by_id
        for rule in rules:
            if rule.match_kind is MatchKind.SERVICE:
                service = services.get(rule.selector)
                if service is not None:
                    rows += len(service.domains) + len(service.networks)
            elif (
                rule.match_kind is not MatchKind.PROCESS
                or str(rule.metadata.get("namespace_ip", "")).strip()
            ):
                rows += 1
        return rows

    def _normalize_rule_ids(self, rule_ids: Iterable[str]) -> tuple[str, ...]:
        values = tuple(str(rule_id).strip() for rule_id in rule_ids)
        if any(not value for value in values):
            raise ValueError("policy IDs cannot be empty")
        if len(values) != len(set(values)):
            raise ValueError("policy selection contains duplicate IDs")
        missing = set(values) - {rule.id for rule in self.store.rules}
        if missing:
            raise ControllerError(
                "selected policy no longer exists: " + ", ".join(sorted(missing))
            )
        return values

    def _local_deployment_for_endpoint(self) -> PolicyDeploymentManifest | None:
        matches = [
            deployment
            for deployment in self.store.policy_deployments
            if deployment.router_host == self.store.router_host
            and deployment.router_port == self.store.router_port
            and deployment.controller_id == self.store.controller_id
        ]
        return matches[-1] if matches else None

    @staticmethod
    def _reject_layer_overlap(
        core_rule_ids: Iterable[str],
        overlay_rule_ids: Iterable[str],
    ) -> None:
        overlap = set(core_rule_ids) & set(overlay_rule_ids)
        if overlap:
            raise ControllerError(
                "persistent core and RAM overlay cannot contain the same policy IDs: "
                + ", ".join(sorted(overlap))
                + ". Move the policies out of the other layer first."
            )

    def _compile_layer_payload(
        self,
        rule_ids: tuple[str, ...],
        *,
        layer: str,
    ) -> str:
        by_id = {rule.id: rule for rule in self.store.rules}
        selected = [by_id[rule_id] for rule_id in rule_ids]
        max_bytes = MAX_COMPILED_BYTES if layer == "core" else MAX_OVERLAY_BYTES
        try:
            compilation = compile_rules(
                selected,
                self.catalog,
                max_bytes=max_bytes,
            )
            if layer == "overlay" and len(compilation.rules) > MAX_OVERLAY_ROWS:
                raise ControllerError(
                    f"RAM overlay has {len(compilation.rules):,} rows, but one "
                    f"controller accepts at most {MAX_OVERLAY_ROWS:,}."
                )
            return compilation.to_tsv()
        except ValueError as exc:
            original = str(exc).strip() or "policy compilation failed"
            if layer == "core":
                match = COMPILED_CAPACITY_RE.search(original)
                if match is not None:
                    needed = int(match.group(1).replace(",", ""))
                    limit = int(match.group(2).replace(",", ""))
                    raise ControllerError(
                        f"Persistent core needs {needed:,} bytes, but the NVRAM "
                        f"contract allows at most {limit:,}."
                    ) from exc
            raise ControllerError(
                f"{layer.capitalize()} cannot be compiled: {original}"
            ) from exc

    def _read_layered_status(
        self,
        status: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        current = status if status is not None else self.router.status()
        layers = _policy_layers(current)
        if layers is not None:
            return current, layers
        try:
            effective = self.router.effective_status()
        except (AttributeError, RouterError) as exc:
            raise ControllerError(
                "the installed router companion does not support hybrid core/RAM "
                "policy storage; legacy Apply policies remains available"
            ) from exc
        layers = _policy_layers(effective)
        if layers is None:
            raise ControllerError(
                "the router returned status without the hybrid policy fields"
            )
        return effective, layers

    def _require_layered_document(
        self,
        status: dict[str, Any],
    ) -> dict[str, Any]:
        layers = _policy_layers(status)
        if layers is None:
            raise ControllerError(
                "the router mutation succeeded without verifiable layered status"
            )
        return layers

    @staticmethod
    def _companion_version(
        status: dict[str, Any],
        layers: dict[str, Any],
    ) -> str:
        value = status.get("version", layers.get("version"))
        if not isinstance(value, str) or not value.strip():
            raise ControllerError("router status omitted the companion version")
        return value.strip()

    def _require_deployment(
        self,
        status: dict[str, Any],
        layers: dict[str, Any],
    ) -> PolicyDeploymentManifest:
        version = self._companion_version(status, layers)
        manifest = self.store.deployment_for(
            router_host=self.store.router_host,
            router_port=self.store.router_port,
            companion_version=version,
        )
        if manifest is None:
            raise ControllerError(
                "configure a version-bound hybrid deployment for this router first"
            )
        return manifest

    def _verified_host_fingerprint(
        self,
        host_key: WindowsHostKey | None = None,
        *,
        expected: str | None = None,
    ) -> str:
        if self.store.router_use_ssh_config:
            raise ControllerError(
                "hybrid deployment binding requires explicit router host, port, "
                "identity, and known_hosts settings"
            )
        current = host_key or self.inspect_router_host_key()
        if (
            current.host != self.store.router_host
            or current.port != self.store.router_port
            or current.known_hosts_path != self.known_hosts_path
        ):
            raise ControllerError(
                "the inspected SSH host key does not match the configured router"
            )
        if current.trust_state != "trusted":
            raise ControllerError(
                "the router SSH host key must be trusted before binding policy state"
            )
        if expected is not None and current.fingerprint != expected:
            raise ControllerError(
                "the router SSH host-key fingerprint differs from the saved "
                "deployment; no policy was written"
            )
        return current.fingerprint

    def _put_ram_overlay(
        self,
        rule_ids: tuple[str, ...] | None,
        *,
        source: str | None,
        require_saved_hash: bool,
        status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_companion_write("loading this computer's RAM overlay")
        current, layers = self._read_layered_status(status)
        manifest = self._require_deployment(current, layers)
        self._verified_host_fingerprint(
            expected=manifest.router_host_key_fingerprint,
        )
        if self._ensure_hybrid_helper() == "installed":
            current, layers = self._read_layered_status()
            manifest = self._require_deployment(current, layers)
        selected = manifest.overlay_rule_ids if rule_ids is None else rule_ids
        if not selected:
            raise ControllerError(
                "select at least one RAM overlay policy; use Remove RAM overlay "
                "to clear this controller's layer"
            )
        self._reject_layer_overlap(manifest.core_rule_ids, selected)
        payload = self._compile_layer_payload(selected, layer="overlay")
        expected_hash = _payload_hash(payload)
        if (
            require_saved_hash
            and manifest.overlay_hash is not None
            and expected_hash != manifest.overlay_hash.casefold()
        ):
            raise ControllerError(
                "the saved RAM overlay policies changed locally; explicitly load "
                "the new selection before enabling or retrying automatic restore"
            )
        requested_source = normalize_overlay_source(source or manifest.source)
        current_owner = _owner_overlay(layers, manifest.controller_id)
        result = self.router.overlay_put(
            manifest.controller_id,
            _layer_generation(current_owner),
            requested_source,
            payload,
        )
        result_layers = self._require_layered_document(result)
        applied_owner = _owner_overlay(result_layers, manifest.controller_id)
        if applied_owner is None:
            raise ControllerError(
                "router reported success without this computer's RAM overlay"
            )
        self._verify_applied_hash(
            expected_hash,
            _layer_hash(applied_owner),
            "RAM overlay",
        )
        manifest.overlay_rule_ids = selected
        manifest.overlay_hash = expected_hash
        manifest.overlay_generation = _layer_generation(applied_owner)
        manifest.source = requested_source
        manifest.resolved_source = _layer_source(applied_owner)
        manifest.source_mac = _layer_source_mac(applied_owner)
        manifest.last_runtime_epoch = _runtime_epoch_from_layers(result_layers)
        self.store.upsert_deployment(manifest)
        return result

    @staticmethod
    def _verify_applied_hash(
        expected: str,
        actual: str | None,
        label: str,
    ) -> None:
        if actual is None:
            raise ControllerError(f"router omitted the applied {label} hash")
        if actual != expected.casefold():
            raise ControllerError(
                f"router {label} readback hash differs from the uploaded document"
            )

    def _ensure_hybrid_helper(self) -> str:
        return RouterInstaller(self.router).ensure_hybrid_helper().action

    def _finish_policy_reconciliation(
        self,
        status: dict[str, Any],
    ) -> dict[str, Any]:
        """Perform at most one opted-in overlay restore for one runtime epoch."""

        layers = _policy_layers(status)
        if layers is None or summarize_policy_runtime(status).state != "ready":
            return status
        try:
            version = self._companion_version(status, layers)
        except ControllerError:
            return status
        manifest = self.store.deployment_for(
            router_host=self.store.router_host,
            router_port=self.store.router_port,
            companion_version=version,
        )
        if (
            manifest is None
            or not manifest.restore_overlay_after_reboot
            or not manifest.overlay_rule_ids
            or manifest.overlay_hash is None
        ):
            return status
        epoch = _runtime_epoch_from_layers(layers)
        if (
            epoch is None
            or epoch == manifest.last_runtime_epoch
            or epoch in self._overlay_restore_attempted_epochs
        ):
            return status

        owner_layer = _owner_overlay(layers, manifest.controller_id)
        if _layer_hash(owner_layer) == manifest.overlay_hash.casefold() and (
            manifest.resolved_source is None
            or _layer_source(owner_layer) == manifest.resolved_source
        ):
            manifest.overlay_generation = _layer_generation(owner_layer)
            manifest.resolved_source = _layer_source(owner_layer)
            manifest.source_mac = _layer_source_mac(owner_layer)
            manifest.last_runtime_epoch = epoch
            self.store.upsert_deployment(manifest)
            return status

        self._overlay_restore_attempted_epochs.add(epoch)
        try:
            restored = self._put_ram_overlay(
                None,
                source=None,
                require_saved_hash=True,
                status=status,
            )
        except (ControllerError, RouterError, ValueError) as exc:
            notice = (
                "The persistent core is healthy, but this computer's opted-in RAM "
                f"overlay could not be restored once for this router boot: {exc}. "
                "Use Restore RAM overlay now to retry."
            )
            self.recovery_notice = (
                f"{self.recovery_notice} {notice}" if self.recovery_notice else notice
            )
            return status
        notice = "This computer's RAM overlay was restored after the router reboot."
        self.recovery_notice = (
            f"{self.recovery_notice} {notice}" if self.recovery_notice else notice
        )
        return restored

    def install_companion(self) -> InstallResult:
        self._require_write("installing the router companion")
        result = RouterInstaller(self.router).install()
        self.store.companion_enabled = True
        self.store.save()
        return result

    def repair_companion(self) -> EnsureResult:
        self._require_companion_write("repairing the router companion")
        return RouterInstaller(self.router).ensure(allow_install=False)

    def restore_native(self) -> dict[str, Any]:
        self._require_companion_write("removing the router companion")
        status = RouterInstaller(self.router).uninstall()
        self.store.companion_enabled = False
        self.store.save()
        return status

    def refresh_domains(self) -> dict[str, Any]:
        self._require_companion_write("refreshing domain routes")
        return self.router.refresh()

    def rollback(self) -> dict[str, Any]:
        self._require_companion_write("rolling back router policies")
        return self.router.rollback()

    def set_connection(self, connected: bool) -> dict[str, Any]:
        self._require_write("changing the Astrill connection")
        return self.router.set_astrill_connection(
            connected,
            companion_enabled=self.store.companion_enabled,
        )

    def save_astrill_connection(
        self,
        selection: AstrillConnectionSelection,
        changes: dict[str, Any],
    ) -> NativeAstrillSettings:
        self._require_write("saving the Astrill connection")
        return self.router.save_astrill_connection(selection, changes)

    def apply_astrill_connection(
        self,
        selection: AstrillConnectionSelection,
        changes: dict[str, Any],
    ) -> AstrillConnectionResult:
        self._require_write("applying the Astrill connection")
        return self.router.apply_astrill_connection(
            selection,
            changes,
            companion_enabled=self.store.companion_enabled,
        )

    def apply_server_connection(
        self,
        server: AstrillServer,
        protocol: int,
        changes: dict[str, Any] | None = None,
    ) -> AstrillConnectionResult:
        if not isinstance(server, AstrillServer):
            raise TypeError("connection endpoint must be an Astrill server")
        selection = AstrillConnectionSelection.from_server(server, protocol, 0)
        return self.apply_astrill_connection(selection, changes or {})

    def switch_server(
        self,
        server: AstrillServer,
        protocol: int,
    ) -> dict[str, Any]:
        self._require_companion_write("switching the Astrill endpoint")
        sid, endpoint = server.endpoint_for(protocol)
        return self.router.switch_astrill(
            server_id=server.id,
            sid=sid,
            encoded_ip=endpoint.encoded_ip,
            port=endpoint.port,
            port_index=endpoint.port_index,
            protocol=protocol,
            vpn_mode=endpoint.vpn_mode_for(protocol),
        )

    def _require_write(self, action: str) -> None:
        if self.store.read_only:
            raise ControllerError(
                f"read-only access prevents {action}; enable router changes "
                "in Settings first"
            )

    def _require_companion_write(self, action: str) -> None:
        self._require_write(action)
        if not self.store.companion_enabled:
            raise ControllerError(
                f"the router companion must be installed before {action}"
            )

    def _router_client_from_store(self) -> RouterClient:
        if self.store.router_use_ssh_config:
            return RouterClient(
                self.store.router_host,
                host_key_policy="yes",
            )
        return RouterClient(
            self.store.router_host,
            user=self.store.router_user,
            port=self.store.router_port,
            identity_file=self.store.router_identity,
            host_key_policy="yes",
            known_hosts_file=self.known_hosts_path,
        )

    @property
    def known_hosts_path(self) -> Path:
        return self.store.path.with_name("known_hosts")
