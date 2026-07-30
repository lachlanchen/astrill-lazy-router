# Hybrid Policy Storage

## Status

This document is a design proposal. The current companion does **not** yet
separate persistent core policy from RAM overlays.

Today, the companion package and active files run from the RAM-backed
`/tmp/astrill-lazy` directory, but every successful policy Apply stores the
complete current and previous compiled documents in NVRAM. The current
6,144-byte compiled-document limit therefore protects both persistence
headroom and router runtime.

The proposed design separates those concerns:

- a small persistent core remains available immediately after a router reboot;
- larger, computer-owned overlays live only in router RAM;
- each GUI restores only its own overlay after observing a new router runtime;
- the companion transactionally composes the core and all valid overlays into
  one effective policy; and
- no RAM-overlay operation performs `nvram commit`.

This is a bridge between the current router-owned destination policy and the
longer-term [device-local routing](DEVICE_ROUTING.md) design. It reduces NVRAM
pressure and multi-controller conflicts, but it does not make destination
catalogs process-aware.

## Why The Design Is Feasible

A read-only snapshot of the validated E4200 showed:

| Resource | Observed value |
| --- | ---: |
| `/proc/meminfo` `MemTotal` | 58,708 KiB |
| `MemFree` | about 31-32 MiB |
| `/tmp/astrill-lazy` package and runtime files | about 124 KiB |
| Current UU Remote plus Nutstore document | 2,688 bytes |
| Current rollback document | 1,655 bytes |
| NVRAM free | about 6.3 KiB |
| Complete Windows library | 313 compiled rows / 28,373 bytes |

These figures are one runtime snapshot, not a guaranteed capacity. The
28,373-byte source document is small relative to available RAM. Its generated
iptables matches, DNS results, connection tracking, and linear rule traversal
can cost substantially more memory and CPU than the document itself.

The currently saved UU Remote, Nutstore/Jianguoyun, and WeChat rules compile
to 38 rows and 3,846 ASCII bytes. That candidate fits the existing 6,144-byte
core contract. A deterministic gzip representation measured 534 bytes, or 712
bytes after base64 encoding. Compression is therefore useful for the small
persistent core, although every real save must still pass live NVRAM-headroom
checks and verified readback.

Two uncompressed 3,846-byte core generations would leave only about 2.9 KiB
free in the observed NVRAM state. That is too close to the current 2 KiB
reserve for comfortable package or settings growth. A compressed current and
rollback core, or a compressed current core plus a smaller known-good recovery
baseline, provides better margin. Compression and chunk decoding are companion
changes that must be proven with the exact BusyBox tools on the router; the
current policy records are plain NVRAM strings.

Compressing the complete library is not the final answer. The current complete
document compresses close to the NVRAM limit by itself, leaving no comfortable
space for rollback or growth, and compression does not reduce the expanded
firewall workload.

## Storage And Ownership

| Layer | Owner | Location | Survives reboot | Purpose |
| --- | --- | --- | --- | --- |
| Local library | One computer | Desktop configuration | Yes | Authoritative editable catalog and rules |
| Persistent core | Router administrator | Compressed, chunked NVRAM record | Yes | Small policies that must work while every computer is offline |
| RAM overlay | One paired controller | `/tmp/astrill-lazy/overlays/` | No | Larger or computer-specific policy restored on demand |
| Effective policy | Companion | `/tmp/astrill-lazy/effective.tsv` | No | Deterministic composition currently enforced by A/B chains |
| Previous effective policy | Companion | RAM only | No | Fast rollback for the most recent runtime transaction |

The persistent core should contain only deliberate, high-value rules. On this
router, reasonable candidates are:

- UU Remote to Direct;
- Nutstore/Jianguoyun to Direct; and
- WeChat to Direct.

Those catalog profiles are destination-based. UU Remote and WeChat can also
use dynamic peer, relay, CDN, or encrypted-discovery paths that a seed-domain
list cannot guarantee to identify. A source-device rule or future
process-aware local backend remains the broader fallback.

## Proposed RAM Layout

```text
/tmp/astrill-lazy/
  core.tsv
  overlays/
    <controller-id>.tsv
    <controller-id>.meta
  effective.tsv
  effective.previous.tsv
  resolved.tsv
  unresolved.txt
  runtime-epoch
```

The startup bootstrap reconstructs and verifies the companion package exactly
as it does now. It then decodes the current persistent core, validates it, and
creates a core-only effective policy. Overlay files begin empty after every
boot.

The NVRAM core record should be versioned, deterministically compressed,
base64 encoded, divided into bounded chunks, and protected by a digest. Current
and previous core records retain persistent rollback without storing two raw
TSV documents. A corrupt core must not be partially decoded or activated.

## Boot And Restore Lifecycle

1. DD-WRT reconstructs the companion package from NVRAM.
2. The companion reconstructs and validates the persistent core.
3. It builds and activates a core-only policy using the existing inactive/active
   chain transaction.
