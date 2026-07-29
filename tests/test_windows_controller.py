from __future__ import annotations

from pathlib import Path
from typing import Any

import astrill_lazy.windows_controller as controller_module
import pytest
from astrill_lazy.astrill import (
    AstrillEndpoint,
    AstrillNode,
    AstrillServer,
)
from astrill_lazy.catalog import load_catalog
from astrill_lazy.installer import EnsureResult, InstallResult
from astrill_lazy.models import MatchKind, RouteTarget
from astrill_lazy.native_settings import NativeAstrillSettings
from astrill_lazy.service_policy import ServiceRouteMode
from astrill_lazy.store import ConfigStore
from astrill_lazy.windows_controller import ControllerError, WindowsController
from astrill_lazy.windows_ssh_setup import (
    WindowsHostKey,
    WindowsKeyAuthorization,
)


class FakeRouter:
    def __init__(self) -> None:
        self.read_calls: list[str] = []
        self.write_calls: list[tuple[str, object]] = []
        self.payload = (
            b"this.list = [{id:1,name:'USA - Test',servers:["
            b"{id:7,lf:1,ips:["
            b"{ip:123,port:'443',mode:0,proto:134,index:0,protop:5},"
            b"{ip:124,port:'80',mode:0,proto:6,index:0,protop:5}"
            b"]}]}];"
        )

    def ping(self) -> bool:
        self.read_calls.append("ping")
        return True

    def status(self) -> dict[str, Any]:
        self.read_calls.append("status")
        return {"health": "healthy", "mode": "companion"}

    def native_astrill_status(self) -> dict[str, Any]:
        self.read_calls.append("native_status")
        return {"health": "healthy", "native_mode": True}

    def clients(self) -> list[dict[str, Any]]:
        self.read_calls.append("clients")
        return [{"address": "192.168.1.10"}]

    def native_clients(self) -> list[dict[str, Any]]:
        self.read_calls.append("native_clients")
        return [{"address": "192.168.1.20"}]

    def fetch_astrill_payload(self) -> bytes:
        self.read_calls.append("servers")
        return self.payload

    def native_astrill_settings(self) -> NativeAstrillSettings:
        self.read_calls.append("native_settings")
        return NativeAstrillSettings.from_dict({})

    def update_native_astrill_settings(
        self, changes: dict[str, Any]
    ) -> NativeAstrillSettings:
        self.write_calls.append(("native_settings", dict(changes)))
        return NativeAstrillSettings.from_dict(changes)

    def apply_rules(self, payload: str) -> dict[str, Any]:
        self.write_calls.append(("apply", payload))
        return {"ok": True, "origin_count": 1}

    def refresh(self) -> dict[str, Any]:
        self.write_calls.append(("refresh", None))
        return {"ok": True, "resolved_addresses": 1}

    def rollback(self) -> dict[str, Any]:
        self.write_calls.append(("rollback", None))
        return {"ok": True, "rolled_back": True}

    def set_astrill_connection(
        self, connected: bool, *, companion_enabled: bool
    ) -> dict[str, Any]:
        self.write_calls.append(
            (
                "connection",
                {
                    "connected": connected,
                    "companion_enabled": companion_enabled,
                },
            )
        )
        return {"ok": True, "vpn_state": "up" if connected else "down"}

    def switch_astrill(self, **arguments: object) -> dict[str, Any]:
        self.write_calls.append(("switch", dict(arguments)))
        return {"ok": True, "astrill_server_id": arguments["server_id"]}


def make_store(path: Path) -> ConfigStore:
    return ConfigStore(path)


def make_server() -> AstrillServer:
    return AstrillServer(
        id=1,
        name="USA - Test",
        nodes=(
            AstrillNode(
                id=7,
                weight=1,
                endpoints=(
                    AstrillEndpoint(
                        encoded_ip=123,
                        port="443",
                        mode=0,
                        protocol_code=134,
                        port_index=0,
                        protocol_original=5,
                    ),
                ),
            ),
        ),
    )


