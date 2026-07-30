# Changelog

## 0.2.9 - 2026-07-30

- Add native Astrill favorite synchronization to the Ubuntu and Windows
  endpoint browsers so favorites can be read from and changed on DD-WRT.
- Confirm every add or remove, refresh the favorite list immediately before
  writing, compare it on the router before replacement, commit only
  `astrill_favlist` once, and verify the complete readback.
- Preserve unknown favorite records and unsaved Astrill-page edits, and block
  favorite writes when the router value is malformed or the local page is
  dirty.
- Mirror Astrill's automatic next-favorite reconnect and router-boot startup
  controls beside the endpoint list while retaining the complete Connection
  editor and its unsaved-change protection.
- Keep synchronization event-driven through startup, page demand, explicit
  refresh, and completed-action reads; no recurring SSH poll, endpoint
  reconnect, latency test, or companion operation is added.

## 0.2.8 - 2026-07-30

- Persist validated Windows PC endpoint-latency results in a separate local
  cache without triggering automatic probes or involving DD-WRT.
- Add explicit Default, Region, and numeric PC-latency endpoint ordering while
  retaining selection across filters and marking stale or changed targets for
  a manual retest.

## 0.2.7 - 2026-07-30

- Add stable Country, Ping, and Action columns to the Endpoints view using
  normalized country names from Astrill's installed server catalog.
- Make the Country and Ping headers sortable in both directions while keeping
  pending, no-reply, and unmeasured endpoints below valid latency results.
- Preserve Astrill's applet order by default and keep measurement behind the
  explicit filtered Ping action.

## 0.2.6 - 2026-07-29

- Parse Astrill's encoded endpoint-address map and expose a validated TCP probe
  target for every current applet location.
- Add on-demand, visible-row endpoint latency measurements to the Ubuntu
  frontend with bounded desktop concurrency, stable result labels, timeout
  reporting, and no router write or background polling.
- Prefer the current OptiPlex 7090 hostname, LAN address, and macOS SSH alias in
  the secure Mac and Windows noVNC launchers.

## 0.2.5 - 2026-07-29

- Collapse native status, allowlisted settings, companion presence, and
  companion health into one read-only SSH startup snapshot.
- Reuse the startup snapshot for the connection mirror and reserve additional
  package and repair checks for detected companion degradation.
- Sequence the larger endpoint-catalog transfer after startup health checks so
  the router does not process concurrent SSH sessions during GUI launch.
- Add live verification evidence for all-device UU Remote Direct routing and
  matched Ubuntu/macOS performance through the active Astrill endpoint.
- Replace LINE's non-resolving legacy app domain with its current official
  LIFF and Mini App hosts.

## 0.2.4 - 2026-07-29

- Add a dedicated Connection view that mirrors Astrill's endpoint, protocol,
  endpoint-specific port, favorite, cipher, MTU, acceleration, kill switch,
  favorite cycling, and router-boot connection settings.
- Derive supported protocols and common port choices from the installed
  applet's server records instead of presenting invalid combinations.
- Add verified Save, Connect, Disconnect, and Apply & Reconnect flows with
  confirmation and recovery of previous native settings after a failed start.
- Synchronize native connection changes at launch, on explicit refresh, and
  from completed-action readback while preserving pending local edits and
  reporting concurrent router changes.
- Keep the unchanged router companion package at 0.2.3 so this desktop-only
  update does not request an unnecessary DD-WRT package rewrite.

## 0.2.3 - 2026-07-29

- Add automatic SSH, native Astrill, and companion checks; configurable stable
  key-only SSH; confirmation-gated companion installation; and transient,
  hash-reviewed user-provided Astrill installation.
- Restart the persistent noVNC and GTK controller stack after any component
  exits, while retaining boot startup through the lingering user manager.
- Add a native-only, read-only first-run mode so an already-working Astrill
  router can be inspected without companion installation or router writes.
- Start the native Windows frontend after sign-in through a per-user Startup
  shortcut, recreate it safely on update, and remove it on uninstall without
  adding a Start Menu entry.
- Reconcile native Windows status after router reboot: reuse a healthy
  companion, reconstruct its validated runtime from retained NVRAM, or fall
  back to native-only mode when the router no longer retains it, without
  silently rewriting a package.
- Replace the Windows raw NVRAM table with Ubuntu-aligned, human-readable
  Astrill controls that retain exact safe-key metadata, change detection,
  validation, confirmation, and readback.
- Keep background Windows OpenSSH status, endpoint, host-key, and identity
  helpers console-free while retaining the explicitly requested interactive
  SSH terminal.
- Replace recurring 60-second desktop SSH polling with one startup check,
  explicit refreshes, page-demand reads, and status returned by completed
  actions. Cache successful empty inventories and make boot-order retries
  manual so an early login launch does not create a polling loop.
