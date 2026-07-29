# Desktop Application

## Installation

The source deployment is installed without root:

```bash
./scripts/install-desktop.sh
```

This creates a system-site-enabled virtual environment at `.venv`, installs the
editable package, adds `astrill-lazy` and `astrill-lazy-gui` under
`~/.local/bin`, and registers the desktop entry under
`~/.local/share/applications`. It also enables a user-level login entry under
`~/.config/autostart`; no root password is needed.

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

At startup and every 60 seconds, the GUI checks the DD-WRT companion. A missing
or outdated companion is installed, while a current companion with a stopped
watchdog is repaired in place. A healthy runtime is only read, not rewritten.
The package fingerprint prevents an identical broken package from being
automatically written on every monitor cycle; the Install/Upgrade action is the
explicit recovery override.

## Isolated noVNC Debugging

Run visual tests without opening a window in the active Ubuntu session:

```bash
./scripts/run-novnc-debug.sh
```

The default local URL is:

```text
http://127.0.0.1:6086/vnc.html?autoconnect=1&resize=scale
```

The script uses X display `:44`, VNC port `5926`, and web port `6086`. Override
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

The Mac desktop application `Astrill Lazy Router.app` creates a passwordless
SSH local forward from `127.0.0.1:16086` to this loopback-only web port and
then opens the controller. It never exposes noVNC to the LAN. Its auditable
source is
[`contrib/macos/open-astrill-lazy.applescript`](../contrib/macos/open-astrill-lazy.applescript).
The application does not launch or take focus until it is opened by the user.

## Views

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
domain and filtered by category and profile type. It includes Chinese and
global vendors, AI tools, development services, media, messaging, work, and
common daily services.

### Countries

Country is a preference attached to each VPN policy. The Countries view
aggregates enabled policies by preference, shows the number of matching Astrill
endpoints, identifies the active country, and links directly to its endpoints.
It warns when policies request several specific countries or when the connected
endpoint does not satisfy the one requested country.

### Devices

The companion merges current DHCP leases, configured static reservations, and
active ARP neighbors on the DD-WRT LAN bridge. Duplicate MAC addresses collapse
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

## Application Profiles

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

`~/.config/astrill-lazy/config.json` is mode `0600` and uses schema version 1.
It contains:

- the SSH host alias;
- active country and endpoint metadata;
- enabled extension IDs;
- editable source rules;
- application profile metadata and allocated lease addresses;
- the enabled/disabled companion state and route recommendation results.

It contains no Astrill account credential or SSH private key.
