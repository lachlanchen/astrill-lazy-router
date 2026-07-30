from __future__ import annotations

from pathlib import Path
from typing import Any

import astrill_lazy.windows_controller as controller_module
import pytest
from astrill_lazy.astrill import (
    AstrillConnectionSelection,
    AstrillEndpoint,
    AstrillFavorite,
    AstrillNode,
    AstrillServer,
)
from astrill_lazy.catalog import load_catalog
from astrill_lazy.installer import CompanionCheck, EnsureResult, InstallResult
from astrill_lazy.models import MatchKind, RouteTarget, Rule
from astrill_lazy.native_settings import NativeAstrillSettings
from astrill_lazy.router import AstrillConnectionResult, RouterMonitorSnapshot
from astrill_lazy.service_policy import ServiceRouteMode
from astrill_lazy.store import ConfigStore
from astrill_lazy.windows_controller import (
    ControllerError,
    PolicyCompilationSummary,
    ServerCatalog,
    WindowsController,
    summarize_policy_runtime,
)
from astrill_lazy.windows_ssh_setup import (
    WindowsHostKey,
    WindowsKeyAuthorization,
)


class FakeRouter:
    def __init__(self) -> None:
        self.read_calls: list[str] = []
        self.write_calls: list[tuple[str, object]] = []
        self.monitor_presence: dict[str, Any] = {
            "installed": True,
            "version": "0.2.5",
            "runtime": True,
        }
        self.monitor_companion_status: dict[str, Any] | None = {
            "health": "healthy",
            "version": "0.2.5",
            "jump_installed": True,
            "watchdog": True,
            "policy_health": "ready",
            "precedence_ok": True,
            "vpn_state": "up",
            "astrill_server_id": 1,
            "astrill_protocol": 2,
        }
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

    def monitor_snapshot(self, *, include_companion: bool) -> RouterMonitorSnapshot:
        self.read_calls.append("monitor")
        return RouterMonitorSnapshot(
            native_status={
                "health": "healthy",
                "native_mode": True,
                "vpn_state": "down",
            },
            settings=NativeAstrillSettings.from_dict(
                {
                    "astrill_serverid": "1",
                    "astrill_protocol": "2",
                }
            ),
            companion_presence=(
                dict(self.monitor_presence)
                if include_companion
                else {"installed": False, "version": None, "runtime": False}
            ),
            companion_status=(
                dict(self.monitor_companion_status)
                if include_companion and self.monitor_companion_status is not None
                else None
            ),
        )

    def update_native_astrill_settings(
        self, changes: dict[str, Any]
    ) -> NativeAstrillSettings:
        self.write_calls.append(("native_settings", dict(changes)))
        return NativeAstrillSettings.from_dict(changes)

    def replace_astrill_favorites(
        self, expected_current: str, replacement: str
    ) -> NativeAstrillSettings:
        self.write_calls.append(
            (
                "favorites",
                {
                    "expected_current": expected_current,
                    "replacement": replacement,
                },
            )
        )
        return NativeAstrillSettings.from_dict({"astrill_favlist": replacement})

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

    def save_astrill_connection(
        self,
        selection: AstrillConnectionSelection,
        changes: dict[str, Any],
    ) -> NativeAstrillSettings:
        values = {**selection.native_values(), **changes}
        self.write_calls.append(("save_connection", values))
        return NativeAstrillSettings.from_dict(values)

    def apply_astrill_connection(
        self,
        selection: AstrillConnectionSelection,
        changes: dict[str, Any],
        *,
        companion_enabled: bool,
    ) -> AstrillConnectionResult:
        values = {**selection.native_values(), **changes}
        self.write_calls.append(
            (
                "apply_connection",
                {
                    "values": values,
                    "companion_enabled": companion_enabled,
                },
            )
        )
        return AstrillConnectionResult(
            status={
                "ok": True,
                "vpn_state": "up",
                "astrill_server_id": selection.server_id,
            },
            settings=NativeAstrillSettings.from_dict(values),
        )


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
        lambda: controller.apply_server_connection(server, 2),
        lambda: controller.save_astrill_connection(
            AstrillConnectionSelection.from_server(server, 2, 0),
            {},
        ),
        lambda: controller.save_native_settings({"astrill_adsblock": "1"}),
        lambda: controller.set_endpoint_favorite(server, 2, enabled=True),
        lambda: controller.apply_endpoint_favorite_changes(((server.id, None),)),
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