- Add an explicitly triggered Windows PC-side endpoint test with selected,
  visible, or all-loaded scope. It reports bounded TCP-connect latency without
  sending router commands, switching endpoints, or claiming throughput.
- Preserve writable companion behavior for legacy configuration files while
  adding an explicit `astrill-lazy access` guard for new deployments.
- Add companion-free LAN client discovery and a sanitized native inspection
  report covering status, effective routes, endpoints, and client counts.
- Validate a second Linksys E4200 using native Include routing, and document
  DD-WRT key-only SSH setup, BusyBox limitations, and safe Telnet recovery.
- Render the noVNC user service from the actual checkout path and make desktop
  login autostart opt-in.
- Select and verify Python 3.11 or newer during desktop installation instead of
  trusting a potentially incompatible Conda `python3`.
- Add a branded project README, repository screenshot, GitHub Sponsors panel,
  and concise guides across the profile's 11-language navigation set.
- Add provider-country filtering, persistent multi-selection, and Suggested,
  Direct, or Astrill batch actions to the Services catalog.
- Restore mixed per-service route suggestions while showing the actual route
  for services that already have policies.
- Add a strict device-local policy schema and read-only planner for Direct,
  fixed-tunnel, and health-selected three-endpoint routes.
- Add application, service, domain, IP network, and ISO-country matching with
  named country groups, deterministic precedence, route fallback, hysteresis,
  and country-prefix compilation.
- Extend router refresh operations to 180 seconds and report SSH command
  timeouts cleanly, because a complete DNS refresh can exceed the normal
  interactive timeout.
- Make explicit companion matches a reversible overlay of native Global,
  Include, and Exclude modes, with guarded earlier policy preferences and exact
  cleanup of companion-owned legacy preferences.
- Add a boot-persistent, configurable isolated noVNC service with deterministic
  window fitting and secure, idempotent macOS Dock and Windows Desktop/Start
  Menu launchers.

## 0.2.2 - 2026-07-29

- Mirror native Astrill website, device, Wi-Fi, VLAN, DNS, connection, and
  advanced settings in the desktop GUI with validated, verified NVRAM writes.
- Flatten native include/exclude modes into effective Direct/Astrill controls
  while preserving native rule precedence and incremental companion behavior.
- Add one-click path detection with per-policy Direct/Astrill latency,
  conservative service-aware recommendations, and explicit batch apply.
- Keep UU Remote, WeChat, Taobao, and Meituan as minimum Direct
  recommendations and expand their common app, web, CDN, and literal endpoint
  coverage.
- Add native Astrill connect/disconnect controls and a fully audited
  `Restore Astrill Only` path that removes the companion without changing the
  active endpoint, protocol, or tunnel state.
- Serialize firewall mutations with the xtables lock and verify connected and
  disconnected behavior, native-only restoration, noVNC layouts, and live
  traffic marks.

## 0.2.1 - 2026-07-28

- Separate preferred policy countries from selectable Astrill server endpoints
  in the native GUI.
- Add a Countries view with per-country policy counts, endpoint counts,
  active-country state, and single-tunnel conflict or mismatch warnings.
- Merge DHCP leases, static reservations, and active LAN neighbors in the
  companion device inventory while excluding the WAN interface.
- Add Astrill protocol selection and connection, runtime repair, domain
  refresh, guarded rollback, and companion upgrade controls to the Router view.
- Add the Nutstore (Jianguoyun) profile and verified application endpoints.
- Keep GUI debugging isolated on a dedicated noVNC display.

## 0.2.0 - 2026-07-28

- Expand the core catalog to 260 company, application, and website profiles
  across 19 categories and five maintainable data files.
- Add category and profile-type filters, deterministic sorting, live result
  counts, and broader category icons to the native Services view.
- Validate catalog paths, duplicate JSON keys, profile fields, region
  references, source URLs, seed limits, and compiled router payload size.
- Add a catalog audit command with optional concurrent IPv4 DNS checks.
- Start the desktop controller after user login and expose that setting in the
  GUI and CLI.
- Reconcile the router companion at GUI startup and every 60 seconds, repairing
  its runtime before considering an idempotent reinstall.
- Guard automatic recovery with the deterministic package fingerprint to avoid
  repeated writes of an identical broken NVRAM package.
- Distinguish a configured Astrill location from an active tunnel after reboot.
- Verify companion persistence, direct routing, and Internet access after a
  physical router reboot.

## 0.1.0 - 2026-07-28

- Add the native GTK 4 and Libadwaita policy application.
- Add the persistent DD-WRT controller, watchdog, status API, and MyPage UI.
- Add direct and active-Astrill rules for services, domains, networks, devices,
  protocols, and destination ports.
- Add Polkit-authorized application identities using macvlan namespaces.
- Add Astrill location discovery and guarded server switching.
- Add the 44-service core catalog and data-only extension loader.
- Add A/B activation, rollback, fail-closed VPN routes, and domain refresh.
- Add encrypted pre-install backup and detailed recovery documentation.
