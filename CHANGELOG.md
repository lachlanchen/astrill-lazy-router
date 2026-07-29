# Changelog

## Unreleased

- Add a strict device-local policy schema and read-only planner for Direct,
  fixed-tunnel, and health-selected three-endpoint routes.
- Add application, service, domain, IP network, and ISO-country matching with
  named country groups, deterministic precedence, route fallback, hysteresis,
  and country-prefix compilation.
- Extend router refresh operations to 180 seconds and report SSH command
  timeouts cleanly, because a complete DNS refresh can exceed the normal
  interactive timeout.
- Add a boot-persistent isolated noVNC service and a secure Mac controller
  launcher source.

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
