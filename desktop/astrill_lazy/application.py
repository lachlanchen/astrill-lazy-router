from __future__ import annotations

import shlex
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango

from .astrill import (
    ASTRILL_PROTOCOL_NAMES,
    AstrillConnectionSelection,
    AstrillServer,
    group_by_region,
    parse_applet,
)
from .astrill_install import (
    ASTRILL_INSTALL_TEMPLATE,
    AstrillInstaller,
    install_astrill,
    prepare_astrill_installer,
)
from .autostart import (
    disable_autostart,
    enable_autostart,
    is_autostart_enabled,
)
from .catalog import Catalog, discover_extensions, load_catalog
from .compiler import compile_rules
from .connection_page import AstrillConnectionPage, ConnectionDraft
from .detector import (
    MINIMUM_BYPASS_SERVICES,
    RouteRecommendation,
    detect_rules,
)
from .installer import CompanionCheck, EnsureResult, RouterInstaller
from .launcher import ApplicationLauncher, parse_command
from .models import MatchKind, Region, RouteTarget, Rule, Service
from .native_page import NativeSettingsPage
from .native_settings import NativeAstrillSettings
from .router import AstrillConnectionResult, RouterClient, RouterError
from .service_policy import ServiceRouteMode, service_policy_route
from .ssh_setup import authorize_router_key, ensure_local_identity
from .store import ConfigStore

APP_ID = "io.github.lachlanchen.AstrillLazyRouter"


CSS = """
.app-sidebar { background: #f2f4f5; border-right: 1px solid #d8dde1; }
.brand-block { padding: 18px 16px 12px; }
.brand-title { font-size: 18px; font-weight: 700; color: #182129; }
.brand-subtitle, .muted { color: #68747d; }
.nav-list { margin: 8px; background: transparent; }
.nav-list row { border-radius: 6px; margin: 2px 0; }
.nav-list row:selected { background: #dfe7e3; color: #144b36; }
.nav-item { padding: 9px 10px; }
.page { background: #f7f8f9; }
.page-content { padding: 18px 22px 28px; }
.section-title { font-size: 18px; font-weight: 700; color: #202a32; }
.status-band { background: #ffffff; border: 1px solid #d8dde1; border-radius: 6px; }
.metric { padding: 14px 16px; border-right: 1px solid #e0e4e7; }
.metric:last-child { border-right: 0; }
.metric-value { font-size: 17px; font-weight: 700; color: #202a32; }
.metric-label { font-size: 12px; color: #68747d; }
.policy-list, .catalog-list { background: #ffffff; border: 1px solid #d8dde1; border-radius: 6px; }
.policy-list row, .catalog-list row { border-bottom: 1px solid #e5e8ea; }
.policy-list row:last-child, .catalog-list row:last-child { border-bottom: 0; }
.compact-button { min-height: 30px; padding: 2px 9px; }
.route-direct:checked { background: #dcece2; color: #145d3d; }
.route-vpn:checked { background: #dce8f3; color: #145789; }
.route-label { font-size: 12px; font-weight: 700; }
.status-good { color: #18794e; }
.status-bad { color: #b42318; }
.status-neutral { color: #68747d; }
.toolbar-section { margin-top: 18px; margin-bottom: 8px; }
.empty-state { padding: 36px 12px; }
.catalog-route { font-size: 12px; font-weight: 700; padding: 4px 8px; border-radius: 4px; }
.catalog-direct { background: #dcece2; color: #145d3d; }
.catalog-vpn { background: #dce8f3; color: #145789; }
.catalog-count { color: #68747d; margin-left: 4px; }
.location-current { background: #edf7f0; }
.sidebar-status { padding: 12px 16px; border-top: 1px solid #d8dde1; }
.batch-bar { background: #ffffff; border: 1px solid #d8dde1; border-radius: 6px; padding: 8px 10px; }
.batch-count { color: #44515a; font-weight: 600; }
"""


class AstrillLazyApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )
        self.window: MainWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        show_page = Gio.SimpleAction.new("show-page", GLib.VariantType.new("s"))
        show_page.connect("activate", self._show_page)
        self.add_action(show_page)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MainWindow(self)
        self.window.present()

    def _show_page(self, _action: Gio.SimpleAction, parameter: GLib.Variant) -> None:
        self.activate()
        if self.window is not None:
            self.window.select_page(parameter.get_string())


