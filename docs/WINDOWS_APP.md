# Native Windows Application

Astrill Lazy Router includes a native PySide6/Qt frontend for Windows. It runs
directly on Windows, uses the Windows OpenSSH client to reach DD-WRT, and does
not require WSL, GTK, VNC, or noVNC.

The Windows frontend controls the same native Astrill applet and optional
DD-WRT companion as the Ubuntu frontend. Routing is still enforced on DD-WRT;
the Windows application is a controller and does not install a local VPN,
proxy, packet filter, or network driver.

## Supported Features

The native Windows application provides:

- native-only status, Astrill settings, endpoint, and LAN-client inspection;
- local Direct/Astrill policies for services, domains, IPv4 networks, and LAN
  devices;
- the 261-profile service catalog and Suggested, Direct, or Astrill batch
  policy actions;
- country-preference and shared-endpoint summaries;
- manual, Windows-PC-side TCP latency checks for a selected, visible, or all
  loaded endpoints;
- event-driven native favorite synchronization with a Favorite column and
  separately confirmed add/remove controls;
- confirmed Astrill connect and disconnect controls, plus a companion-backed
  action that selects an endpoint and reconnects the router's shared tunnel;
- installation, repair, refresh, rollback, and complete removal of the
  optional DD-WRT companion;
- a local read-only guard that is enabled for every fresh configuration.

The app has eight views: Policies, Services, Countries, Devices, Endpoints,
Astrill, Router, and Settings. Local policy edits are saved immediately, but
they do not affect traffic until the companion is installed and `Apply
policies` succeeds.

## Requirements

Running the packaged application requires:

- Windows 10 or Windows 11;
- the Windows OpenSSH Client (`ssh.exe`) on `PATH`;
- a DD-WRT router with a working Astrill applet;
- verified key-only root SSH access to the router, or temporary LAN Telnet
  access for guided first-time key authorization.

Check OpenSSH from PowerShell:

```powershell
Get-Command ssh.exe
ssh.exe -V
```

OpenSSH Client can be added through **Settings > Optional features** if it is
not already installed.

Building from source additionally requires Python 3.11 or newer and network
access for the first dependency installation. The packaged application
contains its Python and Qt runtime; end users do not need a separate Python
installation after it has been built.

## Build From Source

Clone or update the repository, then run the native build script from its root:

```powershell
git clone https://github.com/lachlanchen/astrill-lazy-router.git
Set-Location .\astrill-lazy-router

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\contrib\windows\build-native.ps1
```

The script:

1. creates `build\windows-venv`;
2. installs the project with its `windows` dependencies;
3. builds a windowed PyInstaller bundle;
4. verifies that the catalog, router package, and schema data were included.

The result is the complete folder:

```text
dist\windows\Astrill Lazy Router\
```

Do not copy only `Astrill Lazy Router.exe`; its adjacent `_internal` runtime is
required. The build script does not launch the result.

Use `-Python` to select a specific interpreter:

```powershell
.\contrib\windows\build-native.ps1 `
  -Python "C:\Path\To\Python311\python.exe"
```

`-VirtualEnvironment`, `-DistPath`, and `-WorkPath` can relocate build output.
`-SkipDependencyInstall` is intended only for an already-prepared build
environment.

The current source build is a PyInstaller folder rather than an MSI or MSIX,
and the build script does not code-sign it. Windows reputation warnings are
therefore possible on a newly built binary.

## Install Or Update

Install the built bundle for the current Windows user:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\contrib\windows\install-native.ps1
```

The installer:

- copies the complete bundle to
  `%LOCALAPPDATA%\Programs\Astrill Lazy Router`;
- stages an update before replacing the installed copy;
- refuses to update while the installed application is running;
- creates one Desktop shortcut and one current-user Startup shortcut;
- removes an older same-named Start Menu shortcut;
- does not create a Start Menu entry, request administrator access, or launch
  the application during installation.

The Startup shortcut is recreated idempotently on every install or update. It
launches the installed app after the current user signs in, including after a
Windows reboot; it does not run before sign-in or as a Windows service. Its
location is:

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Astrill Lazy Router.lnk
```

To install a bundle built in another directory:

```powershell
.\contrib\windows\install-native.ps1 `
  -PackagePath "D:\Builds\Astrill Lazy Router"
```

Close Astrill Lazy Router before updating, rebuild the bundle, and rerun the
installer. Launch it from the Desktop shortcut or directly:

```powershell
& "$env:LOCALAPPDATA\Programs\Astrill Lazy Router\Astrill Lazy Router.exe"
```

## Set Up The Dedicated SSH Key

