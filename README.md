# Astrill Lazy Router

Astrill Lazy Router is a native Ubuntu controller and DD-WRT companion for
placing traffic beside, or through, the active Astrill tunnel. It adds a policy
layer without modifying Astrill's applet files or mark space.

The current deployment provides:

- direct or Astrill routing by service, website, IPv4 network, device, protocol,
  and port;
- isolated per-application identities on Ubuntu using macvlan network
  namespaces;
- a native GTK 4 and Libadwaita control application;
- bidirectional native Astrill website, device, interface, DNS, and connection
  settings using effective Direct/Astrill controls;
- one-click Direct/Astrill path detection, per-policy recommendations, and
  explicit batch apply;
- a companion page at DD-WRT `/MyPage.asp?3`;
- an extension catalog with 261 company, app, and website profiles across 19
  categories and 14 region groups;
- guarded A/B policy activation, rollback, watchdog recovery, and fail-closed
  VPN routes;
- desktop login startup and automatic companion installation or runtime repair;
- an audited native-only restore that disables automatic companion reinstall;
- discovery and switching of all 178 server endpoints in the installed Astrill
  applet;
- a validated, non-enforcing device policy schema for Direct, fixed-tunnel,
  and three-endpoint Auto routing by app, service, domain, network, or
  destination country;
- an encrypted, round-trip-verified recovery backup.

The current deployment keeps `UU Remote`, `WeChat`, `Taobao`, and `Meituan`
Direct while all other traffic follows native Astrill global routing.

## Run

The user-local desktop package is installed at
`/home/lachlan/Projects/astrill-lazy/.venv`.

```bash
astrill-lazy-gui
```

Useful CLI commands:

```bash
astrill-lazy status
astrill-lazy apply
astrill-lazy servers
astrill-lazy refresh
astrill-lazy rollback
astrill-lazy install-router
astrill-lazy autostart status
astrill-lazy device-policy validate examples/device-policy.sample.json
```

The SSH target defaults to the `astrill-router` host alias. The desktop
configuration is stored with mode `0600` at
`~/.config/astrill-lazy/config.json`.
The source installer enables user-session startup at
`~/.config/autostart/io.github.lachlanchen.AstrillLazyRouter.desktop`.
On launch, the GUI checks the companion and then reconciles it every 60
seconds. A healthy current runtime causes no router write.

## Country Model

This router has one Astrill tunnel. A rule can select:

- `Direct`, which uses the WAN gateway; or
- `Astrill`, which uses the one currently active Astrill endpoint.

The GUI stores a preferred country for each VPN policy. The Countries view
shows assignments, available endpoint counts, the active country, and
incompatible simultaneous preferences. The Endpoints view selects the actual
Astrill server for the shared tunnel. One tunnel cannot provide several active
countries at once. `No country override` follows the current endpoint.

## Current Deployment

- Router: Linksys E4200
- Firmware: DD-WRT `v3.0-r62374 mega`
- Astrill applet: `2.9.52`
- Router runtime: `/tmp/astrill-lazy`
- DD-WRT pages: policy `3`, status API `4`
- Desktop/catalog: `0.2.2`
- Router plugin: `0.2.2`
- Astrill tunnel: connected to `*USA - Los Angeles A` with RouterPro UDP
- Native default: global Astrill with the existing device exclusion preserved
- Companion policy: four Direct service groups, 56 compiled rules
- Verified services: Google, YouTube, and Instagram through Astrill; UU Remote,
  WeChat, Taobao, and Meituan through Direct

## Safety

Raw applet files, account state, generated OpenVPN configuration, and installer
credentials are ignored by Git. The complete snapshot is committed only as CMS
ciphertext under `backups/`; its private decryption key remains outside this
repository. Native credentials are never read by the desktop settings mirror.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Astrill and DD-WRT analysis](docs/ASTRILL_ANALYSIS.md)
- [Desktop application](docs/DESKTOP_APP.md)
- [Router installation and rollback](docs/ROUTER_INSTALL.md)
- [Rule model](docs/RULE_MODEL.md)
- [Device-local routing](docs/DEVICE_ROUTING.md)
- [Extensions](docs/EXTENSIONS.md)
- [Backup and restore](docs/BACKUP_RESTORE.md)
- [Security](docs/SECURITY.md)
- [Testing and operations](docs/TESTING.md)
- [Changelog](CHANGELOG.md)
