from __future__ import annotations

import argparse
import json
import sys

from .astrill import parse_applet
from .catalog import load_catalog
from .compiler import compile_rules
from .installer import RouterInstaller
from .router import RouterClient, RouterError
from .store import ConfigStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astrill-lazy")
    parser.add_argument("--router", default=None, help="SSH host alias")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("gui", help="open the native control application")
    subparsers.add_parser("status", help="show router and Astrill status")
    subparsers.add_parser("rules", help="show compiled rules on the router")
    subparsers.add_parser("apply", help="compile and apply the desktop rules")
    subparsers.add_parser("refresh", help="refresh domain addresses and routing")
    subparsers.add_parser("rollback", help="restore the previous router rules")
    subparsers.add_parser("install-router", help="install or upgrade the router plugin")
    subparsers.add_parser("uninstall-router", help="remove the router plugin")

    servers = subparsers.add_parser("servers", help="list Astrill locations")
    servers.add_argument("--json", action="store_true")

    switch = subparsers.add_parser("switch", help="switch the active Astrill server")
    switch.add_argument("server_id", type=int)
    switch.add_argument("--protocol", type=int, choices=range(4), default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command in {None, "gui"}:
        from .application import run_application

        return run_application()

    store = ConfigStore()
    host = arguments.router or store.router_host
    router = RouterClient(host)
    try:
        if arguments.command == "status":
            _print_json(router.status())
        elif arguments.command == "rules":
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
            RouterInstaller(router).uninstall()
            _print_json({"ok": True, "uninstalled": True})
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


if __name__ == "__main__":
    raise SystemExit(main())
