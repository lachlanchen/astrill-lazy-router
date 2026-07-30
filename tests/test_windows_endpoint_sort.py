from __future__ import annotations

import os
from pathlib import Path
from time import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

import astrill_lazy.windows_ui as windows_ui_module
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
from astrill_lazy.router import AstrillConnectionResult
from astrill_lazy.store import ConfigStore
from astrill_lazy.windows_controller import (
    ServerCatalog,
    WindowsController,
)
from astrill_lazy.windows_ui import (
    ENDPOINT_COLUMN_COUNT,
    ENDPOINT_FAVORITE_COLUMN,
    ENDPOINT_LATENCY_COLUMN,
    ENDPOINT_NAME_COLUMN,
    ENDPOINT_REACH_COLUMN,
    ENDPOINT_SELECT_COLUMN,
    ENDPOINT_TESTED_COLUMN,
    STYLE_SHEET,
    MainWindow,
)
from PySide6.QtCore import QItemSelection, QItemSelectionModel, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QMessageBox,
    QTreeWidgetItem,
)


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


def _connection_settings(
    server: AstrillServer,
    **overrides: str,
) -> tuple[NativeAstrillSettings, dict[str, str]]:
    values = {
        **AstrillConnectionSelection.from_server(server, 1, 0).native_values(),
        "astrill_cipher": "default",
        "astrill_wanmtu": "1446",
        "astrill_accel": "0",
        "astrill_blockinternet": "0",
        "astrill_autocycle": "0",
        "astrill_autostart": "0",
        "astrill_favlist": "",
    }
    values.update(overrides)
    return NativeAstrillSettings.from_dict(values), values


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
        server = item.data(ENDPOINT_NAME_COLUMN, Qt.ItemDataRole.UserRole)
        assert isinstance(server, AstrillServer)
        values.append(server.id)
    return values


def _rows_by_id(window: MainWindow) -> dict[int, QTreeWidgetItem]:
    rows: dict[int, QTreeWidgetItem] = {}
    for index in range(window.endpoint_tree.topLevelItemCount()):
        item = window.endpoint_tree.topLevelItem(index)
        server = item.data(ENDPOINT_NAME_COLUMN, Qt.ItemDataRole.UserRole)
        assert isinstance(server, AstrillServer)
        rows[server.id] = item
    return rows


def _click_endpoint_cell(
    app: QApplication,
    window: MainWindow,
    item: QTreeWidgetItem,
    column: int,
    *,
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
) -> None:
    window.stack.setCurrentIndex(5)
    window.resize(1280, 800)
    window.show()
    app.processEvents()
    tree = window.endpoint_tree
    rect = tree.visualItemRect(item)
    assert rect.isValid()
    header = tree.header()
    position = QPoint(
        header.sectionViewportPosition(column) + header.sectionSize(column) // 2,
        rect.center().y(),
    )
    QTest.mouseClick(
        tree.viewport(),
        button,
        Qt.KeyboardModifier.NoModifier,
        position,
    )
    app.processEvents()


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