def test_endpoint_apply_supports_companion_and_transactional_native_paths(
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

    native_result = controller.apply_server_connection(
        server,
        2,
        {"astrill_autostart": "1"},
    )
    assert native_result.status["vpn_state"] == "up"
    assert router.write_calls[-1] == (
        "apply_connection",
        {
            "values": {
                "astrill_serverid": "1",
                "astrill_sid": "7",
                "astrill_ip": "123",
                "astrill_port": "443",
                "astrill_portindex": "0",
                "astrill_protocol": "2",
                "astrill_vpnmode": "6",
                "astrill_autostart": "1",
            },
            "companion_enabled": False,
        },
    )

    store.companion_enabled = True
    companion_result = controller.apply_server_connection(server, 2)
    assert companion_result.settings.get("astrill_serverid") == "1"
    assert router.write_calls[-1][1]["companion_enabled"] is True  # type: ignore[index]


def test_connection_state_sequences_one_snapshot_before_catalog(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "config.json")
    store.companion_enabled = True
    router = FakeRouter()
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )

    state = controller.load_connection_state(refresh_servers=True)

    assert state.status["vpn_state"] == "up"
    assert state.settings.get("astrill_serverid") == "1"
    assert len(state.server_catalog.servers) == 1
    assert router.read_calls[-2:] == ["monitor", "servers"]

    router.read_calls.clear()
    cached = controller.load_connection_state(refresh_servers=False)
    assert cached.server_catalog is controller.server_catalog
    assert router.read_calls == ["monitor"]


def test_connection_state_repairs_vanished_companion_runtime_before_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path / "config.json")
    store.companion_enabled = True
    router = FakeRouter()
    router.monitor_presence = {
        "installed": True,
        "version": "0.2.4",
        "runtime": False,
    }
    router.monitor_companion_status = None
    repaired = {
        "health": "healthy",
        "version": "0.2.4",
        "jump_installed": True,
        "watchdog": True,
        "vpn_state": "down",
        "astrill_server_id": 1,
        "astrill_protocol": 2,
    }

    class FakeInstaller:
        def __init__(self, supplied_router: object) -> None:
            assert supplied_router is router

        def check(
            self,
            *,
            presence: dict[str, Any] | None = None,
            status: dict[str, Any] | None = None,
        ) -> CompanionCheck:
            router.read_calls.append("check")
            assert presence == router.monitor_presence
            assert status is None
            return CompanionCheck(
                "repair",
                "0.2.4",
                "0.2.4",
                None,
                "runtime needs repair",
            )

        def ensure(self, *, allow_install: bool = True) -> EnsureResult:
            router.read_calls.append("ensure")
            assert allow_install is False
            return EnsureResult(repaired, "repaired")

    monkeypatch.setattr(controller_module, "RouterInstaller", FakeInstaller)
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )

    state = controller.load_connection_state(refresh_servers=True)

    assert state.status == repaired
    assert state.status.get("native_mode") is not True
    assert controller.store.companion_enabled is True
    assert "restored from router NVRAM" in str(controller.recovery_notice)
    assert router.read_calls == ["monitor", "check", "ensure", "servers"]


def test_connection_state_never_masks_unrepairable_runtime_as_native(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path / "config.json")
    store.companion_enabled = True
    router = FakeRouter()
    router.monitor_presence = {
        "installed": True,
        "version": "0.2.4",
        "runtime": False,
    }
    router.monitor_companion_status = None

    class FakeInstaller:
        def __init__(self, supplied_router: object) -> None:
            assert supplied_router is router

        def check(
            self,
            *,
            presence: dict[str, Any] | None = None,
            status: dict[str, Any] | None = None,
        ) -> CompanionCheck:
            router.read_calls.append("check")
            assert presence == router.monitor_presence
            assert status is None
            return CompanionCheck(
                "install",
                "0.2.4",
                "0.2.4",
                None,
                "stored runtime cannot be repaired",
            )

    monkeypatch.setattr(controller_module, "RouterInstaller", FakeInstaller)
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )

    with pytest.raises(ControllerError, match="separately confirmed"):
        controller.load_connection_state(refresh_servers=True)

    assert controller.store.companion_enabled is True
    assert router.read_calls == ["monitor", "check"]


