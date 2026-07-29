# Rule Model

## Source Rules

The desktop stores editable JSON rules with these fields:

| Field | Meaning |
| --- | --- |
| `id` | Stable ASCII identifier |
| `name` | User-facing name |
| `match_kind` | `service`, `domain`, `cidr`, `device`, or `process` |
| `selector` | Service ID, domain, IPv4 network/address, or executable path |
| `target` | `direct` or `vpn` |
| `region` | `direct`, current Astrill, or preferred catalog region |
| `enabled` | Whether the rule is compiled as active |
| `priority` | `0..9999`, lower values run first |
| `protocol` | `any`, `tcp`, or `udp` |
| `ports` | `-`, a destination port, ranges, or comma-separated entries |
| `metadata` | Application profile and extension-owned metadata |

Port-specific rules must select TCP or UDP. IPv6 input is rejected on this
router.

## Compilation

Service rules expand to one domain rule for every catalog seed domain and one
CIDR rule for every declared literal endpoint network. Process rules compile
to their allocated namespace address as a device rule. A process rule without
an identity is skipped with a warning instead of accidentally matching the
host.

The router receives schema `astrill-lazy-rules-v1`, with exactly ten validated
TSV fields:

```text
id enabled priority kind selector target protocol ports encoded-label origin
```

Only `domain`, `cidr`, and `device` reach the router. Labels are URL encoded;
identifiers and selectors are constrained to small ASCII grammars. The router
does not use `eval` or construct a shell command from a rule.

Compiled documents are limited to 6,144 bytes. Before activation, the
controller verifies that NVRAM can retain both the new document and its rollback
predecessor with at least 2 KB of headroom.

## Precedence

Rules are sorted by `(priority, id)`. The router sets a mark and returns on the
first match. Put narrow domain/network rules before broad device rules when an
exception is required.

Example:

1. `UU Remote`, domain service, direct, priority `100`
2. `Work laptop`, device, Astrill, priority `500`

UU Remote goes direct from that laptop because its destination match runs
first. Other laptop traffic uses Astrill.

## Domain Resolution

DD-WRT resolves domain seeds through its LAN DNS address:

- up to 16 unique IPv4 addresses are retained per domain;
- refresh occurs every five minutes;
- an unsuccessful refresh reuses the prior addresses when available;
- unresolved domains are counted and shown in both UIs.

Without firmware `ipset` support, a domain policy cannot automatically learn
every future CDN hostname. Catalogs therefore list primary and common service
domains, and users can add observed domains as explicit rules.

## Countries

`region` is a preferred Astrill country, not an independent tunnel selector.
All enabled VPN rules use table `212` and therefore the current `tun0`. The
compiler warns when enabled rules request more than one specific VPN region.
The Countries view reports assignments and conflicts. The Endpoints view
performs the actual shared-server switch.

## Incremental Defaults

New rules extend the active native Astrill mode:

- global or exclude-list native routing creates a Direct exception;
- include-list native routing creates an Astrill union;
- country remains `No country override` until explicitly selected.

Native rules have earlier policy preferences, so a companion rule cannot
silently reverse an explicit native website or device decision.

## Detection Metadata

Network detection stores the checked destination, timestamp, Direct latency,
Astrill latency, recommended target, reason, and applied state under
`metadata.route_recommendation`. It compares only enabled service and website
policies while Astrill is connected. A missing or one-sided latency keeps the
current target. Service catalog route profiles prevent an ICMP-only speed
result from overriding a known access requirement.

## Default Rule

On first run:

```json
{
  "id": "uu-remote-direct",
  "match_kind": "service",
  "selector": "uu-remote",
  "target": "direct",
  "region": "direct",
  "priority": 100,
  "enabled": true,
  "metadata": {
    "minimum_bypass": true
  }
}
```

The core catalog maps that service to its primary app, signaling, relay,
logging, and file hosts plus observed literal fallback endpoints.
