# Testing And Operations

## Automated Checks

Run from the repository:

```bash
.venv/bin/pytest
.venv/bin/ruff check desktop tests scripts/validate-catalog.py
.venv/bin/ruff format --check desktop tests scripts/validate-catalog.py
.venv/bin/python scripts/validate-catalog.py --dns
shellcheck -x -s sh \
  scripts/*.sh helpers/astrill-lazy-netns \
  router/alctl router/alapi router/alpage router/bootstrap.sh
desktop-file-validate data/*.desktop
appstreamcli validate --no-net data/*.metainfo.xml
```

Current result:

```text
253 tests passed, 4 skipped
Ruff lint and format: all checks passed
Catalog: 261 profiles, 19 categories, 729 seeds / 650 unique
```

The release pytest result was produced by the full Windows build virtual
environment with `python -m pytest -ra`. The four skips are two Ubuntu-only
provider tests and two router static tests that require a POSIX shell
unavailable in that Windows environment.

Tests cover:

- requested catalog entries, split entrypoints, and extension merge behavior;
- catalog path containment, duplicate-key rejection, profile validation, and
  the client-side compiled payload ceiling;
- rule and port validation;
- service and application compilation;
- single-tunnel region conflict warnings;
- applet parsing and protocol-specific VPN mode selection;
- deterministic router package contents;
- current-runtime reconciliation, stored-package reconstruction, in-place
  watchdog repair, and identical-package rewrite suppression;
- private, atomic user-session autostart;
- private atomic desktop configuration;
- safe fresh defaults, legacy configuration compatibility, and the CLI write
  guard;
- application command parsing;
- POSIX shell parsing and the no-`eval` policy contract;
- DHCP/static/ARP client inventory merging, MAC deduplication, and WAN
  exclusion;
- companion-free read-only LAN inventory and path-independent noVNC service
  rendering;
- SSH banner error cleanup;
- native mode adapters, device/site parsing, safe-key validation, and
  round-trip writes;
- native favorite parsing, ordered atomic batch add/remove behavior,
  malformed-value rejection, whole-selection validation,
  compare-before-write replacement, exact readback, and Windows
  Favorite/Unfavorite guards;
- Windows Services category/profile/provider-country filtering, durable
  checkbox and row multi-selection, tri-state Select visible, and explicit
  Add-to-Policies behavior;
- Windows endpoint exact-country filtering, durable checkbox plus
  Ctrl/Command/Shift selection, semantic header ordering,
  numeric/missing-value behavior, and persistent manual latency-cache
  rendering;
- Windows Connection draft validation, dirty/conflict preservation,
  protocol/port capability changes, favorite synchronization, action gating,
  and native/companion transactional controller paths;
- seven-section Windows Astrill rendering, complete safe-key coverage, and
  section changes that preserve draft/dirty state;
- companion `0.2.5` executable coverage for post-connect allocation,
  no-ratchet undercut handling, persistent table `212` blackhole fallback,
  exact owned-rule cleanup, stale-lock recovery, and degraded-state reporting;
- route probe parsing, noise thresholds, minimum bypasses, and service-aware
  recommendations;
- complete native-only removal and preserved Astrill state.
- device-local policy validation, deterministic precedence, domain matching,
  three-slot limits, Auto path health scoring, hold and hysteresis behavior,
  fallback safety, duplicate-key rejection, and country-prefix collapsing;
- long-running router refresh timeouts and clean timeout errors.

## Live Router Verification

The following checks were performed against the Linksys E4200:

- router package installation and repeated idempotent upgrade;
- startup/MyPage preservation;
- the legacy fixed-preference undercut that motivated dynamic post-connect
  allocation, plus exact owned cleanup and preservation of unrelated policy
  entries;
- WAN table `213` and tunnel table `212`;
- default empty chain with no traffic effect;
- the explicit `UU Remote -> Direct` workflow scenario, compilation, and DNS
  resolution; fresh product configurations still start with no policy;
- four Direct service groups expanding to 56 domain/network rules;
- real HTTPS request to `uuyc.163.com`;
- 49 packets incrementing the `0x4000000/0xc000000` mark rule in a live
  verification run;
- direct table pointing to `vlan2`;
- removal of the active `PREROUTING` jump and router-local watchdog
  restoration, with the release cadence now one ensure per 60 seconds;
- rollback to zero rules and reapply to the alternate chain;
- upgrade replacing the watchdog PID while retaining persisted rules;
- orphaned old-watchdog upgrade recovery and owner-aware PID-file cleanup;
- controlled watchdog termination reporting degraded, followed by normal
  start recovery;