The application generates or reuses a dedicated local Ed25519 key through
Windows OpenSSH. The private key remains under the configured local path; only
its public half is sent to DD-WRT. Background router commands use
`BatchMode=yes`, `IdentitiesOnly=yes`, `StrictHostKeyChecking=yes`, and the
application-pinned `known_hosts` file. Password prompts and unverified host
keys therefore fail closed.

The fresh Settings values are host `192.168.1.1`, user `root`, port `22`, and
private-key path `~/.ssh/astrill_lazy_router_ed25519`. For guided setup:

1. Keep the PC and router on a trusted local network.
2. Enter the explicit host, user, port, and identity path in **Settings**.
3. Select **Set up key via Telnet**.
4. Compare the displayed SSH algorithm and SHA-256 fingerprint with a trusted
   router record, Web UI, label, or local console. Cancel if it cannot be
   verified.
5. Confirm the Telnet warning and enter the router's root Telnet password.
6. Wait for both strict key-only SSH checks to succeed.

Telnet sends credentials without encryption. The guided flow therefore uses
it once on the LAN and only after fingerprint confirmation. The password is
held transiently in application memory and is never saved in configuration,
logs, command arguments, or environment variables. The setup script verifies
router UID 0, preserves existing authorized keys, appends the public key
idempotently, and restarts SSH. It disables SSH password authentication and
WAN SSH only after Windows OpenSSH proves that the dedicated key works, then
verifies key-only access again. Telnet itself remains available as the
documented recovery path.

An SSH alias can instead be used as the host field. Select **Use OpenSSH config
for user, port, and private key** when the alias should supply those options:

```sshconfig
Host astrill-router
    HostName 192.168.1.1
    User root
    IdentityFile ~/.ssh/id_ed25519_astrill_router
    IdentitiesOnly yes
```

Guided onboarding requires explicit fields rather than an alias. The
interactive terminal remains available as an advanced fallback and explicitly
uses `StrictHostKeyChecking=ask`. The application never supplies
`StrictHostKeyChecking=no` and never auto-accepts a new key. If OpenSSH reports
that a host key changed, first
verify that the router was intentionally replaced, reset, or rekeyed. Only
after that independent verification should the affected entry named in the
error be removed:

For example, when the error names `192.168.1.1`:

```powershell
ssh-keygen.exe -R "192.168.1.1"
```

Substitute the exact host, IP, or `[host]:port` named by OpenSSH. Do not delete
the complete `known_hosts` file to bypass a mismatch.

The Settings host accepts only an SSH alias, hostname, IP address, or
`user@host` without spaces or command-line options. User, port, and private
key also have dedicated validated fields. Relative private-key paths are
rejected; use an absolute path or one beginning with `~`.

## Safe First Use

A fresh Windows configuration is:

- native-only: the optional companion is not assumed to exist;
- read-only: router-changing operations are blocked;
- empty: no policy is seeded or applied automatically.

The application performs one status and reconciliation check when its window
starts. It does not run a recurring timer or poll DD-WRT over SSH in the
background. After that startup check, router status is read only when the
operator selects **Refresh router**, a page first needs router data, or a
completed action returns status or verified readback.

Successful page reads are cached for the life of the window, including a valid
empty device result. Moving between pages therefore does not turn an empty
inventory into repeated router requests. Use the page's explicit Load or
Refresh action when fresh data is required.

This removes desktop SSH polling only. An installed companion still runs its
15-second watchdog and five-minute DNS refresh locally on DD-WRT; those
router-local maintenance cycles do not open desktop SSH sessions.

When the local configuration records a previously confirmed companion,
refresh also reconciles router-reboot state:

- a current, healthy runtime is reused as-is;
- if the validated current package remains in NVRAM but `/tmp` was cleared,
  the runtime is reconstructed from that stored package without rewriting
  NVRAM;
- if neither persistent markers nor a runtime remain, the desktop falls back
  atomically to native-only mode and keeps **Install / upgrade** available;
- SSH or router unavailability leaves companion mode unchanged. If the Windows
  Startup shortcut ran before DD-WRT finished booting, wait until the router is
  reachable and select **Refresh router** to retry reconciliation.

An inconsistent runtime or a package requiring a version rewrite fails closed.
The app never silently installs or rewrites a missing or incompatible
companion; that still requires the separate **Install / upgrade**
confirmation.

Recommended first-use sequence:

1. Set up and test key-only SSH in **Settings**.
2. Keep the read-only guard enabled while reviewing **Router**, **Astrill**,
   **Devices**, and **Endpoints**.
3. Add or edit local policies if desired. This changes only the current user's
   configuration.
