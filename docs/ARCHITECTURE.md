# Architecture

## Components

Astrill Lazy Router has three deliberately small trust domains:

1. The native Ubuntu and Windows applications own editable rules, catalogs,
   Astrill server discovery, and platform-specific application profiles.
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

The global persistent core is evaluated by ascending priority first. Each
owner overlay is then reached only through its source-address and optional MAC
guard and evaluates its own rules by ascending priority. The first matching
rule sets one of these marks and returns:

| Target | Mark | Mask | Preference | Table |
| --- | --- | --- | --- | --- |
| Direct | `0x4000000` | `0xc000000` | Runtime-owned first slot | `213` |
| Astrill | `0x8000000` | `0xc000000` | Runtime-owned second slot | `212` |

Astrill uses mask `0x3000000` and tables `110` through `114`. Its current
applet does not assign a stable numeric preference: Linux can place a newly
added rule immediately before the first existing non-local rule. A fixed
companion range is therefore unsafe because Astrill can undercut it whenever
the tunnel reconnects.

The managed connect lifecycle removes the companion's owned pair before
Astrill starts, waits for the native rules to settle, selects two free adjacent
preferences immediately ahead of the current native minimum, installs the
Direct rule followed by the fail-closed Astrill rule, and verifies the complete
RPDB order. If an unmanaged Astrill reconnect creates native rules ahead of the
recorded companion pair, the watchdog enables fail-closed protection but does
not install another lower pair. Status remains degraded and rebase-required
until the tunnel is observed down or an explicit managed reconnect starts
Astrill with the companion lookups absent. This prevents preference ratcheting.
If no safe pair exists or precedence cannot be verified, the controller
likewise refuses to claim that policy routing is healthy.

An explicit companion match can therefore overlay the native result without
rewriting Astrill's own list. Deleting or disabling that companion rule reveals
the unchanged native result again. Cleanup uses recorded preferences when they
exist. If the record is missing, it scans only exact companion mark, mask, and
table signatures; unrelated policy rules are preserved. This reversible
ownership model is the basis of the incremental extension.

Table `213` contains the DD-WRT WAN default. While Astrill is connected, table
`212` contains the preferred `tun0` default plus a lower-priority blackhole
fallback. If the tunnel route disappears, lookup terminates at that fallback
instead of continuing to the WAN. While disconnected, the blackhole is the
table's only default.

## Transactional Layer Updates

The companion validates and deterministically composes the entire candidate
effective document before touching the active chain. It then:

1. selects the inactive A/B chain;
2. extracts enabled unique domains and prefetches them with no more than eight
   resolver workers and a five-second deadline per lookup, retaining prior
   validated addresses when a fresh lookup fails;
3. counts generated matches and checks memory and elapsed duration while
   creating a deterministic plan;
4. emits one restore document that declares and replaces only the inactive user
   chain, without changing `PREROUTING`;
5. verifies the active/inactive reference topology, memory, and deadline,
   dry-runs that document with `iptables-restore --noflush --test`, rechecks the
   guards, and commits the same document with `--noflush`;
6. reads back the exact rule count and chain-reference topology; and
7. inserts the completed chain at `PREROUTING` position one, removing the old
   jump only after the replacement is ready.

An invalid document, resolver/build deadline, exceeded admission limit,
topology change, restore failure, or readback mismatch discards the candidate
and leaves the previous jump active.

For a core change, the desktop supplies the observed generation. After chain
activation the companion persists the current core and, when the 2 KiB reserve
permits, its previous generation to NVRAM using gzip/base64 when smaller than
plain TSV. It commits, decodes, and byte-verifies the record before advancing
the generation. A persistence failure restores the exact old NVRAM values and
active chain in the same operation.

For an overlay change, the desktop supplies the owner and expected generation.
The companion atomically replaces only that owner's file under `/tmp`; it
never writes or commits NVRAM. Core and overlay origins cannot collide.

