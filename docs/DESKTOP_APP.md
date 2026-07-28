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

## Views

### Policies

The first screen shows controller health, tunnel state, active Astrill
location, and enabled policy count. A configured server is not reported as
active while the tunnel is disconnected. Each rule has:

- an enable switch;
- Direct/Astrill segmented routing;
- a preferred Astrill region;
- an application launch button when applicable;
- a delete action.

Local edits are saved immediately. The orange Apply action compiles and
transactionally installs the full rule set on DD-WRT.

### Services

The 260-profile catalog can be searched by service, company, alias, or seed
domain and filtered by category and profile type. It includes Chinese and
global vendors, AI tools, development services, media, messaging, work, and
common daily services.

### Devices

The app reads current DHCP leases from DD-WRT and can add a direct or Astrill
source policy with one action. Manual fixed addresses are also supported.

### Locations

The app reads the installed Astrill applet, groups its servers by configured
region tokens, identifies the current server, and can switch the shared tunnel.
A confirmation is required because reconnecting pauses VPN-routed traffic.

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
- active location metadata;
- enabled extension IDs;
- editable source rules;
- application profile metadata and allocated lease addresses.

It contains no Astrill account credential or SSH private key.
