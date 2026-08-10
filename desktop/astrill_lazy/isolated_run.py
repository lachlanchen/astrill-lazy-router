from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from .models import DOMAIN_RE, RouteTarget
from .native_settings import NativeAstrillSettings
from .router import RouterError

PROFILE_RE = re.compile(r"^[a-z0-9]{1,10}$")
DEFAULT_HELPER = Path("/usr/local/libexec/astrill-lazy-netns")
SOURCE_PORTS = "1024:65535"
MAX_ALLOWED_DOMAINS = 16
MAX_ALLOWED_PORTS = 8
MAX_ALLOWED_ADDRESSES = 64
MAX_DNS_SERVERS = 3
CLEANUP_RETRY_ATTEMPTS = 6
CLEANUP_RETRY_DELAY_SECONDS = 2.0
DIG = Path("/usr/bin/dig")


class IsolatedRunError(RuntimeError):
    pass


class IsolatedRouter(Protocol):
    def status(self) -> dict[str, object]: ...

    def app_flows(self) -> dict[str, object]: ...

    def native_astrill_settings(self) -> NativeAstrillSettings: ...

    def set_app_flow(
        self,
        flow_id: str,
        source: str,
        protocol: str,
        source_ports: str,
        target: str,
    ) -> dict[str, object]: ...

    def delete_app_flow(self, flow_id: str) -> dict[str, object]: ...

    def set_astrill_connection(
        self, connected: bool, *, companion_enabled: bool
    ) -> dict[str, object]: ...


def run_isolated_command(
    router: IsolatedRouter,
    command: Sequence[str],
    *,
    profile: str = "taskvpn",
    parent_interface: str | None = None,
    helper: Path = DEFAULT_HELPER,
    allowed_domains: Sequence[str] = (),
    allowed_ports: Sequence[int] = (443,),
    dns_servers: Sequence[str] = (),
) -> int:
    """Run one destination-limited TCP command behind Astrill.

    The disposable namespace receives the VPN route while the ordinary host is
    explicitly pinned to Direct. Namespace egress is limited to the resolved
    IPv4 addresses and TCP ports supplied by the caller.
    """

    normalized_profile = profile.strip()
    if not PROFILE_RE.fullmatch(normalized_profile):
        raise IsolatedRunError(
            "profile must contain one to ten lowercase letters or digits"
        )
    if os.name != "posix" or not sys.platform.startswith("linux"):
        raise IsolatedRunError("isolated-run requires Linux network namespaces")
    if os.getuid() < 1000:
        raise IsolatedRunError("isolated-run must be started by a desktop user")
    if (
        not helper.is_absolute()
        or not helper.is_file()
        or not os.access(helper, os.X_OK)
    ):
        raise IsolatedRunError(f"network namespace helper is unavailable: {helper}")

    executable, arguments = _normalize_command(command)
    interface = parent_interface or _default_interface()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface):
        raise IsolatedRunError("parent interface is invalid")
    host_address, host_mac = _interface_identity(interface)
    normalized_dns_servers = _normalize_dns_servers(dns_servers)
    pinned_hosts: tuple[tuple[str, tuple[str, ...]], ...] = ()
    if normalized_dns_servers:
        normalized_domains, pinned_hosts = _resolve_allowed_domain_map(
            allowed_domains,
            dns_servers=normalized_dns_servers,
        )
        allowed_addresses = _flatten_domain_addresses(pinned_hosts)
    else:
        normalized_domains, allowed_addresses = _resolve_allowed_domains(
            allowed_domains
        )
    normalized_ports = _normalize_allowed_ports(allowed_ports)

    host_flow_ids = [
        f"isolated-{normalized_profile}-host-tcp",
        f"isolated-{normalized_profile}-host-udp",
    ]
    task_flow_id = f"isolated-{normalized_profile}-tcp"

    with _task_lock():
        _verify_native_host_is_direct(router, host_address, host_mac)
        initial_status = router.status()
        initially_connected = initial_status.get("vpn_state") == "up"
        namespace_prepared = False
        connection_attempted = False
        installed_flows: list[str] = []
        cleanup_errors: list[str] = []
        command_returncode: int | None = None
        try:
            namespace_prepared = True
            namespace = _prepare_namespace(
                helper,
                normalized_profile,
                interface,
                normalized_dns_servers,
            )
            source = namespace["address"]
            if pinned_hosts:
                _pin_namespace_hosts(helper, normalized_profile, pinned_hosts)

            installed_flows.append(host_flow_ids[0])
            _set_flow_verified(
                router, host_flow_ids[0], host_address, "tcp", target="direct"
            )
            installed_flows.append(host_flow_ids[1])
            _set_flow_verified(
                router, host_flow_ids[1], host_address, "udp", target="direct"
            )
            installed_flows.append(task_flow_id)
            _set_flow_verified(router, task_flow_id, source, "tcp", target="vpn")
            _restrict_namespace(
                helper,
                normalized_profile,
                allowed_addresses,
                normalized_ports,
            )

            if not initially_connected:
                connection_attempted = True
                _set_connection_verified(router, True)

            print(
                "astrill-lazy: isolated VPN active for profile "
                f"{normalized_profile}; allowed domains: "
                + ", ".join(normalized_domains),
                file=sys.stderr,
            )
            completed = subprocess.run(
                [
                    "/usr/bin/sudo",
                    "-n",
                    str(helper),
                    "execute",
                    normalized_profile,
                    str(os.getuid()),
                    str(Path.cwd()),
                    executable,
                    *arguments,
                ],
                check=False,
            )
            command_returncode = completed.returncode
        finally:
            active_exception = sys.exc_info()[0] is not None
            if namespace_prepared:
                try:
                    _run_helper(helper, "cleanup", normalized_profile)
                except IsolatedRunError as exc:
                    cleanup_errors.append(str(exc))

            if connection_attempted:
                cleanup_error = _retry_cleanup(
                    lambda: _disconnect_after_attempt_verified(router),
                    "Astrill disconnect",
                )
                if cleanup_error is not None:
                    cleanup_errors.append(cleanup_error)

            for flow_id in reversed(installed_flows):
                cleanup_error = _retry_cleanup(
                    lambda flow_id=flow_id: _delete_flow_verified(router, flow_id),
                    f"flow cleanup {flow_id}",
                )
                if cleanup_error is not None:
                    cleanup_errors.append(cleanup_error)

            if cleanup_errors:
                cleanup_message = "; ".join(cleanup_errors)
                print(
                    "astrill-lazy: isolated-run cleanup requires attention: "
                    + cleanup_message,
                    file=sys.stderr,
                )
                if not active_exception:
                    raise IsolatedRunError(cleanup_message)
            else:
                print(
                    f"astrill-lazy: isolated VPN cleaned for profile "
                    f"{normalized_profile}",
                    file=sys.stderr,
                )
        if command_returncode is None:
            raise IsolatedRunError("isolated command did not start")
        return command_returncode