- one exact watchdog process remaining healthy across multiple intervals;
- MyPage JSON reporting healthy status;
- physical router reboot reconstruction from NVRAM, with one startup entry,
  one watchdog, both policy rules, and retained MyPage commands;
- direct Internet requests to UU Remote, Cloudflare, and Google after reboot;
- preservation of Astrill's active-session DNS across a dnsmasq restart,
  followed by correct YouTube resolution without a tunnel reconnect;
- connected tests of Google, YouTube, Instagram, UU Remote, WeChat, Taobao,
  and Meituan from Ubuntu and macOS;
- exact live Direct mark and return counters for all four bypass services;
- a controlled Astrill disconnect where Direct traffic continued, VPN-only
  traffic stopped, and the companion did not reconnect Astrill;
- full native-only restoration with no remaining companion NVRAM, MyPage,
  firewall, policy-rule, watchdog, or runtime object, followed by explicit
  reinstall and policy restore;
- 19 deduplicated LAN entries after the guarded static migration, combining 18
  reservations, five current leases, and four active LAN neighbors, with the
  WAN neighbor excluded;
- router MyPage rendering at `1280x900` and `390x844` with loaded status and
  no control/text overlap;
- native GTK rendering of policies, all 261 services, country routes, LAN
  devices, Astrill endpoints, synchronized native settings, route
  recommendations, router operations, login startup, and extension state at
  `1180x760` and `880x600`;
- isolated GUI debugging through noVNC without opening a window in the active
  Ubuntu desktop session.
- boot-persistent loopback-only noVNC startup and a nonblank direct capture of
  its isolated X display.
- a second native-only E4200 with no companion markers/runtime, native website
  Include mode, direct ordinary egress, VPN egress for a listed AI service,
  177 applet endpoints at inspection time, and 17 LAN clients loaded through
  read-only SSH;
- a read-only GTK run showing the safety banner, disabled native/router write
  controls, and the companion-free Devices inventory.
- one combined healthy monitor snapshot carrying 34 native settings plus
  native and companion health in a single SSH session.
- applet address-map parsing, fixed and ranged TCP probe selection, bounded
  endpoint latency validation, successful connection timing, and no-reply
  handling;
- companion `0.2.4` switching from a fully down tunnel to server `998` with
  RouterPro VPN TCP (protocol index `3`) in 28.4 seconds, demonstrating why
  the former 30-second switch cutoff was marginal and verifying the new
  60-second boundary;
- installed companion maintenance configured for a 60-second ensure and a
  domain refresh every 30 cycles (approximately 30 minutes), with no desktop
  polling involved;
- applet country-name normalization, default-order preservation, country
  sorting, measured fastest/slowest sorting, and stable pending/no-reply/
  unmeasured ordering;
- a live current-path run receiving TCP latency from all 173 applet locations,
  with observed values from 172.3 ms to 487.2 ms at inspection time.
- Endpoints Country/Ping header and row rendering at both `1180x760` and
  `880x600` without control, row-label, or result overlap.
- `0.2.10` real Windows-platform captures at `1180x760` for Services,
  Connection, Endpoints, and the seven-section Astrill page, with readable
  text and no observed overlap;
- `0.2.11` real Windows-platform captures at `1180x760` for the compact
  Endpoints workspace and its modeless PC-latency dialog, including a real
  Favorite-button click with an unrelated Connection draft preserved;
- a reversible GUI favorite add/remove for Singapore server `1498`, with the
  committed `astrill_favlist` read back after each action and the original
  nine-record string restored exactly;
- the reverse path, where the same favorite was written as a router-origin
  applet change, appeared after the explicit GUI sync, and disappeared after
  the original router value was restored and synchronized again;
- unchanged post-test native state on server `1109` with RouterPro UDP,
  `astrill_autocycle=1`, `astrill_autostart=0`, and the tunnel up;
- post-test HTTPS responses from Google (`204`), YouTube (`200`), and Instagram
  (`200`), with the UU Remote bridge and GameViewer server processes active.

### 2026-07-30 Windows UU, Nutstore, And Companion 0.2.5 Check

The finalized companion archive was 16,598 bytes, encoded into 13 NVRAM chunks.
Before installation, Astrill was down, `tun0` and companion RPDB rules were
absent, and a full DD-WRT NVRAM plus runtime backup was captured. After the
upgrade, 8,935 NVRAM bytes remained free. Status reported companion `0.2.5`,
`policy_health=ready`, verified precedence, one watchdog, the active mangle
jump, no rebase marker, and the VPN-mark fail-closed guard. Table `213`
contained only the WAN default through `vlan2`; table `212` contained only
`blackhole default metric 32767`.