The desktop owns a complete local editable library while the router reports
the persistent core, this computer's overlay, other overlays, and the composed
effective policy separately. Exact origin IDs, MD5 hashes, generations,
runtime epoch, and source/MAC bindings distinguish deliberate deployment
scope from drift. See [Hybrid policy storage](HYBRID_POLICY_STORAGE.md).

## Runtime Recovery

`alctl watchdog-loop` runs locally on DD-WRT every 60 seconds. It repairs
missing policy objects, tables, chains, and the `PREROUTING` hook. It does not
chase an unmanaged native undercut to a lower preference; that condition stays
fail-closed and rebase-required. When no owner overlay is active, every 30
cycles it can re-resolve the core's domain rules through DD-WRT's local DNS
service and perform a core-only A/B refresh. When an overlay is active, the
watchdog deliberately skips that periodic policy rebuild: an overlay snapshot
is loaded only by an explicit load, one-shot startup/network restoration, or a
manual restore/reload. These router-local checks are not desktop SSH polling.
Status is degraded whenever this watchdog is absent, the active jump is
missing, or the runtime contract for the current tunnel state cannot be
verified. A disconnected state is ready when the Direct table, VPN blackhole
table, and owned VPN-mark forwarding fail-close guard are verified; native and
owned RPDB preferences are intentionally absent in that state.

Connection state and policy-overlay health are reported independently. A
tunnel can be connected while policy precedence is degraded; that partial
success must not be rendered as a healthy bypass. The status contract exposes:

| Field | Meaning |
| --- | --- |
| `policy_health` | `ready` only when the policy runtime is safe for the current tunnel state; otherwise `degraded` |
| `precedence_ok` | The owned RPDB pair is verified ahead of the current native minimum |
| `native_min_pref` | Current minimum native Astrill preference, or null while native rules are absent |
| `direct_pref` / `vpn_pref` | Recorded owned preferences, or null while intentionally absent |
| `table_readiness` | Independent readiness for Direct, VPN/fail-closed, and native tables |
| `vpn_fail_closed` | VPN-marked forwarding is blocked when the tunnel is unavailable |
| `last_reconcile_error` | Exact current reconciliation failure, or null |

A disconnected ready state intentionally has no owned RPDB preferences and no
native table. Its Direct table, VPN blackhole table, and VPN-mark forwarding
fail-close remain ready.

The package lives on tmpfs at `/tmp/astrill-lazy`. A deterministic gzip archive
is base64 encoded into bounded NVRAM chunks. Separately, the installer
normalizes the 6,502-byte bootstrap script, creates deterministic gzip
(`mtime=0`), and stores its 2,560-byte base64 payload in one NVRAM value. Its
MD5 covers that encoded payload plus exactly one canonical trailing newline.

`rc_startup` captures the encoded payload and digest once, hashes those
canonical bytes, decodes that same captured payload through `uudecode` and
gzip, rejects an empty result, and executes the decoded script. The running
bootstrap rechecks the stored encoded-payload identity before and under the
shared controller lock, reconstructs and MD5-verifies the package archive,
extracts into a private staging directory, publishes each runtime file by
atomic rename, and writes the running `PACKAGE_MD5` marker last.
The host installer also records SHA-256 for release verification.

The desktop reconciles the companion once at launch. Later reconciliation is
manual; status and data otherwise come from explicit page loads or the result
of an action the operator requested. A matching version, stored-package MD5,
stored-bootstrap-payload MD5, verified stored chunk/bootstrap integrity, and
exact persistent hooks with the corresponding running package marker, jump,
and watchdog are left untouched. A stopped current runtime may be started in
place, and that exact current package can be reconstructed from its verified
stored bootstrap payload without a rewrite. If login startup
precedes router startup, the failed read does not change the saved mode and
manual Refresh retries after DD-WRT is reachable. A missing, outdated,
package-mismatched, fingerprint-mismatched, or non-repairable package only
opens the Install/Upgrade confirmation. No background desktop monitor can
invoke the NVRAM installer. This lifecycle is separate from Astrill connection
management and does not change `astrill_autostart`.