4. It creates a new non-secret `runtime_epoch` value in `/tmp`.
5. Status exposes that epoch plus the core, overlay, and effective hashes.
6. Each GUI checks status once at application startup, after a relevant Windows
   network-change event, before an explicit router action, or when the user
   selects **Refresh router**.
7. If that GUI's expected overlay hash is absent, it uploads only its own
   overlay and verifies the resulting effective hash.
8. If the GUI is offline, the router continues safely with the persistent core
   and any overlays already restored by other computers.

This does not require frequent SSH polling. If Windows starts before DD-WRT is
ready, the implementation may use a short, bounded backoff sequence with
jitter and then stop. A manual **Restore RAM overlay now** action remains
available. Restoring before Windows sign-in would require a separate,
explicitly installed background service; the current Startup shortcut runs
only after sign-in.

Automatic overlay restoration is a router write and must be an explicit saved
opt-in such as **Restore this computer's RAM overlay after router reboot**.
Without that opt-in, startup reports the missing overlay and leaves restoration
manual.

## Transactional Commands

The current `alctl apply` path both activates and persists one complete
document. The hybrid design needs separate operations:

```text
alctl core-apply FILE
alctl core-rollback
alctl overlay-put CONTROLLER_ID EXPECTED_GENERATION FILE
alctl overlay-remove CONTROLLER_ID EXPECTED_GENERATION
alctl overlay-list --json
alctl effective-status --json
```

The current router MyPage origin actions transform the one active document and
then call the persistent Apply path. They must become layer-aware. Until that
exists, toggle/route controls for volatile origins must be disabled rather
than accidentally copying an overlay into NVRAM.

### Core Apply

`core-apply` must:

1. validate the complete candidate before mutation;
2. enforce the persistent-core byte and NVRAM-headroom limits;
3. reject origin-ID or layer-namespace collisions with installed overlays;
4. compose the candidate core with the current valid overlays;
5. build and verify the inactive chain;
6. switch the active jump;
7. encode and verify current and previous core records in NVRAM;
8. commit NVRAM once; and
9. return the core and effective readback hashes.

An activation, encode, commit, or readback failure must retain the previous
persistent core. If a persistence failure occurs after the new chain was
activated, the transaction must explicitly switch the old A/B jump back before
returning failure.

### Overlay Upsert

`overlay-put` must:

1. authenticate the controller and validate its stable ID;
2. read a bounded candidate into a private temporary file;
3. validate every field, source scope, row count, and generated-match estimate;
4. require an expected generation to prevent lost updates;
5. compose the core and all owner overlays deterministically;
6. build and verify the inactive chain;
7. switch the active jump;
8. atomically rename the candidate into that owner's overlay slot; and
9. return owner, overlay, and effective hashes.

It must never call `nvram set` or `nvram commit`. An interrupted or invalid
upload leaves the previous effective chain active. Immediately after reboot,
failure to restore an overlay leaves the verified core-only chain active.

## Multiple Computers

Separate overlay files prevent last-writer-wins storage, but storage ownership
alone does not provide independent routing. A destination-only rule affects
every LAN device.

For independent per-computer choices, every owner overlay must be scoped to a
stable source identity:

- a reserved DHCP address plus validated MAC address on the local bridge;
- a dedicated source subnet or application namespace address; or
- a future authenticated route-intent carrier.

The current ten-field `astrill-lazy-rules-v1` document cannot express a source
and destination conjunction in one row. A hybrid implementation therefore
needs either a versioned source-scope field or one owner subchain reached only
after matching the paired source identity.

A safe chain order is:

1. return for local and non-routable destinations;
2. explicitly global persistent-core rules;
3. jump to the matching source owner's overlay chain;
4. optional shared RAM rules; and
5. return to native Astrill behavior.

Core and overlay origin IDs must occupy separate namespaces. An overlay cannot
shadow or disable a persistent-core origin implicitly. Changing that contract
requires an explicit core edit and its stronger confirmation.

Each paired controller should have:

- a stable random `controller_id`;
- a dedicated Ed25519 SSH identity;
- an allowed source IP/MAC binding;
- an owner-specific byte, row, and generated-match quota; and
- permission to upsert or remove only its own overlay.

Root SSH remains an administrator path. A production multi-controller design
should use a forced, allowlisted companion command rather than giving every
computer unrestricted root commands. MAC identity is sufficient for accidental
cross-device protection on a trusted home LAN, but it is not authentication
against a hostile LAN.

## Capacity And Admission Control

The implementation must expose separate limits instead of treating NVRAM and
RAM as one 6,144-byte budget:

| Limit | Protects |
| --- | --- |
| Persistent core raw and encoded bytes | NVRAM headroom and reboot recovery |
| Overlay bytes per owner | One controller consuming all RAM policy capacity |
| Total effective rows | Validation and composition time |
| Resolved addresses per domain | DNS fan-out |
| Total generated iptables matches | Kernel memory and packet traversal cost |
| Minimum free/reclaimable RAM | Router and radio stability |
| Maximum apply duration | Management availability |