The Windows selected-apply path then replaced the router document with exactly
`uu-remote-18bc36c7` and `nutstore-jianguoyun-7ebf346c`. The two enabled local
rules compiled to 24 Direct rows and 2,688 bytes, resolved to 43 addresses with
zero unresolved domains, and included both `a56.gdl.netease.com` and
`dav.jianguoyun.com`. The active Windows flows were UU Remote at
`223.252.194.149:443` and Nutstore at `160.19.208.29:80`. Controlled TCP probes
left four packets / 172 bytes on the UU Direct mark and return pair and 26
packets / 2,179 bytes on the active Nutstore pair.

Two managed connects allocated the same verified ordering each time: Direct
preference `32762`, VPN-policy preference `32763`, and native Astrill minimum
`32764`. While connected, table `212` held the usable
`default via 198.18.64.1 dev tun0` plus the worse-priority metric-32767
blackhole fallback. The final managed disconnect removed both companion RPDB
rules, restored the filter fail guard and blackhole-only VPN table, retained
both applied origins, and reported healthy/ready with Astrill down after later
watchdog and GUI reconciliation checks.

The native Windows `0.2.12` bundle was rebuilt from the verified source instead
of reusing the stale bundle, installed per-user, and launched without a console
child. Its Desktop and Startup shortcuts target the installed GUI, the legacy
Start Menu shortcut is absent, the Settings page reports
`Astrill Lazy Router 0.2.12`, and the bundled router/catalog data reports
companion `0.2.5` plus the UU and Nutstore additions.

### 2026-07-29 UU And Endpoint Check

The active Ubuntu UU/GameViewer server flow was
`192.168.1.100 -> 34.95.122.33:443`. The enabled `UU Remote -> Direct` rule had
no source restriction, so it applied to every LAN device. A controlled HTTPS
probe increased both its Direct mark and return rules from 0 to 22 packets.
Policy table `213` pointed to the WAN interface while Astrill table `212`
pointed to `tun0`; the matching traffic therefore bypassed Astrill rather than
merely matching a displayed GUI rule.

Ubuntu and macOS simultaneously observed the same VPN egress while the router
was connected to Los Angeles A with RouterPro UDP:

| Client | 1.1.1.1 average | Loss | Download | Upload |
| --- | ---: | ---: | ---: | ---: |
| Ubuntu | 173.3 ms | 20% | 8.17 Mbit/s | 7.80 Mbit/s |
| macOS | 173.8 ms | 10% | 7.14 Mbit/s | 5.21 Mbit/s |

Throughput used sequential Cloudflare 25 MB download and 10 MB upload probes.
These are a point-in-time path check, not a server capacity benchmark. The
matching UU rule covers catalogued destinations; a newly introduced UU
hostname or address still requires a catalog update before it can bypass.

The bootstrap was exercised directly, during multiple upgrades, and through a
physical reboot. Astrill was later connected using its own upstream state while
the pre-existing `astrill_autostart=0` setting remained unchanged. No endpoint
switch was forced during this release verification.

## Package Verification

The release wheel is checked for all catalog data and executable modes on the
namespace helper and router scripts.

## Health Checklist

```bash
astrill-lazy access status
astrill-lazy inspect
astrill-lazy status
ssh astrill-router 'ip rule show'
ssh astrill-router 'ip route show table 212'
ssh astrill-router 'ip route show table 213'
ssh astrill-router 'iptables -t mangle -S | grep AL_LAZY'
ssh astrill-router '/tmp/astrill-lazy/alctl logs'
```

Healthy state requires:

- `vpn_state` up when any VPN rules are enabled;
- one active A/B jump;
- a live watchdog PID;
- both mark policy rules;
- no unresolved domain warning, unless a prior cached address is expected.

For native-only inspection, companion chains, policy tables, and a watchdog are
expected to be absent. `astrill-lazy inspect` should instead report a healthy
native applet and `"installed": false` for the companion.

## Failure Response

1. Run `astrill-lazy status`.
2. Inspect `alctl logs`.
3. Run `astrill-lazy refresh`.
4. Run `astrill-lazy rollback` if the issue followed a rule change.
5. Run `alctl stop` to return entirely to Astrill behavior.
6. Use Telnet recovery only if SSH is unavailable.

Do not reinstall Astrill as a first response. Preserve its current applet and
NVRAM state before any upstream update.
