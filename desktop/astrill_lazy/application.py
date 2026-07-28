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

from .astrill import AstrillServer, group_by_region, parse_applet
from .catalog import Catalog, discover_extensions, load_catalog
from .compiler import compile_rules
from .installer import RouterInstaller
from .launcher import ApplicationLauncher, parse_command
from .models import MatchKind, Region, RouteTarget, Rule
from .router import RouterClient
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
.location-current { background: #edf7f0; }
.sidebar-status { padding: 12px 16px; border-top: 1px solid #d8dde1; }
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
        ("devices", "Devices", "network-workgroup-symbolic"),
        ("locations", "Locations", "find-location-symbolic"),
        ("extensions", "Extensions", "application-x-addon-symbolic"),
    )

    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application)
        self.set_title("Astrill Lazy Router")
        self.set_default_size(1180, 760)
        self.set_size_request(880, 600)

        self.store = ConfigStore()
        self.catalog: Catalog = load_catalog(self.store.enabled_extensions)
        self.router = RouterClient(self.store.router_host)
        self.launcher = ApplicationLauncher()
        self.router_status: dict[str, Any] = {}
        self.servers: tuple[AstrillServer, ...] | None = None
        self.servers_loading = False
        self.server_groups: dict[str, tuple[AstrillServer, ...]] = {}
        self.clients: list[dict[str, str]] = []
        self.busy_count = 0
        self.dirty = False
        self._region_filter = "all"

        self._install_css()
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)
        self.split_view = Adw.NavigationSplitView()
        self.toast_overlay.set_child(self.split_view)
        self._build_sidebar()
        self._build_content()
        self._render_rules()
        self._render_services()
        self._render_extensions()
        self.refresh_router()
        self.load_servers()

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
        refresh.set_tooltip_text("Refresh router status")
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
        self.stack.add_named(self._build_devices_page(), "devices")
        self.stack.add_named(self._build_locations_page(), "locations")
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
            ("location", "Active location"),
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
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.service_search = Gtk.SearchEntry()
        self.service_search.set_placeholder_text("Search service, company, or domain")
        self.service_search.set_hexpand(True)
        self.service_search.connect(
            "search-changed", lambda _entry: self._render_services()
        )
        top.append(self.service_search)
        categories = [
            "All categories",
            *sorted({item.category for item in self.catalog.services}),
        ]
        self.category_dropdown = Gtk.DropDown.new_from_strings(categories)
        self.category_dropdown.connect(
            "notify::selected", lambda *_args: self._render_services()
        )
        top.append(self.category_dropdown)
        content.append(top)
        self.service_list = Gtk.ListBox()
        self.service_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.service_list.add_css_class("catalog-list")
        content.append(self.service_list)
        content.append(_vertical_spacer())
        return _scroll_page(content)

    def _build_devices_page(self) -> Gtk.Widget:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.add_css_class("page-content")
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Router Devices")
        title.set_xalign(0)
        title.set_hexpand(True)
        title.add_css_class("section-title")
        heading.append(title)
        manual = _button_with_icon(
            "Manual IP", "list-add-symbolic", self._show_device_dialog
        )
        heading.append(manual)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Reload DHCP clients")
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

    def _build_locations_page(self) -> Gtk.Widget:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.add_css_class("page-content")
        banner = Adw.Banner(
            title="One Astrill tunnel is available; all VPN policies share the active location."
        )
        banner.set_revealed(True)
        content.append(banner)
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.location_search = Gtk.SearchEntry()
        self.location_search.set_placeholder_text("Search Astrill locations")
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
        content.append(controls)
        self.location_list = Gtk.ListBox()
        self.location_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.location_list.add_css_class("catalog-list")
        content.append(self.location_list)
        content.append(_vertical_spacer())
        self._render_locations()
        return _scroll_page(content)

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

        router_heading = Gtk.Label(label="Router Companion")
        router_heading.set_xalign(0)
        router_heading.add_css_class("section-title")
        router_heading.add_css_class("toolbar-section")
        content.append(router_heading)
        self.router_companion_row = Adw.ActionRow(
            title="DD-WRT MyPage plugin",
            subtitle="Persistent controller, watchdog, status API, and policy page",
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
            "devices": "DHCP clients and fixed addresses",
            "locations": "Choose the shared Astrill country",
            "extensions": "Catalog and router components",
        }
        self.window_title.set_title(title)
        self.window_title.set_subtitle(subtitles[page_id])
        if page_id == "devices" and not self.clients:
            self.refresh_clients()
        if page_id == "locations" and self.servers is None:
            self.load_servers()

    def _show_services(self) -> None:
        self.nav_list.select_row(self.nav_rows["services"])

    def _show_devices(self) -> None:
        self.nav_list.select_row(self.nav_rows["devices"])

    def _render_rules(self) -> None:
        _clear_list(self.policy_list)
        if not self.store.rules:
            self.policy_list.append(
                _empty_row(
                    "No policies", "Add a service, website, device, or application."
                )
            )
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

    def _render_services(self) -> None:
        if not hasattr(self, "service_list"):
            return
        _clear_list(self.service_list)
        query = self.service_search.get_text().strip().casefold()
        category_index = self.category_dropdown.get_selected()
        category = (
            None
            if category_index == 0
            else self.category_dropdown.get_selected_item().get_string()
        )
        existing = {
            rule.selector
            for rule in self.store.rules
            if rule.match_kind is MatchKind.SERVICE
        }
        services = [
            service
            for service in self.catalog.services
            if (not query or query in service.search_text)
            and (category is None or service.category == category)
        ]
        for service in services:
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(service.name)
            row.set_subtitle(
                f"{service.company} · {service.category} · {len(service.domains)} domains"
            )
            row.add_prefix(
                Gtk.Image.new_from_icon_name(_category_icon(service.category))
            )
            route = Gtk.Label(
                label="DIRECT"
                if service.default_route is RouteTarget.DIRECT
                else "ASTRILL"
            )
            route.add_css_class("catalog-route")
            route.add_css_class(
                "catalog-direct"
                if service.default_route is RouteTarget.DIRECT
                else "catalog-vpn"
            )
            row.add_suffix(route)
            add = Gtk.Button.new_from_icon_name(
                "object-select-symbolic"
                if service.id in existing
                else "list-add-symbolic"
            )
            add.set_tooltip_text(
                "Policy already added"
                if service.id in existing
                else "Add service policy"
            )
            add.set_sensitive(service.id not in existing)
            add.set_valign(Gtk.Align.CENTER)
            add.connect(
                "clicked",
                lambda _button, service_id=service.id: self._add_service(service_id),
            )
            row.add_suffix(add)
            self.service_list.append(row)
        if not services:
            self.service_list.append(
                _empty_row("No matching services", "Try a company name or domain.")
            )

    def _render_devices(self) -> None:
        if not hasattr(self, "device_list"):
            return
        _clear_list(self.device_list)
        if not self.clients:
            self.device_list.append(
                _empty_row("No clients loaded", "Refresh to read current DHCP leases.")
            )
            return
        for client in sorted(
            self.clients, key=lambda item: (item.get("hostname", ""), item["address"])
        ):
            hostname = client.get("hostname") or "Unknown device"
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(hostname if hostname != "*" else "Unknown device")
            row.set_subtitle(f"{client['address']} · {client['mac']}")
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

    def _render_locations(self) -> None:
        if not hasattr(self, "location_list"):
            return
        _clear_list(self.location_list)
        if self.servers is None:
            self.location_list.append(
                _empty_row(
                    "Locations not loaded", "Open this page to read the Astrill applet."
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
        visible = [
            server
            for server in self.servers
            if (not query or query in server.name.casefold())
            and (allowed is None or server.id in allowed)
        ]
        for server in visible:
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(server.name)
            row.set_subtitle(
                f"Server {server.id} · {len(server.nodes)} endpoint groups"
            )
            if server.id == current_id:
                row.add_css_class("location-current")
                current = Gtk.Image.new_from_icon_name("object-select-symbolic")
                current.set_tooltip_text("Current Astrill location")
                row.add_prefix(current)
            else:
                row.add_prefix(Gtk.Image.new_from_icon_name("network-vpn-symbolic"))
            connect = Gtk.Button(
                label="Connected" if server.id == current_id else "Connect"
            )
            connect.add_css_class("compact-button")
            connect.set_sensitive(server.id != current_id)
            connect.set_valign(Gtk.Align.CENTER)
            connect.connect(
                "clicked",
                lambda _button, item=server: self._confirm_switch_server(item),
            )
            row.add_suffix(connect)
            self.location_list.append(row)
        if not visible:
            self.location_list.append(
                _empty_row("No matching locations", "Change the region or search text.")
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
        self._render_rules()
        self._render_services()
        self._render_locations()
        self._render_extensions()
        self.toast("Extension settings updated")

    def _set_rule_target(
        self, rule: Rule, target: RouteTarget, dropdown: Gtk.DropDown
    ) -> None:
        if rule.target is target:
            return
        rule.target = target
        dropdown.set_sensitive(target is RouteTarget.VPN)
        region_ids = [region.id for region in self.catalog.regions]
        if target is RouteTarget.DIRECT:
            rule.region = "direct"
        elif rule.region == "direct":
            rule.region = "active-astrill"
        dropdown.set_selected(region_ids.index(rule.region))
        self._changed()

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

    def _add_service(self, service_id: str) -> None:
        service = self.catalog.services_by_id[service_id]
        if any(
            rule.match_kind is MatchKind.SERVICE and rule.selector == service_id
            for rule in self.store.rules
        ):
            self.toast("This service already has a policy")
            return
        rule = Rule.create(
            name=service.name,
            match_kind=MatchKind.SERVICE,
            selector=service.id,
            target=service.default_route,
            region=service.preferred_region,
            priority=self._next_priority(),
        )
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
        route.set_selected(1)
        region_label = Gtk.Label(label="Preferred Astrill region")
        region_label.set_xalign(0)
        region = Gtk.DropDown.new_from_strings(
            [item.name for item in self._vpn_regions()]
        )
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

    def apply_configuration(self, _button: Gtk.Button | None = None) -> None:
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

    def refresh_router(self) -> None:
        self._run_task(
            self.router.status,
            self._router_refreshed,
            "Could not reach the router",
            quiet=True,
        )

    def _router_refreshed(self, status: dict[str, Any]) -> None:
        self.router_status = status
        self._update_status()
        self._render_locations()

    def _update_status(self) -> None:
        status = self.router_status
        healthy = status.get("health") == "healthy"
        self.metrics["health"].set_label("Healthy" if healthy else "Needs attention")
        _set_status_class(self.metrics["health"], healthy)
        tunnel = status.get("vpn_state") == "up"
        self.metrics["tunnel"].set_label("Connected" if tunnel else "Disconnected")
        _set_status_class(self.metrics["tunnel"], tunnel)
        server_id = int(status.get("astrill_server_id", 0))
        server = self._server_by_id(server_id)
        self.metrics["location"].set_label(
            server.name if server else f"Server {server_id}"
        )
        self.metrics["rules"].set_label(str(status.get("origin_count", 0)))
        if healthy:
            self.sidebar_status_icon.set_from_icon_name("network-vpn-symbolic")
            self.sidebar_status_label.set_label("Router connected")
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
        installed = status.get("version") not in {None, "", "unknown"}
        self.router_companion_icon.set_from_icon_name(
            "object-select-symbolic" if installed else "network-offline-symbolic"
        )

    def refresh_clients(self) -> None:
        self._run_task(
            self.router.clients,
            self._clients_refreshed,
            "Could not load router clients",
        )

    def _clients_refreshed(self, clients: list[dict[str, str]]) -> None:
        self.clients = clients
        self._render_devices()
        self.toast(f"Loaded {len(clients)} DHCP clients")

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
            self._render_locations()
            self._update_status()

        self._run_task(load, success, "Could not load Astrill locations")

    def _on_location_filter(self, dropdown: Gtk.DropDown, _param: Any) -> None:
        self._region_filter = self.location_filter_regions[dropdown.get_selected()].id
        self._render_locations()

    def _server_by_id(self, server_id: int) -> AstrillServer | None:
        if self.servers is None:
            return None
        return next((server for server in self.servers if server.id == server_id), None)

    def _confirm_switch_server(self, server: AstrillServer) -> None:
        dialog = Adw.MessageDialog.new(
            self,
            f"Connect to {server.name}?",
            "The shared Astrill tunnel will reconnect, briefly pausing VPN-routed traffic.",
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
        protocol = int(self.router_status.get("astrill_protocol", 0))
        try:
            sid, endpoint = server.endpoint_for(protocol)
        except ValueError as exc:
            self.toast(str(exc))
            return

        def switch() -> dict[str, Any]:
            return self.router.switch_astrill(
                server_id=server.id,
                sid=sid,
                encoded_ip=endpoint.encoded_ip,
                port=endpoint.port,
                port_index=endpoint.port_index,
                protocol=protocol,
                vpn_mode=endpoint.vpn_mode_for(protocol),
            )

        def success(status: dict[str, Any]) -> None:
            self.router_status = status
            self.store.active_region = self._region_for_server(server)
            self.store.save()
            self._update_status()
            self._render_locations()
            self.toast(f"Connected to {server.name}")

        self._run_task(switch, success, "Astrill location switch failed")

    def _region_for_server(self, server: AstrillServer) -> str:
        for region_id, servers in self.server_groups.items():
            if any(item.id == server.id for item in servers):
                return region_id
        return "other"

    def launch_app(self, rule: Rule) -> None:
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

    def install_router(self, _button: Gtk.Button | None = None) -> None:
        self._run_task(
            lambda: RouterInstaller(self.router).install(),
            lambda result: (
                self.toast(f"Router companion {result.version} installed"),
                self.refresh_router(),
            ),
            "Router companion installation failed",
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
        if prefix == "Could not load Astrill locations":
            self.servers_loading = False
        return GLib.SOURCE_REMOVE

    def _task_finished(self) -> None:
        self.busy_count = max(0, self.busy_count - 1)
        self.apply_button.set_sensitive(self.busy_count == 0)

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
        "Commerce": "user-bookmarks-symbolic",
        "Development": "applications-development-symbolic",
        "Gaming": "applications-games-symbolic",
        "Messaging": "chat-message-new-symbolic",
        "Music": "applications-multimedia-symbolic",
        "Video": "applications-multimedia-symbolic",
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
