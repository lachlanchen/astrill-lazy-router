# Changelog

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
