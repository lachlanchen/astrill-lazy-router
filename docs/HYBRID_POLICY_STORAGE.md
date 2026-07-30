# Hybrid Policy Storage

## Status

Companion `0.2.11` and Windows app `0.2.13` implement the hybrid policy model.
It separates the editable local library from two router layers:

- a small global core stored in NVRAM and activated immediately after reboot;
- controller-owned, source-scoped overlays stored only in router RAM; and
- a deterministic effective document composed from the core and every valid
  overlay.

This solves the original 6,144-byte storage conflict without pretending the
E4200 can safely enforce an unlimited catalog. The persistent core keeps its
strict NVRAM limit. RAM overlays have separate document, generated-firewall,
memory, and duration admission limits.

The feature does not create multiple Astrill tunnels and does not identify a
Windows process. All VPN policies still share the router's one active Astrill
endpoint. Each overlay applies to one LAN source identity, not one executable.

## Why Two Router Layers

The Windows library on the verified workstation currently expands as follows:

| Scope | Origins | Compiled rows | ASCII bytes |
| --- | ---: | ---: | ---: |
| Complete enabled library | 88 | 316 | 28,686 |
| UU Remote + Nutstore/Jianguoyun + WeChat core | 3 | 41 | 4,135 |
| Remaining computer overlay | 85 | 275 | 24,551 |

The complete document cannot fit the persistent 6,144-byte contract. The
three high-value Direct policies do fit and remain useful while every computer
is offline. The rest can be restored into RAM for only the computer that owns
them. Adding the overlay source/MAC scope fields produces a 316-row,
38,455-byte effective runtime document.

A read-only E4200 snapshot showed 58,708 KiB total RAM and roughly 6.3 KiB of
free NVRAM after the earlier companion was installed. Those figures motivated
the split but are not capacity promises. DNS fan-out and generated iptables
matches, rather than TSV bytes alone, are the important runtime cost.

## Storage Model

| Layer | Authority | Location | Reboot |
| --- | --- | --- | --- |
| Local library | Windows user | App configuration | Survives |
| Base companion package | Router installer | Base64 NVRAM chunks | Survives |
| Bootstrap payload | Router installer | Deterministic gzip/base64 NVRAM value | Survives |
| Persistent core | Router administrator | Compressed or plain NVRAM rule record | Survives |
| RAM transaction helper | Trusted desktop | `/tmp/astrill-lazy/alhybrid` | Cleared |
| Owner overlay | One paired controller | `/tmp/astrill-lazy/overlays` | Cleared |
| Effective document | Companion | `/tmp/astrill-lazy/effective.tsv` | Rebuilt |
| Runtime epoch and generations | Companion | `/tmp/astrill-lazy` | Renewed |

The persistent footprint is deliberately limited to the base package, the
compressed bootstrap payload, and the small core. The normalized bootstrap is
6,502 bytes; deterministic gzip plus base64 reduces its stored NVRAM value to
2,560 bytes. The deployed base package is 19,960 bytes, exactly 26,616 base64
bytes in 15 chunks, with MD5 `3552747bcb9a06a8f6b64dcbb1ce0675`
and SHA-256
`2f0dbbda03af55a54ebf75fa6a06d2f47ffcd071310082544202edac4422a4be`.
The locked E4200 preflight started with 3,115 bytes free, projected 608 bytes of
growth and 2,507 bytes free—459 bytes above the 2,048-byte reserve. The
physical-reboot readback was 2,494 bytes free, a 446-byte margin. Every install
recomputes the projection from the router's current snapshot.

The package stored in NVRAM contains the base `alctl` runtime. The larger
`alhybrid` extension is shipped by the desktop, atomically uploaded to
`/tmp/astrill-lazy/alhybrid`, and verified by MD5 before use. The upload takes
the same controller lock as package and policy transactions, rechecks the
running and stored base-package identities under that lock, and publishes the
helper with an atomic rename. Excluding the extension keeps the encoded
package inside the router's persistent headroom.

After a reboot, base `alctl` does not need that extension to decode, validate,
and activate the core. Overlay commands become available after a trusted
desktop supplies the matching extension. Persistent-core mutations also use
the helper because it owns the transaction journal and cross-layer rollback;
the desktop stages it before either the guarded or administrator core path.
The helper, overlays, effective document, runtime epoch, and layer generations
are all RAM-only and consume no persistent NVRAM.

The core reuses the companion's proven current/previous rule records. Each is
stored as deterministic gzip plus base64 when that representation is smaller,
or as legacy-compatible plain TSV otherwise. A second persistent generation
is retained only when doing so leaves the enforced 2 KiB NVRAM reserve.

## Packet Order And Ownership

The effective chain is ordered as:

1. return for private, local, multicast, and broadcast destinations;
2. evaluate the global persistent core;
3. enter an owner's overlay only for its source address and optional MAC;
4. return unmatched traffic to native Astrill behavior.

The core therefore has deliberate global authority. An overlay cannot shadow
a core origin, and duplicate origin IDs across layers are rejected.

Each overlay has:

- a stable random controller ID;
- a runtime-scoped generation;
- an MD5 document hash;
- a LAN IPv4 host or subnet;
- an observed bridge-neighbor MAC for a host binding when available; and
- origin, row, and byte metadata.

`auto` is the recommended binding. The router takes the SSH peer address,
requires it to be inside the configured LAN, resolves its MAC from the LAN
bridge ARP table, and stores the address as `/32`. An explicitly entered host
is also upgraded with its observed MAC when available. A subnet can be used
only without a MAC.

MAC binding protects against accidental address reuse on a trusted home LAN;
it is not authentication against a hostile LAN. Key-only SSH and the pinned
router host key remain the controller authentication boundary.

## Boot And One-Shot Restore

1. DD-WRT reconstructs the verified base package from NVRAM.
2. `alctl` decodes and validates the current core.
3. It builds and activates a core-only A/B chain.
4. It creates a fresh opaque runtime epoch and runtime generations.
5. The Windows app checks once at startup, on a relevant Windows network
   change, before an explicit router write, or on **Refresh router**.
6. If automatic restore was explicitly enabled and this controller's expected
   overlay is missing in a new epoch, the app uploads it once.
7. The app verifies owner, source, MAC, generation, hash, and effective status.

The last attempted epoch and error are persisted locally. Relaunching the app
does not repeatedly attack a router that rejected the same restore. Manual
**Restore RAM overlay now** remains available.

There is no recurring desktop SSH poll. The router's existing local watchdog
still ensures its own runtime and routes every 60 seconds. It may refresh a
core-only document about every 30 minutes when no overlay is loaded. Once any
RAM overlay is active, it does not periodically rebuild that effective policy.
Overlay construction occurs only for an explicit load, one-shot
startup/network restoration, or a manual restore/reload.

## Transactional Commands

Normal desktop core writes use generation compare-and-swap:

```text
alctl core-apply EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 \
  EXPECTED_GENERATION FILE|-
alctl core-rollback EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 \
  EXPECTED_GENERATION
```

The administrator compatibility commands `apply` and `rollback` remain
available without a generation guard. The GUI does not use them for normal
hybrid changes. They are still identity-bound and use the same helper:

```text
alctl apply EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 FILE|-
alctl rollback EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 [--json]
alctl toggle-origin EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 ID
alctl route-origin EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 \
  ID direct|vpn
```

Owner overlay operations are:

```text
alctl overlay-put EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 \
  OWNER EXPECTED_GENERATION SOURCE_OR_AUTO \
  EXPECTED_SOURCE_OR_DASH EXPECTED_MAC_OR_DASH FILE|-
alctl overlay-remove EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 \
  OWNER EXPECTED_GENERATION
alctl overlay-list --json
alctl effective-status --json
```

Every candidate is copied into a private temporary file and validated before
activation. A reboot restore supplies the manifest's previously trusted source
and MAC as preconditions; the router compares them with the newly resolved
binding before it builds or activates a candidate chain. `-` permits an
explicit first load or reviewed rebind. The companion then composes a fresh
effective document, builds the inactive chain, verifies it, changes the active
jump, and only then commits the new runtime metadata. A failed file move or
verification switches back to the previous chain and restores the prior
document.

### Bounded DNS and atomic inactive-chain loading

Before planning a candidate, the helper extracts the unique enabled domain
selectors. It runs at most eight resolver jobs at once and gives each
`nslookup` five seconds before terminating it. A refresh prefers a fresh
validated answer but falls back to the prior validated cache when the fresh
lookup fails; an unresolved domain with no prior answer is reported and
omitted. The resulting addresses are then compiled in deterministic policy
order, with no more than 16 addresses retained for one domain.

The supported E4200 path serializes the complete candidate into one bounded
`mangle` restore document. That document declares only the inactive A/B user
chain; it contains no `PREROUTING` mutation. Before both the dry run and commit,
the helper requires the inactive chain to be the exact unreferenced A/B peer,
the active chain to have exactly one reference and the first/only owned
`PREROUTING` jump, sufficient reclaimable memory, and remaining transaction
time. It runs `iptables-restore --noflush --test` first, then commits the same
document once with `iptables-restore --noflush`.

After commit, the helper reads back the exact expected rule count and verifies
that the reference topology did not change. Only then may the normal A/B jump
swap publish the candidate. A resolver timeout, guard failure, restore error,
deadline, or readback mismatch discards the inactive candidate and leaves the
previous active policy in place.

