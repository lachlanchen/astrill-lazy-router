from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .astrill import parse_applet
from .autostart import (
    autostart_path,
    disable_autostart,
    enable_autostart,
    is_autostart_enabled,
)
from .catalog import load_catalog
from .compiler import compile_rules
from .device_policy import (
    TrafficContext,
    compile_country_routes,
    decide_route,
    load_country_networks,
    load_device_policy,
)
from .installer import RouterInstaller
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command in {None, "gui"}:
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

    store = ConfigStore()
    mutating_commands = {
        "apply",
        "refresh",
        "rollback",
        "install-router",
        "uninstall-router",
        "switch",
    }
    if store.read_only and arguments.command in mutating_commands:
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
    except (RouterError, OSError, ValueError) as exc:
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


if __name__ == "__main__":
    raise SystemExit(main())
