from __future__ import annotations

import os
from pathlib import Path
from time import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from astrill_lazy.astrill import (
    AstrillEndpoint,
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
from astrill_lazy.store import ConfigStore
from astrill_lazy.windows_controller import (
    ServerCatalog,
    WindowsController,
)
from astrill_lazy.windows_ui import MainWindow
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


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
