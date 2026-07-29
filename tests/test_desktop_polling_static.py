from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_UI = (PROJECT_ROOT / "desktop" / "astrill_lazy" / "windows_ui.py").read_text(
    encoding="utf-8"
)
UBUNTU_UI = (PROJECT_ROOT / "desktop" / "astrill_lazy" / "application.py").read_text(
    encoding="utf-8"
)


def test_windows_desktop_has_no_recurring_router_status_timer() -> None:
    assert "setInterval(60_000)" not in WINDOWS_UI
    assert "self.monitor.start()" not in WINDOWS_UI
    assert "Status is not polled automatically." in WINDOWS_UI


def test_ubuntu_desktop_has_no_recurring_router_status_timer() -> None:
    assert "GLib.timeout_add_seconds" not in UBUNTU_UI
    assert "_monitor_router_companion" not in UBUNTU_UI
    assert "manual; no background polling" in UBUNTU_UI


def test_successfully_loaded_empty_page_data_is_cached() -> None:
    assert "self._clients_loaded = True" in WINDOWS_UI
    assert "self._endpoint_catalog_loaded = True" in WINDOWS_UI
    assert "self._clients_loaded = True" in UBUNTU_UI


def test_pc_latency_probe_is_only_started_by_its_button() -> None:
    assert (
        "self.endpoint_probe_button.clicked.connect(self._test_endpoint_latency)"
        in WINDOWS_UI
    )
    assert WINDOWS_UI.count("probe_servers(servers, protocol)") == 1
