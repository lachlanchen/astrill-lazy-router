from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .astrill import AstrillServer, group_by_region, parse_applet
from .catalog import Catalog, load_catalog
from .compiler import compile_rules
from .detector import MINIMUM_BYPASS_SERVICES
from .installer import EnsureResult, InstallResult, RouterInstaller
from .models import MatchKind, RouteTarget, Rule
from .native_settings import NativeAstrillSettings
from .router import RouterClient
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

    def reconcile_status(self) -> dict[str, Any]:
        """Resume safely after a router reboot or lost companion installation."""
        self.recovery_notice = None
        if not self.store.companion_enabled:
            return self.router.native_astrill_status()

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
            return self.router.native_astrill_status()

        installer = RouterInstaller(self.router)
        check = installer.check()
        if check.action == "none":
            if check.status is None:
                raise ControllerError(
                    "the router companion passed inspection without returning status"
                )
            return check.status
        if check.action == "repair":
            result = installer.ensure(allow_install=False)
            self.recovery_notice = (
                "The validated companion runtime was restored from router NVRAM "
                "after reboot."
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

    def save_native_settings(self, changes: dict[str, Any]) -> NativeAstrillSettings:
        self._require_write("saving native Astrill settings")
        return self.router.update_native_astrill_settings(changes)

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

    def apply_rules(self) -> dict[str, Any]:
        self._require_companion_write("applying router policies")
        compilation = compile_rules(self.store.rules, self.catalog)
        return self.router.apply_rules(compilation.to_tsv())

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
