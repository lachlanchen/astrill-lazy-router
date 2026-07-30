from __future__ import annotations

import os
from pathlib import Path
from time import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from astrill_lazy.astrill import (
    AstrillConnectionSelection,
    AstrillEndpoint,
    AstrillFavorite,
    AstrillNode,
    AstrillServer,
)
from astrill_lazy.catalog import load_catalog
from astrill_lazy.endpoint_probe import (
    EndpointProbeResult,
    EndpointProbeStatus,
)
from astrill_lazy.endpoint_probe_store import (
    SavedEndpointProbe,
    endpoint_probe_cache_path,
    load_endpoint_probe_cache,
    save_endpoint_probe_cache,
)
from astrill_lazy.native_settings import NativeAstrillSettings
from astrill_lazy.store import ConfigStore
from astrill_lazy.windows_controller import (
    ServerCatalog,
    WindowsController,
)
from astrill_lazy.windows_ui import (
    ENDPOINT_COLUMN_COUNT,
    ENDPOINT_FAVORITE_COLUMN,
    ENDPOINT_LATENCY_COLUMN,
    ENDPOINT_REACH_COLUMN,
    ENDPOINT_TESTED_COLUMN,
    MainWindow,
)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _server(server_id: int, name: str, address: str) -> AstrillServer:
    return AstrillServer(
        id=server_id,
        name=name,
        nodes=(
            AstrillNode(
                id=server_id + 100,
                weight=1,
                endpoints=(
                    AstrillEndpoint(
                        encoded_ip=536872392 + server_id,
                        port="443",
                        mode=1,
                        protocol_code=6,
                        port_index=0,
                        resolved_ip=address,
                    ),
                ),
            ),
        ),
    )


def _result(server: AstrillServer, latency_ms: float) -> EndpointProbeResult:
    return EndpointProbeResult(
        server_id=server.id,
        server_name=server.name,
        selected_protocol=1,
        tested_protocol=1,
        address=server.nodes[0].endpoints[0].resolved_ip,
        port=443,
        status=EndpointProbeStatus.REACHABLE,
        latency_ms=latency_ms,
        detail="TCP connection established",
    )


def _favorite(server: AstrillServer) -> AstrillFavorite:
    return AstrillFavorite.from_selection(
        AstrillConnectionSelection.from_server(server, 1, 0)
    )


def _settings(*favorites: AstrillFavorite) -> NativeAstrillSettings:
    return NativeAstrillSettings.from_dict(
        {"astrill_favlist": ",".join(favorite.to_native() for favorite in favorites)}
    )