def test_configure_router_validates_and_persists_without_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path / "config.json")
    initial_router = FakeRouter()
    created: list[tuple[str, dict[str, object]]] = []

    class ConfiguredRouter(FakeRouter):
        def __init__(self, host: str, **options: object) -> None:
            super().__init__()
            self.host = host
            self.options = options
            created.append((host, options))

    monkeypatch.setattr(controller_module, "RouterClient", ConfiguredRouter)
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=initial_router,  # type: ignore[arg-type]
    )

    assert controller.configure_router(" root@192.168.1.1 ") == ("root@192.168.1.1")
    assert created == [
        (
            "192.168.1.1",
            {
                "user": "root",
                "port": 22,
                "identity_file": "~/.ssh/astrill_lazy_router_ed25519",
                "host_key_policy": "yes",
                "known_hosts_file": tmp_path / "known_hosts",
            },
        )
    ]
    assert controller.router.host == "192.168.1.1"  # type: ignore[attr-defined]
    assert controller.router.options["user"] == "root"  # type: ignore[attr-defined]
    saved = ConfigStore(store.path)
    assert saved.router_host == "192.168.1.1"
    assert saved.router_user == "root"
    assert saved.router_port == 22
    assert saved.router_identity == "~/.ssh/astrill_lazy_router_ed25519"
    assert initial_router.read_calls == []
    assert initial_router.write_calls == []

    for invalid in (
        "",
        "-oProxyCommand=bad",
        "router host",
        "router\nhost",
        "router..local",
    ):
        with pytest.raises(ValueError, match="router target"):
            controller.configure_router(invalid)
    with pytest.raises(ValueError, match="SSH user"):
        controller.configure_router("router.local", user="root admin")
    with pytest.raises(ValueError, match="SSH port"):
        controller.configure_router("router.local", port=0)
    with pytest.raises(ValueError, match="identity"):
        controller.configure_router("router.local", identity_file="bad\nkey")
    with pytest.raises(ValueError, match="absolute or start with"):
        controller.configure_router("router.local", identity_file="relative-key")

    assert len(created) == 1
    assert ConfigStore(store.path).router_host == "192.168.1.1"

    assert (
        controller.configure_router(
            "router.local",
            user="admin",
            port=2222,
            identity_file="~/.ssh/router_ed25519",
        )
        == "admin@router.local"
    )
    saved = ConfigStore(store.path)
    assert saved.router_host == "router.local"
    assert saved.router_user == "admin"
    assert saved.router_port == 2222
    assert saved.router_identity == "~/.ssh/router_ed25519"
    assert created[-1][1]["host_key_policy"] == "yes"