def test_exact_country_filter_preserves_hidden_endpoint_selection(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    rows = _rows_by_id(window)
    window._set_endpoint_selected(servers[0].id, True, item=rows[servers[0].id])
    window._refresh_endpoint_country_filter()

    country = servers[1].country_name()
    country_index = window.endpoint_country_filter.findData(country)
    assert country_index >= 0
    window.endpoint_country_filter.setCurrentIndex(country_index)

    assert _visible_server_ids(window) == [servers[1].id]
    assert window._endpoint_selected_server_ids == {servers[0].id}
    assert "1 hidden by filters" in window.endpoint_selection_status.text()

    window.endpoint_country_filter.setCurrentIndex(0)
    assert _visible_server_ids(window) == [server.id for server in servers]
    assert (
        _rows_by_id(window)[servers[0].id].checkState(ENDPOINT_SELECT_COLUMN)
        == Qt.CheckState.Checked
    )
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
    selected = window.endpoint_tree.currentItem().data(
        ENDPOINT_NAME_COLUMN,
        Qt.ItemDataRole.UserRole,
    )
    assert isinstance(selected, AstrillServer)
    assert selected.id == servers[0].id

    window._clear_endpoint_probe_results()
    assert window._endpoint_probe_results == {}
    assert load_endpoint_probe_cache(window._endpoint_probe_cache_path) == {}
    assert not window._endpoint_probe_cache_path.exists()
    window.close()


def test_explicit_clear_stays_clear_when_configured_endpoint_rerenders(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    window.router_status = {
        "vpn_state": "down",
        "astrill_server_id": servers[0].id,
        "astrill_protocol": 1,
    }
    window._render_endpoints()
    assert window._endpoint_selected_server_ids == {servers[0].id}

    window._clear_endpoint_selection()
    window.endpoint_search.setText("Europe")
    window.endpoint_search.clear()
    window.endpoint_sort.setCurrentIndex(window.endpoint_sort.findData("region"))

    assert window._endpoint_selected_server_ids == set()
    assert not window._selected_endpoints()
    window.close()


def test_ctrl_and_shift_style_multiselection_survives_sort_and_filter(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _servers = _window(tmp_path, monkeypatch)
    assert (
        window.endpoint_tree.selectionMode()
        == QAbstractItemView.SelectionMode.ExtendedSelection
    )
    rows = _rows_by_id(window)
    selection = QItemSelection(
        window.endpoint_tree.indexFromItem(rows[1], ENDPOINT_NAME_COLUMN),
        window.endpoint_tree.indexFromItem(rows[3], ENDPOINT_NAME_COLUMN),
    )
    window.endpoint_tree.selectionModel().select(
        selection,
        QItemSelectionModel.SelectionFlag.ClearAndSelect
        | QItemSelectionModel.SelectionFlag.Rows,
    )
    app.processEvents()

    assert window._endpoint_selected_server_ids == {1, 2, 3}
    assert all(
        row.checkState(ENDPOINT_SELECT_COLUMN) == Qt.CheckState.Checked
        for row in _rows_by_id(window).values()
    )

    window.endpoint_tree.selectionModel().select(
        window.endpoint_tree.indexFromItem(rows[2], ENDPOINT_NAME_COLUMN),
        QItemSelectionModel.SelectionFlag.Toggle
        | QItemSelectionModel.SelectionFlag.Rows,
    )
    app.processEvents()
    assert window._endpoint_selected_server_ids == {1, 3}

    window.endpoint_sort.setCurrentIndex(window.endpoint_sort.findData("region"))
    sorted_rows = _rows_by_id(window)
    assert sorted_rows[1].checkState(ENDPOINT_SELECT_COLUMN) == Qt.CheckState.Checked
    assert sorted_rows[2].checkState(ENDPOINT_SELECT_COLUMN) == Qt.CheckState.Unchecked
    assert sorted_rows[3].checkState(ENDPOINT_SELECT_COLUMN) == Qt.CheckState.Checked

    window.endpoint_search.setText("Europe")
    assert window._endpoint_selected_server_ids == {1, 3}
    assert "2 hidden by filters" in window.endpoint_selection_status.text()
    window.endpoint_search.clear()
    assert {server.id for server in window._selected_endpoints()} == {1, 3}
    assert all(
        _rows_by_id(window)[server_id].checkState(ENDPOINT_SELECT_COLUMN)
        == Qt.CheckState.Checked
        for server_id in (1, 3)
    )
    window.close()


def test_select_visible_preserves_hidden_selection_and_clear_resets_all(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _servers = _window(tmp_path, monkeypatch)

    window.endpoint_search.setText("Europe")
    window.endpoint_select_visible.setCheckState(Qt.CheckState.Checked)
    assert window._endpoint_selected_server_ids == {2}

    window.endpoint_search.clear()
    assert window.endpoint_select_visible.checkState() == Qt.CheckState.PartiallyChecked
    window.endpoint_search.setText("Zulu")
    window.endpoint_select_visible.setCheckState(Qt.CheckState.Checked)
    assert window._endpoint_selected_server_ids == {1, 2}
    assert "1 hidden by filters" in window.endpoint_selection_status.text()

    window.endpoint_clear_selection_button.click()
    assert window._endpoint_selected_server_ids == set()
    assert window.endpoint_select_visible.checkState() == Qt.CheckState.Unchecked
    window.endpoint_search.clear()
    assert all(
        row.checkState(ENDPOINT_SELECT_COLUMN) == Qt.CheckState.Unchecked
        for row in _rows_by_id(window).values()
    )
    assert not window.endpoint_clear_selection_button.isEnabled()
    window.close()


def test_header_sort_toggles_direction_indicator_and_preserves_selection(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _servers = _window(tmp_path, monkeypatch)
    rows = _rows_by_id(window)
    window._set_endpoint_selected(1, True, item=rows[1])
    window._set_endpoint_selected(3, True, item=rows[3])

    window._endpoint_header_clicked(ENDPOINT_LATENCY_COLUMN)
    header = window.endpoint_tree.header()
    assert window.endpoint_sort.currentData() == "header"
    assert header.isSortIndicatorShown()
    assert header.sortIndicatorSection() == ENDPOINT_LATENCY_COLUMN
    assert header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
    assert _visible_server_ids(window) == [2, 1, 3]
    assert window._endpoint_selected_server_ids == {1, 3}

    window._endpoint_header_clicked(ENDPOINT_LATENCY_COLUMN)
    assert header.sortIndicatorSection() == ENDPOINT_LATENCY_COLUMN
    assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
    assert _visible_server_ids(window) == [1, 2, 3]
    assert "high" in window.endpoint_sort.currentText()
    assert window._endpoint_selected_server_ids == {1, 3}
    rerendered = _rows_by_id(window)
    assert rerendered[1].checkState(ENDPOINT_SELECT_COLUMN) == Qt.CheckState.Checked
    assert rerendered[3].checkState(ENDPOINT_SELECT_COLUMN) == Qt.CheckState.Checked
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
        "Select",
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
    rows = _rows_by_id(window)
    assert rows[1].text(ENDPOINT_FAVORITE_COLUMN) == "—"
    assert rows[2].text(ENDPOINT_FAVORITE_COLUMN) == "★ Favorite"
    assert rows[2].text(ENDPOINT_LATENCY_COLUMN) == "9.5 ms"
    assert rows[2].text(ENDPOINT_REACH_COLUMN) == "Reachable"
    assert rows[2].text(ENDPOINT_TESTED_COLUMN) != ""

    window._set_endpoint_selected(servers[1].id, True, item=rows[2])
    assert window.endpoint_favorite_button.text() == "Favorite selected"
    assert window.endpoint_unfavorite_button.text() == "Unfavorite selected (1)"
    window._clear_endpoint_selection()
    window._set_endpoint_selected(servers[0].id, True, item=rows[1])
    assert window.endpoint_favorite_button.text() == "Favorite selected (1)"
    assert window.endpoint_unfavorite_button.text() == "Unfavorite selected"
    window.close()


def test_add_and_remove_favorite_use_verified_returned_settings(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    window.controller.store.read_only = False
    window._endpoint_favorites_loaded(_settings())
    rows = _rows_by_id(window)
    window._set_endpoint_selected(servers[0].id, True, item=rows[servers[0].id])
    window._set_endpoint_selected(servers[1].id, True, item=rows[servers[1].id])
    calls: list[tuple[tuple[int, ...], int | None, bool]] = []

    def set_favorites(
        selected: tuple[AstrillServer, ...],
        protocol: int | None,
        *,
        enabled: bool,
    ) -> NativeAstrillSettings:
        calls.append((tuple(server.id for server in selected), protocol, enabled))
        return (
            _settings(*(_favorite(server) for server in selected))
            if enabled
            else _settings()
        )

    monkeypatch.setattr(
        window.controller,
        "set_endpoint_favorites",
        set_favorites,
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
    window._set_selected_endpoint_favorites(True)
    assert calls == [((servers[0].id, servers[1].id), 1, True)]
    assert not window.endpoint_favorite_button.isEnabled()
    assert window.endpoint_unfavorite_button.text() == "Unfavorite selected (2)"

    # Removal is based on server ID and remains available even if the global
    # protocol selection is unsupported by this endpoint.
    window.protocol.setCurrentIndex(0)
    assert window.endpoint_unfavorite_button.isEnabled()
    window._set_selected_endpoint_favorites(False)
    assert calls[-1] == ((servers[0].id, servers[1].id), None, False)
    assert window.endpoint_favorite_button.text() == "Favorite selected (2)"
    assert not window.endpoint_unfavorite_button.isEnabled()
    window.close()


def test_favorite_button_click_confirms_and_dispatches_exactly_once(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    window.controller.store.read_only = False
    window._endpoint_favorites_loaded(_settings())
    selected = servers[0]
    window._set_endpoint_selected(
        selected.id,
        True,
        item=_rows_by_id(window)[selected.id],
    )
    controller_calls: list[tuple[tuple[int, ...], int | None, bool]] = []
    task_dispatches: list[str] = []

    def set_favorites(
        chosen: tuple[AstrillServer, ...],
        protocol: int | None,
        *,
        enabled: bool,
    ) -> NativeAstrillSettings:
        controller_calls.append(
            (tuple(server.id for server in chosen), protocol, enabled)
        )
        return _settings(_favorite(selected))

    def run_now(
        label: str,
        function: object,
        success: object = None,
        **_kwargs: object,
    ) -> None:
        task_dispatches.append(label)
        result = function()  # type: ignore[operator]
        if success is not None:
            success(result)  # type: ignore[operator]

    monkeypatch.setattr(
        window.controller,
        "set_endpoint_favorites",
        set_favorites,
    )
    monkeypatch.setattr(window, "_run_task", run_now)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    assert window.endpoint_favorite_button.isEnabled()
    window.endpoint_favorite_button.click()

    assert len(task_dispatches) == 1
    assert controller_calls == [((selected.id,), 1, True)]
    assert window._endpoint_favorite_records == {selected.id: _favorite(selected)}
    window.close()


def test_favorite_cell_click_toggles_only_that_endpoint_and_preserves_selection(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    window.controller.store.read_only = False
    window._endpoint_favorites_loaded(_settings(_favorite(servers[1])))
    rows = _rows_by_id(window)
    window._set_endpoint_selected(servers[0].id, True, item=rows[servers[0].id])
    window._set_endpoint_selected(servers[1].id, True, item=rows[servers[1].id])
    calls: list[tuple[tuple[int, ...], int | None, bool]] = []
    favorite_ids = {servers[1].id}

    def set_favorites(
        chosen: tuple[AstrillServer, ...],
        protocol: int | None,
        *,
        enabled: bool,
    ) -> NativeAstrillSettings:
        calls.append((tuple(server.id for server in chosen), protocol, enabled))
        for server in chosen:
            if enabled:
                favorite_ids.add(server.id)
            else:
                favorite_ids.discard(server.id)
        return _settings(
            *(_favorite(server) for server in servers if server.id in favorite_ids)
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

    monkeypatch.setattr(window.controller, "set_endpoint_favorites", set_favorites)
    monkeypatch.setattr(window, "_run_task", run_now)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    _click_endpoint_cell(
        app,
        window,
        rows[servers[0].id],
        ENDPOINT_FAVORITE_COLUMN,
        button=Qt.MouseButton.RightButton,
    )
    assert calls == []

    _click_endpoint_cell(
        app,
        window,
        rows[servers[0].id],
        ENDPOINT_FAVORITE_COLUMN,
    )

    assert calls == [((servers[0].id,), 1, True)]
    assert set(window._endpoint_selected_server_ids) == {
        servers[0].id,
        servers[1].id,
    }
    assert set(window._endpoint_favorite_records) == {
        servers[0].id,
        servers[1].id,
    }

    _click_endpoint_cell(
        app,
        window,
        _rows_by_id(window)[servers[0].id],
        ENDPOINT_FAVORITE_COLUMN,
    )

    assert calls[-1] == ((servers[0].id,), None, False)
    assert set(window._endpoint_selected_server_ids) == {
        servers[0].id,
        servers[1].id,
    }
    assert set(window._endpoint_favorite_records) == {servers[1].id}
    window.close()


def test_favorite_cell_click_uses_guards_and_ignores_invalid_rows(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    window.controller.store.read_only = False
    messages: list[str] = []
    controller_calls: list[object] = []
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(window, "_select_something", messages.append)
    monkeypatch.setattr(
        window.controller,
        "set_endpoint_favorites",
        lambda *args, **kwargs: controller_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message, *_args, **_kwargs: (
            warnings.append((title, message)) or QMessageBox.StandardButton.Cancel
        ),
    )

    window.endpoint_tree.favoriteCellClicked.emit(object())
    window.endpoint_tree.favoriteCellClicked.emit(QTreeWidgetItem())
    assert messages == []
    assert controller_calls == []

    row = _rows_by_id(window)[servers[0].id]
    window.endpoint_tree.favoriteCellClicked.emit(row)
    assert "Sync a valid favorite list" in messages[-1]
    assert controller_calls == []

    window._endpoint_favorites_loaded(
        NativeAstrillSettings.from_dict({"astrill_favlist": "malformed"})
    )
    window.endpoint_tree.favoriteCellClicked.emit(_rows_by_id(window)[servers[0].id])
    assert "Sync a valid favorite list" in messages[-1]
    assert controller_calls == []

    window._endpoint_favorites_loaded(_settings())
    window.controller.store.read_only = True
    window.endpoint_tree.favoriteCellClicked.emit(_rows_by_id(window)[servers[0].id])
    assert "read-only guard" in messages[-1]
    assert controller_calls == []

    window.controller.store.read_only = False
    window.busy_count = 1
    window.endpoint_tree.favoriteCellClicked.emit(_rows_by_id(window)[servers[0].id])
    assert "Wait for the current action" in window.statusBar().currentMessage()
    assert controller_calls == []

    window.busy_count = 0
    window.protocol.setCurrentIndex(0)
    window.endpoint_tree.favoriteCellClicked.emit(_rows_by_id(window)[servers[0].id])
    assert warnings
    assert warnings[-1][0] == "Unsupported endpoint protocol"
    assert controller_calls == []
    window.close()


def test_unrelated_connection_draft_survives_endpoint_favorite_merge(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    window.controller.store.read_only = False
    settings, baseline_values = _connection_settings(servers[0])
    window._apply_native_settings(settings, force_native_page=True)

    connection_server = servers[1]
    connection_index = window.connection_page.server_dropdown.findData(
        connection_server.id
    )
    assert connection_index >= 0
    window.connection_page.server_dropdown.setCurrentIndex(connection_index)
    draft_before = window.connection_page.collect()
    assert window.connection_page.dirty
    assert not window.connection_page.has_pending_favorite_changes

    endpoint_favorite = _favorite(servers[2])
    returned_values = {
        **baseline_values,
        "astrill_favlist": endpoint_favorite.to_native(),
    }
    returned_settings = NativeAstrillSettings.from_dict(returned_values)
    endpoint_rows = _rows_by_id(window)
    window._set_endpoint_selected(
        servers[2].id,
        True,
        item=endpoint_rows[servers[2].id],
    )
    dispatches: list[str] = []

    monkeypatch.setattr(
        window.controller,
        "set_endpoint_favorites",
        lambda *_args, **_kwargs: returned_settings,
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    def run_now(
        label: str,
        function: object,
        success: object = None,
        **_kwargs: object,
    ) -> None:
        dispatches.append(label)
        result = function()  # type: ignore[operator]
        if success is not None:
            success(result)  # type: ignore[operator]

    monkeypatch.setattr(window, "_run_task", run_now)

    assert window.endpoint_favorite_button.isEnabled()
    window.endpoint_favorite_button.click()

    draft_after = window.connection_page.collect()
    assert dispatches == ["Adding 1 router favorites"]
    assert window.connection_page.dirty
    assert draft_after.selection == draft_before.selection
    assert draft_after.changes == draft_before.changes
    assert draft_after.favorite_changes == ()
    assert not window.connection_page.has_pending_favorite_changes
    assert window.connection_page.settings is not None
    assert (
        window.connection_page.settings.get("astrill_favlist")
        == endpoint_favorite.to_native()
    )
    assert dict(window.connection_page._baseline or ())["astrill_favlist"] == (
        endpoint_favorite.to_native()
    )
    assert not window.connection_page.conflict_banner.isVisible()
    window.close()


def test_dirty_native_controls_survive_endpoint_favorite_click(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    window.controller.store.read_only = False
    settings, baseline_values = _connection_settings(servers[0])
    window._apply_native_settings(settings, force_native_page=True)

    ads = window.native_page._direct_controls["astrill_adsblock"]
    assert isinstance(ads, QCheckBox)
    ads.setChecked(True)
    assert window.native_page.dirty

    window._clear_endpoint_selection()
    selected = servers[2]
    window._set_endpoint_selected(
        selected.id,
        True,
        item=_rows_by_id(window)[selected.id],
    )
    returned = NativeAstrillSettings.from_dict(
        {
            **baseline_values,
            "astrill_favlist": _favorite(selected).to_native(),
        }
    )
    monkeypatch.setattr(
        window.controller,
        "set_endpoint_favorites",
        lambda *_args, **_kwargs: returned,
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

    assert window.endpoint_favorite_button.isEnabled()
    window.endpoint_favorite_button.click()

    assert window.native_page.dirty
    assert ads.isChecked()
    assert selected.id in window._endpoint_favorite_records
    assert (
        "1 saved endpoint" in window.native_page._state_labels["astrill_favlist"].text()
    )
    window.close()


def test_favorite_merge_keeps_unrelated_router_change_as_connection_conflict(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    window.controller.store.read_only = False
    settings, baseline_values = _connection_settings(servers[0])
    window._apply_native_settings(settings, force_native_page=True)

    draft_server = servers[1]
    window.connection_page.server_dropdown.setCurrentIndex(
        window.connection_page.server_dropdown.findData(draft_server.id)
    )
    draft_before = window.connection_page.collect()
    assert window.connection_page.dirty

    window._clear_endpoint_selection()
    favorite_server = servers[2]
    window._set_endpoint_selected(
        favorite_server.id,
        True,
        item=_rows_by_id(window)[favorite_server.id],
    )
    returned = NativeAstrillSettings.from_dict(
        {
            **baseline_values,
            "astrill_favlist": _favorite(favorite_server).to_native(),
            "astrill_cipher": "AES-256-CBC",
        }
    )
    monkeypatch.setattr(
        window.controller,
        "set_endpoint_favorites",
        lambda *_args, **_kwargs: returned,
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
    window.endpoint_favorite_button.click()

    draft_after = window.connection_page.collect()
    assert draft_after.selection == draft_before.selection
    assert draft_after.changes == draft_before.changes
    assert window.connection_page.settings is not None
    assert window.connection_page.settings.get("astrill_cipher") == "default"
    assert dict(window.connection_page._baseline or ())["astrill_cipher"] == "default"
    assert not window.connection_page.conflict_banner.isHidden()
    window.close()


def test_pending_connection_favorite_edit_blocks_endpoint_favorite_action(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    window.controller.store.read_only = False
    settings, _values = _connection_settings(servers[0])
    window._apply_native_settings(settings, force_native_page=True)
    window.connection_page.favorite_switch.setChecked(True)
    assert window.connection_page.dirty
    assert window.connection_page.has_pending_favorite_changes

    endpoint_rows = _rows_by_id(window)
    window._set_endpoint_selected(
        servers[1].id,
        True,
        item=endpoint_rows[servers[1].id],
    )
    window._sync_endpoint_action_ui()

    assert not window.endpoint_favorite_button.isEnabled()
    tooltip = window.endpoint_favorite_button.toolTip().casefold()
    assert "connection" in tooltip
    assert "favorite" in tooltip
    assert "unsaved" in tooltip or "pending" in tooltip
    window.close()


def test_multiple_selected_endpoints_block_router_connect(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    window.controller.store.read_only = False
    window.controller.store.companion_enabled = True
    rows = _rows_by_id(window)
    window._set_endpoint_selected(servers[0].id, True, item=rows[servers[0].id])
    window._set_endpoint_selected(servers[1].id, True, item=rows[servers[1].id])
    messages: list[str] = []
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(window, "_select_something", messages.append)
    monkeypatch.setattr(
        window.controller,
        "switch_server",
        lambda server, protocol: calls.append((server.id, protocol)),
    )

    window._sync_endpoint_action_ui()
    assert not window.connect_endpoint_button.isEnabled()
    window._connect_endpoint()

    assert calls == []
    assert messages == [
        "Select exactly one Astrill endpoint before connecting the router."
    ]
    assert "requires exactly one endpoint" in window.endpoint_action_status.text()
    window.close()


def test_single_endpoint_connect_uses_transactional_native_path_without_companion(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    window.controller.store.read_only = False
    window.controller.store.companion_enabled = False
    window._clear_endpoint_selection()
    rows = _rows_by_id(window)
    selected = servers[0]
    window._set_endpoint_selected(selected.id, True, item=rows[selected.id])
    calls: list[tuple[int, int]] = []

    def apply(server: AstrillServer, protocol: int) -> AstrillConnectionResult:
        calls.append((server.id, protocol))
        return AstrillConnectionResult(
            status={
                "health": "healthy",
                "native_mode": True,
                "vpn_state": "up",
                "astrill_server_id": server.id,
                "astrill_protocol": protocol,
            },
            settings=NativeAstrillSettings.from_dict(
                {
                    "astrill_serverid": str(server.id),
                    "astrill_protocol": str(protocol),
                }
            ),
        )

    def run_now(
        _label: str,
        function: object,
        success: object = None,
        **_options: object,
    ) -> None:
        result = function()  # type: ignore[operator]
        if success is not None:
            success(result)  # type: ignore[operator]

    monkeypatch.setattr(window.controller, "apply_server_connection", apply)
    monkeypatch.setattr(window, "_run_task", run_now)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    window._sync_endpoint_action_ui()
    assert window.connect_endpoint_button.isEnabled()
    assert "transactional native path" in window.endpoint_action_status.text()
    window._connect_endpoint()

    assert calls == [(selected.id, 1)]
    assert window.router_status["vpn_state"] == "up"
    assert window.router_status["astrill_server_id"] == selected.id
    window.close()


def test_dedicated_connection_page_save_apply_and_disconnect_are_synchronized(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    server = servers[0]
    settings, values = _connection_settings(server)
    window.controller.store.read_only = False
    window.router_status = {
        "health": "healthy",
        "vpn_state": "down",
        "astrill_server_id": server.id,
        "astrill_protocol": 1,
    }
    window._apply_native_settings(settings, force_native_page=True)
    window._sync_access_ui()

    assert window._page_index("connection") == 4
    assert window._page_index("endpoints") == 5
    assert window.connection_page.server_dropdown.currentData() == server.id
    assert window.connection_page.protocol_dropdown.currentData() == 1
    assert not window.connection_page.dirty

    calls: list[tuple[str, object]] = []
    favorite_calls: list[tuple[tuple[int, str | None], ...]] = []

    def run_now(
        _label: str,
        function: object,
        success: object = None,
        **options: object,
    ) -> None:
        result = function()  # type: ignore[operator]
        if success is not None:
            success(result)  # type: ignore[operator]
        finished = options.get("finished_callback")
        if finished is not None:
            finished()  # type: ignore[operator]

    def save(
        selection: AstrillConnectionSelection,
        changes: dict[str, object],
    ) -> NativeAstrillSettings:
        calls.append(("save", (selection.server_id, dict(changes))))
        values.update({key: str(value) for key, value in changes.items()})
        values.update(selection.native_values())
        return NativeAstrillSettings.from_dict(values)

    def apply(
        selection: AstrillConnectionSelection,
        changes: dict[str, object],
    ) -> AstrillConnectionResult:
        calls.append(("apply", (selection.server_id, dict(changes))))
        values.update({key: str(value) for key, value in changes.items()})
        values.update(selection.native_values())
        return AstrillConnectionResult(
            status={
                "health": "healthy",
                "vpn_state": "up",
                "astrill_server_id": selection.server_id,
                "astrill_protocol": selection.protocol,
            },
            settings=NativeAstrillSettings.from_dict(values),
        )

    def apply_favorites(
        changes: tuple[tuple[int, AstrillFavorite | None], ...],
    ) -> NativeAstrillSettings:
        favorite_calls.append(
            tuple(
                (
                    server_id,
                    favorite.to_native() if favorite is not None else None,
                )
                for server_id, favorite in changes
            )
        )
        values["astrill_favlist"] = ",".join(
            favorite.to_native()
            for _server_id, favorite in changes
            if favorite is not None
        )
        return NativeAstrillSettings.from_dict(values)

    monkeypatch.setattr(window, "_run_task", run_now)
    monkeypatch.setattr(window.controller, "save_astrill_connection", save)
    monkeypatch.setattr(window.controller, "apply_astrill_connection", apply)
    monkeypatch.setattr(
        window.controller,
        "apply_endpoint_favorite_changes",
        apply_favorites,
    )
    monkeypatch.setattr(
        window.controller,
        "set_connection",
        lambda connected: {
            "health": "healthy",
            "vpn_state": "up" if connected else "down",
            "astrill_server_id": server.id,
            "astrill_protocol": 1,
        },
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    window.connection_page.favorite_switch.setChecked(True)
    window.connection_page.switches["astrill_autostart"].setChecked(True)
    assert window.connection_page.dirty
    window._save_connection_page()
    assert favorite_calls == [((server.id, _favorite(server).to_native()),)]
    assert calls[0] == ("save", (server.id, {"astrill_autostart": "1"}))
    assert not window.connection_page.dirty
    assert "saved and verified" in window.connection_page.action_status.text()

    window.connection_page.switches["astrill_blockinternet"].setChecked(True)
    window._apply_connection_page()
    assert calls[1] == ("apply", (server.id, {"astrill_blockinternet": "1"}))
    assert window.router_status["vpn_state"] == "up"
    assert not window.connection_page.dirty
    assert "connected" in window.connection_page.action_status.text()

    window._disconnect_connection_page()
    assert window.router_status["vpn_state"] == "down"
    assert "disconnected" in window.connection_page.action_status.text()
    window.close()


def test_astrill_and_connection_editors_lock_overlapping_drafts(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    settings, _values = _connection_settings(servers[0])
    window.controller.store.read_only = False
    window.router_status = {
        "health": "healthy",
        "vpn_state": "down",
        "astrill_server_id": servers[0].id,
        "astrill_protocol": 1,
    }
    window._apply_native_settings(settings, force_native_page=True)
    window._sync_access_ui()

    ads = window.native_page._direct_controls["astrill_adsblock"]
    assert isinstance(ads, QCheckBox)
    ads.setChecked(True)

    assert window.native_page.dirty
    assert not window.connection_page.mtu.isEnabled()
    assert not window.connection_page.apply_button.isEnabled()
    window._save_connection_page()
    assert "Astrill-page draft" in window.connection_page.action_status.text()

    window.native_page.render(settings, [])
    assert not window.native_page.dirty
    assert window.connection_page.favorite_switch.isEnabled()
    window.connection_page.mtu.setValue(1400)

    assert window.connection_page.dirty
    assert not ads.isEnabled()
    assert not window.native_page.save_button.isEnabled()
    assert "Connection-page draft" in window.native_page.save_button.toolTip()
    window.close()


def test_connection_draft_reports_favorite_saved_before_later_failure(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    settings, _values = _connection_settings(servers[0])
    window.controller.store.read_only = False
    window.router_status = {
        "health": "healthy",
        "vpn_state": "down",
        "astrill_server_id": servers[0].id,
        "astrill_protocol": 1,
    }
    window._apply_native_settings(settings, force_native_page=True)
    window._sync_access_ui()
    window.connection_page.favorite_switch.setChecked(True)
    draft = window.connection_page.collect()
    favorite_calls: list[object] = []
    monkeypatch.setattr(
        window.controller,
        "apply_endpoint_favorite_changes",
        lambda changes: favorite_calls.append(changes),
    )
    monkeypatch.setattr(
        window.controller,
        "save_astrill_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("NVRAM unavailable")),
    )

    with pytest.raises(RuntimeError, match="Favorite edits were saved and verified"):
        window._save_connection_draft(draft)

    assert favorite_calls == [draft.favorite_changes]
    window.close()


def test_selected_latency_scope_uses_durable_multiselection_only(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    rows = _rows_by_id(window)
    window._set_endpoint_selected(servers[0].id, True, item=rows[servers[0].id])
    window._set_endpoint_selected(servers[2].id, True, item=rows[servers[2].id])
    window.endpoint_search.setText("Europe")
    assert window.endpoint_probe_scope.currentData() == "selected"
    assert tuple(server.id for server in window._endpoint_probe_selection()) == (1, 3)

    probe_calls: list[tuple[tuple[int, ...], int]] = []
    task_options: list[dict[str, object]] = []

    def probe(
        selected: tuple[AstrillServer, ...],
        protocol: int,
    ) -> tuple[EndpointProbeResult, ...]:
        probe_calls.append((tuple(server.id for server in selected), protocol))
        return ()

    def run_now(
        _label: str,
        function: object,
        success: object = None,
        **options: object,
    ) -> None:
        task_options.append(options)
        result = function()  # type: ignore[operator]
        if success is not None:
            success(result)  # type: ignore[operator]
        finished = options.get("finished_callback")
        if finished is not None:
            finished()  # type: ignore[operator]

    monkeypatch.setattr(windows_ui_module, "probe_servers", probe)
    monkeypatch.setattr(window, "_run_task", run_now)

    window._test_endpoint_latency()

    assert probe_calls == [((1, 3), 1)]
    assert task_options[0]["router_related"] is False
    assert not window._endpoint_probe_running
    window.close()


def test_latency_dialog_launcher_preserves_scope_and_status_when_reopened(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _servers = _window(tmp_path, monkeypatch)
    window.show()
    app.processEvents()

    dialog = window.endpoint_latency_dialog
    assert dialog.isHidden()
    assert not dialog.isVisible()

    window.endpoint_latency_dialog_button.click()
    app.processEvents()
    assert dialog.isVisible()

    all_index = window.endpoint_probe_scope.findData("all")
    assert all_index >= 0
    window.endpoint_probe_scope.setCurrentIndex(all_index)
    preserved_status = (
        "Manual PC test saved Â· 3/3 reachable Â· no DD-WRT commands sent."
    )
    window.endpoint_probe_status.setText(preserved_status)

    dialog.close()
    app.processEvents()
    assert dialog.isHidden()
    window.endpoint_latency_dialog_button.click()
    app.processEvents()

    assert dialog.isVisible()
    assert window.endpoint_probe_scope.currentData() == "all"
    assert window.endpoint_probe_status.text() == preserved_status
    dialog.close()
    window.close()


def test_endpoint_toolbar_labels_fit_at_supported_minimum_window(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _servers = _window(tmp_path, monkeypatch)
    window.setStyleSheet(STYLE_SHEET)
    window.stack.setCurrentIndex(5)
    window.resize(960, 640)
    window.show()
    app.processEvents()

    controls = (
        window.endpoint_select_visible,
        window.endpoint_clear_selection_button,
        window.endpoint_behavior_dialog_button,
        window.endpoint_favorite_button,
        window.endpoint_unfavorite_button,
    )
    clipped = {
        control.text(): (control.width(), control.sizeHint().width())
        for control in controls
        if control.width() < control.sizeHint().width()
    }
    assert clipped == {}
    assert window.endpoint_tree.height() >= 120
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


def test_favorite_action_handlers_enforce_overlap_read_only_and_busy_guards(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, servers = _window(tmp_path, monkeypatch)
    window.controller.store.read_only = False
    connection_settings, _values = _connection_settings(servers[0])
    window._apply_native_settings(connection_settings, force_native_page=True)
    window.endpoint_tree.setCurrentItem(window.endpoint_tree.topLevelItem(0))
    messages: list[str] = []
    calls: list[tuple[tuple[int, ...], int | None, bool]] = []
    monkeypatch.setattr(window, "_select_something", messages.append)
    monkeypatch.setattr(
        window.controller,
        "set_endpoint_favorites",
        lambda servers, protocol, *, enabled: calls.append(
            (tuple(server.id for server in servers), protocol, enabled)
        ),
        raising=False,
    )

    window.connection_page.favorite_switch.setChecked(True)
    assert window.connection_page.has_pending_favorite_changes
    window._toggle_selected_endpoint_favorite()
    assert calls == []
    assert "unsaved favorite edits" in messages[-1]

    window._apply_native_settings(
        connection_settings,
        force_native_page=True,
        force_connection_page=True,
    )
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
    rows = _rows_by_id(window)
    assert rows[favorite.server_id].text(ENDPOINT_FAVORITE_COLUMN) == "★ Favorite"
    assert "existing GUI state preserved" in window.endpoint_favorite_status.text()
    window.close()


def test_endpoint_connection_behavior_follows_native_readback(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _servers = _window(tmp_path, monkeypatch)
    window._native_settings_loaded(
        NativeAstrillSettings.from_dict(
            {
                "astrill_favlist": "",
                "astrill_autocycle": "1",
                "astrill_autostart": "0",
            }
        )
    )
    assert window.endpoint_autocycle.isChecked()
    assert not window.endpoint_autostart.isChecked()

    window._native_settings_loaded(
        NativeAstrillSettings.from_dict(
            {
                "astrill_favlist": "",
                "astrill_autocycle": "0",
                "astrill_autostart": "1",
            }
        )
    )
    assert not window.endpoint_autocycle.isChecked()
    assert window.endpoint_autostart.isChecked()
    window.close()


def test_endpoint_connection_behavior_writes_only_selected_key(
    app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _servers = _window(tmp_path, monkeypatch)
    window.controller.store.read_only = False
    initial = NativeAstrillSettings.from_dict(
        {
            "astrill_favlist": "",
            "astrill_autocycle": "1",
            "astrill_autostart": "0",
        }
    )
    window._native_settings_loaded(initial)
    changes: list[dict[str, object]] = []

    def save_native_settings(values: dict[str, object]) -> NativeAstrillSettings:
        changes.append(values)
        return NativeAstrillSettings.from_dict({**initial.values, **values})

    def run_now(
        _label: str,
        function: object,
        success: object = None,
        **options: object,
    ) -> None:
        result = function()  # type: ignore[operator]
        if success is not None:
            success(result)  # type: ignore[operator]
        finished = options.get("finished_callback")
        if finished is not None:
            finished()  # type: ignore[operator]

    monkeypatch.setattr(
        window.controller,
        "save_native_settings",
        save_native_settings,
    )
    monkeypatch.setattr(window, "_run_task", run_now)

    window.endpoint_autostart.click()

    assert changes == [{"astrill_autostart": "1"}]
    assert window.endpoint_autostart.isChecked()
    assert window.endpoint_autocycle.isChecked()
    window.close()