def _window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MainWindow, tuple[AstrillServer, ...]]:
    store = ConfigStore(tmp_path / "config.json")
    servers = (
        _server(1, "Zulu US", "67.43.53.1"),
        _server(2, "Alpha Europe", "67.43.53.2"),
        _server(3, "Untested Other", "67.43.53.3"),
    )
    cache = {
        (1, 1): SavedEndpointProbe(_result(servers[0], 100.0), int(time())),
        (2, 1): SavedEndpointProbe(_result(servers[1], 9.5), int(time())),
    }
    save_endpoint_probe_cache(endpoint_probe_cache_path(store.path), cache)
    controller = WindowsController(
        store=store,
        catalog=load_catalog(),
        router=SimpleNamespace(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(MainWindow, "_refresh_status", lambda *_args, **_kwargs: None)
    window = MainWindow(controller)
    controller.server_catalog = ServerCatalog(
        servers,
        {
            "united-states": (servers[0],),
            "europe": (servers[1],),
            "other": (servers[2],),
        },
    )
    window._endpoint_catalog_loaded = True
    window.protocol.setCurrentIndex(1)
    window._render_endpoints()
    return window, servers


def _visible_server_ids(window: MainWindow) -> list[int]:
    values: list[int] = []
    for index in range(window.endpoint_tree.topLevelItemCount()):
        item = window.endpoint_tree.topLevelItem(index)
        server = item.data(0, Qt.ItemDataRole.UserRole)
        assert isinstance(server, AstrillServer)
        values.append(server.id)
    return values


def test_endpoint_sort_modes_use_saved_numeric_latency(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _servers = _window(tmp_path, monkeypatch)

    assert "Loaded 2 saved results" in window.endpoint_probe_status.text()
    assert _visible_server_ids(window) == [1, 2, 3]

    window.endpoint_sort.setCurrentIndex(window.endpoint_sort.findData("region"))
    assert _visible_server_ids(window) == [2, 3, 1]

    window.endpoint_sort.setCurrentIndex(window.endpoint_sort.findData("latency"))
    assert _visible_server_ids(window) == [2, 1, 3]

    window.endpoint_sort.setCurrentIndex(window.endpoint_sort.findData("default"))
    assert _visible_server_ids(window) == [1, 2, 3]
    window.close()


def test_selection_survives_filter_and_clear_removes_saved_cache(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    first = window.endpoint_tree.topLevelItem(0)
    window.endpoint_tree.setCurrentItem(first)
    assert window._endpoint_selected_server_id == servers[0].id

    window.endpoint_search.setText("Europe")
    assert window.endpoint_tree.currentItem() is None
    window.endpoint_search.clear()
    selected = window.endpoint_tree.currentItem().data(0, Qt.ItemDataRole.UserRole)
    assert isinstance(selected, AstrillServer)
    assert selected.id == servers[0].id

    window._clear_endpoint_probe_results()
    assert window._endpoint_probe_results == {}
    assert load_endpoint_probe_cache(window._endpoint_probe_cache_path) == {}
    assert not window._endpoint_probe_cache_path.exists()
    window.close()


def test_completed_manual_test_is_saved_without_router_access(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    result = _result(servers[0], 12.3)
    router_calls: list[object] = []
    monkeypatch.setattr(
        window.controller.router,
        "raw",
        lambda *args, **kwargs: router_calls.append((args, kwargs)),
        raising=False,
    )

    window._endpoint_probe_completed((result,))

    saved = load_endpoint_probe_cache(window._endpoint_probe_cache_path)
    assert saved[(servers[0].id, 1)].result.latency_ms == 12.3
    assert router_calls == []
    window.close()


def test_router_favorites_sync_into_a_dedicated_endpoint_column(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    window._endpoint_favorites_loaded(_settings(_favorite(servers[1])))

    headers = [
        window.endpoint_tree.headerItem().text(column)
        for column in range(ENDPOINT_COLUMN_COUNT)
    ]
    assert headers == [
        "Endpoint",
        "Region",
        "Favorite",
        "Server ID",
        "Router state",
        "Nodes",
        "PC latency",
        "Reach",
        "Tested",
    ]
    rows = {
        window.endpoint_tree.topLevelItem(index)
        .data(0, Qt.ItemDataRole.UserRole)
        .id: window.endpoint_tree.topLevelItem(index)
        for index in range(window.endpoint_tree.topLevelItemCount())
    }
    assert rows[1].text(ENDPOINT_FAVORITE_COLUMN) == "—"
    assert rows[2].text(ENDPOINT_FAVORITE_COLUMN) == "★ Favorite"
    assert rows[2].text(ENDPOINT_LATENCY_COLUMN) == "9.5 ms"
    assert rows[2].text(ENDPOINT_REACH_COLUMN) == "Reachable"
    assert rows[2].text(ENDPOINT_TESTED_COLUMN) != ""

    window.endpoint_tree.setCurrentItem(rows[2])
    assert window.endpoint_favorite_button.text() == "Remove selected favorite"
    window.endpoint_tree.setCurrentItem(rows[1])
    assert window.endpoint_favorite_button.text() == "Add selected favorite"
    window.close()


def test_add_and_remove_favorite_use_verified_returned_settings(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    window.controller.store.read_only = False
    window._endpoint_favorites_loaded(_settings())
    first = window.endpoint_tree.topLevelItem(0)
    window.endpoint_tree.setCurrentItem(first)
    calls: list[tuple[int, int, bool]] = []

    def set_favorite(
        server: AstrillServer,
        protocol: int,
        *,
        enabled: bool,
    ) -> NativeAstrillSettings:
        calls.append((server.id, protocol, enabled))
        return _settings(_favorite(server)) if enabled else _settings()

    monkeypatch.setattr(
        window.controller,
        "set_endpoint_favorite",
        set_favorite,
        raising=False,
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    def run_now(
        _label: str,
        function: object,
        success: object = None,
        **_kwargs: object,
    ) -> None:
        result = function()  # type: ignore[operator]
        if success is not None:
            success(result)  # type: ignore[operator]

    monkeypatch.setattr(window, "_run_task", run_now)
    window._toggle_selected_endpoint_favorite()
    assert calls == [(servers[0].id, 1, True)]
    assert window.endpoint_favorite_button.text() == "Remove selected favorite"

    # Removal is based on server ID and remains available even if the global
    # protocol selection is unsupported by this endpoint.
    window.protocol.setCurrentIndex(0)
    assert window.endpoint_favorite_button.isEnabled()
    window._toggle_selected_endpoint_favorite()
    assert calls[-1] == (servers[0].id, 0, False)
    assert window.endpoint_favorite_button.text() == "Add selected favorite"
    window.close()


def test_favorite_sync_preserves_dirty_native_page_and_invalid_data_blocks_edits(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    window.controller.store.read_only = False
    window._endpoint_favorites_loaded(_settings())
    ads = window.native_page._direct_controls["astrill_adsblock"]
    assert isinstance(ads, QCheckBox)
    ads.setChecked(True)
    assert window.native_page.dirty

    window._endpoint_favorites_loaded(_settings(_favorite(servers[0])))
    assert window.native_page.dirty
    assert ads.isChecked()
    assert (
        "1 saved endpoint" in window.native_page._state_labels["astrill_favlist"].text()
    )
    assert not window.endpoint_favorite_button.isEnabled()

    window.native_page.render(_settings())
    window._endpoint_favorites_loaded(
        NativeAstrillSettings.from_dict({"astrill_favlist": "invalid"})
    )
    assert window._endpoint_favorites_valid is False
    assert not window.endpoint_favorite_button.isEnabled()
    assert all(
        window.endpoint_tree.topLevelItem(index).text(ENDPOINT_FAVORITE_COLUMN)
        == "Invalid"
        for index in range(window.endpoint_tree.topLevelItemCount())
    )
    window.close()


def test_favorite_action_handlers_enforce_dirty_read_only_and_busy_guards(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _servers = _window(tmp_path, monkeypatch)
    window.controller.store.read_only = False
    window._endpoint_favorites_loaded(_settings())
    window.endpoint_tree.setCurrentItem(window.endpoint_tree.topLevelItem(0))
    messages: list[str] = []
    calls: list[tuple[int, int | None, bool]] = []
    monkeypatch.setattr(window, "_select_something", messages.append)
    monkeypatch.setattr(
        window.controller,
        "set_endpoint_favorite",
        lambda server, protocol, *, enabled: calls.append(
            (server.id, protocol, enabled)
        ),
        raising=False,
    )

    ads = window.native_page._direct_controls["astrill_adsblock"]
    assert isinstance(ads, QCheckBox)
    ads.setChecked(True)
    window._toggle_selected_endpoint_favorite()
    assert calls == []
    assert "unsaved Astrill-page edits" in messages[-1]

    window.native_page.render(_settings())
    window.controller.store.read_only = True
    window._toggle_selected_endpoint_favorite()
    assert calls == []
    assert "read-only guard" in messages[-1]

    window.controller.store.read_only = False
    window.busy_count = 1
    window._toggle_selected_endpoint_favorite()
    assert calls == []
    assert "Wait for the current action" in window.statusBar().currentMessage()
    window.busy_count = 0
    window.close()


def test_failed_favorite_sync_preserves_last_valid_state_and_finishes_loading(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    favorite = _favorite(servers[1])
    window._endpoint_favorites_loaded(_settings(favorite))

    def fail_now(
        label: str,
        _function: object,
        _success: object = None,
        *,
        quiet: bool = False,
        finished_callback: object = None,
        router_related: bool = True,
    ) -> None:
        window._task_failed(
            label,
            "router offline",
            quiet,
            router_related=router_related,
        )
        if finished_callback is not None:
            finished_callback()  # type: ignore[operator]

    monkeypatch.setattr(window, "_run_task", fail_now)
    window._sync_endpoint_favorites(quiet=True)

    assert not window._native_settings_loading
    assert window._endpoint_favorites_valid is True
    assert window._endpoint_favorite_records == {favorite.server_id: favorite}
    rows = {
        window.endpoint_tree.topLevelItem(index)
        .data(0, Qt.ItemDataRole.UserRole)
        .id: window.endpoint_tree.topLevelItem(index)
        for index in range(window.endpoint_tree.topLevelItemCount())
    }
    assert rows[favorite.server_id].text(ENDPOINT_FAVORITE_COLUMN) == "★ Favorite"
    assert "existing GUI state preserved" in window.endpoint_favorite_status.text()
    window.close()