def test_endpoint_favorite_fresh_reads_and_does_not_require_companion(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "config.json")
    store.read_only = False
    router = FakeRouter()
    current = "9999:123:443:1:6:9999"

    def native_settings() -> NativeAstrillSettings:
        router.read_calls.append("native_settings")
        return NativeAstrillSettings.from_dict({"astrill_favlist": current})

    router.native_astrill_settings = native_settings  # type: ignore[method-assign]
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )
    controller.server_catalog = controller.load_servers()
    router.read_calls.clear()

    settings = controller.set_endpoint_favorite(
        controller.server_catalog.servers[0],
        2,
        enabled=True,
    )

    assert router.read_calls == ["native_settings"]
    assert router.write_calls == [
        (
            "favorites",
            {
                "expected_current": current,
                "replacement": current + ",1:123:443:0:6:7",
            },
        )
    ]
    assert settings.get("astrill_favlist") == current + ",1:123:443:0:6:7"
    assert store.companion_enabled is False


def test_endpoint_favorite_remove_is_protocol_independent_and_idempotent(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "config.json")
    store.read_only = False
    router = FakeRouter()
    current = "1:123:443:0:6:7,9999:456:53:0:5:9999"

    def native_settings() -> NativeAstrillSettings:
        router.read_calls.append("native_settings")
        return NativeAstrillSettings.from_dict({"astrill_favlist": current})

    router.native_astrill_settings = native_settings  # type: ignore[method-assign]
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )

    removed = controller.set_endpoint_favorite(
        make_server(),
        None,
        enabled=False,
    )
    assert removed.get("astrill_favlist") == "9999:456:53:0:5:9999"
    assert router.write_calls[-1] == (
        "favorites",
        {
            "expected_current": current,
            "replacement": "9999:456:53:0:5:9999",
        },
    )

    router.write_calls.clear()
    missing = AstrillServer(700, "Missing", ())
    unchanged = controller.set_endpoint_favorite(
        missing,
        None,
        enabled=False,
    )
    assert unchanged.get("astrill_favlist") == current
    assert router.write_calls == []


def test_endpoint_favorite_malformed_snapshot_performs_no_write(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "config.json")
    store.read_only = False
    router = FakeRouter()
    router.native_astrill_settings = lambda: NativeAstrillSettings.from_dict(  # type: ignore[method-assign]
        {"astrill_favlist": "invalid"}
    )
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="favorite record"):
        controller.set_endpoint_favorite(make_server(), None, enabled=False)
    assert router.write_calls == []