4. Review the router prerequisites and recovery procedure in
   [Router Installation And Rollback](ROUTER_INSTALL.md).
5. Select **Install / upgrade** in **Router** and confirm the exact DD-WRT
   writes. On a fresh profile that confirmation also disables the local guard;
   a failed installation restores it automatically.
6. In **Endpoints**, load the server list, select a server and protocol, then
   add or remove its router favorite as needed. **Auto reconnect to next
   favorite server** and **Start automatically after router boot** mirror the
   corresponding native Astrill settings. Select **Connect router to selected
   endpoint** only when changing the active tunnel; its separate confirmation
   writes the endpoint to DD-WRT and briefly reconnects the shared router
   tunnel. It does not connect a VPN or change routing on the Windows PC.
7. Apply policies only through the separate confirmation when intended.

Guarded remote write operations include native Astrill setting changes,
connection changes, companion installation, policy application, endpoint
switching, domain refresh, rollback, and companion removal. Destructive or
traffic-interrupting actions add confirmation dialogs, and the controller
checks the read-only guard again even when a button remains visible.

The endpoint list remains available for read-only inspection. Switching is
enabled only while the app is idle, the read-only guard is off, the companion
is enabled, and a server is selected. The companion transaction restores the
previous router endpoint settings when the requested endpoint does not connect.
All Astrill-targeted devices and policies still share that one router tunnel.

Favorites do not require the companion. Each endpoint row displays the
router's native favorite state; **Add selected favorite** and **Remove selected
favorite** merge against a fresh `astrill_favlist` read, commit once, and
render the verified readback. **Sync from router** and page-demand reads bring
favorites plus the two connection-behavior checkboxes back from Astrill's
router page. Favorite and behavior writes refuse to replace pending edits in
the full **Astrill** view; synchronization preserves the draft while updating
the read-only favorite summary and endpoint controls.

**Test PC latency** is a separate, manual inspection action. It can test the
selected endpoint, endpoints visible under the current search, or all loaded
endpoints. The test opens one bounded TCP connection per target from the
Windows PC over its current network path, records TCP-connect latency and
reachability, and immediately closes the connection. It never starts
automatically, sends no command to DD-WRT, changes no NVRAM value, and does not
switch or reconnect the router endpoint. The result is not a bandwidth,
download-speed, or VPN-throughput measurement. For a selected UDP protocol,
the app tests the same endpoint family's TCP counterpart when available.

Manual results are saved separately from router and policy configuration at:

```text
%LOCALAPPDATA%\Astrill Lazy Router\endpoint-latency.json
```

They survive application restarts and endpoint-catalog reloads until
**Clear results** is selected or a new manual test replaces them. Loading a
saved result never opens a connection. Results older than 24 hours are marked
for retesting, and a saved result is not used for latency ordering when the
current applet advertises a different address, port, or TCP counterpart. The
cache is bounded, validated, and atomically replaced; damage to this derived
file cannot prevent the application from starting.

Use the endpoint **Sort** control to choose **Default order**, **Region
(A–Z)**, or **PC latency (fastest)**. Latency sorting uses the numeric
measurement rather than its displayed text, places current reachable results
first, and leaves untested endpoints last. Search is applied before sorting,
and the selected endpoint is retained when it is temporarily hidden by a
filter.

### Router favorites

The endpoint table's **Favorite** column mirrors native Astrill favorite
membership from DD-WRT. Loading or reloading the endpoint catalog schedules
one favorite read after the catalog is available. **Sync from router** performs
the same read explicitly, and a completed favorite or native-settings action
applies its verified readback. These are event-driven reads; the Windows app
does not schedule a recurring SSH poll.

Select a row and use **Add selected favorite** or **Remove selected favorite**.
Adding uses the currently selected supported protocol and its default endpoint
port. Removing is matched by server ID and does not depend on the currently
selected protocol. Both operations show a cancel-default confirmation that
states the exact endpoint and effect.

After confirmation, the controller reads the complete current native settings
again instead of trusting the displayed copy. It parses the fresh
`astrill_favlist`, preserves every other record and its order, and prepares
only the requested membership change. The router then compares that fresh
value with the current NVRAM value before replacement. If another client
changed it in the meantime, the write stops and the app asks for another sync.
On success, only `astrill_favlist` is set, `nvram commit` runs once, and the
complete allowlisted settings are read back to verify the saved value.

Favorite membership is a native Astrill setting and does not require the
router companion. Adding or removing a favorite does not switch the active
endpoint, reconnect the tunnel, run a latency test, change PC routing, or
start background monitoring.

