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
29 tests passed
Ruff: all checks passed
Catalog: 261 profiles, 608/608 unique seed hosts resolved to IPv4
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
- application command parsing;
- POSIX shell parsing and the no-`eval` policy contract;
- DHCP/static/ARP client inventory merging, MAC deduplication, and WAN
  exclusion;
- SSH banner error cleanup.

## Live Router Verification

The following checks were performed against the Linksys E4200:

- router package installation and repeated idempotent upgrade;
- startup/MyPage preservation;
- policy preferences `32000` and `32001`;
- WAN table `213` and tunnel table `212`;
- default empty chain with no traffic effect;
- `UU Remote -> Direct` compilation and DNS resolution;
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
- six deduplicated LAN devices from four leases and five active LAN neighbors,
  with the WAN neighbor excluded;
- router MyPage rendering at `1280x900` and `390x844` with loaded status and
  no control/text overlap;
- native GTK rendering of policies, all 261 services, country routes, LAN
  devices, Astrill endpoints, router operations, login startup, and extension
  state at `1180x760` and `880x600`;
- isolated GUI debugging through noVNC without opening a window in the active
  Ubuntu desktop session.

The bootstrap was exercised directly, during multiple upgrades, and through a
physical reboot. Astrill was later connected using its own upstream state while
the pre-existing `astrill_autostart=0` setting remained unchanged. No endpoint
switch was forced during this release verification.

## Package Verification

The release wheel is checked for all catalog data and executable modes on the
namespace helper and router scripts.

## Health Checklist

```bash
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

## Failure Response

1. Run `astrill-lazy status`.
2. Inspect `alctl logs`.
3. Run `astrill-lazy refresh`.
4. Run `astrill-lazy rollback` if the issue followed a rule change.
5. Run `alctl stop` to return entirely to Astrill behavior.
6. Use Telnet recovery only if SSH is unavailable.

Do not reinstall Astrill as a first response. Preserve its current applet and
NVRAM state before any upstream update.