def test_endpoint_favorites_bulk_add_uses_one_fresh_read_and_one_cas(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "config.json")
    store.read_only = False
    router = FakeRouter()
    current = "1:999:80:1:5:1,9999:456:53:0:5:9999"

    def server(server_id: int) -> AstrillServer:
        return AstrillServer(
            id=server_id,
            name=f"Endpoint {server_id}",
            nodes=(
                AstrillNode(
                    id=server_id + 10,
                    weight=1,
                    endpoints=(
                        AstrillEndpoint(
                            encoded_ip=server_id * 100 + 2,
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

    first, second, third = server(1), server(2), server(3)

    def native_settings() -> NativeAstrillSettings:
        router.read_calls.append("native_settings")
        return NativeAstrillSettings.from_dict({"astrill_favlist": current})

    router.native_astrill_settings = native_settings  # type: ignore[method-assign]
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )
    controller.server_catalog = ServerCatalog((first, second, third), {})

    settings = controller.set_endpoint_favorites(
        (third, first, second),
        2,
        enabled=True,
    )

    replacement = current + ",3:302:443:0:6:13,2:202:443:0:6:12"
    assert router.read_calls == ["native_settings"]
    assert router.write_calls == [
        (
            "favorites",
            {
                "expected_current": current,
                "replacement": replacement,
            },
        )
    ]
    assert settings.get("astrill_favlist") == replacement


def test_endpoint_favorites_bulk_remove_is_catalog_and_protocol_independent(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "config.json")
    store.read_only = False
    router = FakeRouter()
    current = "1:123:443:0:6:7,9999:456:53:0:5:9999,2:202:443:0:6:12"

    def native_settings() -> NativeAstrillSettings:
        router.read_calls.append("native_settings")
        return NativeAstrillSettings.from_dict({"astrill_favlist": current})

    router.native_astrill_settings = native_settings  # type: ignore[method-assign]
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )

    settings = controller.set_endpoint_favorites(
        (make_server(), AstrillServer(700, "Missing", ())),
        None,
        enabled=False,
    )

    replacement = "9999:456:53:0:5:9999,2:202:443:0:6:12"
    assert router.read_calls == ["native_settings"]
    assert router.write_calls == [
        (
            "favorites",
            {
                "expected_current": current,
                "replacement": replacement,
            },
        )
    ]
    assert settings.get("astrill_favlist") == replacement


def test_endpoint_favorites_bulk_validation_is_atomic_and_noops_skip_cas(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "config.json")
    store.read_only = False
    router = FakeRouter()
    current = "9999:456:53:0:5:9999"

    def native_settings() -> NativeAstrillSettings:
        router.read_calls.append("native_settings")
        return NativeAstrillSettings.from_dict({"astrill_favlist": current})

    router.native_astrill_settings = native_settings  # type: ignore[method-assign]
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )
    existing = AstrillServer(1, "Existing without loaded protocol data", ())
    unsupported = AstrillServer(
        2,
        "Unsupported",
        (
            AstrillNode(
                12,
                1,
                (
                    AstrillEndpoint(
                        encoded_ip=202,
                        port="443",
                        mode=0,
                        protocol_code=6,
                        port_index=0,
                    ),
                ),
            ),
        ),
    )

    controller.server_catalog = ServerCatalog((make_server(), unsupported), {})
    with pytest.raises(ValueError, match="does not support"):
        controller.set_endpoint_favorites(
            (make_server(), unsupported),
            2,
            enabled=True,
        )
    assert router.read_calls == ["native_settings"]
    assert router.write_calls == []

    router.read_calls.clear()
    current = "1:123:443:0:6:7"
    controller.server_catalog = ServerCatalog((), {})
    settings = controller.set_endpoint_favorites(
        (existing,),
        2,
        enabled=True,
    )
    assert settings.get("astrill_favlist") == current
    assert router.read_calls == ["native_settings"]
    assert router.write_calls == []

    router.read_calls.clear()
    with pytest.raises(ValueError, match="at least one"):
        controller.set_endpoint_favorites((), 2, enabled=True)
    with pytest.raises(ValueError, match="duplicate server ID"):
        controller.set_endpoint_favorites(
            (existing, existing),
            2,
            enabled=True,
        )
    with pytest.raises(ValueError, match="select an Astrill protocol"):
        controller.set_endpoint_favorites((existing,), True, enabled=True)
    assert router.read_calls == []
    assert router.write_calls == []


def test_connection_favorite_changes_fresh_merge_without_catalog_overwrite(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "config.json")
    store.read_only = False
    router = FakeRouter()
    current = "9999:456:53:0:5:9999,1:123:443:0:6:7"

    def native_settings() -> NativeAstrillSettings:
        router.read_calls.append("native_settings")
        return NativeAstrillSettings.from_dict({"astrill_favlist": current})

    router.native_astrill_settings = native_settings  # type: ignore[method-assign]
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )
    added = AstrillFavorite(2, 202, "443", 0, 6, 12)

    settings = controller.apply_endpoint_favorite_changes(((1, None), (2, added)))

    replacement = "9999:456:53:0:5:9999,2:202:443:0:6:12"
    assert router.read_calls == ["native_settings"]
    assert router.write_calls == [
        (
            "favorites",
            {
                "expected_current": current,
                "replacement": replacement,
            },
        )
    ]
    assert settings.get("astrill_favlist") == replacement


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


def test_policy_preflight_blocks_oversize_full_apply_without_router_io(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "config.json")
    store.read_only = False
    store.companion_enabled = True
    store.rules = [
        Rule(
            id=f"capacity-{index:03d}",
            name=f"Capacity policy {index:03d}",
            match_kind=MatchKind.DOMAIN,
            selector=f"host-{index:03d}.example.com",
            target=RouteTarget.DIRECT,
            region="direct",
            priority=100 + index,
        )
        for index in range(120)
    ]
    router = FakeRouter()
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )

    preview = controller.policy_preflight()

    assert isinstance(preview, PolicyCompilationSummary)
    assert preview.rule_count == 120
    assert preview.enabled_count == 120
    assert preview.compiled_rows == 120
    assert preview.compiled_bytes is not None
    assert preview.compiled_bytes > preview.limit_bytes == 6144
    assert preview.can_apply is False
    assert "Select a smaller set" in str(preview.error)
    with pytest.raises(ControllerError, match="router accepts at most 6,144"):
        controller.apply_rules()
    assert router.write_calls == []


