# Desktop Application

Astrill Lazy Router has two native frontends: GTK 4/Libadwaita on Ubuntu and
PySide6/Qt on Windows. Both use key-only SSH to inspect native Astrill and
control the optional DD-WRT companion. Windows installation, SSH host-key
verification, and platform limitations are covered in
[Native Windows Application](WINDOWS_APP.md).

## Ubuntu Installation

The source deployment is installed without root:

```bash
./scripts/install-desktop.sh
```

This creates a system-site-enabled virtual environment at `.venv`, installs the
editable package, adds `astrill-lazy` and `astrill-lazy-gui` under
`~/.local/bin`, and registers the desktop entry under
`~/.local/share/applications`. Login startup is not enabled on a fresh
installation. Opt in with `ASTRILL_LAZY_ENABLE_AUTOSTART=1`, or enable it later
through the GUI/CLI; no root password is needed.

The installer selects Python 3.12, then 3.11, before a generic `python3`, and
rejects Python 3.10 or older. Set `ASTRILL_LAZY_PYTHON` to an explicit
compatible interpreter when several Conda/system installations coexist.

Launch it from the Ubuntu application menu or run:

```bash
astrill-lazy-gui
```

Manage login startup from the Extensions view or the CLI:

```bash
astrill-lazy autostart enable
astrill-lazy autostart status
astrill-lazy autostart disable
```

At startup and every 60 seconds, a native-only or read-only Ubuntu GUI performs
status reads only. A writable Ubuntu configuration with companion mode enabled
checks the DD-WRT companion: a missing or outdated companion is installed,
while a current companion with a stopped watchdog is repaired in place. A
healthy runtime is only read, not rewritten.
The package fingerprint prevents an identical broken package from being
automatically written on every monitor cycle; the Install/Upgrade action is the
explicit recovery override.

## Native Windows Installation

Build and install the Qt frontend from Windows PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\contrib\windows\build-native.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\contrib\windows\install-native.ps1
```

The application is installed under
`%LOCALAPPDATA%\Programs\Astrill Lazy Router`. The installer creates one
Desktop shortcut, removes the older same-named Start Menu shortcut, creates no
new Start Menu entry, and does not launch the application. It needs Windows
OpenSSH Client at runtime but does not need WSL, GTK, or noVNC.

The Windows frontend refreshes status every 60 seconds but does not
automatically install or repair the companion. Those actions remain explicit
in its Router view. Follow the
[Windows build, install, and verified SSH setup](WINDOWS_APP.md) before first
use.

## Frontend Differences

| Capability | Ubuntu GTK frontend | Windows Qt frontend |
| --- | --- | --- |
| Native Astrill inspection | Yes | Yes |
| Companion policies and recovery | Yes | Yes |
| Service, domain, network, and device policy | Yes | Yes |
| Endpoint and safe native-setting controls | Yes | Yes |
| Per-application routing identity | Linux macvlan namespace | Not available; no Windows WFP backend |
| Route detection and recommendations | Yes | Not exposed |
| Extension and login-startup controls | Yes | Not exposed |
| Isolated noVNC session | Optional | Not needed |

## Isolated Ubuntu noVNC Debugging

Run visual tests without opening a window in the active Ubuntu session:

```bash
./scripts/run-novnc-debug.sh
```

The default local URL is:

```text
http://127.0.0.1:6087/vnc.html?host=127.0.0.1&port=6087&autoconnect=1&resize=scale
```

The script uses X display `:45`, VNC port `5927`, and web port `6087`. Override
them with `ASTRILL_LAZY_NOVNC_DISPLAY`, `ASTRILL_LAZY_VNC_PORT`, and
`ASTRILL_LAZY_NOVNC_PORT`. Both listeners bind only to loopback, and the
separate X display cannot move or focus windows in the active desktop session.

The current deployment keeps this isolated controller available after reboot
through the lingering user systemd instance:

```bash
./scripts/install-novnc-service.sh
systemctl --user status \
  io.github.lachlanchen.AstrillLazyRouter.NoVNC.service