The app will not rewrite malformed favorite data. It labels the Favorite
column invalid, preserves the router value, and disables add/remove until a
valid value is available. It also disables favorite changes while the Astrill
page has unsaved edits. A sync may update the read-only favorite summary
without replacing those pending controls, but the draft must be saved or
reloaded before changing membership.

The guard is an accident-prevention feature, not an authorization boundary.
A user who can edit the configuration or invoke `ssh.exe` directly can still
change the router.

## Human-Readable Astrill Settings

The **Astrill** view uses the same effective native controls as the Ubuntu
frontend instead of presenting an editable raw NVRAM table. Settings are
grouped into current endpoint state, website routing, device routing, router
interfaces, DNS, connection behavior, and advanced website filters. Boolean
values use checkboxes, validated modes use named choices, MTU uses a bounded
number control, and lists use appropriately sized text fields.

Website, device, Wi-Fi, and VLAN include/exclude modes are shown as effective
**Direct** or **Astrill** defaults plus route exceptions. Editing the website
list regenerates its compiled IPv4 networks; observed DD-WRT clients are
merged with Astrill's stored native device records. Automatic native website
modes are preserved unless the related controls are actually changed.

For transparency, every row shows its underlying `astrill_*` NVRAM key in
small text. Endpoint, node, protocol, encoded address, port, VPN mode,
connection state, the favorite summary, and the generated IPv4 summary remain
read-only on this page. Favorite membership is edited only through the
confirmed Endpoints controls described above. The page covers exactly the
explicit safe-key allowlist; it never requests or displays Astrill account
credentials, router passwords, tokens, installer URLs, or generated VPN
credentials.

Controls stay disabled until a complete read succeeds. The read-only guard and
background-task lock disable every writable control as well as Save. Save is
enabled only when the presented value differs from the last readback. A
Cancel-default confirmation lists the changed NVRAM keys without echoing their
values; the controller validates those values, performs one NVRAM commit, and
reads every changed key back before the page is marked synchronized.

## Background Terminal Behavior

Normal router refreshes, endpoint loading, host-key inspection, fingerprint
checks, and local identity generation run Windows OpenSSH tools without
creating a console window. This prevents `ssh.exe`, `ssh-keyscan.exe`, and
`ssh-keygen.exe` from flashing a terminal during background app work.

**Open interactive SSH setup** is intentionally different: it opens a visible
terminal because that action hands an interactive SSH session to the operator.
The router companion itself does not launch a terminal on Windows.

## Windows Limitations

There is no Windows per-application WFP routing backend in this release.
Ubuntu application policies depend on a Linux macvlan network namespace,
Polkit, and a distinct DD-WRT DHCP identity. The Windows frontend therefore:

- cannot create a Windows process/application policy;
- cannot launch an application inside an isolated routing identity;
- cannot prepare or clean up an Ubuntu application namespace.

An existing Ubuntu process rule can be displayed as a policy record, but its
network identity still has to be launched and managed from Ubuntu. A genuine
Windows equivalent would require a separately designed, privileged, and
signed Windows Filtering Platform service or driver; none is installed by this
project.

Other intentional boundaries:

- one router Astrill tunnel means one active VPN endpoint for all VPN policies;
- routing enforcement occurs on DD-WRT, not in the Windows network stack;
- the policy engine is currently IPv4;
- Windows login startup is installer-managed rather than exposed as an in-app
  switch; the Windows UI does not expose Polkit helpers, extension management,
  route detection, or namespace lifecycle controls;
- the native app does not use or expose noVNC.

## Configuration And Uninstall

The Windows configuration is stored at:

```text
%LOCALAPPDATA%\Astrill Lazy Router\config.json
```

`XDG_CONFIG_HOME`, when explicitly set, takes precedence. The configuration
contains the SSH target, local policies, enabled catalog IDs, companion mode,
and read-only state. The confirmed SSH host key is pinned beside it as
`known_hosts`. The dedicated private key remains at its separately configured
path. Neither file contains the router Telnet password or Astrill account
credentials.

Uninstall the native application with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\contrib\windows\uninstall-native.ps1
```

Close the application first. `-Force` terminates only a running executable
from the expected per-user installation directory. Uninstall removes the
application folder and matching Desktop, Startup, and legacy Start Menu
shortcuts but deliberately preserves unrelated shortcuts, user configuration,
and SSH keys.

## Native App Versus The noVNC Launcher

`install-native.ps1` installs the Qt application documented here. The older
`install-launcher.ps1` installs a browser shortcut that opens an Ubuntu noVNC
session through an SSH tunnel to another workstation. It is not the native
Windows application and is unnecessary for normal Windows use.

The two installers use the same display name, so do not install both on the
same Windows account unless that shortcut replacement is intentional.