The applet endpoint payload is a separate, larger read. Startup queues it only
after the health snapshot completes instead of opening both SSH sessions
concurrently.

The same payload contains an encoded-address lookup table. The desktop parser
associates those validated IPv4 addresses with each endpoint and selects a
fixed TCP port, preferring 443, for a manually requested latency probe. Probes
run on the desktop with bounded concurrency and are never part of router
reconciliation. They therefore report current desktop-path connection latency
without changing router marks, tables, routes, or Astrill state.

The Ubuntu endpoint browser also derives a display country from Astrill's own server
name, normalizing its USA, UK, Korea, Czechia, bracketed China, and city-only
aliases. Country and latency ordering operate only on the loaded in-memory
catalog. Unsuccessful and incomplete measurements are ranked after successful
latencies, including when measured values are sorted slowest first.

## Native Connection Mirror

The desktop reads an explicit NVRAM allowlist and the installed applet's server
catalog. A connection selection is compiled to Astrill's server ID, node ID,
encoded address, protocol, port, port index, and VPN mode fields. Supported
protocols and port indexes are intersected across the selected server's node
records so the GUI cannot construct a combination absent from the applet.

Cipher, MTU, acceleration, disconnect blocking, favorite records, favorite
cycling, and startup remain native Astrill values. They are validated
individually, committed once, and read back exactly. The companion's existing
transactional endpoint switch performs connected changes. In writable
native-only mode, the desktop stops an active tunnel, writes the complete
selection, starts it again, and restores both the prior values and active
session if startup fails.

`astrill_favlist` is the sole favorite source for the router page, Ubuntu, and
Windows. A desktop favorite edit first reads the current native list, merges
one server record derived from the selected applet endpoint, validates the
complete list, commits once, and renders the readback. `astrill_autocycle` and
`astrill_autostart` remain independent one-key native writes. Dirty connection
or native-settings forms block these endpoint shortcuts, preventing a
page-level edit from silently replacing another local draft.

Startup, explicit refreshes, and completed-action readbacks obtain status and
settings in both companion and native-only modes. There is no recurring
desktop SSH monitor. Clean controls follow router-side applet changes. Unsaved
desktop edits retain their baseline and expose a reload conflict when the
router changes concurrently.

Windows endpoint favorites use a narrower native-setting transaction. Endpoint
loading, explicit synchronization, or a completed action may refresh the
Favorite column, but no timer performs that read. A confirmed add or remove
starts from a fresh allowlisted settings snapshot, preserves all unrelated
favorite records and their order, and calculates a replacement for
`astrill_favlist` only. The DD-WRT script compares the expected snapshot with
the current NVRAM value before setting it, commits once, and the controller
then reads back and verifies the complete setting. Concurrent or malformed
values fail closed. The transaction is independent of companion installation
and tunnel switching, and dirty Astrill-page drafts block favorite mutation.
Ubuntu retains its existing favorite control inside the synchronized
Connection draft.

`Restore Astrill Only` records native tunnel state, removes all
companion-owned runtime and persistent objects, audits that cleanup, and then
checks that endpoint, protocol, and tunnel state are unchanged. The desktop
persists native-only mode only after this audit succeeds. Runtime stop and the
exact-byte NVRAM removal transaction use the same controller lock as policy
writes; snapshot or residue drift is refused, and a failed locked mutation
attempts same-session restoration of the captured NVRAM state.

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
  changing CDN hostname, dynamic ICE relay, or peer-to-peer destination used by
  a company. UU Remote is one such case. Prefer a narrowly scoped source-device
  Direct rule when all traffic from that device may bypass, or a process-aware
  device-local backend when only one application may bypass; do not substitute
  broad hosting-provider CIDRs.
- Local and non-routable destinations still return before policy matching, so
  those broader source-device fallbacks do not reroute RFC 1918 LAN traffic.
- NAT and connection tracking bind an existing application flow to its prior
  route. After applying a policy change, reconnect the affected application
  rather than flushing connection tracking for every LAN client.
- Application identities require an Ethernet parent that supports macvlan.
  Other transports can be added as launcher providers.
