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

At startup, the Ubuntu GUI performs one environment check covering key-only
SSH, native status and settings, companion presence, and companion health in
one read-only snapshot. A healthy current companion is only read. A current
stored package with a stopped runtime can be repaired in place. A missing,
outdated, or non-repairable package opens a confirmation dialog and is never
installed silently. The larger applet endpoint catalog is fetched afterward,
so startup does not place two concurrent SSH handshakes on the router.

The desktop does not repeat that check on a timer or poll DD-WRT over SSH in
the background. Later reads are manual, requested when a page first needs its
data, or returned by a completed router action. Successfully loaded empty
device data is cached too, so changing pages does not cause repeated reads.
Use the header Refresh action after DD-WRT becomes reachable if login startup
ran before the router finished booting. The package fingerprint and explicit
Install/Upgrade action remain the safeguards against rewriting a broken
package.

## Ubuntu Router Onboarding

The Ubuntu Router page stores only the host, SSH user, port, and dedicated
identity path. Fresh defaults are `192.168.1.1`, `root`, port `22`, and
`~/.ssh/astrill_lazy_router_ed25519`. Save & Check creates the local Ed25519 key
when needed and tests it with bounded connection attempts and keepalives.
Authorize Key accepts a transient router password, verifies key login before
disabling SSH password login, and never writes the password to disk.

If the native Astrill applet is absent, Install Applet accepts a user-provided
URL or shell command. The displayed template replaces both private installer
path values with `xxx`. The desktop downloads or accepts at most 512 KiB,
reports the SHA-256 digest and transport security, then asks again before
running the script as router root. The input, downloaded script, and installer
token are not persisted.

Copyable redacted template:

```sh
eval `wget -q -O - http://astroutercn.com/router/install/xxx/xxx`
```

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
Desktop shortcut and one current-user Startup shortcut, removes the older
same-named Start Menu shortcut, creates no new Start Menu entry, and does not
launch the application during installation. The Startup shortcut opens it
after Windows sign-in, including after a reboot. It needs Windows OpenSSH
Client at runtime but does not need WSL, GTK, or noVNC.

The Windows frontend performs one status and reconciliation check when it
starts, then uses manual refreshes, first-page data loads, and status returned
by completed actions. It does not poll the router in the background. For a
previously confirmed companion, the startup or manual check can restore the
validated current runtime from the package retained in router NVRAM after a
reboot; this does not rewrite NVRAM. If Windows login startup runs before the
router is ready, select **Refresh router** after DD-WRT becomes reachable. If
the router retained no companion, the check falls back to native-only mode.
Installing or rewriting a package remains an explicit action in the Router
view. Follow the
[Windows build, install, and verified SSH setup](WINDOWS_APP.md) before first
use. Its spacious native layout uses a scrollable Settings page and adapts its
opening size to the available Windows desktop. Guided Settings onboarding can
generate the dedicated Ed25519 key and authorize its public half through a
confirmation-gated, one-time LAN Telnet session.

## Frontend Differences

| Capability | Ubuntu GTK frontend | Windows Qt frontend |
| --- | --- | --- |
| Native Astrill inspection | Yes | Yes |
| Companion policies and recovery | Yes | Yes |
| Service, domain, network, and device policy | Yes | Yes |
| Endpoint and safe native-setting controls | Yes | Yes |
| Manual endpoint TCP latency | Visible rows; desktop-side Ping | Selected, visible, or all rows; persistent PC-side results |
| Per-application routing identity | Linux macvlan namespace | Not available; no Windows WFP backend |
| Route detection and recommendations | Yes | Not exposed |
| Extension and login-startup controls | Yes | Startup is installer-managed; no in-app switch |
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
through the lingering user systemd instance, without requiring an interactive
desktop login:

```bash
./scripts/install-novnc-service.sh
systemctl --user status \
  io.github.lachlanchen.AstrillLazyRouter.NoVNC.service