The companion already limits one domain to 16 resolved IPv4 addresses. A RAM
overlay design must additionally count the final generated matches before
activation and reject the candidate while retaining the old chain if any
tested limit would be exceeded.

The watchdog must reconcile and refresh the composed effective document, not
the persistent core or one owner's overlay in isolation.

Initial limits must be selected from an E4200 benchmark, not from document
bytes alone. Acceptance testing should measure free memory, load, apply time,
DNS refresh time, packet latency, and both radios at increasing rule counts.
The complete 313-row library should be treated as a benchmark candidate, not
automatically declared safe because its text fits in RAM.

## Status And GUI Model

The Policies page should show five distinct states:

| UI item | Meaning |
| --- | --- |
| Local library | Everything saved on this computer |
| Persistent core | Small router policy available immediately after reboot |
| This computer's RAM overlay | Owner-scoped policy expected from this GUI |
| Other RAM overlays | Counts and owners restored by other paired controllers |
| Effective router policy | Core plus every currently active overlay |

Suggested status fields are:

```json
{
  "runtime_epoch": "opaque-value",
  "core": {
    "generation": 4,
    "hash": "sha256:...",
    "origins": 3,
    "rows": 38,
    "bytes": 3846
  },
  "overlays": [
    {
      "owner": "controller-id",
      "generation": 7,
      "hash": "sha256:...",
      "source": "192.168.1.100",
      "origins": 85,
      "rows": 275,
      "bytes": 24527
    }
  ],
  "effective": {
    "hash": "sha256:...",
    "rows": 313,
    "generated_matches": null
  }
}
```

Hashes are examples; status must never expose private keys or credentials.
`generated_matches` is populated after DNS resolution and chain generation.

The warning hierarchy should be:

- green: persistent core and this computer's overlay match their expected
  hashes;
- amber: core is active, but this computer's volatile overlay has not yet been
  restored after reboot;
- neutral: additional local policies are deliberately outside this router
  profile;
- red: the expected core is missing, an overlay restore failed, or policy
  fail-closed health is degraded.

Actions should be explicit:

- **Pin selected to persistent core** warns that it writes NVRAM;
- **Load selected into router RAM** reports that it is volatile and performs no
  NVRAM commit;
- **Restore this computer's RAM overlay now** reconciles one owner;
- **Remove this computer's RAM overlay** leaves other owners untouched; and
- **Clear all RAM overlays** is administrator-only.

## Failure And Reboot Matrix

| Event | Required result |
| --- | --- |
| Router boots while all PCs are off | Verified core-only policy becomes active |
| GUI starts after router boot | Its missing or stale overlay is restored once |
| GUI starts before router is ready | Bounded retry stops safely; manual restore remains available |
| One overlay upload is interrupted | Previous effective chain remains active |
| One PC is asleep | Its existing RAM overlay remains source-scoped until reboot or removal |
| Router reboots | All overlays disappear; core remains; each PC can independently restore |
| One controller changes policy | Other controller files and generations are unchanged |
| Astrill disconnects | VPN-targeted traffic retains the existing fail-closed behavior |
| RAM admission check fails | Candidate is rejected; active policy and NVRAM remain unchanged |
| Core record is corrupt | Companion reports degraded state and never activates a partial core |

## Relationship To Route Intent

RAM overlays scale persistence better than NVRAM documents, but DD-WRT still
performs destination classification. Very large catalogs still consume router
CPU, DNS work, and firewall rules.

The longer-term scalable boundary remains:

```text
computer classifies application/domain
             |
             v
      inherit / Direct / VPN
             |
             v
router executes a constant-size path decision
```

On hardware with a safe packet-intent matcher, a local classifier can carry
that decision directly. This E4200 build does not expose a suitable DSCP/TOS
matcher, so source identities or a small LAN path broker are the practical
fallbacks for true per-application independence. The hybrid RAM overlay is
still valuable for shared devices and as a migration step, but it should not
replace the device-local architecture with another unbounded router catalog.

## Implementation And Acceptance Order

1. Correct the GUI distinction between local library and selected router
   profile.
2. Add compressed, hashed persistent-core storage while preserving current
   Apply compatibility.
3. Add core-only boot and status hashes.
4. Add one owner-scoped RAM overlay and prove that its restore performs zero
   NVRAM writes.
5. Add source binding and multi-owner conditional updates.
6. Benchmark increasing rows and generated matches on the E4200.
7. Add bounded startup/network-change restoration on Windows.
8. Add the local route-intent backend separately.

Required tests include core-only physical reboot, GUI-before-router startup,
two independent computers, interrupted uploads, stale generations, conflicting
priorities, low-memory rejection, DNS refresh, rollback, Astrill disconnect,
VPN fail-closed behavior, and an NVRAM before/after comparison for every
overlay operation.