def test_selected_policy_apply_preserves_oversize_local_catalog(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "config.json")
    store.read_only = False
    store.companion_enabled = True
    store.rules = [
        Rule(
            id=f"selected-{index:03d}",
            name=f"Selected policy {index:03d}",
            match_kind=MatchKind.DOMAIN,
            selector=f"selected-{index:03d}.example.com",
            target=RouteTarget.DIRECT,
            region="direct",
            priority=100 + index,
        )
        for index in range(120)
    ]
    router = FakeRouter()
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=router,  # type: ignore[arg-type]
    )
    selected_ids = ("selected-000", "selected-001")

    empty_preview = controller.policy_preflight(())
    assert empty_preview.can_apply is False
    assert "Select at least one policy" in str(empty_preview.error)
    with pytest.raises(ControllerError, match="Select at least one policy"):
        controller.apply_rules(())
    assert router.write_calls == []

    preview = controller.policy_preflight(selected_ids)
    result = controller.apply_rules(selected_ids)

    assert preview.can_apply is True
    assert preview.rule_count == preview.enabled_count == preview.compiled_rows == 2
    assert preview.compiled_bytes is not None
    assert preview.compiled_bytes < preview.limit_bytes
    assert result["ok"] is True
    assert len(store.rules) == 120
    assert router.write_calls[0][0] == "apply"
    payload = str(router.write_calls[0][1])
    assert payload.startswith("# astrill-lazy-rules-v1\n")
    assert "\tselected-000\n" in payload
    assert "\tselected-001\n" in payload
    assert "selected-002" not in payload


def test_policy_runtime_summary_accepts_new_and_legacy_status_fields() -> None:
    legacy = summarize_policy_runtime({"health": "healthy", "vpn_state": "up"})
    assert legacy.state == "unknown"
    assert legacy.precedence_ok is None

    ready = summarize_policy_runtime(
        {
            "policy_health": "ready",
            "precedence_ok": "true",
            "native_min_pref": "27998",
            "direct_pref": 27996,
            "vpn_pref": "27997",
            "table_readiness": {
                "direct": "true",
                "vpn": 1,
                "native": False,
            },
            "vpn_fail_closed": "1",
        }
    )
    assert ready.state == "ready"
    assert ready.precedence_ok is True
    assert ready.native_min_pref == 27998
    assert ready.direct_pref == 27996
    assert ready.vpn_pref == 27997
    assert ready.table_readiness == {
        "direct": True,
        "vpn": True,
        "native": False,
    }
    assert ready.vpn_fail_closed is True

    degraded = summarize_policy_runtime(
        {
            "vpn_state": "up",
            "policy_health": "degraded",
            "precedence_ok": False,
            "last_reconcile_error": "native rules did not stabilize",
        }
    )
    assert degraded.degraded is True
    assert degraded.last_error == "native rules did not stabilize"


def test_policy_origin_comparison_prefers_exact_enabled_rule_detail(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "config.json")
    enabled = Rule(
        id="enabled-local",
        name="Enabled local",
        match_kind=MatchKind.DOMAIN,
        selector="enabled.example.com",
        target=RouteTarget.DIRECT,
        region="direct",
    )
    disabled = Rule(
        id="disabled-local",
        name="Disabled local",
        match_kind=MatchKind.DOMAIN,
        selector="disabled.example.com",
        target=RouteTarget.DIRECT,
        region="direct",
        enabled=False,
    )
    store.rules = [enabled, disabled]
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=FakeRouter(),  # type: ignore[arg-type]
    )

    exact = controller.policy_origin_comparison(
        {
            "enabled_origin_count": 99,
            "rules": [
                {"origin": enabled.id, "enabled": True},
                {"origin": enabled.id, "enabled": True},
                {"origin": disabled.id, "enabled": False},
            ],
        }
    )
    assert exact.exact is True
    assert exact.applied_count == 1
    assert exact.matches is True

    fallback = controller.policy_origin_comparison({"enabled_origin_count": "1"})
    assert fallback.exact is False
    assert fallback.applied_count == 1
    assert fallback.matches is None


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
