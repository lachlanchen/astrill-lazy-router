# Architecture

## Components

Astrill Lazy Router has three deliberately small trust domains:

1. The native Ubuntu application owns editable rules, catalogs, Astrill server
   discovery, and application launch profiles.
2. The DD-WRT controller owns validated compiled rules, address resolution,
   packet marking, policy tables, rollback, and runtime recovery.
3. Astrill continues to own its tunnel, DNS behavior, low-order marks, server
   configuration, and OpenVPN process.

The device-local policy model is a fourth, currently non-enforcing trust
domain. It validates Direct and multi-tunnel decisions without touching the
router or host routes. Platform-specific privileged backends remain separate
and must consume the same versioned policy only after opening their own
provider sessions. See [Device-local routing](DEVICE_ROUTING.md).

The desktop sends a versioned, tab-separated compiled document over key-only
SSH. The router never parses desktop JSON and never executes catalog code.

The companion is optional. In native-only mode the desktop uses allowlisted,
read-only SSH requests for status, native settings, endpoint discovery, DHCP
leases, static reservations, and LAN ARP neighbors. Those requests occur at
the startup check, through an explicit refresh, or when a page first needs its
data; the desktop has no recurring SSH poll. Successful empty inventories are
cached rather than mistaken for data that has not loaded. Native-only reads
create no remote runtime file and perform no reconciliation. Fresh
configurations start in this mode behind a local read-only guard.

## Packet Path

The router inserts one jump at the beginning of mangle `PREROUTING`. The active
chain is either `AL_LAZY_A` or `AL_LAZY_B`.

Local and non-routable destinations return before policy matching:

- `10.0.0.0/8`
- `172.16.0.0/12`
- `192.168.0.0/16`
- `224.0.0.0/4`
- `255.255.255.255/32`

Rules are evaluated by ascending priority. The first matching rule sets one of
these marks and returns:

| Target | Mark | Mask | Preference | Table |
| --- | --- | --- | --- | --- |
| Direct | `0x4000000` | `0xc000000` | `29000` | `213` |
| Astrill | `0x8000000` | `0xc000000` | `29001` | `212` |

Astrill uses mask `0x3000000`, tables `110` through `114`, and observed
preferences `29998` through `30001`. The companion uses separate high bits and
the earlier preferences `29000` and `29001`. An explicit companion match
therefore overlays the native result without rewriting Astrill's own list.
Deleting or disabling that companion rule reveals the unchanged native result
again. This reversible overlay is the basis of the incremental extension
model.

The controller refuses to apply if a future Astrill build occupies an equal or
earlier preference for tables `110` through `114`. Upgrades remove only the
companion's exact legacy `32000` and `32001` rules; an unrelated rule at either
preference is left untouched.

Table `213` contains the DD-WRT WAN default. Table `212` contains the `tun0`
default while Astrill is connected. When `tun0` is unavailable, table `212`
contains `blackhole default`, so a VPN-targeted rule cannot silently leak to
WAN.

## Transactional Apply

The controller validates the entire document before touching the active chain.
It then:

1. selects the inactive A/B chain;
2. flushes and completely builds that chain;
3. resolves domain rules, retaining prior addresses when a refresh fails;
4. verifies both policy tables and rules;
5. inserts the completed chain at `PREROUTING` position one;
6. removes the old jump;
7. persists current and previous documents to NVRAM.

An invalid document or failed chain build leaves the previous jump active.
Rollback applies the same transaction in the opposite direction.

## Runtime Recovery

`alctl watchdog-loop` runs locally on DD-WRT every 15 seconds. It repairs
missing policy rules, tables, chains, and the `PREROUTING` hook. Every 20
cycles it re-resolves domain rules through DD-WRT's local DNS service and
performs an A/B refresh. This router-local watchdog and five-minute DNS cycle
are not desktop SSH polling.
Status is degraded whenever this watchdog is absent, the active jump is
missing, or a VPN policy is enabled while the tunnel is down.

The package lives on tmpfs at `/tmp/astrill-lazy`. A gzip archive is base64
encoded into bounded NVRAM chunks. `rc_startup` reconstructs the archive,
verifies its MD5 value using tools available in this firmware, extracts it, and
starts the controller. The host installer also records SHA-256 for release
verification.

The desktop reconciles the companion once at launch. Later reconciliation is
manual; status and data otherwise come from explicit page loads or the result
of an action the operator requested. A matching version with its jump and
watchdog present is left untouched; a stopped current runtime may be started
in place; and a current package can be reconstructed from its stored bootstrap
without a rewrite. If login startup precedes router startup, the failed read
does not change the saved mode and manual Refresh retries after DD-WRT is
reachable. A missing, outdated, fingerprint-mismatched, or non-repairable
package only opens the Install/Upgrade confirmation. No background desktop
monitor can invoke the NVRAM installer. This lifecycle is separate from
Astrill connection management and does not change `astrill_autostart`.

`Restore Astrill Only` records native tunnel state, removes all
companion-owned runtime and persistent objects, audits that cleanup, and then
checks that endpoint, protocol, and tunnel state are unchanged. The desktop
persists native-only mode only after this audit succeeds.

## Native Policy Composition

The desktop presents final Direct/Astrill outcomes while preserving the
native applet's underlying modes:

| Native website mode | Native default | New companion policy default |
| --- | --- | --- |
| Global (`0`) | Astrill | Direct exception |
| Include list (`1`) | Direct | Astrill union |
| Exclude list (`2`) | Astrill | Direct union |
| Automatic (`3`/`4`) | Applet-owned | Direct exception |

The selected default extends the native mode, while changing the target
performs the corresponding set subtraction:

- Global + Direct bypasses Astrill for the new match.
- Include + Astrill extends the included set; Include + Direct subtracts the
  match from that set.
- Exclude + Direct extends the excluded set; Exclude + Astrill subtracts the
  match from that set.

This is effective-policy composition, not destructive editing of Astrill's
stored list. Explicit companion matches take precedence; unmatched website,
device, Wi-Fi, and VLAN traffic keeps the native result.
Country is independent: a new Astrill policy starts with
`No country override`, and only an explicit country selection requests an
endpoint region.

## Per-Application Routing

Routers see addresses and packets, not Ubuntu process names. The desktop's
privileged helper therefore creates a macvlan network namespace for an
application profile:

1. a unique macvlan interface is attached to the active Ethernet interface;
2. BusyBox `udhcpc` obtains and renews a separate router lease;
3. the lease becomes a source-device rule in the compiled router policy;
4. the selected executable starts inside that network namespace as the desktop
   user.

The helper is invoked with `pkexec`, validates every profile/interface/path
argument, and drops privileges before launching the application. The profile
inherits its own DNS and default route, plus the desktop's Wayland and session
bus endpoints.

## Constraints

- The current policy engine is IPv4-only because the installed DD-WRT/Astrill
  routing path is IPv4-only.
- One Astrill tunnel means one active VPN country. Policy countries are
  assignments; Astrill endpoints are the concrete server choices.
- Domain rules use periodically resolved A records because this firmware has no
  `ipset` support. Catalog seeds improve coverage but cannot identify every
  changing CDN hostname owned by a company.
- Application identities require an Ethernet parent that supports macvlan.
  Other transports can be added as launcher providers.
