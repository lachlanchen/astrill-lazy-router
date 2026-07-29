# Device-Local Routing

## Scope

The router companion and a device-local tunnel engine solve different
problems:

- the router companion classifies traffic after it reaches DD-WRT and uses the
  one Astrill tunnel owned by the applet;
- a device-local engine can classify applications before encryption and can
  maintain several independent tunnel sessions on that device.

Device policy schema v1 is the shared, platform-neutral control model for the
second case. It is deliberately non-enforcing in this release. Validation,
traffic decisions, and country-prefix compilation are functional, but no
system route or tunnel is changed. This protects the stable router deployment
until local provider profiles are available and each privileged backend has
its own rollback tests.

## Policy Model

A policy contains up to three named tunnel slots, a default route, and ordered
rules. A route is one of:

- `direct`: use the primary physical network;
- `tunnel`: use one fixed tunnel slot;
- `auto`: choose between two or three tunnel slots using fresh health probes.

Fixed and Auto routes declare either a `direct` or `block` fallback. Direct is
appropriate for performance routing where uninterrupted Internet access is
more important. Block is appropriate when sending protected traffic outside
the tunnel would be a leak.

Rules match application IDs, service catalog IDs, domain suffixes, IP
networks, ISO 3166-1 alpha-2 destination country codes, or named
country groups. Groups are explicit lists of codes in the policy, so regions
such as Europe remain data-driven and auditable rather than hidden in the
engine. Lower numeric priority wins. At equal priority, specificity is:

1. application;
2. service;
3. domain;
4. network;
5. individual country;
6. country group.

The rule ID is the final deterministic tie breaker. This keeps UU Remote,
WeChat, Taobao, and Meituan Direct even when their server addresses are in a
country with a broader tunnel policy, unless the country rule is explicitly
given a lower numeric priority.

The schema is
[`schemas/device-policy-v1.schema.json`](../schemas/device-policy-v1.schema.json).
The complete three-slot example is
[`examples/device-policy.sample.json`](../examples/device-policy.sample.json).
Configuration references are opaque identifiers; credentials never belong in
the policy document.

## Auto Selection

Auto selection excludes unreachable probes and probes older than 120 seconds.
The remaining paths are scored as:

```text
latency_ms + (packet_loss_percent * 20)
```

The current healthy path is retained for at least five minutes after a switch.
After that hold period, it remains selected while its score is within 15
percent of the best path. These two controls prevent endpoint oscillation.
An unhealthy current path is replaced immediately.

Auto routing chooses among already established provider sessions. It does not
make one Astrill router applet provide three tunnels.

## Country Prefixes

Country matching is based on the destination IP, not the language, top-level
domain, company headquarters, or Astrill endpoint country. The route compiler
accepts strict CSV:

```text
country_code,network
CN,1.0.1.0/24
US,8.8.8.0/24
```

It validates each code and prefix, rejects duplicates, applies policy
precedence, and collapses adjacent prefixes only when they have the same final
route.

Production builds should consume a regularly updated licensed country
database, such as MaxMind GeoLite2 Country. GeoIP is inherently approximate,
and anycast or CDN addresses may not reflect the service or user location.
The database snapshot version must be recorded with every compiled plan.

## Commands

All current device commands are read-only:

```bash
astrill-lazy device-policy validate examples/device-policy.sample.json

astrill-lazy device-policy decide examples/device-policy.sample.json \
  --domain api.nrd.nie.163.com --country CN

astrill-lazy device-policy routes examples/device-policy.sample.json \
  examples/country-networks.sample.csv
```

Each response contains `"enforcing": false`. This flag must not become true
until a backend has opened and verified its tunnels, installed routes
transactionally, and retained an automatic restore snapshot.

## Platform Backends

### Linux

The backend will run each provider profile in a separate network namespace,
use one TUN interface and route table per slot, and classify local traffic with
validated nftables marks and `ip rule`. A transactional inactive/active
ruleset, watchdog, and restore timer are required. OpenVPN is installed on the
current Ubuntu host, but no local Astrill profile exists.

### macOS And iOS

A signed `NEPacketTunnelProvider` owns the virtual interface. Included and
excluded IP routes can implement destination-country routing. Multiple
upstream sessions must be multiplexed inside that one provider rather than
starting several competing system VPN configurations.

The current Mac has Xcode and valid Apple Development identities. Its existing
provisioning profiles do not include the Network Extension entitlement, and it
has no local Astrill client or profile. The entitlement and provider
configuration are therefore hard deployment prerequisites, not conditions to
bypass.

### Android

One foreground `VpnService` owns the device TUN interface. Android permits only
one active VPN service per user or profile, so one service must classify
packets and maintain all upstream sessions internally. Package allow/disallow
lists can implement coarse per-app routing; destination and multi-endpoint
selection happen inside the service.

### Windows

A signed Windows service owns tunnel sessions and uses Windows Filtering
Platform for application-aware classification. Wintun or an installed provider
adapter carries tunnel traffic. Installation, driver signing, and WFP cleanup
must be tested across upgrade and crash recovery before this backend can
enforce policy.

## Provider Constraint

Three simultaneous endpoints are technically possible only when the provider
supplies three usable local client configurations and the account permits
concurrent sessions. The router applet's generated OpenVPN state and private
credentials are not copied into desktop or mobile builds. Doing so would
couple local clients to an undocumented router implementation and could
disconnect the currently working router tunnel.

Until those provider prerequisites are supplied, the Ubuntu GUI and the Mac
launcher continue to control the single router endpoint, and device policy
remains a validated plan rather than a network mutation.

## Primary Platform References

- [Apple: NEPacketTunnelProvider](https://developer.apple.com/documentation/networkextension/nepackettunnelprovider)
- [Apple: Routing VPN network traffic](https://developer.apple.com/documentation/networkextension/routing-your-vpn-network-traffic)
- [Android: VPN developer guide](https://developer.android.com/develop/connectivity/vpn)
- [Microsoft: Windows Filtering Platform](https://learn.microsoft.com/en-us/windows/win32/fwp/windows-filtering-platform-start-page)
- [OpenVPN: Multiple client instances](https://openvpn.net/community-docs/creating-configuration-files-for-server-and-clients.html)
- [MaxMind: Country database format](https://dev.maxmind.com/geoip/docs/databases/city-and-country/)