def test_legacy_ssh_alias_and_composite_target_keep_openssh_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "schema_version": 1,
  "router_host": "admin@router-alias",
  "active_region": "active-astrill",
  "enabled_extensions": ["core-catalog"],
  "rules": []
}
""".strip(),
        encoding="utf-8",
    )
    created: list[tuple[str, dict[str, object]]] = []

    class ConfiguredRouter(FakeRouter):
        def __init__(self, host: str, **options: object) -> None:
            super().__init__()
            created.append((host, options))

    monkeypatch.setattr(controller_module, "RouterClient", ConfiguredRouter)
    store = ConfigStore(path)
    controller = WindowsController(store=store, catalog=load_catalog())

    assert store.router_use_ssh_config is True
    assert created == [("admin@router-alias", {"host_key_policy": "yes"})]

    controller.set_read_only(True)
    loaded = ConfigStore(path)
    assert loaded.router_host == "admin@router-alias"
    assert loaded.router_use_ssh_config is True

    assert (
        controller.configure_router(
            "admin@192.168.1.1",
            user="ignored",
            port=2222,
            identity_file="~/.ssh/router",
            use_ssh_config=False,
        )
        == "admin@192.168.1.1"
    )
    assert created[-1] == (
        "192.168.1.1",
        {
            "user": "admin",
            "port": 2222,
            "identity_file": "~/.ssh/router",
            "host_key_policy": "yes",
            "known_hosts_file": tmp_path / "known_hosts",
        },
    )
    loaded = ConfigStore(path)
    assert loaded.router_host == "192.168.1.1"
    assert loaded.router_user == "admin"
    assert loaded.router_use_ssh_config is False


def test_guided_telnet_key_setup_requires_confirmation_and_never_saves_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path / "config.json")
    router = FakeRouter()
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )
    store.save()
    inspected = WindowsHostKey(
        host="192.168.1.1",
        port=22,
        key_type="ssh-ed25519",
        key_base64="AAAATEST",
        fingerprint="SHA256:test",
        trust_state="unknown",
        known_hosts_path=tmp_path / "known_hosts",
    )
    monkeypatch.setattr(
        controller_module,
        "inspect_windows_host_key",
        lambda *_args, **_kwargs: inspected,
    )
    assert controller.inspect_router_host_key() == inspected

    with pytest.raises(ControllerError, match="confirmation"):
        controller.authorize_router_key_via_telnet(
            inspected,
            "one-time-secret",
        )

    captured: dict[str, object] = {}
    authorized = WindowsKeyAuthorization(
        host_key=inspected,
        identity_file=tmp_path / "key",
        password_login_disabled=True,
    )

    def authorize(*args: object, **options: object) -> WindowsKeyAuthorization:
        captured["args"] = args
        captured["options"] = options
        return authorized

    monkeypatch.setattr(
        controller_module,
        "authorize_windows_router_key_via_telnet",
        authorize,
    )
    assert (
        controller.authorize_router_key_via_telnet(
            inspected,
            "one-time-secret",
            confirmed=True,
        )
        == authorized
    )
    assert captured["args"][2] == "one-time-secret"  # type: ignore[index]
    assert captured["options"] == {
        "user": "root",
        "identity_file": "~/.ssh/astrill_lazy_router_ed25519",
    }
    assert "one-time-secret" not in store.path.read_text(encoding="utf-8")


def test_read_only_blocks_every_router_mutation_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path / "config.json")
    store.companion_enabled = True
    router = FakeRouter()
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )

    class UnexpectedInstaller:
        def __init__(self, _router: object) -> None:
            pytest.fail("read-only operations must not construct an installer")

    monkeypatch.setattr(controller_module, "RouterInstaller", UnexpectedInstaller)
    server = make_server()
    operations = (
        controller.apply_rules,
        controller.install_companion,
        controller.repair_companion,
        controller.restore_native,
        controller.refresh_domains,
        controller.rollback,
        lambda: controller.set_connection(True),
        lambda: controller.switch_server(server, 2),
        lambda: controller.save_native_settings({"astrill_adsblock": "1"}),
    )

    for operation in operations:
        with pytest.raises(ControllerError, match="read-only access"):
            operation()

    assert router.write_calls == []


def test_endpoint_switch_requires_companion_and_dispatches_selected_protocol(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "config.json")
    store.read_only = False
    router = FakeRouter()
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )
    server = make_server()

    with pytest.raises(ControllerError, match="companion must be installed"):
        controller.switch_server(server, 2)
    assert router.write_calls == []

    store.companion_enabled = True
    assert controller.switch_server(server, 2) == {
        "ok": True,
        "astrill_server_id": 1,
    }
    assert router.write_calls == [
        (
            "switch",
            {
                "server_id": 1,
                "sid": 7,
                "encoded_ip": 123,
                "port": "443",
                "port_index": 0,
                "protocol": 2,
                "vpn_mode": 6,
            },
        )
    ]


def test_local_policy_service_and_device_mutations_are_saved_atomically(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "config.json")
    router = FakeRouter()
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )

    website = controller.add_custom_rule(
        name="Example",
        match_kind=MatchKind.DOMAIN,
        selector="example.com",
        target=RouteTarget.VPN,
        region="united-states",
    )
    before_invalid_edit = website.to_dict()
    with pytest.raises(ValueError, match="rule name"):
        controller.update_rule(website.id, name="")
    assert controller.rule_by_id(website.id).to_dict() == before_invalid_edit

    website = controller.update_rule(
        website.id,
        target=RouteTarget.DIRECT,
        enabled=False,
        priority=250,
    )
    assert website.target is RouteTarget.DIRECT
    assert website.region == "direct"
    assert website.enabled is False
    assert website.priority == 250
    assert website.metadata["country_override"] == "united-states"

    added = controller.add_services(["google"], ServiceRouteMode.SUGGESTED)
    assert (added.added, added.updated) == (1, 0)
    google = next(rule for rule in store.rules if rule.selector == "google")
    assert google.target is RouteTarget.VPN
    assert google.region == "united-states"

    direct = controller.add_services(["google"], ServiceRouteMode.DIRECT)
    assert (direct.added, direct.updated) == (0, 1)
    assert google.target is RouteTarget.DIRECT
    assert google.region == "direct"
    assert google.metadata["country_override"] == "united-states"

    vpn = controller.add_services(["google"], ServiceRouteMode.VPN)
    assert (vpn.added, vpn.updated) == (0, 1)
    assert google.target is RouteTarget.VPN
    assert google.region == "united-states"

    device = controller.add_device(
        "192.168.1.44",
        "Laptop",
        RouteTarget.DIRECT,
    )
    assert device.match_kind is MatchKind.DEVICE
    assert device.selector == "192.168.1.44"
    assert device.region == "direct"

    saved = ConfigStore(store.path)
    assert {rule.id for rule in saved.rules} == {
        website.id,
        google.id,
        device.id,
    }
    assert router.read_calls == []
    assert router.write_calls == []

    with pytest.raises(ValueError, match="unknown service"):
        controller.add_services(["missing-service"], ServiceRouteMode.SUGGESTED)
    with pytest.raises(ValueError, match="cannot be created"):
        controller.add_custom_rule(
            name="Windows App",
            match_kind=MatchKind.PROCESS,
            selector=r"C:\Program Files\App\app.exe",
            target=RouteTarget.DIRECT,
            region="direct",
        )
    assert len(store.rules) == 3

    assert controller.delete_rule(website.id).id == website.id
    assert {rule.id for rule in ConfigStore(store.path).rules} == {
        google.id,
        device.id,
    }


def test_read_operations_choose_native_or_companion_without_writes(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "config.json")
    router = FakeRouter()
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )

    assert controller.test_connection()
    assert controller.refresh_status()["native_mode"] is True
    assert controller.load_clients() == [{"address": "192.168.1.20"}]
    catalog = controller.load_servers()
    assert catalog.servers[0].name == "USA - Test"
    assert catalog.groups["united-states"] == catalog.servers
    assert isinstance(controller.load_native_settings(), NativeAstrillSettings)

    store.companion_enabled = True
    assert controller.refresh_status()["mode"] == "companion"
    assert controller.load_clients() == [{"address": "192.168.1.10"}]
    assert router.write_calls == []


def test_writable_router_operations_use_only_injected_fakes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path / "config.json")
    store.read_only = False
    router = FakeRouter()
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )
    controller.add_custom_rule(
        name="Example",
        match_kind=MatchKind.DOMAIN,
        selector="example.com",
        target=RouteTarget.DIRECT,
        region="direct",
    )

    class FakeInstaller:
        def __init__(self, supplied_router: object) -> None:
            assert supplied_router is router

        def install(self) -> InstallResult:
            return InstallResult(
                version="0.2.3",
                package_bytes=100,
                package_sha256="hash",
                nvram_chunks=1,
                policy_page=1,
                api_page=2,
                status={"ok": True},
            )

        def ensure(self, *, allow_install: bool = True) -> EnsureResult:
            assert allow_install is False
            return EnsureResult(status={"ok": True}, action="none")

        def uninstall(self) -> dict[str, Any]:
            return {"ok": True, "native_mode": True}

    monkeypatch.setattr(controller_module, "RouterInstaller", FakeInstaller)

    assert controller.install_companion().version == "0.2.3"
    assert store.companion_enabled is True
    assert controller.repair_companion().action == "none"
    assert controller.apply_rules()["ok"] is True
    assert controller.refresh_domains()["resolved_addresses"] == 1
    assert controller.rollback()["rolled_back"] is True
    assert controller.set_connection(True)["vpn_state"] == "up"
    assert controller.switch_server(make_server(), 2)["astrill_server_id"] == 1
    assert controller.save_native_settings({"astrill_adsblock": "1"}).enabled(
        "astrill_adsblock"
    )
    assert controller.restore_native()["native_mode"] is True
    assert store.companion_enabled is False

    write_names = [name for name, _value in router.write_calls]
    assert write_names == [
        "apply",
        "refresh",
        "rollback",
        "connection",
        "switch",
        "native_settings",
    ]