```

The installer renders the unit from the actual checkout path, so clones named
`astrill-lazy-router` or stored outside `~/Projects` do not depend on a
hard-coded source directory. It also records the selected display and ports in
`~/.config/astrill-lazy/novnc.env`. The unit supervises Xvfb, Openbox, x11vnc,
websockify, and the GTK application as one stack. It restarts the complete
stack after any component exits, while an explicit `systemctl --user stop`
still leaves it stopped. The validated defaults deliberately avoid port `6086`,
which another virtual desktop already owns on this workstation.

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
The launchers do not run or take focus until opened by the user. Current
defaults prefer
`glassagent-ubuntu`, `OptiPlex-7090.local`, and `192.168.1.100`, with the prior
server addresses retained as fallbacks.

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

### Connection

The Connection view is the desktop mirror of Astrill's native connection page.
It reads and controls:

- the selected endpoint and its favorite state;
- OpenVPN or RouterPro over UDP or TCP when supported by that endpoint;
- the endpoint-specific port or automatic port range;
- OpenVPN cipher and UDP MTU;
- acceleration, disconnect blocking, favorite cycling, and router-boot
  connection.

The endpoint parser intersects protocol and port capabilities across the
server's applet records, matching the choices the native page can use. Save is
available only while disconnected. Connect applies a clean saved selection;
changed values require a confirmation and use `Apply & Connect` or
`Apply & Reconnect`. Every write is committed once and read back before the
GUI reports success. In native-only writable mode, reconnect stops the current
tunnel first and restores the previous settings and connection after a failed
start.

Connection state and native values are read at startup and on explicit refresh,
page demand, or completion of a related action. The desktop does not schedule a
recurring SSH poll. When a remote change is discovered, it updates a clean page
immediately. If local edits are pending, the GUI retains them and shows a
Reload conflict instead of silently overwriting the form.

Ubuntu's Favorite switch remains part of this Connection draft. It synchronizes
the selected endpoint's native favorite state through the same validated Save
or Apply flow and retains the page's existing dirty-edit conflict protection.

### Endpoints

The app reads the installed Astrill applet, groups its servers by configured
country tokens, identifies the current endpoint, and provides a quick
confirmed reconnect using a selected Astrill protocol. The Connection view is
the full endpoint-specific editor.

The applet's encoded address map is parsed into validated IPv4 probe targets.
Ubuntu's **Ping** action measures the currently visible endpoints only when
clicked, using at most 12 desktop threads and a 1.5-second TCP-connect timeout.
Its results remain in memory until the endpoint catalog is reloaded.

The Windows Endpoints view offers **Test PC latency** for the selected, visible,
or all loaded endpoints. It also runs only when clicked and opens bounded TCP
connections from the Windows PC. Results are saved in a validated local
sidecar cache and restored without network activity when the app restarts. The
view marks results older than 24 hours or tied to a changed endpoint target for
manual retesting. Its Sort control offers Astrill's default order, region
order, and numeric fastest-first PC latency order; clearing results removes the
saved cache.

Windows also presents DD-WRT's native Astrill favorites in a dedicated
**Favorite** column. The list is synchronized after endpoint loading, by the
explicit **Sync favorites** action, and from completed-action readbacks. It is
not polled. Add and remove require confirmation and operate independently of
the active connection: they do not need the companion, reconnect the tunnel,
switch endpoints, or trigger a latency test.

Each confirmed change starts with a fresh settings read. The controller parses
that current list, preserves unknown servers and record order, and changes
only the selected server's membership. DD-WRT compares the expected list with
NVRAM before replacing it, commits `astrill_favlist` once, and returns a full
readback for exact verification. A concurrent router-side change therefore
fails closed instead of being overwritten.

Malformed native favorite data is preserved and disables favorite editing.
Pending edits on the Windows Astrill page also disable add/remove; an inbound
favorite summary can refresh without discarding that dirty form.

Neither frontend's latency action sends a command to DD-WRT or switches the
router endpoint. The displayed value is TCP-connect latency and reachability
over the desktop's current path, not bandwidth or VPN throughput. The tests
are never started by page navigation, endpoint loading, filtering, or a
background timer.

Each Ubuntu endpoint row has stable Country, Ping, and Action columns. Country labels are
normalized from the installed applet's endpoint names, independently of the
broader policy-region groups. Clicking Country toggles A-Z/Z-A; clicking Ping
toggles fastest/slowest measured results. Pending, no-reply, and unmeasured
rows remain below successful measurements in either speed direction. The
initial order remains the applet's own order.

### Router

The Router view reports the upstream Astrill connection and companion policy
runtime. It links to the full Connection view and exposes runtime repair,
domain refresh, confirmation-gated policy rollback, and idempotent companion
installation or upgrade. `Restore Astrill Only` removes every companion-owned
runtime, NVRAM, MyPage, firewall, policy-rule, and watchdog object while
preserving Astrill's endpoint, protocol, and connection state. The saved desktop
mode keeps later manual or page-demand reads in native-only mode; reinstallation
remains an explicit action. The companion watchdog referenced here runs locally
on DD-WRT for router runtime recovery; it is not a recurring desktop SSH
monitor.

### Astrill

The Astrill view reads the native applet's settings directly from DD-WRT and
maps include/exclude choices to effective Direct/Astrill defaults and
per-entry routes. It synchronizes website and device lists, Wi-Fi and VLAN
filters, DNS, and advanced filter fields. Endpoint and transport controls live
in the Connection view. Opening or refreshing either view is read-only. Only
changed controls are written, every value is validated, and the complete
result is read back before success is reported. Account and router credentials
are outside the allowed key set.

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

- the SSH host or alias, user, port, identity-file path, and whether OpenSSH
  config supplies those connection options;
- active country and endpoint metadata;
- enabled extension IDs;
- editable source rules;
- application profile metadata and allocated lease addresses;
- the enabled/disabled companion state and route recommendation results;
- the read-only/read-write router access guard.

It contains no Astrill account credential, router password, or SSH private-key
contents.

A fresh configuration is native-only, read-only, and has no seeded policy.
Legacy files that predate the access guard retain their existing writable
companion behavior. See [native-only operation](NATIVE_ONLY.md).