class MainWindow(Adw.ApplicationWindow):
    PAGE_DEFINITIONS = (
        ("policies", "Policies", "view-list-symbolic"),
        ("services", "Services", "view-app-grid-symbolic"),
        ("countries", "Countries", "mark-location-symbolic"),
        ("devices", "Devices", "network-workgroup-symbolic"),
        ("connection", "Connection", "network-vpn-symbolic"),
        ("locations", "Endpoints", "network-server-symbolic"),
        ("astrill", "Astrill", "preferences-system-symbolic"),
        ("router", "Router", "network-server-symbolic"),
        ("extensions", "Extensions", "application-x-addon-symbolic"),
    )

    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application)
        self.set_title("Astrill Lazy Router")
        self.set_default_size(1180, 760)
        self.set_size_request(880, 600)

        self.store = ConfigStore()
        self.ssh_setup_error: str | None = None
        try:
            ensure_local_identity(self.store.router_identity)
        except (OSError, RuntimeError, ValueError) as exc:
            self.ssh_setup_error = str(exc)
        self.catalog: Catalog = load_catalog(self.store.enabled_extensions)
        self.router = self._router_client_from_store()
        self.launcher = ApplicationLauncher()
        self.router_status: dict[str, Any] = {}
        self.astrill_applet_available: bool | None = None
        self.router_companion_check: CompanionCheck | None = None
        self.native_settings: NativeAstrillSettings | None = None
        self.servers: tuple[AstrillServer, ...] | None = None
        self.servers_loading = False
        self.server_groups: dict[str, tuple[AstrillServer, ...]] = {}
        self.clients: list[dict[str, Any]] = []
        self._clients_loading = False
        self._clients_loaded = False
        self._native_settings_loading = False
        self.selected_service_ids: set[str] = set()
        self.service_batch_route_mode = ServiceRouteMode.SUGGESTED
        self.busy_count = 0
        self.dirty = False
        self._region_filter = "all"
        self._updating_service_selection = False
        self._updating_autostart = False
        self._updating_protocol = False
        self._updating_astrill_connection = False
        self._protocol_user_selected = False
        self._astrill_install_prompted = False
        self._companion_install_prompted = False
        self._ssh_setup_prompted = False
        self.router_install_buttons: list[Gtk.Button] = []

        self._install_css()
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)
        self.split_view = Adw.NavigationSplitView()
        self.toast_overlay.set_child(self.split_view)
        self._build_sidebar()
        self._build_content()
        self._render_rules()
        self._render_services()
        self._render_countries()
        self._render_extensions()
        self.native_page.set_read_only(self.store.read_only)
        self.connection_page.set_read_only(self.store.read_only)
        self.check_router_environment(quiet=False)
        self.load_servers()
        self.refresh_native_settings(
            quiet=True,
            force_native=True,
            force_connection=True,
        )

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_sidebar(self) -> None:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_title(False)
        toolbar.add_top_bar(header)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class("app-sidebar")
        brand = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        brand.add_css_class("brand-block")
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon = Gtk.Image.new_from_icon_name("network-vpn-symbolic")
        icon.set_pixel_size(28)
        title = Gtk.Label(label="Astrill Lazy")
        title.set_xalign(0)
        title.add_css_class("brand-title")
        title_row.append(icon)
        title_row.append(title)
        subtitle = Gtk.Label(label="Router policy control")
        subtitle.set_xalign(0)
        subtitle.add_css_class("brand-subtitle")
        brand.append(title_row)
        brand.append(subtitle)
        root.append(brand)

        self.nav_list = Gtk.ListBox()
        self.nav_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.nav_list.add_css_class("nav-list")
        self.nav_rows: dict[str, Gtk.ListBoxRow] = {}
        for page_id, title_text, icon_name in self.PAGE_DEFINITIONS:
            row = Gtk.ListBoxRow()
            row.page_id = page_id  # type: ignore[attr-defined]
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            box.add_css_class("nav-item")
            box.append(Gtk.Image.new_from_icon_name(icon_name))
            label = Gtk.Label(label=title_text)
            label.set_xalign(0)
            box.append(label)
            row.set_child(box)
            self.nav_list.append(row)
            self.nav_rows[page_id] = row
        self.nav_list.connect("row-selected", self._on_nav_selected)
        root.append(self.nav_list)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        root.append(spacer)
        sidebar_status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sidebar_status.add_css_class("sidebar-status")
        self.sidebar_status_icon = Gtk.Image.new_from_icon_name(
            "network-offline-symbolic"
        )
        self.sidebar_status_label = Gtk.Label(label="Checking router")
        self.sidebar_status_label.set_xalign(0)
        self.sidebar_status_label.set_hexpand(True)
        sidebar_status.append(self.sidebar_status_icon)
        sidebar_status.append(self.sidebar_status_label)
        root.append(sidebar_status)
        toolbar.set_content(root)

        page = Adw.NavigationPage.new(toolbar, "Navigation")
        self.split_view.set_sidebar(page)
        self.split_view.set_min_sidebar_width(230)
        self.split_view.set_max_sidebar_width(280)

    def _build_content(self) -> None:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title="Policies", subtitle="Router policy")
        header.set_title_widget(self.window_title)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text(
            "Refresh router status now (manual; no background polling)"
        )
        refresh.connect("clicked", lambda _button: self.refresh_router())
        header.pack_end(refresh)
        self.apply_button = _button_with_icon(
            "Apply", "document-send-symbolic", self.apply_configuration
        )
        self.apply_button.add_css_class("suggested-action")
        header.pack_end(self.apply_button)
        toolbar.add_top_bar(header)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._build_policies_page(), "policies")
        self.stack.add_named(self._build_services_page(), "services")
        self.stack.add_named(self._build_countries_page(), "countries")
        self.stack.add_named(self._build_devices_page(), "devices")
        self.stack.add_named(self._build_connection_page(), "connection")
        self.stack.add_named(self._build_locations_page(), "locations")
        self.stack.add_named(self._build_native_settings_page(), "astrill")
        self.stack.add_named(self._build_router_page(), "router")
        self.stack.add_named(self._build_extensions_page(), "extensions")
        toolbar.set_content(self.stack)
        self.split_view.set_content(Adw.NavigationPage.new(toolbar, "Content"))
        self.nav_list.select_row(self.nav_rows["policies"])

    def select_page(self, page_id: str) -> None:
        row = self.nav_rows.get(page_id)
        if row is not None:
            self.nav_list.select_row(row)

    def _build_policies_page(self) -> Gtk.Widget:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.add_css_class("page-content")
        self.access_banner = Adw.Banner()
        self.access_banner.set_title(
            "Read-only access: router changes and companion installation are disabled."
        )
        self.access_banner.set_revealed(self.store.read_only)
        content.append(self.access_banner)
        self.policy_banner = Adw.Banner()
        self.policy_banner.set_revealed(False)
        content.append(self.policy_banner)

        status_band = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            homogeneous=True,
        )
        status_band.add_css_class("status-band")
        self.metrics: dict[str, Gtk.Label] = {}
        for key, label in (
            ("health", "Controller"),
            ("tunnel", "Astrill tunnel"),
            ("location", "Active endpoint"),
            ("rules", "Enabled policies"),
        ):
            metric = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            metric.add_css_class("metric")
            value = Gtk.Label(label="...")
            value.set_xalign(0)
            value.set_ellipsize(Pango.EllipsizeMode.END)
            value.add_css_class("metric-value")
            caption = Gtk.Label(label=label)
            caption.set_xalign(0)
            caption.add_css_class("metric-label")
            metric.append(value)
            metric.append(caption)
            status_band.append(metric)
            self.metrics[key] = value
        content.append(status_band)

        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        heading.add_css_class("toolbar-section")
        label = Gtk.Label(label="Traffic Policies")
        label.set_xalign(0)
        label.set_hexpand(True)
        label.add_css_class("section-title")
        heading.append(label)
        self.detect_routes_button = _button_with_icon(
            "Detect", "network-transmit-receive-symbolic", self.detect_routes
        )
        self.detect_routes_button.set_tooltip_text(
            "Compare Direct and Astrill paths for enabled policies"
        )
        heading.append(self.detect_routes_button)
        self.apply_recommendations_button = _button_with_icon(
            "Apply Recommendations",
            "object-select-symbolic",
            self.apply_route_recommendations,
        )
        self.apply_recommendations_button.set_tooltip_text(
            "Use all current route recommendations"
        )
        heading.append(self.apply_recommendations_button)
        add_menu = Gtk.MenuButton()
        add_menu.set_child(_button_content("Add", "list-add-symbolic"))
        add_menu.set_tooltip_text("Add a policy")
        add_menu.set_popover(self._build_add_popover())
        heading.append(add_menu)
        content.append(heading)

        self.policy_list = Gtk.ListBox()
        self.policy_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.policy_list.add_css_class("policy-list")
        content.append(self.policy_list)
        content.append(_vertical_spacer())
        return _scroll_page(content)

    def _build_add_popover(self) -> Gtk.Popover:
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        actions = (
            ("Service", "view-app-grid-symbolic", self._show_services),
            ("Website", "web-browser-symbolic", self._show_domain_dialog),
            ("Device", "network-workgroup-symbolic", self._show_devices),
            ("Application", "system-run-symbolic", self._show_application_dialog),
            ("IP Network", "network-server-symbolic", self._show_network_dialog),
        )
        for label, icon, callback in actions:
            button = Gtk.Button()
            button.set_child(_button_content(label, icon, expand=True))
            button.connect("clicked", lambda _b, fn=callback: (popover.popdown(), fn()))
            box.append(button)
        popover.set_child(box)
        return popover

    def _build_services_page(self) -> Gtk.Widget:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.add_css_class("page-content")
        self.service_search = Gtk.SearchEntry()
        self.service_search.set_placeholder_text(
            "Search app, company, alias, or domain"
        )
        self.service_search.set_hexpand(True)
        self.service_search.connect(
            "search-changed", lambda _entry: self._render_services()
        )
        content.append(self.service_search)

        filters = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.category_dropdown = Gtk.DropDown()
        self.category_dropdown.set_tooltip_text("Filter by category")
        self.category_dropdown.connect(
            "notify::selected", lambda *_args: self._render_services()
        )
        filters.append(self.category_dropdown)
        self.profile_type_dropdown = Gtk.DropDown()
        self.profile_type_dropdown.set_tooltip_text("Filter by profile type")
        self.profile_type_dropdown.connect(
            "notify::selected", lambda *_args: self._render_services()
        )
        filters.append(self.profile_type_dropdown)
        self.service_country_dropdown = Gtk.DropDown()
        self.service_country_dropdown.set_tooltip_text("Filter by provider country")
        self.service_country_dropdown.connect(
            "notify::selected", lambda *_args: self._render_services()
        )
        filters.append(self.service_country_dropdown)
        self.service_result_count = Gtk.Label()
        self.service_result_count.set_xalign(1)
        self.service_result_count.set_hexpand(True)
        self.service_result_count.add_css_class("catalog-count")
        filters.append(self.service_result_count)
        content.append(filters)
        self._refresh_service_filters()

        batch = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        batch.add_css_class("batch-bar")
        self.select_visible_services = Gtk.CheckButton(label="Select visible")
        self.select_visible_services.set_tooltip_text(
            "Select every service matching the current filters"
        )
        self.select_visible_services.connect(
            "toggled", self._toggle_visible_service_selection
        )
        batch.append(self.select_visible_services)
        clear_selection = Gtk.Button.new_from_icon_name("edit-clear-all-symbolic")
        clear_selection.set_tooltip_text("Clear service selection")
        clear_selection.connect(
            "clicked", lambda _button: self._clear_service_selection()
        )
        batch.append(clear_selection)
        self.clear_service_selection_button = clear_selection
        self.service_selection_count = Gtk.Label(label="0 selected")
        self.service_selection_count.set_xalign(0)
        self.service_selection_count.set_hexpand(True)
        self.service_selection_count.add_css_class("batch-count")
        batch.append(self.service_selection_count)

        route_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        route_box.add_css_class("linked")
        self.service_batch_route_buttons: dict[ServiceRouteMode, Gtk.ToggleButton] = {}
        first_route_button: Gtk.ToggleButton | None = None
        for mode, label in (
            (ServiceRouteMode.SUGGESTED, "Suggested"),
            (ServiceRouteMode.DIRECT, "Direct"),
            (ServiceRouteMode.VPN, "Astrill"),
        ):
            button = Gtk.ToggleButton(label=label)
            button.add_css_class("compact-button")
            if mode is ServiceRouteMode.DIRECT:
                button.add_css_class("route-direct")
            elif mode is ServiceRouteMode.VPN:
                button.add_css_class("route-vpn")
            if first_route_button is None:
                first_route_button = button
            else:
                button.set_group(first_route_button)
            button.connect(
                "toggled",
                lambda item, route_mode=mode: self._set_service_batch_route(
                    item, route_mode
                ),
            )
            route_box.append(button)
            self.service_batch_route_buttons[mode] = button
        self.service_batch_route_buttons[ServiceRouteMode.SUGGESTED].set_active(True)
        route_box.set_valign(Gtk.Align.CENTER)
        batch.append(route_box)
        self.add_selected_services_button = _button_with_icon(
            "Add to Policies",
            "list-add-symbolic",
            self._add_selected_services,
        )
        self.add_selected_services_button.add_css_class("suggested-action")
        self.add_selected_services_button.set_tooltip_text(
            "Add new selected services and update selected existing policies"
        )
        batch.append(self.add_selected_services_button)
        content.append(batch)

        self.service_list = Gtk.ListBox()
        self.service_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.service_list.add_css_class("catalog-list")
        content.append(self.service_list)
        content.append(_vertical_spacer())
        return _scroll_page(content)

    def _build_countries_page(self) -> Gtk.Widget:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.add_css_class("page-content")
        self.country_banner = Adw.Banner()
        self.country_banner.set_revealed(False)
        content.append(self.country_banner)

        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Country Routes")
        title.set_xalign(0)
        title.set_hexpand(True)
        title.add_css_class("section-title")
        heading.append(title)
        self.country_result_count = Gtk.Label()
        self.country_result_count.add_css_class("catalog-count")
        heading.append(self.country_result_count)
        content.append(heading)

        self.country_list = Gtk.ListBox()
        self.country_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.country_list.add_css_class("catalog-list")
        content.append(self.country_list)
        content.append(_vertical_spacer())
        return _scroll_page(content)

    def _build_devices_page(self) -> Gtk.Widget:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.add_css_class("page-content")
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="LAN Devices")
        title.set_xalign(0)
        title.set_hexpand(True)
        title.add_css_class("section-title")
        heading.append(title)
        manual = _button_with_icon(
            "Manual IP", "list-add-symbolic", self._show_device_dialog
        )
        heading.append(manual)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Reload observed LAN devices")
        refresh.connect("clicked", lambda _button: self.refresh_clients())
        heading.append(refresh)
        content.append(heading)
        self.device_list = Gtk.ListBox()
        self.device_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.device_list.add_css_class("catalog-list")
        content.append(self.device_list)
        content.append(_vertical_spacer())
        self._render_devices()
        return _scroll_page(content)

    def _build_connection_page(self) -> Gtk.Widget:
        self.connection_page = AstrillConnectionPage(
            on_refresh=self.confirm_refresh_connection,
            on_save=self.save_astrill_connection,
            on_connect=self.confirm_connect_astrill,
            on_disconnect=lambda: self._change_astrill_connection(False),
        )
        return _scroll_page(self.connection_page)

    def _build_locations_page(self) -> Gtk.Widget:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.add_css_class("page-content")
        banner = Adw.Banner(
            title="One Astrill tunnel is available; all VPN policies share the active endpoint."
        )
        banner.set_revealed(True)
        content.append(banner)
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.location_search = Gtk.SearchEntry()
        self.location_search.set_placeholder_text("Search Astrill endpoints")
        self.location_search.set_hexpand(True)
        self.location_search.connect(
            "search-changed", lambda _entry: self._render_locations()
        )
        controls.append(self.location_search)
        filter_regions = [
            Region("all", "All regions", "astrill"),
            *self.catalog.regions,
        ]
        filter_regions = [region for region in filter_regions if region.id != "direct"]
        self.location_filter_regions = filter_regions
        self.location_filter = Gtk.DropDown.new_from_strings(
            [region.name for region in filter_regions]
        )
        self.location_filter.connect("notify::selected", self._on_location_filter)
        controls.append(self.location_filter)
        self.protocol_dropdown = Gtk.DropDown.new_from_strings(
            list(ASTRILL_PROTOCOL_NAMES)
        )
        self.protocol_dropdown.set_size_request(180, -1)
        self.protocol_dropdown.set_tooltip_text(
            "Protocol for the next Astrill connection"
        )
        self.protocol_dropdown.connect("notify::selected", self._on_protocol_selected)
        controls.append(self.protocol_dropdown)
        content.append(controls)
        self.location_list = Gtk.ListBox()
        self.location_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.location_list.add_css_class("catalog-list")
        content.append(self.location_list)
        content.append(_vertical_spacer())
        self._render_locations()
        return _scroll_page(content)

    def _build_router_page(self) -> Gtk.Widget:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.add_css_class("page-content")

        connection_heading = Gtk.Label(label="Router Connection")
        connection_heading.set_xalign(0)
        connection_heading.add_css_class("section-title")
        content.append(connection_heading)
        connection_list = Gtk.ListBox()
        connection_list.set_selection_mode(Gtk.SelectionMode.NONE)
        connection_list.add_css_class("catalog-list")

        self.router_host_entry = Gtk.Entry()
        self.router_host_entry.set_text(self.store.router_host)
        self.router_host_entry.set_width_chars(20)
        self.router_host_entry.set_max_length(255)
        host_row = Adw.ActionRow(
            title="Router address",
            subtitle="IPv4 address, DNS name, or OpenSSH host alias",
        )
        host_row.add_prefix(Gtk.Image.new_from_icon_name("network-server-symbolic"))
        host_row.add_suffix(self.router_host_entry)
        connection_list.append(host_row)

        self.router_user_entry = Gtk.Entry()
        self.router_user_entry.set_text(self.store.router_user)
        self.router_user_entry.set_width_chars(12)
        self.router_user_entry.set_max_length(64)
        user_row = Adw.ActionRow(
            title="SSH user",
            subtitle="DD-WRT administrative shell account",
        )
        user_row.add_prefix(Gtk.Image.new_from_icon_name("avatar-default-symbolic"))
        user_row.add_suffix(self.router_user_entry)
        connection_list.append(user_row)

        self.router_port_entry = Gtk.Entry()
        self.router_port_entry.set_text(str(self.store.router_port))
        self.router_port_entry.set_width_chars(7)
        self.router_port_entry.set_max_length(5)
        port_row = Adw.ActionRow(title="SSH port", subtitle="LAN management port")
        port_row.add_prefix(Gtk.Image.new_from_icon_name("network-wired-symbolic"))
        port_row.add_suffix(self.router_port_entry)
        connection_list.append(port_row)

        self.router_identity_entry = Gtk.Entry()
        self.router_identity_entry.set_text(self.store.router_identity)
        self.router_identity_entry.set_width_chars(28)
        self.router_identity_entry.set_max_length(4096)
        identity_row = Adw.ActionRow(
            title="Dedicated identity",
            subtitle="Generated locally; the private key never leaves this computer",
        )
        identity_row.add_prefix(Gtk.Image.new_from_icon_name("channel-secure-symbolic"))
        identity_row.add_suffix(self.router_identity_entry)
        connection_list.append(identity_row)

        self.router_ssh_row = Adw.ActionRow(
            title="Key-only SSH",
            subtitle=self.ssh_setup_error or "Checking router access",
        )
        self.router_ssh_row.set_use_markup(False)
        self.router_ssh_icon = Gtk.Image.new_from_icon_name("network-offline-symbolic")
        self.router_ssh_row.add_prefix(self.router_ssh_icon)
        save_connection = _button_with_icon(
            "Save & Check",
            "document-save-symbolic",
            self._save_router_connection,
        )
        save_connection.set_valign(Gtk.Align.CENTER)
        self.router_ssh_row.add_suffix(save_connection)
        authorize_key = _button_with_icon(
            "Authorize Key",
            "dialog-password-symbolic",
            self.confirm_authorize_router_key,
        )
        authorize_key.set_valign(Gtk.Align.CENTER)
        self.router_ssh_row.add_suffix(authorize_key)
        connection_list.append(self.router_ssh_row)
        content.append(connection_list)

        astrill_heading = Gtk.Label(label="Astrill Connection")
        astrill_heading.set_xalign(0)
        astrill_heading.add_css_class("section-title")
        astrill_heading.add_css_class("toolbar-section")
        content.append(astrill_heading)
        self.router_astrill_row = Adw.ActionRow(
            title="Shared tunnel",
            subtitle="Checking Astrill",
        )
        self.router_astrill_row.set_use_markup(False)
        self.router_astrill_icon = Gtk.Image.new_from_icon_name(
            "network-offline-symbolic"
        )
        self.router_astrill_row.add_prefix(self.router_astrill_icon)
        self.astrill_install_button = _button_with_icon(
            "Install Applet",
            "software-update-available-symbolic",
            lambda _button: self.confirm_install_astrill(force=True),
        )
        self.astrill_install_button.set_valign(Gtk.Align.CENTER)
        self.router_astrill_row.add_suffix(self.astrill_install_button)
        self.choose_location_button = _button_with_icon(
            "Configure",
            "preferences-system-symbolic",
            lambda _button: self._show_connection(),
        )
        self.choose_location_button.set_valign(Gtk.Align.CENTER)
        self.router_astrill_row.add_suffix(self.choose_location_button)
        self.astrill_connection_switch = Gtk.Switch()
        self.astrill_connection_switch.set_valign(Gtk.Align.CENTER)
        self.astrill_connection_switch.set_tooltip_text("Connect or disconnect Astrill")
        self.astrill_connection_switch.connect(
            "notify::active", self._toggle_astrill_connection
        )
        self.router_astrill_row.add_suffix(self.astrill_connection_switch)
        astrill_list = Gtk.ListBox()
        astrill_list.set_selection_mode(Gtk.SelectionMode.NONE)
        astrill_list.add_css_class("catalog-list")
        astrill_list.append(self.router_astrill_row)
        content.append(astrill_list)

        companion_heading = Gtk.Label(label="Router Companion")
        companion_heading.set_xalign(0)
        companion_heading.add_css_class("section-title")
        companion_heading.add_css_class("toolbar-section")
        content.append(companion_heading)
        companion_list = Gtk.ListBox()
        companion_list.set_selection_mode(Gtk.SelectionMode.NONE)
        companion_list.add_css_class("catalog-list")

        self.router_runtime_row = Adw.ActionRow(
            title="Policy runtime",
            subtitle="Checking companion",
        )
        self.router_runtime_row.set_use_markup(False)
        self.router_runtime_icon = Gtk.Image.new_from_icon_name(
            "network-offline-symbolic"
        )
        self.router_runtime_row.add_prefix(self.router_runtime_icon)
        self.router_repair_button = _button_with_icon(
            "Repair",
            "system-run-symbolic",
            self.reconcile_router,
        )
        self.router_repair_button.set_valign(Gtk.Align.CENTER)
        self.router_runtime_row.add_suffix(self.router_repair_button)
        companion_list.append(self.router_runtime_row)

        self.router_domain_row = Adw.ActionRow(
            title="Domain routes",
            subtitle="No resolution status",
        )
        self.router_domain_row.set_use_markup(False)
        self.router_domain_row.add_prefix(
            Gtk.Image.new_from_icon_name("network-workgroup-symbolic")
        )
        self.router_refresh_domains_button = _button_with_icon(
            "Refresh",
            "view-refresh-symbolic",
            self.refresh_domains,
        )
        self.router_refresh_domains_button.set_valign(Gtk.Align.CENTER)
        self.router_domain_row.add_suffix(self.router_refresh_domains_button)
        companion_list.append(self.router_domain_row)

        rollback_row = Adw.ActionRow(
            title="Previous policy",
            subtitle="Last successful router document",
        )
        rollback_row.set_use_markup(False)
        rollback_row.add_prefix(Gtk.Image.new_from_icon_name("edit-undo-symbolic"))
        self.router_rollback_button = _button_with_icon(
            "Roll Back",
            "edit-undo-symbolic",
            self.confirm_rollback,
        )
        self.router_rollback_button.set_valign(Gtk.Align.CENTER)
        rollback_row.add_suffix(self.router_rollback_button)
        companion_list.append(rollback_row)

        package_row = Adw.ActionRow(
            title="Companion package",
            subtitle=f"Expected version {RouterInstaller(self.router).expected_version}",
        )
        package_row.set_use_markup(False)
        package_row.add_prefix(
            Gtk.Image.new_from_icon_name("package-x-generic-symbolic")
        )
        install = _button_with_icon(
            "Install / Upgrade",
            "software-update-available-symbolic",
            self.install_router,
        )
        install.set_valign(Gtk.Align.CENTER)
        self.router_install_buttons.append(install)
        package_row.add_suffix(install)
        self.restore_native_button = _button_with_icon(
            "Restore Astrill Only",
            "edit-delete-symbolic",
            self.confirm_restore_native,
        )
        self.restore_native_button.add_css_class("destructive-action")
        self.restore_native_button.set_valign(Gtk.Align.CENTER)
        package_row.add_suffix(self.restore_native_button)
        companion_list.append(package_row)

        content.append(companion_list)
        content.append(_vertical_spacer())
        return _scroll_page(content)

    def _build_native_settings_page(self) -> Gtk.Widget:
        self.native_page = NativeSettingsPage(
            on_refresh=lambda: self.refresh_native_settings(force_native=True),
            on_save=self.save_native_settings,
        )
        return _scroll_page(self.native_page)

    def _build_extensions_page(self) -> Gtk.Widget:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.add_css_class("page-content")
        heading = Gtk.Label(label="Installed Extensions")
        heading.set_xalign(0)
        heading.add_css_class("section-title")
        content.append(heading)
        self.extension_list = Gtk.ListBox()
        self.extension_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.extension_list.add_css_class("catalog-list")
        content.append(self.extension_list)

        desktop_heading = Gtk.Label(label="Desktop")
        desktop_heading.set_xalign(0)
        desktop_heading.add_css_class("section-title")
        desktop_heading.add_css_class("toolbar-section")
        content.append(desktop_heading)
        autostart_row = Adw.ActionRow(
            title="Launch at login",
            subtitle="Start the controller and reconcile the router companion",
        )
        autostart_row.set_use_markup(False)
        autostart_row.add_prefix(Gtk.Image.new_from_icon_name("system-run-symbolic"))
        self.autostart_switch = Gtk.Switch(active=is_autostart_enabled())
        self.autostart_switch.set_valign(Gtk.Align.CENTER)
        self.autostart_switch.set_tooltip_text("Launch Astrill Lazy after login")
        self.autostart_switch.connect("notify::active", self._toggle_autostart)
        autostart_row.add_suffix(self.autostart_switch)
        desktop_list = Gtk.ListBox()
        desktop_list.set_selection_mode(Gtk.SelectionMode.NONE)
        desktop_list.add_css_class("catalog-list")
        desktop_list.append(autostart_row)
        content.append(desktop_list)

        router_heading = Gtk.Label(label="Router Companion")
        router_heading.set_xalign(0)
        router_heading.add_css_class("section-title")
        router_heading.add_css_class("toolbar-section")
        content.append(router_heading)
        self.router_companion_row = Adw.ActionRow(
            title="DD-WRT MyPage plugin",
            subtitle="Persistent controller, watchdog, MyPage, and automatic repair",
        )
        self.router_companion_row.set_use_markup(False)
        self.router_companion_icon = Gtk.Image.new_from_icon_name(
            "network-offline-symbolic"
        )
        self.router_companion_row.add_prefix(self.router_companion_icon)
        install = _button_with_icon(
            "Install / Upgrade",
            "software-update-available-symbolic",
            self.install_router,
        )
        install.set_valign(Gtk.Align.CENTER)
        self.router_install_buttons.append(install)
        self.router_companion_row.add_suffix(install)
        companion_list = Gtk.ListBox()
        companion_list.set_selection_mode(Gtk.SelectionMode.NONE)
        companion_list.add_css_class("catalog-list")
        companion_list.append(self.router_companion_row)
        content.append(companion_list)
        content.append(_vertical_spacer())
        return _scroll_page(content)

    def _on_nav_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        page_id = row.page_id  # type: ignore[attr-defined]
        self.stack.set_visible_child_name(page_id)
        title = next(item[1] for item in self.PAGE_DEFINITIONS if item[0] == page_id)
        subtitles = {
            "policies": "Direct or current Astrill routing",
            "services": f"{len(self.catalog.services)} service profiles",
            "countries": "Policy countries on one shared tunnel",
            "devices": "Observed LAN clients and fixed addresses",
            "connection": "Native endpoint and transport settings",
            "locations": "Choose the shared Astrill server",
            "astrill": "Native routing, DNS, and effective routes",
            "router": "Runtime status and recovery",
            "extensions": "Catalog and router components",
        }
        self.window_title.set_title(title)
        self.window_title.set_subtitle(subtitles[page_id])
        if page_id in {"devices", "astrill"} and not self._clients_loaded:
            self.refresh_clients()
        if page_id in {"connection", "locations"} and self.servers is None:
            self.load_servers()
        if page_id == "services":
            self._render_services()
        if page_id == "astrill" and self.native_settings is None:
            self.refresh_native_settings(quiet=True, force_native=True)
        if page_id == "connection" and self.native_settings is None:
            self.refresh_native_settings(quiet=True, force_connection=True)

    def _show_services(self) -> None:
        self.nav_list.select_row(self.nav_rows["services"])

    def _show_devices(self) -> None:
        self.nav_list.select_row(self.nav_rows["devices"])

    def _show_locations(self) -> None:
        self.nav_list.select_row(self.nav_rows["locations"])

    def _show_connection(self) -> None:
        self.nav_list.select_row(self.nav_rows["connection"])

    def _render_rules(self) -> None:
        _clear_list(self.policy_list)
        if not self.store.rules:
            self.policy_list.append(
                _empty_row(
                    "No policies", "Add a service, website, device, or application."
                )
            )
            self._update_recommendation_controls()
            return
        regions = list(self.catalog.regions)
        region_names = [region.name for region in regions]
        region_ids = [region.id for region in regions]
        for rule in sorted(
            self.store.rules, key=lambda item: (item.priority, item.name)
        ):
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(rule.name)
            subtitle = f"{_kind_label(rule.match_kind)} · {rule.selector}"
            if rule.match_kind is MatchKind.PROCESS:
                address = str(rule.metadata.get("namespace_ip", ""))
                subtitle = f"Application · {address or 'identity not prepared'}"
            recommendation = rule.metadata.get("route_recommendation")
            if isinstance(recommendation, dict):
                target = str(recommendation.get("target", "")).capitalize()
                direct = _latency_label(recommendation.get("direct_ms"))
                astrill = _latency_label(recommendation.get("astrill_ms"))
                reason = str(recommendation.get("reason", ""))
                state = "Applied" if recommendation.get("applied") else "Recommended"
                subtitle += (
                    f"\n{state}: {target} · Direct {direct} · "
                    f"Astrill {astrill} · {reason}"
                )
            row.set_subtitle(subtitle)
            row.add_prefix(Gtk.Image.new_from_icon_name(_kind_icon(rule.match_kind)))

            if rule.match_kind is MatchKind.PROCESS:
                launch = Gtk.Button.new_from_icon_name("media-playback-start-symbolic")
                launch.set_tooltip_text("Prepare identity and launch application")
                launch.set_valign(Gtk.Align.CENTER)
                launch.connect(
                    "clicked", lambda _button, item=rule: self.launch_app(item)
                )
                row.add_suffix(launch)

            dropdown = Gtk.DropDown.new_from_strings(region_names)
            dropdown.set_size_request(170, -1)
            dropdown.set_valign(Gtk.Align.CENTER)
            try:
                dropdown.set_selected(region_ids.index(rule.region))
            except ValueError:
                dropdown.set_selected(region_ids.index("active-astrill"))
            dropdown.set_sensitive(rule.target is RouteTarget.VPN)
            dropdown.connect(
                "notify::selected",
                lambda widget, _param, item=rule, ids=region_ids: self._set_rule_region(
                    item, ids[widget.get_selected()]
                ),
            )
            row.add_suffix(dropdown)

            route_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            route_box.add_css_class("linked")
            direct = Gtk.ToggleButton(label="Direct")
            direct.add_css_class("compact-button")
            direct.add_css_class("route-direct")
            vpn = Gtk.ToggleButton(label="Astrill")
            vpn.add_css_class("compact-button")
            vpn.add_css_class("route-vpn")
            vpn.set_group(direct)
            direct.set_active(rule.target is RouteTarget.DIRECT)
            vpn.set_active(rule.target is RouteTarget.VPN)
            direct.connect(
                "toggled",
                lambda button, item=rule, location=dropdown: (
                    button.get_active()
                    and self._set_rule_target(item, RouteTarget.DIRECT, location)
                ),
            )
            vpn.connect(
                "toggled",
                lambda button, item=rule, location=dropdown: (
                    button.get_active()
                    and self._set_rule_target(item, RouteTarget.VPN, location)
                ),
            )
            route_box.append(direct)
            route_box.append(vpn)
            route_box.set_valign(Gtk.Align.CENTER)
            row.add_suffix(route_box)

            enabled = Gtk.Switch(active=rule.enabled)
            enabled.set_valign(Gtk.Align.CENTER)
            enabled.set_tooltip_text("Enable policy")
            enabled.connect(
                "notify::active",
                lambda switch, _param, item=rule: self._set_rule_enabled(
                    item, switch.get_active()
                ),
            )
            row.add_suffix(enabled)
            delete = Gtk.Button.new_from_icon_name("edit-delete-symbolic")
            delete.set_tooltip_text("Delete policy")
            delete.set_valign(Gtk.Align.CENTER)
            delete.connect(
                "clicked", lambda _button, item=rule: self._delete_rule(item)
            )
            row.add_suffix(delete)
            self.policy_list.append(row)
        self._update_recommendation_controls()

    def _render_services(self) -> None:
        if not hasattr(self, "service_list"):
            return
        _clear_list(self.service_list)
        existing = {
            rule.selector: rule
            for rule in self.store.rules
            if rule.match_kind is MatchKind.SERVICE
        }
        services = self._filtered_services()
        self.service_result_count.set_label(
            f"{len(services)} of {len(self.catalog.services)}"
        )
        for service in services:
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(service.name)
            row.set_subtitle(
                f"{service.company} · {service.provider_country} · {service.category} · "
                f"{service.profile_type.title()} · {len(service.domains)} domains"
            )
            selected = Gtk.CheckButton()
            selected.set_tooltip_text("Select for a batch policy action")
            selected.set_active(service.id in self.selected_service_ids)
            selected.connect(
                "toggled",
                lambda button, service_id=service.id: self._toggle_service_selection(
                    service_id, button.get_active()
                ),
            )
            row.add_prefix(selected)
            row.add_prefix(
                Gtk.Image.new_from_icon_name(_category_icon(service.category))
            )
            existing_rule = existing.get(service.id)
            displayed_target = (
                existing_rule.target
                if existing_rule is not None
                else service.default_route
            )
            route = Gtk.Label(
                label=(
                    "DIRECT" if displayed_target is RouteTarget.DIRECT else "ASTRILL"
                )
            )
            route.set_tooltip_text(
                "Current policy route"
                if existing_rule is not None
                else "Catalog suggestion"
            )
            route.add_css_class("catalog-route")
            route.add_css_class(
                "catalog-direct"
                if displayed_target is RouteTarget.DIRECT
                else "catalog-vpn"
            )
            row.add_suffix(route)
            add = Gtk.Button.new_from_icon_name(
                "object-select-symbolic"
                if existing_rule is not None
                else "list-add-symbolic"
            )
            add.set_tooltip_text(
                "Policy already added"
                if existing_rule is not None
                else "Add service policy"
            )
            add.set_sensitive(existing_rule is None)
            add.set_valign(Gtk.Align.CENTER)
            add.connect(
                "clicked",
                lambda _button, service_id=service.id: self._add_service(service_id),
            )
            row.add_suffix(add)
            self.service_list.append(row)
        if not services:
            self.service_list.append(
                _empty_row(
                    "No matching services",
                    "Change the country, category, profile type, or search text.",
                )
            )
        self._update_service_batch_controls(services)

    def _refresh_service_filters(self) -> None:
        self.service_categories = [
            "all",
            *sorted({item.category for item in self.catalog.services}),
        ]
        self.service_profile_types = ["all", "company", "app", "website"]
        self.service_countries = [
            "all",
            *sorted(
                {item.provider_country for item in self.catalog.services},
                key=str.casefold,
            ),
        ]
        self.category_dropdown.set_model(
            Gtk.StringList.new(
                [
                    "All categories",
                    *self.service_categories[1:],
                ]
            )
        )
        self.category_dropdown.set_selected(0)
        self.profile_type_dropdown.set_model(
            Gtk.StringList.new(["All profiles", "Companies", "Apps", "Websites"])
        )
        self.profile_type_dropdown.set_selected(0)
        self.service_country_dropdown.set_model(
            Gtk.StringList.new(["All countries", *self.service_countries[1:]])
        )
        self.service_country_dropdown.set_selected(0)
        self.selected_service_ids.intersection_update(
            service.id for service in self.catalog.services
        )

    def _filtered_services(self) -> list[Service]:
        query = self.service_search.get_text().strip().casefold()
        category = self.service_categories[self.category_dropdown.get_selected()]
        profile_type = self.service_profile_types[
            self.profile_type_dropdown.get_selected()
        ]
        country = self.service_countries[self.service_country_dropdown.get_selected()]
        services = [
            service
            for service in self.catalog.services
            if (not query or query in service.search_text)
            and (category == "all" or service.category == category)
            and (profile_type == "all" or service.profile_type == profile_type)
            and (country == "all" or service.provider_country == country)
        ]
        services.sort(
            key=lambda service: (service.company.casefold(), service.name.casefold())
        )
        return services

    def _toggle_service_selection(self, service_id: str, selected: bool) -> None:
        if self._updating_service_selection:
            return
        if selected:
            self.selected_service_ids.add(service_id)
        else:
            self.selected_service_ids.discard(service_id)
        self._update_service_batch_controls()

    def _toggle_visible_service_selection(self, button: Gtk.CheckButton) -> None:
        if self._updating_service_selection:
            return
        visible_ids = {service.id for service in self._filtered_services()}
        if button.get_active():
            self.selected_service_ids.update(visible_ids)
        else:
            self.selected_service_ids.difference_update(visible_ids)
        self._render_services()

    def _clear_service_selection(self) -> None:
        self.selected_service_ids.clear()
        self._render_services()

    def _update_service_batch_controls(
        self, visible_services: list[Service] | None = None
    ) -> None:
        if not hasattr(self, "select_visible_services"):
            return
        services = (
            self._filtered_services() if visible_services is None else visible_services
        )
        visible_ids = {service.id for service in services}
        selected_visible = visible_ids & self.selected_service_ids
        all_visible_selected = bool(visible_ids) and selected_visible == visible_ids
        partially_selected = bool(selected_visible) and not all_visible_selected
        self._updating_service_selection = True
        self.select_visible_services.set_inconsistent(partially_selected)
        self.select_visible_services.set_active(all_visible_selected)
        self._updating_service_selection = False
        count = len(self.selected_service_ids)
        self.service_selection_count.set_label(
            f"{count} {'service' if count == 1 else 'services'} selected"
        )
        self.clear_service_selection_button.set_sensitive(count > 0)
        self.add_selected_services_button.set_sensitive(count > 0)

    def _set_service_batch_route(
        self, button: Gtk.ToggleButton, mode: ServiceRouteMode
    ) -> None:
        if button.get_active():
            self.service_batch_route_mode = mode

    def _add_selected_services(self, _button: Gtk.Button | None = None) -> None:
        selected_services = [
            service
            for service in self.catalog.services
            if service.id in self.selected_service_ids
        ]
        if not selected_services:
            self.toast("Select at least one service")
            return

        existing = {
            rule.selector: rule
            for rule in self.store.rules
            if rule.match_kind is MatchKind.SERVICE
        }
        vpn_region_ids = {region.id for region in self._vpn_regions()}
        added = 0
        updated = 0
        for service in selected_services:
            rule = existing.get(service.id)
            current_region: str | None = None
            if rule is not None:
                current_region = rule.region
                if current_region == "direct":
                    remembered = str(rule.metadata.get("country_override", ""))
                    current_region = (
                        remembered if remembered in vpn_region_ids else None
                    )
            target, region = service_policy_route(
                service,
                self.service_batch_route_mode,
                current_region=current_region,
            )
            if rule is None:
                rule = Rule.create(
                    name=service.name,
                    match_kind=MatchKind.SERVICE,
                    selector=service.id,
                    target=target,
                    region=region,
                    priority=self._next_priority(),
                )
                if service.id in MINIMUM_BYPASS_SERVICES:
                    rule.metadata["minimum_bypass"] = True
                self.store.rules.append(rule)
                existing[service.id] = rule
                added += 1
                continue

            if rule.target is target and rule.region == region:
                continue
            if target is RouteTarget.DIRECT and rule.region != "direct":
                rule.metadata["country_override"] = rule.region
            elif target is RouteTarget.VPN:
                rule.metadata["country_override"] = region
            rule.target = target
            rule.region = region
            rule.metadata.pop("route_recommendation", None)
            updated += 1

        self.selected_service_ids.clear()
        if not added and not updated:
            self._render_services()
            self.toast("Selected policies already use this route")
            return
        self._changed()
        self._render_rules()
        self._render_services()
        summary = []
        if added:
            summary.append(f"{added} added")
        if updated:
            summary.append(f"{updated} updated")
        self.toast(f"Service policies: {', '.join(summary)}")

    def _render_devices(self) -> None:
        if not hasattr(self, "device_list"):
            return
        _clear_list(self.device_list)
        if not self.clients:
            self.device_list.append(
                _empty_row(
                    "No devices found",
                    "No DHCP lease, active LAN neighbor, or static reservation was found.",
                )
            )
            return
        for client in sorted(
            self.clients, key=lambda item: (item.get("hostname", ""), item["address"])
        ):
            raw_hostname = str(client.get("hostname", "")).strip()
            hostname = (
                "Unknown device"
                if raw_hostname.casefold() in {"", "*", "unknown"}
                else raw_hostname
            )
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(hostname)
            sources = {
                "dhcp": "DHCP lease",
                "arp": "LAN neighbor",
                "static": "Static reservation",
            }
            source_labels = [
                sources.get(source, source.title())
                for source in str(client.get("source", "")).split(",")
                if source
            ]
            details = [str(client["address"]), str(client["mac"])]
            if client.get("active") is True:
                details.append("Online")
            details.extend(source_labels)
            row.set_subtitle(" · ".join(details))
            row.add_prefix(Gtk.Image.new_from_icon_name("network-computer-symbolic"))
            for target, label in (
                (RouteTarget.DIRECT, "Direct"),
                (RouteTarget.VPN, "Astrill"),
            ):
                button = Gtk.Button(label=label)
                button.add_css_class("compact-button")
                button.set_valign(Gtk.Align.CENTER)
                button.connect(
                    "clicked",
                    lambda _button, address=client["address"], name=hostname, route=target: (
                        self._add_device(address, name, route)
                    ),
                )
                row.add_suffix(button)
            self.device_list.append(row)

    def _render_countries(self) -> None:
        if not hasattr(self, "country_list"):
            return
        _clear_list(self.country_list)
        enabled_rules = [rule for rule in self.store.rules if rule.enabled]
        rules_by_region = {
            region.id: [rule for rule in enabled_rules if rule.region == region.id]
            for region in self.catalog.regions
        }
        requested = {
            rule.region
            for rule in enabled_rules
            if rule.target is RouteTarget.VPN
            and rule.region not in {"direct", "active-astrill"}
        }
        region_names = self.catalog.regions_by_id
        tunnel_connected = self.router_status.get("vpn_state") == "up"
        server = self._server_by_id(int(self.router_status.get("astrill_server_id", 0)))
        active_region = (
            self._region_for_server(server)
            if tunnel_connected and server is not None
            else None
        )

        if len(requested) > 1:
            names = ", ".join(
                region_names[region_id].name for region_id in sorted(requested)
            )
            self.country_banner.set_title(
                f"Country conflict: {names} cannot be active on one tunnel."
            )
            self.country_banner.set_revealed(True)
        elif requested and active_region not in requested:
            requested_id = next(iter(requested))
            requested_name = region_names[requested_id].name
            active_name = (
                region_names[active_region].name
                if active_region in region_names
                else "no connected endpoint"
            )
            self.country_banner.set_title(
                f"Policies request {requested_name}; active country is {active_name}."
            )
            self.country_banner.set_revealed(True)
        else:
            self.country_banner.set_revealed(False)

        assigned_count = 0
        for region in self.catalog.regions:
            policies = rules_by_region[region.id]
            assigned_count += len(policies)
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(region.name)
            policy_text = _policy_summary(policies)
            if region.kind == "direct":
                endpoint_text = "WAN gateway"
                icon_name = "network-wired-symbolic"
            elif region.id == "active-astrill":
                endpoint_text = "Selected Astrill endpoint"
                icon_name = "network-vpn-symbolic"
            else:
                endpoint_count = len(self.server_groups.get(region.id, ()))
                endpoint_text = (
                    f"{endpoint_count} "
                    f"{'endpoint' if endpoint_count == 1 else 'endpoints'}"
                    if self.servers is not None
                    else "Loading endpoints"
                )
                icon_name = "mark-location-symbolic"
            row.set_subtitle(f"{policy_text} · {endpoint_text}")
            row.add_prefix(Gtk.Image.new_from_icon_name(icon_name))

            if region.id == active_region:
                active = Gtk.Label(label="ACTIVE")
                active.add_css_class("catalog-route")
                active.add_css_class("catalog-vpn")
                active.set_valign(Gtk.Align.CENTER)
                row.add_suffix(active)

            if region.kind != "direct":
                endpoints = Gtk.Button.new_from_icon_name("go-next-symbolic")
                endpoints.set_tooltip_text(f"Show {region.name} endpoints")
                endpoints.set_valign(Gtk.Align.CENTER)
                endpoints.connect(
                    "clicked",
                    lambda _button, region_id=region.id: self._open_region_endpoints(
                        region_id
                    ),
                )
                row.add_suffix(endpoints)
            self.country_list.append(row)

        self.country_result_count.set_label(
            f"{assigned_count} enabled "
            f"{'policy' if assigned_count == 1 else 'policies'}"
        )

    def _render_locations(self) -> None:
        if not hasattr(self, "location_list"):
            return
        _clear_list(self.location_list)
        if self.servers is None:
            self.location_list.append(
                _empty_row(
                    "Endpoints not loaded", "Open this page to read the Astrill applet."
                )
            )
            return
        query = self.location_search.get_text().strip().casefold()
        region_id = self._region_filter
        allowed = (
            {server.id for server in self.server_groups.get(region_id, ())}
            if region_id not in {"all", "active-astrill"}
            else None
        )
        current_id = int(self.router_status.get("astrill_server_id", 0))
        tunnel_connected = self.router_status.get("vpn_state") == "up"
        visible = [
            server
            for server in self.servers
            if (not query or query in server.name.casefold())
            and (allowed is None or server.id in allowed)
        ]
        for server in visible:
            configured = server.id == current_id
            connected = configured and tunnel_connected
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(server.name)
            row.set_subtitle(
                f"Server {server.id} · {len(server.nodes)} endpoint groups"
            )
            if connected:
                row.add_css_class("location-current")
                current = Gtk.Image.new_from_icon_name("object-select-symbolic")
                current.set_tooltip_text("Current Astrill endpoint")
                row.add_prefix(current)
            elif configured:
                configured_icon = Gtk.Image.new_from_icon_name(
                    "network-offline-symbolic"
                )
                configured_icon.set_tooltip_text(
                    "Configured endpoint; Astrill is disconnected"
                )
                row.add_prefix(configured_icon)
            else:
                row.add_prefix(Gtk.Image.new_from_icon_name("network-vpn-symbolic"))
            connect = Gtk.Button(label="Connected" if connected else "Connect")
            connect.add_css_class("compact-button")
            connect.set_sensitive(not self.store.read_only and not connected)
            connect.set_valign(Gtk.Align.CENTER)
            connect.connect(
                "clicked",
                lambda _button, item=server: self._confirm_switch_server(item),
            )
            row.add_suffix(connect)
            self.location_list.append(row)
        if not visible:
            self.location_list.append(
                _empty_row(
                    "No matching endpoints", "Change the country or search text."
                )
            )

    def _render_extensions(self) -> None:
        if not hasattr(self, "extension_list"):
            return
        _clear_list(self.extension_list)
        available = discover_extensions()
        enabled = set(self.store.enabled_extensions)
        for extension in available.values():
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(extension.name)
            capability_count = len(extension.capabilities)
            row.set_subtitle(
                f"Version {extension.version} · {capability_count} capabilities · schema 1"
            )
            row.add_prefix(Gtk.Image.new_from_icon_name("application-x-addon-symbolic"))
            toggle = Gtk.Switch(active=extension.id in enabled)
            toggle.set_valign(Gtk.Align.CENTER)
            toggle.set_sensitive(extension.id != "core-catalog")
            toggle.set_tooltip_text(
                "The core catalog is always enabled"
                if extension.id == "core-catalog"
                else "Enable extension"
            )
            toggle.connect(
                "notify::active",
                lambda switch, _param, extension_id=extension.id: (
                    self._toggle_extension(extension_id, switch.get_active())
                ),
            )
            row.add_suffix(toggle)
            folder = Gtk.Button.new_from_icon_name("folder-open-symbolic")
            folder.set_tooltip_text("Open extension folder")
            folder.set_valign(Gtk.Align.CENTER)
            folder.connect(
                "clicked",
                lambda _button, path=extension.path: Gio.AppInfo.launch_default_for_uri(
                    path.as_uri(), None
                ),
            )
            row.add_suffix(folder)
            self.extension_list.append(row)

    def _toggle_extension(self, extension_id: str, enabled: bool) -> None:
        if extension_id == "core-catalog":
            return
        configured = self.store.enabled_extensions
        if enabled and extension_id not in configured:
            configured.append(extension_id)
        elif not enabled and extension_id in configured:
            in_use = any(
                rule.match_kind is MatchKind.SERVICE
                and rule.selector
                not in load_catalog(
                    [item for item in configured if item != extension_id]
                ).services_by_id
                for rule in self.store.rules
            )
            if in_use:
                self.toast("Remove policies from this extension before disabling it")
                self._render_extensions()
                return
            configured.remove(extension_id)
        self.store.save()
        self.catalog = load_catalog(configured)
        self.location_filter_regions = [
            Region("all", "All regions", "astrill"),
            *[region for region in self.catalog.regions if region.id != "direct"],
        ]
        self.location_filter.set_model(
            Gtk.StringList.new([region.name for region in self.location_filter_regions])
        )
        self.location_filter.set_selected(0)
        self._region_filter = "all"
        if self.servers is not None:
            self.server_groups = group_by_region(self.servers, self.catalog.regions)
        self._refresh_service_filters()
        self._render_rules()
        self._render_services()
        self._render_countries()
        self._render_locations()
        self._render_extensions()
        self.toast("Extension settings updated")

    def _toggle_autostart(self, switch: Gtk.Switch, _param: object) -> None:
        if self._updating_autostart:
            return
        try:
            if switch.get_active():
                enable_autostart()
            else:
                disable_autostart()
        except OSError as exc:
            self._updating_autostart = True
            switch.set_active(is_autostart_enabled())
            self._updating_autostart = False
            self.toast(f"Could not update desktop startup: {exc}")
            return
        self.toast(
            "Astrill Lazy will start after login"
            if switch.get_active()
            else "Desktop startup disabled"
        )

    def _toggle_astrill_connection(self, switch: Gtk.Switch, _param: object) -> None:
        if self._updating_astrill_connection:
            return
        self._change_astrill_connection(switch.get_active())

    def _change_astrill_connection(self, connected: bool) -> None:
        if not self._require_write_access("changing the Astrill connection"):
            self._update_status()
            return

        def success(status: dict[str, Any]) -> None:
            self._router_refreshed(status)
            self.toast("Astrill connected" if connected else "Astrill disconnected")

        self._run_task(
            lambda: self.router.set_astrill_connection(
                connected,
                companion_enabled=self.store.companion_enabled,
            ),
            success,
            "Could not change Astrill connection",
        )

    def _set_rule_target(
        self, rule: Rule, target: RouteTarget, dropdown: Gtk.DropDown
    ) -> None:
        if rule.target is target:
            return
        rule.target = target
        dropdown.set_sensitive(target is RouteTarget.VPN)
        region_ids = [region.id for region in self.catalog.regions]
        if target is RouteTarget.DIRECT:
            if rule.region != "direct":
                rule.metadata["country_override"] = rule.region
            rule.region = "direct"
        elif rule.region == "direct":
            remembered = str(rule.metadata.get("country_override", "active-astrill"))
            rule.region = remembered if remembered in region_ids else "active-astrill"
        rule.metadata.pop("route_recommendation", None)
        dropdown.set_selected(region_ids.index(rule.region))
        self._changed()
        self._render_services()
        self._update_recommendation_controls()

    def _set_rule_region(self, rule: Rule, region: str) -> None:
        if not region or rule.region == region:
            return
        if rule.target is RouteTarget.DIRECT and region != "direct":
            return
        rule.region = region
        self._changed()

    def _set_rule_enabled(self, rule: Rule, enabled: bool) -> None:
        if rule.enabled == enabled:
            return
        rule.enabled = enabled
        self._changed()

    def _changed(self) -> None:
        self.store.save()
        self.dirty = True
        self.window_title.set_subtitle("Changes ready to apply")
        self._render_countries()

    def _add_service(self, service_id: str) -> None:
        service = self.catalog.services_by_id[service_id]
        if any(
            rule.match_kind is MatchKind.SERVICE and rule.selector == service_id
            for rule in self.store.rules
        ):
            self.toast("This service already has a policy")
            return
        target, region = service_policy_route(service, ServiceRouteMode.SUGGESTED)
        rule = Rule.create(
            name=service.name,
            match_kind=MatchKind.SERVICE,
            selector=service.id,
            target=target,
            region=region,
            priority=self._next_priority(),
        )
        if service.id in MINIMUM_BYPASS_SERVICES:
            rule.metadata["minimum_bypass"] = True
        self.store.rules.append(rule)
        self._changed()
        self._render_rules()
        self._render_services()
        self.toast(f"Added {service.name}")

    def _add_device(self, address: str, name: str, target: RouteTarget) -> None:
        rule = Rule.create(
            name=name if name and name != "*" else address,
            match_kind=MatchKind.DEVICE,
            selector=address,
            target=target,
            region="direct" if target is RouteTarget.DIRECT else "active-astrill",
            priority=self._next_priority(),
        )
        self.store.rules.append(rule)
        self._changed()
        self._render_rules()
        self.toast(f"Added device policy for {address}")

    def _delete_rule(self, rule: Rule) -> None:
        dialog = Adw.MessageDialog.new(
            self,
            "Delete policy?",
            f"{rule.name} will be removed from the next router apply.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(_dialog: Adw.MessageDialog, response_id: str) -> None:
            if response_id != "delete":
                return
            self.store.rules.remove(rule)
            self._changed()
            self._render_rules()
            self._render_services()
            if rule.match_kind is MatchKind.PROCESS and rule.metadata.get(
                "namespace_ip"
            ):
                self._run_task(
                    lambda: self.launcher.cleanup(rule),
                    lambda _result: self.toast("Application identity removed"),
                    "Could not remove application identity",
                )

        dialog.connect("response", response)
        dialog.present()

    def _next_priority(self) -> int:
        return min(
            9999, max((rule.priority for rule in self.store.rules), default=0) + 100
        )

    def _show_domain_dialog(self) -> None:
        self._show_selector_dialog(
            "Add Website",
            "Domain",
            "example.com",
            MatchKind.DOMAIN,
            _normalize_domain,
        )

    def _show_network_dialog(self) -> None:
        self._show_selector_dialog(
            "Add IP Network",
            "IPv4 address or CIDR",
            "203.0.113.0/24",
            MatchKind.CIDR,
            lambda value: value.strip(),
        )

    def _show_device_dialog(self) -> None:
        self._show_selector_dialog(
            "Add Device",
            "IPv4 address",
            "192.168.1.100",
            MatchKind.DEVICE,
            lambda value: value.strip(),
        )

    def _show_selector_dialog(
        self,
        heading: str,
        field_label: str,
        placeholder: str,
        kind: MatchKind,
        normalize: Callable[[str], str],
    ) -> None:
        dialog = Adw.MessageDialog.new(self, heading, "")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        form.set_size_request(420, -1)
        label = Gtk.Label(label=field_label)
        label.set_xalign(0)
        entry = Gtk.Entry()
        entry.set_placeholder_text(placeholder)
        form.append(label)
        form.append(entry)
        name_label = Gtk.Label(label="Policy name")
        name_label.set_xalign(0)
        name = Gtk.Entry()
        form.append(name_label)
        form.append(name)
        route, region = self._route_fields(form)
        dialog.set_extra_child(form)

        def response(_dialog: Adw.MessageDialog, response_id: str) -> None:
            if response_id != "add":
                return
            try:
                selector = normalize(entry.get_text())
                target = (
                    RouteTarget.DIRECT if route.get_selected() == 0 else RouteTarget.VPN
                )
                region_id = (
                    "direct"
                    if target is RouteTarget.DIRECT
                    else self._vpn_regions()[region.get_selected()].id
                )
                rule = Rule.create(
                    name=name.get_text().strip() or selector,
                    match_kind=kind,
                    selector=selector,
                    target=target,
                    region=region_id,
                    priority=self._next_priority(),
                )
                rule.validate()
            except ValueError as exc:
                self.toast(str(exc))
                return
            self.store.rules.append(rule)
            self._changed()
            self._render_rules()

        dialog.connect("response", response)
        dialog.present()

    def _show_application_dialog(self) -> None:
        dialog = Adw.MessageDialog.new(self, "Add Application", "")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        form.set_size_request(460, -1)
        label = Gtk.Label(label="Executable and optional arguments")
        label.set_xalign(0)
        command_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        command = Gtk.Entry()
        command.set_hexpand(True)
        command.set_placeholder_text("/usr/bin/application")
        browse = Gtk.Button.new_from_icon_name("document-open-symbolic")
        browse.set_tooltip_text("Choose executable")
        command_row.append(command)
        command_row.append(browse)
        form.append(label)
        form.append(command_row)
        name_label = Gtk.Label(label="Policy name")
        name_label.set_xalign(0)
        name = Gtk.Entry()
        form.append(name_label)
        form.append(name)
        route, region = self._route_fields(form)
        dialog.set_extra_child(form)

        def choose_file(_button: Gtk.Button) -> None:
            chooser = Gtk.FileDialog(title="Choose application")

            def selected(file_dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
                try:
                    selected_file = file_dialog.open_finish(result)
                except GLib.Error:
                    return
                path = selected_file.get_path()
                if path:
                    command.set_text(shlex.quote(path))

            chooser.open(self, None, selected)

        browse.connect("clicked", choose_file)

        def response(_dialog: Adw.MessageDialog, response_id: str) -> None:
            if response_id != "add":
                return
            try:
                executable, arguments = parse_command(command.get_text())
                target = (
                    RouteTarget.DIRECT if route.get_selected() == 0 else RouteTarget.VPN
                )
                region_id = (
                    "direct"
                    if target is RouteTarget.DIRECT
                    else self._vpn_regions()[region.get_selected()].id
                )
                rule = Rule.create(
                    name=name.get_text().strip() or Path(executable).name,
                    match_kind=MatchKind.PROCESS,
                    selector=executable,
                    target=target,
                    region=region_id,
                    priority=self._next_priority(),
                )
                rule.metadata["arguments"] = arguments
                rule.validate()
            except ValueError as exc:
                self.toast(str(exc))
                return
            self.store.rules.append(rule)
            self._changed()
            self._render_rules()

        dialog.connect("response", response)
        dialog.present()

    def _route_fields(self, form: Gtk.Box) -> tuple[Gtk.DropDown, Gtk.DropDown]:
        route_label = Gtk.Label(label="Route")
        route_label.set_xalign(0)
        route = Gtk.DropDown.new_from_strings(["Direct", "Astrill"])
        route.set_selected(
            0 if self._incremental_default_target() is RouteTarget.DIRECT else 1
        )
        region_label = Gtk.Label(label="Country override")
        region_label.set_xalign(0)
        region = Gtk.DropDown.new_from_strings(
            [item.name for item in self._vpn_regions()]
        )
        region.set_sensitive(route.get_selected() == 1)
        route.connect(
            "notify::selected",
            lambda widget, _param: region.set_sensitive(widget.get_selected() == 1),
        )
        form.append(route_label)
        form.append(route)
        form.append(region_label)
        form.append(region)
        return route, region

    def _vpn_regions(self) -> list[Region]:
        return [region for region in self.catalog.regions if region.id != "direct"]

    def _incremental_default_target(self) -> RouteTarget:
        if (
            self.native_settings is not None
            and self.native_settings.integer("astrill_routingmode") == 1
        ):
            return RouteTarget.VPN
        return RouteTarget.DIRECT

    def apply_configuration(self, _button: Gtk.Button | None = None) -> None:
        if not self._require_write_access("applying router policies"):
            return
        if not self.store.companion_enabled:
            self.toast("Install the companion before applying routing policies")
            return
        self.store.save()
        try:
            compilation = compile_rules(self.store.rules, self.catalog)
        except ValueError as exc:
            self.toast(str(exc))
            return

        def success(status: dict[str, Any]) -> None:
            self.router_status = status
            self.dirty = False
            self.window_title.set_subtitle("Router policy is up to date")
            self._update_status()
            if compilation.warnings:
                self.policy_banner.set_title(" ".join(compilation.warnings))
                self.policy_banner.set_revealed(True)
            else:
                self.toast(f"Applied {status.get('origin_count', 0)} policy groups")

        self._run_task(
            lambda: self.router.apply_rules(compilation.to_tsv()),
            success,
            "Could not apply router policy",
        )

    def detect_routes(self, _button: Gtk.Button | None = None) -> None:
        if not self.store.companion_enabled:
            self.toast("Install the companion before detecting routes")
            return
        candidates = [
            rule
            for rule in self.store.rules
            if rule.enabled and rule.match_kind in {MatchKind.SERVICE, MatchKind.DOMAIN}
        ]
        if not candidates:
            self.toast("No enabled service or website policies to detect")
            return

        def work() -> list[RouteRecommendation]:
            status = self.router.status()
            if status.get("vpn_state") != "up":
                raise RuntimeError("connect Astrill before comparing both paths")
            return detect_rules(self.router, candidates, self.catalog)

        def success(recommendations: list[RouteRecommendation]) -> None:
            by_id = {
                recommendation.rule_id: recommendation
                for recommendation in recommendations
            }
            for rule in self.store.rules:
                recommendation = by_id.get(rule.id)
                if recommendation is None:
                    continue
                metadata = recommendation.to_metadata()
                metadata["applied"] = False
                rule.metadata["route_recommendation"] = metadata
            self.store.save()
            self._render_rules()
            changes = sum(
                recommendation.target
                is not self._rule_by_id(recommendation.rule_id).target
                for recommendation in recommendations
            )
            if changes:
                self.toast(
                    f"Checked {len(recommendations)} policies; "
                    f"{changes} route changes recommended"
                )
            else:
                self.toast(
                    f"Checked {len(recommendations)} policies; current routes recommended"
                )

        self._run_task(
            work,
            success,
            "Network detection failed",
        )

    def apply_route_recommendations(self, _button: Gtk.Button | None = None) -> None:
        if not self._require_write_access("applying route recommendations"):
            return
        recommendations = [
            (rule, value)
            for rule in self.store.rules
            if isinstance((value := rule.metadata.get("route_recommendation")), dict)
            and not value.get("applied")
        ]
        if not recommendations:
            self.toast("No pending route recommendations")
            return
        region_ids = {region.id for region in self.catalog.regions}
        changed = 0
        for rule, recommendation in recommendations:
            try:
                target = RouteTarget(str(recommendation["target"]))
            except (KeyError, ValueError):
                continue
            if rule.target is not target:
                changed += 1
            rule.target = target
            if target is RouteTarget.DIRECT:
                if rule.region != "direct":
                    rule.metadata["country_override"] = rule.region
                rule.region = "direct"
            elif rule.region == "direct":
                remembered = str(
                    rule.metadata.get("country_override", "active-astrill")
                )
                rule.region = (
                    remembered if remembered in region_ids else "active-astrill"
                )
            recommendation["applied"] = True
        self.store.save()
        self.dirty = True
        self._render_rules()
        self._render_services()
        self._render_countries()
        self.apply_configuration()
        self.toast(
            f"Applying {changed} recommended route change{'' if changed == 1 else 's'}"
        )

    def _rule_by_id(self, rule_id: str) -> Rule:
        return next(rule for rule in self.store.rules if rule.id == rule_id)

    def _update_recommendation_controls(self) -> None:
        if not hasattr(self, "apply_recommendations_button"):
            return
        pending = any(
            isinstance(value := rule.metadata.get("route_recommendation"), dict)
            and not value.get("applied")
            for rule in self.store.rules
        )
        idle = self.busy_count == 0
        self.detect_routes_button.set_sensitive(self.store.companion_enabled and idle)
        self.apply_recommendations_button.set_sensitive(
            self.store.companion_enabled
            and not self.store.read_only
            and idle
            and pending
        )

    def refresh_router(self) -> None:
        work = (
            self.router.status
            if self.store.companion_enabled
            else self.router.native_astrill_status
        )
        self._run_task(
            work,
            self._router_refreshed,
            "Could not reach the router",
            quiet=True,
        )

    def refresh_native_settings(
        self,
        *,
        quiet: bool = False,
        force_native: bool = False,
        force_connection: bool = False,
    ) -> None:
        if self._native_settings_loading:
            return
        self._native_settings_loading = True
        self._run_task(
            self.router.native_astrill_settings,
            lambda settings: self._native_settings_refreshed(
                settings,
                notify=not quiet,
                force_native=force_native,
                force_connection=force_connection,
            ),
            "Could not load native Astrill settings",
            quiet=quiet,
        )

    def _native_settings_refreshed(
        self,
        settings: NativeAstrillSettings,
        *,
        notify: bool,
        force_native: bool = False,
        force_connection: bool = False,
    ) -> None:
        self._native_settings_loading = False
        self.native_settings = settings
        if force_native or not self.native_page.dirty:
            self.native_page.render(settings, self.clients)
        self.connection_page.sync(
            settings,
            self.servers or (),
            self.router_status,
            force=force_connection,
        )
        self._render_services()
        if notify:
            self.toast("Native Astrill settings synchronized")

    def confirm_refresh_connection(self) -> None:
        if not self.connection_page.dirty:
            self.refresh_native_settings(force_connection=True)
            return
        dialog = Adw.MessageDialog.new(
            self,
            "Discard unsaved connection changes?",
            "The connection page will reload the current values from the router.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reload", "Reload")
        dialog.set_response_appearance("reload", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            lambda _dialog, response: (
                response == "reload"
                and self.refresh_native_settings(force_connection=True)
            ),
        )
        dialog.present()

    def save_native_settings(self) -> None:
        if not self._require_write_access("saving native Astrill settings"):
            return
        try:
            changes = self.native_page.collect_changes()
        except ValueError as exc:
            self.toast(str(exc))
            return
        if not changes:
            self.native_page.mark_clean()
            self.toast("Native Astrill settings are already synchronized")
            return

        def success(settings: NativeAstrillSettings) -> None:
            self._native_settings_refreshed(
                settings,
                notify=False,
                force_native=True,
            )
            self.toast("Native Astrill settings saved")

        self._run_task(
            lambda: self.router.update_native_astrill_settings(changes),
            success,
            "Could not save native Astrill settings",
        )

    def save_astrill_connection(self) -> None:
        if not self._require_write_access("saving the Astrill connection"):
            return
        try:
            draft = self.connection_page.collect()
        except ValueError as exc:
            self.toast(str(exc))
            return
        if not self.connection_page.dirty:
            self.toast("Astrill connection settings are already synchronized")
            return
        if self.router_status.get("vpn_state") == "up":
            self.confirm_connect_astrill()
            return

        def success(settings: NativeAstrillSettings) -> None:
            self._native_settings_refreshed(
                settings,
                notify=False,
                force_connection=True,
            )
            status = dict(self.router_status)
            status["astrill_server_id"] = draft.selection.server_id
            status["astrill_protocol"] = draft.selection.protocol
            self._router_refreshed(status)
            self.toast("Astrill connection settings saved")

        self._run_task(
            lambda: self.router.save_astrill_connection(
                draft.selection,
                draft.changes,
            ),
            success,
            "Could not save Astrill connection",
        )

    def confirm_connect_astrill(self) -> None:
        if not self._require_write_access("changing the Astrill connection"):
            return
        try:
            draft = self.connection_page.collect()
        except ValueError as exc:
            self.toast(str(exc))
            return
        if not self.connection_page.dirty:
            self._change_astrill_connection(True)
            return

        server = self._server_by_id(draft.selection.server_id)
        server_name = (
            server.name if server is not None else f"Server {draft.selection.server_id}"
        )
        connected = self.router_status.get("vpn_state") == "up"
        verb = "Reconnect" if connected else "Connect"
        dialog = Adw.MessageDialog.new(
            self,
            f"{verb} to {server_name}?",
            f"{ASTRILL_PROTOCOL_NAMES[draft.selection.protocol]} on port "
            f"{draft.selection.port} will be saved to the router. "
            + (
                "VPN-routed traffic will pause during reconnection."
                if connected
                else "The shared Astrill tunnel will be started."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("connect", verb)
        dialog.set_response_appearance("connect", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("connect")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            lambda _dialog, response: (
                response == "connect" and self._apply_astrill_connection(draft)
            ),
        )
        dialog.present()

    def _apply_astrill_connection(self, draft: ConnectionDraft) -> None:
        def success(result: AstrillConnectionResult) -> None:
            self._native_settings_refreshed(
                result.settings,
                notify=False,
                force_connection=True,
            )
            self._router_refreshed(result.status)
            server = self._server_by_id(draft.selection.server_id)
            if server is not None:
                self.store.active_region = self._region_for_server(server)
                self.store.save()
            self.toast(
                f"Connected with {ASTRILL_PROTOCOL_NAMES[draft.selection.protocol]}"
            )

        self._run_task(
            lambda: self.router.apply_astrill_connection(
                draft.selection,
                draft.changes,
                companion_enabled=self.store.companion_enabled,
            ),
            success,
            "Could not apply Astrill connection",
        )

    def refresh_domains(self, _button: Gtk.Button | None = None) -> None:
        if not self._require_write_access("refreshing companion domain routes"):
            return
        self._run_task(
            self.router.refresh,
            self._domains_refreshed,
            "Could not refresh domain routes",
        )

    def _domains_refreshed(self, status: dict[str, Any]) -> None:
        self._router_refreshed(status)
        self.toast(f"Refreshed {status.get('resolved_addresses', 0)} domain addresses")

    def confirm_rollback(self, _button: Gtk.Button | None = None) -> None:
        if not self._require_write_access("rolling back the router policy"):
            return
        dialog = Adw.MessageDialog.new(
            self,
            "Restore the previous router policy?",
            "The desktop policy remains unchanged. Apply sends it to the router again.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rollback", "Roll Back")
        dialog.set_response_appearance("rollback", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            lambda _dialog, response: (
                response == "rollback" and self._rollback_router()
            ),
        )
        dialog.present()

    def _rollback_router(self) -> None:
        def success(status: dict[str, Any]) -> None:
            self.dirty = True
            self._router_refreshed(status)
            self.policy_banner.set_title(
                "Router is using the previous policy. Apply restores the desktop policy."
            )
            self.policy_banner.set_revealed(True)
            self.toast("Restored the previous router policy")

        self._run_task(
            self.router.rollback,
            success,
            "Could not roll back router policy",
        )

    def _router_client_from_store(self) -> RouterClient:
        if self.store.router_use_ssh_config:
            return RouterClient(self.store.router_host)
        return RouterClient(
            self.store.router_host,
            user=self.store.router_user,
            port=self.store.router_port,
            identity_file=self.store.router_identity,
        )

    def _save_router_connection(self, _button: Gtk.Button | None = None) -> None:
        if not self._apply_router_connection_fields():
            return
        self._companion_install_prompted = False
        self._ssh_setup_prompted = False
        self.check_router_environment(quiet=False)

    def _apply_router_connection_fields(self) -> bool:
        host = self.router_host_entry.get_text().strip()
        user = self.router_user_entry.get_text().strip()
        identity = self.router_identity_entry.get_text().strip()
        try:
            port = int(self.router_port_entry.get_text().strip())
            if not host or any(character.isspace() for character in host):
                raise ValueError("router address must not be empty or contain spaces")
            if not user or any(character.isspace() for character in user):
                raise ValueError("SSH user must not be empty or contain spaces")
            if not 1 <= port <= 65535:
                raise ValueError("SSH port must be between 1 and 65535")
            ensure_local_identity(identity)
        except (OSError, RuntimeError, ValueError) as exc:
            self.toast(f"Could not save router connection: {exc}")
            return False

        self.store.router_host = host
        self.store.router_user = user
        self.store.router_port = port
        self.store.router_identity = identity
        self.store.router_use_ssh_config = False
        self.store.save()
        self.router = self._router_client_from_store()
        self.router_ssh_icon.set_from_icon_name("content-loading-symbolic")
        self.router_ssh_row.set_subtitle("Checking key-only SSH")
        return True

    def confirm_authorize_router_key(self, _button: Gtk.Button | None = None) -> None:
        if not self._apply_router_connection_fields():
            return
        dialog = Adw.MessageDialog.new(
            self,
            "Authorize the dedicated SSH key?",
            "The password is used once and is never saved. The router keeps "
            "Telnet unchanged, enables LAN SSH, verifies key login, then "
            "disables SSH password login and WAN SSH management.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("authorize", "Authorize Key")
        dialog.set_response_appearance("authorize", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        form.set_size_request(420, -1)
        password_label = Gtk.Label(label="Router password")
        password_label.set_xalign(0)
        password = Gtk.PasswordEntry()
        password.set_show_peek_icon(True)
        password.set_text("admin")
        form.append(password_label)
        form.append(password)
        dialog.set_extra_child(form)

        def response(_dialog: Adw.MessageDialog, response_id: str) -> None:
            if response_id != "authorize":
                password.set_text("")
                return
            supplied_password = password.get_text()
            password.set_text("")
            self._run_task(
                lambda: authorize_router_key(self.router, supplied_password),
                lambda _result: self._router_key_authorized(),
                "Router SSH setup failed",
            )

        dialog.connect("response", response)
        dialog.present()

    def _router_key_authorized(self) -> None:
        self.router_ssh_icon.set_from_icon_name("object-select-symbolic")
        self.router_ssh_row.set_subtitle(
            f"Connected as {self.store.router_user} on port {self.store.router_port}"
        )
        self.toast("Router SSH key authorized")
        self._ssh_setup_prompted = True
        self.check_router_environment(quiet=False)

    def check_router_environment(self, *, quiet: bool = True) -> None:
        def check() -> tuple[
            dict[str, Any],
            CompanionCheck,
            NativeAstrillSettings | None,
        ]:
            if not self.router.ping():
                raise RuntimeError("router did not acknowledge SSH")
            native_status = self.router.native_astrill_status()
            companion = RouterInstaller(self.router).check()
            try:
                settings = self.router.native_astrill_settings()
            except RouterError:
                settings = None
            return native_status, companion, settings

        self._run_task(
            check,
            self._router_environment_checked,
            "Could not check router setup",
            quiet=quiet,
        )

    def _router_environment_checked(
        self,
        result: tuple[
            dict[str, Any],
            CompanionCheck,
            NativeAstrillSettings | None,
        ],
    ) -> None:
        native_status, companion, settings = result
        self.astrill_applet_available = native_status.get("health") == "healthy"
        self.router_companion_check = companion
        if settings is not None:
            self._native_settings_refreshed(settings, notify=False)
        self.router_ssh_icon.set_from_icon_name("object-select-symbolic")
        self.router_ssh_row.set_subtitle(
            f"Connected as {self.store.router_user} on port {self.store.router_port}"
        )

        if native_status.get("health") != "healthy":
            self._router_refreshed(native_status)
            self.confirm_install_astrill()
            return
        if companion.action == "none":
            if self.store.companion_enabled:
                self._router_refreshed(companion.status or native_status)
            else:
                self._router_refreshed(native_status)
                self._confirm_use_detected_companion(companion)
            return
        if companion.action == "repair":
            self._router_refreshed(companion.status or native_status)
            if self.store.companion_enabled and not self.store.read_only:
                self.ensure_router_companion()
            else:
                self._confirm_companion_install(companion)
            return
        self._router_refreshed(native_status)
        self._confirm_companion_install(companion)

    def ensure_router_companion(self, *, quiet: bool = True) -> None:
        if not self.store.companion_enabled or self.store.read_only:
            self.refresh_router()
            return
        self._run_task(
            lambda: RouterInstaller(self.router).ensure(allow_install=False),
            self._router_companion_ensured,
            "Could not reconcile the router companion",
            quiet=quiet,
        )

    def reconcile_router(self, _button: Gtk.Button | None = None) -> None:
        if not self._require_write_access("repairing the router companion"):
            return
        if not self.store.companion_enabled:
            self.toast("Install the companion before repairing its runtime")
            return
        self._run_task(
            lambda: RouterInstaller(self.router).ensure(allow_install=False),
            self._manual_companion_ensured,
            "Could not repair the router companion",
        )

    def _manual_companion_ensured(self, result: EnsureResult) -> None:
        self._router_companion_ensured(result)
        if result.action == "none":
            self.toast("Router companion is already current")

    def _router_companion_ensured(self, result: EnsureResult) -> None:
        self._router_refreshed(result.status)
        if result.action == "repaired":
            self.toast("Router companion runtime repaired")

    def _router_refreshed(self, status: dict[str, Any]) -> None:
        self.router_status = status
        self.connection_page.update_status(status)
        self._update_status()
        self._render_countries()
        self._render_locations()

    def _update_status(self) -> None:
        status = self.router_status
        native_mode = not self.store.companion_enabled
        healthy = status.get("health") == "healthy"
        self.metrics["health"].set_label(
            "Native Astrill"
            if native_mode and healthy
            else ("Healthy" if healthy else "Needs attention")
        )
        _set_status_class(self.metrics["health"], healthy)
        tunnel = status.get("vpn_state") == "up"
        self.metrics["tunnel"].set_label("Connected" if tunnel else "Disconnected")
        _set_status_class(self.metrics["tunnel"], tunnel)
        server_id = int(status.get("astrill_server_id", 0))
        server = self._server_by_id(server_id)
        protocol = int(status.get("astrill_protocol", 0))
        if not self._protocol_user_selected and 0 <= protocol < len(
            ASTRILL_PROTOCOL_NAMES
        ):
            self._updating_protocol = True
            self.protocol_dropdown.set_selected(protocol)
            self._updating_protocol = False
        self.metrics["location"].set_label(
            (server.name if server else f"Server {server_id}")
            if tunnel
            else "No active tunnel"
        )
        self.metrics["rules"].set_label(str(status.get("origin_count", 0)))
        if healthy:
            self.sidebar_status_icon.set_from_icon_name("network-vpn-symbolic")
            self.sidebar_status_label.set_label(
                "Native Astrill" if native_mode else "Router connected"
            )
        else:
            self.sidebar_status_icon.set_from_icon_name("network-error-symbolic")
            self.sidebar_status_label.set_label("Router needs attention")
        if status.get("vpn_rules", 0) and not tunnel:
            self.policy_banner.set_title(
                "VPN policies are fail-closed until Astrill reconnects."
            )
            self.policy_banner.set_revealed(True)
        elif status.get("unresolved_domains", 0):
            self.policy_banner.set_title(
                f"{status['unresolved_domains']} domain policies are unresolved."
            )
            self.policy_banner.set_revealed(True)
        elif not self.dirty:
            self.policy_banner.set_revealed(False)
        installed = self.store.companion_enabled and status.get("version") not in {
            None,
            "",
            "unknown",
        }
        self.router_companion_icon.set_from_icon_name(
            "object-select-symbolic" if installed else "network-offline-symbolic"
        )
        runtime_healthy = (
            status.get("jump_installed") is True and status.get("watchdog") is True
        )
        self.router_runtime_icon.set_from_icon_name(
            "object-select-symbolic" if runtime_healthy else "network-offline-symbolic"
        )
        if native_mode:
            self.router_runtime_row.set_subtitle(
                "Native Astrill mode · companion runtime removed"
            )
            self.router_domain_row.set_subtitle(
                "Unavailable until the companion is installed"
            )
            self.router_companion_row.set_subtitle(
                "Not installed · native Astrill is unchanged"
            )
        else:
            watchdog = (
                "Watchdog active" if status.get("watchdog") else "Watchdog stopped"
            )
            self.router_runtime_row.set_subtitle(
                f"Version {status.get('version', 'unknown')} · "
                f"{status.get('active_chain') or 'No active chain'} · {watchdog}"
            )
            self.router_domain_row.set_subtitle(
                f"{status.get('resolved_addresses', 0)} resolved · "
                f"{status.get('unresolved_domains', 0)} unresolved · "
                f"{status.get('origin_count', 0)} policies"
            )
            self.router_companion_row.set_subtitle(
                "Persistent controller, watchdog, MyPage, and automatic repair"
            )
        protocol_name = (
            ASTRILL_PROTOCOL_NAMES[protocol]
            if 0 <= protocol < len(ASTRILL_PROTOCOL_NAMES)
            else f"Protocol {protocol}"
        )
        location_name = server.name if server else f"Server {server_id}"
        self.router_astrill_row.set_subtitle(
            f"{'Connected' if tunnel else 'Disconnected'} · "
            f"{location_name} · {protocol_name}"
        )
        self.router_astrill_icon.set_from_icon_name(
            "network-vpn-symbolic" if tunnel else "network-offline-symbolic"
        )
        self._updating_astrill_connection = True
        self.astrill_connection_switch.set_active(tunnel)
        self._updating_astrill_connection = False
        writable = not self.store.read_only and self.busy_count == 0
        companion_writable = self.store.companion_enabled and writable
        self.astrill_connection_switch.set_sensitive(writable)
        self.astrill_install_button.set_visible(self.astrill_applet_available is False)
        self.astrill_install_button.set_sensitive(self.busy_count == 0)
        self.choose_location_button.set_sensitive(writable)
        self.protocol_dropdown.set_sensitive(writable)
        for control in (
            self.router_repair_button,
            self.router_refresh_domains_button,
            self.router_rollback_button,
        ):
            control.set_sensitive(companion_writable)
        self.restore_native_button.set_sensitive(companion_writable)
        self.apply_button.set_sensitive(companion_writable)
        for button in self.router_install_buttons:
            button.set_sensitive(self.busy_count == 0)
        self.native_page.set_read_only(self.store.read_only)
        self.connection_page.set_read_only(self.store.read_only)
        self._update_recommendation_controls()

    def refresh_clients(self) -> None:
        if self._clients_loading:
            return
        self._clients_loading = True
        self._run_task(
            (
                self.router.clients
                if self.store.companion_enabled
                else self.router.native_clients
            ),
            self._clients_refreshed,
            "Could not load router clients",
        )

    def _clients_refreshed(self, clients: list[dict[str, Any]]) -> None:
        self._clients_loading = False
        self._clients_loaded = True
        self.clients = clients
        self._render_devices()
        self.native_page.update_clients(clients)
        self.toast(f"Loaded {len(clients)} LAN devices")

    def load_servers(self) -> None:
        if self.servers_loading:
            return
        self.servers_loading = True

        def load() -> tuple[
            tuple[AstrillServer, ...], dict[str, tuple[AstrillServer, ...]]
        ]:
            servers = parse_applet(self.router.fetch_astrill_payload())
            return servers, group_by_region(servers, self.catalog.regions)

        def success(
            result: tuple[
                tuple[AstrillServer, ...], dict[str, tuple[AstrillServer, ...]]
            ],
        ) -> None:
            self.servers_loading = False
            self.servers, self.server_groups = result
            if self.native_settings is not None:
                self.connection_page.sync(
                    self.native_settings,
                    self.servers,
                    self.router_status,
                )
            self._render_countries()
            self._render_locations()
            self._update_status()

        self._run_task(load, success, "Could not load Astrill endpoints")

    def _on_location_filter(self, dropdown: Gtk.DropDown, _param: Any) -> None:
        self._region_filter = self.location_filter_regions[dropdown.get_selected()].id
        self._render_locations()

    def _open_region_endpoints(self, region_id: str) -> None:
        selected_id = "all" if region_id == "active-astrill" else region_id
        region_ids = [region.id for region in self.location_filter_regions]
        if selected_id in region_ids:
            self.location_filter.set_selected(region_ids.index(selected_id))
            self._region_filter = selected_id
            self._render_locations()
        self.select_page("locations")

    def _on_protocol_selected(self, dropdown: Gtk.DropDown, _param: Any) -> None:
        if self._updating_protocol:
            return
        current = int(self.router_status.get("astrill_protocol", 0))
        self._protocol_user_selected = dropdown.get_selected() != current

    def _server_by_id(self, server_id: int) -> AstrillServer | None:
        if self.servers is None:
            return None
        return next((server for server in self.servers if server.id == server_id), None)

    def _confirm_switch_server(self, server: AstrillServer) -> None:
        protocol = self.protocol_dropdown.get_selected()
        dialog = Adw.MessageDialog.new(
            self,
            f"Connect to {server.name}?",
            f"{ASTRILL_PROTOCOL_NAMES[protocol]} will reconnect the shared tunnel, "
            "briefly pausing VPN-routed traffic.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("connect", "Connect")
        dialog.set_response_appearance("connect", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("connect")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            lambda _dialog, response: (
                response == "connect" and self._switch_server(server)
            ),
        )
        dialog.present()

    def _switch_server(self, server: AstrillServer) -> None:
        if not self._require_write_access("switching the Astrill endpoint"):
            return
        protocol = self.protocol_dropdown.get_selected()
        try:
            selection = AstrillConnectionSelection.from_server(server, protocol, 0)
        except ValueError as exc:
            self.toast(str(exc))
            return

        def switch() -> AstrillConnectionResult:
            return self.router.apply_astrill_connection(
                selection,
                {},
                companion_enabled=self.store.companion_enabled,
            )

        def success(result: AstrillConnectionResult) -> None:
            self.router_status = result.status
            self._native_settings_refreshed(
                result.settings,
                notify=False,
                force_connection=True,
            )
            self.store.active_region = self._region_for_server(server)
            self.store.save()
            self._protocol_user_selected = False
            self._update_status()
            self._render_countries()
            self._render_locations()
            self.toast(f"Connected to {server.name}")

        self._run_task(switch, success, "Astrill endpoint switch failed")

    def _region_for_server(self, server: AstrillServer) -> str:
        for region_id, servers in self.server_groups.items():
            if any(item.id == server.id for item in servers):
                return region_id
        return "other"

    def launch_app(self, rule: Rule) -> None:
        if not self._require_write_access("launching a router-routed application"):
            return

        def prepare_apply_launch() -> tuple[str, dict[str, Any]]:
            address = self.launcher.prepare(rule)
            self.store.save()
            compilation = compile_rules(self.store.rules, self.catalog)
            status = self.router.apply_rules(compilation.to_tsv())
            self.launcher.launch(rule)
            return address, status

        def success(result: tuple[str, dict[str, Any]]) -> None:
            address, status = result
            self.router_status = status
            self.dirty = False
            self._render_rules()
            self._update_status()
            self.toast(f"Launched {rule.name} as {address}")

        self._run_task(
            prepare_apply_launch,
            success,
            "Application launch failed",
        )

    def confirm_install_astrill(
        self,
        _button: Gtk.Button | None = None,
        *,
        force: bool = False,
    ) -> None:
        if self._astrill_install_prompted and not force:
            return
        self._astrill_install_prompted = True
        dialog = Adw.MessageDialog.new(
            self,
            "Provide the Astrill installer?",
            "The installer runs as root on the router and can change network "
            "access. Its URL, token, and content are kept only for this "
            "operation and are never saved.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("review", "Review Installer")
        dialog.set_response_appearance("review", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        form.set_size_request(520, -1)
        source_label = Gtk.Label(label="Installer URL or shell command")
        source_label.set_xalign(0)
        source = Gtk.PasswordEntry()
        source.set_show_peek_icon(True)
        source.set_text(ASTRILL_INSTALL_TEMPLATE)
        form.append(source_label)
        form.append(source)
        dialog.set_extra_child(form)

        def response(_dialog: Adw.MessageDialog, response_id: str) -> None:
            if response_id != "review":
                source.set_text("")
                return
            supplied = source.get_text()
            source.set_text("")
            self._run_task(
                lambda: prepare_astrill_installer(supplied),
                self._review_astrill_installer,
                "Could not prepare the Astrill installer",
            )

        dialog.connect("response", response)
        dialog.present()

    def _review_astrill_installer(self, installer: AstrillInstaller) -> None:
        transport_warning = (
            "\nThe download used unencrypted HTTP."
            if installer.insecure_transport
            else ""
        )
        dialog = Adw.MessageDialog.new(
            self,
            "Run this Astrill installer?",
            f"Source: {installer.source}\n"
            f"Size: {installer.size} bytes\n"
            f"SHA-256: {installer.sha256}{transport_warning}\n\n"
            "This executes third-party shell code as router root.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("install", "Install Astrill")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            lambda _dialog, response: (
                response == "install" and self._install_astrill(installer)
            ),
        )
        dialog.present()

    def _install_astrill(self, installer: AstrillInstaller) -> None:
        self._run_task(
            lambda: install_astrill(self.router, installer),
            self._astrill_installed,
            "Astrill installation failed",
        )

    def _astrill_installed(self, status: dict[str, Any]) -> None:
        self.astrill_applet_available = True
        self._router_refreshed(status)
        self.toast("Astrill applet installed")
        self.load_servers()
        self.refresh_native_settings(quiet=True)
        self.check_router_environment(quiet=False)

    def _confirm_use_detected_companion(self, check: CompanionCheck) -> None:
        if self._companion_install_prompted:
            return
        self._companion_install_prompted = True
        dialog = Adw.MessageDialog.new(
            self,
            "Use the detected router companion?",
            f"Version {check.installed_version or check.expected_version} is "
            "already installed and healthy. Enabling it allows this desktop "
            "to apply and repair policy routing.",
        )
        dialog.add_response("cancel", "Keep Native Only")
        dialog.add_response("enable", "Use Companion")
        dialog.set_response_appearance("enable", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(_dialog: Adw.MessageDialog, response_id: str) -> None:
            if response_id != "enable":
                return
            self.store.companion_enabled = True
            self.store.read_only = False
            self.store.save()
            self.native_page.set_read_only(False)
            self.connection_page.set_read_only(False)
            self.access_banner.set_revealed(False)
            if check.status is not None:
                self._router_refreshed(check.status)
            self.toast("Router companion enabled")

        dialog.connect("response", response)
        dialog.present()

    def _confirm_companion_install(
        self,
        check: CompanionCheck | None,
        *,
        force: bool = False,
    ) -> None:
        if self._companion_install_prompted and not force:
            return
        self._companion_install_prompted = True
        expected = (
            check.expected_version
            if check is not None
            else RouterInstaller(self.router).expected_version
        )
        reason = (
            check.reason if check is not None else "A manual reinstall was requested."
        )
        dialog = Adw.MessageDialog.new(
            self,
            f"Install router companion {expected}?",
            f"{reason}\n\n"
            "This writes a validated package to DD-WRT NVRAM, adds its MyPage "
            "entries, and starts the policy watchdog. Native Astrill files, "
            "account data, endpoint, and connection state are not replaced.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("install", "Install Companion")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            lambda _dialog, response: (
                response == "install" and self._install_router_companion()
            ),
        )
        dialog.present()

    def install_router(self, _button: Gtk.Button | None = None) -> None:
        self._confirm_companion_install(self.router_companion_check, force=True)

    def _install_router_companion(self) -> None:

        def success(result: Any) -> None:
            self.store.companion_enabled = True
            self.store.read_only = False
            self.store.save()
            self.native_page.set_read_only(False)
            self.connection_page.set_read_only(False)
            self.access_banner.set_revealed(False)
            self._router_refreshed(result.status)
            self.toast(f"Router companion {result.version} installed")

        self._run_task(
            lambda: RouterInstaller(self.router).install(),
            success,
            "Router companion installation failed",
        )

    def confirm_restore_native(self, _button: Gtk.Button | None = None) -> None:
        if not self._require_write_access("removing the router companion"):
            return
        dialog = Adw.MessageDialog.new(
            self,
            "Restore native Astrill only?",
            "This removes the companion watchdog, policy chains, routes, saved "
            "package, and MyPage entries. Astrill's endpoint and connection "
            "state are preserved.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore Astrill Only")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            lambda _dialog, response: (
                response == "restore" and self._restore_native_astrill()
            ),
        )
        dialog.present()

    def _restore_native_astrill(self) -> None:
        def success(status: dict[str, Any]) -> None:
            self.store.companion_enabled = False
            self.store.save()
            self._router_refreshed(status)
            self.toast("Companion removed; native Astrill restored")

        self._run_task(
            lambda: RouterInstaller(self.router).uninstall(),
            success,
            "Could not fully restore native Astrill",
        )

    def _run_task(
        self,
        work: Callable[[], Any],
        success: Callable[[Any], None],
        error_prefix: str,
        *,
        quiet: bool = False,
    ) -> None:
        self.busy_count += 1
        self.apply_button.set_sensitive(False)
        self.native_page.set_busy(True)
        self.connection_page.set_busy(True)
        self._update_recommendation_controls()

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(self._task_failed, error_prefix, str(exc), quiet)
            else:
                GLib.idle_add(self._task_succeeded, success, result)

        threading.Thread(target=runner, daemon=True).start()

    def _task_succeeded(self, callback: Callable[[Any], None], result: Any) -> bool:
        self._task_finished()
        callback(result)
        return GLib.SOURCE_REMOVE

    def _task_failed(self, prefix: str, message: str, quiet: bool) -> bool:
        self._task_finished()
        if not quiet or not self.router_status:
            self.toast(f"{prefix}: {message}")
        if prefix == "Could not reach the router":
            self.sidebar_status_icon.set_from_icon_name("network-offline-symbolic")
            self.sidebar_status_label.set_label("Router unavailable")
        if prefix in {"Could not check router setup", "Router SSH setup failed"}:
            self.router_ssh_icon.set_from_icon_name("network-offline-symbolic")
            self.router_ssh_row.set_subtitle(message)
            self.sidebar_status_icon.set_from_icon_name("network-offline-symbolic")
            self.sidebar_status_label.set_label("Router unavailable")
            if (
                prefix == "Could not check router setup"
                and not self._ssh_setup_prompted
            ):
                self._ssh_setup_prompted = True
                self.confirm_authorize_router_key()
        if prefix == "Could not load Astrill endpoints":
            self.servers_loading = False
        if prefix == "Could not load router clients":
            self._clients_loading = False
        if prefix == "Could not load native Astrill settings":
            self._native_settings_loading = False
        if prefix in {
            "Could not apply Astrill connection",
            "Could not change Astrill connection",
            "Could not save Astrill connection",
            "Could not fully restore native Astrill",
        }:
            self.refresh_router()
        return GLib.SOURCE_REMOVE

    def _task_finished(self) -> None:
        self.busy_count = max(0, self.busy_count - 1)
        self.apply_button.set_sensitive(
            self.store.companion_enabled
            and not self.store.read_only
            and self.busy_count == 0
        )
        self.native_page.set_busy(self.busy_count != 0)
        self.connection_page.set_busy(self.busy_count != 0)
        self._update_recommendation_controls()

    def _require_write_access(self, action: str) -> bool:
        if not self.store.read_only:
            return True
        self.toast(
            f"Read-only access prevents {action}. "
            "Run “astrill-lazy access read-write” and reopen the app."
        )
        return False

    def toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=message, timeout=4))


def _scroll_page(content: Gtk.Widget) -> Gtk.Widget:
    viewport = Gtk.ScrolledWindow()
    viewport.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    viewport.set_child(content)
    viewport.add_css_class("page")
    return viewport


def _vertical_spacer() -> Gtk.Widget:
    spacer = Gtk.Box()
    spacer.set_vexpand(True)
    return spacer


def _clear_list(list_box: Gtk.ListBox) -> None:
    child = list_box.get_first_child()
    while child is not None:
        next_child = child.get_next_sibling()
        list_box.remove(child)
        child = next_child


def _empty_row(title: str, subtitle: str) -> Gtk.ListBoxRow:
    row = Gtk.ListBoxRow()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.add_css_class("empty-state")
    heading = Gtk.Label(label=title)
    heading.add_css_class("section-title")
    detail = Gtk.Label(label=subtitle)
    detail.add_css_class("muted")
    detail.set_wrap(True)
    box.append(heading)
    box.append(detail)
    row.set_child(box)
    row.set_activatable(False)
    return row


def _button_content(label: str, icon_name: str, *, expand: bool = False) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    box.append(Gtk.Image.new_from_icon_name(icon_name))
    text = Gtk.Label(label=label)
    text.set_xalign(0)
    text.set_hexpand(expand)
    box.append(text)
    return box


def _button_with_icon(
    label: str, icon_name: str, callback: Callable[..., Any]
) -> Gtk.Button:
    button = Gtk.Button()
    button.set_child(_button_content(label, icon_name))
    button.connect("clicked", callback)
    return button


def _set_status_class(label: Gtk.Label, good: bool) -> None:
    label.remove_css_class("status-good")
    label.remove_css_class("status-bad")
    label.add_css_class("status-good" if good else "status-bad")


def _policy_summary(policies: list[Rule]) -> str:
    count = len(policies)
    if not policies:
        return "No enabled policies"
    names = ", ".join(rule.name for rule in policies[:2])
    if count > 2:
        names = f"{names} +{count - 2}"
    return f"{count} {'policy' if count == 1 else 'policies'}: {names}"


def _latency_label(value: object) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "no reply"
    return f"{float(value):.0f} ms"


def _kind_label(kind: MatchKind) -> str:
    return {
        MatchKind.SERVICE: "Service",
        MatchKind.DOMAIN: "Website",
        MatchKind.CIDR: "IP network",
        MatchKind.DEVICE: "Device",
        MatchKind.PROCESS: "Application",
    }[kind]


def _kind_icon(kind: MatchKind) -> str:
    return {
        MatchKind.SERVICE: "view-app-grid-symbolic",
        MatchKind.DOMAIN: "web-browser-symbolic",
        MatchKind.CIDR: "network-server-symbolic",
        MatchKind.DEVICE: "network-computer-symbolic",
        MatchKind.PROCESS: "system-run-symbolic",
    }[kind]


def _category_icon(category: str) -> str:
    return {
        "AI": "applications-science-symbolic",
        "Cloud": "folder-remote-symbolic",
        "Commerce": "user-bookmarks-symbolic",
        "Development": "applications-development-symbolic",
        "Education": "accessories-dictionary-symbolic",
        "Finance": "wallet-symbolic",
        "Gaming": "applications-games-symbolic",
        "Local services": "find-location-symbolic",
        "Messaging": "chat-message-new-symbolic",
        "Music": "applications-multimedia-symbolic",
        "News": "text-x-generic-symbolic",
        "Reference": "accessories-dictionary-symbolic",
        "Remote access": "computer-symbolic",
        "Social": "system-users-symbolic",
        "Travel": "mark-location-symbolic",
        "Video": "applications-multimedia-symbolic",
        "Web": "web-browser-symbolic",
        "Work": "applications-office-symbolic",
    }.get(category, "applications-internet-symbolic")


def _normalize_domain(value: str) -> str:
    text = value.strip().lower()
    if "://" in text:
        text = urlparse(text).hostname or ""
    text = text.split("/")[0].rstrip(".")
    return text


def run_application() -> int:
    application = AstrillLazyApplication()
    return application.run(None)


def main() -> int:
    return run_application()


if __name__ == "__main__":
    raise SystemExit(main())
