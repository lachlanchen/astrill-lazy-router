from __future__ import annotations

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
from .router import AstrillConnectionResult, RouterClient
from .service_policy import ServiceRouteMode, service_policy_route
from .ssh_setup import identity_path
from .store import ConfigStore
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
    limit_bytes: int = MAX_COMPILED_BYTES
    warnings: tuple[str, ...] = ()
    error: str | None = None
    compilation: Compilation | None = field(default=None, repr=False, compare=False)

    @property
    def can_apply(self) -> bool:
        return self.error is None and self.compilation is not None

    @property
    def remaining_bytes(self) -> int | None:
        if self.compiled_bytes is None:
            return None
        return self.limit_bytes - self.compiled_bytes


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
            return check.status
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
            return result.status

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
                    error=f"Selected policy no longer exists: {names}.",
                )
            selected = [by_id[rule_id] for rule_id in requested]

        selected_ids = tuple(rule.id for rule in selected)
        compiled_rows = self._compiled_row_count(selected)
        try:
            compilation = compile_rules(selected, self.catalog)
        except ValueError as exc:
            original = str(exc).strip() or "Policy compilation failed."
            match = COMPILED_CAPACITY_RE.search(original)
            if match is not None:
                compiled_bytes = int(match.group(1).replace(",", ""))
                limit_bytes = int(match.group(2).replace(",", ""))
                error = (
                    f"Compiled policy needs {compiled_bytes:,} bytes, but this "
                    f"router accepts at most {limit_bytes:,}. Select a smaller "
                    "set in the policy table and use Apply selected; all other "
                    "policies will remain saved locally."
                )
            else:
                compiled_bytes = None
                limit_bytes = MAX_COMPILED_BYTES
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
        return PolicyCompilationSummary(
            rule_ids=selected_ids,
            rule_count=len(selected),
            enabled_count=sum(rule.enabled for rule in selected),
            compiled_rows=len(compilation.rules),
            compiled_bytes=len(payload.encode("ascii")),
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