The expected version, package MD5, and helper MD5 are checked after acquiring
the controller lock. The base identity must match the verified running
`VERSION` and `PACKAGE_MD5` markers and the installed NVRAM metadata; the
helper digest must match the executable that will be sourced for the
transaction. A same-version package replacement or helper replacement
therefore cannot race a mutation onto different code.

Overlay put/remove never invokes `nvram set` or `nvram commit`.

## Core Transaction And Recovery

A core replacement:

1. requires the expected runtime generation;
2. rejects core/overlay origin collisions;
3. validates the complete core and projected persistent capacity;
4. composes it with all current valid overlays;
5. builds and activates an inactive chain;
6. encodes, writes, commits, decodes, and byte-verifies the NVRAM record;
7. advances the generation only after successful persistence; and
8. returns layered readback status.

Before mutation, the companion saves the current/previous NVRAM values,
runtime documents, generation, and active chain. If persistence or final file
activation fails, it restores and commits those exact NVRAM values and
reactivates the previous chain in the same operation.

At boot, a corrupt current record is never partially activated. A separately
verified previous record is used in a visibly degraded recovery state. If
neither record validates, the companion activates an empty core and reports a
degraded recovery error rather than treating corruption as an intentional
empty policy.

## Admission Limits

Companion `0.2.11` starts with these E4200 bounds:

| Limit | Value |
| --- | ---: |
| Persistent core TSV | 6,144 bytes |
| NVRAM reserve after core/package write | 2,048 bytes |
| One overlay | 32,768 bytes / 320 rows |
| Overlay owners | 8 |
| Effective document | 131,072 bytes / 512 rows |
| Generated iptables matches | 1,536 |
| Minimum reclaimable policy memory | 8,192 KiB |
| Whole policy transaction | 240 seconds |
| Parallel DNS lookups | 8 |
| Per-domain DNS lookup | 5 seconds |
| Resolved addresses per domain | 16 |

The helper uses the larger of `MemAvailable` and
`MemFree + Buffers + Cached`, because the validated DD-WRT kernel reports an
unusually low `MemAvailable` despite substantial reclaimable memory. It checks
memory before the build and again after the candidate chain exists.

Generated matches are counted while the plan is built, not only after an
oversized chain has consumed resources. Duration is checked during DNS
prefetch, planning, restore testing, and commit; memory is checked before
planning and again around candidate publication. A rejection discards the
inactive candidate chain and retains the previous active policy.

Device-selector rows are rejected from owner overlays. The overlay chain
already provides the source condition; silently combining it with another
device selector would be ambiguous.

## Windows Policy Workspace

The Policies view follows the Ubuntu app's local-versus-applied hierarchy but
shows the new storage boundary explicitly:

| Card | Meaning |
| --- | --- |
| Local library | All saved policies, including intentionally undeployed rows |
| Persistent core | Global policy available immediately after reboot |
| This computer's RAM overlay | Expected and currently active owner state |
| Other overlays | Read-only summaries for other controllers |
| Effective router | The composed policy and generated-match result |

The main actions are:

- **Replace persistent core** — global NVRAM replacement with a whole-core
  summary and generation/hash drift refusal;
- **Load selected into router RAM** — owner-only volatile upload, with `auto`
  source binding recommended;
- **Restore RAM overlay now** — explicit reconciliation of this owner;
- **Remove this computer's RAM overlay** — leaves core and other owners intact;
- **Refresh router** — event-driven readback, not a polling toggle.

The local manifest adopts observed router state before the first change. It is
bound to the confirmed host fingerprint, expected companion version, and exact
stored-package MD5. A different same-version package is therefore not trusted
for a hybrid write. The helper MD5 is derived from the same desktop bundle and
is supplied as a per-mutation precondition rather than persisted as overlay
state. Core and overlay hash or generation drift is presented for review
rather than silently overwritten.

## Layered Status

`effective-status --json` adds fields shaped like:

```json
{
  "runtime_epoch": "c838dc8397a57cd936a1f9e7e3649caa",
  "package_md5": "3552747bcb9a06a8f6b64dcbb1ce0675",
  "stored_package_md5": "3552747bcb9a06a8f6b64dcbb1ce0675",
  "helper_md5": "ram-helper-md5",
  "core": {
    "generation": 1,
    "hash": "md5:b9651667705f05dbd97019aa529bc256",
    "storage": "compressed-nvram",
    "origins": 3,
    "rows": 41,
    "bytes": 4135,
    "origin_ids": ["..."]
  },
  "overlays": [
    {
      "owner": "controller-id",
      "generation": 1,
      "hash": "md5:6d6fe09c400bb103e0af5a168236f1d6",
      "source": "192.168.1.166/32",
      "mac": "54:bf:64:80:aa:23",
      "origins": 85,
      "rows": 275,
      "bytes": 24551,
      "origin_ids": ["..."]
    }
  ],
  "effective": {
    "hash": "md5:383499271b38e263b709040abbed1da8",
    "rows": 316,
    "bytes": 38455,
    "generated_matches": 693
  }
}
```