def _normalize_command(command: Sequence[str]) -> tuple[str, list[str]]:
    values = list(command)
    if values and values[0] == "--":
        values.pop(0)
    if not values or not values[0]:
        raise IsolatedRunError("isolated-run requires a command after --")
    if any("\x00" in value for value in values):
        raise IsolatedRunError("isolated-run command contains a NUL byte")
    requested = values[0]
    if "/" in requested:
        path = Path(requested).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        executable = str(path)
    else:
        resolved = shutil.which(requested)
        if resolved is None:
            raise IsolatedRunError(f"command was not found: {requested}")
        executable = resolved
    if not Path(executable).is_file() or not os.access(executable, os.X_OK):
        raise IsolatedRunError(f"command is not executable: {executable}")
    return executable, values[1:]


def _default_interface() -> str:
    completed = subprocess.run(
        ["/usr/sbin/ip", "-json", "route", "show", "default"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise IsolatedRunError("could not inspect the default network interface")
    try:
        routes = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise IsolatedRunError("default network route output is invalid") from exc
    interfaces = [
        route.get("dev")
        for route in routes
        if isinstance(route, dict) and isinstance(route.get("dev"), str)
    ]
    if len(set(interfaces)) != 1:
        raise IsolatedRunError(
            "default network interface is ambiguous; pass --interface"
        )
    return interfaces[0]


def _interface_identity(interface: str) -> tuple[str, str]:
    address_completed = subprocess.run(
        ["/usr/sbin/ip", "-json", "-4", "address", "show", "dev", interface],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if address_completed.returncode != 0:
        raise IsolatedRunError("could not inspect the parent interface IPv4 identity")
    try:
        address_records = json.loads(address_completed.stdout)
    except json.JSONDecodeError as exc:
        raise IsolatedRunError("parent interface IPv4 output is invalid") from exc
    addresses = {
        str(item.get("local"))
        for record in address_records
        if isinstance(record, dict)
        for item in record.get("addr_info", [])
        if isinstance(item, dict)
        and item.get("family") == "inet"
        and item.get("scope") == "global"
        and isinstance(item.get("local"), str)
    }

    link_completed = subprocess.run(
        ["/usr/sbin/ip", "-json", "link", "show", "dev", interface],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if link_completed.returncode != 0:
        raise IsolatedRunError("could not inspect the parent interface Ethernet identity")
    try:
        link_records = json.loads(link_completed.stdout)
    except json.JSONDecodeError as exc:
        raise IsolatedRunError("parent interface link output is invalid") from exc
    macs = {
        str(record.get("address")).casefold()
        for record in link_records
        if isinstance(record, dict)
        and isinstance(record.get("address"), str)
        and re.fullmatch(
            r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}",
            str(record.get("address")),
        )
    }
    if len(addresses) != 1 or len(macs) != 1:
        raise IsolatedRunError(
            "parent interface must have one global IPv4 and one Ethernet identity"
        )
    return next(iter(addresses)), next(iter(macs))


def _resolve_allowed_domains(
    domains: Sequence[str],
    *,
    dns_servers: Sequence[str] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    normalized, domain_addresses = _resolve_allowed_domain_map(
        domains,
        dns_servers=dns_servers,
    )
    return normalized, _flatten_domain_addresses(domain_addresses)


def _resolve_allowed_domain_map(
    domains: Sequence[str],
    *,
    dns_servers: Sequence[str] = (),
) -> tuple[tuple[str, ...], tuple[tuple[str, tuple[str, ...]], ...]]:
    normalized: list[str] = []
    for value in domains:
        candidate = value.strip().rstrip(".")
        try:
            candidate = candidate.encode("idna").decode("ascii").casefold()
        except UnicodeError as exc:
            raise IsolatedRunError(f"allowed domain is invalid: {value!r}") from exc
        if not DOMAIN_RE.fullmatch(candidate):
            raise IsolatedRunError(f"allowed domain is invalid: {value!r}")
        if candidate not in normalized:
            normalized.append(candidate)
    if not normalized:
        raise IsolatedRunError("isolated-run requires at least one --allow-domain")
    if len(normalized) > MAX_ALLOWED_DOMAINS:
        raise IsolatedRunError(
            f"isolated-run supports at most {MAX_ALLOWED_DOMAINS} allowed domains"
        )

    normalized_dns_servers = _normalize_dns_servers(dns_servers)
    resolved: list[tuple[str, tuple[str, ...]]] = []
    for domain in normalized:
        if normalized_dns_servers:
            domain_addresses = _resolve_domain_with_dns(
                domain,
                normalized_dns_servers,
            )
        else:
            try:
                records = socket.getaddrinfo(
                    domain,
                    443,
                    family=socket.AF_INET,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                )
            except socket.gaierror as exc:
                raise IsolatedRunError(
                    f"could not resolve allowed domain before routing: {domain}"
                ) from exc
            domain_addresses = {
                str(ipaddress.ip_address(record[4][0]))
                for record in records
                if record[0] == socket.AF_INET
            }
        if not domain_addresses:
            raise IsolatedRunError(f"allowed domain has no IPv4 address: {domain}")
        resolved.append(
            (
                domain,
                tuple(sorted(domain_addresses, key=ipaddress.ip_address)),
            )
        )
    return tuple(normalized), tuple(resolved)


def _flatten_domain_addresses(
    domain_addresses: Sequence[tuple[str, Sequence[str]]],
) -> tuple[str, ...]:
    addresses = {
        address
        for _domain, resolved_addresses in domain_addresses
        for address in resolved_addresses
    }
    if len(addresses) > MAX_ALLOWED_ADDRESSES:
        raise IsolatedRunError(
            f"isolated-run supports at most {MAX_ALLOWED_ADDRESSES} allowed addresses"
        )
    return tuple(sorted(addresses, key=ipaddress.ip_address))


def _normalize_dns_servers(servers: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in servers:
        try:
            address = ipaddress.ip_address(value.strip())
        except ValueError as exc:
            raise IsolatedRunError(f"DNS server is not a valid IPv4 address: {value!r}") from exc
        if (
            address.version != 4
            or address.is_unspecified
            or address.is_multicast
            or address.is_loopback
            or address.is_link_local
        ):
            raise IsolatedRunError(f"DNS server is not a usable IPv4 address: {value!r}")
        rendered = str(address)
        if rendered not in normalized:
            normalized.append(rendered)
    if len(normalized) > MAX_DNS_SERVERS:
        raise IsolatedRunError(
            f"isolated-run supports at most {MAX_DNS_SERVERS} DNS servers"
        )
    return tuple(normalized)


def _resolve_domain_with_dns(
    domain: str,
    dns_servers: Sequence[str],
) -> set[str]:
    if not DIG.is_file() or not os.access(DIG, os.X_OK):
        raise IsolatedRunError("explicit DNS resolution requires /usr/bin/dig")
    addresses: set[str] = set()
    for server in dns_servers:
        try:
            completed = subprocess.run(
                [
                    str(DIG),
                    "+time=3",
                    "+tries=1",
                    "+short",
                    "A",
                    domain,
                    f"@{server}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            )
        except subprocess.TimeoutExpired:
            continue
        if completed.returncode != 0:
            continue
        for line in completed.stdout.splitlines():
            try:
                address = ipaddress.ip_address(line.strip())
            except ValueError:
                continue
            if address.version == 4:
                addresses.add(str(address))
    if not addresses:
        raise IsolatedRunError(
            f"could not resolve allowed domain through explicit DNS: {domain}"
        )
    return addresses


def _normalize_allowed_ports(ports: Sequence[int]) -> tuple[int, ...]:
    normalized = tuple(sorted(set(ports)))
    if not normalized:
        raise IsolatedRunError("isolated-run requires at least one allowed TCP port")
    if len(normalized) > MAX_ALLOWED_PORTS:
        raise IsolatedRunError(
            f"isolated-run supports at most {MAX_ALLOWED_PORTS} allowed TCP ports"
        )
    if any(not isinstance(port, int) or not 1 <= port <= 65535 for port in normalized):
        raise IsolatedRunError("allowed TCP ports must be between 1 and 65535")
    return normalized


def _verify_native_host_is_direct(
    router: IsolatedRouter, host_address: str, host_mac: str
) -> None:
    settings = router.native_astrill_settings()
    policy = settings.device_policy
    if policy.default is not RouteTarget.DIRECT:
        raise IsolatedRunError(
            "native Astrill device policy is not Direct by default; refusing to "
            "start a shared tunnel"
        )
    host_is_exception = any(
        device.address == host_address or device.mac.casefold() == host_mac
        for device in settings.devices
    )
    if host_is_exception and policy.exception is RouteTarget.VPN:
        raise IsolatedRunError(
            "this host is selected by Astrill's native VPN device list; remove it "
            "before isolated-run so Codex remains Direct"
        )


def _prepare_namespace(
    helper: Path,
    profile: str,
    interface: str,
    dns_servers: Sequence[str] = (),
) -> dict[str, str]:
    completed = _run_helper(
        helper,
        "prepare",
        profile,
        interface,
        profile_dns=" ".join(dns_servers),
    )
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise IsolatedRunError("namespace helper returned invalid JSON") from exc
    if (
        document.get("profile") != profile
        or document.get("namespace") != f"al-{profile}"
    ):
        raise IsolatedRunError("namespace helper returned the wrong profile identity")
    try:
        address = ipaddress.ip_address(document.get("address", ""))
    except ValueError as exc:
        raise IsolatedRunError("namespace helper returned an invalid address") from exc
    if address.version != 4 or address.is_unspecified or address.is_multicast:
        raise IsolatedRunError("namespace helper returned an unsafe address")
    return {
        "profile": profile,
        "namespace": f"al-{profile}",
        "address": str(address),
    }


def _restrict_namespace(
    helper: Path,
    profile: str,
    addresses: Sequence[str],
    ports: Sequence[int],
) -> None:
    _run_helper(
        helper,
        "restrict",
        profile,
        ",".join(str(port) for port in ports),
        *addresses,
    )


def _pin_namespace_hosts(
    helper: Path,
    profile: str,
    domain_addresses: Sequence[tuple[str, Sequence[str]]],
) -> None:
    arguments: list[str] = []
    for domain, addresses in domain_addresses:
        for address in addresses:
            arguments.extend((domain, address))
    _run_helper(helper, "pin-hosts", profile, *arguments)


def _run_helper(
    helper: Path,
    *arguments: str,
    profile_dns: str = "",
) -> subprocess.CompletedProcess[str]:
    command = ["/usr/bin/sudo", "-n"]
    environment = None
    if profile_dns:
        command.append("--preserve-env=ASTRILL_LAZY_PROFILE_DNS")
        environment = os.environ.copy()
        environment["ASTRILL_LAZY_PROFILE_DNS"] = profile_dns
    command.extend((str(helper), *arguments))
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=35,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise IsolatedRunError("network namespace helper timed out") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "unknown helper failure"
        raise IsolatedRunError(detail)
    return completed


def _set_flow_verified(
    router: IsolatedRouter,
    flow_id: str,
    source: str,
    protocol: str,
    *,
    target: str,
) -> None:
    try:
        router.set_app_flow(flow_id, source, protocol, SOURCE_PORTS, target)
    except RouterError:
        if not _flow_matches(router, flow_id, source, protocol, target):
            raise
    if not _flow_matches(router, flow_id, source, protocol, target):
        raise IsolatedRunError(f"router did not activate isolated flow {flow_id}")


def _delete_flow_verified(router: IsolatedRouter, flow_id: str) -> None:
    try:
        router.delete_app_flow(flow_id)
    except RouterError:
        if _flow_exists(router, flow_id):
            raise
    if _flow_exists(router, flow_id):
        raise IsolatedRunError(f"router did not remove isolated flow {flow_id}")


def _flow_matches(
    router: IsolatedRouter,
    flow_id: str,
    source: str,
    protocol: str,
    target: str,
) -> bool:
    for flow in _flows(router):
        if flow.get("id") != flow_id:
            continue
        return flow == {
            "id": flow_id,
            "source": source,
            "protocol": protocol,
            "source_ports": SOURCE_PORTS,
            "target": target,
        }
    return False


def _flow_exists(router: IsolatedRouter, flow_id: str) -> bool:
    return any(flow.get("id") == flow_id for flow in _flows(router))


def _flows(router: IsolatedRouter) -> list[dict[str, object]]:
    document = router.app_flows()
    flows = document.get("flows")
    if not isinstance(flows, list) or not all(isinstance(item, dict) for item in flows):
        raise IsolatedRunError("router returned invalid application flow state")
    return flows


def _set_connection_verified(router: IsolatedRouter, connected: bool) -> None:
    expected = "up" if connected else "down"
    try:
        result = router.set_astrill_connection(connected, companion_enabled=True)
    except RouterError:
        result = router.status()
        if result.get("vpn_state") != expected:
            raise
    if result.get("vpn_state") != expected:
        observed = router.status()
        if observed.get("vpn_state") != expected:
            raise IsolatedRunError(
                f"Astrill connection did not reach the {expected} state"
            )


def _disconnect_after_attempt_verified(router: IsolatedRouter) -> None:
    """Acquire the controller after a possibly late connect, then force it down."""

    result = router.set_astrill_connection(False, companion_enabled=True)
    if result.get("vpn_state") != "down":
        observed = router.status()
        if observed.get("vpn_state") != "down":
            raise IsolatedRunError("Astrill cleanup did not reach the down state")


def _retry_cleanup(
    action: Callable[[], None],
    label: str,
) -> str | None:
    last_error: RouterError | IsolatedRunError | None = None
    for attempt in range(CLEANUP_RETRY_ATTEMPTS):
        try:
            action()
            return None
        except (RouterError, IsolatedRunError) as exc:
            last_error = exc
            if attempt + 1 < CLEANUP_RETRY_ATTEMPTS:
                time.sleep(CLEANUP_RETRY_DELAY_SECONDS)
    return f"{label}: {last_error}"


@contextmanager
def _task_lock() -> Iterator[None]:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    if not runtime.is_dir():
        runtime = Path("/tmp")
    lock_path = runtime / f"astrill-lazy-isolated-run-{os.getuid()}.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise IsolatedRunError("another isolated-run command is active") from exc
        yield
    finally:
        os.close(descriptor)
