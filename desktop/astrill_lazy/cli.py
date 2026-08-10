from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent_package import build_portable_agent_package, plan_balanced_policy
from .astrill import parse_applet
from .autostart import (
    autostart_path,
    disable_autostart,
    enable_autostart,
    is_autostart_enabled,
)
from .catalog import Catalog, load_catalog
from .compiler import compile_rules
from .device_flow import (
    DeviceFlowSpec,
    put_device_flow,
    remove_device_flow,
    summarize_device_flow,
)
from .device_policy import (
    TrafficContext,
    compile_country_routes,
    decide_route,
    load_country_networks,
    load_device_policy,
)
from .host_key import inspect_host_key
from .installer import RouterInstaller
from .isolated_run import IsolatedRunError, run_isolated_command
from .policy_bundle import (
    PolicyBundleDownload,
    apply_policy_bundle,
    download_policy_bundle,
    export_service_policy_bundle,
    load_policy_bundle,
)
from .router import RouterClient, RouterError
from .ssh_setup import ensure_local_identity, read_public_key
from .store import ConfigStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astrill-lazy")
    parser.add_argument("--router", default=None, help="SSH host alias")
    parser.add_argument("--router-user", default=None, help="SSH user")
    parser.add_argument("--router-port", default=None, type=int, help="SSH port")
    parser.add_argument("--identity", default=None, help="SSH private key path")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("gui", help="open the native control application")
    subparsers.add_parser("status", help="show router and Astrill status")
    inspect = subparsers.add_parser(
        "inspect", help="inspect native Astrill without changing the router"
    )
    inspect.add_argument(
        "--full",
        action="store_true",
        help="include native website and device entries",
    )
    subparsers.add_parser("rules", help="show compiled rules on the router")
    subparsers.add_parser("apply", help="compile and apply the desktop rules")
    subparsers.add_parser("refresh", help="refresh domain addresses and routing")
    subparsers.add_parser("rollback", help="restore the previous router rules")
    subparsers.add_parser(
        "preflight-router",
        help="project a companion install without changing the router",
    )
    subparsers.add_parser("install-router", help="install or upgrade the router plugin")
    subparsers.add_parser("uninstall-router", help="remove the router plugin")
    subparsers.add_parser("setup-ssh", help="create the dedicated local SSH identity")
    autostart = subparsers.add_parser(
        "autostart", help="manage desktop-session autostart"
    )
    autostart.add_argument("action", choices=("enable", "disable", "status"))
    access = subparsers.add_parser(
        "access", help="show or change the local router write guard"
    )
    access.add_argument("action", choices=("status", "read-only", "read-write"))

    servers = subparsers.add_parser("servers", help="list Astrill server endpoints")
    servers.add_argument("--json", action="store_true")

    switch = subparsers.add_parser("switch", help="switch the active Astrill server")
    switch.add_argument("server_id", type=int)
    switch.add_argument("--protocol", type=int, choices=range(4), default=None)

    app_flow = subparsers.add_parser(
        "app-flow",
        help="manage transient process socket routes through the router companion",
    )
    app_flow_commands = app_flow.add_subparsers(dest="app_flow_command", required=True)
    app_flow_commands.add_parser("list", help="list transient socket routes")
    set_app_flow = app_flow_commands.add_parser("set", help="set a source-port route")
    set_app_flow.add_argument("flow_id")
    set_app_flow.add_argument("source")
    set_app_flow.add_argument("protocol", choices=("tcp", "udp"))
    set_app_flow.add_argument("source_ports")
    set_app_flow.add_argument("target", choices=("direct", "vpn"))
    delete_app_flow = app_flow_commands.add_parser(
        "delete", help="delete a source-port route"
    )
    delete_app_flow.add_argument("flow_id")

    device_flow = subparsers.add_parser(
        "device-flow",
        help="manage a volatile domain route for one external LAN device",
    )
    device_flow_commands = device_flow.add_subparsers(
        dest="device_flow_command", required=True
    )
    list_device_flows = device_flow_commands.add_parser(
        "list", help="list source-scoped RAM overlays"
    )
    list_device_flows.add_argument("--owner")
    set_device_flow = device_flow_commands.add_parser(
        "set", help="set an exact-device, exact-domain volatile route"
    )
    set_device_flow.add_argument("--owner", required=True)
    set_device_flow.add_argument("--source", required=True)
    set_device_flow.add_argument("--mac", required=True)
    set_device_flow.add_argument("--domain", action="append", required=True)
    set_device_flow.add_argument(
        "--protocol", action="append", choices=("tcp", "udp")
    )
    set_device_flow.add_argument("--port", type=int, default=443)
    set_device_flow.add_argument("--target", choices=("direct", "vpn"), default="vpn")
    remove_device_flow_parser = device_flow_commands.add_parser(
        "delete", help="remove one owned volatile device route"
    )
    remove_device_flow_parser.add_argument("--owner", required=True)

    isolated_run = subparsers.add_parser(
        "isolated-run",
        help="run one command through a disposable Astrill network identity",
    )
    isolated_run.add_argument("--profile", default="taskvpn")
    isolated_run.add_argument("--interface", default=None)
    isolated_run.add_argument(
        "--allow-domain",
        action="append",
        required=True,
        help="allow one destination domain; repeat for additional domains",
    )
    isolated_run.add_argument(
        "--allow-port",
        action="append",
        type=int,
        default=None,
        help="allow one destination TCP port; defaults to 443",
    )
    isolated_run.add_argument(
        "--dns-server",
        action="append",
        default=None,
        help=(
            "resolve and use one explicit IPv4 DNS server inside the namespace; "
            "repeat for up to three servers"
        ),
    )
    isolated_run.add_argument(
        "command_args",
        nargs=argparse.REMAINDER,
        metavar="-- COMMAND [ARG ...]",
    )

    device_policy = subparsers.add_parser(
        "device-policy",
        help="validate and inspect a device-local routing policy",
    )
    device_commands = device_policy.add_subparsers(dest="device_command", required=True)
    validate = device_commands.add_parser(
        "validate", help="validate a device policy without changing routes"
    )
    validate.add_argument("policy", type=Path)

    decide = device_commands.add_parser(
        "decide", help="evaluate one destination without changing routes"
    )
    decide.add_argument("policy", type=Path)
    decide.add_argument("--application", action="append", default=[])
    decide.add_argument("--service", action="append", default=[])
    decide.add_argument("--domain")
    decide.add_argument("--ip")
    decide.add_argument("--country")

    routes = device_commands.add_parser(
        "routes", help="compile country CIDRs without changing system routes"
    )
    routes.add_argument("policy", type=Path)
    routes.add_argument("country_networks", type=Path)

    policy_bundle = subparsers.add_parser(
        "policy-bundle",
        help="inspect, apply, or export a catalog-only policy bundle",
    )
    policy_bundle_commands = policy_bundle.add_subparsers(
        dest="policy_bundle_command",
        required=True,
    )
    inspect_bundle = policy_bundle_commands.add_parser(
        "inspect",
        help="validate a local file or HTTPS policy bundle without changing config",
    )
    inspect_bundle.add_argument("source")
    inspect_bundle.add_argument("--sha256")
    apply_bundle = policy_bundle_commands.add_parser(
        "apply",
        help="verify and apply a local file or HTTPS policy bundle",
    )
    apply_bundle.add_argument("source")
    apply_bundle.add_argument("--sha256", required=True)
    apply_bundle.add_argument(
        "--merge",
        action="store_true",
        help="retain service policies absent from the bundle",
    )
    export_bundle = policy_bundle_commands.add_parser(
        "export",
        help="export service decisions without devices, paths, or credentials",
    )
    export_bundle.add_argument("output", type=Path)
    export_bundle.add_argument("--bundle-id", default="daily-balanced")
    export_bundle.add_argument("--version", default="1.0.0")
    export_bundle.add_argument("--description", default="")

    agent = subparsers.add_parser(
        "agent",
        help="plan or build a source-bound portable restore agent",
    )
    agent_commands = agent.add_subparsers(dest="agent_command", required=True)
    agent_commands.add_parser(
        "plan",
        help="preview the balanced persistent-core/RAM-overlay split",
    )
    build_agent = agent_commands.add_parser(
        "build",
        help="build a token-free portable agent package without changing the router",
    )
    build_agent.add_argument("output", type=Path)
    build_agent.add_argument("--host", required=True)
    build_agent.add_argument("--user", default="root")
    build_agent.add_argument("--port", type=int, default=22)
    build_agent.add_argument("--identity-file", required=True)
    build_agent.add_argument("--host-fingerprint", required=True)
    build_agent.add_argument("--controller-id")
    build_agent.add_argument("--source", default="auto")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command in {None, "gui"}:
        if sys.platform == "win32":
            from .windows_app import run_application
        else:
            from .application import run_application

        return run_application()

    if arguments.command == "autostart":
        if arguments.action == "enable":
            enable_autostart()
        elif arguments.action == "disable":
            disable_autostart()
        _print_json(
            {
                "enabled": is_autostart_enabled(),
                "path": str(autostart_path()),
            }
        )
        return 0

    if arguments.command == "access":
        store = ConfigStore()
        if arguments.action == "read-only":
            store.read_only = True
            store.save()
        elif arguments.action == "read-write":
            store.read_only = False
            store.save()
        _print_json(
            {
                "access": "read-only" if store.read_only else "read-write",
                "companion_enabled": store.companion_enabled,
                "path": str(store.path),
            }
        )
        return 0

    if arguments.command == "setup-ssh":
        store = ConfigStore()
        configured_identity = arguments.identity or store.router_identity
        try:
            private_key = ensure_local_identity(configured_identity)
            public_key = read_public_key(configured_identity)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"astrill-lazy: {exc}", file=sys.stderr)
            return 1
        _print_json(
            {
                "identity": str(private_key),
                "public_key": public_key,
                "password_saved": False,
            }
        )
        return 0

    if arguments.command == "device-policy":
        try:
            policy = load_device_policy(arguments.policy)
            if arguments.device_command == "validate":
                _print_json(
                    {
                        "ok": True,
                        "schema_version": policy.schema_version,
                        "tunnels": len(policy.tunnels),
                        "enabled_tunnels": sum(item.enabled for item in policy.tunnels),
                        "country_groups": len(policy.country_groups),
                        "rules": len(policy.rules),
                        "enabled_rules": sum(item.enabled for item in policy.rules),
                        "enforcing": False,
                    }
                )
            elif arguments.device_command == "decide":
                service_ids = set(arguments.service)
                if arguments.domain:
                    service_ids.update(_services_for_domain(arguments.domain))
                context = TrafficContext(
                    application_ids=tuple(arguments.application),
                    service_ids=tuple(sorted(service_ids)),
                    domain=arguments.domain,
                    destination_ip=arguments.ip,
                    country_code=arguments.country,
                )
                decision = decide_route(policy, context)
                result = decision.to_dict()
                result["context"] = {
                    "application_ids": list(context.application_ids),
                    "service_ids": list(context.service_ids),
                    "domain": context.domain,
                    "destination_ip": context.destination_ip,
                    "country_code": context.country_code,
                }
                result["enforcing"] = False
                _print_json(result)
            elif arguments.device_command == "routes":
                plans = compile_country_routes(
                    policy, load_country_networks(arguments.country_networks)
                )
                _print_json(
                    {
                        "ok": True,
                        "enforcing": False,
                        "plans": [item.to_dict() for item in plans],
                    }
                )
        except (OSError, TypeError, ValueError) as exc:
            print(f"astrill-lazy: {exc}", file=sys.stderr)
            return 1
        return 0

    if arguments.command == "policy-bundle":
        store = ConfigStore()
        catalog = load_catalog(store.enabled_extensions)
        try:
            if arguments.policy_bundle_command == "export":
                bundle = export_service_policy_bundle(
                    store.rules,
                    bundle_id=arguments.bundle_id,
                    version=arguments.version,
                    description=arguments.description,
                )
                payload = bundle.to_bytes()
                arguments.output.parent.mkdir(parents=True, exist_ok=True)
                arguments.output.write_bytes(payload)
                _print_json(
                    {
                        "ok": True,
                        "path": str(arguments.output.resolve()),
                        "bundle_id": bundle.bundle_id,
                        "version": bundle.version,
                        "rules": len(bundle.entries),
                        "bytes": len(payload),
                        "sha256": bundle.sha256,
                        "sensitive_selectors_exported": False,
                    }
                )
                return 0
            download = _load_policy_bundle_source(
                arguments.source,
                catalog=catalog,
                expected_sha256=arguments.sha256,
            )
            if arguments.policy_bundle_command == "inspect":
                _print_json(
                    {
                        "ok": True,
                        "source": download.source,
                        "bundle_id": download.bundle.bundle_id,
                        "version": download.bundle.version,
                        "rules": len(download.bundle.entries),
                        "bytes": len(download.payload),
                        "sha256": download.sha256,
                        "mutated": False,
                    }
                )
                return 0
            result = apply_policy_bundle(
                store,
                catalog,
                download,
                replace_services=not arguments.merge,
            )
            _print_json(
                {
                    "ok": True,
                    "bundle_id": result.bundle_id,
                    "version": result.bundle_version,
                    "sha256": result.bundle_sha256,
                    "added": result.added,
                    "updated": result.updated,
                    "removed": result.removed,
                    "unchanged": result.unchanged,
                    "router_mutated": False,
                }
            )
            return 0
        except (OSError, TypeError, ValueError) as exc:
            print(f"astrill-lazy: {exc}", file=sys.stderr)
            return 1

    if arguments.command == "agent":
        store = ConfigStore()
        catalog = load_catalog(store.enabled_extensions)
        try:
            plan = plan_balanced_policy(store, catalog)
            if arguments.agent_command == "plan":
                _print_json(
                    {
                        "ok": True,
                        "core": {
                            "origins": len(plan.core_rule_ids),
                            "rows": len(plan.core_compilation.rules),
                            "bytes": plan.core_bytes,
                            "rule_ids": list(plan.core_rule_ids),
                        },
                        "overlay": {
                            "origins": len(plan.overlay_rule_ids),
                            "rows": len(plan.overlay_compilation.rules),
                            "bytes": plan.overlay_bytes,
                        },
                        "effective_rows": plan.effective_rows,
                        "undeployed": len(plan.undeployed_rule_ids),
                        "router_mutated": False,
                    }
                )
                return 0
            if not 1 <= arguments.port <= 65535:
                raise ValueError("agent router port must be between 1 and 65535")
            inspected = inspect_host_key(
                arguments.host,
                arguments.port,
                known_hosts_path=arguments.output.expanduser() / "known_hosts",
            )
            if inspected.fingerprint != arguments.host_fingerprint:
                raise ValueError(
                    "inspected router SSH fingerprint does not match --host-fingerprint"
                )
            router = RouterClient(
                arguments.host,
                user=arguments.user,
                port=arguments.port,
                identity_file=arguments.identity_file,
            )
            result = build_portable_agent_package(
                arguments.output,
                store=store,
                catalog=catalog,
                host_key=inspected,
                router_user=arguments.user,
                identity_file=arguments.identity_file,
                controller_id=arguments.controller_id,
                source=arguments.source,
                router_installer=RouterInstaller(router),
            )
            _print_json(
                {
                    "ok": True,
                    "path": str(result.path),
                    "controller_id": result.controller_id,
                    "core_origins": len(result.core_rule_ids),
                    "overlay_origins": len(result.overlay_rule_ids),
                    "overlay_rows": result.overlay_rows,
                    "overlay_bytes": result.overlay_bytes,
                    "overlay_md5": result.overlay_md5,
                    "overlay_sha256": result.overlay_sha256,
                    "package_sha256": result.package_sha256,
                    "router_mutated": False,
                    "contains_private_key": False,
                }
            )
            return 0
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            print(f"astrill-lazy: {exc}", file=sys.stderr)
            return 1

    store = ConfigStore()
    mutating_commands = {
        "apply",
        "refresh",
        "rollback",
        "install-router",
        "uninstall-router",
        "switch",
        "isolated-run",
    }
    mutating_app_flow = (
        arguments.command == "app-flow" and arguments.app_flow_command != "list"
    )
    mutating_device_flow = (
        arguments.command == "device-flow" and arguments.device_flow_command != "list"
    )
    if store.read_only and (
        arguments.command in mutating_commands
        or mutating_app_flow
        or mutating_device_flow
    ):
        print(
            "astrill-lazy: read-only access blocks this command; "
            "run `astrill-lazy access read-write` first",
            file=sys.stderr,
        )
        return 2
    host = arguments.router or store.router_host
    router_port = (
        arguments.router_port
        if arguments.router_port is not None
        else store.router_port
    )
    if not 1 <= router_port <= 65535:
        parser.error("--router-port must be between 1 and 65535")
    use_ssh_config = store.router_use_ssh_config and not any(
        (
            arguments.router,
            arguments.router_user,
            arguments.router_port,
            arguments.identity,
        )
    )
    if use_ssh_config:
        router = RouterClient(host)
    else:
        router = RouterClient(
            host,
            user=arguments.router_user or store.router_user,
            port=router_port,
            identity_file=arguments.identity or store.router_identity,
        )
    try:
        if arguments.command == "status":
            _print_json(
                router.status()
                if store.companion_enabled
                else router.native_astrill_status()
            )
        elif arguments.command == "inspect":
            _print_json(
                _native_inspection(
                    router,
                    host=host,
                    store=store,
                    full=arguments.full,
                )
            )
        elif arguments.command == "rules":
            if not store.companion_enabled:
                raise RouterError(
                    "compiled rules require the companion; use `astrill-lazy inspect` "
                    "for a native-only router"
                )
            print(router.rules(), end="")
        elif arguments.command == "apply":
            compilation = compile_rules(store.rules, load_catalog())
            result = router.apply_rules(compilation.to_tsv())
            result["warnings"] = list(compilation.warnings)
            _print_json(result)
        elif arguments.command == "refresh":
            _print_json(router.refresh())
        elif arguments.command == "rollback":
            _print_json(router.rollback())
        elif arguments.command == "preflight-router":
            result = RouterInstaller(router).preflight_install()
            _print_json(
                {
                    "version": result.version,
                    "installed_version": result.installed_version,
                    "package_bytes": result.package_bytes,
                    "package_sha256": result.package_sha256,
                    "nvram_chunks": result.nvram_chunks,
                    "nvram_free_before": result.nvram_free_before,
                    "projected_growth": result.projected_growth,
                    "projected_free": result.projected_free,
                    "minimum_free": result.minimum_free,
                    "can_install": result.can_install,
                    "mutated": False,
                }
            )
        elif arguments.command == "app-flow":
            if not store.companion_enabled:
                raise RouterError(
                    "application flow routes require the router companion"
                )
            if arguments.app_flow_command == "list":
                _print_json(router.app_flows())
            elif arguments.app_flow_command == "set":
                _print_json(
                    router.set_app_flow(
                        arguments.flow_id,
                        arguments.source,
                        arguments.protocol,
                        arguments.source_ports,
                        arguments.target,
                    )
                )
            elif arguments.app_flow_command == "delete":
                _print_json(router.delete_app_flow(arguments.flow_id))
        elif arguments.command == "device-flow":
            if not store.companion_enabled:
                raise RouterError("device-flow routes require the router companion")
            if arguments.device_flow_command == "list":
                status = router.effective_status()
                if arguments.owner:
                    overlays = [
                        summarize_device_flow(status, arguments.owner.strip().casefold())
                    ]
                    overlays = [item for item in overlays if item is not None]
                else:
                    overlays = [
                        summarize_device_flow(status, str(item.get("owner", "")))
                        for item in status.get("overlays", [])
                        if isinstance(item, dict)
                    ]
                    overlays = [item for item in overlays if item is not None]
                _print_json({"ok": True, "temporary": True, "overlays": overlays})
            elif arguments.device_flow_command == "set":
                spec = DeviceFlowSpec.create(
                    owner=arguments.owner,
                    source=arguments.source,
                    mac=arguments.mac,
                    domains=arguments.domain,
                    target=arguments.target,
                    protocols=arguments.protocol,
                    port=arguments.port,
                )
                status = put_device_flow(router, spec, load_catalog())
                _print_json(
                    {
                        "ok": True,
                        "temporary": True,
                        "domains": list(spec.domains),
                        "protocols": [item.value for item in spec.protocols],
                        "port": spec.port,
                        "target": spec.target.value,
                        "overlay": summarize_device_flow(status, spec.owner),
                    }
                )
            elif arguments.device_flow_command == "delete":
                removed, status = remove_device_flow(router, arguments.owner)
                _print_json(
                    {
                        "ok": True,
                        "temporary": True,
                        "owner": arguments.owner.strip().casefold(),
                        "removed": removed,
                        "remaining": summarize_device_flow(
                            status, arguments.owner.strip().casefold()
                        ),
                    }
                )
        elif arguments.command == "isolated-run":
            if not store.companion_enabled:
                raise RouterError("isolated-run requires the router companion")
            return run_isolated_command(
                router,
                arguments.command_args,
                profile=arguments.profile,
                parent_interface=arguments.interface,
                allowed_domains=arguments.allow_domain,
                allowed_ports=arguments.allow_port or (443,),
                dns_servers=arguments.dns_server or (),
            )
        elif arguments.command == "install-router":
            result = RouterInstaller(router).install()
            store.companion_enabled = True
            store.save()
            _print_json(
                {
                    "version": result.version,
                    "package_bytes": result.package_bytes,
                    "package_sha256": result.package_sha256,
                    "nvram_chunks": result.nvram_chunks,
                    "policy_page": result.policy_page,
                    "api_page": result.api_page,
                    "status": result.status,
                }
            )
        elif arguments.command == "uninstall-router":
            status = RouterInstaller(router).uninstall()
            store.companion_enabled = False
            store.save()
            _print_json({"ok": True, "uninstalled": True, "status": status})
        elif arguments.command == "servers":
            servers = parse_applet(router.fetch_astrill_payload())
            if arguments.json:
                _print_json(
                    [{"id": server.id, "name": server.name} for server in servers]
                )
            else:
                for server in servers:
                    print(f"{server.id:4d}  {server.name}")
        elif arguments.command == "switch":
            status = router.status()
            protocol = (
                arguments.protocol
                if arguments.protocol is not None
                else int(status.get("astrill_protocol", 0))
            )
            servers = parse_applet(router.fetch_astrill_payload())
            server = next(
                (item for item in servers if item.id == arguments.server_id), None
            )
            if server is None:
                parser.error(f"Astrill server {arguments.server_id} was not found")
            sid, endpoint = server.endpoint_for(protocol)
            result = router.switch_astrill(
                server_id=server.id,
                sid=sid,
                encoded_ip=endpoint.encoded_ip,
                port=endpoint.port,
                port_index=endpoint.port_index,
                protocol=protocol,
                vpn_mode=endpoint.vpn_mode_for(protocol),
            )
            _print_json(result)
    except (IsolatedRunError, RouterError, OSError, ValueError) as exc:
        print(f"astrill-lazy: {exc}", file=sys.stderr)
        return 1
    return 0


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _native_inspection(
    router: RouterClient,
    *,
    host: str,
    store: ConfigStore,
    full: bool,
) -> dict[str, object]:
    status = router.native_astrill_status()
    settings = router.native_astrill_settings()
    clients = router.native_clients()
    companion = router.companion_presence()
    servers = parse_applet(router.fetch_astrill_payload())
    site_entries = [
        line.strip()
        for line in settings.get("astrill_iplistraw").splitlines()
        if line.strip()
    ]
    devices = settings.devices
    result: dict[str, object] = {
        "ok": True,
        "router": host,
        "access": "read-only" if store.read_only else "read-write",
        "configured_mode": ("companion" if store.companion_enabled else "native-only"),
        "companion": companion,
        "native_astrill": {
            "health": status.get("health"),
            "vpn_state": status.get("vpn_state"),
            "server_id": status.get("astrill_server_id"),
            "protocol": status.get("astrill_protocol"),
            "autostart": settings.enabled("astrill_autostart"),
            "website_policy": {
                "mode": settings.integer("astrill_routingmode"),
                "default": settings.site_policy.default.value,
                "listed_route": settings.site_policy.exception.value,
                "entries": len(site_entries),
                "compiled_ipv4": len(settings.get("astrill_iplist").split()),
            },
            "device_policy": {
                "mode": settings.integer("astrill_devmode"),
                "default": settings.device_policy.default.value,
                "listed_route": settings.device_policy.exception.value,
                "entries": len(devices),
            },
            "wifi_mode": settings.integer("astrill_ifmode"),
            "vlan_mode": settings.integer("astrill_vlanmode"),
            "dns_mode": settings.get("astrill_dnsserver"),
        },
        "discovered_lan_clients": len(clients),
        "astrill_endpoints": len(servers),
    }
    if full:
        result["native_entries"] = {
            "websites": site_entries,
            "devices": [
                {
                    "mac": device.mac,
                    "address": device.address,
                    "name": device.name,
                }
                for device in devices
            ],
            "lan_clients": clients,
        }
    return result


def _services_for_domain(domain: str) -> set[str]:
    normalized = domain.rstrip(".").casefold()
    matches: set[str] = set()
    for service in load_catalog().services:
        if any(
            normalized == seed or normalized.endswith(f".{seed}")
            for seed in service.domains
        ):
            matches.add(service.id)
    return matches


def _load_policy_bundle_source(
    source: str,
    *,
    catalog: Catalog,
    expected_sha256: str | None,
) -> PolicyBundleDownload:
    if source.startswith(("http://", "https://")):
        return download_policy_bundle(
            source,
            catalog=catalog,
            expected_sha256=expected_sha256,
        )
    return load_policy_bundle(
        Path(source).expanduser(),
        catalog=catalog,
        expected_sha256=expected_sha256,
    )


if __name__ == "__main__":
    raise SystemExit(main())