```

The installer renders the unit from the actual checkout path, so clones named
`astrill-lazy-router` or stored outside `~/Projects` do not depend on a
hard-coded source directory. It also records the selected display and ports in
`~/.config/astrill-lazy/novnc.env`. The validated defaults deliberately avoid
port `6086`, which another virtual desktop already owns on this workstation:

```bash
./scripts/install-novnc-service.sh
```

The Mac desktop application `Astrill Lazy Router.app` and legacy Windows
shortcuts
create a passwordless SSH local forward from `127.0.0.1:16087` to the
workstation's loopback-only port `6087`, then open the controller. They never
expose noVNC to the LAN. Their auditable sources are
[`contrib/macos/`](../contrib/macos/) and
[`contrib/windows/`](../contrib/windows/). Install them on each client:

```bash
# macOS
./contrib/macos/install-launcher.sh
```

```powershell
# Windows PowerShell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\contrib\windows\install-launcher.ps1
```

The macOS installer adds the application to the Dock. The legacy Windows
noVNC installer creates both Desktop and Start Menu shortcuts. It is separate
from `install-native.ps1`, which installs the Qt application with a Desktop
shortcut only and removes its same-named Start Menu shortcut. Do not install
both Windows options unless replacing the same-named shortcut is intentional.
The launchers do not run or take focus until opened by the user.

## Ubuntu Views

The detailed views below describe the full Ubuntu frontend. The native Windows
frontend provides Policies, Services, Countries, Devices, Endpoints, Astrill,
Router, and Settings with the boundaries summarized above and detailed in
[its platform guide](WINDOWS_APP.md).

### Policies

The first screen shows controller health, tunnel state, active Astrill
endpoint, and enabled policy count. A configured server is not reported as
active while the tunnel is disconnected. Each rule has:

- an enable switch;
- Direct/Astrill segmented routing;
- a preferred Astrill region;
- an application launch button when applicable;
- a delete action.

Local edits are saved immediately. The orange Apply action compiles and
transactionally installs the full rule set on DD-WRT.

`Detect` measures the same representative IPv4 destination through the WAN
interface and `tun0`. Results show Direct and Astrill path latency on each
enabled service or website policy. Recommendations require a meaningful
improvement, keep the current route when both paths cannot be compared, and
honor the catalog's access profile. UU Remote, WeChat, Taobao, and Meituan are
minimum Direct recommendations. Detection never changes routing;
`Apply Recommendations` is a separate explicit action.

### Services

The 261-profile catalog can be searched by service, company, alias, or seed
domain and filtered by provider country, category, and profile type. Catalog
rows show the actual route for an existing policy and the mixed catalog
suggestion otherwise. `Select visible` selects the current combined filter
result. The Suggested, Direct, and Astrill batch modes then add new selections
or update existing selected policies in one saved transaction.

Suggested mode preserves each profile's catalog route and preferred endpoint
country. Direct forces every selected service to WAN. Astrill preserves an
existing VPN country, uses the catalog preference for a new VPN service, and
uses the active Astrill endpoint when a Direct-only profile has no VPN country.

### Countries

Country is a preference attached to each VPN policy. The Countries view
aggregates enabled policies by preference, shows the number of matching Astrill
endpoints, identifies the active country, and links directly to its endpoints.
It warns when policies request several specific countries or when the connected
endpoint does not satisfy the one requested country.

### Devices

The companion merges current DHCP leases, configured static reservations, and
active ARP neighbors on the DD-WRT LAN bridge. Native-only mode reads and
merges the same sources directly without installing or writing a runtime file.
Duplicate MAC addresses collapse
to one device, each source and online state is shown, and WAN neighbors are
excluded. An entirely offline device with no remaining lease or static
reservation cannot be discovered; its fixed address can be added manually.
Every listed device can become a direct or Astrill source policy with one
action.

### Endpoints

The app reads the installed Astrill applet, groups its servers by configured
country tokens, identifies the current endpoint, and can reconnect the shared
tunnel using a selected Astrill protocol. A confirmation is required because
reconnecting pauses VPN-routed traffic.

### Router

The Router view reports the upstream Astrill connection and companion policy
runtime. It exposes endpoint selection, runtime repair, domain refresh,
confirmation-gated policy rollback, and idempotent companion installation or
upgrade. `Restore Astrill Only` removes every companion-owned runtime, NVRAM,
MyPage, firewall, policy-rule, and watchdog object while preserving Astrill's
endpoint, protocol, and connection state. The saved desktop mode then prevents
the 60-second monitor from reinstalling it.

### Astrill

The Astrill view reads the native applet's settings directly from DD-WRT and
maps include/exclude choices to effective Direct/Astrill defaults and
per-entry routes. It synchronizes website and device lists, Wi-Fi and VLAN
filters, DNS, cipher, MTU, kill switch, startup, favorites summary, and
advanced filter fields. Opening or refreshing the view is read-only. Only
changed controls are written, every value is validated, and the complete
result is read back before success is reported. Account and router
credentials are outside the allowed key set.

### Extensions

Installed catalogs can be enabled, disabled, inspected, and opened in the file
manager. The core catalog is mandatory. The same view controls login startup,
reports whether the DD-WRT companion is installed, and can perform an
idempotent upgrade.

## Ubuntu Application Profiles

Add an Application policy with an absolute executable and optional arguments.
The first Launch action:

1. requests Polkit administrator authorization;
2. creates or reuses a named macvlan namespace;
3. obtains a distinct DD-WRT DHCP lease;
4. saves that address in the rule;
5. applies the router policy;
6. launches the executable as the desktop user.

The namespace helper is
`helpers/astrill-lazy-netns`. It requires `/usr/bin/busybox`, `ip`, `runuser`,
and `pkexec`, all present on this Ubuntu machine. Authorization cancellation is
reported without changing the router rule.

Application namespaces last until explicitly removed, rebooted, or deleted
through the app. BusyBox `udhcpc` remains inside each namespace to renew its
lease.

## Configuration

The Ubuntu configuration is
`~/.config/astrill-lazy/config.json` and is written with mode `0600`. The
Windows configuration is
`%LOCALAPPDATA%\Astrill Lazy Router\config.json`; it is separate from the
installed program and survives uninstall. `XDG_CONFIG_HOME`, when explicitly
set, overrides the platform default. Both use schema version 1.
It contains:

- the SSH host alias;
- active country and endpoint metadata;
- enabled extension IDs;
- editable source rules;
- application profile metadata and allocated lease addresses;
- the enabled/disabled companion state and route recommendation results.
- the read-only/read-write router access guard.

It contains no Astrill account credential or SSH private key.

A fresh configuration is native-only, read-only, and has no seeded policy.
Legacy files that predate the access guard retain their existing writable
companion behavior. See [native-only operation](NATIVE_ONLY.md).
