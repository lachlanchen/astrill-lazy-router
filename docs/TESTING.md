# Testing And Operations

## Automated Checks

Run from the repository:

```bash
.venv/bin/pytest
.venv/bin/ruff check desktop tests scripts/validate-catalog.py
.venv/bin/python scripts/validate-catalog.py --dns
shellcheck -x -s sh \
  scripts/*.sh helpers/astrill-lazy-netns \
  router/alctl router/alapi router/alpage router/bootstrap.sh
desktop-file-validate data/*.desktop
appstreamcli validate --no-net data/*.metainfo.xml
```

Current result:

```text
100 tests passed
Ruff: all checks passed
Catalog: 261 profiles, 650/650 unique seed hosts resolved to IPv4
ShellCheck: no findings
Desktop entry: valid
AppStream metadata: valid
```

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
- overlay policy preferences `29000` and `29001`, their precedence guard, and
  exact cleanup of legacy `32000` and `32001` rules;
- WAN table `213` and tunnel table `212`;
- default empty chain with no traffic effect;
- `UU Remote -> Direct` compilation and DNS resolution;
- four Direct service groups expanding to 56 domain/network rules;
- real HTTPS request to `uuyc.163.com`;
- 49 packets incrementing the `0x4000000/0xc000000` mark rule in a live
  verification run;
- direct table pointing to `vlan2`;
- removal of the active `PREROUTING` jump and watchdog restoration within 18
  seconds;
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
- applet country-name normalization, default-order preservation, country
  sorting, measured fastest/slowest sorting, and stable pending/no-reply/
  unmeasured ordering;
- a live current-path run receiving TCP latency from all 173 applet locations,
  with observed values from 172.3 ms to 487.2 ms at inspection time.
- Endpoints Country/Ping header and row rendering at both `1180x760` and
  `880x600` without control, row-label, or result overlap.

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