The numeric measurements and policy hashes reflect the final post-reboot
observation; the owner and helper values remain schema placeholders. DNS
results determine the generated match count and can vary without changing the
document hash. Status never exposes passwords, private keys, or Astrill
credentials.

## Live E4200 Deployment Result

The persistent core installed at generation 1 with three origins, 41 rows,
4,135 bytes, and hash `md5:b9651667705f05dbd97019aa529bc256`.
The Windows controller loaded 85 remaining origins as a generation-1,
275-row/24,551-byte overlay with hash
`md5:6d6fe09c400bb103e0af5a168236f1d6`, source
`192.168.1.166/32`, and MAC `54:bf:64:80:aa:23`. The resulting 316-row,
38,455-byte effective document had hash
`md5:383499271b38e263b709040abbed1da8`.

The first live attempt used the original 120-second helper limit and timed out
safely: the previous active chain stayed selected and no transaction residue
remained. With a 240-second helper deadline and 330-second desktop allowance,
the complete manual operation succeeded in 277.82 seconds including client
work. Its DNS snapshot produced 694 generated matches and 1,394 chain rules
(`2 * 694 + 6`), with one active reference and no inactive reference.
Single-process validation and committed-effective readback subsequently reduced
ordinary status retrieval from more than 90 seconds to about seven seconds.

A physical reboot created epoch
`c838dc8397a57cd936a1f9e7e3649caa`. Before Windows restoration, the persistent
core was already active while the helper and overlay were absent as designed.
The opted-in Windows one-shot path then staged the helper and restored the
overlay once in about 200 seconds; the GUI remained responsive, and the saved
epoch, attempt epoch, source, MAC, generation, and hashes all matched with no
recorded error. Fresh DNS produced 693 generated matches and 1,392 chain rules,
one rule pair fewer than the pre-reboot snapshot without changing the policy
document hash. Final readback showed one active reference, no inactive
reference, no transaction journal, 2,494 free NVRAM bytes, and Astrill
disconnected.

## Failure Matrix

| Event | Result |
| --- | --- |
| Router boots while every PC is off | Verified core-only policy is active |
| GUI observes a new epoch | Its opted-in overlay is restored at most once |
| GUI starts before DD-WRT is ready | No tight loop; manual refresh remains |
| Overlay upload is invalid or interrupted | Previous effective chain remains |
| Core/overlay generation is stale | Write is rejected without replacement |
| Source or MAC differs | Automatic restore is blocked for review |
| One controller updates its overlay | Other owner files and generations remain |
| Overlay exceeds memory/time/match limits | Candidate chain is flushed |
| Fresh DNS lookup times out | Prior validated addresses are reused when available |
| Restore topology or readback differs | Candidate is discarded; active jump remains |
| Core current record is corrupt | Verified previous core or degraded empty core |
| Astrill disconnects | VPN-target traffic keeps the existing fail-closed path |

## Remaining Boundary

RAM storage removes NVRAM pressure and source scoping prevents one computer's
overlay from changing another computer's traffic. It does not make
destination catalogs process-aware. UU Remote, WeChat, and similar software
can still use dynamic peers, relays, or CDNs outside maintained domains.

The long-term scalable design remains device-local route intent:

```text
application/domain classification on the computer
                    |
              inherit/direct/VPN
                    |
           constant-size router action
```

The E4200 build lacks a validated packet-intent matcher for that design.
Source-scoped overlays are the practical decoupled implementation today, with
device-local backends remaining the correct future path for true per-process
independence.

## Acceptance Checklist

- full automated tests and lint pass;
- deterministic base package remains within its archive limit;
- installer preflight predicts at least the 2 KiB post-write NVRAM reserve;
- same-version/different-MD5 upgrade is not skipped;
- failed upgrade restores the exact previous package and runtime;
- physical reboot activates the core before the desktop starts;
- opted-in GUI restores one owner overlay once for the new epoch;
- overlay source and MAC match the intended Windows computer;
- NVRAM digest is identical before and after every overlay operation;
- rejected candidates leave the previous effective chain active;
- management ping, 2.4 GHz radio, watchdog, and fail-closed state remain
  healthy; and
- final Astrill connection state matches the operator's requested state.
